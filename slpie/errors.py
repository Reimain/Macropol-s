"""Error taxonomy for SLPIE.

Subsystems route on exception *type*, never on message text. Every failure mode
the platform can produce deliberately is named here, so a governance rule or a
UI view can react to a class of failure without parsing strings.
"""

from __future__ import annotations


class SlpieError(Exception):
    """Root of every error SLPIE raises deliberately."""


# --- identity ------------------------------------------------------------


class IdentityError(SlpieError):
    """An identifier could not be parsed, normalized, or rendered."""


class InvalidPurl(IdentityError):
    """A Package URL is malformed or violates the purl specification."""

    def __init__(self, value: str, reason: str) -> None:
        self.value = value
        self.reason = reason
        super().__init__(f"invalid purl {value!r}: {reason}")


class InvalidUrn(IdentityError):
    """An SLPIE URN is malformed."""

    def __init__(self, value: str, reason: str) -> None:
        self.value = value
        self.reason = reason
        super().__init__(f"invalid urn {value!r}: {reason}")


# --- evidence ------------------------------------------------------------


class EvidenceError(SlpieError):
    """Something is wrong with the evidence supporting a claim."""


class EvidenceRequired(EvidenceError):
    """An attempt was made to assert a relationship with no supporting evidence.

    This is the platform's central invariant. It is a programming error, not a
    data condition — a discoverer that cannot cite a source has not discovered
    anything.
    """

    def __init__(self, subject: str = "") -> None:
        self.subject = subject
        super().__init__(
            f"no relationship may exist without evidence"
            + (f": {subject}" if subject else "")
        )


class ContradictoryEvidence(EvidenceError):
    """Two pieces of evidence make incompatible claims about the same fact."""

    def __init__(self, subject: str, left: str, right: str) -> None:
        self.subject = subject
        self.left = left
        self.right = right
        super().__init__(f"contradictory evidence for {subject}: {left} vs {right}")


# --- versions and licences ----------------------------------------------


class VersionError(SlpieError):
    """A version string or range could not be parsed or compared."""


class LicenseError(SlpieError):
    """A licence expression could not be parsed or evaluated."""


# --- ledger and graph ----------------------------------------------------


class LedgerError(SlpieError):
    """The immutable ledger rejected an operation."""


class ChainBroken(LedgerError):
    """The hash chain does not verify — history has been tampered with or truncated."""

    def __init__(self, sequence: int, expected: str, found: str) -> None:
        self.sequence = sequence
        self.expected = expected
        self.found = found
        super().__init__(
            f"ledger chain broken at sequence {sequence}: expected {expected}, found {found}"
        )


class GraphError(SlpieError):
    """A graph operation failed."""


class NodeNotFound(GraphError):
    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        super().__init__(f"no such node: {node_id}")


class SchemaViolation(GraphError):
    """A node or edge violates the registered schema for its kind."""


# --- environment, binding, station --------------------------------------


class ManifestError(SlpieError):
    """The environment manifest is malformed or inconsistent."""


class BindingError(SlpieError):
    """A capability could not be bound to a connector."""


class TargetRefused(BindingError):
    """An operation against a live target was refused by the guard."""

    def __init__(self, operation: str, reason: str) -> None:
        self.operation = operation
        self.reason = reason
        super().__init__(f"live target refused {operation!r}: {reason}")


class StationError(SlpieError):
    """Attachment, capability negotiation, or handover failed."""


class CapabilityRefused(StationError):
    """An element refused a capability. This becomes a reported gap, not a crash."""

    def __init__(self, element: str, capability: str, reason: str) -> None:
        self.element = element
        self.capability = capability
        self.reason = reason
        super().__init__(f"{element} refused {capability!r}: {reason}")


# --- plugins -------------------------------------------------------------


class PluginError(SlpieError):
    """A plugin could not be loaded, described, or executed."""


class PluginProtocolError(PluginError):
    """An out-of-process plugin violated the wire contract."""


class PluginQuarantined(PluginError):
    """A plugin misbehaved and has been withdrawn for this run."""

    def __init__(self, plugin_id: str, reason: str) -> None:
        self.plugin_id = plugin_id
        self.reason = reason
        super().__init__(f"plugin {plugin_id!r} quarantined: {reason}")


# --- discovery and reasoning --------------------------------------------


class DiscoveryError(SlpieError):
    """A discoverer failed. Reported as a gap; it never aborts a scan."""


class ReasoningError(SlpieError):
    """A reasoning layer could not complete."""


class ConstraintUnsatisfiable(ReasoningError):
    """No version assignment satisfies the constraint set.

    Carries the incompatibility chain so the failure can be explained rather
    than merely reported.
    """

    def __init__(self, subject: str, explanation: str = "") -> None:
        self.subject = subject
        self.explanation = explanation
        super().__init__(
            f"no solution for {subject}" + (f": {explanation}" if explanation else "")
        )


class GovernanceError(SlpieError):
    """A governance rule or policy could not be registered or evaluated.

    Named for the package rather than for "policy" because `gratimos.errors`
    already has a `PolicyError` meaning something else — no policymaker could
    decide — and two classes with one name across two taxonomies makes
    `from ..errors import PolicyError` ambiguous to whoever reads it next.
    """


# --- memory, simulation, and the bus -------------------------------------
#
# These three exist because the subsystems below them were raising builtins for
# want of anything better. A `ValueError` from the spill tier and a `ValueError`
# from a purl parser are indistinguishable to a caller trying to decide whether
# to retry, which is the whole reason this module opens by saying subsystems
# route on type rather than on message text.


class SpillError(SlpieError):
    """A block could not be encoded, stored or read back, or a session was
    misconfigured or given a malformed id or name.

    Declared here rather than in `slpie/spill/codec.py`, where it used to live.
    A taxonomy whose classes are scattered across the subsystems that raise them
    is a taxonomy a caller has to go looking for, and `slpie/spill/budget.py`
    duly raised `ValueError` for want of finding this one.

    Note what is *not* here: `SpilledSequence.__getitem__` raises `IndexError`,
    because it satisfies the `Sequence` protocol and a caller iterating it must
    not have to know it is spilled. Substitutability outranks taxonomy at that
    one seam, deliberately.
    """


class SimulatorError(SlpieError):
    """The simulated world could not do what was asked of it."""


class ScenarioNotFound(SimulatorError):
    """No scenario by that name. Carries what is available, so a typo is cheap."""

    def __init__(self, name: str, available: tuple[str, ...] = ()) -> None:
        self.name = name
        self.available = tuple(available)
        super().__init__(
            f"no scenario named {name!r}"
            + (f"; available: {', '.join(self.available)}" if self.available else "")
        )


class BusError(SlpieError):
    """An event subscription could not be registered."""


# --- artifacts and interface --------------------------------------------


class ArtifactError(SlpieError):
    """An output artifact could not be generated."""


class UiError(SlpieError):
    """The interface layer failed."""
