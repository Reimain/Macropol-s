"""GraphQL SDL — the schema *is* the contract, so it is read as one.

A GraphQL service publishes one endpoint and a type system. That makes the
interesting questions different from REST: not "which paths exist" but "which
fields exist, what do they return, and who breaks if this field changes".

So the unit here is the **field**, not the operation. `urn:slpie:api:graphql/
Query.orders` is what a client actually depends on, and it is what a schema
change removes. Recording only `Query` would make every breaking change look
identical.

Three things this reader is careful about:

* **Type references are unwrapped.** `[Order!]!` refers to `Order`. Keeping the
  decoration would produce four different node names for one type and no edge
  between any of them.
* **Built-in scalars are not nodes.** `String`, `Int`, `Float`, `Boolean` and
  `ID` exist in every schema; making them nodes adds five hubs that every type
  connects to and that nobody can act on.
* **Descriptions and comments are stripped before parsing**, because a `#`
  comment or a triple-quoted description block containing a brace would
  otherwise close a type early and silently truncate the schema.

The parser is deliberately structural rather than a full SDL grammar: it reads
type definitions and their fields, which is what the graph needs, and does not
attempt directives, unions of unions, or schema stitching. What it cannot read
it reports rather than half-reads.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

from ...domain.evidence import EvidenceKind
from ...domain.identity import Urn
from ...plugins.protocol import DiscoveryResult, Observation
from ..base import Source, declares, evidence_at, result, scope_of

EXTRACTOR = "slpie.graphql"

#: Scalars every schema has. Nodes for these would be hubs nobody can act on.
BUILTIN = frozenset({"String", "Int", "Float", "Boolean", "ID"})

#: The roots. A field on one of these is reachable by a client directly, which
#: is what makes removing it a breaking change rather than an internal one.
ROOTS = ("Query", "Mutation", "Subscription")

#: `type Order implements Node {`, `interface Node {`, `enum Status {`
_DEFINITION = re.compile(
    r"^\s*(?P<keyword>type|interface|input|enum|union|scalar)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s+implements\s+(?P<implements>[^\{]+?))?"
    r"\s*(?P<open>\{)?\s*$",
    re.MULTILINE,
)

#: `orders(first: Int): [Order!]!`
_FIELD = re.compile(
    r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\((?P<arguments>[^)]*)\))?"
    r"\s*:\s*(?P<type>[\[\]!A-Za-z0-9_ ]+?)\s*$"
)


def api_urn(name: str) -> Urn:
    return Urn.create("api", "graphql", name)


def type_urn(scope: str, name: str) -> Urn:
    return Urn.create("schema", "graphql", scope, name)


def unwrap(reference: str) -> str:
    """`[Order!]!` → `Order`. Decoration is not identity."""
    return reference.replace("[", "").replace("]", "").replace("!", "").strip()


def strip_comments(text: str) -> str:
    """Remove block descriptions and hash comments before anything is parsed.

    A GraphQL description block delimited by three double-quotes can contain a
    brace. Left in place it would close a type early and truncate the rest of
    the schema silently — which reads downstream as "this service has three
    fields" rather than as a parse failure.

    (This docstring says "three double-quotes" rather than showing them for the
    same reason: written literally, they would end this docstring here.)

    Removed text is replaced by its own newlines rather than deleted, so every
    line number afterwards still matches the file on disk. Evidence that cites
    a line the reader cannot find is worse than evidence citing none.
    """

    def blank(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    without_blocks = re.sub(r'"""[\s\S]*?"""', blank, text)
    return re.sub(r"#[^\n]*", "", without_blocks)


def _service(source: Source) -> str:
    return Urn.create("service", scope_of(source.element, "graphql")).to_string()


def _blocks(text: str) -> Iterator[tuple[str, str, str, int, tuple[str, ...]]]:
    """Yield (keyword, name, body, line, implements) for each definition."""
    for match in _DEFINITION.finditer(text):
        keyword = match.group("keyword")
        name = match.group("name")
        # From the name, not from match.start(): `^\s*` in MULTILINE mode
        # happily consumes the *previous* line's newline, which would report
        # every definition one line early.
        line = text[: match.start("name")].count("\n") + 1
        implements = tuple(
            part.strip()
            for part in (match.group("implements") or "").split("&")
            if part.strip()
        )

        if not match.group("open"):
            # `scalar DateTime` and `union X = A | B` have no brace body.
            yield (keyword, name, "", line, implements)
            continue

        depth = 1
        index = match.end()
        while index < len(text) and depth:
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
            index += 1
        yield (keyword, name, text[match.end(): index - 1], line, implements)


def discover_schema(source: Source) -> DiscoveryResult:
    """Read an SDL document into types, fields and the references between them."""
    text = strip_comments(source.text)
    if not text.strip():
        return result(())

    observations: list[Observation] = []
    errors: list[str] = []
    service = _service(source)
    scope = scope_of(source.element, "graphql")
    defined: set[str] = set()

    for keyword, name, body, line, implements in _blocks(text):
        defined.add(name)
        subject = type_urn(scope, name).to_string()
        evidence = evidence_at(
            source.uri, kind=EvidenceKind.MANIFEST_DECLARED, extractor=EXTRACTOR,
            line=line, excerpt=f"{keyword} {name}", digest=source.digest,
        )
        observations.append(declares(
            subject, evidence, node_kind="schema", protocol="graphql",
            definition=keyword, root=name in ROOTS,
        ))

        # `type Order implements Node & Timestamped` — a real edge, and the one
        # that answers "what else breaks if this interface changes".
        for parent in implements:
            observations.append(Observation(
                kind="references", subject=subject,
                object=type_urn(scope, parent).to_string(),
                evidence=evidence,
                properties={"protocol": "graphql", "relation": "implements"},
            ))

        if not body:
            continue

        # The body begins immediately after the opening brace, so its first
        # split line is the tail of the definition line itself — which makes
        # `line + index` the file line, not `line + index + 1`.
        for index, raw in enumerate(body.splitlines()):
            field = _FIELD.match(raw)
            if field is None:
                continue
            field_name = field.group("name")
            returns = unwrap(field.group("type"))
            field_line = line + index
            field_evidence = evidence_at(
                source.uri, kind=EvidenceKind.MANIFEST_DECLARED, extractor=EXTRACTOR,
                line=field_line, excerpt=raw.strip()[:200], digest=source.digest,
            )

            if name in ROOTS:
                # A root field is the thing a client calls, so it is an API
                # surface in its own right and the service owns it.
                operation = api_urn(f"{name}.{field_name}").to_string()
                observations.append(declares(
                    operation, field_evidence, node_kind="api", protocol="graphql",
                    operation=name.lower(), returns=returns,
                    arguments=field.group("arguments") or "",
                ))
                observations.append(Observation(
                    kind="owns", subject=service, object=operation,
                    evidence=field_evidence,
                    properties={"protocol": "graphql"},
                ))
                reference_from = operation
            else:
                reference_from = subject

            if returns and returns not in BUILTIN:
                observations.append(Observation(
                    kind="references", subject=reference_from,
                    object=type_urn(scope, returns).to_string(),
                    evidence=field_evidence,
                    properties={"field": field_name, "protocol": "graphql"},
                ))

    if not defined:
        errors.append(
            f"{source.uri}: no GraphQL type definitions found; if this is a "
            f"query document rather than a schema, it declares no contract"
        )

    return result(observations, errors=errors)


DISCOVERERS = (
    (
        "slpie.graphql", discover_schema,
        ("*.graphql", "*.gql", "schema.graphqls", "*.graphqls"),
        ("declares", "owns", "references"),
        (EvidenceKind.MANIFEST_DECLARED,),
    ),
)
