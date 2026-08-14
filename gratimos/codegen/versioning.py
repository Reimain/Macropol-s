"""Versioned generated modules: write, merge, load, roll back.

This is the component that makes "the code reshapes itself" survivable. Every
generation is recorded — source, digest, the shape it came from, and the merge
decisions that produced it — so the *previous* generated source is always
available as the merge base, and any generation can be restored.

Loading is by explicit path into a private module namespace. Generated modules
are never placed on ``sys.path`` where an unrelated import could pick them up by
accident.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Sequence

from ..contextflow import ContextFlow, EventKind
from ..errors import CodegenError
from ..ids import StorageName, iso, new_id, now_ns
from ..meta import DataShape
from .astmerge import ConflictPolicy, Decision, MergeResult, merge_sources
from .emitter import DEFAULT_EMIT, EmitOptions, GeneratedModule, PythonEmitter
from .proto import ProtoAllocation, ProtoEmitter, ProtoSchema

#: Namespace generated modules are imported under.
MODULE_NAMESPACE = "gratimos_generated"


@dataclass(frozen=True, slots=True)
class Generation:
    """One recorded emission of a module."""

    entity: str
    module_name: str
    revision: int
    source: str
    shape_digest: str
    storage: StorageName
    decisions: tuple[Decision, ...] = ()
    conflicts: tuple[Decision, ...] = ()
    event_id: str = ""
    id: str = field(default_factory=lambda: new_id("gen"))
    ts_ns: int = field(default_factory=now_ns)

    @property
    def clean(self) -> bool:
        return not self.conflicts

    @property
    def at(self) -> str:
        return iso(self.ts_ns)

    @property
    def path(self) -> str:
        return f"code.{self.entity}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "entity": self.entity,
            "module": self.module_name,
            "revision": self.revision,
            "shape_digest": self.shape_digest,
            "key": self.storage.key,
            "clean": self.clean,
            "at": self.at,
            "lines": self.source.count("\n") + 1,
            "decisions": [d.to_dict() for d in self.decisions],
            "conflicts": [d.to_dict() for d in self.conflicts],
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Generation {self.module_name} r{self.revision}{'' if self.clean else ' CONFLICTED'}>"


class ModuleRegistry:
    """Owns the generated package directory and the history behind it."""

    def __init__(
        self,
        root: str | Path,
        *,
        flow: ContextFlow | None = None,
        emitter: PythonEmitter | None = None,
        proto_emitter: ProtoEmitter | None = None,
        actor: str = "codegen",
        conflict_policy: ConflictPolicy = ConflictPolicy.RAISE,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.flow = flow
        self.emitter = emitter if emitter is not None else PythonEmitter()
        self.proto_emitter = proto_emitter if proto_emitter is not None else ProtoEmitter()
        self.actor = actor
        self.conflict_policy = conflict_policy
        self._generations: dict[str, list[Generation]] = {}
        self._allocations: dict[str, ProtoAllocation] = {}
        self._loaded: dict[str, ModuleType] = {}
        self._lock = threading.RLock()
        self._ensure_package()

    # -- generation ------------------------------------------------------

    def generate(
        self,
        shape: DataShape,
        *,
        entity: str = "",
        options: EmitOptions = DEFAULT_EMIT,
        policy: ConflictPolicy | None = None,
        write: bool = True,
    ) -> Generation:
        """Emit a module for a shape and merge it into whatever is on disk."""
        name = entity or shape.name
        with self._lock:
            history = self._generations.setdefault(name, [])
            previous = history[-1] if history else None

            # Short-circuit before emitting. The emitted source embeds the
            # revision number, so generating first and diffing after would make
            # every regeneration differ from itself and inflate history forever.
            if (
                previous is not None
                and previous.shape_digest == shape.digest
                and (self.root / f"{previous.module_name}.py").exists()
            ):
                return previous

            revision = (previous.revision + 1) if previous else 1

            emitted: GeneratedModule = PythonEmitter(options).emit(
                shape.rename(name), revision=revision, source=shape.source
            )
            path = self.root / emitted.filename
            merge: MergeResult | None = None
            source = emitted.source

            if path.exists():
                on_disk = path.read_text(encoding="utf-8")
                base = previous.source if previous else None
                merge = merge_sources(
                    on_disk, emitted.source, base,
                    policy=policy if policy is not None else self.conflict_policy,
                    label=emitted.filename,
                )
                source = merge.source
                if not merge.changed and merge.clean and previous is not None:
                    # Nothing moved: do not inflate history with a no-op.
                    return previous

            storage = StorageName.create("codegen", name, ext="py")
            generation = Generation(
                entity=name,
                module_name=emitted.module_name,
                revision=revision,
                source=source,
                shape_digest=shape.digest,
                storage=storage,
                decisions=tuple(merge.decisions) if merge else (),
                conflicts=tuple(merge.conflicts) if merge else (),
            )
            if write:
                path.write_text(source, encoding="utf-8")
            history.append(generation)
            self._emit_event(generation, emitted, merge)
            return generation

    def generate_proto(self, shape: DataShape, *, entity: str = "", write: bool = True) -> ProtoSchema:
        """Emit a .proto for a shape, reusing the recorded field allocation."""
        name = entity or shape.name
        with self._lock:
            allocation = self._allocations.get(name)
            schema = self.proto_emitter.emit(shape.rename(name), allocation=allocation)
            self._allocations[name] = schema.allocation
            if write:
                (self.root / schema.filename).write_text(schema.source, encoding="utf-8")
            if self.flow is not None:
                self.flow.emit(
                    EventKind.CODEGEN,
                    actor=self.actor,
                    paths=[f"proto.{name}"],
                    payload={
                        "entity": name,
                        "message": schema.message,
                        "fields": len(schema.allocation.fields),
                        "reserved": schema.allocation.reserved(),
                        "shape_digest": shape.digest,
                    },
                )
            return schema

    def generate_all(self, shapes: Mapping[str, DataShape], **kwargs: Any) -> list[Generation]:
        return [self.generate(shape, entity=entity, **kwargs) for entity, shape in shapes.items()]

    # -- history ---------------------------------------------------------

    def history(self, entity: str) -> list[Generation]:
        return list(self._generations.get(entity, ()))

    def current(self, entity: str) -> Generation | None:
        history = self._generations.get(entity)
        return history[-1] if history else None

    def entities(self) -> list[str]:
        return sorted(self._generations)

    def conflicted(self) -> list[Generation]:
        return [g for e in self.entities() if (g := self.current(e)) and not g.clean]

    def restore(self, entity: str, revision: int) -> Generation:
        """Rewrite a module back to a previous generation.

        Recorded as a *new* generation carrying the restored source, so history
        stays append-only and the merge base for the next regeneration is the
        source that is actually on disk.
        """
        history = self._generations.get(entity, [])
        target = next((g for g in history if g.revision == revision), None)
        if target is None:
            raise CodegenError(f"no generation {revision} for {entity!r}")
        path = self.root / f"{target.module_name}.py"
        path.write_text(target.source, encoding="utf-8")
        restored = Generation(
            entity=entity,
            module_name=target.module_name,
            revision=history[-1].revision + 1,
            source=target.source,
            shape_digest=target.shape_digest,
            storage=StorageName.create("codegen", entity, ext="py"),
        )
        history.append(restored)
        self._invalidate(target.module_name)
        if self.flow is not None:
            self.flow.emit(
                EventKind.CODEGEN,
                actor=self.actor,
                paths=[restored.path],
                payload={
                    "entity": entity, "restored_from": revision,
                    "revision": restored.revision, "shape_digest": restored.shape_digest,
                },
                labels={"operation": "restore"},
            )
        return restored

    # -- loading ---------------------------------------------------------

    def load(self, entity: str, *, reload: bool = False) -> ModuleType:
        """Import a generated module from its file, under a private namespace."""
        generation = self.current(entity)
        if generation is None:
            raise CodegenError(f"nothing generated for {entity!r}")
        if not generation.clean:
            raise CodegenError(
                f"refusing to load {entity!r}: {len(generation.conflicts)} unresolved merge conflict(s)"
            )
        qualified = f"{MODULE_NAMESPACE}.{generation.module_name}"
        if not reload and (existing := self._loaded.get(qualified)) is not None:
            return existing

        path = self.root / f"{generation.module_name}.py"
        if not path.is_file():
            raise CodegenError(f"generated module is missing from disk: {path}")
        spec = importlib.util.spec_from_file_location(qualified, path)
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise CodegenError(f"cannot build an import spec for {path}")
        module = importlib.util.module_from_spec(spec)
        # Registered before exec because dataclasses resolves annotations via
        # sys.modules[cls.__module__] while the class body is executing.
        sys.modules[qualified] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            sys.modules.pop(qualified, None)
            raise CodegenError(f"generated module {entity!r} failed to import: {exc}") from exc
        self._loaded[qualified] = module
        return module

    def _invalidate(self, module_name: str) -> None:
        qualified = f"{MODULE_NAMESPACE}.{module_name}"
        self._loaded.pop(qualified, None)
        sys.modules.pop(qualified, None)

    def unload_all(self) -> None:
        for qualified in list(self._loaded):
            self._loaded.pop(qualified, None)
            sys.modules.pop(qualified, None)

    # -- internals -------------------------------------------------------

    def _ensure_package(self) -> None:
        init = self.root / "__init__.py"
        if not init.exists():
            init.write_text(
                '"""Modules generated by Gratimos.\n\n'
                "Everything here is written by the code generator and merged at the AST\n"
                "level on regeneration. Hand edits survive, but mark them with\n"
                "`# gratimos:keep` to make that explicit and unconditional.\n"
                '"""\n',
                encoding="utf-8",
            )
        parent = sys.modules.get(MODULE_NAMESPACE)
        if parent is None:
            package = ModuleType(MODULE_NAMESPACE)
            package.__path__ = [str(self.root)]  # type: ignore[attr-defined]
            package.__doc__ = "Namespace for Gratimos-generated modules."
            sys.modules[MODULE_NAMESPACE] = package

    def _emit_event(
        self, generation: Generation, emitted: GeneratedModule, merge: MergeResult | None
    ) -> None:
        if self.flow is None:
            return
        event = self.flow.emit(
            EventKind.CODEGEN if merge is None else EventKind.MERGE,
            actor=self.actor,
            paths=[generation.path],
            payload={
                "entity": generation.entity,
                "module": generation.module_name,
                "revision": generation.revision,
                "shape_digest": generation.shape_digest,
                "symbols": list(emitted.symbols),
                "clean": generation.clean,
                "conflicts": [d.name for d in generation.conflicts],
                "decisions": merge.by_resolution() if merge else {},
                "warnings": emitted.warnings,
            },
            storage=generation.storage,
        )
        object.__setattr__(generation, "event_id", event.id)

    # -- reporting -------------------------------------------------------

    def report(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "entities": self.entities(),
            "generations": sum(len(h) for h in self._generations.values()),
            "conflicted": [g.entity for g in self.conflicted()],
            "loaded": sorted(self._loaded),
            "current": {
                entity: {"revision": g.revision, "digest": g.shape_digest, "clean": g.clean}
                for entity in self.entities() if (g := self.current(entity)) is not None
            },
        }

    def __len__(self) -> int:
        return len(self._generations)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<ModuleRegistry root={self.root} entities={len(self._generations)}>"
