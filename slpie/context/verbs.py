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
from ..domain.finding import Gap, GapKind
from ..domain.reasoning import ReasoningStep
from .facet import FacetKind
from .index import build
from .lexicon import LexiconError, default
from .profile import load_profiles, resolve

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


def _lexicon(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """The words a context uses, and where each one came from."""
    profile = str(arguments.get("profile") or "").strip()
    root = str(getattr(context, "root", "") or ".")

    gaps: list[Gap] = []
    try:
        words = resolve({"profile": profile} if profile else {}, root=root)
    except LexiconError as failure:
        # An authored profile that names a term nobody defines is a mistake
        # worth reporting, not worth failing on: the platform's own words are a
        # correct console, and the gap says exactly what was ignored.
        words = default()
        gaps.append(Gap(
            kind=GapKind.NOT_DECLARED, subject=f"lexicon:{profile}",
            detail=str(failure),
            remediation="fix the profile under .slpie/lexicon/, then re-run",
        ))

    loaded = load_profiles(root)
    for message in loaded.errors:
        gaps.append(Gap(
            kind=GapKind.PARSE_FAILURE, subject="lexicon",
            detail=message,
            remediation="a malformed profile costs that profile, not the console",
        ))

    return flow.then(
        Kind.REPORT, tuple(term.to_dict() for term in words), stage="lexicon",
        steps=[ReasoningStep(
            claim=(
                f"{len(words)} term(s) under profile {words.name!r}: "
                f"{len(words.renameable)} a context may rename, "
                f"{len(words.protected)} carrying a decision and therefore fixed"
            ),
            layer="context", operation="resolve",
        )],
        gaps=gaps,
        facts={
            "profile": words.name,
            "digest": words.digest,
            "available": [item.name for item in loaded.profiles],
            "protected": [term.key for term in words.protected],
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
        Verb(
            name="lexicon", group=GROUP, produces=Kind.REPORT,
            summary="the words this context uses for the platform's nouns",
            detail=(
                "The default is derived from the code — `slpie/domain/*.py` and "
                "the package names under `slpie/`, each glossed by its own "
                "docstring — so the platform cannot offer a word it does not "
                "use.\n\n"
                "A profile under `.slpie/lexicon/` may rename the product. It "
                "may never rename a control: every severity, gap kind, verdict "
                "and target state is protected, derived from the enums "
                "themselves, because a tenant renaming `refused` to `pending` "
                "is how a control becomes invisible."
            ),
            params=(
                Param("profile", "str",
                      "a profile under .slpie/lexicon/; omit for the default"),
            ),
            examples=(
                "lexicon",
                "lexicon --profile platform-engineering",
                "lexicon | count",
            ),
            run=_lexicon,
        ),
    )


#: The facet kinds, re-exported so a caller can name one without reaching past
#: this module into the package's internals.
KINDS = tuple(kind.value for kind in FacetKind)
