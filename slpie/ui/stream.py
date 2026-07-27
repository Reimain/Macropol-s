"""Server-sent events — the live feed the CQRS bus makes almost free.

Because every change is already an ordered, published event, a live interface is
a subscriber rather than a polling loop. That is the architectural dividend made
visible: fire a scenario and the Findings view updates because the same event
that changed the graph reached the browser.

Each client gets its own bounded queue. A browser tab that stops reading must
not be able to slow the platform down, so a full queue drops its oldest events
and the client is told it fell behind — a visible gap in the feed, never silent
back-pressure on the write path.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from ..core.events import DomainEvent

#: Per-client buffer. Large enough for a burst from a full scan, small enough
#: that a hundred abandoned tabs cannot exhaust memory.
CLIENT_BUFFER = 512

#: Sent when nothing has happened, so proxies do not close an idle connection.
KEEPALIVE_SECONDS = 20.0


@dataclass(slots=True)
class Client:
    """One connected browser."""

    id: str
    events: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=CLIENT_BUFFER))
    dropped: int = 0
    connected_at: float = field(default_factory=time.time)

    def offer(self, payload: str) -> None:
        """Enqueue, discarding the oldest first if this client is behind."""
        try:
            self.events.put_nowait(payload)
        except queue.Full:
            try:
                self.events.get_nowait()
                self.dropped += 1
                self.events.put_nowait(payload)
            except (queue.Empty, queue.Full):  # pragma: no cover - racing readers
                self.dropped += 1


class EventStream:
    """Fans domain events out to connected clients.

    Subscribes to the bus like any other projector, which is why the UI needs no
    special support anywhere else in the platform.
    """

    def __init__(self, *, name: str = "ui-stream") -> None:
        self.name = name
        self._clients: dict[str, Client] = {}
        self._lock = threading.RLock()
        self._sequence = 0
        self._history: list[str] = []

    # -- bus side --------------------------------------------------------

    def handle(self, event: DomainEvent) -> None:
        """Bus entry point. Never raises — a dead client is not an outage."""
        payload = self._encode(event)
        with self._lock:
            self._sequence += 1
            self._history.append(payload)
            if len(self._history) > CLIENT_BUFFER:
                del self._history[: len(self._history) - CLIENT_BUFFER]
            for client in list(self._clients.values()):
                client.offer(payload)

    def _encode(self, event: DomainEvent) -> str:
        body = {
            "id": event.event_id,
            "sequence": event.sequence,
            "kind": event.kind.value,
            "subject": event.subject,
            "actor": event.actor,
            "occurred_at": event.occurred_at,
            "operational": event.kind.is_operational,
            "mutates_graph": event.kind.mutates_graph,
            "payload": _summarise(event.payload),
        }
        return json.dumps(body, default=str)

    # -- client side -----------------------------------------------------

    def connect(self, client_id: str, *, backlog: int = 20) -> Client:
        """Register a client, priming it with recent history.

        The backlog matters: a tab opened mid-scan should show what just
        happened rather than an empty feed until the next event arrives.
        """
        client = Client(id=client_id)
        with self._lock:
            for payload in self._history[-backlog:]:
                client.offer(payload)
            self._clients[client_id] = client
        return client

    def disconnect(self, client_id: str) -> None:
        with self._lock:
            self._clients.pop(client_id, None)

    def follow(self, client: Client) -> Iterator[bytes]:
        """Yield SSE frames for one client until it goes away."""
        yield b": connected\n\n"
        last_activity = time.monotonic()
        while True:
            try:
                payload = client.events.get(timeout=1.0)
            except queue.Empty:
                if time.monotonic() - last_activity > KEEPALIVE_SECONDS:
                    last_activity = time.monotonic()
                    yield b": keepalive\n\n"
                continue
            last_activity = time.monotonic()
            if client.dropped:
                # Tell the client it missed events rather than letting its view
                # quietly diverge from the platform's.
                yield f"event: dropped\ndata: {client.dropped}\n\n".encode("utf-8")
                client.dropped = 0
            yield f"data: {payload}\n\n".encode("utf-8")

    @property
    def clients(self) -> int:
        return len(self._clients)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "clients": len(self._clients),
                "delivered": self._sequence,
                "buffered": len(self._history),
                "dropped": sum(client.dropped for client in self._clients.values()),
            }


def _summarise(payload: Any) -> dict[str, Any]:
    """Trim an event payload to what a feed line needs.

    A node assertion carries its whole property bag; sending that to every
    connected browser on every event would make the feed the most expensive
    thing in the platform.
    """
    if not isinstance(payload, dict):
        return {}
    keep = (
        "kind", "capability", "reason", "severity", "title", "target", "scenario",
        "discoverer", "nodes", "edges", "finding_id", "attribute", "value",
        "label", "artifact", "path", "uri", "question",
    )
    summary: dict[str, Any] = {}
    for key in keep:
        if key in payload:
            value = payload[key]
            summary[key] = value[:120] if isinstance(value, str) else value
    return summary
