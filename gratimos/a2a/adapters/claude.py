"""Claude as an A2A agent.

Wraps the Anthropic Messages API in the :class:`~gratimos.a2a.server.AgentServer`
executor contract, so a Claude-backed specialist is addressable over A2A exactly
like any other peer — same card, same task lifecycle, same trace.

Two things make this more than a thin HTTP wrapper:

* **Shaped data crosses intact.** A ``DataPart`` carrying records plus their
  inferred shape is rendered so the model sees the structure, not a wall of
  JSON — which is the difference between an answer about the data and an
  answer about its formatting.
* **Kernel tools.** The executor can expose the hub's catalog and payloads as
  Claude tools, so a peer can ask questions that require actually looking at
  the data rather than being told about it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ...errors import AgentError
from ...ids import slug
from ..server import AgentServer, ExecutionContext, build_card
from ..types import AgentSkill, DataPart, FilePart, TextPart

#: Default model. Opus 5 is the current top-tier model; override deliberately.
DEFAULT_MODEL = "claude-opus-5"
#: Beta flag for server-side refusal fallbacks (`fallbacks="default"`).
FALLBACK_BETA = "server-side-fallback-2026-07-01"
#: Streaming is required for large outputs; the SDK refuses long non-streaming calls.
STREAMING_MAX_TOKENS = 64_000
BLOCKING_MAX_TOKENS = 16_000

SYSTEM_PROMPT = """You are a specialist agent inside a Gratimos kernel — a system that \
discovers data in its environment, infers shapes from it, and generates code against \
those shapes.

You receive payloads that carry both records and the shape observed for them. Trust the \
shape as evidence, not as a contract: it describes what has been seen so far, and part \
of your job is noticing where the data disagrees with it.

Be concrete. When you find a problem, name the field and the evidence. When you propose \
a transformation, describe it precisely enough to implement. Prefer a short answer that \
is checkable over a long one that is not."""


@dataclass(frozen=True, slots=True)
class ClaudeConfig:
    """How the Claude-backed executor calls the API."""

    model: str = DEFAULT_MODEL
    #: 0 selects a sensible default for the chosen transport mode.
    max_tokens: int = 0
    #: Thinking depth and overall token spend: low | medium | high | xhigh | max.
    effort: str = "high"
    #: Stream by default — long analyses otherwise risk an HTTP timeout.
    stream: bool = True
    #: "summarized" surfaces reasoning; "omitted" (the API default) hides it.
    thinking_display: str = "summarized"
    #: Opt into server-side refusal fallbacks. Recommended on Opus-5-class models.
    server_side_fallbacks: bool = True
    system: str = SYSTEM_PROMPT
    max_tool_rounds: int = 8
    timeout_s: float = 600.0

    def token_budget(self) -> int:
        if self.max_tokens:
            return self.max_tokens
        return STREAMING_MAX_TOKENS if self.stream else BLOCKING_MAX_TOKENS


@dataclass(slots=True)
class ClaudeTool:
    """A kernel capability Claude may call while answering."""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    run: Callable[[Mapping[str, Any]], Any]

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
        }


def hub_tools(hub: Any) -> list[ClaudeTool]:
    """Expose a :class:`~gratimos.hubs.DataHub` as tools Claude can call."""

    def list_entities(_: Mapping[str, Any]) -> Any:
        return {"entities": hub.entities(), "catalog": hub.meta.catalog()}

    def describe_entity(args: Mapping[str, Any]) -> Any:
        entity = str(args.get("entity", ""))
        shape = hub.shape(entity)
        if shape is None:
            return {"error": f"unknown entity {entity!r}", "known": hub.entities()}
        return shape.to_dict()

    def sample_entity(args: Mapping[str, Any]) -> Any:
        entity = str(args.get("entity", ""))
        limit = max(1, min(int(args.get("limit", 5)), 50))
        payload = hub.latest(entity)
        if payload is None:
            return {"error": f"no payload held for {entity!r}", "known": hub.entities()}
        return {"entity": entity, "rows": payload.records()[:limit], "total_rows": payload.rows}

    return [
        ClaudeTool(
            name="list_entities",
            description=(
                "List every entity the kernel currently holds, with field types and "
                "revision numbers. Call this first when you need to know what data exists."
            ),
            input_schema={"type": "object", "properties": {}},
            run=list_entities,
        ),
        ClaudeTool(
            name="describe_entity",
            description=(
                "Return the full inferred shape of one entity: every field, its type, "
                "nullability, and observation counts. Call this when you need the schema."
            ),
            input_schema={
                "type": "object",
                "properties": {"entity": {"type": "string", "description": "Entity name."}},
                "required": ["entity"],
            },
            run=describe_entity,
        ),
        ClaudeTool(
            name="sample_entity",
            description=(
                "Return actual rows from an entity. Call this when the shape alone cannot "
                "answer the question and you need to look at the values."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "entity": {"type": "string", "description": "Entity name."},
                    "limit": {"type": "integer", "description": "Rows to return (1-50)."},
                },
                "required": ["entity"],
            },
            run=sample_entity,
        ),
    ]


class ClaudeExecutor:
    """Drives a task by asking Claude, with an optional tool loop."""

    def __init__(
        self,
        config: ClaudeConfig | None = None,
        *,
        tools: Sequence[ClaudeTool] = (),
        client: Any = None,
    ) -> None:
        self.config = config if config is not None else ClaudeConfig()
        self.tools = list(tools)
        self._client = client

    # -- client ----------------------------------------------------------

    @property
    def client(self) -> Any:
        """The Anthropic client, imported on first use."""
        if self._client is None:
            try:
                import anthropic  # noqa: PLC0415 - optional dependency
            except ImportError as exc:
                raise AgentError(
                    "the Claude adapter needs the Anthropic SDK: pip install anthropic"
                ) from exc
            # Zero-arg construction resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN,
            # or an `ant auth login` profile — do not hardcode a key here.
            self._client = anthropic.Anthropic(timeout=self.config.timeout_s)
        return self._client

    # -- executor contract ----------------------------------------------

    def __call__(self, context: ExecutionContext) -> None:
        context.working("calling Claude")
        prompt = self._render(context)
        if not prompt.strip():
            context.reject("message contained no readable content")
            return

        try:
            text, artifacts = self._converse(prompt, context)
        except AgentError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface API faults as task failures
            context.fail(f"{type(exc).__name__}: {exc}")
            return

        for name, payload in artifacts:
            context.artifact(name, payload, description="produced by a Claude tool call")
        context.complete(text)

    # -- prompt rendering ------------------------------------------------

    def _render(self, context: ExecutionContext) -> str:
        """Turn A2A parts into a prompt that preserves structure."""
        chunks: list[str] = []
        for part in context.message.parts:
            if isinstance(part, TextPart):
                chunks.append(part.text)
            elif isinstance(part, DataPart):
                chunks.append(self._render_data(part))
            elif isinstance(part, FilePart):
                chunks.append(
                    f"<file name=\"{part.name or 'attachment'}\" "
                    f"type=\"{part.mime_type}\" bytes=\"{len(part.bytes_b64) * 3 // 4}\" />"
                )
        if skill := context.skill():
            chunks.insert(0, f"<requested_skill>{skill}</requested_skill>")
        return "\n\n".join(chunk for chunk in chunks if chunk.strip())

    @staticmethod
    def _render_data(part: DataPart) -> str:
        """Render a data part with its shape, capping the rows sent inline."""
        metadata = dict(part.metadata)
        entity = metadata.get("entity", "payload")
        shape = metadata.get("shape") or {}
        fields = shape.get("fields", []) if isinstance(shape, Mapping) else []
        schema = "\n".join(
            f"  {f.get('ident', f.get('name'))}: {f.get('tag')}"
            + (" (nullable)" if f.get("nullable") else "")
            for f in fields
        )
        rows = part.data if isinstance(part.data, list) else [part.data]
        shown = rows[:50]
        body = json.dumps(shown, indent=2, default=str)
        omitted = len(rows) - len(shown)
        note = f"\n<!-- {omitted} further row(s) omitted -->" if omitted > 0 else ""
        return (
            f"<entity name=\"{entity}\" rows=\"{metadata.get('rows', len(rows))}\">\n"
            + (f"<observed_shape>\n{schema}\n</observed_shape>\n" if schema else "")
            + f"<records>\n{body}{note}\n</records>\n</entity>"
        )

    # -- API calls -------------------------------------------------------

    def _request_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.token_budget(),
            "system": self.config.system,
            "thinking": {"type": "adaptive", "display": self.config.thinking_display},
            "output_config": {"effort": self.config.effort},
        }
        if self.tools:
            kwargs["tools"] = [tool.definition() for tool in self.tools]
        return kwargs

    def _converse(self, prompt: str, context: ExecutionContext) -> tuple[str, list[tuple[str, Any]]]:
        """Run the tool loop until Claude stops calling tools."""
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        artifacts: list[tuple[str, Any]] = []
        by_name = {tool.name: tool for tool in self.tools}

        for round_index in range(self.config.max_tool_rounds):
            response = self._call(messages)

            # Safety classifiers can decline; content may be empty or partial.
            if getattr(response, "stop_reason", "") == "refusal":
                details = getattr(response, "stop_details", None)
                category = getattr(details, "category", None) if details else None
                raise AgentError(
                    f"Claude declined this request"
                    + (f" (category: {category})" if category else "")
                )

            if response.stop_reason != "tool_use":
                return self._text_of(response), artifacts

            calls = [b for b in response.content if getattr(b, "type", "") == "tool_use"]
            messages.append({"role": "assistant", "content": response.content})
            results: list[dict[str, Any]] = []
            for call in calls:
                context.working(f"tool: {call.name}")
                tool = by_name.get(call.name)
                if tool is None:
                    results.append({
                        "type": "tool_result", "tool_use_id": call.id,
                        "content": f"unknown tool: {call.name}", "is_error": True,
                    })
                    continue
                try:
                    output = tool.run(dict(call.input))
                    artifacts.append((f"{slug(call.name)}_{round_index}", output))
                    results.append({
                        "type": "tool_result", "tool_use_id": call.id,
                        "content": json.dumps(output, default=str)[:100_000],
                    })
                except Exception as exc:  # noqa: BLE001 - report to the model, keep going
                    results.append({
                        "type": "tool_result", "tool_use_id": call.id,
                        "content": f"{type(exc).__name__}: {exc}", "is_error": True,
                    })
            messages.append({"role": "user", "content": results})

        raise AgentError(
            f"Claude did not finish within {self.config.max_tool_rounds} tool rounds"
        )

    def _call(self, messages: list[dict[str, Any]]) -> Any:
        kwargs = self._request_kwargs()
        kwargs["messages"] = messages
        if self.config.server_side_fallbacks:
            # A policy decline is re-run on Anthropic's recommended fallback model
            # inside the same call, rather than surfacing as a dead end.
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
            surface = self.client.beta.messages
        else:
            surface = self.client.messages

        if self.config.stream:
            with surface.stream(**kwargs) as stream:
                return stream.get_final_message()
        return surface.create(**kwargs)

    @staticmethod
    def _text_of(response: Any) -> str:
        return "\n".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        ).strip()


def claude_agent(
    name: str = "claude-analyst",
    *,
    config: ClaudeConfig | None = None,
    hub: Any = None,
    tools: Sequence[ClaudeTool] = (),
    flow: Any = None,
    description: str = "",
    url: str = "",
) -> AgentServer:
    """Build an A2A server backed by Claude.

    Passing ``hub`` grants the kernel-inspection tools, which is what turns this
    from a text endpoint into an agent that can actually look at the data.
    """
    all_tools = list(tools) + (hub_tools(hub) if hub is not None else [])
    skills = [
        AgentSkill(
            id="analyze",
            name="Analyze shaped data",
            description="Reason over records and their inferred shapes; find anomalies and propose transformations.",
            tags=("analysis", "data", "shapes"),
            examples=("Which fields in `orders` look mistyped?",),
        ),
        AgentSkill(
            id="explain",
            name="Explain the context",
            description="Answer questions about what the kernel has discovered and why it decided what it did.",
            tags=("explain", "catalog"),
        ),
    ]
    if all_tools:
        skills.append(AgentSkill(
            id="inspect",
            name="Inspect the kernel",
            description="Query the data hub directly — list entities, read shapes, sample rows.",
            tags=("inspect", "tools"),
        ))
    card = build_card(
        name,
        description=description or "Claude-backed analyst inside a Gratimos kernel.",
        url=url,
        skills=skills,
        streaming=True,
        provider={"organization": "Anthropic", "model": (config or ClaudeConfig()).model},
    )
    return AgentServer(card, ClaudeExecutor(config, tools=all_tools), flow=flow, actor="claude")
