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
from dataclasses import dataclass
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
    authored: bool = False               # a hand-built screens/<key>.js exists

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
            "authored": self.authored,
        }


#: The screens with a designed identity. Everything else is generated below as an
#: inspector, and an inspector is deliberately utilitarian: four finished screens
#: and thirty-two honest inspectors reads as a platform, where thirty-six
#: half-designed screens reads as a broken product.
DESIGNED: tuple[Screen, ...] = (
    Screen("console", "/", "Console", "console",
           reads=("GET /api/status", "GET /api/manifest", "GET /api/stream"),
           verbs=("ask", "options", "suggest", "accept", "dismiss"),
           events=("*",), action="intelligence.ask"),
    Screen("compose", "/compose", "Compose", "operate",
           reads=("GET /api/verbs",), verbs=("routine",),
           action="platform.discover"),
    Screen("findings", "/findings/:severity?", "Findings", "operate",
           reads=("GET /api/findings",), verbs=("findings", "govern", "rules"),
           events=("finding_raised", "constraint_violated"),
           action="analysis.findings"),
    Screen("graph", "/graph", "Graph", "operate",
           reads=("GET /api/graph",), verbs=("graph", "search"),
           events=("node_asserted", "edge_asserted", "node_retired"),
           action="environment.graph"),
    Screen("node", "/node/:id", "Node", "operate",
           reads=("GET /api/node",), events=("node_asserted", "node_retired"),
           action="environment.graph", crumbs=("graph",)),
    Screen("impact", "/impact/:id", "Impact", "operate",
           reads=("GET /api/impact",), verbs=("impact", "radius"),
           action="environment.impact", crumbs=("graph", "node")),
    Screen("cycles", "/cycles", "Cycles", "operate",
           reads=("GET /api/cycles",), action="environment.graph"),
    Screen("reconcile", "/reconcile", "Reconciliation", "operate",
           reads=("GET /api/reconcile",), verbs=("reconcile", "declare"),
           events=("contradiction_found",), action="environment.reconcile"),
    Screen("station", "/station", "Station", "operate",
           reads=("GET /api/station",), verbs=("attach", "gaps", "status"),
           events=("element_attached", "capability_refused"),
           action="environment.attach"),
    Screen("history", "/history/:subject?", "History", "operate",
           reads=("GET /api/history", "GET /api/causation"),
           verbs=("history",), action="dispatch.history"),
    Screen("ledger", "/ledger", "Ledger", "operate",
           reads=("GET /api/integrity", "GET /api/projections",
                  "GET /api/stream/status"),
           action="platform.discover"),
    Screen("simulator", "/simulator", "Simulator", "operate",
           reads=("GET /api/scenarios",), verbs=("simulate", "fire", "target"),
           events=("scenario_fired", "target_changed"),
           action="environment.target"),
    Screen("catalog", "/catalog/:tenant?/:realm?/:dataset?/:object?", "Catalog",
           "catalog", reads=("GET /api/search", "GET /api/admin/datasets"),
           verbs=("scan", "changed"),
           events=("node_asserted", "node_retired"), action="dataset.read"),
    Screen("portal", "/portal/:api?", "Developer portal", "api",
           reads=("GET /api/manual", "GET /api/contract"),
           action="platform.discover"),
    Screen("gateway", "/gateway", "Gateway", "api",
           reads=("GET /api/routes",), action="apim.gateway.read"),
    Screen("workspaces", "/admin/workspaces/:id?", "Workspaces", "admin",
           reads=("GET /api/admin/workspaces", "GET /api/admin/quota"),
           action="workspace.create"),
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
        known.setdefault(f"group-{group}", Screen(
            key=f"group-{group}",
            path=f"/inspect/{group}",
            title=group.replace("-", " ").title(),
            section="build",
            verbs=uncovered,
            action=f"{group}.*",
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
            reads=(f"{method} {path}",),
            action="platform.discover",
        ))

    return tuple(
        Screen(**{**screen.to_dict(), "authored": _authored(screen.key),
                  "reads": screen.reads, "verbs": screen.verbs,
                  "events": screen.events, "crumbs": screen.crumbs})
        for screen in sorted(known.values(), key=lambda s: (
            SECTIONS.index(s.section) if s.section in SECTIONS else len(SECTIONS),
            s.key,
        ))
    )


# --- the generated JavaScript client ------------------------------------


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
