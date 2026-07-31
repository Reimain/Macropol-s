"""A graph to judge, built from a scan when no database exists.

Every rule reads `RuleContext.graph`, a `GraphView`. That is the right contract —
a rule must not be able to mutate the thing it is judging — but taken literally it
would mean governance only runs once somebody has an environment, a ledger and a
SQLite file. The tree that most needs a licence check is the one nobody has
described yet, and a rule plane that could not read it would be answering the
easy half of the question.

So `view_of` turns observations into a graph. The decision worth defending is
**that it builds the real one** rather than a lightweight stand-in:

* `materialise` (`slpie/discovery/registry.py`) already converts observations into
  the exact `Node` and `Edge` objects the projection stores. Reusing it means a
  rule sees byte-identical inputs whether they came from a scan or from Postgres.
* `SqliteGraph(None)` is in memory and is the same class the engine uses, so
  `nodes(live=True)` has one implementation. A second `GraphView` written for the
  offline path would be a parallel truth, and the two would disagree the first
  time a filter changed on one side only.

That is invariant 7 applied to governance: simulated and live, scanned and stored,
differ in *binding* and in nothing above it. The cost is real — building 2,000
nodes takes roughly half a second — and it is the right price for rules that
cannot tell where their input came from.

The view is **closed by the caller**. `govern` owns it for the length of one
evaluation, which is why it is a context manager rather than something cached: a
governance run judges the tree as it was read, and a view held across two scans
would be judging a mixture.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Sequence


@contextmanager
def view_of(observations: Iterable[Any]) -> Iterator[Any]:
    """Observations → a live in-memory graph, closed when the caller is done."""
    from ..discovery.registry import materialise
    from ..graph.sqlite_graph import SqliteGraph

    nodes, edges, _errors = materialise(tuple(observations))
    graph = SqliteGraph(None)
    try:
        for node in nodes:
            graph.assert_node(node)
        for edge in edges:
            graph.assert_edge(edge)
        yield graph
    finally:
        graph.close()


def view_from_resolution(resolution: Any) -> tuple[Any, ...]:
    """The observations behind a resolution, so `link | govern` can be judged.

    A `Resolution` has already merged observations onto identities, and the
    merge is what the resolver exists for — but the graph projection performs its
    own merge on the same rule, so handing the *observations* back is not a
    round trip that loses anything. Handing the resolved entries instead would
    mean writing a second conversion from `Resolved` to `Node`, and two paths
    into the graph is exactly the drift this module refuses.
    """
    found: list[Any] = []
    for entry in getattr(resolution, "resolved", ()):
        found.extend(entry.observations)
    return tuple(found)
