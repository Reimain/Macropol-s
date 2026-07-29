"""Canonical identity — making two dialects for one package land on one node.

A Maven POM says `com.fasterxml.jackson.core:jackson-databind:2.15.2`, a
lockfile says something else, and a container manifest says a third thing. Until
those converge on a single `NodeId` the graph holds three nodes where the
ecosystem has one, and every answer built on it is wrong in a way that is hard
to see.

Four modules, each answering one question:

* `purl` — the canonical form of a purl, and the node id two dialects must
  agree on.
* `versions` — one ecosystem's version notation in the shared algebra.
* `coordinates` — the cross-walk both ways: what a file says becomes a purl,
  and a purl becomes what that ecosystem's own tooling accepts, because telling
  a Go developer their problem is `pkg:golang/...` leaves them to translate it.
* `licenses` — whatever a publisher typed, made SPDX *or refused*. An
  unrecognised string never becomes a licence: `"BSD"` alone is ambiguous
  between three licences with different obligations, and guessing produces a
  compliance answer somebody relies on.
"""

from __future__ import annotations

from . import coordinates, licenses, purl, versions

__all__ = ["purl", "versions", "coordinates", "licenses"]
