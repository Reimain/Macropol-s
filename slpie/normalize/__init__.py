"""Canonical identity — making two dialects for one package land on one node.

A Maven POM says `com.fasterxml.jackson.core:jackson-databind:2.15.2`, a
lockfile says something else, and a container manifest says a third thing. Until
those converge on a single `NodeId` the graph holds three nodes where the
ecosystem has one, and every answer built on it is wrong in a way that is hard
to see.

**Incomplete.** `purl` and `versions` are written; `coordinates` and `licenses`
are not yet. Nothing here is wired into the pipeline — see the phase-9 note in
the plan.
"""

from __future__ import annotations

from . import purl, versions

__all__ = ["purl", "versions"]
