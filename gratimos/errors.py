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
    """No policymaker could decide, or a decision was rejected."""


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
