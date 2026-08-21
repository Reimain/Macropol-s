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

And the reuse half — deciding what *not* to write, by finding what already
exists and proving it fits:

* :mod:`gratimos.ontology` — the concept lattice, and a need keyed by meaning
  rather than by phrasing
* :mod:`gratimos.reason` — forward chaining to propagate a discovery, backward
  chaining to answer a question, over one fact base
* :mod:`gratimos.crawl` — polite, cached, offline-capable discovery across the
  official package registries
* :mod:`gratimos.reuse` — the licence gate that excludes and the ranking that
  explains itself
* :mod:`gratimos.distill` — the loop that calls a validating agent when, and
  only when, memory cannot answer

These are imported lazily rather than eagerly: the reuse path pulls in an HTTP
transport and an inference engine, and a caller who only wants
:func:`govern` should not pay for either at import time.
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
    # Lazily resolved — see __getattr__ below.
    "Assessment",
    "Crawler",
    "DistillationLoop",
    "Engine",
    "Gate",
    "Need",
    "Ontology",
    "ReuseAssessor",
    "base_ontology",
    "need_from_text",
]

#: Names that resolve on first use rather than at import. Each maps to the
#: module that defines it.
_LAZY: dict[str, str] = {
    "Ontology": "gratimos.ontology",
    "base_ontology": "gratimos.ontology",
    "Need": "gratimos.ontology.need",
    "need_from_text": "gratimos.ontology.need",
    "Engine": "gratimos.reason",
    "Crawler": "gratimos.crawl",
    "Gate": "gratimos.reuse",
    "ReuseAssessor": "gratimos.reuse",
    "Assessment": "gratimos.reuse",
    "DistillationLoop": "gratimos.distill",
}


def __getattr__(name: str) -> object:
    """Resolve the reuse-path exports on first access.

    `gratimos.reuse` reaches SLPIE for licence analysis and `gratimos.crawl`
    builds an HTTP stack; importing both eagerly would make `import gratimos`
    meaningfully slower for every caller who never touches them.
    """
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    import importlib

    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value  # cached; the lookup happens once per process
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))
