"""Running the judge — over our tree, or over anybody's.

The audit takes a *tree*, not a configuration, and the checks it runs are
declared per-tree. That is what makes it a product rather than an internal test:
the same judge that asserts SLPIE's own invariants runs against a customer's
repository, or against graphify, with a different set of boundaries named.

`--strict` turns a violation into a non-zero exit, which is what makes it a CI
gate. `--digest` prints one comparable value: unchanged tree, unchanged digest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .judge import Audit, Judgement, Verdict
from .project import Projection, project
from .rules import Rule, by_name, rules


@dataclass(frozen=True, slots=True)
class Check:
    """One rule with the options that aim it at a particular tree."""

    rule: str
    options: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "options": {
            key: value for key, value in self.options.items()
            if isinstance(value, (str, int, float, bool, list, tuple))
        }}


def slpie_checks() -> tuple[Check, ...]:
    """This repository's own invariants, as checks.

    These are the same claims `tests/test_slpie_boundaries.py` asserts. The judge
    does not replace that test — it is cross-checked against it, so if the judge
    ever reports UPHELD for something the test catches, the suite fails and the
    judge is what is broken.
    """
    return (
        Check("readable"),
        Check("statically-visible"),
        Check("single-import", {
            "rule": "slpie→gratimos", "ring": "slpie", "target": "gratimos",
            "allowed": "slpie.artifacts.codegen",
        }),
        Check("single-import", {
            "rule": "gratimos→slpie", "ring": "gratimos", "target": "slpie",
            "allowed": "gratimos.reuse.bridge",
        }),
        Check("purity", {"rule": "kernel-purity", "ring": "slpie"}),
    )


def generic_checks() -> tuple[Check, ...]:
    """What can be judged about a tree nobody has described to us.

    Deliberately modest. Asserting somebody else's boundaries would be inventing
    their architecture and then failing them against it — the honest default is
    the checks that hold for any Python tree, plus whatever the operator names.
    """
    return (Check("readable"), Check("statically-visible"))


class Auditor:
    """Projects a tree, runs the checks, and digests the result."""

    def __init__(self, checks: Sequence[Check] = ()) -> None:
        self.checks = tuple(checks) if checks else generic_checks()
        self._rules = by_name()

    def run(self, root: Path | str, **shared: Any) -> Audit:
        projection = project(root)
        return self.judge(projection, **shared)

    def judge(self, projection: Projection, **shared: Any) -> Audit:
        judgements: list[Judgement] = []
        errors: list[str] = []

        for check in self.checks:
            rule = self._rules.get(check.rule)
            if rule is None:
                errors.append(f"no rule named {check.rule!r}")
                continue
            options = {**shared, **dict(check.options)}
            try:
                judgements.extend(rule.run(projection, **options))
            except Exception as error:  # noqa: BLE001 - a rule may be third-party
                # A rule that raises must not be read as a pass. Its failure is
                # recorded and the audit's coverage suffers, which is the same
                # treatment the firewall gives a broken rule.
                errors.append(f"{check.rule} raised: {type(error).__name__}: {error}")
                judgements.append(Judgement(
                    rule=check.rule, verdict=Verdict.INDETERMINATE,
                    subject=str(projection.root.name),
                    detail=f"the rule failed to evaluate: {error}",
                ))

        return Audit(
            judgements=tuple(judgements),
            tree=str(projection.root),
            scanned=len(projection),
            errors=tuple(errors) + tuple(
                f"{item.name}: {item.error}" for item in projection.unparsed
            ),
        )


def audit(root: Path | str, *, checks: Sequence[Check] = (), **shared: Any) -> Audit:
    """One call. Uses the generic checks unless a tree's own are supplied."""
    return Auditor(checks).run(root, **shared)


def audit_self(root: Path | str = ".", **shared: Any) -> Audit:
    """This repository, against its own stated invariants."""
    return Auditor(slpie_checks()).run(root, **shared)
