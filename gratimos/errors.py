"""Error taxonomy for the Gratimos kernel.

Every failure mode the kernel can produce is named here so that policymakers can
route on the exception type instead of parsing messages.
"""

from __future__ import annotations


class GratimosError(Exception):
    """Root of every error the kernel raises deliberately."""


# --- ContextFlow / trace -------------------------------------------------


class ContextFlowError(GratimosError):
    """Something went wrong on the versioned spine."""


class TimetravelConflict(ContextFlowError):
    """An event was appended on a base version that is no longer an ancestor.

    This is the guard that stops a rolled-back branch from silently re-applying
    its mutations on top of history that has moved on.
    """

    def __init__(self, base_version: str, head_version: str, actor: str = "") -> None:
        self.base_version = base_version
        self.head_version = head_version
        self.actor = actor
        super().__init__(
            f"event based on {base_version!r} cannot apply to head {head_version!r}"
            + (f" (actor={actor!r})" if actor else "")
        )


class RollbackError(ContextFlowError):
    """A rollback target was missing, already superseded, or not reachable."""


# --- shapes / casting ----------------------------------------------------


class ShapeError(GratimosError):
    """A shape could not be inferred, merged, or reconciled."""


class CastError(ShapeError):
    """A value could not be cast into the requested type under the active policy."""


# --- hubs / memory / storage --------------------------------------------


class HubError(GratimosError):
    """Routing, channel, or hub registration failure."""


class MemoryBudgetExceeded(HubError):
    """A payload could not be held in memory and no spill target accepted it."""


class StorageError(GratimosError):
    """An object store rejected a read or write."""


class UnsafePathError(StorageError):
    """A key resolved outside its secured repository root."""


# --- transformations / sandbox ------------------------------------------


class TransformError(GratimosError):
    """A transformation module failed to load or execute."""


class SandboxViolation(TransformError):
    """Static or runtime policy rejected a transformation."""

    def __init__(self, reason: str, *, node: str = "", lineno: int = 0) -> None:
        self.reason = reason
        self.node = node
        self.lineno = lineno
        where = f" at line {lineno}" if lineno else ""
        super().__init__(f"{reason}{where}" + (f" [{node}]" if node else ""))


# --- codegen -------------------------------------------------------------


class CodegenError(GratimosError):
    """A generator could not emit valid code for a shape."""


class MergeConflict(CodegenError):
    """Generated code and the on-disk module diverged irreconcilably."""

    def __init__(self, symbol: str, detail: str = "") -> None:
        self.symbol = symbol
        self.detail = detail
        super().__init__(f"merge conflict on {symbol!r}" + (f": {detail}" if detail else ""))


# --- policy / migrations -------------------------------------------------


class PolicyError(GratimosError):
    """No policymaker could decide, or a decision was rejected.

    Distinct from `slpie.errors.GovernanceError`, which is about a governance
    rule failing to register or evaluate. The two used to share the name
    `PolicyError` across the two taxonomies, which made
    `from ..errors import PolicyError` say nothing about which one it meant.
    """


# --- shell, crawling, probing, reuse, ontology ---------------------------
#
# These five roots used to be declared in the subsystem that raised them —
# `ShellError` in `shell/command.py`, `CrawlError` in `crawl/policy.py`,
# `OntologyError` in `ontology/concepts.py`, and so on. A taxonomy scattered
# across the modules it serves is one a caller has to go looking for, and the
# reliable symptom is what this module's own docstring warns about: the
# subsystems next door raise `ValueError` instead, because nothing obvious was
# importable. Subclasses stay with their subsystem; the roots live here.


class ShellError(GratimosError):
    """A command could not be read, or was refused before execution."""


class CrawlError(GratimosError):
    """A fetch was refused, malformed, or forbidden by policy."""


class ProbeError(GratimosError):
    """A probe could not read its target."""


class AccessDenied(ProbeError):
    """A request was refused by the access policy before it was sent."""


class ReuseError(GratimosError):
    """A reuse assessment could not be configured or completed."""


class OntologyError(GratimosError):
    """The concept lattice is malformed or a concept is unknown."""


class OrchestrationError(GratimosError):
    """A cycle could not be planned, or was configured with something invalid."""


class MigrationError(GratimosError):
    """A data-as-code mutation could not be recorded or replayed."""


# --- agents / A2A --------------------------------------------------------


class AgentError(GratimosError):
    """Agent interop failure."""


class TransportError(AgentError):
    """The A2A transport could not deliver or decode a message."""


class ProtocolError(AgentError):
    """A peer returned a payload that violates the A2A contract."""


class TaskNotFound(AgentError):
    """A task id is unknown to the executor."""
