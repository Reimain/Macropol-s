"""Codegen: shapes in, mergeable source out."""

from .astmerge import (
    ConflictPolicy,
    Decision,
    MergeResult,
    ModuleParts,
    Resolution,
    Symbol,
    SymbolKind,
    diff_symbols,
    merge_sources,
    parse_module,
)
from .emitter import (
    DEFAULT_EMIT,
    HEADER_MARKER,
    KEEP_MARKER,
    EmitOptions,
    GeneratedModule,
    PythonEmitter,
    emit_module,
    module_name_for,
)
from .proto import ProtoAllocation, ProtoEmitter, ProtoSchema, emit_proto
from .versioning import MODULE_NAMESPACE, Generation, ModuleRegistry

__all__ = [
    "DEFAULT_EMIT",
    "HEADER_MARKER",
    "KEEP_MARKER",
    "MODULE_NAMESPACE",
    "ConflictPolicy",
    "Decision",
    "EmitOptions",
    "GeneratedModule",
    "Generation",
    "MergeResult",
    "ModuleParts",
    "ModuleRegistry",
    "ProtoAllocation",
    "ProtoEmitter",
    "ProtoSchema",
    "PythonEmitter",
    "Resolution",
    "Symbol",
    "SymbolKind",
    "diff_symbols",
    "emit_module",
    "emit_proto",
    "merge_sources",
    "module_name_for",
    "parse_module",
]
