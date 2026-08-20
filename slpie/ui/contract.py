"""The API contract, emitted from the verb registry rather than maintained.

This is the keystone of §24's "one registry, five projections". `Api._register`
already declares routes as data; this turns that plus the verb registry into an
OpenAPI 3.1 document and a TypeScript client. Three consumers then build from one
source — the stdlib server, the FastAPI adapter (phase 16), and every client — so
a capability added once appears in all of them and none can drift.

Two decisions worth stating.

**The verb routes are generated, not listed.** Every registered verb gets
`POST /api/v/<name>` automatically, so adding a verb adds a route with no file
edited. That is what makes the registry *authoritative* rather than merely
adjacent: there is no place to forget to wire something.

**The contract carries the type graph.** `consumes`/`produces` and the successor
map travel in the document, which is what lets a generated client type-check a
composition before sending it. Without that a client would have to round-trip to
the server to learn that `findings | attach` is impossible, and the whole point of
typing the pipe is that the answer arrives before anything runs.

Stdlib only: this writes text. No `jsonschema`, no code generator, no build step.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..compose.flow import Kind
from ..compose.registry import VerbRegistry, registry as default_registry
from ..compose.verb import Param, Verb

#: The contract's own version. Bumped when the *shape* changes, not when a verb is
#: added — clients pin against the shape, and a verb arriving is not a breaking
#: change to it.
CONTRACT_VERSION = "1.0.0"

#: JSON Schema types for the parameter types verbs may declare.
SCHEMA_TYPES: Mapping[str, str] = {
    "str": "string", "path": "string", "int": "integer",
    "float": "number", "bool": "boolean", "list": "array",
}

#: Routes that read without changing anything, so an edge cache may hold them.
#: Marked here rather than guessed by method, because `POST /api/v/discover` is a
#: read despite being a POST — it takes a body, and that is a transport detail.
CACHEABLE_KINDS = frozenset({
    Kind.REPORT, Kind.NODES, Kind.GRAPH, Kind.MANIFEST,
})


def openapi(
    *,
    verbs: VerbRegistry | None = None,
    routes: Sequence[tuple[str, str]] = (),
    title: str = "SLPIE",
) -> dict[str, Any]:
    """The OpenAPI 3.1 document. One source for every server and client."""
    verbs = verbs if verbs is not None else default_registry()

    paths: dict[str, Any] = {}

    # The hand-declared read routes, taken from the server's own route table so
    # the document cannot claim a route the server does not serve.
    for method, path in routes:
        entry = paths.setdefault(path, {})
        entry[method.lower()] = {
            "summary": f"{method} {path}",
            "operationId": _operation_id(method, path),
            "responses": _responses(),
            "tags": ["read"],
        }

    # The verb routes, generated. Adding a verb adds a route; there is nowhere to
    # forget to wire it.
    for verb in verbs:
        paths[f"/api/v/{verb.name}"] = {
            "post": {
                "summary": verb.summary,
                "description": verb.detail or verb.summary,
                "operationId": f"verb_{verb.name.replace('-', '_')}",
                "tags": [verb.group],
                "x-slpie-consumes": verb.consumes.value,
                "x-slpie-produces": verb.produces.value,
                "x-slpie-mutates": verb.mutates,
                "x-slpie-cacheable": (
                    verb.produces in CACHEABLE_KINDS and not verb.mutates
                ),
                "x-slpie-successors": [
                    item.name for item in verbs.successors(verb.name)
                ],
                "requestBody": {
                    "required": bool(_required(verb)),
                    "content": {
                        "application/json": {"schema": _verb_schema(verb)},
                    },
                },
                "responses": _responses(),
            }
        }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": title,
            "version": CONTRACT_VERSION,
            "description": (
                "Capabilities are verbs that compose. Every route below is a "
                "projection of the verb registry, so a capability added once "
                "appears in the CLI, this contract, the manual and every client. "
                "The x-slpie-consumes/produces extensions carry the type graph, "
                "so a client can reject an impossible composition before sending "
                "it."
            ),
        },
        "paths": paths,
        "components": {
            "schemas": {
                "Flow": _flow_schema(),
                "Gap": _gap_schema(),
                "Error": {
                    "type": "object",
                    "properties": {
                        "error": {"type": "string"},
                        "type": {"type": "string"},
                        "refused": {"type": "boolean"},
                    },
                    "required": ["error"],
                },
            },
        },
        "x-slpie-kinds": [
            {
                "kind": kind.value,
                "produced_by": [v.name for v in verbs.verbs if v.produces is kind],
                "consumed_by": [v.name for v in verbs.verbs if v.consumes is kind],
            }
            for kind in Kind
            if not kind.polymorphic and kind is not Kind.NOTHING
        ],
        "x-slpie-polymorphic": [
            v.name for v in verbs.verbs if v.consumes is Kind.ANY
        ],
    }


def _verb_schema(verb: Verb) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for param in verb.params:
        properties[param.name] = _param_schema(param)
    # Every verb route accepts an upstream flow, which is how a client composes
    # server-side without serialising a whole pipeline per stage.
    properties["upstream"] = {
        "$ref": "#/components/schemas/Flow",
        "description": "the flow to continue from; omit for a source verb",
    }
    if verb.mutates:
        properties["confirmed"] = {
            "type": "boolean",
            "description": (
                "required: this verb changes the environment and is refused "
                "without it, by the same guard the CLI hits"
            ),
        }
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    required = _required(verb)
    if required:
        schema["required"] = required
    return schema


def _param_schema(param: Param) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": SCHEMA_TYPES.get(param.type, "string"),
        "description": param.help,
    }
    if param.type == "list":
        body["items"] = {"type": "string"}
    if param.choices:
        body["enum"] = list(param.choices)
    if param.default is not None:
        body["default"] = param.default
    return body


def _required(verb: Verb) -> list[str]:
    return [param.name for param in verb.params if param.required]


def _responses() -> dict[str, Any]:
    return {
        "200": {
            "description": "the answer, with its reasoning and its gaps",
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/Flow"},
                },
            },
        },
        "400": _error("the request or the composition was invalid"),
        "403": _error("refused — a guard declined it, and the reason is given"),
    }


def _error(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/Error"}},
        },
    }


def _flow_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": (
            "A value plus everything needed to justify it. Composing accumulates "
            "the reasoning and the gaps rather than discarding them."
        ),
        "properties": {
            "kind": {"type": "string", "enum": [k.value for k in Kind]},
            "size": {"type": "integer"},
            "stages": {"type": "array", "items": {"type": "string"}},
            "confidence": {
                "type": "number",
                "description": "the path's confidence, discounted by the gaps",
            },
            "grounded": {
                "type": "boolean",
                "description": "whether every claim traces to a file and a line",
            },
            "digest": {
                "type": "string",
                "description": (
                    "content-addressed over the answer, not the run — two "
                    "surfaces executing the same composition must agree"
                ),
            },
            "gaps": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/Gap"},
            },
            "reasoning": {"type": "object"},
            "value": {},
            "facts": {"type": "object"},
        },
        "required": ["kind", "size", "stages", "digest"],
    }


def _gap_schema() -> dict[str, Any]:
    from ..domain.finding import GapKind

    return {
        "type": "object",
        "description": "something the platform could not see, and what it cost",
        "properties": {
            "kind": {"type": "string", "enum": [k.value for k in GapKind]},
            "subject": {"type": "string"},
            "detail": {"type": "string"},
            "remediation": {"type": "string"},
            "confidence_impact": {"type": "number"},
        },
        "required": ["kind", "subject", "detail"],
    }


def _operation_id(method: str, path: str) -> str:
    cleaned = path.strip("/").replace("/", "_").replace("-", "_").replace(".", "_")
    return f"{method.lower()}_{cleaned or 'root'}"


# --- the generated TypeScript client ------------------------------------


def typescript(*, verbs: VerbRegistry | None = None) -> str:
    """A typed client, generated. A route change becomes a compile error.

    That is the whole value: the web, desktop and mobile clients consume this, so a
    verb whose parameters changed surfaces at build time in every client rather
    than as a runtime 404 somebody reports from production.

    Written by hand rather than by a generator dependency because the surface is
    small and regular, and adding a Node toolchain to emit it would put a build
    step inside a kernel that is deliberately stdlib-only.
    """
    verbs = verbs if verbs is not None else default_registry()
    lines: list[str] = [
        "// Generated from the SLPIE verb registry. Do not edit.",
        "// Regenerate with: slpie contract --typescript",
        f"// contract {CONTRACT_VERSION}",
        "",
        "export type Kind =",
        *[f"  | {json.dumps(kind.value)}" for kind in Kind],
        "  ;",
        "",
        "export interface Gap {",
        "  kind: string;",
        "  subject: string;",
        "  detail: string;",
        "  remediation?: string;",
        "  confidence_impact?: number;",
        "}",
        "",
        "export interface Flow<T = unknown> {",
        "  kind: Kind;",
        "  size: number;",
        "  stages: string[];",
        "  /** The path's confidence, already discounted by the gaps. */",
        "  confidence: number;",
        "  /** Whether every claim traces back to a file and a line. */",
        "  grounded: boolean;",
        "  /** Two surfaces running the same composition must produce the same digest. */",
        "  digest: string;",
        "  gaps: Gap[];",
        "  reasoning: { steps: unknown[]; sources: string[] };",
        "  value: T;",
        "  facts: Record<string, unknown>;",
        "}",
        "",
        "/** What each verb consumes and produces, so a client can type-check a",
        "  * composition before sending it. Mirrors the server's type graph. */",
        "export const VERB_TYPES = {",
    ]

    for verb in verbs:
        lines.append(
            f"  {json.dumps(verb.name)}: {{ "
            f"consumes: {json.dumps(verb.consumes.value)}, "
            f"produces: {json.dumps(verb.produces.value)}, "
            f"mutates: {json.dumps(verb.mutates)} }},"
        )

    lines += [
        "} as const satisfies Record<string, {",
        "  consumes: Kind; produces: Kind; mutates: boolean;",
        "}>;",
        "",
        "export type VerbName = keyof typeof VERB_TYPES;",
        "",
        "/** Reject an impossible composition without a round trip. */",
        "export function validate(pipeline: VerbName[]): string | null {",
        '  let current: Kind = "nothing";',
        "  for (let i = 0; i < pipeline.length; i++) {",
        "    const verb = VERB_TYPES[pipeline[i]];",
        '    if (verb.consumes !== "any" && verb.consumes !== current) {',
        "      return `stage ${i + 1} \\`${pipeline[i]}\\` consumes "
        "${verb.consumes.toUpperCase()}, but it was given "
        "${current.toUpperCase()}`;",
        "    }",
        '    current = verb.produces === "same" ? current : verb.produces;',
        "  }",
        "  return null;",
        "}",
        "",
        "export function producedKind(pipeline: VerbName[]): Kind {",
        '  let current: Kind = "nothing";',
        "  for (const name of pipeline) {",
        "    const verb = VERB_TYPES[name];",
        '    current = verb.produces === "same" ? current : verb.produces;',
        "  }",
        "  return current;",
        "}",
        "",
        "export interface ClientOptions {",
        "  baseUrl?: string;",
        "  fetch?: typeof fetch;",
        "}",
        "",
        "export class SlpieClient {",
        "  private readonly baseUrl: string;",
        "  private readonly doFetch: typeof fetch;",
        "",
        "  constructor(options: ClientOptions = {}) {",
        '    this.baseUrl = (options.baseUrl ?? "").replace(/\\/$/, "");',
        "    this.doFetch = options.fetch ?? fetch;",
        "  }",
        "",
        "  /** Run a whole composition server-side. The primary entry point. */",
        "  async run(pipeline: string, confirmed = false): Promise<Flow> {",
        "    return this.post(`/api/run`, { pipeline, confirmed });",
        "  }",
        "",
        "  /** Check a composition without running any of it. */",
        "  async check(pipeline: string): Promise<{ ok: boolean; explanation: string }> {",
        "    return this.post(`/api/compose/validate`, { pipeline });",
        "  }",
        "",
        "  /** Ask the planner to write a composition for a question. */",
        "  async plan(question: string): Promise<unknown> {",
        "    return this.post(`/api/plan`, { question });",
        "  }",
        "",
        "  async verbs(): Promise<unknown> {",
        "    return this.get(`/api/verbs`);",
        "  }",
        "",
        "  async manual(): Promise<unknown> {",
        "    return this.get(`/api/manual`);",
        "  }",
        "",
    ]

    for verb in verbs:
        signature = _ts_signature(verb)
        lines += [
            f"  /** {verb.summary} */",
            f"  async {_ts_name(verb.name)}({signature}): Promise<Flow> {{",
            f"    return this.post(`/api/v/{verb.name}`, params);",
            "  }",
            "",
        ]

    lines += [
        "  private async get(path: string): Promise<any> {",
        "    const response = await this.doFetch(`${this.baseUrl}${path}`);",
        "    return this.unwrap(response);",
        "  }",
        "",
        "  private async post(path: string, body: unknown): Promise<any> {",
        "    const response = await this.doFetch(`${this.baseUrl}${path}`, {",
        '      method: "POST",',
        '      headers: { "content-type": "application/json" },',
        "      body: JSON.stringify(body ?? {}),",
        "    });",
        "    return this.unwrap(response);",
        "  }",
        "",
        "  private async unwrap(response: Response): Promise<any> {",
        "    const body = await response.json();",
        "    if (!response.ok) {",
        "      // A refusal is an answer with a reason, not a generic failure.",
        "      throw Object.assign(",
        '        new Error(body?.error ?? `HTTP ${response.status}`),',
        "        { status: response.status, refused: body?.refused === true },",
        "      );",
        "    }",
        "    return body;",
        "  }",
        "}",
        "",
    ]
    return "\n".join(lines)


def _ts_name(name: str) -> str:
    head, *rest = name.split("-")
    return head + "".join(part.capitalize() for part in rest)


def _ts_signature(verb: Verb) -> str:
    fields: list[str] = []
    for param in verb.params:
        optional = "" if param.required else "?"
        fields.append(f"{param.name}{optional}: {_ts_type(param)}")
    fields.append("upstream?: Flow")
    if verb.mutates:
        fields.append("confirmed?: boolean")
    required = any(param.required for param in verb.params)
    return f"params: {{ {'; '.join(fields)} }}" + ("" if required else " = {}")


def _ts_type(param: Param) -> str:
    if param.choices:
        return " | ".join(json.dumps(choice) for choice in param.choices)
    return {
        "int": "number", "float": "number", "bool": "boolean",
        "list": "string[]",
    }.get(param.type, "string")


def cacheable_routes(
    *,
    verbs: VerbRegistry | None = None,
    routes: Sequence[tuple[str, str]] = (),
) -> frozenset[str]:
    """Every route whose answer a client may hold on a device.

    Derived from the document the contract already emits, so there is one
    statement of what may be cached and three consumers of it: the service
    worker, the browser's device tier (§31), and any edge cache in front of the
    API. A second list would be a second answer.

    A hand-declared read route is cacheable when it needs no confirmation and
    changes nothing — which for a GET is all of them. The interesting judgements
    are on the verb routes, where `POST /api/v/discover` is a read despite the
    method, and those already carry `x-slpie-cacheable` per operation.
    """
    document = openapi(verbs=verbs, routes=routes)
    found: set[str] = set()
    for path, operations in document["paths"].items():
        for method, operation in operations.items():
            marked = operation.get("x-slpie-cacheable")
            if marked is True or (marked is None and method.lower() == "get"):
                found.add(f"{method.upper()} {path}")
    # The live feed is a connection, not a document. It is registered as a route
    # so a generated client can discover it, which means it would otherwise be
    # swept up here as an ordinary GET — and a cached SSE response replays
    # history as though it were happening now.
    return frozenset(found - {"GET /api/stream"})


def route_set(
    *,
    verbs: VerbRegistry | None = None,
    routes: Sequence[tuple[str, str]] = (),
) -> frozenset[str]:
    """Every route the contract declares. Compared against the server's own.

    This is the drift check: if the stdlib server and the contract ever disagree,
    a test fails rather than a client discovering it as a 404. Pass the server's
    `Api.routes` so the comparison covers the hand-declared read routes as well as
    the generated verb ones.
    """
    document = openapi(verbs=verbs, routes=routes)
    return frozenset(
        f"{method.upper()} {path}"
        for path, operations in document["paths"].items()
        for method in operations
    )


# --- the screen manifest ------------------------------------------------


#: Where a screen sits in the navigation. Six sections, because that is how many
#: distinct *jobs* the platform has, not because six is a nice number: asking,
#: operating, building artifacts, browsing the catalogue, managing the API, and
#: administering the tenancy.
SECTIONS = ("console", "operate", "build", "catalog", "api", "admin")


#: The components a screen block may name. This is the dictionary half of
#: "screens as data": a block says `grid` and the browser resolves it, exactly
#: as `screens/index.js` resolves a screen key to a module.
#:
#: Deliberately smaller than the browser's whole component vocabulary. `ui/` has
#: ~35 exported functions and most take a shape only hand-written JavaScript can
#: build — `claim(value, confidence)` wants a number the payload does not carry.
#: What is addressable here is what can be driven by *data*, and
#: `test_the_addressable_components_match_the_browser_registry` asserts this set
#: equals the keys of `COMPONENTS` in `app/ui/components.js`, in both directions:
#: a name here with no implementation renders nothing, and an implementation
#: with no name here is unreachable.
COMPONENTS = frozenset({
    "auto",       # render by shape — a table if it is rows, metrics if it is fields
    "grid",       # the dense register's instrument, with declared columns
    "table",      # the plain table, for a handful of rows that need no sorting
    "metrics",    # label/value pairs from a flat object
    "runner",     # the verb forms: parameters, type signature, an example
    "prose",      # a sentence, resolved through the lexicon
    "stat",       # one number with a label
    "bars",       # a ranked bar list from {label, value} rows
})

#: How a cell is drawn. A block cannot carry a function, so the rendering
#: behaviour is a *named* formatter — the same move as naming the component.
FORMATS = frozenset({
    "", "mono", "severity", "pill", "cite", "count", "confidence", "link",
})


@dataclass(frozen=True, slots=True)
class Column:
    """One column of a data-driven grid.

    Mirrors the spec `ui/grid.js` already takes, minus the callables: `render`
    and `sortValue` are JavaScript functions and cannot travel as data, so their
    common cases become `format` and the browser supplies the function.
    """

    key: str
    label: str = ""
    align: str = ""              # "" | "right" — right for quantities
    density: str = ""            # "" | "dense" — a column only the dense register shows
    format: str = ""             # a name in FORMATS
    link: str = ""               # a hash template: "#/node/:id"

    def __post_init__(self) -> None:
        if self.format not in FORMATS:
            raise ValueError(
                f"column {self.key!r} asks for format {self.format!r}; "
                f"known formats are {', '.join(sorted(FORMATS - {''}))}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "label": self.label or self.key.replace("_", " "),
            "align": self.align, "density": self.density,
            "format": self.format, "link": self.link,
        }


@dataclass(frozen=True, slots=True)
class Block:
    """One piece of a screen, named rather than written.

    This is the JSON dictionary the interface is shipped as. A screen with
    blocks is *composed* by the browser from a component registry; a screen with
    a hand-built module is drawn by that module and ignores its blocks entirely,
    because `screens/index.js` resolves an authored key first. Authored beats
    composed beats dumped, and nothing here changes a screen somebody designed.
    """

    component: str
    source: str = ""             # "GET /api/findings" — a route in `Screen.reads`
    select: str = ""             # a dotted path into the body: "findings"
    title: str = ""              # a lexicon key, or a literal
    columns: tuple[Column, ...] = ()
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.component not in COMPONENTS:
            raise ValueError(
                f"{self.component!r} is not an addressable component; "
                f"known components are {', '.join(sorted(COMPONENTS))}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "source": self.source,
            "select": self.select,
            "title": self.title,
            "columns": [column.to_dict() for column in self.columns],
            "options": dict(self.options),
        }


@dataclass(frozen=True, slots=True)
class Screen:
    """One screen, declared here so the browser does not have to guess.

    The projection lives in Python for the same reason the OpenAPI document
    does: it can be tested without a browser. `test_every_verb_group_has_a_screen`
    and `test_every_read_route_is_reachable_from_some_screen` are the frontend's
    equivalent of `route_set()`, and they fail on the commit that adds an
    unreachable capability rather than on the day somebody notices.

    `events` is the field that earns this. The interface currently decides what
    to refetch with a hand-written chain — `if (event.kind.startsWith("finding")
    && current === "findings")` — which a new event kind silently falls off. As
    data, the SSE-to-refetch map is generated, so wiring is not something anybody
    has to remember.
    """

    key: str
    path: str
    title: str
    section: str
    reads: tuple[str, ...] = ()          # "GET /api/findings"
    verbs: tuple[str, ...] = ()
    events: tuple[str, ...] = ()         # event kinds that invalidate this screen
    action: str = ""                     # the RBAC action, dotted
    resource: str = "*"
    crumbs: tuple[str, ...] = ()         # parent screen keys, outermost first
    parent: str = ""                     # the screen this is a *view of*
    summary: str = ""                    # one line, shown under the page title
    authored: bool = False               # a hand-built screens/<key>.js exists
    blocks: tuple[Block, ...] = ()       # how to compose it, when nobody wrote it

    @property
    def is_destination(self) -> bool:
        """Whether this belongs in the rail.

        A screen with a parent is a *view of* something rather than a place you
        go, so it appears as a tab on its parent's page and never as a rail row.
        Without this rule the rail listed Node, Impact, Cycles and History as
        peers of Graph — while those same screens declared `crumbs=("graph",)`,
        so the manifest already knew they were children and the navigation was
        contradicting its own data. Eleven Operate rows, of which four were
        details of another row, is how a rail stops being a map.
        """
        return not self.parent

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "path": self.path,
            "title": self.title,
            "section": self.section,
            "reads": list(self.reads),
            "verbs": list(self.verbs),
            "events": list(self.events),
            "action": self.action,
            "resource": self.resource,
            "crumbs": list(self.crumbs),
            "parent": self.parent,
            "summary": self.summary,
            "authored": self.authored,
            "blocks": [block.to_dict() for block in self.blocks],
        }


#: The screens with a designed identity. Everything else is generated below as an
#: inspector, and an inspector is deliberately utilitarian: four finished screens
#: and thirty-two honest inspectors reads as a platform, where thirty-six
#: half-designed screens reads as a broken product.
#:
#: **A screen with a `parent` is a view, not a destination.** It renders as a tab
#: on its parent's page and never appears in the rail. Node, Impact and Cycles
#: are things you look at *about a graph*; History is a view *of the ledger*;
#: Reconciliation is a view *of the environment*. Listing them beside their own
#: parents made the rail a list of every page rather than a map of the product,
#: which is the difference between navigation and a table of contents.
DESIGNED: tuple[Screen, ...] = (
    Screen("console", "/", "Console", "console",
           reads=("GET /api/status", "GET /api/manifest", "GET /api/stream"),
           verbs=("ask", "options", "suggest", "accept", "dismiss"),
           events=("*",), action="intelligence.ask",
           summary="Ask about this environment and get the answer with the "
                   "reasoning that produced it and the gaps that limit it."),
    Screen("compose", "/compose", "Compose", "operate",
           reads=("GET /api/verbs",), verbs=("routine",),
           action="platform.discover",
           summary="Build a pipeline from typed verbs. Invalid compositions are "
                   "refused before anything runs."),
    Screen("findings", "/findings/:severity?", "Findings", "operate",
           reads=("GET /api/findings",), verbs=("findings", "govern", "rules"),
           events=("finding_raised", "constraint_violated"),
           action="analysis.findings",
           summary="Everything the rules raised, ranked by severity, each with "
                   "its evidence and a remediation."),

    Screen("graph", "/graph", "Graph", "operate",
           reads=("GET /api/graph",), verbs=("graph", "search", "interest"),
           events=("node_asserted", "edge_asserted", "node_retired"),
           action="environment.graph",
           summary="Nodes and edges as the platform observed them, shaded by "
                   "the confidence of the evidence behind each one."),
    Screen("node", "/node/:id", "Node", "operate", parent="graph",
           reads=("GET /api/node",), events=("node_asserted", "node_retired"),
           action="environment.graph", crumbs=("graph",)),
    Screen("impact", "/impact/:id", "Impact", "operate", parent="graph",
           reads=("GET /api/impact",), verbs=("impact", "radius"),
           action="environment.impact", crumbs=("graph", "node")),
    Screen("cycles", "/cycles", "Cycles", "operate", parent="graph",
           reads=("GET /api/cycles",), action="environment.graph",
           crumbs=("graph",)),

    Screen("station", "/station", "Environment", "operate",
           reads=("GET /api/station",), verbs=("attach", "gaps", "status"),
           events=("element_attached", "capability_refused"),
           action="environment.attach",
           summary="What is attached, which capabilities each element granted "
                   "or refused, and the gaps those refusals put on every answer."),
    Screen("reconcile", "/reconcile", "Reconciliation", "operate",
           parent="station", crumbs=("station",),
           reads=("GET /api/reconcile",), verbs=("reconcile", "declare"),
           events=("contradiction_found",), action="environment.reconcile"),

    Screen("ledger", "/ledger", "Ledger", "operate",
           reads=("GET /api/integrity", "GET /api/projections",
                  "GET /api/stream/status"),
           action="platform.discover",
           summary="The append-only record every answer is derived from: chain "
                   "integrity, projection lag, and the live feed's own health."),
    Screen("history", "/history/:subject?", "History", "operate",
           parent="ledger", crumbs=("ledger",),
           reads=("GET /api/history", "GET /api/causation"),
           verbs=("history",), action="dispatch.history"),

    Screen("simulator", "/simulator", "Simulator", "operate",
           reads=("GET /api/scenarios",), verbs=("simulate", "fire", "target"),
           events=("scenario_fired", "target_changed"),
           action="environment.target",
           summary="Materialise the declared world as real files, fire a "
                   "scenario at it, and watch the platform react.",
           blocks=(
               Block("grid", source="GET /api/scenarios", select="scenarios",
                     title="Scenarios",
                     columns=(Column("", label="Scenario", format="mono"),)),
               Block("runner", title="Run"),
           )),

    Screen("catalog", "/catalog/:tenant?/:realm?/:dataset?/:object?", "Catalog",
           "catalog", reads=("GET /api/search", "GET /api/admin/datasets"),
           verbs=("scan", "changed"),
           events=("node_asserted", "node_retired"), action="dataset.read",
           summary="Tenants, realms, datasets and objects — everything the "
                   "platform has catalogued, with its lineage."),

    Screen("verbs", "/verbs", "Verbs", "build",
           reads=("GET /api/verbs",), action="platform.discover",
           summary="Every capability this build has, as a typed verb. Each one "
                   "is reachable from the CLI, the API and a pipeline."),

    Screen("portal", "/portal/:api?", "Developer portal", "api",
           reads=("GET /api/manual", "GET /api/contract", "GET /api/apim/apis"),
           action="platform.discover",
           summary="The APIs this platform publishes, their operations, and a "
                   "console to try them.",
           blocks=(
               Block("grid", source="GET /api/apim/apis", select="apis",
                     title="Published APIs", columns=(
                         Column("name", "API"),
                         Column("api_id", "Id", format="mono", density="dense"),
                         Column("version", "Version", format="mono"),
                         Column("visibility", "Visibility", format="pill"),
                         Column("default_throttle", "Throttle",
                                density="dense"),
                         Column("operations", "Operations", align="right",
                                format="count"),
                     )),
           )),
    Screen("gateway", "/gateway", "Gateway", "api",
           reads=("GET /api/routes", "GET /api/apim/gateway"),
           action="apim.gateway.read",
           summary="The live route table and the policy chain in front of it — "
                   "which rule admitted or refused each call, and why.",
           blocks=(
               Block("metrics", source="GET /api/apim/gateway",
                     title="Gateway"),
               Block("grid", source="GET /api/routes", select="routes",
                     title="Route table",
                     columns=(Column("", label="Route", format="mono"),)),
           )),
    Screen("throttling", "/throttling", "Throttling", "api", parent="gateway",
           crumbs=("gateway",),
           reads=("GET /api/apim/throttles",), action="apim.throttles.read",
           blocks=(
               Block("grid", source="GET /api/apim/throttles", select="tiers",
                     title="Tiers", columns=(
                         Column("name", "Tier"),
                         Column("requests", "Requests", align="right",
                                format="count"),
                         Column("window_seconds", "Window", align="right",
                                format="count"),
                         Column("burst", "Burst", align="right", format="count",
                                density="dense"),
                         Column("applies_to", "Applies to", density="dense"),
                         Column("description", "What it is for"),
                     )),
               Block("metrics", source="GET /api/apim/throttles",
                     title="Right now"),
           )),
    Screen("analytics", "/analytics", "Analytics", "api", parent="gateway",
           crumbs=("gateway",),
           reads=("GET /api/apim/analytics",), action="apim.analytics.read"),
    Screen("publisher", "/publisher/:api?", "Publisher", "api",
           reads=("GET /api/apim/lifecycle", "GET /api/apim/apis"),
           action="apim.lifecycle.read",
           summary="Every API this platform publishes, where it stands in its "
                   "life, and which moves are legal from there.",
           blocks=(
               Block("grid", source="GET /api/apim/lifecycle", select="states",
                     title="The transition table", columns=(
                         Column("state", "State", format="pill"),
                         Column("serves", "Serves calls"),
                         Column("terminal", "Terminal", density="dense"),
                         Column("may_reach", "May move to"),
                         Column("reason_required", "Needs a stated reason"),
                     )),
               Block("grid", source="GET /api/apim/apis", select="apis",
                     title="Published APIs", columns=(
                         Column("name", "API"),
                         Column("api_id", "Id", format="mono", density="dense"),
                         Column("version", "Version", format="mono"),
                         Column("visibility", "Visibility", format="pill"),
                         Column("operations", "Operations", align="right",
                                format="count"),
                     )),
               Block("metrics", source="GET /api/apim/lifecycle",
                     title="Manager"),
           )),
    Screen("actions", "/actions", "Actions", "api", parent="gateway",
           crumbs=("gateway",),
           reads=("GET /api/apim/actions",), action="apim.gateway.read",
           summary="Which permission each route demands — the question an "
                   "operator writing a grant actually has.",
           blocks=(
               Block("metrics", source="GET /api/apim/actions",
                     title="Coverage"),
               Block("grid", source="GET /api/apim/actions", select="actions",
                     title="Every action, and what it opens", columns=(
                         Column("action", "Action", format="mono"),
                         Column("family", "Family", format="pill", density="dense"),
                         Column("routes", "Routes", align="right", format="count"),
                         Column("serves", "Serves", format="mono"),
                     )),
           )),
    Screen("keys", "/apps/:application?", "Applications and keys", "api",
           reads=("GET /api/apim/subscriptions",), action="apim.subscriptions.read",
           summary="Applications, their subscriptions, and the credentials "
                   "issued to them."),

    Screen("workspaces", "/admin/workspaces/:id?", "Workspaces", "admin",
           reads=("GET /api/admin/workspaces", "GET /api/admin/quota"),
           action="workspace.create",
           summary="Tenancy, quotas and headroom, and the grants that decide "
                   "who may read which dataset."),
)


def _authored(key: str) -> bool:
    """Whether a hand-built screen module exists on disk.

    Read rather than declared, so the flag cannot claim a screen that is not
    there — which is the failure mode this whole section exists to prevent, at
    one level down.
    """
    from .server import APP_ROOT

    return (APP_ROOT / "screens" / f"{key}.js").is_file()


def screens(
    *,
    verbs: VerbRegistry | None = None,
    routes: Sequence[tuple[str, str]] = (),
) -> tuple[Screen, ...]:
    """The screen manifest: designed screens, plus an inspector for the rest.

    An inspector is what keeps the manifest total. A verb group with no designed
    screen still gets one, so the platform never has a capability the interface
    cannot reach — which is §24's rule applied to the surface rather than to the
    registry.
    """
    verbs = verbs if verbs is not None else default_registry()
    known = {screen.key: screen for screen in DESIGNED}

    designed_verbs = {name for screen in DESIGNED for name in screen.verbs}
    for group, members in verbs.groups().items():
        uncovered = tuple(
            sorted(verb.name for verb in members if verb.name not in designed_verbs)
        )
        if not uncovered:
            # Every verb in this group already has a designed home. An inspector
            # would be a second, worse way to reach the same capability.
            continue
        # A group inspector is a *view of the verb catalogue*, not a place in
        # the product, so it hangs off `verbs` rather than taking a rail row of
        # its own. Eleven generated rows sitting beside the designed screens was
        # the rail advertising the implementation's shape instead of the
        # product's.
        known.setdefault(f"group-{group}", Screen(
            key=f"group-{group}",
            path=f"/inspect/{group}",
            title=group.replace("-", " ").title(),
            section="build",
            parent="verbs",
            crumbs=("verbs",),
            verbs=uncovered,
            action=f"{group}.*",
            # A group inspector is verb forms and nothing else, so it is one
            # block. That it is *composed* from the same registry an authored
            # screen draws from — rather than being a second rendering path
            # nobody maintains — is the whole point of naming components.
            blocks=(Block("runner"),),
        ))

    claimed = {route for screen in known.values() for route in screen.reads}
    for method, path in routes:
        if method != "GET" or f"{method} {path}" in claimed:
            continue
        key = "route" + path.replace("/api", "").replace("/", "-")
        known.setdefault(key, Screen(
            key=key,
            path=f"/inspect{path.replace('/api', '')}",
            title=path.replace("/api/", "").replace("/", " ").title(),
            section="build",
            parent="verbs",
            crumbs=("verbs",),
            reads=(f"{method} {path}",),
            action="platform.discover",
            # `auto`, because Python cannot know the shape of an arbitrary
            # route's body and declaring columns for each of these by hand
            # would be a list that drifts the first time a payload changes.
            # Rendering *by shape* at draw time is honest about what is known,
            # and it is still a component named in the manifest rather than a
            # second code path: rows become a table, fields become metrics, and
            # anything else says so instead of pretending.
            blocks=(Block("auto", source=f"{method} {path}"),),
        ))

    # Every screen nobody authored gets blocks, derived from what it declares
    # it reads. Without this the unauthored *designed* screens — Station,
    # Ledger, Reconciliation and the rest — would fall through to a JSON dump
    # while the generated inspectors composed, which is exactly backwards: the
    # screens somebody bothered to name are the ones most likely to be looked
    # at. Derived rather than listed, so a screen declaring a new read composes
    # it with no file edited.
    for key, screen in list(known.items()):
        if screen.blocks or _authored(key) or not screen.reads:
            continue
        known[key] = Screen(**{
            **screen.to_dict(), "reads": screen.reads, "verbs": screen.verbs,
            "events": screen.events, "crumbs": screen.crumbs,
            "blocks": tuple(
                Block("auto", source=read) for read in screen.reads
                if read.startswith("GET ")
            ),
        })

    # Destinations first within each section, then their views. The rail reads
    # the first group and a parent's page reads the second, so one ordering
    # serves both rather than each sorting the manifest its own way.
    return tuple(
        Screen(**{**screen.to_dict(), "authored": _authored(screen.key),
                  "reads": screen.reads, "verbs": screen.verbs,
                  "events": screen.events, "crumbs": screen.crumbs,
                  "blocks": screen.blocks})
        for screen in sorted(known.values(), key=lambda s: (
            SECTIONS.index(s.section) if s.section in SECTIONS else len(SECTIONS),
            s.parent or s.key,
            bool(s.parent),
            s.key,
        ))
    )


# --- the generated JavaScript client ------------------------------------


def _lexicon_words() -> dict[str, Any]:
    """The default lexicon, in the compact form the browser consumes.

    Imported here rather than at module scope: `slpie/context/` imports the
    contract to build its screen facets, and importing it back at the top would
    be a cycle. A function-local import is the ordinary answer and is what the
    codebase already does for the same reason elsewhere.
    """
    from ..context.lexicon import default

    return default().words()


def javascript(
    *,
    verbs: VerbRegistry | None = None,
    routes: Sequence[tuple[str, str]] = (),
) -> str:
    """A native ES module: the contract, as data, plus the client over it.

    The browser gets the same treatment the TypeScript clients get, and for a
    sharper reason. The composition type-check currently exists in **four**
    places — here in `typescript()`, twice in `app/compose.js` (`checkPipeline`
    and `kindAfter`), and once on the server in `Verb.accepts`. Four copies of
    one rule is precisely the drift this module was written to prevent, and the
    interface was the one consumer still maintaining its own.

    Emitted to `app/data/client.js` and committed rather than served from a
    route: the service worker precaches the shell, and a module generated at
    request time cannot boot with the network unplugged.

    No build step is involved. This is text, and the browser loads it as an ES
    module because `server.py` serves `.js` as `text/javascript`.
    """
    verbs = verbs if verbs is not None else default_registry()

    catalogue = {
        verb.name: {
            "group": verb.group,
            "summary": verb.summary,
            "consumes": verb.consumes.value if verb.consumes else Kind.NOTHING.value,
            "produces": verb.produces.value,
            "mutates": verb.mutates,
            "source": verb.is_source,
            "params": [
                {
                    "name": param.name,
                    "type": param.type,
                    "help": param.help,
                    "required": param.required,
                    "choices": list(param.choices or ()),
                }
                for param in verb.params
            ],
            "examples": list(verb.examples),
        }
        for verb in sorted(verbs, key=lambda item: item.name)
    }
    groups: dict[str, list[str]] = {}
    for name, entry in catalogue.items():
        groups.setdefault(entry["group"], []).append(name)

    manifest = [screen.to_dict() for screen in screens(verbs=verbs, routes=routes)]

    def block(name: str, value: Any) -> str:
        return f"export const {name} = Object.freeze({json.dumps(value, indent=2)});"

    return "\n".join([
        "// Generated from the SLPIE verb registry. Do not edit.",
        "// Regenerate with: slpie contract --javascript",
        f"// contract {CONTRACT_VERSION}",
        "",
        f'export const CONTRACT = "{CONTRACT_VERSION}";',
        "",
        block("KINDS", [kind.value for kind in Kind]),
        "",
        block("VERBS", catalogue),
        "",
        block("GROUPS", {group: sorted(names) for group, names in sorted(groups.items())}),
        "",
        block("SCREENS", manifest),
        "",
        "/** The platform's own words, baked so the first frame paints in them.",
        "  * `core/lexicon.js` swaps in a context's vocabulary from",
        "  * `GET /api/lexicon` once the caller is known — but a console must",
        "  * render correctly before that round trip, and offline it never",
        "  * happens at all. */",
        block("LEXICON", _lexicon_words()),
        "",
        block("ROUTES", [
            {"method": method, "path": path,
             "transport": "sse" if path == "/api/stream" else "json"}
            for method, path in sorted(routes)
        ]),
        "",
        "/** What is flowing after these stages. `same` passes the kind through. */",
        "export function producedKind(names) {",
        '  let current = "nothing";',
        "  for (const name of names) {",
        "    const verb = VERBS[name];",
        "    if (!verb) return current;",
        '    current = verb.produces === "same" ? current : verb.produces;',
        "  }",
        "  return current;",
        "}",
        "",
        "/** The first type error, or null. The server's rule, not a copy of it. */",
        "export function validate(names) {",
        '  let current = "nothing";',
        "  for (let index = 0; index < names.length; index += 1) {",
        "    const verb = VERBS[names[index]];",
        "    if (!verb) return `stage ${index + 1}: unknown verb \\`${names[index]}\\``;",
        '    if (verb.consumes !== "any" && verb.consumes !== current) {',
        '      if (current === "nothing") {',
        "        return `stage ${index + 1} \\`${names[index]}\\` needs ` +",
        "          `${verb.consumes.toUpperCase()} piped into it, but it starts ` +",
        "          `the pipeline — a source verb has to come first`;",
        "      }",
        "      return `stage ${index + 1} \\`${names[index]}\\` consumes ` +",
        "        `${verb.consumes.toUpperCase()}, but it was given ` +",
        "        `${current.toUpperCase()}`;",
        "    }",
        '    current = verb.produces === "same" ? current : verb.produces;',
        "  }",
        "  return null;",
        "}",
        "",
        "/** Every verb that could legally follow what is currently flowing. */",
        "export function successors(kind) {",
        "  return Object.keys(VERBS).filter((name) => {",
        "    const verb = VERBS[name];",
        '    return verb.consumes === "any" || verb.consumes === kind;',
        "  });",
        "}",
        "",
    ])
