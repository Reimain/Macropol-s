"""Emitting Python from shapes.

Generated code has to be readable by the people who will later have to debug it
at 3am, so it is plain: dataclasses, standard-library types, explicit
conversion methods. No metaclass tricks, no runtime code assembly.

Every generated module carries its provenance in the header — the entity, the
shape digest, and the revision it was generated from. That header is what makes
regeneration a *merge* instead of an overwrite: the digest says whether the
shape actually moved, and the marker lines tell the AST merge which symbols the
generator owns.
"""

from __future__ import annotations

import keyword
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..errors import CodegenError
from ..ids import iso, slug
from ..meta import DataShape, FieldShape, TypeTag

#: Marks a symbol as hand-owned; the merge never overwrites it.
KEEP_MARKER = "# gratimos:keep"
#: Marks the start of the generated header block.
HEADER_MARKER = "# gratimos:generated"

_IMPORTS_FOR: dict[TypeTag, str] = {
    TypeTag.DECIMAL: "decimal",
    TypeTag.DATE: "datetime",
    TypeTag.DATETIME: "datetime",
    TypeTag.DURATION: "datetime",
    TypeTag.UUID: "uuid",
}


@dataclass(frozen=True, slots=True)
class EmitOptions:
    """How much machinery to generate."""

    frozen: bool = True
    slots: bool = True
    docstrings: bool = True
    examples: bool = True
    converters: bool = True   # from_record / to_record
    validators: bool = True   # __post_init__ null checks
    shape_constant: bool = True
    module_docstring: bool = True


DEFAULT_EMIT = EmitOptions()


@dataclass(slots=True)
class GeneratedModule:
    """Emitted source plus what it was generated from."""

    entity: str
    module_name: str
    source: str
    shape_digest: str
    revision: int = 1
    symbols: tuple[str, ...] = ()
    imports: tuple[str, ...] = ()
    warnings: list[str] = field(default_factory=list)

    @property
    def filename(self) -> str:
        return f"{self.module_name}.py"

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "module": self.module_name,
            "filename": self.filename,
            "shape_digest": self.shape_digest,
            "revision": self.revision,
            "symbols": list(self.symbols),
            "lines": self.source.count("\n") + 1,
            "warnings": self.warnings,
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<GeneratedModule {self.module_name} symbols={list(self.symbols)}>"


def module_name_for(entity: str) -> str:
    name = slug(entity)
    if name[0].isdigit():
        name = f"m_{name}"
    return f"{name}_" if keyword.iskeyword(name) else name


class PythonEmitter:
    """Turns a :class:`DataShape` into a dataclass module."""

    def __init__(self, options: EmitOptions = DEFAULT_EMIT) -> None:
        self.options = options

    # -- entry point -----------------------------------------------------

    def emit(self, shape: DataShape, *, revision: int = 1, source: str = "") -> GeneratedModule:
        if not shape.fields:
            raise CodegenError(f"cannot generate a model for {shape.name!r}: shape has no fields")

        warnings: list[str] = []
        seen: dict[str, str] = {}
        for f in shape.fields:
            if f.ident in seen:
                warnings.append(
                    f"fields {seen[f.ident]!r} and {f.name!r} both map to identifier {f.ident!r}"
                )
            seen[f.ident] = f.name

        imports = self._imports(shape)
        # Order matters: the shape builder must be defined before SHAPE is
        # evaluated, and SHAPE must exist before any module-level use of it.
        blocks = [
            self._header(shape, revision, source),
            "from __future__ import annotations",
            "",
            *(sorted(imports) if imports else []),
            "from dataclasses import dataclass",
            "",
            "",
            self._digest_constant(shape, revision),
            self._shape_functions(shape),
        ]
        if self.options.shape_constant:
            blocks.append(self._shape_constant())
        blocks.append(self._class(shape))
        blocks.append(self._describe(shape))

        body = "\n".join(block for block in blocks if block is not None)
        return GeneratedModule(
            entity=shape.name,
            module_name=module_name_for(shape.name),
            source=body.rstrip() + "\n",
            shape_digest=shape.digest,
            revision=revision,
            symbols=self._symbols(shape),
            imports=tuple(sorted(imports)),
            warnings=warnings,
        )

    # -- pieces ----------------------------------------------------------

    def _symbols(self, shape: DataShape) -> tuple[str, ...]:
        out = ["SHAPE_DIGEST", "SHAPE_REVISION", "_shape_payload", "_rebuild_shape"]
        if self.options.shape_constant:
            out.append("SHAPE")
        out.extend([shape.symbol, "describe"])
        return tuple(out)

    def _imports(self, shape: DataShape) -> set[str]:
        modules = {"typing"}
        for f in _walk(shape.fields):
            if module := _IMPORTS_FOR.get(f.tag):
                modules.add(module)
        return {f"import {m}" for m in modules}

    def _header(self, shape: DataShape, revision: int, source: str) -> str:
        if not self.options.module_docstring:
            return HEADER_MARKER
        origin = source or shape.source or "the observed context"
        lines = [
            HEADER_MARKER,
            '"""Model for `%s`, generated by Gratimos.' % shape.name,
            "",
            f"Generated from {origin} at {iso()}.",
            "",
            "Regeneration merges into this file at the AST level rather than",
            f"overwriting it. To keep a symbol you edited by hand, mark it with",
            f"`{KEEP_MARKER}` on the line above its definition.",
            "",
            f"shape digest : {shape.digest}",
            f"revision     : {revision}",
            f"fields       : {len(shape.fields)}",
            f"rows observed: {shape.rows}",
            '"""',
        ]
        return "\n".join(lines)

    def _digest_constant(self, shape: DataShape, revision: int) -> str:
        return (
            f'SHAPE_DIGEST = "{shape.digest}"\n'
            f"SHAPE_REVISION = {revision}\n"
        )

    def _class(self, shape: DataShape) -> str:
        decorator = "@dataclass(" + ", ".join(
            part for part in (
                "frozen=True" if self.options.frozen else "",
                "slots=True" if self.options.slots else "",
            ) if part
        ) + ")" if (self.options.frozen or self.options.slots) else "@dataclass"

        lines = [decorator, f"class {shape.symbol}:"]
        if self.options.docstrings:
            lines.extend(self._class_doc(shape))

        # Required fields must precede defaulted ones in a dataclass.
        ordered = sorted(shape.fields, key=lambda f: bool(f.nullable or f.optional))
        for f in ordered:
            lines.append("    " + self._field(f))
        if self.options.validators:
            lines.append("")
            lines.extend(self._post_init(shape, ordered))
        if self.options.converters:
            lines.append("")
            lines.extend(self._from_record(shape, ordered))
            lines.append("")
            lines.extend(self._to_record(shape))
        lines.append("")
        return "\n".join(lines) + "\n"

    def _class_doc(self, shape: DataShape) -> list[str]:
        out = ['    """One `%s` record.' % shape.name, ""]
        if shape.doc:
            out.extend([f"    {shape.doc}", ""])
        out.append("    Fields:")
        for f in shape.fields:
            note = []
            if f.nullable:
                note.append("nullable")
            if f.optional:
                note.append("optional")
            if f.format_hint:
                note.append(f"as {f.format_hint}")
            suffix = f" ({', '.join(note)})" if note else ""
            out.append(f"        {f.ident}: {f.tag.value}{suffix}")
            if self.options.examples and f.examples:
                out.append(f"            e.g. {', '.join(repr(e) for e in f.examples[:2])}")
        out.extend(['    """', ""])
        return out

    def _field(self, f: FieldShape) -> str:
        annotation = f.python_type
        if f.nullable or f.optional:
            annotation = f"{annotation} | None"
        line = f"{f.ident}: {annotation}"
        if f.nullable or f.optional:
            line += " = None"
        if f.name != f.ident:
            line += f"  # source field: {f.name!r}"
        return line

    def _post_init(self, shape: DataShape, ordered: Sequence[FieldShape]) -> list[str]:
        required = [f for f in ordered if not (f.nullable or f.optional)]
        if not required:
            return ["    def __post_init__(self) -> None:", "        return None", ""]
        out = [
            "    def __post_init__(self) -> None:",
            '        """Reject records missing values the shape says are always present."""',
            "        missing = [",
            "            name",
            "            for name in (" + ", ".join(f'"{f.ident}"' for f in required) + ",)",
            "            if getattr(self, name) is None",
            "        ]",
            "        if missing:",
            "            raise ValueError(",
            f'                f"{shape.symbol} is missing non-nullable field(s): {{\', \'.join(missing)}}"',
            "            )",
        ]
        return out

    def _from_record(self, shape: DataShape, ordered: Sequence[FieldShape]) -> list[str]:
        out = [
            "    @classmethod",
            f'    def from_record(cls, record: typing.Mapping[str, typing.Any]) -> "{shape.symbol}":',
            '        """Build from a raw record, casting through the shape."""',
            "        from gratimos.meta import CastMode, Caster",
            "",
            "        values, report = Caster(SHAPE, mode=CastMode.LENIENT).record(record)",
            "        if report.failures:",
            "            raise ValueError(",
            f'                f"cannot build {shape.symbol}: {{\'; \'.join(report.failures[:3])}}"',
            "            )",
            "        return cls(**{k: v for k, v in values.items() if k in cls.__dataclass_fields__})",
        ]
        return out

    def _to_record(self, shape: DataShape) -> list[str]:
        out = [
            "    def to_record(self) -> dict[str, typing.Any]:",
            '        """Back to a plain record, keyed by the original field names."""',
            "        return {",
        ]
        for f in shape.fields:
            out.append(f'            "{f.name}": self.{f.ident},')
        out.extend(["        }"])
        return out

    def _shape_constant(self) -> str:
        return (
            "#: The shape this module was generated from, rebuilt at import time so\n"
            "#: `from_record` casts against exactly what the generator saw.\n"
            "SHAPE = _rebuild_shape()\n"
        )

    def _shape_functions(self, shape: DataShape) -> str:
        payload = _literal(shape.to_dict())
        return (
            "def _shape_payload() -> dict[str, typing.Any]:\n"
            '    """The serialized shape; kept as data so it stays diffable."""\n'
            f"    return {payload}\n"
            "\n"
            "\ndef _rebuild_shape() -> typing.Any:\n"
            "    from gratimos.meta import DataShape\n"
            "\n"
            "    return DataShape.from_dict(_shape_payload())\n"
            "\n"
        )

    def _describe(self, shape: DataShape) -> str:
        return (
            "\ndef describe() -> dict[str, typing.Any]:\n"
            '    """What this module models — used by the orchestrator\'s catalog."""\n'
            "    return {\n"
            f'        "entity": "{shape.name}",\n'
            f'        "symbol": "{shape.symbol}",\n'
            '        "digest": SHAPE_DIGEST,\n'
            '        "revision": SHAPE_REVISION,\n'
            '        "fields": [f.ident for f in SHAPE.fields],\n'
            "    }\n"
        )


def _walk(fields: Iterable[FieldShape]) -> Iterable[FieldShape]:
    for f in fields:
        yield f
        if f.children:
            yield from _walk(f.children)
        if f.item:
            yield from _walk([f.item])


def _literal(value: Any, indent: int = 4) -> str:
    """Render a JSON-ish value as readable Python source."""
    pad = " " * indent
    inner = " " * (indent + 4)
    if isinstance(value, Mapping):
        if not value:
            return "{}"
        items = ",\n".join(f"{inner}{_literal(k, indent + 4)}: {_literal(v, indent + 4)}"
                           for k, v in value.items())
        return "{\n" + items + f",\n{pad}}}"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        items = ",\n".join(f"{inner}{_literal(v, indent + 4)}" for v in value)
        return "[\n" + items + f",\n{pad}]"
    return repr(value)


def emit_module(shape: DataShape, *, revision: int = 1, options: EmitOptions = DEFAULT_EMIT) -> GeneratedModule:
    """Convenience wrapper over :class:`PythonEmitter`."""
    return PythonEmitter(options).emit(shape, revision=revision)
