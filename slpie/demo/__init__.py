"""The demo — one script, projected to a terminal or to screens.

A demo made of print statements can only ever be a terminal transcript: it cannot
drive the UI, cannot be stepped through on a phone, and cannot be checked against
what it claims. So this one is a :class:`~slpie.demo.script.Script` of
:class:`~slpie.demo.script.Beat`\\ s, each stating *what* it demonstrates, *which*
surface demonstrates it, *which* screen it maps to, and *which* call produces it.

Three projections of the same beats:

* :func:`~slpie.demo.runner.render` — the terminal, for ``slpie demo``;
* :meth:`~slpie.demo.script.Script.to_dict` — what the UI fetches to walk the same
  demo through its own views;
* ``tests/test_slpie_demo.py`` — which asserts every beat's ``claim``.

That third one is why the beats carry a claim at all. A beat reports ``holds``, and
a demo that narrated something it could not substantiate would be the most
expensive kind of documentation: convincing and wrong.
"""

from .beats import PIPELINE, script
from .runner import Demo, main, render, run
from .script import Beat, Outcome, Script, Stage, Surface
from .world import EXPECTATIONS, FILES, materialise

__all__ = [
    "EXPECTATIONS",
    "FILES",
    "PIPELINE",
    "Beat",
    "Demo",
    "Outcome",
    "Script",
    "Stage",
    "Surface",
    "main",
    "materialise",
    "render",
    "run",
    "script",
]
