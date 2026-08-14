"""Transformations: operator-supplied code, gated and isolated."""

from .loader import (
    TRANSFORM_TEMPLATE,
    Transformation,
    TransformOutcome,
    TransformRegistry,
    scaffold,
)
from .policy import (
    BLOCKED_ATTRIBUTES,
    BLOCKED_BUILTINS,
    BLOCKED_IMPORTS,
    DEFAULT_POLICY,
    SAFE_IMPORTS,
    TRUSTED_POLICY,
    SandboxPolicy,
)
from .sandbox import (
    ENTRYPOINT,
    Inspection,
    Sandbox,
    SandboxResult,
    inspect_file,
    inspect_source,
)

__all__ = [
    "BLOCKED_ATTRIBUTES",
    "BLOCKED_BUILTINS",
    "BLOCKED_IMPORTS",
    "DEFAULT_POLICY",
    "ENTRYPOINT",
    "SAFE_IMPORTS",
    "TRANSFORM_TEMPLATE",
    "TRUSTED_POLICY",
    "Inspection",
    "Sandbox",
    "SandboxPolicy",
    "SandboxResult",
    "TransformOutcome",
    "TransformRegistry",
    "Transformation",
    "inspect_file",
    "inspect_source",
    "scaffold",
]
