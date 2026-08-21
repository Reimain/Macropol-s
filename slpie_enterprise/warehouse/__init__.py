"""Columnar export — the one form that needs a dependency, so it lives in ring 1.

CSV, JSON and SQL are stdlib and ship in ring 0, which is what keeps the
warehouse usable air-gapped. Parquet is not: it needs Arrow, which is a large
compiled dependency and exactly the kind of thing invariant 4 keeps out of the
kernel.

It is worth having anyway. A 1.9-million-row fact table is a different object in
Parquet than in CSV — column pruning, predicate pushdown and a tenth the bytes —
and an analytics engine pointed at an extract wants that shape. So it is here,
behind the same seam Postgres is: `slpie[enterprise]`, one import, and the
`Table` it reads is the identical declaration ring 0 exports from.
"""

from .parquet import AVAILABLE, to_parquet, write_parquet

__all__ = ["AVAILABLE", "to_parquet", "write_parquet"]
