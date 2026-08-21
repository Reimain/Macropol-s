"""The CQRS core — commands in, events to the ledger, projections out.

```
WRITE   Command → CommandBus → DomainEvent[] → Ledger(append) → EventBus
READ    Query   → QueryBus   → Projection                        ↑
                                          projectors + UI stream subscribe
```

Nothing crosses the middle. A handler never writes a read model, a query never
reaches the ledger except to read history, and the only path from one side to
the other is an appended event.
"""

from __future__ import annotations

from .bus import DeliveryReport, EventBus, Recorder, Subscriber, Subscription
from .commands import (
    AskQuestion,
    AttachElement,
    ChangeTarget,
    Command,
    DeclareEnvironment,
    DeriveEnrichment,
    DetachElement,
    FireScenario,
    GenerateArtifact,
    HandOverElement,
    NegotiateCapability,
    QuarantinePlugin,
    RaiseFinding,
    RecordHeartbeat,
    RecordObservation,
    RecordSourceChange,
    RegisterPlugin,
    ResolveFinding,
    RetireEdge,
    RetireNode,
    SealSnapshot,
    SuppressFinding,
)
from .events import UNSEQUENCED, DomainEvent, EventKind, causation_chain, emit
from .handlers import CommandBus
from .queries import (
    Causation,
    History,
    LedgerIntegrity,
    OpenFindings,
    ProjectionStatus,
    Query,
    QueryBus,
    QueryResult,
    StaleEvidence,
    StationStatus,
)

__all__ = [
    "DomainEvent", "EventKind", "emit", "causation_chain", "UNSEQUENCED",
    "EventBus", "Subscription", "Subscriber", "DeliveryReport", "Recorder",
    "Command", "CommandBus",
    "DeclareEnvironment", "ChangeTarget", "AttachElement", "DetachElement",
    "HandOverElement", "NegotiateCapability", "RecordHeartbeat", "RecordObservation",
    "RecordSourceChange", "RetireNode", "RetireEdge", "DeriveEnrichment",
    "RaiseFinding", "ResolveFinding", "SuppressFinding", "SealSnapshot",
    "GenerateArtifact", "FireScenario", "AskQuestion", "RegisterPlugin",
    "QuarantinePlugin",
    "Query", "QueryBus", "QueryResult", "StationStatus", "OpenFindings", "History",
    "Causation", "StaleEvidence", "ProjectionStatus", "LedgerIntegrity",
]
