"""Supply-chain risk: who you are trusting, and whether you meant to.

A vulnerability is a defect in a package you chose. Supply-chain risk is the
other half — the package you did not choose, the one whose name is one keystroke
from the one you meant, the one nobody has touched in four years, the one that
resolves from somewhere other than the registry everything else came from.

Four rules, and each is about a different way trust is misplaced:

* **typosquat** — a dependency one edit away from a much more popular package.
  The single highest-yield check in this family, because the attack is cheap and
  the defence is a lookup.
* **unmaintained** — nothing published in a long time. Reported as a *fact with
  an age*, never as a verdict: plenty of good libraries are finished, and a rule
  that called `left-pad` abandoned would be wrong about the least interesting
  package in history.
* **integrity** — a lockfile records a hash for a package and two lockfiles
  disagree about it. That is either a corrupted install or a substituted
  artifact, and both are worth stopping for.
* **unpinned** — a dependency that resolves to whatever was newest that day.
  Not an attack, but it is the precondition for one: an unpinned range is the
  door a compromised release walks through without anyone changing a file.

Every rule here is **offline and data-driven**. The popularity list, the release
dates and the registry expectations arrive through `RuleContext.facts`; nothing
in this module fetches. That is what makes a governance run reproducible, and
identical in the simulator and against a real environment — and it is why a
missing fact makes a rule *decline* rather than guess.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from ...domain.evidence import strongest
from ...domain.finding import Finding, FindingKind, Remediation
from ...domain.lifecycle import Severity
from ..rules import Rule, RuleContext, RuleSet, cite, packages

#: Facts a caller supplies. Named once so the verb, the manual and the rules
#: cannot disagree about their spelling.
POPULAR_FACT = "popular_packages"        # {ecosystem: (name, …)} or (name, …)
RELEASED_FACT = "last_released"          # {coordinate: epoch seconds}
UNMAINTAINED_AFTER = "unmaintained_after_days"

#: Two years with no release. Long enough that a finished library is not
#: constantly accused, short enough to catch genuine abandonment.
DEFAULT_STALE_DAYS = 730

#: A name shorter than this makes edit distance meaningless — half the registry
#: is one edit from `ms`.
MIN_NAME = 4

#: Ranges that pin nothing. Mirrors `l8_optimize.UNCONSTRAINED`, which judges the
#: same strings for a different reason: there it is about resolution being
#: arbitrary, here it is about a compromised release arriving unnoticed.
UNPINNED = frozenset({"*", "", "any", "latest", ">=0", ">=0.0.0", "x", "X"})




def edit_distance(left: str, right: str, *, ceiling: int = 2) -> int:
    """Damerau-Levenshtein distance, abandoned once it passes `ceiling`.

    **A transposition costs one edit, not two**, and that is the whole reason
    this is not plain Levenshtein. Typosquats are overwhelmingly transpositions —
    `lodahs` for `lodash`, `strapi` for `strpai` — because that is what fingers
    actually do. Under Levenshtein a swapped pair scores 2, so a threshold of 1
    misses the most common attack shape entirely, and raising the threshold to 2
    to compensate floods the report with unrelated short names. Counting the swap
    correctly is the fix; loosening the threshold is not.

    Bounded because the answer is only ever compared against a small threshold,
    and a full matrix over every package name against every popular name is the
    difference between a rule that runs and one that gets disabled.
    """
    if abs(len(left) - len(right)) > ceiling:
        return ceiling + 1
    if left == right:
        return 0

    # Optimal string alignment: two rows back, which is what lets a transposition
    # be seen as a single operation.
    before: list[int] = []
    previous = list(range(len(right) + 1))

    for i, a in enumerate(left, start=1):
        current = [i]
        for j, b in enumerate(right, start=1):
            cost = min(
                previous[j] + 1,               # deletion
                current[j - 1] + 1,            # insertion
                previous[j - 1] + (a != b),    # substitution
            )
            if (
                i > 1 and j > 1
                and a == right[j - 2] and left[i - 2] == b
            ):
                cost = min(cost, before[j - 2] + 1)   # transposition
            current.append(cost)
        if min(current) > ceiling:
            return ceiling + 1
        before, previous = previous, current

    return previous[-1]


def _popular(context: RuleContext, ecosystem: str) -> tuple[str, ...]:
    """The popularity list for one ecosystem, however the caller shaped it."""
    supplied = context.fact(POPULAR_FACT) or ()
    if isinstance(supplied, Mapping):
        return tuple(supplied.get(ecosystem, ()) or ())
    return tuple(supplied)


def _ecosystem(node: Any) -> str:
    identity = getattr(node, "identity", None)
    return str(getattr(identity, "type", "") or "")




def typosquat_rule(*, distance: int = 1) -> Rule:
    """Dependencies one edit away from a much more popular package."""

    def matches(context: RuleContext) -> bool:
        return context.graph is not None and bool(context.fact(POPULAR_FACT))

    def evaluate(context: RuleContext) -> list[Finding]:
        raised: list[Finding] = []
        for node in packages(context):
            name = str(node.name or "")
            if len(name) < MIN_NAME:
                continue
            popular = _popular(context, _ecosystem(node))
            if not popular or name in popular:
                continue                          # it *is* the popular one

            near = [
                candidate for candidate in popular
                if 0 < edit_distance(name, candidate, ceiling=distance) <= distance
            ]
            if not near:
                continue

            raised.append(Finding(
                kind=FindingKind.TYPOSQUAT_SUSPECT,
                severity=Severity.CRITICAL,
                subject=node.id,
                title=f"{node.display} is one edit from {near[0]}",
                detail=(
                    f"{name!r} differs from the far more widely used "
                    f"{', '.join(repr(item) for item in near[:3])} by a single "
                    f"character. Confirm this is the package that was intended: "
                    f"typosquatting is cheap to attempt and installs run as you"
                ),
                code=near[0],
                evidence=cite(node.evidence),
                remediation=Remediation(
                    summary=(
                        f"confirm {name!r} is intended; if not, replace it with "
                        f"{near[0]!r} and treat the machine as exposed"
                    ),
                    action="replace", target=near[0], breaking=True,
                ),
                rule_id="supplychain.typosquat",
                properties={"near": near[:5], "ecosystem": _ecosystem(node)},
            ))
        return raised

    return Rule(
        id="supplychain.typosquat",
        title="a dependency's name is one edit from a popular package",
        kind=FindingKind.TYPOSQUAT_SUSPECT,
        severity=Severity.CRITICAL,
        evaluate=evaluate,
        matches=matches,
        remediation="confirm the package is the one that was intended",
        description=(
            "compares against a popularity list supplied by the caller; declines "
            "entirely when none was supplied rather than inventing one"
        ),
        tags=("security", "supply-chain"),
    )


def unmaintained_rule(*, stale_days: int = DEFAULT_STALE_DAYS) -> Rule:
    """Packages with no release in a long time."""

    def matches(context: RuleContext) -> bool:
        # Needs both the dates and a clock. Without `now` the age is unknowable,
        # and computing it from the wall clock would make the same tree produce
        # different findings on different days.
        return (
            context.graph is not None
            and bool(context.fact(RELEASED_FACT))
            and context.now > 0
        )

    def evaluate(context: RuleContext) -> list[Finding]:
        released: Mapping[str, Any] = context.fact(RELEASED_FACT) or {}
        threshold = int(context.fact(UNMAINTAINED_AFTER) or stale_days)
        raised: list[Finding] = []

        for node in packages(context):
            when = released.get(node.coordinate) or released.get(node.name)
            if not when:
                continue
            days = int((context.now - int(when)) / 86400)
            if days < threshold:
                continue

            raised.append(Finding(
                kind=FindingKind.UNMAINTAINED_PACKAGE,
                severity=Severity.MEDIUM,
                subject=node.id,
                title=f"{node.display} last published {days // 365} year(s) ago",
                detail=(
                    f"no release in {days} days. This is an age, not a verdict: "
                    f"a small library can be finished rather than abandoned. It "
                    f"matters because an unmaintained package is one where the "
                    f"next vulnerability has nobody to fix it"
                ),
                evidence=cite(node.evidence),
                remediation=Remediation(
                    summary=(
                        f"confirm {node.name} is still maintained, or plan a "
                        f"replacement before you need one urgently"
                    ),
                    action="replace", target=node.name,
                ),
                rule_id="supplychain.unmaintained",
                properties={"days_since_release": days, "threshold_days": threshold},
            ))
        return raised

    return Rule(
        id="supplychain.unmaintained",
        title="a dependency has not been released in a long time",
        kind=FindingKind.UNMAINTAINED_PACKAGE,
        severity=Severity.MEDIUM,
        evaluate=evaluate,
        matches=matches,
        remediation="confirm it is still maintained, or plan a replacement",
        description=(
            "reports an age rather than a judgement, and declines without a "
            "supplied clock so the same tree cannot answer differently by date"
        ),
        tags=("security", "supply-chain", "lifecycle"),
    )


def integrity_rule() -> Rule:
    """One package, two different recorded hashes."""

    def matches(context: RuleContext) -> bool:
        return context.graph is not None

    def evaluate(context: RuleContext) -> list[Finding]:
        # Grouped by coordinate rather than by node: the whole point is to catch
        # one package recorded twice with different hashes, and if they had
        # merged onto one node there would be nothing left to compare.
        by_coordinate: dict[str, list[tuple[Any, str]]] = {}
        for node in packages(context):
            recorded = str((getattr(node, "properties", {}) or {}).get("integrity", ""))
            if recorded:
                by_coordinate.setdefault(node.coordinate, []).append((node, recorded))

        raised: list[Finding] = []
        for coordinate, entries in sorted(by_coordinate.items()):
            hashes = {value for _node, value in entries}
            if len(hashes) < 2:
                continue
            node = entries[0][0]
            raised.append(Finding(
                kind=FindingKind.INTEGRITY_MISMATCH,
                severity=Severity.CRITICAL,
                subject=node.id,
                title=f"{coordinate} has two different recorded hashes",
                detail=(
                    f"{len(hashes)} distinct integrity values were recorded for "
                    f"the same package: {', '.join(sorted(h[:24] for h in hashes))}. "
                    f"Either an install is corrupted or an artifact was "
                    f"substituted; both are worth stopping for"
                ),
                code=coordinate,
                evidence=cite(tuple(
                    strongest(item.evidence) for item, _ in entries[:4]
                )),
                remediation=Remediation(
                    summary=(
                        "delete the lockfiles and reinstall from the registry; if "
                        "the hashes still differ, treat the artifact as suspect"
                    ),
                    action="pin", breaking=True,
                ),
                rule_id="supplychain.integrity",
                properties={"hashes": sorted(hashes)},
            ))
        return raised

    return Rule(
        id="supplychain.integrity",
        title="one package is recorded with two different hashes",
        kind=FindingKind.INTEGRITY_MISMATCH,
        severity=Severity.CRITICAL,
        evaluate=evaluate,
        matches=matches,
        remediation="reinstall from the registry; if it persists, treat it as suspect",
        description="corrupted install or substituted artifact — both stop a release",
        tags=("security", "supply-chain", "integrity"),
    )


def unpinned_rule() -> Rule:
    """Dependencies declared with a range that pins nothing."""

    def matches(context: RuleContext) -> bool:
        return context.graph is not None

    def evaluate(context: RuleContext) -> list[Finding]:
        raised: list[Finding] = []
        for node in context.nodes(live=True):
            for edge in context.graph.edges_from(node.id, live=True):
                declared = str((edge.properties or {}).get("range", "")).strip()
                if declared.lower() not in UNPINNED or not declared:
                    continue
                target = context.graph.node(edge.dst)
                raised.append(Finding(
                    kind=FindingKind.UNPINNED_DEPENDENCY,
                    severity=Severity.MEDIUM,
                    subject=edge.dst,
                    title=(
                        f"{getattr(target, 'display', edge.dst)} is declared "
                        f"as {declared!r}"
                    ),
                    detail=(
                        f"{node.display} accepts any version of it. This is not an "
                        f"attack, it is the precondition for one: a compromised "
                        f"release is installed without anybody changing a file"
                    ),
                    code=declared,
                    evidence=cite(edge.evidence, node.evidence),
                    remediation=Remediation(
                        summary="declare a bounded range, and commit the lockfile",
                        action="pin", target=edge.dst,
                    ),
                    rule_id="supplychain.unpinned",
                    related=(node.id,),
                    properties={"range": declared},
                ))
        return raised

    return Rule(
        id="supplychain.unpinned",
        title="a dependency is declared with a range that pins nothing",
        kind=FindingKind.UNPINNED_DEPENDENCY,
        severity=Severity.MEDIUM,
        evaluate=evaluate,
        matches=matches,
        remediation="declare a bounded range, and commit the lockfile",
        description="the door a compromised release walks through unnoticed",
        tags=("security", "supply-chain"),
    )


def supplychain_rules(*, stale_days: int = DEFAULT_STALE_DAYS) -> RuleSet:
    """The supply-chain family, as a set for registration."""
    return RuleSet(
        (
            typosquat_rule(),
            unmaintained_rule(stale_days=stale_days),
            integrity_rule(),
            unpinned_rule(),
        ),
        name="supplychain",
    )
