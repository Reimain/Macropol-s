"""Generated outputs — SBOM, C4 views, and architecture as importable code.

`codegen` is the single SLPIE module permitted to import Gratimos, asserted by
`tests/test_slpie_boundaries.py::test_exactly_one_slpie_module_may_import_gratimos`.
Everything else here is plain text generation over the graph.

**Incomplete.** `codegen`, `sbom` and `c4` are written; `reports` and `lineage`
are not yet, and none of it has tests — see the phase-12 note in the plan.
"""

from __future__ import annotations

from . import c4, codegen, sbom

__all__ = ["codegen", "sbom", "c4"]
