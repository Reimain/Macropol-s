"""One addressable thing, and what it connects to.

The metadata atom of §31. A `Facet` is anything in this product worth pointing
at — a module, a verb, a route, a screen, a component, a term, a test file, a
plan section — reduced to the four things that make it *connectable*: what kind
of thing it is, a stable id, where it lives in the source, and what it links to.

Two decisions are load-bearing.

**A facet is anchored or it is counted.** `source` is a `file:line`, exactly as
every evidence excerpt and every audit verdict is, because the whole point of
this index is that a claim about the product resolves to somewhere a reader can
look. A facet that cannot be anchored is not dropped and is not faked — it is
kept with an empty `source` and counted against the index's coverage, which is
the same doctrine `Verdict.INDETERMINATE` applies in `slpie/audit/judge.py`. An
index that silently omitted what it could not place would report a smaller,
cleaner product than the one that exists.

**Links are typed, and they point one way.** `Relation` is a closed vocabulary,
so "what connects to what" is queryable rather than a graph of untyped strings.
A screen *reads* a route; a verb *projects* into a route; a test *covers* a
module; a section *owns* a package. The inverse is computed by the index rather
than stored, because storing both directions is how they come to disagree.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FacetKind(str, Enum):
    """What sort of thing a facet is.

    Deliberately few, and each one is something a reader can open: a file, a
    command, an endpoint, a page, a word. There is no `CONCEPT` kind, because a
    facet nobody can open is a note, and notes drift.
    """

    MODULE = "module"
    PACKAGE = "package"
    VERB = "verb"
    KIND = "kind"
    ROUTE = "route"
    SCREEN = "screen"
    COMPONENT = "component"
    TERM = "term"
    TEST = "test"
    SECTION = "section"


class Relation(str, Enum):
    """How one facet reaches another. A closed vocabulary, so it is queryable."""

    PROJECTS = "projects"      # a verb → the route and screen it appears as
    READS = "reads"            # a screen → a route it fetches
    RENDERS = "renders"        # a screen → a component it draws with
    RUNS = "runs"              # a screen → a verb it can invoke
    CONSUMES = "consumes"      # a verb → the kind it takes
    PRODUCES = "produces"      # a verb → the kind it emits
    COVERS = "covers"          # a test → what it exercises
    IMPORTS = "imports"        # a module → a module
    OWNS = "owns"              # a package → its modules; a section → its packages
    CLAIMS = "claims"          # a module → the plan section it says it implements
    PARENT = "parent"          # a screen → the screen it is a view of
    NAMES = "names"            # a term → the facet it puts a word on


@dataclass(frozen=True, slots=True, order=True)
class Link:
    """One typed edge out of a facet."""

    relation: Relation
    target: str                # another facet's id

    def to_dict(self) -> dict[str, Any]:
        return {"relation": self.relation.value, "target": self.target}

    def __str__(self) -> str:
        return f"{self.relation.value} → {self.target}"


@dataclass(frozen=True, slots=True)
class Facet:
    """One addressable thing in the product."""

    kind: FacetKind
    name: str
    title: str = ""
    summary: str = ""
    source: str = ""                        # "slpie/compose/registry.py:120"
    links: tuple[Link, ...] = ()
    tags: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        """The stable address. `verb:findings`, `screen:graph`, `component:grid`."""
        return f"{self.kind.value}:{self.name}"

    @property
    def anchored(self) -> bool:
        """Whether this resolves to somewhere a reader can look.

        Unanchored facets are kept and counted, never dropped — see the module
        docstring. This is the property the coverage number is computed from.
        """
        return bool(self.source)

    @property
    def path(self) -> str:
        """The file half of `source`, without the line."""
        return self.source.rsplit(":", 1)[0] if self.source else ""

    def linked(self, relation: Relation) -> tuple[str, ...]:
        return tuple(link.target for link in self.links if link.relation is relation)

    def with_links(self, *added: Link) -> "Facet":
        """A copy carrying more links, de-duplicated and ordered.

        Building an index means discovering edges from several directions — a
        screen declares the routes it reads, and a verb separately declares the
        route it projects into — so facets accumulate links rather than being
        written once. Sorting here means the order edges were discovered in
        cannot reach the digest.
        """
        merged = tuple(sorted(set(self.links) | set(added)))
        return Facet(
            kind=self.kind, name=self.name, title=self.title,
            summary=self.summary, source=self.source, links=merged, tags=self.tags,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "name": self.name,
            "title": self.title,
            "summary": self.summary,
            "source": self.source,
            "links": [link.to_dict() for link in self.links],
            "tags": list(self.tags),
        }

    @property
    def digest(self) -> str:
        """Content-addressed over everything that is not incidental.

        The whole facet takes part, links included: an edge appearing or
        disappearing is exactly the kind of drift the index digest exists to
        catch, so leaving links out would make the digest agree across a
        restructure that rewired the product.
        """
        body = "\x1f".join((
            self.kind.value, self.name, self.title, self.summary, self.source,
            "\x1e".join(f"{link.relation.value}>{link.target}" for link in self.links),
            "\x1e".join(self.tags),
        ))
        return hashlib.blake2b(body.encode("utf-8"), digest_size=16).hexdigest()

    def __str__(self) -> str:
        return f"{self.id} ({self.source})" if self.source else f"{self.id} (unanchored)"
