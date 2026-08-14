"""Routing channels: pluggable lanes that payloads travel down.

A channel is a predicate plus a set of sinks. Channels are added and removed at
runtime — that is the whole point, since the kernel does not know at startup
what entities it will meet. Matching is by predicate rather than by name so a
channel can claim "anything with a customer_id" without anyone having to
enumerate which sources produce one.
"""

from __future__ import annotations

import fnmatch
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..errors import HubError
from ..ids import new_id
from ..meta import DataShape, TypeTag, Wrapped

Predicate = Callable[[Wrapped], bool]
Sink = Callable[[Wrapped], None]


class Delivery(str, Enum):
    """What a channel does with a payload it accepts."""

    BROADCAST = "broadcast"   # every matching channel receives it
    EXCLUSIVE = "exclusive"   # claims the payload; lower-priority lanes skipped
    TEE = "tee"               # always gets a copy, never consumes
    FALLBACK = "fallback"     # only sees payloads no other channel claimed


# --- predicate builders --------------------------------------------------


def by_name(pattern: str) -> Predicate:
    """Match on entity name, glob-style."""
    def predicate(payload: Wrapped) -> bool:
        return fnmatch.fnmatch(payload.name, pattern)
    return predicate


def by_source(pattern: str) -> Predicate:
    def predicate(payload: Wrapped) -> bool:
        return fnmatch.fnmatch(payload.provenance.source or "", pattern)
    return predicate


def by_probe(*names: str) -> Predicate:
    wanted = set(names)
    def predicate(payload: Wrapped) -> bool:
        return payload.provenance.probe in wanted
    return predicate


def has_fields(*names: str) -> Predicate:
    """Match payloads whose shape carries all of these fields."""
    wanted = set(names)
    def predicate(payload: Wrapped) -> bool:
        return wanted <= set(payload.shape.names())
    return predicate


def has_type(tag: TypeTag) -> Predicate:
    def predicate(payload: Wrapped) -> bool:
        return any(f.tag is tag for f in payload.shape.fields)
    return predicate


def larger_than(rows: int) -> Predicate:
    def predicate(payload: Wrapped) -> bool:
        return payload.rows > rows
    return predicate


def labelled(**labels: str) -> Predicate:
    def predicate(payload: Wrapped) -> bool:
        actual = payload.provenance.labels
        return all(actual.get(k) == v for k, v in labels.items())
    return predicate


def all_of(*predicates: Predicate) -> Predicate:
    def predicate(payload: Wrapped) -> bool:
        return all(p(payload) for p in predicates)
    return predicate


def any_of(*predicates: Predicate) -> Predicate:
    def predicate(payload: Wrapped) -> bool:
        return any(p(payload) for p in predicates)
    return predicate


ALWAYS: Predicate = lambda payload: True  # noqa: E731 - a named constant reads better here


# --- channels ------------------------------------------------------------


@dataclass(slots=True)
class Channel:
    """One routing lane."""

    name: str
    predicate: Predicate = ALWAYS
    delivery: Delivery = Delivery.BROADCAST
    priority: int = 100
    sinks: list[Sink] = field(default_factory=list)
    labels: Mapping[str, str] = field(default_factory=dict)
    #: Path prefix under which payloads on this channel are keyed.
    keyspace: str = "data"
    id: str = field(default_factory=lambda: new_id("chn"))
    delivered: int = 0
    errors: list[str] = field(default_factory=list)

    def accepts(self, payload: Wrapped) -> bool:
        try:
            return bool(self.predicate(payload))
        except Exception as exc:  # noqa: BLE001 - a bad predicate must not stop routing
            self.errors.append(f"predicate raised: {type(exc).__name__}: {exc}")
            return False

    def deliver(self, payload: Wrapped) -> int:
        """Push to every sink, returning how many succeeded."""
        delivered = 0
        for sink in list(self.sinks):
            try:
                sink(payload)
                delivered += 1
            except Exception as exc:  # noqa: BLE001 - isolate sink failures
                self.errors.append(f"sink {getattr(sink, '__name__', sink)!r} raised: {exc}")
        self.delivered += 1
        return delivered

    def subscribe(self, sink: Sink) -> Callable[[], None]:
        self.sinks.append(sink)
        return lambda: self.sinks.remove(sink)

    def path_for(self, payload: Wrapped) -> str:
        return f"{self.keyspace}.{payload.name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "delivery": self.delivery.value,
            "priority": self.priority,
            "keyspace": self.keyspace,
            "sinks": len(self.sinks),
            "delivered": self.delivered,
            "labels": dict(self.labels),
            "errors": self.errors[-5:],
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Channel {self.name} delivery={self.delivery.value} delivered={self.delivered}>"


@dataclass(frozen=True, slots=True)
class RouteResult:
    """Where one payload actually went."""

    payload_id: str
    entity: str
    channels: tuple[str, ...]
    paths: tuple[str, ...]
    sinks_notified: int
    unrouted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "payload_id": self.payload_id,
            "entity": self.entity,
            "channels": list(self.channels),
            "paths": list(self.paths),
            "sinks_notified": self.sinks_notified,
            "unrouted": self.unrouted,
        }


class Router:
    """Holds channels and decides which ones see a payload."""

    def __init__(self, channels: Sequence[Channel] = ()) -> None:
        self._channels: list[Channel] = list(channels)
        self._lock = threading.RLock()
        self.unrouted: list[str] = []

    def add(self, channel: Channel) -> Channel:
        with self._lock:
            if any(c.name == channel.name for c in self._channels):
                raise HubError(f"channel {channel.name!r} is already registered")
            self._channels.append(channel)
            self._channels.sort(key=lambda c: c.priority)
        return channel

    def plug(
        self,
        name: str,
        predicate: Predicate = ALWAYS,
        *,
        delivery: Delivery = Delivery.BROADCAST,
        priority: int = 100,
        keyspace: str = "data",
        sinks: Iterable[Sink] = (),
        **labels: str,
    ) -> Channel:
        """Register a channel in one call — the runtime-pluggable path."""
        return self.add(Channel(
            name=name, predicate=predicate, delivery=delivery, priority=priority,
            keyspace=keyspace, sinks=list(sinks), labels=labels,
        ))

    def unplug(self, name: str) -> bool:
        with self._lock:
            before = len(self._channels)
            self._channels = [c for c in self._channels if c.name != name]
            return len(self._channels) != before

    def get(self, name: str) -> Channel | None:
        return next((c for c in self._channels if c.name == name), None)

    def channels(self) -> list[Channel]:
        return list(self._channels)

    def select(self, payload: Wrapped) -> list[Channel]:
        """Channels that claim this payload.

        Ordinary lanes are tried in priority order; an exclusive one that
        accepts ends the cascade. Fallback lanes are consulted only when
        nothing else claimed the payload — that is what makes a residual
        channel a genuine "nobody wanted this" signal rather than a lane every
        payload happens to also travel. Tees always get a copy.
        """
        matched: list[Channel] = []
        for channel in self._channels:  # already priority-ordered
            if channel.delivery in (Delivery.FALLBACK, Delivery.TEE):
                continue
            if not channel.accepts(payload):
                continue
            matched.append(channel)
            if channel.delivery is Delivery.EXCLUSIVE:
                break
        if not matched:
            matched = [c for c in self._channels
                       if c.delivery is Delivery.FALLBACK and c.accepts(payload)]
        return matched + [c for c in self._channels
                          if c.delivery is Delivery.TEE and c.accepts(payload)]

    def route(self, payload: Wrapped) -> RouteResult:
        channels = self.select(payload)
        if not channels:
            self.unrouted.append(payload.name)
            return RouteResult(payload.id, payload.name, (), (), 0, unrouted=True)
        notified = sum(channel.deliver(payload) for channel in channels)
        return RouteResult(
            payload_id=payload.id,
            entity=payload.name,
            channels=tuple(c.name for c in channels),
            paths=tuple(c.path_for(payload) for c in channels),
            sinks_notified=notified,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "channels": [c.to_dict() for c in self._channels],
            "unrouted": self.unrouted[-20:],
            "unrouted_count": len(self.unrouted),
        }

    def __len__(self) -> int:
        return len(self._channels)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Router channels={[c.name for c in self._channels]}>"


def default_channels(router: Router) -> Router:
    """A sensible starting topology.

    Ordered by priority so the specific lanes get first refusal and ``residual``
    catches whatever nothing else wanted — which is exactly the set a
    policymaker should look at when deciding what the kernel still cannot model.
    """
    router.plug("media", by_probe("image", "video"), delivery=Delivery.EXCLUSIVE,
                priority=10, keyspace="media")
    router.plug("scripts", by_probe("shell"), delivery=Delivery.EXCLUSIVE,
                priority=20, keyspace="ops")
    router.plug("records", larger_than(0), priority=50, keyspace="data")
    router.plug("residual", ALWAYS, delivery=Delivery.FALLBACK, priority=900, keyspace="residual")
    return router
