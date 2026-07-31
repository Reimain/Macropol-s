"""Licence obligations, judged in a stated distribution context.

The mistake this family exists to avoid is a licence "allowlist". A list of
approved identifiers cannot answer the only question that matters, because the
answer depends on facts the list does not contain: AGPL in an internal tool is
fine, AGPL in a hosted product is a source-disclosure obligation, and GPL
statically linked into a shipped binary is a third thing. `slpie/domain/license.py`
already models that — `check_compatibility` takes a `Distribution` and a
`Linkage` and returns a verdict with the reason written for a human — so this
module supplies context and turns verdicts into findings, and decides nothing
about licences itself.

Three rules, and the third is the one people forget:

* **incompatible** — the obligation does not survive this distribution context.
* **unknown** — the licence could not be read at all. Reported separately and at
  a lower severity, because "we cannot tell" and "this is wrong" are different
  states and merging them means the unknowns get triaged as false positives until
  somebody stops reading the list.
* **undeclared** — the package states no licence. Not the same as unknown: an
  unparseable string is a bug in the metadata, an absent one is a package that
  never said. A report that called both "unknown" would hide which of the two an
  operator can fix by opening an issue upstream.

The distribution context comes from the caller, never from a default that
pretends to know. Where it is not supplied, the rules **decline** rather than
assuming the most permissive reading — an assumption that would turn every AGPL
dependency in a SaaS product into a silent pass.
"""

from __future__ import annotations

from typing import Any, Mapping

from ...domain.evidence import Evidence
from ...domain.finding import Finding, FindingKind, Remediation
from ...domain.license import (
    Distribution,
    Linkage,
    LicenseExpression,
    Obligation,
    check_compatibility,
    parse_expression,
)
from ...domain.lifecycle import Severity
from ..rules import Rule, RuleContext, RuleSet, cite, packages

#: Where a licence is recorded by the discoverers. Checked in order, so a
#: manifest's own `license` field beats anything inferred later.
LICENSE_PROPERTIES = ("license", "licence", "license_expression", "spdx")

#: The fact names a caller sets to state the context. Named here so the manual,
#: the verb's parameters and the rules cannot disagree about their spelling.
DISTRIBUTION_FACT = "distribution"
LINKAGE_FACT = "linkage"
PROJECT_LICENSE_FACT = "project_license"




def _context(context: RuleContext) -> tuple[Distribution, Linkage, str] | None:
    """The stated distribution context, or `None` when it was not stated.

    Returning `None` is what makes the rules decline. Defaulting to
    `INTERNAL_ONLY` would be the permissive reading and would report a clean
    result for the one case — a hosted product carrying AGPL — that this family
    exists to catch.
    """
    declared = context.fact(DISTRIBUTION_FACT)
    if not declared:
        return None
    try:
        distribution = Distribution(str(declared))
    except ValueError:
        return None

    try:
        linkage = Linkage(str(context.fact(LINKAGE_FACT) or Linkage.DYNAMIC.value))
    except ValueError:
        linkage = Linkage.DYNAMIC

    return distribution, linkage, str(context.fact(PROJECT_LICENSE_FACT) or "")


def _declared(node: Any) -> str:
    """Whatever the node says its licence is, as written."""
    properties = getattr(node, "properties", {}) or {}
    for name in LICENSE_PROPERTIES:
        value = properties.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
        # npm's older form is a list of `{"type": …}` objects.
        if isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str) and item.strip():
                    return item.strip()
                if isinstance(item, Mapping) and str(item.get("type", "")).strip():
                    return str(item["type"]).strip()
    return ""


def _read(text: str) -> LicenseExpression | None:
    """The declared text as an expression, or `None` if it cannot be read."""
    from ...normalize.licenses import normalize_license

    reading = normalize_license(text)
    if not reading.expression:
        return None
    try:
        return parse_expression(reading.expression)
    except Exception:  # noqa: BLE001 - an unreadable expression is a finding, not a crash
        return None




def incompatible_license_rule() -> Rule:
    """Licences whose obligations do not survive the stated distribution."""

    def matches(context: RuleContext) -> bool:
        return context.graph is not None and _context(context) is not None

    def evaluate(context: RuleContext) -> list[Finding]:
        stated = _context(context)
        if stated is None:                      # matches() guards this; belt and braces
            return []
        distribution, linkage, project = stated

        raised: list[Finding] = []
        for node in packages(context):
            text = _declared(node)
            expression = _read(text) if text else None
            if expression is None:
                continue                        # the other two rules own these

            verdict = check_compatibility(
                expression,
                project=project or None,
                distribution=distribution,
                linkage=linkage,
            )
            if verdict.compatible:
                continue

            raised.append(Finding(
                kind=FindingKind.LICENSE_INCOMPATIBLE,
                # A network-copyleft obligation in a hosted product is a
                # different order of problem from a weak one, and flattening
                # them to a single severity makes the list unrankable.
                severity=(
                    Severity.CRITICAL
                    if verdict.obligation is Obligation.NETWORK_COPYLEFT
                    else Severity.HIGH
                ),
                subject=node.id,
                title=f"{node.display} is licensed {expression.to_string()}",
                detail=verdict.reason,
                code=expression.to_string(),
                evidence=cite(node.evidence),
                remediation=Remediation(
                    summary=(
                        verdict.remediation
                        or "replace the dependency, or change how this is distributed"
                    ),
                    action="replace",
                    target=node.name,
                    breaking=True,
                ),
                rule_id="license.incompatible",
                properties={
                    "license": expression.to_string(),
                    "obligation": verdict.obligation.value,
                    "distribution": distribution.value,
                    "linkage": linkage.value,
                },
            ))
        return raised

    return Rule(
        id="license.incompatible",
        title="a dependency's licence conflicts with how this is distributed",
        kind=FindingKind.LICENSE_INCOMPATIBLE,
        severity=Severity.HIGH,
        evaluate=evaluate,
        matches=matches,
        remediation="replace the dependency, or change the distribution context",
        description=(
            "evaluates each declared licence against the stated distribution and "
            "linkage; declines entirely when no context was supplied, because the "
            "permissive default is the one that hides real obligations"
        ),
        tags=("security", "license", "compliance"),
    )


def unknown_license_rule() -> Rule:
    """Licences that were stated and could not be read."""

    def matches(context: RuleContext) -> bool:
        return context.graph is not None

    def evaluate(context: RuleContext) -> list[Finding]:
        raised: list[Finding] = []
        for node in packages(context):
            text = _declared(node)
            if not text or _read(text) is not None:
                continue
            raised.append(Finding(
                kind=FindingKind.LICENSE_UNKNOWN,
                severity=Severity.MEDIUM,
                subject=node.id,
                title=f"{node.display} states a licence that cannot be read",
                detail=(
                    f"the metadata says {text!r}, which is not a recognised SPDX "
                    f"expression. Nothing can be concluded about its obligations "
                    f"— this is not the same as concluding that they are benign"
                ),
                code=text[:64],
                evidence=cite(node.evidence),
                remediation=Remediation(
                    summary=(
                        f"record {node.name}'s licence as an SPDX expression, or "
                        f"confirm it upstream"
                    ),
                    action="declare", target=node.name,
                ),
                rule_id="license.unknown",
                properties={"declared": text},
            ))
        return raised

    return Rule(
        id="license.unknown",
        title="a dependency's licence could not be read",
        kind=FindingKind.LICENSE_UNKNOWN,
        severity=Severity.MEDIUM,
        evaluate=evaluate,
        matches=matches,
        remediation="record the licence as an SPDX expression",
        description=(
            "separate from `license.undeclared`: an unreadable string is a defect "
            "in the metadata, an absent one is a package that never said"
        ),
        tags=("security", "license", "compliance"),
    )


def undeclared_license_rule() -> Rule:
    """Packages that state no licence at all."""

    def matches(context: RuleContext) -> bool:
        return context.graph is not None

    def evaluate(context: RuleContext) -> list[Finding]:
        raised: list[Finding] = []
        for node in packages(context):
            if _declared(node):
                continue
            raised.append(Finding(
                kind=FindingKind.LICENSE_UNKNOWN,
                severity=Severity.LOW,
                subject=node.id,
                title=f"{node.display} declares no licence",
                detail=(
                    "no licence was found in any manifest that mentions this "
                    "package. Absent is not permissive: code with no stated "
                    "licence is, by default, not licensed for use at all"
                ),
                evidence=cite(node.evidence),
                remediation=Remediation(
                    summary=f"establish what {node.name} is licensed under",
                    action="declare", target=node.name,
                ),
                rule_id="license.undeclared",
            ))
        return raised

    return Rule(
        id="license.undeclared",
        title="a dependency declares no licence",
        kind=FindingKind.LICENSE_UNKNOWN,
        severity=Severity.LOW,
        evaluate=evaluate,
        matches=matches,
        remediation="establish and record what the dependency is licensed under",
        description=(
            "absent is not permissive — unlicensed code carries no grant, which "
            "is a stricter position than any licence would impose"
        ),
        tags=("security", "license", "compliance"),
    )


def license_rules() -> RuleSet:
    """The licence family, as a set for registration."""
    return RuleSet(
        (
            incompatible_license_rule(),
            unknown_license_rule(),
            undeclared_license_rule(),
        ),
        name="licenses",
    )
