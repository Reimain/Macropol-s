"""The peer registry: who else is out there, and what can they do.

An agent that delegates needs to answer "who should handle this?" without the
call site hard-coding a name. The registry answers it by capability — skills and
tags declared on each peer's agent card — so adding a specialist is a
registration, not a code change.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Sequence

from ..contextflow import ContextFlow
from ..errors import AgentError
from ..ids import slug
from .client import A2AClient, Exchange
from .transport import HttpAuth, HttpTransport, Transport
from .types import AgentCard


@dataclass(slots=True)
class Peer:
    """A registered agent and the client that reaches it."""

    name: str
    client: A2AClient
    tags: tuple[str, ...] = ()
    priority: int = 100
    calls: int = 0
    failures: int = 0
    last_error: str = ""

    @property
    def card(self) -> AgentCard:
        return self.client.card

    def skills(self) -> list[str]:
        return [s.id for s in self.card.skills]

    def can(self, skill: str) -> bool:
        return self.client.supports(skill)

    def matches(self, tag: str) -> bool:
        return tag in self.tags or self.card.has_tag(tag)

    @property
    def healthy(self) -> bool:
        """False once a peer has failed more than half its recent calls."""
        return self.calls == 0 or (self.failures / self.calls) <= 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "transport": self.client.transport.name,
            "skills": self.skills(),
            "tags": list(self.tags),
            "priority": self.priority,
            "calls": self.calls,
            "failures": self.failures,
            "healthy": self.healthy,
            "last_error": self.last_error[:200],
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Peer {self.name} skills={self.skills()} healthy={self.healthy}>"


class AgentRegistry:
    """Holds peers and routes work to them by capability."""

    def __init__(self, flow: ContextFlow | None = None, *, actor: str = "gratimos") -> None:
        self.flow = flow
        self.actor = actor
        self._peers: dict[str, Peer] = {}
        self._lock = threading.RLock()

    # -- registration ----------------------------------------------------

    def add(self, client: A2AClient, *, name: str = "", tags: Sequence[str] = (), priority: int = 100) -> Peer:
        """Register a peer, discovering its card if that has not happened yet."""
        try:
            resolved = name or client.name
        except Exception as exc:  # noqa: BLE001 - discovery failure is a registration failure
            raise AgentError(f"cannot register peer: agent card unavailable ({exc})") from exc
        peer = Peer(name=slug(resolved), client=client, tags=tuple(tags), priority=priority)
        with self._lock:
            self._peers[peer.name] = peer
        return peer

    def connect(
        self,
        url: str,
        *,
        name: str = "",
        auth: HttpAuth | None = None,
        tags: Sequence[str] = (),
        priority: int = 100,
        timeout: float = 60.0,
    ) -> Peer:
        """Register a remote peer by URL."""
        client = A2AClient(HttpTransport(url, auth=auth, timeout=timeout), flow=self.flow, actor=self.actor)
        return self.add(client, name=name, tags=tags, priority=priority)

    def host(self, server: Any, *, tags: Sequence[str] = (), priority: int = 50) -> Peer:
        """Register an in-process :class:`~gratimos.a2a.server.AgentServer`."""
        client = A2AClient(server.transport(), flow=self.flow, actor=self.actor)
        return self.add(client, tags=tags, priority=priority)

    def remove(self, name: str) -> bool:
        with self._lock:
            return self._peers.pop(slug(name), None) is not None

    # -- lookup ----------------------------------------------------------

    def get(self, name: str) -> Peer | None:
        return self._peers.get(slug(name))

    def require(self, name: str) -> Peer:
        peer = self.get(name)
        if peer is None:
            raise AgentError(f"no peer named {name!r}; registered: {self.names()}")
        return peer

    def names(self) -> list[str]:
        return sorted(self._peers)

    def peers(self) -> list[Peer]:
        return sorted(self._peers.values(), key=lambda p: (p.priority, p.name))

    def with_skill(self, skill: str) -> list[Peer]:
        return [p for p in self.peers() if p.can(skill)]

    def with_tag(self, tag: str) -> list[Peer]:
        return [p for p in self.peers() if p.matches(tag)]

    def best_for(self, skill: str = "", *, tag: str = "") -> Peer | None:
        """Highest-priority healthy peer that can do the work."""
        candidates = self.peers()
        if skill:
            candidates = [p for p in candidates if p.can(skill)]
        if tag:
            candidates = [p for p in candidates if p.matches(tag)]
        healthy = [p for p in candidates if p.healthy]
        return (healthy or candidates)[0] if (healthy or candidates) else None

    # -- delegation ------------------------------------------------------

    def ask(self, payload: Any, *, skill: str = "", tag: str = "", peer: str = "") -> Exchange:
        """Send work to the best-matching peer."""
        target = self.require(peer) if peer else self.best_for(skill, tag=tag)
        if target is None:
            raise AgentError(
                f"no peer can handle skill={skill!r} tag={tag!r}; registered: {self.names()}"
            )
        return self._invoke(target, payload, skill=skill)

    def broadcast(self, payload: Any, *, skill: str = "", tag: str = "") -> list[Exchange]:
        """Ask every matching peer — for consensus or comparison."""
        targets = [p for p in self.peers() if (not skill or p.can(skill)) and (not tag or p.matches(tag))]
        out: list[Exchange] = []
        for target in targets:
            try:
                out.append(self._invoke(target, payload, skill=skill))
            except Exception as exc:  # noqa: BLE001 - one bad peer must not stop the rest
                target.last_error = f"{type(exc).__name__}: {exc}"
        return out

    def _invoke(self, peer: Peer, payload: Any, *, skill: str) -> Exchange:
        peer.calls += 1
        try:
            exchange = peer.client.send(payload, skill=skill if peer.can(skill) else "")
        except Exception as exc:  # noqa: BLE001 - record, then re-raise to the caller
            peer.failures += 1
            peer.last_error = f"{type(exc).__name__}: {exc}"
            raise
        if not exchange.ok:
            peer.failures += 1
            peer.last_error = f"task {exchange.state.value}"
        return exchange

    # -- reporting -------------------------------------------------------

    def catalog(self) -> dict[str, Any]:
        """Every capability reachable from here, by skill."""
        by_skill: dict[str, list[str]] = {}
        for peer in self.peers():
            for skill in peer.skills():
                by_skill.setdefault(skill, []).append(peer.name)
        return {
            "peers": [p.to_dict() for p in self.peers()],
            "by_skill": {k: sorted(v) for k, v in sorted(by_skill.items())},
            "unhealthy": [p.name for p in self.peers() if not p.healthy],
        }

    def __contains__(self, name: object) -> bool:
        return slug(str(name)) in self._peers

    def __iter__(self) -> Iterator[Peer]:
        return iter(self.peers())

    def __len__(self) -> int:
        return len(self._peers)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<AgentRegistry peers={self.names()}>"
