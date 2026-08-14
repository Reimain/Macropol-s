"""Terraform — HCL block structure, and the references between blocks.

HCL is not JSON and it is not YAML, so there is a small tokenizer here rather
than a regex. A regex over `resource "..." "..." {` looks fine until a heredoc
contains a brace, a comment contains a quote, or a nested block shares the
keyword — and then it reports resources that do not exist, in a file it claims
to have understood. The tokenizer skips strings, `#`, `//` and `/* */` comments
and heredoc bodies, and matches braces properly, so a block's extent is known
rather than guessed.

Five block types are read: ``resource``, ``module``, ``provider``, ``variable``
and ``output``. Resources and modules become nodes; the interpolations between
them become ``references``, which is what makes "what breaks if this bucket
changes" answerable inside an IaC repository.

``*.tf.json`` is the same language with a JSON skin, and is read as JSON — the
easy path, taken deliberately, because a second hand-written parser for the same
semantics is a second place for them to diverge.

What is *not* claimed: whether any of this is applied. A `.tf` file is intent.
The evidence kind is
:data:`~slpie.domain.evidence.EvidenceKind.IAC_DECLARATION`, never a lockfile
pin, no matter how confident the file looks.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterator

from ...domain.evidence import EvidenceKind
from ...domain.identity import Urn
from ...plugins.protocol import DiscoveryResult, Observation
from ..base import Source, declares, evidence_at, line_of, result

EXTRACTOR = "slpie.terraform"

#: Prefixes that are language, not infrastructure. `var.region` is an input,
#: not a resource, and an edge to it would be noise in every blast radius.
RESERVED = frozenset({
    "var", "local", "locals", "each", "count", "self", "path", "terraform",
    "provider", "lookup", "toset", "tolist", "tomap", "try", "can", "file",
    "jsonencode", "jsondecode", "templatefile", "format", "join", "length",
})

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_HEREDOC = re.compile(r"<<[-~]?\s*\"?([A-Za-z_][A-Za-z0-9_]*)\"?")
_REFERENCE = re.compile(
    r"(?<![\w.\-])(?P<head>[A-Za-z][A-Za-z0-9_-]*)"
    r"\.(?P<second>[A-Za-z_][A-Za-z0-9_-]*)"
    r"(?:\.(?P<third>[A-Za-z_*][A-Za-z0-9_-]*))?"
)


def resource_urn(resource_type: str, name: str) -> Urn:
    return Urn.create("cloudresource", resource_type, name)


def data_urn(resource_type: str, name: str) -> Urn:
    return Urn.create("cloudresource", "data", resource_type, name)


def module_urn(name: str) -> Urn:
    return Urn.create("deployment", "terraform", "module", name)


def provider_urn(name: str) -> Urn:
    return Urn.create("provider", "terraform", name)


def variable_urn(name: str) -> Urn:
    return Urn.create("configuration", "terraform", "variable", name)


def output_urn(name: str) -> Urn:
    return Urn.create("configuration", "terraform", "output", name)


# --- block scanning ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Block:
    """One top-level HCL block, with its labels and its body."""

    type: str
    labels: tuple[str, ...]
    body: str
    line: int
    header: str

    @property
    def label(self) -> str:
        return self.labels[0] if self.labels else ""

    @property
    def second_label(self) -> str:
        return self.labels[1] if len(self.labels) > 1 else ""


def blocks(text: str) -> Iterator[Block]:
    """Yield every top-level block, skipping comments, strings and heredocs."""
    index = 0
    length = len(text)
    header_start = 0
    tokens: list[str] = []

    while index < length:
        character = text[index]

        if character == "#" or text.startswith("//", index):
            index = _end_of_line(text, index)
            continue
        if text.startswith("/*", index):
            closing = text.find("*/", index + 2)
            index = length if closing < 0 else closing + 2
            continue
        if character == '"':
            opened = index
            value, index = _read_string(text, index)
            if not tokens:
                header_start = opened
            tokens.append(value)
            continue
        heredoc = _HEREDOC.match(text, index)
        if heredoc:
            index = _skip_heredoc(text, heredoc.end(), heredoc.group(1))
            continue
        if character == "{":
            body_start = index + 1
            body_end, index = _matching_brace(text, index)
            if tokens:
                yield Block(
                    type=tokens[0],
                    labels=tuple(tokens[1:]),
                    body=text[body_start:body_end],
                    line=line_of(text, header_start),
                    header=text[header_start:body_start].strip(),
                )
            tokens = []
            continue
        if character in "}\n":
            if character == "\n":
                tokens = []
            index += 1
            continue

        identifier = _IDENTIFIER.match(text, index)
        if identifier:
            if not tokens:
                header_start = identifier.start()
            tokens.append(identifier.group(0))
            index = identifier.end()
            continue

        if not character.isspace():
            # `=`, `(`, a number — this line is an attribute, not a header.
            tokens = []
        index += 1


def _end_of_line(text: str, index: int) -> int:
    newline = text.find("\n", index)
    return len(text) if newline < 0 else newline


def _read_string(text: str, index: int) -> tuple[str, int]:
    index += 1
    collected: list[str] = []
    while index < len(text):
        character = text[index]
        if character == "\\" and index + 1 < len(text):
            collected.append(text[index + 1])
            index += 2
            continue
        if character == '"':
            return "".join(collected), index + 1
        collected.append(character)
        index += 1
    return "".join(collected), index


def _skip_heredoc(text: str, index: int, terminator: str) -> int:
    for line_start in _line_starts(text, index):
        if text[line_start:].lstrip().startswith(terminator):
            return _end_of_line(text, line_start)
    return len(text)


def _line_starts(text: str, index: int) -> Iterator[int]:
    position = text.find("\n", index)
    while position >= 0:
        yield position + 1
        position = text.find("\n", position + 1)


def _matching_brace(text: str, index: int) -> tuple[int, int]:
    """Return `(index of the closing brace, index just past it)`."""
    depth = 0
    position = index
    length = len(text)
    while position < length:
        character = text[position]
        if character == "#" or text.startswith("//", position):
            position = _end_of_line(text, position)
            continue
        if text.startswith("/*", position):
            closing = text.find("*/", position + 2)
            position = length if closing < 0 else closing + 2
            continue
        if character == '"':
            _, position = _read_string(text, position)
            continue
        heredoc = _HEREDOC.match(text, position)
        if heredoc:
            position = _skip_heredoc(text, heredoc.end(), heredoc.group(1))
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return position, position + 1
        position += 1
    # Truncated file: the body is whatever was there.
    return length, length


def attribute(body: str, name: str) -> str:
    """The literal string value of a top-level attribute, or ``""``."""
    match = re.search(rf"(?m)^\s*{re.escape(name)}\s*=\s*(.+)$", body)
    if not match:
        return ""
    value = match.group(1).strip()
    if value[:1] in "\"'" and value[-1:] == value[:1] and len(value) > 1:
        return value[1:-1]
    return value


# --- discovery -----------------------------------------------------------


def discover_terraform(source: Source) -> DiscoveryResult:
    """Read blocks and the interpolations between them."""
    if source.uri.endswith(".tf.json"):
        return discover_terraform_json(source)

    found = list(blocks(source.text))
    declared: set[tuple[str, str]] = {
        (block.label, block.second_label)
        for block in found if block.type in ("resource", "data")
    }

    observations: list[Observation] = []
    errors: list[str] = []

    for block in found:
        evidence = evidence_at(
            source.uri, kind=EvidenceKind.IAC_DECLARATION, extractor=EXTRACTOR,
            line=block.line, excerpt=block.header, digest=source.digest,
        )
        subject = _subject(block, errors, source.uri)
        if subject is None:
            continue
        observations.append(subject[1])
        for observation in _references(
            subject[0], block, evidence, declared,
        ):
            observations.append(observation)

    return result(observations, errors=errors)


def _subject(block: Block, errors: list[str], uri: str) -> tuple[str, Observation] | None:
    evidence = evidence_at(
        uri, kind=EvidenceKind.IAC_DECLARATION, extractor=EXTRACTOR,
        line=block.line, excerpt=block.header,
    )
    if block.type == "resource":
        if not block.label or not block.second_label:
            errors.append(f"{uri}:{block.line}: resource block without two labels")
            return None
        subject = resource_urn(block.label, block.second_label).to_string()
        return subject, declares(
            subject, evidence, node_kind="cloud_resource",
            resource_type=block.label, resource_name=block.second_label,
            provider=block.label.split("_")[0], managed=True,
        )
    if block.type == "data":
        if not block.label or not block.second_label:
            return None
        subject = data_urn(block.label, block.second_label).to_string()
        return subject, declares(
            subject, evidence, node_kind="cloud_resource",
            resource_type=block.label, resource_name=block.second_label,
            provider=block.label.split("_")[0], managed=False,
        )
    if block.type == "module":
        if not block.label:
            errors.append(f"{uri}:{block.line}: module block without a name")
            return None
        subject = module_urn(block.label).to_string()
        return subject, declares(
            subject, evidence, node_kind="deployment",
            module_name=block.label,
            source=attribute(block.body, "source"),
            version=attribute(block.body, "version"),
        )
    if block.type == "provider":
        subject = provider_urn(block.label or "unnamed").to_string()
        return subject, declares(
            subject, evidence, node_kind="external_provider",
            provider_name=block.label, region=attribute(block.body, "region"),
            alias=attribute(block.body, "alias"),
        )
    if block.type == "variable":
        subject = variable_urn(block.label or "unnamed").to_string()
        return subject, declares(
            subject, evidence, node_kind="configuration",
            variable_name=block.label, type=attribute(block.body, "type"),
            # The default's *presence*, not its value: a default is as likely to
            # be a token as a region.
            has_default="default" in block.body,
        )
    if block.type == "output":
        subject = output_urn(block.label or "unnamed").to_string()
        return subject, declares(
            subject, evidence, node_kind="configuration",
            output_name=block.label,
            sensitive=attribute(block.body, "sensitive") == "true",
        )
    return None


def _references(
    subject: str, block: Block, evidence: Any, declared: set[tuple[str, str]]
) -> Iterator[Observation]:
    """Interpolations from one block to another, `${…}` or bare."""
    seen: set[str] = set()
    for match in _REFERENCE.finditer(block.body):
        head, second, third = match.group("head"), match.group("second"), match.group("third")
        if head in RESERVED:
            continue
        if head == "module":
            target = module_urn(second).to_string()
        elif head == "data" and third:
            target = data_urn(second, third).to_string()
        elif (head, second) in declared or "_" in head:
            target = resource_urn(head, second).to_string()
        else:
            continue
        if target == subject or target in seen:
            continue
        seen.add(target)
        yield Observation(
            kind="references", subject=subject, object=target,
            evidence=evidence,
            properties={"expression": match.group(0), "language": "hcl"},
        )


# --- *.tf.json -----------------------------------------------------------


def discover_terraform_json(source: Source) -> DiscoveryResult:
    """The JSON spelling of the same language — same nodes, easier parse."""
    try:
        document = json.loads(source.text)
    except ValueError as error:
        return result((), errors=[f"{source.uri}: not valid JSON ({error})"])
    if not isinstance(document, dict):
        return result((), errors=[f"{source.uri}: expected an object"])

    observations: list[Observation] = []
    declared: set[tuple[str, str]] = set()

    for section in ("resource", "data"):
        for resource_type, named in _pairs(document.get(section)):
            for name, _body in _pairs(named):
                declared.add((resource_type, name))

    def cite(needle: str):
        line = source.line(f'"{needle}"')
        return evidence_at(
            source.uri, kind=EvidenceKind.IAC_DECLARATION, extractor=EXTRACTOR,
            line=line, excerpt=source.excerpt(line), digest=source.digest,
        )

    for section, factory, managed in (
        ("resource", resource_urn, True), ("data", data_urn, False),
    ):
        for resource_type, named in _pairs(document.get(section)):
            for name, body in _pairs(named):
                subject = factory(resource_type, name).to_string()
                evidence = cite(name)
                observations.append(declares(
                    subject, evidence, node_kind="cloud_resource",
                    resource_type=resource_type, resource_name=name,
                    provider=resource_type.split("_")[0], managed=managed,
                ))
                observations.extend(_references(
                    subject,
                    Block(section, (resource_type, name), json.dumps(body), evidence.location.line, name),
                    evidence, declared,
                ))

    for name, body in _pairs(document.get("module")):
        subject = module_urn(name).to_string()
        evidence = cite(name)
        observations.append(declares(
            subject, evidence, node_kind="deployment",
            module_name=name,
            source=str(body.get("source", "")) if isinstance(body, dict) else "",
            version=str(body.get("version", "")) if isinstance(body, dict) else "",
        ))
        observations.extend(_references(
            subject, Block("module", (name,), json.dumps(body), evidence.location.line, name),
            evidence, declared,
        ))

    for name, body in _pairs(document.get("variable")):
        observations.append(declares(
            variable_urn(name).to_string(), cite(name), node_kind="configuration",
            variable_name=name,
            has_default=isinstance(body, dict) and "default" in body,
        ))

    for name, body in _pairs(document.get("output")):
        observations.append(declares(
            output_urn(name).to_string(), cite(name), node_kind="configuration",
            output_name=name,
            sensitive=bool(body.get("sensitive")) if isinstance(body, dict) else False,
        ))

    return result(observations)


def _pairs(value: Any) -> Iterator[tuple[str, Any]]:
    """Terraform JSON allows a mapping or a list of mappings in every slot."""
    if isinstance(value, dict):
        yield from ((str(key), item) for key, item in value.items())
    elif isinstance(value, list):
        for entry in value:
            if isinstance(entry, dict):
                yield from ((str(key), item) for key, item in entry.items())


# --- registration --------------------------------------------------------

DISCOVERERS = (
    (
        "slpie.terraform", discover_terraform,
        ("*.tf", "*.tf.json"),
        ("references", "declares"),
        (EvidenceKind.IAC_DECLARATION,),
    ),
)
