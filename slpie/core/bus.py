"""The event bus — ordered delivery, at-least-once, deduplicated on event id.

Subscribers are projectors, the UI's SSE stream, and the incremental engine.
They must all see the same events in the same order, and a subscriber that
raises must not stop the others: a broken report view is a bug, a broken report
view that also stops the graph from updating is an outage.

So delivery is ordered, failures are isolated and counted, and every subscriber
tracks the last sequence it saw. That last part is what makes catching up after
a restart a matter of replaying from a number rather than of guessing.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Iterable, Protocol, Sequence

from ..errors import BusError
from .events import DomainEvent, EventKind

Handler = Callable[[DomainEvent], None]


class Subscriber(Protocol):
    """Anything that reacts to events. Projectors are the main implementers."""

    name: str

    def handle(self, event: DomainEvent) -> None: ...


@dataclass(slots=True)
class Subscription:
    """One registered listener and its delivery state.

    ``kinds`` empty means "everything" — the UI stream wants the whole feed,
    while the graph projector only wants the four kinds that mutate it.
    """

    name: str
    handler: Handler
    kinds: frozenset[EventKind] = frozenset()
    last_sequence: int = 0
    delivered: int = 0
    failures: int = 0
    last_error: str = ""

    def wants(self, event: DomainEvent) -> bool:
        return not self.kinds or event.kind in self.kinds


@dataclass(slots=True)
class DeliveryReport:
    """What one publish actually did — the bus never fails silently."""

    published: int = 0
    delivered: int = 0
    duplicates: int = 0
    failures: tuple[tuple[str, str], ...] = ()   # (subscriber name, error)

    @property
    def clean(self) -> bool:
        return not self.failures


class EventBus:
    """Ordered publish/subscribe with dedupe.

    Deliberately synchronous. Projections must be consistent with the ledger the
    instant an append returns, or a query issued right after a command would read
    a stale read model — the classic CQRS trap, and an avoidable one at this
    scale.
    """

    def __init__(self, *, dedupe_window: int = 8192) -> None:
        self._subscriptions: list[Subscription] = []
        self._seen: dict[str, None] = {}          # insertion-ordered, used as an LRU
        self._dedupe_window = dedupe_window
        self._lock = threading.RLock()
        self._published = 0

    # -- registration ----------------------------------------------------

    def subscribe(
        self,
        name: str,
        handler: Handler,
        *,
        kinds: Iterable[EventKind] = (),
    ) -> Subscription:
        with self._lock:
            if any(s.name == name for s in self._subscriptions):
                raise BusError(f"subscriber {name!r} is already registered")
            subscription = Subscription(name=name, handler=handler, kinds=frozenset(kinds))
            self._subscriptions.append(subscription)
            return subscription

    def subscribe_object(
        self, subscriber: Subscriber, *, kinds: Iterable[EventKind] = ()
    ) -> Subscription:
        return self.subscribe(subscriber.name, subscriber.handle, kinds=kinds)

    def unsubscribe(self, name: str) -> bool:
        with self._lock:
            before = len(self._subscriptions)
            self._subscriptions = [s for s in self._subscriptions if s.name != name]
            return len(self._subscriptions) != before

    @property
    def subscribers(self) -> tuple[str, ...]:
        return tuple(s.name for s in self._subscriptions)

    def subscription(self, name: str) -> Subscription | None:
        return next((s for s in self._subscriptions if s.name == name), None)

    # -- delivery --------------------------------------------------------

    def publish(self, *events: DomainEvent) -> DeliveryReport:
        report = DeliveryReport()
        failures: list[tuple[str, str]] = []

        with self._lock:
            for event in events:
                if event.event_id in self._seen:
                    report.duplicates += 1
                    continue
                self._remember(event.event_id)
                report.published += 1
                self._published += 1

                for subscription in list(self._subscriptions):
                    if not subscription.wants(event):
                        continue
                    try:
                        subscription.handler(event)
                    except Exception as error:  # one broken view must not stop the rest
                        subscription.failures += 1
                        subscription.last_error = f"{type(error).__name__}: {error}"
                        failures.append((subscription.name, subscription.last_error))
                    else:
                        subscription.delivered += 1
                        report.delivered += 1
                    finally:
                        subscription.last_sequence = max(
                            subscription.last_sequence, event.sequence
                        )

        report.failures = tuple(failures)
        return report

    def replay(self, events: Sequence[DomainEvent], *, into: str | None = None) -> int:
        """Redeliver history, ignoring the dedupe window.

        This is how a projection rebuilds from sequence 0. ``into`` restricts the
        replay to one subscriber, so adding a read model does not force every
        other projector to reprocess the whole ledger.
        """
        delivered = 0
        with self._lock:
            targets = [
                s for s in self._subscriptions if into is None or s.name == into
            ]
            for event in events:
                for subscription in targets:
                    if not subscription.wants(event):
                        continue
                    subscription.handler(event)
                    subscription.delivered += 1
                    subscription.last_sequence = max(
                        subscription.last_sequence, event.sequence
                    )
                    delivered += 1
        return delivered

    def _remember(self, event_id: str) -> None:
        self._seen[event_id] = None
        while len(self._seen) > self._dedupe_window:
            self._seen.pop(next(iter(self._seen)))

    # -- introspection ---------------------------------------------------

    @property
    def published(self) -> int:
        return self._published

    def health(self) -> dict[str, dict[str, object]]:
        """Per-subscriber delivery state — rendered directly in the UI."""
        return {
            s.name: {
                "delivered": s.delivered,
                "failures": s.failures,
                "last_sequence": s.last_sequence,
                "last_error": s.last_error,
                "kinds": sorted(k.value for k in s.kinds) or ["*"],
            }
            for s in self._subscriptions
        }


@dataclass(slots=True)
class Recorder:
    """A subscriber that just keeps events. The test fixture, and the SSE backlog."""

    name: str = "recorder"
    limit: int = 1000
    events: list[DomainEvent] = field(default_factory=list)

    def handle(self, event: DomainEvent) -> None:
        self.events.append(event)
        if len(self.events) > self.limit:
            del self.events[: len(self.events) - self.limit]

    def kinds(self, kind: EventKind) -> list[DomainEvent]:
        return [event for event in self.events if event.kind is kind]

    def clear(self) -> None:
        self.events.clear()

    def __len__(self) -> int:
        return len(self.events)
