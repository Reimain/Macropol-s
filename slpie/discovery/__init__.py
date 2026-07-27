"""Discovery — reading sources and reporting what they say, with citations.

A discoverer never touches the graph, never assigns confidence and never decides
what a relationship means. It reads one file and reports what it found and where
it found it; the layers above turn that into structure.

Every built-in registers through the public plugin path, so the extension seam
is exercised on every run rather than only by third-party code nobody has
written yet.
"""

from __future__ import annotations

from .base import Source, declares, depends, evidence_at, imports, result
from .registry import (
    EDGE_KINDS,
    MAX_SOURCE_BYTES,
    SKIP,
    ScanReport,
    Scanner,
    materialise,
    register_builtins,
)

__all__ = [
    "register_builtins", "Scanner", "ScanReport", "materialise",
    "Source", "evidence_at", "depends", "imports", "declares", "result",
    "EDGE_KINDS", "SKIP", "MAX_SOURCE_BYTES",
]
