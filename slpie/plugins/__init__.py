"""Everything is a plugin, and built-ins register through the identical path.

That is what keeps the seam honest: if built-in discoverers took a private
route, the extension point would only ever be exercised by third-party code
nobody had written yet. Because they go through the same registry, every run
tests it.

Language-agnostic extensions run out of process, exchanging newline-delimited
JSON on stdio. It is the only honest route to polyglot analysis — a Go analyzer
should use `go/ast` and a TypeScript one the TS compiler API, not a Python
approximation of either. External plugins run under CPU, memory and wall-clock
limits, and one that misbehaves is quarantined and reported as a gap rather than
being retried until the scan never finishes.
"""

from __future__ import annotations

from .external import ExternalPlugin
from .manifest import (
    MANIFEST_FILENAME,
    ExtensionPoint,
    PluginManifest,
    Runtime,
)
from .protocol import (
    PROTOCOL_VERSION,
    DiscoveryResult,
    Message,
    Observation,
    parse_result,
)
from .registry import InProcess, Registration, Registry
from .sandbox import DEFAULT_LIMITS, Limits

__all__ = [
    "Registry", "Registration", "InProcess",
    "PluginManifest", "ExtensionPoint", "Runtime", "MANIFEST_FILENAME",
    "Observation", "DiscoveryResult", "Message", "parse_result", "PROTOCOL_VERSION",
    "ExternalPlugin", "Limits", "DEFAULT_LIMITS",
]
