"""A2A protocol types.

Models the Agent2Agent wire contract: agent cards, messages built from typed
parts, tasks with an explicit lifecycle, and artifacts. Field names follow the
spec's camelCase on the wire while staying snake_case in Python, so these
objects can be handed straight to a peer implementation — UiPath's, Microsoft's,
or anyone else's — without a translation layer in between.

Parsing is tolerant on the way in and strict on the way out: unknown fields are
preserved rather than dropped, because a peer running a newer revision of the
spec should not lose data by talking to us.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from ..errors import ProtocolError
from ..ids import iso, new_id, now_ns

#: Where an agent card is published, per the spec.
AGENT_CARD_PATH = "/.well-known/agent-card.json"
#: The earlier location; still served by deployed agents in the wild.
LEGACY_CARD_PATH = "/.well-known/agent.json"

PROTOCOL_VERSION = "0.3.0"


class TaskState(str, Enum):
    """The A2A task lifecycle."""

    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"
    REJECTED = "rejected"
    AUTH_REQUIRED = "auth-required"
    UNKNOWN = "unknown"

    @property
    def terminal(self) -> bool:
        return self in (TaskState.COMPLETED, TaskState.CANCELED, TaskState.FAILED, TaskState.REJECTED)

    @property
    def waiting_on_caller(self) -> bool:
        return self in (TaskState.INPUT_REQUIRED, TaskState.AUTH_REQUIRED)


class Role(str, Enum):
    USER = "user"
    AGENT = "agent"


# --- parts ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TextPart:
    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    kind: str = "text"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": "text", "text": self.text}
        if self.metadata:
            out["metadata"] = dict(self.metadata)
        return out


@dataclass(frozen=True, slots=True)
class DataPart:
    """Structured data — how shaped payloads cross the wire."""

    data: Mapping[str, Any] | Sequence[Any]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    kind: str = "data"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": "data", "data": self.data}
        if self.metadata:
            out["metadata"] = dict(self.metadata)
        return out


@dataclass(frozen=True, slots=True)
class FilePart:
    """A file, either inline (base64) or by URI."""

    name: str = ""
    mime_type: str = "application/octet-stream"
    bytes_b64: str = ""
    uri: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    kind: str = "file"

    def to_dict(self) -> dict[str, Any]:
        file_obj: dict[str, Any] = {"mimeType": self.mime_type}
        if self.name:
            file_obj["name"] = self.name
        if self.bytes_b64:
            file_obj["bytes"] = self.bytes_b64
        elif self.uri:
            file_obj["uri"] = self.uri
        out: dict[str, Any] = {"kind": "file", "file": file_obj}
        if self.metadata:
            out["metadata"] = dict(self.metadata)
        return out

    @classmethod
    def from_bytes(cls, payload: bytes, *, name: str = "", mime_type: str = "application/octet-stream") -> "FilePart":
        import base64

        return cls(name=name, mime_type=mime_type, bytes_b64=base64.b64encode(payload).decode("ascii"))

    def read(self) -> bytes:
        import base64

        if not self.bytes_b64:
            raise ProtocolError(
                f"file part {self.name!r} holds a URI, not inline bytes"
            )
        return base64.b64decode(self.bytes_b64)


Part = TextPart | DataPart | FilePart


def part_from_dict(data: Mapping[str, Any]) -> Part:
    kind = data.get("kind") or data.get("type") or "text"
    metadata = dict(data.get("metadata", {}))
    if kind == "data":
        return DataPart(data=data.get("data", {}), metadata=metadata)
    if kind == "file":
        file_obj = dict(data.get("file", {}))
        return FilePart(
            name=file_obj.get("name", ""),
            mime_type=file_obj.get("mimeType", file_obj.get("mime_type", "application/octet-stream")),
            bytes_b64=file_obj.get("bytes", ""),
            uri=file_obj.get("uri", ""),
            metadata=metadata,
        )
    return TextPart(text=str(data.get("text", "")), metadata=metadata)


# --- messages ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Message:
    """One turn in a conversation between agents."""

    role: Role = Role.USER
    parts: tuple[Part, ...] = ()
    message_id: str = field(default_factory=lambda: new_id("msg"))
    task_id: str = ""
    context_id: str = ""
    reference_task_ids: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    extensions: Mapping[str, Any] = field(default_factory=dict)
    kind: str = "message"

    @classmethod
    def text(cls, text: str, *, role: Role = Role.USER, **kwargs: Any) -> "Message":
        return cls(role=role, parts=(TextPart(text=text),), **kwargs)

    @classmethod
    def data(cls, payload: Mapping[str, Any] | Sequence[Any], *, role: Role = Role.USER, **kwargs: Any) -> "Message":
        return cls(role=role, parts=(DataPart(data=payload),), **kwargs)

    def text_content(self) -> str:
        """All text parts joined — the usual way to read a reply."""
        return "\n".join(p.text for p in self.parts if isinstance(p, TextPart))

    def data_content(self) -> list[Any]:
        return [p.data for p in self.parts if isinstance(p, DataPart)]

    def files(self) -> list[FilePart]:
        return [p for p in self.parts if isinstance(p, FilePart)]

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": "message",
            "role": self.role.value,
            "parts": [p.to_dict() for p in self.parts],
            "messageId": self.message_id,
        }
        if self.task_id:
            out["taskId"] = self.task_id
        if self.context_id:
            out["contextId"] = self.context_id
        if self.reference_task_ids:
            out["referenceTaskIds"] = list(self.reference_task_ids)
        if self.metadata:
            out["metadata"] = dict(self.metadata)
        if self.extensions:
            out["extensions"] = dict(self.extensions)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Message":
        return cls(
            role=Role(data.get("role", "user")),
            parts=tuple(part_from_dict(p) for p in data.get("parts", ())),
            message_id=data.get("messageId") or data.get("message_id") or new_id("msg"),
            task_id=data.get("taskId", data.get("task_id", "")),
            context_id=data.get("contextId", data.get("context_id", "")),
            reference_task_ids=tuple(data.get("referenceTaskIds", ())),
            metadata=dict(data.get("metadata", {})),
            extensions=dict(data.get("extensions", {})),
        )

    def __repr__(self) -> str:  # pragma: no cover - display only
        preview = self.text_content()[:40].replace("\n", " ")
        return f"<Message {self.role.value} parts={len(self.parts)} {preview!r}>"


# --- artifacts and tasks -------------------------------------------------


@dataclass(frozen=True, slots=True)
class Artifact:
    """A durable output of a task."""

    name: str = ""
    parts: tuple[Part, ...] = ()
    artifact_id: str = field(default_factory=lambda: new_id("art"))
    description: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"artifactId": self.artifact_id, "parts": [p.to_dict() for p in self.parts]}
        if self.name:
            out["name"] = self.name
        if self.description:
            out["description"] = self.description
        if self.metadata:
            out["metadata"] = dict(self.metadata)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Artifact":
        return cls(
            name=data.get("name", ""),
            parts=tuple(part_from_dict(p) for p in data.get("parts", ())),
            artifact_id=data.get("artifactId") or new_id("art"),
            description=data.get("description", ""),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class TaskStatus:
    """Where a task is, and when it got there."""

    state: TaskState = TaskState.SUBMITTED
    message: Message | None = None
    timestamp: str = field(default_factory=lambda: iso(now_ns()))

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"state": self.state.value, "timestamp": self.timestamp}
        if self.message is not None:
            out["message"] = self.message.to_dict()
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TaskStatus":
        message = data.get("message")
        return cls(
            state=TaskState(data.get("state", "unknown")),
            message=Message.from_dict(message) if message else None,
            timestamp=data.get("timestamp", iso(now_ns())),
        )


@dataclass(slots=True)
class Task:
    """A unit of delegated work, and everything it produced."""

    id: str = field(default_factory=lambda: new_id("task"))
    context_id: str = field(default_factory=lambda: new_id("ctx"))
    status: TaskStatus = field(default_factory=TaskStatus)
    history: list[Message] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    kind: str = "task"

    @property
    def state(self) -> TaskState:
        return self.status.state

    @property
    def done(self) -> bool:
        return self.status.state.terminal

    def advance(self, state: TaskState, message: Message | None = None) -> "Task":
        self.status = TaskStatus(state=state, message=message)
        if message is not None:
            self.history.append(message)
        return self

    def add_artifact(self, artifact: Artifact) -> Artifact:
        self.artifacts.append(artifact)
        return artifact

    def reply(self) -> Message | None:
        """The agent's last message — what a caller usually wants."""
        for message in reversed(self.history):
            if message.role is Role.AGENT:
                return message
        return self.status.message

    def to_dict(self, *, history_length: int = 0) -> dict[str, Any]:
        history = self.history[-history_length:] if history_length else self.history
        return {
            "kind": "task",
            "id": self.id,
            "contextId": self.context_id,
            "status": self.status.to_dict(),
            "history": [m.to_dict() for m in history],
            "artifacts": [a.to_dict() for a in self.artifacts],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Task":
        return cls(
            id=data.get("id") or new_id("task"),
            context_id=data.get("contextId") or new_id("ctx"),
            status=TaskStatus.from_dict(data.get("status", {})),
            history=[Message.from_dict(m) for m in data.get("history", ())],
            artifacts=[Artifact.from_dict(a) for a in data.get("artifacts", ())],
            metadata=dict(data.get("metadata", {})),
        )

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Task {self.id[:16]} {self.state.value} artifacts={len(self.artifacts)}>"


# --- agent card ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentSkill:
    """One capability an agent advertises."""

    id: str
    name: str
    description: str = ""
    tags: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    input_modes: tuple[str, ...] = ()
    output_modes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id, "name": self.name,
            "description": self.description, "tags": list(self.tags),
        }
        if self.examples:
            out["examples"] = list(self.examples)
        if self.input_modes:
            out["inputModes"] = list(self.input_modes)
        if self.output_modes:
            out["outputModes"] = list(self.output_modes)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgentSkill":
        return cls(
            id=data.get("id", ""), name=data.get("name", ""),
            description=data.get("description", ""),
            tags=tuple(data.get("tags", ())), examples=tuple(data.get("examples", ())),
            input_modes=tuple(data.get("inputModes", ())),
            output_modes=tuple(data.get("outputModes", ())),
        )


@dataclass(frozen=True, slots=True)
class AgentCapabilities:
    streaming: bool = False
    push_notifications: bool = False
    state_transition_history: bool = False
    extensions: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "streaming": self.streaming,
            "pushNotifications": self.push_notifications,
            "stateTransitionHistory": self.state_transition_history,
        }
        if self.extensions:
            out["extensions"] = [dict(e) for e in self.extensions]
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgentCapabilities":
        return cls(
            streaming=bool(data.get("streaming", False)),
            push_notifications=bool(data.get("pushNotifications", False)),
            state_transition_history=bool(data.get("stateTransitionHistory", False)),
            extensions=tuple(data.get("extensions", ())),
        )


@dataclass(frozen=True, slots=True)
class AgentCard:
    """The document a peer publishes to describe itself."""

    name: str
    description: str = ""
    url: str = ""
    version: str = "0.1.0"
    protocol_version: str = PROTOCOL_VERSION
    capabilities: AgentCapabilities = field(default_factory=AgentCapabilities)
    skills: tuple[AgentSkill, ...] = ()
    default_input_modes: tuple[str, ...] = ("text/plain", "application/json")
    default_output_modes: tuple[str, ...] = ("text/plain", "application/json")
    provider: Mapping[str, Any] = field(default_factory=dict)
    security_schemes: Mapping[str, Any] = field(default_factory=dict)
    security: tuple[Mapping[str, Any], ...] = ()
    documentation_url: str = ""
    preferred_transport: str = "JSONRPC"
    extra: Mapping[str, Any] = field(default_factory=dict)

    def skill(self, skill_id: str) -> AgentSkill | None:
        return next((s for s in self.skills if s.id == skill_id), None)

    def has_tag(self, tag: str) -> bool:
        return any(tag in s.tags for s in self.skills)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "protocolVersion": self.protocol_version,
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "version": self.version,
            "capabilities": self.capabilities.to_dict(),
            "defaultInputModes": list(self.default_input_modes),
            "defaultOutputModes": list(self.default_output_modes),
            "skills": [s.to_dict() for s in self.skills],
            "preferredTransport": self.preferred_transport,
        }
        if self.provider:
            out["provider"] = dict(self.provider)
        if self.security_schemes:
            out["securitySchemes"] = dict(self.security_schemes)
        if self.security:
            out["security"] = [dict(s) for s in self.security]
        if self.documentation_url:
            out["documentationUrl"] = self.documentation_url
        out.update({k: v for k, v in self.extra.items() if k not in out})
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgentCard":
        known = {
            "protocolVersion", "name", "description", "url", "version", "capabilities",
            "defaultInputModes", "defaultOutputModes", "skills", "provider",
            "securitySchemes", "security", "documentationUrl", "preferredTransport",
        }
        return cls(
            name=data.get("name", "unnamed-agent"),
            description=data.get("description", ""),
            url=data.get("url", ""),
            version=data.get("version", "0.1.0"),
            protocol_version=data.get("protocolVersion", PROTOCOL_VERSION),
            capabilities=AgentCapabilities.from_dict(data.get("capabilities", {})),
            skills=tuple(AgentSkill.from_dict(s) for s in data.get("skills", ())),
            default_input_modes=tuple(data.get("defaultInputModes", ("text/plain",))),
            default_output_modes=tuple(data.get("defaultOutputModes", ("text/plain",))),
            provider=dict(data.get("provider", {})),
            security_schemes=dict(data.get("securitySchemes", {})),
            security=tuple(data.get("security", ())),
            documentation_url=data.get("documentationUrl", ""),
            preferred_transport=data.get("preferredTransport", "JSONRPC"),
            # Unknown keys are preserved: a peer on a newer spec revision must
            # not lose information by round-tripping through us.
            extra={k: v for k, v in data.items() if k not in known},
        )

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<AgentCard {self.name} v{self.version} skills={[s.id for s in self.skills]}>"


# --- JSON-RPC envelopes --------------------------------------------------


class Method(str, Enum):
    """The A2A JSON-RPC method names."""

    SEND = "message/send"
    STREAM = "message/stream"
    GET = "tasks/get"
    CANCEL = "tasks/cancel"
    RESUBSCRIBE = "tasks/resubscribe"
    PUSH_SET = "tasks/pushNotificationConfig/set"
    PUSH_GET = "tasks/pushNotificationConfig/get"
    CARD = "agent/getAuthenticatedExtendedCard"


class ErrorCode(int, Enum):
    """JSON-RPC codes plus the A2A-specific range."""

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    TASK_NOT_FOUND = -32001
    TASK_NOT_CANCELABLE = -32002
    PUSH_NOT_SUPPORTED = -32003
    UNSUPPORTED_OPERATION = -32004
    CONTENT_TYPE_NOT_SUPPORTED = -32005
    INVALID_AGENT_RESPONSE = -32006


@dataclass(frozen=True, slots=True)
class RpcRequest:
    method: str
    params: Mapping[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("rpc"))
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        return {"jsonrpc": self.jsonrpc, "id": self.id, "method": self.method, "params": dict(self.params)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RpcRequest":
        return cls(
            method=data.get("method", ""),
            params=dict(data.get("params", {})),
            id=str(data.get("id", new_id("rpc"))),
            jsonrpc=data.get("jsonrpc", "2.0"),
        )


@dataclass(frozen=True, slots=True)
class RpcError:
    code: int
    message: str
    data: Any = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": int(self.code), "message": self.message}
        if self.data is not None:
            out["data"] = self.data
        return out


@dataclass(frozen=True, slots=True)
class RpcResponse:
    id: str
    result: Any = None
    error: RpcError | None = None
    jsonrpc: str = "2.0"

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            out["error"] = self.error.to_dict()
        else:
            out["result"] = self.result
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RpcResponse":
        error = data.get("error")
        return cls(
            id=str(data.get("id", "")),
            result=data.get("result"),
            error=RpcError(
                code=int(error.get("code", -32603)),
                message=error.get("message", "unknown error"),
                data=error.get("data"),
            ) if error else None,
            jsonrpc=data.get("jsonrpc", "2.0"),
        )


def parts_from(payload: Any) -> tuple[Part, ...]:
    """Build parts from loose Python data — the ergonomic entry point."""
    if isinstance(payload, str):
        return (TextPart(text=payload),)
    if isinstance(payload, (bytes, bytearray)):
        return (FilePart.from_bytes(bytes(payload)),)
    if isinstance(payload, Mapping) or isinstance(payload, Sequence):
        return (DataPart(data=payload),)
    return (TextPart(text=str(payload)),)
