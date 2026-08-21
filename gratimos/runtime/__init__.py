"""The production plane: configuration with provenance, and plug/deplug.

Two problems, both about the cost of running this for more than one customer.

**Why is this setting what it is?** `config` resolves defaults, then
`gratimos.toml`, then `GRATIMOS_*`, then runtime overrides — and every resolved
value remembers which layer won and where it came from, so `explain()` answers
the question that otherwise costs an afternoon.

**How does a customer get behaviour nobody else wants?** `plugins` loads their
code by dotted path named in configuration, at activation time. The kernel never
imports it, so an upgrade cannot break it by moving an import, and their
extension is not carried by every other deployment. A failure quarantines the
extension and leaves the kernel running degraded rather than dead.
"""

from __future__ import annotations

from .config import (
    ENV_PREFIX,
    REDACTED,
    Config,
    ConfigError,
    Layer,
    Setting,
    load,
)
from .plugins import (
    Extension,
    ExtensionError,
    Host,
    Point,
    Slot,
    State,
    extensions_from_config,
)

__all__ = [
    "Config", "ConfigError", "Layer", "Setting", "load", "REDACTED", "ENV_PREFIX",
    "Host", "Point", "Extension", "Slot", "State", "ExtensionError",
    "extensions_from_config",
]
