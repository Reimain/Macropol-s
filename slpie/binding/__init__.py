"""The binding layer — the only code that knows simulated from live.

Above this package, a discoverer asks for a connector and reads bytes. It never
learns which side answered, because the answer has the same type and the same
shape either way. That is what makes `target: simulated` → `target: live` a
one-word change rather than a second code path, and it is asserted by test:
nothing above `binding/` may branch on the target.
"""

from __future__ import annotations

from .connector import Connector, ReadOnlyConnector, RefusingConnector, Resource
from .guard import WRITE_CAPABILITIES, Guard
from .resolver import Binding, FilesystemConnector, Resolver, filesystem_factory
from .target import TargetSelection

__all__ = [
    "Connector", "Resource", "ReadOnlyConnector", "RefusingConnector",
    "Resolver", "Binding", "FilesystemConnector", "filesystem_factory",
    "Guard", "WRITE_CAPABILITIES", "TargetSelection",
]
