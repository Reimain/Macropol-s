"""Gratimos — a self-building agent kernel.

Point it at an environment. It reads what is there, infers the shape of it,
generates code against those shapes, and records every step on a versioned
spine that can be rolled back.

The short version::

    from gratimos import Depth, govern

    report = govern("./data", depth=Depth.GENERATE)
    print(report.summary())

The pieces, if you want them individually:

* :mod:`gratimos.contextflow` — the versioned event spine everything writes to
* :mod:`gratimos.trace` — durable journal, checkpoints, rollback, timetravel guard
* :mod:`gratimos.meta` — shapes, inference, casting, self-describing wrappers
* :mod:`gratimos.probes` — reading JSON, CSV, XLSX, SQLite, APIs, media, scripts
* :mod:`gratimos.hubs` — routing channels, memory budget, spill staging
* :mod:`gratimos.storage` — secured local repository and cloud connectors
* :mod:`gratimos.codegen` — emitters, protobuf, AST-level merge, versioning
* :mod:`gratimos.transforms` — the sandbox for operator-supplied transformations
* :mod:`gratimos.policy` — named rules that make the kernel's decisions explicable
* :mod:`gratimos.migrations` — data-as-code mutations as a reversible ledger
* :mod:`gratimos.a2a` — talking to other agents, including UiPath and Claude
* :mod:`gratimos.orchestrator` — depth, budget, and the loop that spends them
"""

from __future__ import annotations

__version__ = "0.1.0"

from .contextflow import ContextFlow, EventKind, KeyringEvent, MetaKeyring
from .errors import (
    GratimosError,
    MergeConflict,
    SandboxViolation,
    TimetravelConflict,
)
from .ids import StorageName, new_id
from .meta import CastMode, DataShape, FieldShape, TypeTag, Wrapped, infer_shape
from .orchestrator import Budget, Depth, GovernanceReport, Governor, Workspace, govern

__all__ = [
    "Budget",
    "CastMode",
    "ContextFlow",
    "DataShape",
    "Depth",
    "EventKind",
    "FieldShape",
    "GovernanceReport",
    "Governor",
    "GratimosError",
    "KeyringEvent",
    "MergeConflict",
    "MetaKeyring",
    "SandboxViolation",
    "StorageName",
    "TimetravelConflict",
    "TypeTag",
    "Workspace",
    "Wrapped",
    "__version__",
    "govern",
    "infer_shape",
    "new_id",
]
