"""§31 — the context spine: metadata that connects the product to itself.

Everything through §30 made the platform able to answer questions about *an
environment*. Nothing answered the same question about the platform: which
screen reads this route, which test covers this module, which plan section does
this package implement. That knowledge existed only in whoever last worked on
it, and knowledge that lives in a person is knowledge that leaves.

This package builds it, by reading what already exists rather than by keeping a
second copy:

==============================  ==================================================
`facet`                         `Facet` · `FacetKind` · `Link` · `Relation`
`index`                         `ContextIndex` · `build()` — the projection
`verbs`                         the `context` verb, so the map composes
==============================  ==================================================

Three surfaces consume it, and each replaces something that would otherwise be
hand-written and therefore wrong within two releases: the generated half of the
`.claude/skills/slpie/` orientation document, the runtime lexicon that puts a
reader's own words on every screen, and the screen blocks that let the kernel
compose a screen rather than dump a payload.

Ring 0, stdlib only. It reads source files and enum members; it imports no
framework, opens no socket and runs offline.
"""

from __future__ import annotations

from .facet import Facet, FacetKind, Link, Relation
from .index import ContextIndex, build

__all__ = [
    "ContextIndex", "Facet", "FacetKind", "Link", "Relation", "build",
]
