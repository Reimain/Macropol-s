"""The declare-first entry point.

You hand the platform a manifest listing what your environment contains, and the
skeleton graph exists before a single file has been read — the same reason a
`requirements.txt` resolves faster than a search of your disk. Discovery then
corroborates the declaration, and the delta between them is intelligence in its
own right rather than an error to be reconciled away.
"""

from __future__ import annotations

from .loader import DEFAULT_FILENAME, load, loads, scaffold, skeleton
from .manifest import (
    Boundary,
    Declaration,
    DeclarationKind,
    Manifest,
    SecurityPosture,
    Target,
)
from .reconcile import Reconciliation, findings, reconcile
from .schema import SECTIONS, parse_yaml, validate

__all__ = [
    "Manifest", "Target", "Declaration", "DeclarationKind", "Boundary", "SecurityPosture",
    "load", "loads", "skeleton", "scaffold", "DEFAULT_FILENAME",
    "reconcile", "Reconciliation", "findings",
    "parse_yaml", "validate", "SECTIONS",
]
