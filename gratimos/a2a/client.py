"""Calling other agents.

:class:`A2AClient` is the outbound half of the protocol. It sends a message,
follows the task to a terminal state, and gives back something a caller can use
directly — text, structured data, or shaped payloads ready for the hub.

Delegation is where an agent stops being self-contained, so the client is
opinionated about two things: every exchange is recorded on the ContextFlow, and
a peer that asks for more input does not silently hang — ``input-required`` comes
back as a state the caller must handle.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Sequence

from ..contextflow import ContextFlow, EventKind
from ..errors import AgentError, ProtocolError
from ..ids import digest, new_id, slug
from ..meta import Wrapped
from .transport import HttpAuth, HttpTransport, Transport
from .types import (
    AgentCard,
    Artifact,
    DataPart,
    Message,
    Method,
    Part,
    Role,
    RpcRequest,
    Task,
    TaskState,
    TextPart,
    parts_from,
)


@dataclass(slots=True)
class Exchange:
    """One completed round trip with a peer."""

    agent: str
    task: Task
    sent: Message
    duration_s: float = 0.0
    event_id: str = ""

    @property
    def state(self) -> TaskState:
        return self.task.state

    @property
    def ok(self) -> bool:
        return self.task.state is TaskState.COMPLETED

    @property
    def needs_input(self) -> bool:
        return self.task.state.waiting_on_caller

    def text(self) -> str:
        reply = self.task.reply()
        return reply.text_content() if reply else ""

    def data(self) -> list[Any]:
        """Structured payloads from the reply and from every artifact.

        A peer may legitimately return the same payload twice — once as the
        conversational reply and once as the durable artifact — so identical
        payloads are collapsed rather than handed back as duplicates.
        """
        out: list[Any] = []
        seen: set[str] = set()
        candidates: list[Any] = []
        reply = self.task.reply()
        if reply:
            candidates.extend(reply.data_content())
        for artifact in self.task.artifacts:
            candidates.extend(p.data for p in artifact.parts if isinstance(p, DataPart))
        for item in candidates:
            fingerprint = digest(item)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            out.append(item)
        return out

    def payloads(self, *, source: str = "") -> list[Wrapped]:
        """Reply data as shaped wrappers, ready to publish to a hub."""
        origin = source or f"a2a://{slug(self.agent)}"
        out: list[Wrapped] = []
        for index, item in enumerate(self.data()):
            name = f"{slug(self.agent)}_{index}" if index else slug(self.agent)
            out.append(Wrapped.of(item, name=name, source=origin, probe="a2a"))
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "task_id": self.task.id,
            "context_id": self.task.context_id,
            "state": self.state.value,
            "ok": self.ok,
            "duration_s": round(self.duration_s, 4),
            "text": self.text()[:500],
            "artifacts": [a.name for a in self.task.artifacts],
            "event_id": self.event_id,
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Exchange {self.agent} {self.state.value} artifacts={len(self.task.artifacts)}>"


class A2AClient:
    """Speaks A2A to one peer."""

    def __init__(
        self,
        transport: Transport,
        *,
        flow: ContextFlow | None = None,
        actor: str = "gratimos",
        context_id: str = "",
    ) -> None:
        self.transport = transport
        self.flow = flow
        self.actor = actor
        self.context_id = context_id or new_id("ctx")
        self._card: AgentCard | None = None

    @classmethod
    def http(cls, url: str, *, auth: HttpAuth | None = None, timeout: float = 60.0,
             flow: ContextFlow | None = None, **kwargs: Any) -> "A2AClient":
        """Connect to a peer over HTTP."""
        return cls(HttpTransport(url, auth=auth, timeout=timeout), flow=flow, **kwargs)

    # -- discovery -------------------------------------------------------

    @property
    def card(self) -> AgentCard:
        if self._card is None:
            self._card = self.transport.agent_card()
        return self._card

    @property
    def name(self) -> str:
        return self.card.name

    def skills(self) -> list[str]:
        return [s.id for s in self.card.skills]

    def supports(self, skill_id: str) -> bool:
        return self.card.skill(skill_id) is not None

    # -- sending ---------------------------------------------------------

    def send(
        self,
        payload: Any,
        *,
        skill: str = "",
        task_id: str = "",
        parts: Sequence[Part] = (),
        metadata: Mapping[str, Any] | None = None,
        blocking: bool = True,
    ) -> Exchange:
        """Send a message and return the resulting exchange."""
        if skill and not self.supports(skill):
            raise AgentError(
                f"{self.name!r} does not advertise skill {skill!r}; it has: {self.skills()}"
            )
        message = Message(
            role=Role.USER,
            parts=tuple(parts) if parts else parts_from(payload),
            task_id=task_id,
            context_id=self.context_id,
            metadata={**({"skill": skill} if skill else {}), **dict(metadata or {})},
        )
        params: dict[str, Any] = {"message": message.to_dict()}
        if blocking:
            params["configuration"] = {"blocking": True}

        started = time.monotonic()
        response = self.transport.call(RpcRequest(method=Method.SEND.value, params=params))
        duration = time.monotonic() - started

        if response.error is not None:
            raise ProtocolError(
                f"{self.name} returned error {response.error.code}: {response.error.message}"
            )
        task = _task_from_result(response.result)
        exchange = Exchange(agent=self.name, task=task, sent=message, duration_s=duration)
        self._record(exchange)
        return exchange

    def ask(self, text: str, **kwargs: Any) -> str:
        """Send text, get text. The simplest possible delegation."""
        return self.send(text, **kwargs).text()

    def delegate(self, payload: Wrapped, *, skill: str = "", instruction: str = "") -> Exchange:
        """Hand a shaped payload to a peer, shape and provenance included."""
        parts: list[Part] = []
        if instruction:
            parts.append(TextPart(text=instruction))
        parts.append(DataPart(
            data=payload.records(),
            metadata={
                "entity": payload.name,
                "shape": payload.shape.to_dict(),
                "rows": payload.rows,
                "source": payload.provenance.source,
            },
        ))
        return self.send(None, skill=skill, parts=parts)

    def stream(self, payload: Any, *, skill: str = "", task_id: str = "") -> Iterator[Mapping[str, Any]]:
        """Stream lifecycle events instead of waiting for the terminal state."""
        if not self.card.capabilities.streaming:
            raise AgentError(f"{self.name!r} does not advertise streaming support")
        message = Message(
            role=Role.USER, parts=parts_from(payload), task_id=task_id,
            context_id=self.context_id, metadata={"skill": skill} if skill else {},
        )
        request = RpcRequest(method=Method.STREAM.value, params={"message": message.to_dict()})
        yield from self.transport.stream(request)

    # -- task management -------------------------------------------------

    def get(self, task_id: str, *, history_length: int = 0) -> Task:
        params: dict[str, Any] = {"id": task_id}
        if history_length:
            params["historyLength"] = history_length
        response = self.transport.call(RpcRequest(method=Method.GET.value, params=params))
        if response.error is not None:
            raise ProtocolError(f"tasks/get failed: {response.error.message}")
        return _task_from_result(response.result)

    def cancel(self, task_id: str) -> Task:
        response = self.transport.call(RpcRequest(method=Method.CANCEL.value, params={"id": task_id}))
        if response.error is not None:
            raise ProtocolError(f"tasks/cancel failed: {response.error.message}")
        return _task_from_result(response.result)

    def wait(self, task_id: str, *, timeout_s: float = 120.0, poll_s: float = 0.5) -> Task:
        """Poll a task until it reaches a terminal state or needs input."""
        deadline = time.monotonic() + timeout_s
        while True:
            task = self.get(task_id)
            if task.done or task.state.waiting_on_caller:
                return task
            if time.monotonic() >= deadline:
                raise AgentError(
                    f"task {task_id!r} still {task.state.value} after {timeout_s}s"
                )
            time.sleep(poll_s)

    def respond(self, task_id: str, payload: Any) -> Exchange:
        """Answer a peer that asked for more input."""
        return self.send(payload, task_id=task_id)

    # -- trace -----------------------------------------------------------

    def _record(self, exchange: Exchange) -> None:
        if self.flow is None:
            return
        event = self.flow.emit(
            EventKind.AGENT,
            actor=self.actor,
            paths=[f"agent.{slug(exchange.agent)}.{slug(self.context_id)}"],
            payload={
                "direction": "sent",
                "agent": exchange.agent,
                "task_id": exchange.task.id,
                "context_id": exchange.task.context_id,
                "state": exchange.state.value,
                "ok": exchange.ok,
                "duration_s": round(exchange.duration_s, 4),
                "sent_preview": exchange.sent.text_content()[:200],
                "reply_preview": exchange.text()[:200],
                "artifacts": [a.name for a in exchange.task.artifacts],
                "data_payloads": len(exchange.data()),
            },
            labels={"protocol": "a2a", "transport": self.transport.name},
        )
        exchange.event_id = event.id

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<A2AClient -> {self._card.name if self._card else 'undiscovered'} via {self.transport.name}>"


def _task_from_result(result: Any) -> Task:
    """A peer may answer with a Task or a bare Message; normalize to a Task."""
    if not isinstance(result, Mapping):
        raise ProtocolError(f"expected a task or message object, got {type(result).__name__}")
    if result.get("kind") == "message" or ("parts" in result and "status" not in result):
        message = Message.from_dict(result)
        task = Task(id=message.task_id or new_id("task"), context_id=message.context_id or new_id("ctx"))
        task.advance(TaskState.COMPLETED, message)
        return task
    return Task.from_dict(result)
