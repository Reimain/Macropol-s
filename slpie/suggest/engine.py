"""Touch in, ranked paths out. Deterministic candidates, learned order.

This is where the split that makes the whole thing accountable lives:

* **candidates are generated deterministically** from the verb registry's type
  graph and the touch's kind, so a suggestion can never be something the platform
  cannot do, and never a pipeline that would not run;
* **only the order is learned**, from what this environment's reviewers have
  accepted and dismissed.

The worst a mis-learned ranking can therefore do is put a useful thing second
instead of first. It cannot offer something false, and it cannot offer something
that fails when typed — which is what makes it safe to let a non-deterministic
component near a platform whose entire value is being trustworthy.

The other rule: **a stuck reviewer never gets an empty list.** `ERROR`,
`EMPTY_RESULT` and `GAP` touches always yield at least one path, because those are
precisely the moments a conventional help system says nothing and a reviewer
quietly gives up.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Sequence

from ..compose.flow import Kind
from ..compose.pipeline import Composition
from ..compose.registry import VerbRegistry, registry as default_registry
from ..domain.evidence import Evidence
from .gain import Factors, for_touch, rank
from .suggestion import Suggestion, Suggestions, mint_key
from .touch import Touch, TouchKind

#: How many suggestions to offer. A stuck reviewer gets fewer, because a menu is
#: not help when you cannot proceed — one obvious next step is.
WIDTH = 4
STUCK_WIDTH = 2


class SuggestionEngine:
    """Turns a touch into paths worth taking."""

    def __init__(
        self,
        *,
        verbs: VerbRegistry | None = None,
        history: Any = None,
        root: str = ".",
    ) -> None:
        self.verbs = verbs if verbs is not None else default_registry()
        self.history = history
        self.root = root

    # -- the public surface ----------------------------------------------

    def suggest(self, touch: Touch) -> Suggestions:
        candidates = self._candidates(touch)
        candidates = [item for item in candidates if self._runs(item)]

        retired: list[str] = []
        if self.history is not None:
            kept = []
            for item in candidates:
                if self.history.retired(touch, item):
                    retired.append(item.pipeline)
                    continue
                kept.append(item)
            candidates = kept

        ordered = self._rank(touch, candidates)
        width = STUCK_WIDTH if touch.stuck else WIDTH

        return Suggestions(
            touch=touch,
            items=tuple(ordered[:width]),
            reason=self._reason(touch, ordered[:width]),
            retired=tuple(retired),
        )

    # -- deterministic candidate generation ------------------------------

    def _candidates(self, touch: Touch) -> list[Suggestion]:
        builders: dict[TouchKind, Callable[[Touch], list[Suggestion]]] = {
            TouchKind.ERROR: self._for_error,
            TouchKind.EMPTY_RESULT: self._for_empty,
            TouchKind.GAP: self._for_gap,
            TouchKind.FINDING: self._for_finding,
            TouchKind.NODE: self._for_node,
            TouchKind.TERM: self._for_term,
            TouchKind.KIND: self._for_kind,
            TouchKind.VERB: self._for_verb,
        }
        found = builders.get(touch.kind, self._for_term)(touch)

        # The floor. A stuck reviewer with an empty list is the one outcome that
        # must never happen, so a generic-but-real path is always available.
        if not found and touch.stuck:
            found = self._fallback(touch)
        return found

    def _for_error(self, touch: Touch) -> list[Suggestion]:
        """A failed command. The reviewer knows least here, so be concrete."""
        message = str(touch.value("error", touch.subject)).lower()
        found: list[Suggestion] = []

        if "needs an environment" in message:
            found.append(self._make(
                f"discover {self.root} | link | findings", touch,
                because="the verb you ran needs a manifest, and this one does not",
                reveals="the same kind of answer, read straight from the tree",
                cost="moderate",
            ))
        if "consumes" in message or "produces" in message:
            found.append(self._make(
                "", touch, pipeline_override="help compose",
                because="the composition was refused on its types",
                reveals="which verbs may follow which, so the next one type-checks",
            ))
        if "no verb" in message or "did you mean" in message:
            found.append(self._make(
                "", touch, pipeline_override="help",
                because="the verb name was not recognised",
                reveals="every verb this platform has, grouped",
            ))
        if not found:
            found.append(self._make(
                f"discover {self.root} | link | findings", touch,
                because="something failed and the tree is still readable",
                reveals="whether the problem is in your repository or in the command",
                cost="moderate",
            ))
        return found

    def _for_empty(self, touch: Touch) -> list[Suggestion]:
        """Nothing came back. Almost always the filter, or the wrong stage."""
        pipeline = str(touch.value("pipeline", ""))
        found: list[Suggestion] = []

        if "--severity" in pipeline:
            found.append(self._make(
                pipeline.split("--severity")[0].strip().rstrip("|").strip(), touch,
                because="the severity filter removed everything that was found",
                reveals="what was found before the filter narrowed it away",
            ))
        if "filter" in pipeline:
            stripped = " | ".join(
                part.strip() for part in pipeline.split("|")
                if not part.strip().startswith("filter")
            )
            found.append(self._make(
                stripped, touch,
                because="the filter matched nothing that was flowing",
                reveals="what is actually there, so the filter can be aimed",
            ))
        if not found:
            found.append(self._make(
                f"discover {self.root} | link | count", touch,
                because="an empty result usually means nothing was read, not that "
                        "nothing is wrong",
                reveals="how many identities the tree actually yields",
                cost="moderate",
            ))
        return found

    def _for_gap(self, touch: Touch) -> list[Suggestion]:
        return [
            self._make(
                f"discover {self.root} | link | explain", touch,
                because="a gap limits every answer built on it",
                reveals="which files were read and which were not",
                cost="moderate",
            ),
            self._make(
                "", touch, pipeline_override="help compose",
                because="the gap may be closable by composing differently",
                reveals="the recipes, and which of them avoid this gap",
            ),
        ]

    def _for_finding(self, touch: Touch) -> list[Suggestion]:
        """A reviewer opened a finding. They need the evidence and the blast radius."""
        return [
            self._make(
                f"discover {self.root} | link | findings | explain", touch,
                because="a finding is only actionable once you can see its evidence",
                reveals="the file and the line behind this finding",
                cost="moderate",
                evidence=_evidence_of(touch),
            ),
            self._make(
                f"discover {self.root} | link | constraints", touch,
                because="a version finding is often one half of a constraint problem",
                reveals="whether the declared ranges can all hold at once",
                cost="moderate",
            ),
            self._make(
                f"search {touch.subject} | impact", touch,
                because="what else depends on this decides how urgent it is",
                reveals="the blast radius, and how confident each hop is",
                needs_environment=True,
                cost="expensive",
            ),
        ]

    def _for_node(self, touch: Touch) -> list[Suggestion]:
        return [
            self._make(
                f"search {touch.subject} | impact | explain", touch,
                because="the useful question about a node is what depends on it",
                reveals="everything reached from here, with the evidence per hop",
                needs_environment=True, cost="expensive",
            ),
            self._make(
                f"discover {self.root} | link | filter --contains {touch.subject}",
                touch,
                because="you can see this node's own evidence without a graph",
                reveals="every file that mentions it, and what each one said",
                cost="moderate",
            ),
        ]

    def _for_term(self, touch: Touch) -> list[Suggestion]:
        """The dictionary case — and the reason this is not a dictionary.

        A definition would tell the reviewer what the word means. A path tells them
        how to see the thing for themselves in *their* repository, which is what
        actually teaches it.
        """
        word = touch.subject.lower().strip()
        found: list[Suggestion] = []

        verb = self.verbs.get(word)
        if verb is not None:
            found.append(self._make(
                "", touch, pipeline_override=f"help {verb.name}",
                because=f"{verb.name} is a verb, so it has a page",
                reveals=f"what {verb.name} does and what may pipe into it",
            ))
            if verb.examples:
                found.append(self._make(
                    verb.examples[0].replace("discover .", f"discover {self.root}"),
                    touch,
                    because="reading about a verb teaches less than running it once",
                    reveals=f"what {verb.name} actually produces, on your files",
                    cost="moderate",
                ))

        for candidate in _TERM_PATHS:
            if candidate.matches(word):
                found.append(self._make(
                    candidate.pipeline.format(root=self.root), touch,
                    because=candidate.because,
                    reveals=candidate.reveals,
                    cost=candidate.cost,
                ))

        if not found:
            found.append(self._make(
                "", touch, pipeline_override="help compose",
                because=f"{touch.subject!r} is not a verb or a known term here",
                reveals="what this platform can do, and how the pieces compose",
            ))
        return found

    def _for_kind(self, touch: Touch) -> list[Suggestion]:
        """"What is a RESOLUTION?" — answered by producing one."""
        try:
            kind = Kind(touch.subject.lower())
        except ValueError:
            return self._for_term(touch)

        routes = self.verbs.paths_to(kind, max_length=3)
        found: list[Suggestion] = []
        for route in routes[:2]:
            pipeline = " | ".join(route)
            if route and route[0] == "discover":
                pipeline = pipeline.replace("discover", f"discover {self.root}", 1)
            found.append(self._make(
                pipeline, touch,
                because=f"this is the shortest way to produce a {kind.label}",
                reveals=f"a real {kind.label} from your own files, rather than a "
                        f"description of one",
                cost="moderate",
                needs_environment=any(
                    self.verbs.require(name).group == "environment"
                    for name in route
                ),
            ))
        return found

    def _for_verb(self, touch: Touch) -> list[Suggestion]:
        verb = self.verbs.get(touch.subject)
        if verb is None:
            return self._for_term(touch)

        found = [self._make(
            "", touch, pipeline_override=f"help {verb.name}",
            because="every verb has a generated page",
            reveals="its parameters, and what may pipe into and out of it",
        )]
        for example in verb.examples[:2]:
            found.append(self._make(
                example.replace("discover .", f"discover {self.root}"), touch,
                because="the examples are executed by the test suite, so they run",
                reveals=f"what {verb.name} does to your own files",
                cost="moderate",
                needs_environment=any(
                    self.verbs.require(stage.verb).group == "environment"
                    for stage in Composition.read(example, verbs=self.verbs)
                ),
            ))
        return found

    def _fallback(self, touch: Touch) -> list[Suggestion]:
        return [self._make(
            f"discover {self.root} | link | findings", touch,
            because="when nothing else is clear, this reads the tree and reports",
            reveals="what is wrong here, with the evidence for each item",
            cost="moderate",
        )]

    # -- validation ------------------------------------------------------

    def _runs(self, suggestion: Suggestion) -> bool:
        """A suggestion that would not run must never be offered.

        The short key is a promise. Offering a path that fails when typed costs
        more trust than offering nothing, because the reviewer now has to check
        every future suggestion themselves.
        """
        text = suggestion.pipeline.strip()
        if not text:
            return False
        if text.startswith("help"):
            return True      # a help topic, not a composition
        try:
            return Composition.read(text, verbs=self.verbs).validate().ok
        except Exception:  # noqa: BLE001 - a malformed candidate is simply dropped
            return False

    # -- learned ordering ------------------------------------------------

    def _rank(self, touch: Touch, candidates: Sequence[Suggestion]) -> list[Suggestion]:
        scored: list[tuple[float, str, Suggestion]] = []
        for item in candidates:
            weight = 1.0
            if self.history is not None:
                weight = self.history.weight(touch, item)
            # Ties break on the pipeline text so the order is stable across runs.
            # An order that shuffled between invocations would make muscle memory
            # impossible, which is the whole value of the short keys.
            scored.append((-(item.gain * weight), item.pipeline, item))

        scored.sort(key=lambda entry: (entry[0], entry[1]))
        return [item for _score, _text, item in scored]

    def _reason(self, touch: Touch, items: Sequence[Suggestion]) -> str:
        if not items:
            return ""
        parts = ["ranked by expected information gain"]
        if self.history is not None and self.history.informed(touch):
            parts.append("adjusted by what has been accepted here before")
        if touch.stuck:
            parts.append("shortened because you are blocked rather than browsing")
        return "; ".join(parts)

    # -- construction ----------------------------------------------------

    def _make(
        self,
        pipeline: str,
        touch: Touch,
        *,
        because: str,
        reveals: str,
        pipeline_override: str = "",
        cost: str = "cheap",
        needs_environment: bool = False,
        evidence: Sequence[Evidence] = (),
    ) -> Suggestion:
        text = pipeline_override or pipeline
        factors = for_touch(touch, cost=cost)
        return Suggestion(
            key=mint_key(text),
            pipeline=text,
            because=because,
            reveals=reveals,
            evidence=tuple(evidence),
            gain=rank(factors.score(), stuck=touch.stuck),
            confidence=factors.uncertainty,
            cost=cost,
            needs_environment=needs_environment,
        )


# --- the small term table ------------------------------------------------


class _TermPath:
    """A term worth answering with a path rather than a definition."""

    def __init__(self, words: tuple[str, ...], pipeline: str,
                 because: str, reveals: str, cost: str = "moderate") -> None:
        self.words = words
        self.pipeline = pipeline
        self.because = because
        self.reveals = reveals
        self.cost = cost

    def matches(self, word: str) -> bool:
        return any(item in word for item in self.words)


_TERM_PATHS: tuple[_TermPath, ...] = (
    _TermPath(
        ("lockfile", "pin", "pinned"),
        "discover {root} | link | findings",
        because="a pin only means something next to the range that asked for it",
        reveals="where your lockfiles and your manifests disagree, if they do",
    ),
    _TermPath(
        ("purl", "coordinate", "identity"),
        "discover {root} | link",
        because="identity is easier to see than to define",
        reveals="how two spellings of one package became a single node",
    ),
    _TermPath(
        ("evidence", "provenance", "grounded", "cite"),
        "discover {root} | link | explain",
        because="provenance is a chain, and a chain is best shown walked",
        reveals="the file and line behind every claim in the answer",
    ),
    _TermPath(
        ("gap", "confidence", "uncertain"),
        "discover {root} | link | findings | explain",
        because="a gap is only meaningful attached to the answer it limits",
        reveals="what limited this answer, and by how much",
    ),
    _TermPath(
        ("constraint", "conflict", "resolve", "satisfiab"),
        "discover {root} | link | constraints",
        because="a conflict is a concrete pair of demands, not an abstraction",
        reveals="whether your declared ranges can all hold at once",
    ),
    _TermPath(
        ("compose", "pipe", "pipeline", "verb"),
        "discover {root} | link | findings | explain",
        because="composition is learned by seeing provenance survive four stages",
        reveals="how each stage adds to the answer without losing the last one",
    ),
)


def _evidence_of(touch: Touch) -> tuple[Evidence, ...]:
    """Evidence carried on the touch's context, if the surface passed any."""
    found = touch.value("evidence", ())
    return tuple(item for item in found if isinstance(item, Evidence))


def suggest(touch: Touch, **options: Any) -> Suggestions:
    """One call, for the common case."""
    return SuggestionEngine(**options).suggest(touch)
