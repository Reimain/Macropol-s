"""Transports for A2A.

Two implementations ship: an in-process transport for agents running in the same
runtime (the common case when Gratimos hosts several specialists itself), and an
HTTP JSON-RPC transport for peers over the network.

They are interchangeable behind :class:`Transport`, so a peer can move from
in-process to remote without touching a call site — which is exactly what
happens when a specialist agent gets extracted into its own service.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Mapping, Protocol, runtime_checkable
from urllib import request as _request
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin

from ..errors import ProtocolError, TransportError
from .types import (
    AGENT_CARD_PATH,
    LEGACY_CARD_PATH,
    AgentCard,
    ErrorCode,
    RpcError,
    RpcRequest,
    RpcResponse,
)

DEFAULT_TIMEOUT = 60.0


@runtime_checkable
class Transport(Protocol):
    """Carries one JSON-RPC call to a peer and brings the answer back."""

    name: str

    def call(self, request: RpcRequest) -> RpcResponse: ...

    def stream(self, request: RpcRequest) -> Iterator[Mapping[str, Any]]: ...

    def agent_card(self) -> AgentCard: ...


Handler = Callable[[RpcRequest], RpcResponse]


class InProcessTransport:
    """Talks to an agent in this process. No sockets, no serialization loss.

    Payloads are still round-tripped through JSON, deliberately: an agent that
    only works because it received a live Python object would break the moment
    it moved behind HTTP, and this is where that shows up.
    """

    name = "in-process"

    def __init__(self, handler: Handler, card: AgentCard,
                 streamer: Callable[[RpcRequest], Iterator[Mapping[str, Any]]] | None = None) -> None:
        self._handler = handler
        self._card = card
        self._streamer = streamer

    def call(self, request: RpcRequest) -> RpcResponse:
        wire = json.loads(json.dumps(request.to_dict(), default=str))
        response = self._handler(RpcRequest.from_dict(wire))
        return RpcResponse.from_dict(json.loads(json.dumps(response.to_dict(), default=str)))

    def stream(self, request: RpcRequest) -> Iterator[Mapping[str, Any]]:
        if self._streamer is None:
            response = self.call(request)
            if response.error is not None:
                raise ProtocolError(f"{response.error.code}: {response.error.message}")
            yield response.result if isinstance(response.result, Mapping) else {"result": response.result}
            return
        yield from self._streamer(request)

    def agent_card(self) -> AgentCard:
        return self._card

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<InProcessTransport {self._card.name}>"


@dataclass(frozen=True, slots=True)
class HttpAuth:
    """Credentials for a peer. Never logged, never put in a trace event."""

    scheme: str = ""          # "Bearer", "Basic", or "" for none
    token: str = ""
    api_key_header: str = ""  # e.g. "X-API-Key" for header-style keys
    api_key: str = ""
    extra_headers: Mapping[str, str] = field(default_factory=dict)

    def headers(self) -> dict[str, str]:
        out: dict[str, str] = dict(self.extra_headers)
        if self.scheme and self.token:
            out["Authorization"] = f"{self.scheme} {self.token}"
        if self.api_key_header and self.api_key:
            out[self.api_key_header] = self.api_key
        return out

    def __repr__(self) -> str:  # pragma: no cover - must not leak secrets
        marks = []
        if self.token:
            marks.append(f"{self.scheme or 'token'}=<redacted>")
        if self.api_key:
            marks.append(f"{self.api_key_header}=<redacted>")
        return f"<HttpAuth {' '.join(marks) or 'anonymous'}>"


class HttpTransport:
    """JSON-RPC over HTTP, with SSE for streaming."""

    name = "http"

    def __init__(
        self,
        url: str,
        *,
        auth: HttpAuth | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        card: AgentCard | None = None,
        max_bytes: int = 64 << 20,
    ) -> None:
        self.url = url.rstrip("/")
        self.auth = auth if auth is not None else HttpAuth()
        self.timeout = timeout
        self.max_bytes = max_bytes
        self._card = card
        self._lock = threading.Lock()

    # -- calls -----------------------------------------------------------

    def call(self, request: RpcRequest) -> RpcResponse:
        body = json.dumps(request.to_dict(), default=str).encode("utf-8")
        raw = self._post(body, accept="application/json")
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"peer returned non-JSON: {exc}") from exc
        if not isinstance(data, Mapping):
            raise ProtocolError(f"peer returned {type(data).__name__}, expected a JSON-RPC object")
        return RpcResponse.from_dict(data)

    def stream(self, request: RpcRequest) -> Iterator[Mapping[str, Any]]:
        """Consume a server-sent event stream of task updates."""
        body = json.dumps(request.to_dict(), default=str).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            **self.auth.headers(),
        }
        req = _request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with _request.urlopen(req, timeout=self.timeout) as response:
                for event in _parse_sse(response):
                    yield event
        except HTTPError as exc:
            raise TransportError(f"stream failed with HTTP {exc.code}: {exc.reason}") from exc
        except URLError as exc:
            raise TransportError(f"stream failed: {exc.reason}") from exc

    def agent_card(self, *, refresh: bool = False) -> AgentCard:
        """Fetch and cache the peer's card, trying both published locations."""
        with self._lock:
            if self._card is not None and not refresh:
                return self._card
            errors: list[str] = []
            for path in (AGENT_CARD_PATH, LEGACY_CARD_PATH):
                try:
                    raw = self._get(urljoin(self.url + "/", path.lstrip("/")))
                    self._card = AgentCard.from_dict(json.loads(raw.decode("utf-8")))
                    return self._card
                except (TransportError, json.JSONDecodeError) as exc:
                    errors.append(f"{path}: {exc}")
            raise TransportError(
                f"no agent card at {self.url}; tried " + "; ".join(errors)
            )

    # -- http ------------------------------------------------------------

    def _post(self, body: bytes, *, accept: str) -> bytes:
        headers = {"Content-Type": "application/json", "Accept": accept, **self.auth.headers()}
        req = _request.Request(self.url, data=body, headers=headers, method="POST")
        return self._open(req)

    def _get(self, url: str) -> bytes:
        req = _request.Request(url, headers={"Accept": "application/json", **self.auth.headers()})
        return self._open(req)

    def _open(self, req: _request.Request) -> bytes:
        try:
            with _request.urlopen(req, timeout=self.timeout) as response:
                return response.read(self.max_bytes)
        except HTTPError as exc:
            detail = exc.read()[:2000].decode("utf-8", "replace") if hasattr(exc, "read") else ""
            raise TransportError(f"HTTP {exc.code} from {req.full_url}: {exc.reason} {detail}".strip()) from exc
        except URLError as exc:
            raise TransportError(f"cannot reach {req.full_url}: {exc.reason}") from exc

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<HttpTransport {self.url}>"


def _parse_sse(stream: Any) -> Iterator[Mapping[str, Any]]:
    """Yield JSON payloads from a text/event-stream response."""
    data_lines: list[str] = []
    for raw in stream:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if line.startswith(":"):
            continue  # comment / keep-alive
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
            continue
        if line == "" and data_lines:
            payload = "\n".join(data_lines)
            data_lines = []
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(event, Mapping):
                yield event.get("result", event) if "result" in event else event
    if data_lines:
        try:
            event = json.loads("\n".join(data_lines))
            if isinstance(event, Mapping):
                yield event.get("result", event) if "result" in event else event
        except json.JSONDecodeError:
            return


def error_response(request_id: str, code: ErrorCode, message: str, data: Any = None) -> RpcResponse:
    """Build a JSON-RPC error response."""
    return RpcResponse(id=request_id, error=RpcError(code=int(code), message=message, data=data))
