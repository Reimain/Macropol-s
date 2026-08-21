"""Postgres persistence — ring 1's implementation of ring 0's storage protocols.

`GraphStore`, `GraphView` and the ledger's equivalents are published in
`slpie/graph/store.py` and `slpie/ledger/`. This package implements them against
Postgres and adds nothing to them, which is §22's whole argument: the enterprise
tier is an *implementation of published contracts*, never a redesign.

Ring 0 does not know this exists and never imports it.
"""

from __future__ import annotations

from .dialect import translate
from .engine import Database, connect

__all__ = ["Database", "connect", "translate"]
