"""What a changed file costs, computed rather than guessed.

Given a set of changed uris, this answers the only question an incremental
rescan needs: *what stops being true?* The chain is four steps, and every one of
them is a lookup the platform already pays for:

    changed uri  →  evidence drawn from it   (the `evidence(uri)` index)
                 →  nodes and edges resting on that evidence
                 →  those whose evidence is now *entirely* stale
                 →  enrichments derived from any of them, transitively

The third step is where correctness lives, and it is the one an obvious
implementation gets wrong. A node cited by three files does not stop being true
because one of them changed — it keeps its other two pieces of evidence and its
confidence is simply recomputed lower. Retiring it would delete a node the graph
still has grounds for, and the rescan would then re-assert it from the two
unchanged files, producing a retire/assert churn on every scan that touches
anything nearby.

So `Invalidation.retire` holds only the subjects whose evidence is *wholly*
stale, and `weakened` holds the ones that survive with less. Both are reported,
because they are different instructions: one is "remove this", the other is
"recompute what it is worth".

The fourth step is transitive by necessity. An enrichment derived from a retired
enrichment cannot stand either, and stopping at one level would leave conclusions
in the graph whose `derived_from` walk terminates in nothing — which is precisely
the dangling chain invariant 3 exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class Invalidation:
    """Everything a set of changed files stops justifying."""

    stale_evidence: tuple[str, ...] = ()
    retire_nodes: tuple[str, ...] = ()
    retire_edges: tuple[str, ...] = ()
    weakened: tuple[str, ...] = ()
    retire_enrichments: tuple[str, ...] = ()
    rescan: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not (
            self.stale_evidence or self.retire_nodes or self.retire_edges
            or self.weakened or self.retire_enrichments
        )

    @property
    def retired(self) -> int:
        return len(self.retire_nodes) + len(self.retire_edges)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stale_evidence": len(self.stale_evidence),
            "retire_nodes": list(self.retire_nodes),
            "retire_edges": list(self.retire_edges),
            "weakened": list(self.weakened),
            "retire_enrichments": list(self.retire_enrichments),
            "rescan": list(self.rescan),
        }

    def __str__(self) -> str:
        if self.empty:
            return "nothing was invalidated"
        return (
            f"{len(self.stale_evidence)} evidence stale, {self.retired} subject(s) "
            f"retired, {len(self.weakened)} weakened, "
            f"{len(self.retire_enrichments)} enrichment(s) recomputed"
        )


def evidence_for_uris(graph: Any, uris: Iterable[str]) -> tuple[str, ...]:
    """Evidence ids drawn from any of these files.

    Uses the `evidence(uri)` index the schema was given for exactly this — one
    indexed lookup per changed file, rather than a scan of every piece of
    evidence in the graph.
    """
    found: set[str] = set()
    for uri in uris:
        found.update(graph.evidence_by_uri(uri))
    return tuple(sorted(found))


def invalidate(
    graph: Any,
    changed: Iterable[str],
    *,
    removed: Iterable[str] = (),
    enrichments: Mapping[str, Any] | None = None,
) -> Invalidation:
    """What must be retired, weakened and rescanned after these files moved."""
    stale_uris = tuple(dict.fromkeys([*changed, *removed]))
    stale = evidence_for_uris(graph, stale_uris)
    if not stale:
        return Invalidation(rescan=tuple(sorted(set(changed))))

    subjects = graph.subjects_of_evidence(stale)
    stale_set = set(stale)

    retire_nodes: list[str] = []
    retire_edges: list[str] = []
    weakened: list[str] = []

    for what, subject in sorted(set(subjects)):
        supporting = _supporting_evidence(graph, what, subject)
        if not supporting:
            continue
        if supporting <= stale_set:
            # Every piece of evidence for this subject came from a file that
            # moved. Nothing is left to justify it.
            (retire_nodes if what == "node" else retire_edges).append(subject)
        else:
            # It keeps evidence from files that did not move. Its confidence is
            # now different and it is *not* retired — deleting a node the graph
            # still has grounds for would churn it out and straight back in.
            weakened.append(subject)

    return Invalidation(
        stale_evidence=stale,
        retire_nodes=tuple(retire_nodes),
        retire_edges=tuple(retire_edges),
        weakened=tuple(sorted(weakened)),
        retire_enrichments=_dependent_enrichments(
            enrichments or {}, {*stale, *retire_nodes, *retire_edges},
        ),
        rescan=tuple(sorted(set(changed))),
    )


def _supporting_evidence(graph: Any, what: str, subject: str) -> set[str]:
    """Every evidence id behind one node or edge.

    Both carry their own evidence, so this reads it off the object rather than
    querying the join table again — one lookup instead of two, and it cannot
    disagree with what the graph would hand any other caller.
    """
    found = graph.node(subject) if what == "node" else graph.edge(subject)
    return {item.id for item in found.evidence} if found is not None else set()


def _dependent_enrichments(
    enrichments: Mapping[str, Any], gone: set[str],
) -> tuple[str, ...]:
    """Enrichments that cannot stand once `gone` is gone. Transitive.

    Iterated to a fixed point rather than walked once: an enrichment derived
    from an enrichment derived from retired evidence is just as unfounded as the
    first, and leaving it would produce a `derived_from` chain that terminates in
    nothing — the dangling chain the append-only rule exists to prevent.
    """
    doomed: set[str] = set()
    frontier = set(gone)

    while frontier:
        wave = {
            identifier
            for identifier, enrichment in enrichments.items()
            if identifier not in doomed
            and frontier & set(getattr(enrichment, "derived_from", ()))
        }
        if not wave:
            break
        doomed |= wave
        frontier = wave

    return tuple(sorted(doomed))
