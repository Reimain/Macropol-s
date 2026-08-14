"""The one registry. Every surface is a projection of it.

This is the hub of §24. A verb registered here becomes a CLI subcommand, an HTTP
route, a manual page, a planner vocabulary entry and a chip in the UI palette,
because each of those reads *this* rather than restating it. `tests/
test_slpie_compose.py` asserts the projection is total, so registering a verb
without wiring it is a test failure rather than a drift nobody notices for two
releases.

The interesting methods are the graph ones — `successors`, `predecessors`,
`sources`, `paths_to`. They exist because typing the pipe turns the verb set into
a directed graph over kinds, and three separate features fall out of querying it:

* the manual's "what can follow this" section, generated rather than maintained;
* the offline planner's search space, which is what lets it compose without
  guessing;
* the UI's live type checking as somebody drags verbs into a pipeline.

Built-ins register through the identical path a third-party plugin uses
(invariant 6). `ExtensionPoint.VERB` is that path, and `register_provider` is
what a plugin calls — the seam is proven by its own use rather than asserted.
"""

from __future__ import annotations

from typing import Any, Iterable, Iterator, Mapping, Sequence

from .flow import Kind
from .verb import Verb, VerbError, VerbProvider


class VerbRegistry:
    """Every capability the platform exposes, and how they connect."""

    def __init__(self, verbs: Iterable[Verb] = ()) -> None:
        self._verbs: dict[str, Verb] = {}
        self._origin: dict[str, str] = {}
        for verb in verbs:
            self.add(verb)

    # -- registration ----------------------------------------------------

    def add(self, verb: Verb, *, origin: str = "builtin") -> Verb:
        """Register one verb. A duplicate name is refused, never shadowed.

        Silent shadowing would let a third-party plugin replace `target` or
        `deploy apply` with its own implementation and inherit the confirmation
        the operator gave to ours. Overriding a verb has to be a deliberate,
        named act, so it is not available by accident.
        """
        existing = self._verbs.get(verb.name)
        if existing is not None:
            raise VerbError(
                f"{verb.name!r} is already registered by {self._origin[verb.name]}; "
                f"a second verb with that name would shadow the first, and a "
                f"shadowed verb inherits confirmations it was never granted"
            )
        self._verbs[verb.name] = verb
        self._origin[verb.name] = origin
        return verb

    def register_provider(self, provider: VerbProvider, *, origin: str = "plugin") -> int:
        """Every verb a plugin offers. The path built-ins take too."""
        added = 0
        for verb in provider.verbs():
            self.add(verb, origin=origin)
            added += 1
        return added

    def replace(self, verb: Verb, *, origin: str = "override") -> Verb:
        """Deliberately override a registered verb. Recorded as an override."""
        self._verbs[verb.name] = verb
        self._origin[verb.name] = origin
        return verb

    # -- lookup ----------------------------------------------------------

    def get(self, name: str) -> Verb | None:
        return self._verbs.get(name)

    def require(self, name: str) -> Verb:
        """The verb, or a refusal that suggests what was probably meant."""
        found = self._verbs.get(name)
        if found is not None:
            return found
        raise VerbError(
            f"there is no verb {name!r}"
            + (f"; did you mean {', '.join(self.nearest(name))}?"
               if self.nearest(name) else "")
            + " — run `slpie help` for every verb"
        )

    def nearest(self, name: str, *, limit: int = 3) -> tuple[str, ...]:
        """Candidates for a typo. Prefix and substring only — no edit distance.

        Edit distance would confidently suggest `attach` for `detach`, which are
        opposites. A shared prefix is a weaker signal and a much safer one.
        """
        lowered = name.lower()
        scored: list[tuple[int, str]] = []
        for candidate in self._verbs:
            if candidate.startswith(lowered) or lowered.startswith(candidate):
                scored.append((0, candidate))
            elif lowered in candidate or candidate in lowered:
                scored.append((1, candidate))
        scored.sort()
        return tuple(name for _, name in scored[:limit])

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._verbs))

    @property
    def verbs(self) -> tuple[Verb, ...]:
        return tuple(self._verbs[name] for name in self.names)

    def origin_of(self, name: str) -> str:
        return self._origin.get(name, "")

    def groups(self) -> dict[str, tuple[Verb, ...]]:
        """Verbs by group, for a `--help` that is readable rather than a wall."""
        found: dict[str, list[Verb]] = {}
        for verb in self.verbs:
            found.setdefault(verb.group, []).append(verb)
        return {
            group: tuple(sorted(items, key=lambda v: v.name))
            for group, items in sorted(found.items())
        }

    def mutating(self) -> tuple[Verb, ...]:
        return tuple(verb for verb in self.verbs if verb.mutates)

    # -- the type graph --------------------------------------------------

    def sources(self) -> tuple[Verb, ...]:
        """Verbs that can start a pipeline."""
        return tuple(verb for verb in self.verbs if verb.is_source)

    def producing(self, kind: Kind) -> tuple[Verb, ...]:
        return tuple(
            verb for verb in self.verbs
            if verb.produces is kind or verb.produces is Kind.SAME
        )

    def consuming(self, kind: Kind) -> tuple[Verb, ...]:
        """Every verb that legally accepts this kind."""
        return tuple(verb for verb in self.verbs if verb.accepts(kind))

    def successors(self, name: str) -> tuple[Verb, ...]:
        """What may follow this verb. The manual's composition section.

        A polymorphic verb produces `SAME`, so what may follow it depends on what
        was piped *into* it. With no upstream context the honest answer is every
        verb that could accept anything it might pass through, and the manual says
        so rather than implying a narrower set.
        """
        verb = self.require(name)
        if verb.produces is Kind.SAME:
            return tuple(v for v in self.verbs if not v.is_source)
        return tuple(
            candidate for candidate in self.consuming(verb.produces)
            if not candidate.is_source
        )

    def predecessors(self, name: str) -> tuple[Verb, ...]:
        """What may precede this verb."""
        verb = self.require(name)
        if verb.is_source:
            return ()
        return tuple(
            candidate for candidate in self.verbs
            if verb.accepts(candidate.produces) or candidate.produces is Kind.SAME
        )

    def paths_to(self, kind: Kind, *, max_length: int = 5) -> tuple[tuple[str, ...], ...]:
        """Every pipeline, up to a length, that ends in this kind.

        Breadth-first from the source verbs, so shorter compositions come first —
        which is what the planner wants, because a two-stage answer beats a
        five-stage one that reaches the same place.
        """
        found: list[tuple[str, ...]] = []
        frontier: list[tuple[tuple[str, ...], Kind]] = [
            ((verb.name,), verb.produces) for verb in self.sources()
        ]

        while frontier:
            route, current = frontier.pop(0)
            if len(route) > max_length:
                continue
            if current is kind:
                found.append(route)
                # Keep walking: a longer route may refine the same kind, and the
                # planner ranks them rather than taking the first.
            if len(route) == max_length:
                continue
            for candidate in self.consuming(current):
                if candidate.is_source or candidate.name in route:
                    continue      # no cycles — a verb repeating adds nothing
                frontier.append(
                    ((*route, candidate.name), candidate.output_for(current))
                )

        found.sort(key=lambda route: (len(route), route))
        return tuple(found)

    def reachable(self) -> frozenset[Kind]:
        """Every kind some composition can produce. Its complement is a gap."""
        found: set[Kind] = set()
        for kind in Kind:
            if kind.polymorphic or kind is Kind.NOTHING:
                continue
            if self.paths_to(kind, max_length=4):
                found.add(kind)
        return frozenset(found)

    # -- projection ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """What `/api/verbs` returns, and what every client builds its UI from."""
        return {
            "verbs": [verb.to_dict() for verb in self.verbs],
            "groups": {
                group: [verb.name for verb in items]
                for group, items in self.groups().items()
            },
            "kinds": [kind.value for kind in Kind if not kind.polymorphic],
            "sources": [verb.name for verb in self.sources()],
            "mutating": [verb.name for verb in self.mutating()],
            "graph": {
                verb.name: [candidate.name for candidate in self.successors(verb.name)]
                for verb in self.verbs
            },
        }

    def __len__(self) -> int:
        return len(self._verbs)

    def __contains__(self, name: object) -> bool:
        return name in self._verbs

    def __iter__(self) -> Iterator[Verb]:
        return iter(self.verbs)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<VerbRegistry {len(self._verbs)} verbs>"


_REGISTRY: VerbRegistry | None = None


def registry() -> VerbRegistry:
    """The process-wide registry, built once from the built-in verbs."""
    global _REGISTRY
    if _REGISTRY is None:
        from .verbs import register_builtins

        _REGISTRY = register_builtins(VerbRegistry())
    return _REGISTRY


def reset() -> None:
    """Drop the cached registry. For tests that register their own verbs."""
    global _REGISTRY
    _REGISTRY = None
