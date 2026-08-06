"""The notebooks, as data.

Every notebook under `notebooks/` is generated from a specification in this
package. The reason is the one §24 gives for the verb registry: a notebook is a
*projection* of the platform, and a projection that is maintained by hand drifts
from what it projects. A notebook is worse than documentation when it drifts,
because it looks executable and therefore looks checked.

So the pipeline is:

    spec (Python)  →  build.py  →  notebooks/*.ipynb  →  run.py  →  outputs
                                                             ↓
                                                     CI fails if any cell raises

`run.py` executes every notebook with a real kernel and refuses to pass if a
cell errors, so a committed notebook is one that ran. `build.py` regenerates
them, so a change to the platform's API is a change to one spec cell rather than
a hunt through JSON.

Cells are plain strings here rather than `nbformat` objects. That keeps a spec
readable as prose — which matters, because these are the pages a reader learns
the platform from.
"""

from .spec import NOTEBOOKS, Notebook, code, markdown

__all__ = ["NOTEBOOKS", "Notebook", "code", "markdown"]
