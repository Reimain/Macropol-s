"""Dispatch — the kernel does not reimplement the device.

A verb is a syscall, a dispatcher is a driver, an external tool is the device. When
a capability wants ``git log`` or ``rg``, it dispatches: a Python approximation of
ripgrep would be slower, less correct, and a permanent tax on a solved problem.

**Gratimos is the batch abstraction, and it arrives as a driver rather than as an
import.** ``gratimos/shell/`` already parses pipelines, classifies risk and refuses
dangerous commands; that executor registers here through
:meth:`~slpie.dispatch.registry.ToolRegistry.use`. The dependency is inverted on
purpose — ring 0 declares the protocol and loads a driver, and never imports
Gratimos to get one, so invariant 8 keeps its single bridge (§15).

Where dispatch is right, and where it is not:

* **Dispatch** where the external tool is the authority or is dramatically faster —
  ``git`` for history, ``rg`` for content search, ``jq`` for reshaping,
  ``pdftotext`` for what the stdlib PDF reader cannot reach.
* **Stay in-process** where the data is typed domain objects. ``filter``, ``sort``
  and ``head`` (§24) operate on ``Finding``\\ s, and ``grep`` cannot see a
  ``Severity`` — dispatching them would degrade a typed comparison into string
  matching against a rendering. That is not a failure to dispatch; it is
  dispatching to the right device, which there is our own type system.

Two consequences are handled rather than discovered later. Dispatching threatens
the "same composition, same digest" promise, so every tool declares a
:class:`~slpie.dispatch.tool.Determinism` class, runs under a normalised
environment, and contributes its version as evidence. And a missing binary is a
**capability gap, not a crash**: the fallback runs and is *announced*, because an
approximation that looks identical to the real answer is worse than a slower
correct one.
"""

from .local import DispatchError, LocalDispatcher, available, probe, reset_probes
from .registry import Dispatcher, ToolRegistry, registry, reset
from .tool import (
    KNOWN,
    NORMALISED_ENV,
    Determinism,
    Invocation,
    Outcome,
    Tool,
    known,
)

__all__ = [
    "KNOWN",
    "NORMALISED_ENV",
    "Determinism",
    "DispatchError",
    "Dispatcher",
    "Invocation",
    "LocalDispatcher",
    "Outcome",
    "Tool",
    "ToolRegistry",
    "available",
    "known",
    "probe",
    "registry",
    "reset",
    "reset_probes",
]
