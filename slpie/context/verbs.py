"""The index, as a verb — so the product's own metadata composes.

`context` produces a REPORT, which means the map of the platform pipes into
`filter`, `count`, `sort` and `json` like every other conclusion. That is not a
convenience: it is the same argument §25 makes for putting the judge on the
graph rather than in a standalone report. `context --query verb: | count` is one
question, answered by machinery that already exists, rather than a second
reporting surface somebody has to learn.

The verb is deliberately read-only and takes no path outside the repository it
ships in. Pointing this at a customer's tree is the audit's job (§25), which
already has the rules, the verdicts and the digest for saying something about
somebody else's architecture. This one describes *ours*.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..compose.flow import Flow, Kind
from ..compose.verb import Context, Param, Verb
from ..domain.reasoning import ReasoningStep
from .facet import FacetKind
from .index import build

GROUP = "context"


def _context(flow: Flow, arguments: Mapping[str, Any], _context: Context) -> Flow:
    """Build the index and answer the question asked of it."""
    index = build()
    query = str(arguments.get("query") or "").strip()
    depth = int(arguments.get("depth") or 1)

    if query:
        hit = index.get(query)
        if hit is not None:
            found = (hit, *index.connected(query, depth=depth))
            claim = (
                f"{hit.id} and {len(found) - 1} facet(s) connected to it "
                f"within {depth} hop(s)"
            )
        else:
            found = index.search(query)
            claim = f"{len(found)} facet(s) matching {query!r}"
    else:
        found = index.facets
        counts = index.to_dict()["counts"]
        claim = (
            f"{len(found)} facets across {len(counts)} kinds, "
            f"{index.coverage:.1%} anchored to a file and line, "
            f"{len(index.dangling)} link(s) pointing at nothing"
        )

    return flow.then(
        Kind.REPORT, tuple(item.to_dict() for item in found), stage="context",
        steps=[ReasoningStep(
            claim=claim, layer="context", operation="project",
        )],
        facts={
            "digest": index.digest,
            "coverage": index.coverage,
            "counts": index.to_dict()["counts"],
            "unanchored": [item.id for item in index.unanchored],
            "dangling": len(index.dangling),
        },
    )


def verbs() -> tuple[Verb, ...]:
    return (
        Verb(
            name="context", group=GROUP, produces=Kind.REPORT,
            summary="the product's own map: what exists, and what connects it",
            detail=(
                "Every facet is read from a source that already exists — verbs "
                "from the registry, routes from the server, screens from the "
                "contract, modules and imports from the AST projection, plan "
                "sections from the §NN references modules write in their own "
                "docstrings. Nothing is restated here, so the index is wrong "
                "only if the code is.\n\n"
                "With no query it summarises. With one it answers *what is "
                "connected to this* — both directions, because 'what does this "
                "screen read' and 'who reads this route' are the same question "
                "asked from opposite ends."
            ),
            params=(
                Param("query", "str",
                      "a facet id like `verb:findings`, or text to search for"),
                Param("depth", "int", "how many hops to follow", default=1),
            ),
            examples=(
                "context",
                "context --query verb:findings",
                "context | count",
            ),
            run=_context,
        ),
    )


#: The facet kinds, re-exported so a caller can name one without reaching past
#: this module into the package's internals.
KINDS = tuple(kind.value for kind in FacetKind)
