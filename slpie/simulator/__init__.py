"""The environment simulator — real artifacts, not mocks.

A declaration becomes a genuine `package-lock.json`, a genuine git repository, a
genuine OpenAPI document on a temp filesystem, and the ordinary filesystem
connector is bound to them. The discoverers that run against a simulated world
are the same objects that run against a production checkout, so a green
simulated case is evidence about the real code path rather than a statement that
a mock agreed with a test.

Scenarios change that world — a CVE lands, a service dies, a capability is
refused — and the platform meets the new state through discovery, never by
injection.
"""

from __future__ import annotations

from .clock import DAY, EPOCH, HOUR, MINUTE, SECOND, Clock, ControlledClock, SystemClock
from .faults import Fault, FaultInjector, FaultKind, FaultyConnector
from .materialize import Artifact, container_artifacts, materialize
from .scenarios import Outcome, available, fire, scenario
from .world import SimulatedWorld

__all__ = [
    "SimulatedWorld", "materialize", "Artifact", "container_artifacts",
    "Fault", "FaultKind", "FaultInjector", "FaultyConnector",
    "Clock", "SystemClock", "ControlledClock", "EPOCH", "SECOND", "MINUTE", "HOUR", "DAY",
    "fire", "available", "scenario", "Outcome",
]
