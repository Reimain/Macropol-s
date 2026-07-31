"""Enterprise architecture — views over the graph, emitted as code.

TOGAF views, deployment topology and a risk register, each a **projection of the
graph and never a second store**. A view selects nodes, reads what the graph
already derived, and orders the result; it computes no confidence and invents no
classification, so it cannot drift from the graph because there is nothing in it
that could.

===============  ===========================================================
`togaf`          application, data, technology and the standards catalogue
`topology`       what is running, where, and what has not been deployed
`risk`           findings aggregated onto subjects, ranked by reach
===============  ===========================================================

These are the inputs to `slpie/artifacts/codegen.py`, which turns a view into
importable Python through the one Gratimos import the architecture permits. The
dependency points one way: nothing here knows that code generation exists, and
`artifacts` knows nothing about TOGAF — the two meet at the structural
`ArchitectureView` protocol and nowhere else.
"""

from __future__ import annotations

from . import risk, togaf, topology, view
from .risk import Risk, heat_map, register, report, risk_view
from .togaf import (
    application_view,
    data_view,
    standards_view,
    technology_view,
    togaf_views,
)
from .topology import environments, topology_view, undeployed
from .view import View, identifier, relations_between, unique

__all__ = [
    "Risk",
    "View",
    "application_view",
    "data_view",
    "environments",
    "heat_map",
    "identifier",
    "register",
    "relations_between",
    "report",
    "risk",
    "risk_view",
    "standards_view",
    "technology_view",
    "togaf",
    "togaf_views",
    "topology",
    "topology_view",
    "undeployed",
    "unique",
    "view",
]
