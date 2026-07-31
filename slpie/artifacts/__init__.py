"""Generated outputs — SBOM, C4 views, and architecture as importable code.

`codegen` is the single SLPIE module permitted to import Gratimos, asserted by
`tests/test_slpie_boundaries.py::test_exactly_one_slpie_module_may_import_gratimos`.
Everything else here is plain text generation over the graph, which is why it
stays in ring 0: writing a document needs no third-party package and no network.

===========  ==============================================================
`sbom`       CycloneDX 1.5 and SPDX 2.3, with purls passed through untranslated
`c4`         the four C4 levels as Mermaid, each a filter over the same graph
`codegen`    enterprise views as importable Python, through an AST three-way merge
===========  ==============================================================

Two rules hold across all three. **Nothing is invented**: a licence appears only
where a node carries one, a hash only where evidence supplied one. **Nothing
reads the clock**: timestamps are arguments and serial numbers are derived from
content, so identical graphs produce byte-identical documents that can be
diffed, attested and checked into a release.

The views these consume live in :mod:`slpie.enterprise`, and the dependency
points one way — `artifacts` knows nothing about TOGAF, `enterprise` knows
nothing about code generation, and the two meet at a structural protocol.
"""

from __future__ import annotations

from . import c4, codegen, sbom

__all__ = ["c4", "codegen", "sbom"]
