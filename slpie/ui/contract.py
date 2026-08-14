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
