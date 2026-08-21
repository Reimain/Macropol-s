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

#: What the browser is told to wait before reconnecting, in milliseconds. Sent
#: once per connection as an SSE `retry:` field, which `EventSource` honours
#: natively — so the reconnect delay is the server's decision rather than each
#: client's guess.
RETRY_MILLISECONDS = 3000


@dataclass(slots=True)
class Client:
    """One connected browser."""

    id: str
    events: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=CLIENT_BUFFER))
    dropped: int = 0
    connected_at: float = field(default_factory=time.time)
    #: The last stream sequence this client was sent, so a reconnect can say
    #: where to resume from rather than guessing at a fixed backlog.
    last_sent: int = 0

    def offer(self, sequence: int, payload: str) -> None:
        """Enqueue, discarding the oldest first if this client is behind."""
        item = (sequence, payload)
        try:
            self.events.put_nowait(item)
        except queue.Full:
            try:
                self.events.get_nowait()
                self.dropped += 1
                self.events.put_nowait(item)
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
        #: `(stream sequence, payload)`, newest last. The sequence is the
        #: stream's own counter rather than the ledger's: operational events
        #: never reach the ledger and would all carry sequence 0, which makes a
        #: ledger sequence useless as a resume point.
        self._history: list[tuple[int, str]] = []

    # -- bus side --------------------------------------------------------

    def handle(self, event: DomainEvent) -> None:
        """Bus entry point. Never raises — a dead client is not an outage."""
        payload = self._encode(event)
        with self._lock:
            self._sequence += 1
            self._history.append((self._sequence, payload))
            if len(self._history) > CLIENT_BUFFER:
                del self._history[: len(self._history) - CLIENT_BUFFER]
            for client in list(self._clients.values()):
                client.offer(self._sequence, payload)

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

    def connect(self, client_id: str, *, since: int = 0, backlog: int = 20) -> Client:
        """Register a client, priming it with the history it has not seen.

        Two cases, and conflating them is what made a reconnect lose events
        silently. A *new* tab wants the last few lines so the feed is not blank
        until something next happens — that is `backlog`. A *reconnecting* tab
        knows exactly where it stopped and wants everything after that, which is
        `since`, carried by the browser as `Last-Event-ID` at no cost.

        Where `since` predates what is still retained the client is told how many
        events it missed rather than being handed a partial replay it cannot
        distinguish from a complete one. A view that quietly diverges from the
        platform is worse than one that says it needs to refetch.
        """
        client = Client(id=client_id)
        with self._lock:
            if since > 0:
                oldest = self._history[0][0] if self._history else self._sequence + 1
                if oldest > since + 1:
                    # The gap is real: events between `since` and `oldest` have
                    # already been evicted from the retained window.
                    client.dropped += oldest - since - 1
                replay = [item for item in self._history if item[0] > since]
            else:
                replay = self._history[-backlog:]

            for sequence, payload in replay:
                client.offer(sequence, payload)
            client.last_sent = since
            self._clients[client_id] = client
        return client

    def disconnect(self, client_id: str) -> None:
        with self._lock:
            self._clients.pop(client_id, None)

    def follow(self, client: Client) -> Iterator[bytes]:
        """Yield SSE frames for one client until it goes away.

        Every data frame carries an `id:`, which is the whole reconnect story:
        `EventSource` remembers the last id it saw and sends it back as
        `Last-Event-ID` without the page having to track anything.
        """
        yield f"retry: {RETRY_MILLISECONDS}\n\n".encode("utf-8")
        yield b": connected\n\n"
        last_activity = time.monotonic()
        while True:
            try:
                sequence, payload = client.events.get(timeout=1.0)
            except queue.Empty:
                if time.monotonic() - last_activity > KEEPALIVE_SECONDS:
                    last_activity = time.monotonic()
                    yield b": keepalive\n\n"
                continue
            last_activity = time.monotonic()
            if client.dropped:
                # Tell the client it missed events rather than letting its view
                # quietly diverge from the platform's. The count is the client's
                # cue to refetch rather than to patch.
                yield f"event: dropped\ndata: {client.dropped}\n\n".encode("utf-8")
                client.dropped = 0
            client.last_sent = sequence
            yield f"id: {sequence}\ndata: {payload}\n\n".encode("utf-8")

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
                # The oldest sequence still replayable. A client whose
                # `Last-Event-ID` is below this cannot be resumed exactly and
                # has to refetch, so it is worth being able to ask.
                "oldest": self._history[0][0] if self._history else self._sequence,
                "retry_ms": RETRY_MILLISECONDS,
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
