"""A verdict, and what makes it a judgement rather than an opinion.

`gratimos/reference/` records what this system claims to know, with a mandatory
source per entry. That is a registry. It is not a judge, and the difference is
where the value is: a registry states claims, a judge decides whether the code
honours them — **deterministically, reproducibly, and with a file and a line for
every verdict.**

Reproducibility is the whole claim. A verdict you cannot reproduce is an opinion,
and an opinion cannot gate a release. So a `Judgement` is content-addressed over
what it asserts, an `Audit` digests the ordered set of them, and the same tree
yields the same digest on any machine — which is what lets CI pin "the
architecture has not drifted" to a single comparable value, the same trick the
snapshot `root_digest` uses for content.

`INDETERMINATE` is deliberate and load-bearing. A file that fails to parse, a
dynamic import, a `getattr` call target — the judge says it could not decide
rather than guessing either way. A judge that silently returned `UPHELD` for what
it could not examine would be worse than no judge, because the green result is now
a lie. Indeterminacy counts against coverage, and coverage is reported alongside
the verdicts rather than buried.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Iterator, Mapping, Sequence

from ..domain.evidence import Evidence
from ..domain.identity import digest


class Verdict(str, Enum):
    """What a rule decided about one subject."""

    UPHELD = "upheld"
    VIOLATED = "violated"
    INAPPLICABLE = "inapplicable"     # the rule does not apply here
    INDETERMINATE = "indeterminate"   # it applies, and could not be decided

    @property
    def decided(self) -> bool:
        """Whether the judge actually reached a conclusion."""
        return self in (Verdict.UPHELD, Verdict.VIOLATED)

    @property
    def counts_against_coverage(self) -> bool:
        """`INAPPLICABLE` is not a gap; `INDETERMINATE` is."""
        return self is Verdict.INDETERMINATE

    @property
    def blocking(self) -> bool:
        return self is Verdict.VIOLATED


@dataclass(frozen=True, slots=True)
class Judgement:
    """One rule's decision about one subject, with evidence for it."""

    rule: str
    verdict: Verdict
    subject: str
    detail: str = ""
    evidence: tuple[Evidence, ...] = ()
    remediation: str = ""

    @property
    def digest(self) -> str:
        """Content-addressed over the claim, never over the run.

        Excludes evidence *objects* and includes their locations, so a judgement
        is stable across runs while still moving when the thing it points at
        moves. A digest that shifted with a timestamp would be useless for the one
        job a digest has here.
        """
        return digest({
            "rule": self.rule,
            "verdict": self.verdict.value,
            "subject": self.subject,
            "detail": self.detail,
            "at": sorted(item.location.reference for item in self.evidence),
        })

    @property
    def grounded(self) -> bool:
        """A violation with no evidence is an accusation, not a finding."""
        return bool(self.evidence) or self.verdict is not Verdict.VIOLATED

    @property
    def reference(self) -> str:
        return self.evidence[0].location.reference if self.evidence else self.subject

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule, "verdict": self.verdict.value,
            "subject": self.subject, "detail": self.detail,
            "remediation": self.remediation, "digest": self.digest,
            "grounded": self.grounded,
            "at": [item.location.reference for item in self.evidence],
        }

    def render(self) -> str:
        mark = {
            Verdict.UPHELD: "✓", Verdict.VIOLATED: "✕",
            Verdict.INDETERMINATE: "?", Verdict.INAPPLICABLE: "·",
        }[self.verdict]
        line = f"  {mark} {self.rule:<22} {self.subject}"
        if self.detail:
            line += f"\n      {self.detail}"
        if self.evidence:
            line += "\n      at " + ", ".join(
                item.location.reference for item in self.evidence[:3]
            )
        if self.verdict is Verdict.VIOLATED and self.remediation:
            line += f"\n      → {self.remediation}"
        return line

    def __str__(self) -> str:
        return f"{self.verdict.value.upper()} {self.rule} on {self.subject}"


@dataclass(frozen=True, slots=True)
class Audit:
    """Every judgement, its digest, and an honest coverage figure."""

    judgements: tuple[Judgement, ...] = ()
    tree: str = ""
    scanned: int = 0
    errors: tuple[str, ...] = ()

    @property
    def violations(self) -> tuple[Judgement, ...]:
        return tuple(
            item for item in self.judgements if item.verdict is Verdict.VIOLATED
        )

    @property
    def indeterminate(self) -> tuple[Judgement, ...]:
        return tuple(
            item for item in self.judgements
            if item.verdict is Verdict.INDETERMINATE
        )

    @property
    def upheld(self) -> tuple[Judgement, ...]:
        return tuple(
            item for item in self.judgements if item.verdict is Verdict.UPHELD
        )

    @property
    def clean(self) -> bool:
        return not self.violations

    @property
    def coverage(self) -> float:
        """Of the applicable judgements, how many were actually decided.

        Never rounded up. A judge reporting 100% coverage while a tenth of the
        tree failed to parse is the exact dishonesty `INDETERMINATE` exists to
        prevent.
        """
        applicable = [
            item for item in self.judgements
            if item.verdict is not Verdict.INAPPLICABLE
        ]
        if not applicable:
            return 0.0
        decided = sum(1 for item in applicable if item.verdict.decided)
        return round(decided / len(applicable), 4)

    @property
    def digest(self) -> str:
        """The whole audit, as one comparable value.

        Sorted, so filesystem order cannot change it — a digest that depended on
        `os.walk` ordering would differ between machines for identical trees, and
        the value exists precisely to be compared between machines.
        """
        return digest({
            "judgements": sorted(item.digest for item in self.judgements),
        })

    def by_rule(self) -> dict[str, tuple[Judgement, ...]]:
        found: dict[str, list[Judgement]] = {}
        for item in self.judgements:
            found.setdefault(item.rule, []).append(item)
        return {name: tuple(items) for name, items in sorted(found.items())}

    def verdict_for(self, rule: str) -> Verdict:
        """One rule's overall verdict. Worst wins, and undecided beats upheld.

        A rule that upheld nineteen subjects and could not decide the twentieth is
        not a passing rule — reporting it as one is how a blind spot becomes a
        green tick.
        """
        found = [item.verdict for item in self.judgements if item.rule == rule]
        if not found:
            return Verdict.INAPPLICABLE
        if Verdict.VIOLATED in found:
            return Verdict.VIOLATED
        if Verdict.INDETERMINATE in found:
            return Verdict.INDETERMINATE
        if Verdict.UPHELD in found:
            return Verdict.UPHELD
        return Verdict.INAPPLICABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "tree": self.tree, "digest": self.digest, "clean": self.clean,
            "scanned": self.scanned, "coverage": self.coverage,
            "counts": {
                verdict.value: sum(
                    1 for item in self.judgements if item.verdict is verdict
                )
                for verdict in Verdict
            },
            "rules": {
                name: self.verdict_for(name) .value for name in self.by_rule()
            },
            "violations": [item.to_dict() for item in self.violations],
            "indeterminate": [item.to_dict() for item in self.indeterminate],
            "errors": list(self.errors),
        }

    def render(self, *, verbose: bool = False) -> str:
        lines = [
            "",
            f"  audit of {self.tree}",
            f"  {self.scanned} module(s), {len(self.judgements)} judgement(s)",
            "",
        ]

        for name, items in self.by_rule().items():
            verdict = self.verdict_for(name)
            mark = {
                Verdict.UPHELD: "✓", Verdict.VIOLATED: "✕",
                Verdict.INDETERMINATE: "?", Verdict.INAPPLICABLE: "·",
            }[verdict]
            counts = f"{sum(1 for i in items if i.verdict.decided)}/{len(items)}"
            lines.append(f"  {mark} {name:<24} {verdict.value:<14} {counts}")

        if self.violations:
            lines += ["", "  violations:", ""]
            lines += [item.render() for item in self.violations[:40]]

        if self.indeterminate:
            lines += ["", f"  {len(self.indeterminate)} could not be decided:", ""]
            lines += [item.render() for item in self.indeterminate[:20]]

        lines += [
            "",
            f"  coverage {self.coverage:.0%}"
            + (
                f" — {len(self.indeterminate)} undecided, counted against it"
                if self.indeterminate else ""
            ),
            f"  digest   {self.digest}",
            "",
        ]
        if self.errors:
            lines += [f"  {len(self.errors)} file(s) could not be read", ""]
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self.judgements)

    def __iter__(self) -> Iterator[Judgement]:
        return iter(self.judgements)

    def __str__(self) -> str:
        state = "clean" if self.clean else f"{len(self.violations)} violation(s)"
        return f"{state}, coverage {self.coverage:.0%}, {self.digest[:12]}"


def upheld(rule: str, subject: str, detail: str = "", **kwargs: Any) -> Judgement:
    return Judgement(rule=rule, verdict=Verdict.UPHELD, subject=subject,
                     detail=detail, **kwargs)


def violated(rule: str, subject: str, detail: str, **kwargs: Any) -> Judgement:
    return Judgement(rule=rule, verdict=Verdict.VIOLATED, subject=subject,
                     detail=detail, **kwargs)


def indeterminate(rule: str, subject: str, detail: str, **kwargs: Any) -> Judgement:
    return Judgement(rule=rule, verdict=Verdict.INDETERMINATE, subject=subject,
                     detail=detail, **kwargs)


def inapplicable(rule: str, subject: str, detail: str = "", **kwargs: Any) -> Judgement:
    return Judgement(rule=rule, verdict=Verdict.INAPPLICABLE, subject=subject,
                     detail=detail, **kwargs)
