"""The reference audit — a deterministic, AST-queryable graph knowledge judge.

``gratimos/reference/`` *records* what this system claims to know, with a mandatory
source per entry. That is a registry. It is not a judge, and the difference is
where the value is: a registry states claims, a judge decides whether the code
honours them — deterministically, reproducibly, and with a file and a line for
every verdict.

This is graphify's shape taken literally: **the graph is the judge.** An AST is
projected into a queryable structure, and deterministic queries over it return
verdicts. No linter opinions, no model asked to review. Same tree, same digest,
every time — which is what lets CI pin "the architecture has not drifted" to one
comparable value, the same trick the snapshot ``root_digest`` uses for content.

:class:`~slpie.audit.judge.Verdict` has four members and the fourth is the
important one. ``INDETERMINATE`` means the rule applied and could not be decided:
a file that would not parse, a dynamic import, a ``getattr`` target. A judge that
silently returned ``UPHELD`` for what it could not examine would be worse than no
judge, because the green result is now a lie. Indeterminacy counts against
coverage, and coverage is reported rather than buried.

The invariants were previously asserted only inside ``tests/``, which is the right
mechanism in the wrong place — a rule that exists only in a test suite is testable
by us and not queryable by anybody else. ``tests/test_slpie_boundaries.py`` is not
weakened; the judge is cross-checked *against* it, so a judge that went blind fails
the suite rather than quietly passing.
"""

from .engine import Auditor, Check, audit, audit_self, generic_checks, slpie_checks
from .judge import (
    Audit,
    Judgement,
    Verdict,
    inapplicable,
    indeterminate,
    upheld,
    violated,
)
from .project import Definition, Import, Module, Projection, project
from .rules import STDLIB, Rule, by_name, rules

__all__ = [
    "STDLIB",
    "Audit",
    "Auditor",
    "Check",
    "Definition",
    "Import",
    "Judgement",
    "Module",
    "Projection",
    "Rule",
    "Verdict",
    "audit",
    "audit_self",
    "by_name",
    "generic_checks",
    "inapplicable",
    "indeterminate",
    "project",
    "rules",
    "slpie_checks",
    "upheld",
    "violated",
]
