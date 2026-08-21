"""Reconciliation — the delta between what was declared and what was found.

This is where declare-first earns its keep. A platform that only reported what
it discovered would silently omit the two most useful facts an architect can be
told: something you documented does not exist, and something exists that nobody
documented.

Four deltas, and each means something different:

======================================  ==========================================
Declared, never observed                a dead entry, or access we were refused
Observed, never declared                a shadow dependency or undocumented egress
Declared attribute contradicts reality  the documentation is wrong, not stale
Undeclared egress from a boundary       a security finding, high severity
======================================  ==========================================

The third is the one that matters most and is easiest to get wrong. A declared
REST endpoint observed speaking gRPC is not "lower confidence" — it is a
contradiction, and it surfaces as a finding rather than as a number that drifts
down until nobody notices it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..domain.evidence import Evidence, EvidenceKind
from ..domain.finding import Finding, FindingKind, Remediation
from ..domain.lifecycle import Severity
from ..domain.node import Node
from .manifest import Declaration, Manifest

#: Attributes worth contradicting on. Comparing everything would drown the
#: signal in cosmetic disagreements about, say, a team name.
CHECKED_ATTRIBUTES = ("kind", "protocol", "framework", "classification", "language")


@dataclass(frozen=True, slots=True)
class Reconciliation:
    """The full picture: what matched, what did not, and in which direction."""

    corroborated: tuple[str, ...] = ()
    declared_not_found: tuple[str, ...] = ()
    undeclared: tuple[str, ...] = ()
    contradictions: tuple[tuple[str, str, Any, Any], ...] = ()   # node, attr, declared, observed
    boundary_violations: tuple[tuple[str, str, str], ...] = ()   # boundary, inside, outside

    @property
    def clean(self) -> bool:
        return not (
            self.declared_not_found or self.undeclared
            or self.contradictions or self.boundary_violations
        )

    @property
    def coverage(self) -> float:
        """The fraction of declarations discovery actually met.

        Reported rather than assumed to be 1.0. An answer drawn from an
        environment only two thirds of which could be seen is not a confident
        answer about that environment.
        """
        declared = len(self.corroborated) + len(self.declared_not_found)
        return round(len(self.corroborated) / declared, 4) if declared else 1.0

    def summary(self) -> str:
        if self.clean:
            return "declaration and reality agree"
        parts = []
        if self.declared_not_found:
            parts.append(f"{len(self.declared_not_found)} declared but not found")
        if self.undeclared:
            parts.append(f"{len(self.undeclared)} found but not declared")
        if self.contradictions:
            parts.append(f"{len(self.contradictions)} contradicted")
        if self.boundary_violations:
            parts.append(f"{len(self.boundary_violations)} boundary violations")
        return ", ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "clean": self.clean,
            "coverage": self.coverage,
            "summary": self.summary(),
            "corroborated": list(self.corroborated),
            "declared_not_found": list(self.declared_not_found),
            "undeclared": list(self.undeclared),
            "contradictions": [
                {"subject": s, "attribute": a, "declared": d, "observed": o}
                for s, a, d, o in self.contradictions
            ],
            "boundary_violations": [
                {"boundary": b, "inside": i, "outside": o}
                for b, i, o in self.boundary_violations
            ],
        }


def reconcile(
    manifest: Manifest,
    graph: Any,
    *,
    ignore_undeclared_kinds: Iterable[str] = ("package", "module"),
) -> Reconciliation:
    """Compare the manifest against the graph discovery has built.

    ``ignore_undeclared_kinds`` exists because nobody declares their transitive
    npm tree, and reporting forty thousand undeclared packages would bury the
    one undeclared *service* that actually matters. Packages are discovered, not
    declared; services, datasets and providers are the other way round.
    """
    ignored = frozenset(ignore_undeclared_kinds)

    corroborated: list[str] = []
    missing: list[str] = []
    contradictions: list[tuple[str, str, Any, Any]] = []

    declared_ids: set[str] = set()
    for declaration in manifest.declarations:
        node_id = _declared_node_id(declaration)
        declared_ids.add(node_id)
        node = graph.node(node_id)
        # Qualified by section: a codebase and a dataset may both be called
        # `orders`, and they are two different elements with two different
        # fates. Reporting both as "orders" would make the delta unreadable.
        label = str(declaration)

        if node is None or node.declared_only:
            missing.append(label)
            continue

        corroborated.append(label)
        contradictions.extend(_contradictions(declaration, node))

    undeclared = [
        node.id for node in graph.nodes(live=True)
        if node.id not in declared_ids
        and node.kind.value not in ignored
        and not _is_boundary(node)
    ]

    return Reconciliation(
        corroborated=tuple(sorted(corroborated)),
        declared_not_found=tuple(sorted(missing)),
        undeclared=tuple(sorted(undeclared)),
        contradictions=tuple(contradictions),
        boundary_violations=_boundary_violations(manifest, graph),
    )


def _declared_node_id(declaration: Declaration) -> str:
    from ..domain.identity import node_id

    return node_id(declaration.urn)


def _is_boundary(node: Node) -> bool:
    return node.identity.to_string().startswith("urn:slpie:boundary:")


def _contradictions(
    declaration: Declaration, node: Node
) -> list[tuple[str, str, Any, Any]]:
    """Where the declaration and the observation disagree about a fact.

    Only compares attributes both sides actually stated. An observation that is
    silent about `protocol` is not disagreeing with the declaration; it simply
    has nothing to say, and treating silence as contradiction would make every
    partial discoverer look like a liar.
    """
    found: list[tuple[str, str, Any, Any]] = []
    for attribute in CHECKED_ATTRIBUTES:
        declared = declaration.attributes.get(attribute)
        observed = node.properties.get(f"observed_{attribute}")
        if declared is None or observed is None:
            continue
        if str(declared).lower() != str(observed).lower():
            found.append((node.id, attribute, declared, observed))
    return found


def _boundary_violations(manifest: Manifest, graph: Any) -> tuple[tuple[str, str, str], ...]:
    """Edges that leave a declared boundary without a matching declaration.

    A boundary says "these things belong together". An edge from inside it to
    something outside that the manifest never mentioned is exactly the
    undocumented egress the security section exists to catch.
    """
    if not manifest.security.boundaries:
        return ()

    declared_names = {d.name for d in manifest.declarations}
    violations: list[tuple[str, str, str]] = []

    for boundary in manifest.security.boundaries:
        inside = {
            node.id: node for node in graph.nodes(live=True)
            if boundary.holds(_display_name(node))
        }
        for node_id, node in inside.items():
            for edge in graph.edges_from(node_id):
                if edge.dst in inside:
                    continue
                target = graph.node(edge.dst)
                if target is None:
                    continue
                name = _display_name(target)
                if name in declared_names or target.kind.value in ("package", "module"):
                    continue
                violations.append((boundary.name, _display_name(node), name))
    return tuple(sorted(set(violations)))


def _display_name(node: Node) -> str:
    return node.name or node.display


def findings(
    reconciliation: Reconciliation,
    manifest: Manifest,
    *,
    source_uri: str = "",
) -> list[Finding]:
    """Turn a reconciliation into findings, each carrying its own evidence.

    Reconciliation findings cite the *manifest* — the line that made the claim —
    because that is where a reader has to go to resolve them.
    """
    uri = source_uri or manifest.source_uri or "slpie://manifest"
    raised: list[Finding] = []

    for name in reconciliation.declared_not_found:
        declaration = _find(manifest, name)
        evidence = (
            declaration.evidence(source_uri=uri) if declaration
            else _manifest_evidence(uri, f"declared: {name}")
        )
        raised.append(Finding(
            kind=FindingKind.DECLARED_NOT_FOUND,
            severity=Severity.MEDIUM,
            subject=name,
            title=f"{name} is declared but was never observed",
            detail=(
                "The manifest declares this element, and no discoverer produced "
                "independent evidence for it. Either it no longer exists, or the "
                "platform was not granted the access needed to see it."
            ),
            evidence=(evidence,),
            rule_id="reconcile.declared-not-found",
            remediation=Remediation(
                summary=f"attach {name}, grant the capability it needs, or remove the declaration",
                action="attach", target=name,
            ),
        ))

    for node_id in reconciliation.undeclared:
        raised.append(Finding(
            kind=FindingKind.UNDECLARED_ELEMENT,
            severity=Severity.LOW,
            subject=node_id,
            title="an element was observed that the manifest does not declare",
            detail=(
                "Discovery found this in the environment and the manifest does not "
                "mention it. Undocumented elements are how a shadow dependency "
                "survives review."
            ),
            evidence=(_manifest_evidence(uri, f"not declared: {node_id}"),),
            rule_id="reconcile.undeclared-element",
            remediation=Remediation(
                summary="declare it in the manifest, or remove it from the environment",
                action="declare", target=node_id,
            ),
        ))

    for subject, attribute, declared, observed in reconciliation.contradictions:
        raised.append(Finding(
            kind=FindingKind.CONTRADICTED,
            severity=Severity.HIGH,
            subject=subject,
            title=f"declared {attribute} is {declared!r} but {observed!r} was observed",
            detail=(
                "This is not uncertainty about the value — it is a documented fact "
                "that reality disagrees with. Anything built on the declared value "
                "is built on something untrue."
            ),
            evidence=(_manifest_evidence(uri, f"{attribute}: {declared}"),),
            rule_id="reconcile.contradicted",
            properties={"attribute": attribute, "declared": declared, "observed": observed},
            remediation=Remediation(
                summary=f"correct the manifest to {observed!r}, or fix the element to match",
                action="declare", target=subject,
            ),
        ))

    for boundary, inside, outside in reconciliation.boundary_violations:
        raised.append(Finding(
            kind=FindingKind.BOUNDARY_VIOLATION,
            severity=Severity.CRITICAL,
            subject=inside,
            title=f"{inside} reaches {outside}, outside the {boundary} boundary",
            detail=(
                f"The {boundary} boundary declares what belongs together. This edge "
                f"leaves it to something the manifest never declared — undocumented "
                f"egress from a protected set."
            ),
            evidence=(_manifest_evidence(uri, f"boundary: {boundary}"),),
            rule_id="reconcile.boundary-violation",
            related=(outside,),
            properties={"boundary": boundary, "outside": outside},
            remediation=Remediation(
                summary=f"remove the dependency, or declare {outside} inside {boundary}",
                action="declare", target=outside,
            ),
        ))

    return raised


def _find(manifest: Manifest, label: str) -> Declaration | None:
    """Look up a declaration by its `section:name` label, or by bare name."""
    for declaration in manifest.declarations:
        if str(declaration) == label:
            return declaration
    return manifest.named(label)


def _manifest_evidence(uri: str, excerpt: str) -> Evidence:
    from ..domain.evidence import SourceLocation

    return Evidence(
        kind=EvidenceKind.DECLARED,
        location=SourceLocation(uri),
        extractor="environment.reconcile",
        extractor_version="1",
        excerpt=excerpt,
    )
