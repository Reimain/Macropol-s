"""Security boundaries — the one part of the manifest that accuses.

Every other declaration says what should exist, and the reconciliation delta
reports where reality disagreed. A `security.boundaries` entry is different: it
says what must *not* happen. `contains: [payments, vault]` is a claim that
whatever sits inside that boundary does not reach out of it undeclared, and an
edge that crosses is a finding by the boundary's mere existence — nobody has to
have written a rule about that specific dependency.

Three shapes of crossing, and they are genuinely different problems:

* **egress** — something inside the boundary depends on something outside it.
  This is the one that matters for cardholder data and PII: it is the path by
  which regulated data leaves the zone it was promised to stay in.
* **ingress** — something outside reaches in. Lower severity by default, because
  reaching into a boundary is usually the intended API of it; it is reported so
  the surface is *visible*, not because every instance is wrong.
* **shared** — one node sits inside two boundaries at once. Not a crossing at
  all, but it means the two zones are not isolated from each other, which is
  invisible if you only look at edges.

**A boundary with no members is reported, not skipped.** A `contains:` list whose
patterns match nothing produces a boundary that can never raise a finding — and
the resulting clean report is the most dangerous output this family can produce,
because it looks exactly like a boundary that is being respected. That case is
raised as its own finding rather than passing silently.

Membership is by the manifest's own `Boundary.holds`, so there is one definition
of "inside" and this module does not invent a second.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ...domain.evidence import Evidence, strongest
from ...domain.finding import Finding, FindingKind, Remediation
from ...domain.lifecycle import Severity
from ..rules import Rule, RuleContext, RuleSet, cite

#: Edge kinds that move data or control out of a zone. A `contains` edge is
#: structural rather than a crossing, so it is not one of these.
CROSSING_KINDS = frozenset({
    "depends_on", "imports", "calls", "reads", "writes", "publishes",
    "subscribes", "connects_to",
})




def _boundaries(context: RuleContext) -> tuple[Any, ...]:
    security = getattr(context.manifest, "security", None)
    return tuple(getattr(security, "boundaries", ()) or ())


def _addresses(node: Any) -> tuple[str, ...]:
    """Every string a boundary's `contains` pattern may match against.

    A boundary contains **elements** — a service, a codebase, a module — written
    in the operator's own vocabulary (`payments`, `vault`). It does not contain
    third-party packages, and the distinction is what makes egress detectable at
    all.

    The trap, and it silently disabled the whole family: the file that *declares*
    a package is not an address of that package. Including evidence URIs here
    meant every dependency named in `services/payments/package.json` was itself
    judged to be inside the `payments` boundary — so no edge ever left it, and a
    boundary with members reported clean forever. That is worse than the empty
    boundary `boundary.empty` exists to catch, because it looks populated.

    So a purl-identified node — something that came from a registry — is never a
    member. Its home is the registry, not the manifest that mentioned it.
    """
    identity = getattr(node, "identity", None)
    if type(identity).__name__ == "Purl":
        return ()

    properties = getattr(node, "properties", {}) or {}
    found = [
        str(getattr(node, "display", "") or ""),
        str(identity or ""),
        str(properties.get("element", "") or ""),
        str(properties.get("path", "") or ""),
        str(properties.get("root", "") or ""),
    ]
    return tuple(item for item in found if item)


def _inside(node: Any, boundary: Any) -> bool:
    return any(boundary.holds(address) for address in _addresses(node))


def _members(context: RuleContext, boundary: Any) -> dict[str, Any]:
    return {
        node.id: node
        for node in context.nodes(live=True)
        if _inside(node, boundary)
    }


def boundary_egress_rule() -> Rule:
    """Undeclared dependencies leaving a declared security boundary."""

    def matches(context: RuleContext) -> bool:
        return context.graph is not None and bool(_boundaries(context))

    def evaluate(context: RuleContext) -> list[Finding]:
        raised: list[Finding] = []
        for boundary in _boundaries(context):
            members = _members(context, boundary)
            if not members:
                continue                          # the empty-boundary rule owns this
            for node_id, node in members.items():
                for edge in context.graph.edges_from(node_id, live=True):
                    if str(getattr(edge.kind, "value", edge.kind)) not in CROSSING_KINDS:
                        continue
                    if edge.dst in members:
                        continue                  # stays inside; not a crossing
                    target = context.graph.node(edge.dst)
                    raised.append(Finding(
                        kind=FindingKind.BOUNDARY_VIOLATION,
                        severity=Severity.HIGH,
                        subject=node_id,
                        title=(
                            f"{node.display} leaves the {boundary.name} boundary"
                        ),
                        detail=(
                            f"{node.display} "
                            f"{str(getattr(edge.kind, 'value', edge.kind))} "
                            f"{getattr(target, 'display', edge.dst)}, which is not "
                            f"declared inside {boundary.name}"
                            + (
                                f". That boundary is classified "
                                f"{boundary.classification}"
                                if boundary.classification else ""
                            )
                        ),
                        code=boundary.name,
                        evidence=cite(edge.evidence, node.evidence),
                        remediation=Remediation(
                            summary=(
                                f"declare the dependency inside {boundary.name}, or "
                                f"remove the crossing"
                            ),
                            action="declare", target=edge.dst, breaking=True,
                        ),
                        rule_id="boundary.egress",
                        related=(edge.dst,),
                        properties={
                            "boundary": boundary.name,
                            "classification": boundary.classification,
                            "direction": "egress",
                        },
                    ))
        return raised

    return Rule(
        id="boundary.egress",
        title="something inside a security boundary depends on something outside it",
        kind=FindingKind.BOUNDARY_VIOLATION,
        severity=Severity.HIGH,
        evaluate=evaluate,
        matches=matches,
        remediation="declare the dependency inside the boundary, or remove it",
        description=(
            "the path by which regulated data leaves the zone it was promised to "
            "stay in; raised by the boundary's existence, not by a per-package rule"
        ),
        tags=("security", "boundary", "compliance"),
    )


def boundary_ingress_rule() -> Rule:
    """Things outside a boundary reaching into it."""

    def matches(context: RuleContext) -> bool:
        return context.graph is not None and bool(_boundaries(context))

    def evaluate(context: RuleContext) -> list[Finding]:
        raised: list[Finding] = []
        for boundary in _boundaries(context):
            members = _members(context, boundary)
            if not members:
                continue
            for node_id, node in members.items():
                for edge in context.graph.edges_to(node_id, live=True):
                    if str(getattr(edge.kind, "value", edge.kind)) not in CROSSING_KINDS:
                        continue
                    if edge.src in members:
                        continue
                    source = context.graph.node(edge.src)
                    raised.append(Finding(
                        kind=FindingKind.BOUNDARY_VIOLATION,
                        # Medium, not high: reaching into a boundary is often the
                        # intended API of it. This is reported so the surface is
                        # visible, not because every instance is a defect.
                        severity=Severity.MEDIUM,
                        subject=node_id,
                        title=(
                            f"{getattr(source, 'display', edge.src)} reaches into "
                            f"the {boundary.name} boundary"
                        ),
                        detail=(
                            f"an element outside {boundary.name} depends on "
                            f"{node.display} inside it. This is the boundary's "
                            f"attack surface; it is not necessarily wrong, and it "
                            f"should be a surface somebody chose"
                        ),
                        code=boundary.name,
                        evidence=cite(edge.evidence, node.evidence),
                        remediation=Remediation(
                            summary=(
                                f"confirm this is an intended entry point into "
                                f"{boundary.name}, and declare it"
                            ),
                            action="declare", target=edge.src,
                        ),
                        rule_id="boundary.ingress",
                        related=(edge.src,),
                        properties={
                            "boundary": boundary.name, "direction": "ingress",
                        },
                    ))
        return raised

    return Rule(
        id="boundary.ingress",
        title="something outside a security boundary reaches into it",
        kind=FindingKind.BOUNDARY_VIOLATION,
        severity=Severity.MEDIUM,
        evaluate=evaluate,
        matches=matches,
        remediation="confirm the entry point is intended, and declare it",
        description="the boundary's attack surface, made visible rather than judged",
        tags=("security", "boundary"),
    )


def shared_membership_rule() -> Rule:
    """One node inside two boundaries at once."""

    def matches(context: RuleContext) -> bool:
        return context.graph is not None and len(_boundaries(context)) > 1

    def evaluate(context: RuleContext) -> list[Finding]:
        boundaries = _boundaries(context)
        raised: list[Finding] = []
        for node in context.nodes(live=True):
            holding = [b.name for b in boundaries if _inside(node, b)]
            if len(holding) < 2:
                continue
            raised.append(Finding(
                kind=FindingKind.BOUNDARY_VIOLATION,
                severity=Severity.HIGH,
                subject=node.id,
                title=f"{node.display} sits inside {' and '.join(holding)}",
                detail=(
                    f"one element is a member of {len(holding)} declared "
                    f"boundaries, so those zones are not isolated from each "
                    f"other. No edge crosses between them because none has to"
                ),
                evidence=cite(node.evidence),
                remediation=Remediation(
                    summary=(
                        "narrow the `contains` patterns so each element belongs to "
                        "one zone, or accept that the zones are joined here"
                    ),
                    action="declare", breaking=True,
                ),
                rule_id="boundary.shared",
                properties={"boundaries": holding},
            ))
        return raised

    return Rule(
        id="boundary.shared",
        title="one element belongs to two security boundaries",
        kind=FindingKind.BOUNDARY_VIOLATION,
        severity=Severity.HIGH,
        evaluate=evaluate,
        matches=matches,
        remediation="narrow the contains patterns, or accept the zones are joined",
        description=(
            "isolation lost without any edge crossing — invisible to a check that "
            "only looks at relationships"
        ),
        tags=("security", "boundary"),
    )


def empty_boundary_rule() -> Rule:
    """A boundary whose patterns match nothing."""

    def matches(context: RuleContext) -> bool:
        return context.graph is not None and bool(_boundaries(context))

    def evaluate(context: RuleContext) -> list[Finding]:
        raised: list[Finding] = []
        for boundary in _boundaries(context):
            if _members(context, boundary):
                continue
            raised.append(Finding(
                kind=FindingKind.POLICY_VIOLATION,
                severity=Severity.HIGH,
                subject=f"boundary:{boundary.name}",
                title=f"the {boundary.name} boundary contains nothing",
                detail=(
                    f"none of {', '.join(boundary.contains) or 'its patterns'} "
                    f"matched any element that was scanned, so this boundary "
                    f"cannot raise a finding however badly it is violated. A "
                    f"boundary reporting nothing looks identical to one being "
                    f"respected, which is why this is reported rather than skipped"
                ),
                code=boundary.name,
                evidence=(_manifest_evidence(context, boundary),),
                remediation=Remediation(
                    summary=(
                        "fix the `contains` patterns, or scan the elements the "
                        "boundary is meant to cover"
                    ),
                    action="declare", target=boundary.name,
                ),
                rule_id="boundary.empty",
                properties={"contains": list(boundary.contains)},
            ))
        return raised

    return Rule(
        id="boundary.empty",
        title="a declared security boundary matches nothing",
        kind=FindingKind.POLICY_VIOLATION,
        severity=Severity.HIGH,
        evaluate=evaluate,
        matches=matches,
        remediation="fix the contains patterns, or scan what the boundary covers",
        description=(
            "the most dangerous clean report this family can produce: a boundary "
            "that cannot fail looks exactly like one that is holding"
        ),
        tags=("security", "boundary", "coverage"),
    )


def _manifest_evidence(context: RuleContext, boundary: Any) -> Evidence:
    """The manifest line that declared this boundary.

    `Finding` refuses to exist without evidence, and that is right — but a
    boundary's evidence is the declaration itself rather than anything in the
    tree, so it is cited as the manifest it came from.
    """
    from ...domain.evidence import EvidenceKind, SourceLocation

    uri = (
        getattr(context.manifest, "source_uri", "")
        or context.source_uri
        or "slpie://manifest"
    )
    return Evidence(
        kind=EvidenceKind.DECLARED,
        location=SourceLocation(uri),
        extractor="governance.boundaries",
        excerpt=f"boundary {boundary.name}: contains {list(boundary.contains)}",
    )


def boundary_rules() -> RuleSet:
    """The boundary family, as a set for registration."""
    return RuleSet(
        (
            boundary_egress_rule(),
            boundary_ingress_rule(),
            shared_membership_rule(),
            empty_boundary_rule(),
        ),
        name="boundaries",
    )
