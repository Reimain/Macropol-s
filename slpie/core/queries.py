"""Queries — the read side, which never touches the write model.

Every query answers from a projection. None of them reads the ledger to compute
a result on the fly, and none of them can write. That restriction is what makes
the read side cheap enough to serve a UI that repaints on every event: a query
is a dictionary lookup over state that was folded once, not a scan.

The one deliberate exception is :class:`History`, which reads the ledger
directly — because the ledger *is* the history, and projecting it would only
produce a slower copy of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ..errors import GraphError
from .events import DomainEvent, EventKind, causation_chain


@dataclass(frozen=True, slots=True)
class Query:
    """Base for every query. Inert data, like commands."""


@dataclass(frozen=True, slots=True)
class StationStatus(Query):
    """Attached elements, their capabilities, and the gaps their refusals create."""

    element: str = ""
    attached_only: bool = False


@dataclass(frozen=True, slots=True)
class OpenFindings(Query):
    severity: str = ""
    family: str = ""
    subject: str = ""


@dataclass(frozen=True, slots=True)
class History(Query):
    """The audit trail for one thing — every event that ever touched it."""

    subject: str = ""
    since: int = 0
    limit: int = 0
    kinds: tuple[EventKind, ...] = ()


@dataclass(frozen=True, slots=True)
class Causation(Query):
    """Why did the platform do this? Walks the causation chain to its root."""

    event_id: str = ""


@dataclass(frozen=True, slots=True)
class StaleEvidence(Query):
    """Which evidence a set of changed files invalidates."""

    uris: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectionStatus(Query):
    """How far each read model has folded — the recovery diagnostic."""


@dataclass(frozen=True, slots=True)
class LedgerIntegrity(Query):
    """Verify the hash chain. Cheap to ask for, expensive to be wrong about."""


@dataclass(frozen=True, slots=True)
class QueryResult:
    """A query's answer, with the ledger version it was computed at.

    The version matters more than it looks. Two answers taken at different
    versions are not comparable, and a UI that redraws on every event needs to
    know which of two arriving responses is the newer one.
    """

    value: Any
    version: int = 0
    projection: str = ""
    stale: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value, "version": self.version,
            "projection": self.projection, "stale": self.stale,
        }


class QueryBus:
    """Routes queries to read models. Cannot write, by construction.

    It holds a ledger *reader* and a projection set — there is no append method
    reachable from here, so a query handler that tried to mutate would have
    nothing to mutate through.
    """

    def __init__(self, projections: Any, ledger: Any) -> None:
        self.projections = projections
        self._ledger = ledger
        self._handlers: dict[type, Callable[[Query], Any]] = {}
        self._register_defaults()

    def register(self, query_type: type, handler: Callable[[Query], Any]) -> None:
        self._handlers[query_type] = handler

    def ask(self, query: Query) -> QueryResult:
        handler = self._handlers.get(type(query))
        if handler is None:
            raise GraphError(f"no handler registered for {type(query).__name__}")
        value = handler(query)
        return QueryResult(value=value, version=self._version())

    def __call__(self, query: Query) -> QueryResult:
        return self.ask(query)

    def _version(self) -> int:
        version = getattr(self._ledger, "version", None)
        return int(version) if version is not None else len(self._ledger)

    # -- built-in handlers -----------------------------------------------

    def _register_defaults(self) -> None:
        self.register(StationStatus, self._station)
        self.register(OpenFindings, self._findings)
        self.register(History, self._history)
        self.register(Causation, self._causation)
        self.register(StaleEvidence, self._stale)
        self.register(ProjectionStatus, self._projection_status)
        self.register(LedgerIntegrity, self._integrity)

    def _station(self, query: StationStatus) -> Any:
        station = self.projections.get("station")
        if station is None:
            return {}
        if query.element:
            element = station.elements.get(query.element)
            return element.to_dict() if element else {}
        elements = station.attached if query.attached_only else station.elements.values()
        return {
            "elements": [element.to_dict() for element in elements],
            "refusals": [
                {"element": e, "capability": c, "reason": r}
                for e, c, r in station.refusals()
            ],
        }

    def _findings(self, query: OpenFindings) -> Any:
        findings = self.projections.get("findings")
        if findings is None:
            return []
        results = list(findings.open.values())
        if query.severity:
            results = [f for f in results if f.get("severity") == query.severity]
        if query.family:
            results = [f for f in results if f.get("family") == query.family]
        if query.subject:
            results = [f for f in results if f.get("subject") == query.subject]
        return results

    def _history(self, query: History) -> Any:
        if query.subject:
            records = self._ledger.subject_history(query.subject)
        else:
            records = self._ledger.read(since=query.since, limit=query.limit)
        if query.kinds:
            wanted = frozenset(query.kinds)
            records = tuple(r for r in records if r.kind in wanted)
        return [record.to_dict() for record in records]

    def _causation(self, query: Causation) -> Any:
        record = self._ledger.by_event_id(query.event_id)
        if record is None:
            return []
        by_id = {r.event.event_id: r.event for r in self._ledger.read()}
        return [event.to_dict() for event in causation_chain(record.event, by_id=by_id)]

    def _stale(self, query: StaleEvidence) -> Any:
        sources = self.projections.get("sources")
        return list(sources.stale_evidence(query.uris)) if sources else []

    def _projection_status(self, _query: ProjectionStatus) -> Any:
        return self.projections.status()

    def _integrity(self, _query: LedgerIntegrity) -> Any:
        from ..errors import ChainBroken

        try:
            self._ledger.verify()
        except ChainBroken as error:
            return {
                "intact": False, "sequence": error.sequence,
                "expected": error.expected, "found": error.found,
                "detail": str(error),
            }
        return {"intact": True, "version": self._version(), "digest": self._ledger.digest}
