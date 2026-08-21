"""The dangerous half — refused here, performed elsewhere.

Two responsibilities, and keeping them in one small module is deliberate: the
refusal and the delegation are the whole of what ring 0 does about applying, and
splitting them would make it possible to reach one without the other.

── The guard is reused, never reimplemented ─────────────────────────────

`slpie/binding/guard.py` already answers "may this dangerous thing happen": it
refuses an unconfirmed live binding and refuses a write against a live target
without a separate grant. An apply is the same class of action — it changes
something real and cannot be undone by looking away — so it goes through the
same object.

The alternative was a `--confirm` check inside the verb. That would work, and it
would be a second implementation of a decision that already has one, in a
different file, with its own bugs. §16 refuses that trade for FastAPI and §30
refuses it for the gateway; this is the same refusal.

── Applying is ring 1, and its absence is a gap ─────────────────────────

Nothing here runs `terraform`. Ring 0 emits text; the binaries and the cloud
credentials belong to the operator, and `slpie_enterprise/deploy/` is where
shelling out lives. Absent that adapter, this reports a **capability gap naming
the tool** — the treatment §3 gives a refused capability and §27 gives a missing
binary — rather than raising, and certainly rather than returning a success
nobody performed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..errors import TargetRefused
from .manifest import Deployment, DeployTarget


@dataclass(frozen=True, slots=True)
class Applied:
    """What an apply did, or why it did nothing."""

    environment: str
    applied: bool = False
    #: The adapter that performed it, empty when nothing did.
    by: str = ""
    steps: tuple[str, ...] = ()
    gaps: tuple[str, ...] = field(default_factory=tuple)

    @property
    def summary(self) -> str:
        if self.applied:
            return f"applied {self.environment} through {self.by}"
        return f"{self.environment} was not applied: {self.gaps[0] if self.gaps else 'no reason given'}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "environment": self.environment, "applied": self.applied,
            "by": self.by, "steps": list(self.steps), "gaps": list(self.gaps),
            "summary": self.summary,
        }


def refuse_unconfirmed(declared: Deployment, context: Any) -> None:
    """Raise `TargetRefused` unless the operator confirmed this apply.

    Two conditions, both required, and they are not redundant:

    * the **manifest** must say `target: apply` — the same one-tag gate the
      environment manifest uses for `simulated | live`, so a plan-only manifest
      cannot be applied by passing a flag;
    * the **caller** must have confirmed — the flag somebody had to set.

    A manifest that says `apply` is a statement of intent written down and
    reviewable in a diff. A confirmation is a person at the moment it happens.
    Requiring both means neither a stale file nor a slip of the hand is enough
    on its own.
    """
    from ..binding.guard import Guard
    from ..binding.target import TargetSelection
    from ..environment.manifest import Target

    if declared.target is not DeployTarget.APPLY:
        raise TargetRefused(
            f"apply {declared.environment}",
            f"the manifest declares target: {declared.target.value}. Applying "
            f"needs `target: apply` in the manifest as well as --confirmed, so "
            f"that the intent is reviewable in a diff and not only in a shell.",
        )

    # The guard's own vocabulary: an apply is a write against a live target, and
    # that is exactly what it is asked. Constructed here rather than taken from
    # the engine because an apply must be refusable with no environment open —
    # `slpie deploy apply` in a directory with a deployment manifest and nothing
    # else has to be stopped, and an engine-shaped guard would not be there.
    guard = Guard(selection=TargetSelection(
        default=Target.LIVE, confirmed=bool(getattr(context, "confirmed", False)),
    ))
    guard.check_binding(declared.environment)
    if getattr(context, "confirmed", False):
        guard.grant_writes(reason=f"deploy apply {declared.environment}")
    guard.check_capability(declared.environment, "apply")


def apply_through(declared: Deployment, context: Any, *, emitter: str = "") -> Applied:
    """Hand the apply to the ring-1 adapter, or report that there is none."""
    applier = getattr(context.engine, "deployment_applier", None) if context.engine else None
    if applier is None:
        return Applied(
            environment=declared.environment,
            gaps=(
                "this build cannot apply: ring 0 emits text and does not shell "
                "out to terraform, helm or kubectl. Install `slpie[enterprise]` "
                "for the adapter, or render the artifacts and apply them "
                "yourself — `slpie deploy render --write` produces exactly what "
                "an apply would have run.",
            ),
        )
    return applier(declared, emitter=emitter)
