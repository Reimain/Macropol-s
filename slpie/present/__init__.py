"""Presentation — and the reason it is a tier of its own rather than a method.

Three models carried a `to_mermaid()`: the enterprise `View`, the C4 view and
the codegen view. Three implementations of one idea, each reachable only by
already holding the object it hangs off. That had two costs, and the second is
the one that mattered:

1. **They drifted.** Escaping, the empty case and the arrow syntax were decided
   three times, and a fix to one was a fix to one.
2. **Rendering became the only way out of the intelligence layer.** A view's
   numbers were reachable through a diagram or not at all, so anything wanting
   *facts* — a warehouse, a dashboard, another tool — had to parse a picture of
   them.

So the dependency is inverted. A model exposes its structure as a `Diagram`,
which is data; this package turns data into Mermaid, into a chart spec, into
whatever comes next. **Nothing in `enterprise/` or `artifacts/` imports this
package**, and a test asserts it — a model that knew how it was drawn would be
back where it started.

The rule in one line: *upstream produces values, downstream produces pictures.*
"""

from . import c4
from .diagram import Diagram, Link, Mark
from .mermaid import document, mermaid

__all__ = ["Diagram", "Link", "Mark", "c4", "document", "mermaid"]
