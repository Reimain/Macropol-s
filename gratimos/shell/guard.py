"""The gate: what may run, what needs a human, and what is refused outright.

`Guard.review()` returns a verdict. It never executes anything, and it has no
code path that could — execution belongs to the caller, who must first hold a
verdict that permits it. Splitting the decision from the doing is what lets the
same guard sit in front of a CLI, a CI job and an autonomous agent and give all
three the same answer.

The three stances exist because two are not enough:

* **ALLOW** — go.
* **CONFIRM** — a human must say yes. This is the one that carries the design.
  A binary allow/refuse forces every judgement call into one bucket, and the
  bucket chosen is always "allow", because refusing routine work makes the tool
  unusable and people disable it.
* **REFUSE** — no verdict a human can give makes this safe here.

**`CONFIRM` where nobody can be asked is `REFUSE`.** In CI, or under an
unattended agent, there is no human, and treating "unattended" as "approved" is
precisely how a destructive command runs at 2am. The guard resolves this itself
rather than leaving it to each caller to remember.

**Unknown is refused by default.** A command naming a binary this machine does
not have is not "probably fine" — it is a script written against a different
machine, and running it is how a plan drifts into a language the box no longer
speaks. The policy can relax that; the default does not.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from .command import Analysis, Category, Command, Finding, Risk, analyse
from .environment import Environment


class Stance(str, Enum):
    """What the caller may do with this command."""

    ALLOW = "allow"
    CONFIRM = "confirm"
    REFUSE = "refuse"

    @property
    def runnable(self) -> bool:
        """Whether it may run *without further input*. CONFIRM is not runnable."""
        return self is Stance.ALLOW


@dataclass(frozen=True, slots=True)
class Policy:
    """Where the lines are. Stated once, applied everywhere.

    The defaults are deliberately strict, because the cost of the two errors is
    not symmetric: a wrongly refused command costs somebody thirty seconds, and
    a wrongly allowed one can cost the machine.
    """

    #: Highest risk that may run unattended.
    allow_up_to: Risk = Risk.SAFE
    #: Highest risk a human may approve. Above this, refusal is final.
    confirm_up_to: Risk = Risk.DANGEROUS
    require_known_binaries: bool = True
    allow_network: bool = True
    allow_privilege: bool = False
    #: Commands refused whatever else is true — the deny list wins over all.
    forbidden: frozenset[str] = frozenset()
    #: Binaries the deployment vouches for even if the probe missed them.
    trusted_binaries: frozenset[str] = frozenset()

    def to_dict(self) -> dict[str, Any]:
        return {
            "allow_up_to": self.allow_up_to.label,
            "confirm_up_to": self.confirm_up_to.label,
            "require_known_binaries": self.require_known_binaries,
            "allow_network": self.allow_network,
            "allow_privilege": self.allow_privilege,
            "forbidden": sorted(self.forbidden),
            "trusted_binaries": sorted(self.trusted_binaries),
        }


#: For a developer's own machine: more latitude, still no remote-code pipes.
DEVELOPMENT = Policy(
    allow_up_to=Risk.CAUTION, confirm_up_to=Risk.DANGEROUS,
    allow_privilege=True, require_known_binaries=False,
)

#: For anything that reaches a real environment. Nothing above SAFE runs
#: unattended, and CATASTROPHIC is never approvable.
PRODUCTION = Policy(
    allow_up_to=Risk.SAFE, confirm_up_to=Risk.DANGEROUS,
    allow_privilege=False, require_known_binaries=True,
)


@dataclass(frozen=True, slots=True)
class Verdict:
    """The answer, and everything needed to act on it or defend it."""

    stance: Stance
    analysis: Analysis
    reasons: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    decided_at: float = 0.0

    @property
    def risk(self) -> Risk:
        return self.analysis.risk

    @property
    def command(self) -> str:
        return self.analysis.command.raw

    @property
    def runnable(self) -> bool:
        return self.stance.runnable

    def __bool__(self) -> bool:
        return self.stance is not Stance.REFUSE

    def explain(self) -> str:
        """Written to be shown to whoever must decide, unedited."""
        lines = [f"{self.stance.value}: {self.command} [{self.risk.label}]"]
        lines.extend(f"  · {reason}" for reason in self.reasons)
        for finding in sorted(self.analysis.findings, key=lambda f: -f.risk):
            if finding.remediation:
                lines.append(f"  → {finding.remediation}")
        if self.requires:
            lines.append(f"  needs: {', '.join(self.requires)}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stance": self.stance.value,
            "risk": self.risk.label,
            "command": self.command,
            "reasons": list(self.reasons),
            "requires": list(self.requires),
            "analysis": self.analysis.to_dict(),
            "decided_at": self.decided_at,
        }

    def __str__(self) -> str:
        return f"{self.stance.value}: {self.command}"


class Guard:
    """Reviews commands. Cannot run them — there is no code path that would."""

    def __init__(self, policy: Policy | None = None,
                 environment: Environment | None = None) -> None:
        self.policy = policy if policy is not None else PRODUCTION
        self.environment = environment
        self._reviewed = 0
        self._refused = 0

    def review(
        self,
        command: str | Command,
        *,
        environment: Environment | None = None,
        attended: bool | None = None,
        now: float | None = None,
    ) -> Verdict:
        """Decide one command. Never raises for a refusal — see :meth:`permit`."""
        moment = time.time() if now is None else now
        where = environment if environment is not None else self.environment
        analysis = analyse(command, where, trusted=self.policy.trusted_binaries)
        policy = self.policy
        reasons: list[str] = []
        requires: list[str] = []
        self._reviewed += 1

        # Is anyone there to answer a CONFIRM?
        if attended is None:
            attended = where.interactive if where is not None else False

        def settle(stance: Stance) -> Verdict:
            if stance is Stance.REFUSE:
                self._refused += 1
            verdict = Verdict(
                stance=stance, analysis=analysis, reasons=tuple(reasons),
                requires=tuple(requires), decided_at=moment,
            )
            return verdict

        for binary in analysis.command.binaries:
            if binary in policy.forbidden:
                reasons.append(
                    f"{binary!r} is on this deployment's deny list, which overrides "
                    f"every other consideration"
                )
                return settle(Stance.REFUSE)

        if not analysis.command.parsed:
            reasons.append(
                f"the command could not be tokenised ({analysis.command.parse_error}), "
                f"so it cannot be reviewed — and what cannot be reviewed is not run"
            )
            return settle(Stance.REFUSE)

        unknown = analysis.unknown_binaries
        if unknown and policy.require_known_binaries:
            reasons.append(
                f"names {', '.join(repr(name) for name in unknown)}, which this "
                f"machine does not have; the script was written for a different box"
            )
            requires.extend(unknown)
            return settle(Stance.REFUSE)

        if Category.PRIVILEGE in analysis.categories and not policy.allow_privilege:
            if analysis.command.elevated:
                reasons.append("requires elevated privilege, which this policy withholds")
                return settle(Stance.REFUSE)

        if Category.NETWORK in analysis.categories and not policy.allow_network:
            reasons.append("reaches the network, which this policy withholds")
            return settle(Stance.REFUSE)

        if analysis.risk > policy.confirm_up_to:
            reasons.append(
                f"is {analysis.risk.label}, above the highest risk a human may "
                f"approve here ({policy.confirm_up_to.label})"
            )
            reasons.extend(f.message for f in analysis.blocking)
            return settle(Stance.REFUSE)

        if analysis.risk > policy.allow_up_to:
            if not attended:
                reasons.append(
                    f"is {analysis.risk.label} and would need a human to approve it, "
                    f"but nothing here can be asked — unattended is not approved"
                )
                reasons.extend(f.message for f in analysis.findings if f.risk >= Risk.CAUTION)
                return settle(Stance.REFUSE)
            reasons.append(
                f"is {analysis.risk.label}; a human must approve it before it runs"
            )
            reasons.extend(f.message for f in analysis.findings if f.risk >= Risk.CAUTION)
            return settle(Stance.CONFIRM)

        reasons.append(
            f"is {analysis.risk.label} and within what this policy runs unattended"
        )
        return settle(Stance.ALLOW)

    def permit(self, command: str | Command, **settings: Any) -> Verdict:
        """Review, and raise unless the result may run. For callers about to act."""
        from .command import ShellError

        verdict = self.review(command, **settings)
        if not verdict.runnable:
            raise ShellError(verdict.explain())
        return verdict

    def review_all(self, commands: Iterable[str], **settings: Any) -> tuple[Verdict, ...]:
        """Review a whole plan. Every command, not up to the first refusal.

        Stopping at the first problem makes a reviewer fix one thing, re-run,
        and find the next — which is the slowest possible way to learn that a
        plan has four problems.
        """
        return tuple(self.review(command, **settings) for command in commands)

    def status(self) -> dict[str, Any]:
        return {
            "policy": self.policy.to_dict(),
            "reviewed": self._reviewed,
            "refused": self._refused,
            "environment": self.environment.summary() if self.environment else None,
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Guard {self._refused}/{self._reviewed} refused>"


def summarise(verdicts: Sequence[Verdict]) -> str:
    """A whole plan's verdicts as one block, worst first."""
    if not verdicts:
        return "nothing to review"
    order = {Stance.REFUSE: 0, Stance.CONFIRM: 1, Stance.ALLOW: 2}
    counts: dict[str, int] = {}
    for verdict in verdicts:
        counts[verdict.stance.value] = counts.get(verdict.stance.value, 0) + 1
    head = ", ".join(f"{count} {stance}" for stance, count in sorted(counts.items()))
    lines = [f"{len(verdicts)} commands reviewed — {head}"]
    for verdict in sorted(verdicts, key=lambda v: order[v.stance]):
        lines.append(verdict.explain())
    return "\n".join(lines)
