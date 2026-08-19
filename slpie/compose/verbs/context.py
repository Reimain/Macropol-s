"""The context family — re-exported from `slpie/context/verbs.py`.

The verbs live beside the index they project, for the same reason the audit
verbs live beside the judge: a verb is a thin typed wrapper over a capability,
and separating the two puts the summary text a manual page renders in a
different package from the code it describes.
"""

from __future__ import annotations

from ...context.verbs import verbs

__all__ = ["verbs"]
