"""The GraphStore protocol — the seam distributed storage would slot into.

Everything above this line works in nodes, edges and evidence. Nothing above it
knows that the default implementation is SQLite, which is what makes the plan's
deferred "distributed scale" an added implementation rather than a rewrite.

:class:`GraphView` is the read-only face handed to governance rules and the
reasoning layers. Rules run over the graph constantly and must not be able to
change it — a rule that could write would make findings a function of evaluation
order.
"""

from __future__ import annotations

from typing import Any, Iterable, Protocol, Sequence, runtime_checkable

from ..domain.edge import Edge, EdgeKind
from ..domain.evidence import Evidence
from ..domain.node import Node, NodeKind


@runtime_checkable
class GraphView(Protocol):
    """Read-only access to the graph. What rules and layers receive."""

    def node(self, node_id: str) -> Node | None: ...

    def nodes(
        self, *, kind: NodeKind | None = None, live: bool = True, limit: int = 0
    ) -> tuple[Node, ...]: ...

    def edges_from(
        self, node_id: str, *, kind: EdgeKind | None = None, live: bool = True
    ) -> tuple[Edge, ...]: ...

    def edges_to(
        self, node_id: str, *, kind: EdgeKind | None = None, live: bool = True
    ) -> tuple[Edge, ...]: ...

    def evidence_for(self, subject_id: str) -> tuple[Evidence, ...]: ...


@runtime_checkable
class GraphStore(GraphView, Protocol):
    """The writable graph. Only projectors hold one of these."""

    def assert_node(self, node: Node, *, sequence: int = 0) -> None: ...

    def assert_edge(self, edge: Edge, *, sequence: int = 0) -> None: ...

    def retire_node(self, node_id: str, *, valid_to: int, sequence: int = 0) -> bool: ...

    def retire_edge(self, edge_id: str, *, valid_to: int, sequence: int = 0) -> bool: ...

    def clear(self) -> None: ...


@runtime_checkable
class Traversal(Protocol):
    """Reachability queries. Implemented in SQL, not in Python loops."""

    def blast_radius(
        self, node_id: str, *, max_depth: int = 10, min_confidence: float = 0.0
    ) -> tuple[tuple[str, int, float], ...]: ...

    def reachable(
        self, node_id: str, *, max_depth: int = 10, min_confidence: float = 0.0
    ) -> tuple[tuple[str, int, float], ...]: ...

    def cycles(self, *, max_depth: int = 12) -> tuple[tuple[str, ...], ...]: ...
