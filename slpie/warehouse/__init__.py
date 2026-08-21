"""The BI layer — facts and dimensions, so the numbers are reachable as numbers.

Everything this platform derives used to leave through a picture. A TOGAF view
rendered as Mermaid, a risk register rendered as Markdown, a C4 diagram — all
correct, all *terminal*. Anything that wanted to ask a question the report did
not already answer had to parse a diagram of the answer, and a question nobody
anticipated is exactly what a warehouse exists for.

So this package sits between the graph and everything downstream of it:

    graph ──▶ warehouse (facts, dimensions, measures) ──┬──▶ SQL, in the store
                                                        ├──▶ CSV / JSON extracts
                                                        ├──▶ Parquet (ring 1)
                                                        └──▶ templates ──▶ screens

Reports become one consumer among several rather than the only exit.

── It is a star, and that is a decision ─────────────────────────────────

Facts are long and thin — one row per finding, per edge, per observation — and
dimensions are short and wide. Not because star schemas are fashionable but
because the questions are: *findings by severity over time*, *edges by evidence
kind*, *nodes by team*. Every one of those is a group-by on a dimension key, and
a shape that made them joins across a graph would make a BI tool crawl.

── Nothing here loses provenance ────────────────────────────────────────

A fact row carries the confidence and the evidence kind it came from, because a
count of relationships is a different number depending on whether they were
read from a lockfile or inferred from a name. A warehouse that dropped that
would be the one place in this platform where a number arrives without its
qualification — and the whole claim is that it never does.
"""

from .measures import MEASURES, Measure, measure
from .model import Column, Dimension, Fact, Star, Table

__all__ = [
    "Column", "Dimension", "Fact", "MEASURES", "Measure", "Star", "Table",
    "measure",
]
