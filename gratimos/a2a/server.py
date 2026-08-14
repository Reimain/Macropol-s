"""Hosting an A2A agent.

:class:`AgentServer` implements the protocol surface — the JSON-RPC methods, the
task lifecycle, the agent card — and delegates the actual work to an executor.
An executor is just a function that receives an :class:`ExecutionContext` and
drives the task through its states.

Every inbound and outbound exchange is recorded on the ContextFlow as an
``AGENT`` event, so a conversation with a peer sits in the same trace as the
data changes it caused. That is the point of routing agent traffic through the
kernel rather than around it: when a peer's answer reshapes a model, the trace
shows the message that did it.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Mapping, Protocol, Sequence

from ..contextflow import ContextFlow, EventKind
from ..errors import AgentError, TaskNotFound
from ..ids import new_id, slug
from .transport import InProcessTransport, error_response
from .types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Artifact,
    ErrorCode,
    Message,
    Method,
    Part,
    Role,
    RpcRequest,
    RpcResponse,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
    parts_from,
)


class TaskStore:
    """Where tasks live between calls."""

    def __init__(self, *, limit: int = 10_000) -> None:
        self._tasks: dict[str, Task] = {}
        self._by_context: dict[str, list[str]] = {}
        self._limit = limit
        self._lock = threading.RLock()

    def save(self, task: Task) -> Task:
        with self._lock:
            self._tasks[task.id] = task
            ids = self._by_context.setdefault(task.context_id, [])
            if task.id not in ids:
                ids.append(task.id)
            if len(self._tasks) > self._limit:
                self._evict()
            return task

    def get(self, task_id: str) -> Task:
        task = self._tasks.get(task_id)
        if task is None:
            raise TaskNotFound(f"no such task: {task_id!r}")
        return task

    def find(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def context(self, context_id: str) -> list[Task]:
        return [self._tasks[i] for i in self._by_context.get(context_id, ()) if i in self._tasks]

    def all(self) -> list[Task]:
        return list(self._tasks.values())

    def _evict(self) -> None:
        """Drop the oldest terminal tasks first; never evict live work."""
        done = [t for t in self._tasks.values() if t.done]
        for task in done[: max(len(self._tasks) - self._limit, 0)]:
            self._tasks.pop(task.id, None)

    def __len__(self) -> int:
        return len(self._tasks)


@dataclass(slots=True)
class ExecutionContext:
    """What an executor is handed, and how it reports progress."""

    task: Task
    message: Message
    server: "AgentServer"
    events: list[dict[str, Any]] = field(default_factory=list)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    # -- reading ---------------------------------------------------------

    @property
    def text(self) -> str:
        return self.message.text_content()

    @property
    def data(self) -> list[Any]:
        return self.message.data_content()

    def skill(self) -> str:
        """The skill the caller asked for, if they named one."""
        return str(self.message.metadata.get("skill", "")) or str(self.metadata.get("skill", ""))

    # -- reporting -------------------------------------------------------

    def working(self, note: str = "") -> None:
        self._advance(TaskState.WORKING, note)

    def input_required(self, prompt: str) -> None:
        self._advance(TaskState.INPUT_REQUIRED, prompt)

    def complete(self, reply: Any = None) -> None:
        self._advance(TaskState.COMPLETED, reply)

    def fail(self, reason: str) -> None:
        self._advance(TaskState.FAILED, reason)

    def reject(self, reason: str) -> None:
        self._advance(TaskState.REJECTED, reason)

    def artifact(self, name: str, payload: Any, *, description: str = "") -> Artifact:
        artifact = Artifact(name=name, parts=parts_from(payload), description=description)
        self.task.add_artifact(artifact)
        self.events.append({
            "kind": "artifact-update",
            "taskId": self.task.id,
            "contextId": self.task.context_id,
            "artifact": artifact.to_dict(),
            "lastChunk": True,
        })
        return artifact

    def _advance(self, state: TaskState, payload: Any = None) -> None:
        message = None
        if payload is not None and payload != "":
            message = (
                payload if isinstance(payload, Message)
                else Message(role=Role.AGENT, parts=parts_from(payload),
                             task_id=self.task.id, context_id=self.task.context_id)
            )
        self.task.advance(state, message)
        self.events.append({
            "kind": "status-update",
            "taskId": self.task.id,
            "contextId": self.task.context_id,
            "status": self.task.status.to_dict(),
            "final": state.terminal,
        })


class Executor(Protocol):  # pragma: no cover - structural typing
    def __call__(self, context: ExecutionContext) -> None: ...


class AgentServer:
    """An A2A endpoint: a card, a task store, and an executor behind them."""

    def __init__(
        self,
        card: AgentCard,
        executor: Executor,
        *,
        flow: ContextFlow | None = None,
        store: TaskStore | None = None,
        actor: str = "",
    ) -> None:
        self.card = card
        self.executor = executor
        self.flow = flow
        self.store = store if store is not None else TaskStore()
        self.actor = actor or f"agent:{slug(card.name)}"

    # -- JSON-RPC surface ------------------------------------------------

    def handle(self, request: RpcRequest) -> RpcResponse:
        """Dispatch one JSON-RPC call."""
        try:
            if request.method in (Method.SEND.value, Method.STREAM.value):
                return RpcResponse(id=request.id, result=self._send(request.params).to_dict())
            if request.method == Method.GET.value:
                task = self.store.get(str(request.params.get("id", "")))
                length = int(request.params.get("historyLength", 0) or 0)
                return RpcResponse(id=request.id, result=task.to_dict(history_length=length))
            if request.method == Method.CANCEL.value:
                return RpcResponse(id=request.id, result=self._cancel(str(request.params.get("id", ""))).to_dict())
            if request.method == Method.CARD.value:
                return RpcResponse(id=request.id, result=self.card.to_dict())
            if request.method in (Method.PUSH_SET.value, Method.PUSH_GET.value):
                if not self.card.capabilities.push_notifications:
                    return error_response(
                        request.id, ErrorCode.PUSH_NOT_SUPPORTED,
                        "this agent does not advertise push notification support",
                    )
                return error_response(
                    request.id, ErrorCode.UNSUPPORTED_OPERATION,
                    "push notification configuration is not implemented by this server",
                )
            return error_response(
                request.id, ErrorCode.METHOD_NOT_FOUND, f"unknown method: {request.method!r}"
            )
        except TaskNotFound as exc:
            return error_response(request.id, ErrorCode.TASK_NOT_FOUND, str(exc))
        except AgentError as exc:
            return error_response(request.id, ErrorCode.INVALID_PARAMS, str(exc))
        except Exception as exc:  # noqa: BLE001 - a JSON-RPC server must always answer
            return error_response(
                request.id, ErrorCode.INTERNAL_ERROR,
                f"{type(exc).__name__}: {exc}",
            )

    def handle_stream(self, request: RpcRequest) -> Iterator[Mapping[str, Any]]:
        """Run a call and yield its lifecycle events in order."""
        if request.method not in (Method.STREAM.value, Method.SEND.value):
            yield self.handle(request).to_dict()
            return
        message = Message.from_dict(request.params.get("message", {}))
        task, context = self._prepare(message, request.params)
        yield {"kind": "task", **task.to_dict()}
        self._run(context)
        for event in context.events:
            yield event

    # -- method implementations ------------------------------------------

    def _send(self, params: Mapping[str, Any]) -> Task:
        raw = params.get("message")
        if not isinstance(raw, Mapping):
            raise AgentError("message/send requires a `message` object in params")
        message = Message.from_dict(raw)
        if not message.parts:
            raise AgentError("message contains no parts")
        task, context = self._prepare(message, params)
        self._run(context)
        return task

    def _prepare(self, message: Message, params: Mapping[str, Any]) -> tuple[Task, ExecutionContext]:
        existing = self.store.find(message.task_id) if message.task_id else None
        if existing is not None and existing.done:
            raise AgentError(
                f"task {existing.id!r} is already {existing.state.value}; start a new task"
            )
        task = existing or Task(context_id=message.context_id or new_id("ctx"))
        task.history.append(message)
        self.store.save(task)
        self._record("received", task, message)
        configuration = params.get("configuration") or {}
        return task, ExecutionContext(
            task=task, message=message, server=self, metadata=dict(configuration)
        )

    def _run(self, context: ExecutionContext) -> None:
        try:
            self.executor(context)
        except Exception as exc:  # noqa: BLE001 - executor faults become task failures
            context.fail(f"{type(exc).__name__}: {exc}")
        if not context.task.done and not context.task.state.waiting_on_caller:
            # An executor that returned without deciding has completed by
            # default; leaving a task in `working` forever is worse.
            context.complete()
        self.store.save(context.task)
        self._record("answered", context.task, context.task.reply())

    def _cancel(self, task_id: str) -> Task:
        task = self.store.get(task_id)
        if task.done:
            raise AgentError(f"task {task_id!r} is {task.state.value} and cannot be canceled")
        task.advance(TaskState.CANCELED, Message.text("canceled by caller", role=Role.AGENT))
        self.store.save(task)
        self._record("canceled", task, None)
        return task

    # -- trace -----------------------------------------------------------

    def _record(self, direction: str, task: Task, message: Message | None) -> None:
        if self.flow is None:
            return
        self.flow.emit(
            EventKind.AGENT,
            actor=self.actor,
            paths=[f"agent.{slug(self.card.name)}.{slug(task.context_id)}"],
            payload={
                "direction": direction,
                "agent": self.card.name,
                "task_id": task.id,
                "context_id": task.context_id,
                "state": task.state.value,
                "parts": len(message.parts) if message else 0,
                "preview": (message.text_content()[:200] if message else ""),
                "artifacts": [a.name for a in task.artifacts],
            },
            labels={"protocol": "a2a", "transport": "local"},
        )

    # -- convenience -----------------------------------------------------

    def transport(self) -> InProcessTransport:
        """An in-process transport pointing at this server."""
        return InProcessTransport(self.handle, self.card, self.handle_stream)

    def report(self) -> dict[str, Any]:
        tasks = self.store.all()
        by_state: dict[str, int] = {}
        for task in tasks:
            by_state[task.state.value] = by_state.get(task.state.value, 0) + 1
        return {
            "agent": self.card.name,
            "skills": [s.id for s in self.card.skills],
            "tasks": len(tasks),
            "by_state": dict(sorted(by_state.items())),
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<AgentServer {self.card.name} tasks={len(self.store)}>"


def build_card(
    name: str,
    *,
    description: str = "",
    url: str = "",
    skills: Sequence[AgentSkill] = (),
    streaming: bool = True,
    version: str = "0.1.0",
    provider: Mapping[str, Any] | None = None,
    documentation_url: str = "",
    **extra: Any,
) -> AgentCard:
    """Build an agent card without assembling the nested objects by hand.

    Spec-defined fields are named parameters so they land on the card's own
    attributes; anything else goes to ``extra`` and is serialized alongside
    them, which is how a deployment adds its own metadata without a subclass.
    """
    return AgentCard(
        name=name,
        description=description,
        url=url,
        version=version,
        capabilities=AgentCapabilities(streaming=streaming, state_transition_history=True),
        skills=tuple(skills),
        provider=dict(provider or {}),
        documentation_url=documentation_url,
        extra=extra,
    )


def serve_http(server: AgentServer, host: str = "127.0.0.1", port: int = 0) -> Any:
    """Expose an :class:`AgentServer` over HTTP using the standard library.

    Returns a started :class:`~http.server.ThreadingHTTPServer`; the caller owns
    its lifetime. This is a real, spec-shaped endpoint, but it is a plain
    stdlib server: put a reverse proxy in front of it for TLS, auth, and rate
    limiting before exposing it beyond localhost.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from .types import AGENT_CARD_PATH, LEGACY_CARD_PATH

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:  # noqa: A003 - quiet by default
            return

        def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path.rstrip("/") in (AGENT_CARD_PATH.rstrip("/"), LEGACY_CARD_PATH.rstrip("/")):
                self._send(200, json.dumps(server.card.to_dict(), indent=2).encode("utf-8"))
                return
            self._send(404, json.dumps({"error": "not found"}).encode("utf-8"))

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                self._send(400, json.dumps(
                    error_response("", ErrorCode.PARSE_ERROR, str(exc)).to_dict()
                ).encode("utf-8"))
                return

            request = RpcRequest.from_dict(payload)
            wants_stream = "text/event-stream" in (self.headers.get("Accept") or "")
            if wants_stream and request.method == Method.STREAM.value:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                for event in server.handle_stream(request):
                    frame = json.dumps({"jsonrpc": "2.0", "id": request.id, "result": event}, default=str)
                    self.wfile.write(f"data: {frame}\n\n".encode("utf-8"))
                    self.wfile.flush()
                return

            response = server.handle(request)
            self._send(200, json.dumps(response.to_dict(), default=str).encode("utf-8"))

    httpd = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True, name="gratimos-a2a")
    thread.start()
    httpd.gratimos_url = f"http://{httpd.server_address[0]}:{httpd.server_address[1]}"  # type: ignore[attr-defined]
    return httpd
