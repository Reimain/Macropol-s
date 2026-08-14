"""The migration ledger: data-as-code mutations, auditable and reversible.

Shapes evolve because the data evolved, which means the kernel is constantly
proposing schema changes to itself. Left unrecorded that is indistinguishable
from drift. The ledger gives every change an identity, a parent, an upgrade
path, and — wherever it is possible — a downgrade path.

The revision chain is Alembic-shaped on purpose (``revision`` /
``down_revision`` / ``upgrade`` / ``downgrade``), so a change proposed here can
be handed to Alembic without translation. See :mod:`.alembic_adapter`.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from ..contextflow import ContextFlow, EventKind
from ..errors import MigrationError
from ..ids import digest, iso, new_id, now_ns, slug
from ..meta import ChangeKind, DataShape, ShapeChange, TypeTag


class OpKind(str, Enum):
    """The mutations the ledger knows how to describe."""

    CREATE_ENTITY = "create_entity"
    DROP_ENTITY = "drop_entity"
    ADD_FIELD = "add_field"
    DROP_FIELD = "drop_field"
    ALTER_TYPE = "alter_type"
    ALTER_NULLABLE = "alter_nullable"
    RENAME_FIELD = "rename_field"


#: Operations that cannot be undone without the data that was discarded.
LOSSY = frozenset({OpKind.DROP_ENTITY, OpKind.DROP_FIELD})


@dataclass(frozen=True, slots=True)
class Operation:
    """One reversible (or explicitly irreversible) change."""

    kind: OpKind
    entity: str
    field_name: str = ""
    from_type: str = ""
    to_type: str = ""
    nullable: bool | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    @property
    def lossy(self) -> bool:
        return self.kind in LOSSY

    def inverse(self) -> "Operation | None":
        """The operation that undoes this one, or ``None`` when there is none."""
        if self.kind is OpKind.ADD_FIELD:
            return Operation(OpKind.DROP_FIELD, self.entity, self.field_name, from_type=self.to_type)
        if self.kind is OpKind.CREATE_ENTITY:
            return Operation(OpKind.DROP_ENTITY, self.entity)
        if self.kind is OpKind.ALTER_TYPE:
            return Operation(OpKind.ALTER_TYPE, self.entity, self.field_name,
                             from_type=self.to_type, to_type=self.from_type)
        if self.kind is OpKind.ALTER_NULLABLE:
            return Operation(OpKind.ALTER_NULLABLE, self.entity, self.field_name,
                             nullable=not self.nullable)
        if self.kind is OpKind.RENAME_FIELD:
            return Operation(OpKind.RENAME_FIELD, self.entity,
                             field_name=str(self.detail.get("to", "")),
                             detail={"to": self.field_name})
        # DROP_FIELD / DROP_ENTITY: the structure is recoverable, the data is not.
        return None

    def describe(self) -> str:
        if self.kind is OpKind.ADD_FIELD:
            return f"add {self.entity}.{self.field_name} ({self.to_type})"
        if self.kind is OpKind.DROP_FIELD:
            return f"drop {self.entity}.{self.field_name} (was {self.from_type})"
        if self.kind is OpKind.ALTER_TYPE:
            return f"alter {self.entity}.{self.field_name}: {self.from_type} -> {self.to_type}"
        if self.kind is OpKind.ALTER_NULLABLE:
            return f"{'allow' if self.nullable else 'forbid'} nulls on {self.entity}.{self.field_name}"
        if self.kind is OpKind.CREATE_ENTITY:
            return f"create {self.entity}"
        if self.kind is OpKind.DROP_ENTITY:
            return f"drop {self.entity}"
        return f"{self.kind.value} {self.entity}.{self.field_name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "entity": self.entity,
            "field": self.field_name,
            "from_type": self.from_type,
            "to_type": self.to_type,
            "nullable": self.nullable,
            "lossy": self.lossy,
            "detail": dict(self.detail),
            "describe": self.describe(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Operation":
        return cls(
            kind=OpKind(data["kind"]),
            entity=data["entity"],
            field_name=data.get("field", ""),
            from_type=data.get("from_type", ""),
            to_type=data.get("to_type", ""),
            nullable=data.get("nullable"),
            detail=dict(data.get("detail", {})),
        )


@dataclass(frozen=True, slots=True)
class Revision:
    """A ledger entry: one atomic set of operations with a parent."""

    revision: str
    down_revision: str
    entity: str
    operations: tuple[Operation, ...]
    message: str = ""
    shape_digest: str = ""
    applied: bool = False
    event_id: str = ""
    ts_ns: int = field(default_factory=now_ns)

    @property
    def reversible(self) -> bool:
        return all(op.inverse() is not None for op in self.operations)

    @property
    def lossy(self) -> bool:
        return any(op.lossy for op in self.operations)

    @property
    def at(self) -> str:
        return iso(self.ts_ns)

    def downgrade_ops(self) -> list[Operation]:
        """Inverse operations in reverse order; empty when not reversible."""
        if not self.reversible:
            return []
        return [op.inverse() for op in reversed(self.operations)]  # type: ignore[misc]

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "down_revision": self.down_revision,
            "entity": self.entity,
            "message": self.message,
            "shape_digest": self.shape_digest,
            "applied": self.applied,
            "reversible": self.reversible,
            "lossy": self.lossy,
            "at": self.at,
            "operations": [op.to_dict() for op in self.operations],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Revision":
        return cls(
            revision=data["revision"],
            down_revision=data.get("down_revision", ""),
            entity=data["entity"],
            operations=tuple(Operation.from_dict(o) for o in data.get("operations", ())),
            message=data.get("message", ""),
            shape_digest=data.get("shape_digest", ""),
            applied=bool(data.get("applied", False)),
            event_id=data.get("event_id", ""),
        )

    def __repr__(self) -> str:  # pragma: no cover - display only
        state = "applied" if self.applied else "pending"
        return f"<Revision {self.revision[:12]} {self.entity} ops={len(self.operations)} {state}>"


def operations_for(entity: str, changes: Sequence[ShapeChange], shape: DataShape | None = None) -> list[Operation]:
    """Translate shape changes into ledger operations."""
    out: list[Operation] = []
    for change in changes:
        if change.kind is ChangeKind.ADDED:
            out.append(Operation(OpKind.ADD_FIELD, entity, change.field, to_type=change.after))
        elif change.kind is ChangeKind.REMOVED:
            out.append(Operation(OpKind.DROP_FIELD, entity, change.field, from_type=change.before))
        elif change.kind in (ChangeKind.WIDENED, ChangeKind.NARROWED, ChangeKind.RETYPED):
            out.append(Operation(
                OpKind.ALTER_TYPE, entity, change.field,
                from_type=change.before, to_type=change.after,
                detail={"direction": change.kind.value},
            ))
        elif change.kind is ChangeKind.NULLABILITY:
            out.append(Operation(
                OpKind.ALTER_NULLABLE, entity, change.field,
                nullable=change.after == "true",
            ))
    return out


class MigrationLedger:
    """Append-only chain of revisions per entity, with replay and rollback."""

    def __init__(self, flow: ContextFlow | None = None, *, actor: str = "migrations") -> None:
        self.flow = flow
        self.actor = actor
        self._revisions: list[Revision] = []
        self._by_id: dict[str, int] = {}
        self._heads: dict[str, str] = {}
        self._lock = threading.RLock()

    # -- proposing -------------------------------------------------------

    def propose(
        self,
        entity: str,
        changes: Sequence[ShapeChange],
        *,
        shape: DataShape | None = None,
        message: str = "",
    ) -> Revision | None:
        """Record a pending revision for a set of shape changes."""
        operations = operations_for(entity, changes, shape)
        if not operations:
            return None
        return self.record(entity, operations, message=message,
                           shape_digest=shape.digest if shape else "")

    def create_entity(self, shape: DataShape, *, message: str = "") -> Revision:
        """The initial revision for a newly discovered entity."""
        operations = [Operation(OpKind.CREATE_ENTITY, shape.name,
                                detail={"fields": len(shape.fields)})]
        operations += [
            Operation(OpKind.ADD_FIELD, shape.name, f.name, to_type=f.tag.value,
                      nullable=f.nullable)
            for f in shape.fields
        ]
        return self.record(shape.name, operations, shape_digest=shape.digest,
                           message=message or f"create {shape.name}")

    def record(
        self,
        entity: str,
        operations: Sequence[Operation],
        *,
        message: str = "",
        shape_digest: str = "",
    ) -> Revision:
        with self._lock:
            down = self._heads.get(entity, "")
            revision = Revision(
                revision=_revision_id(entity, operations, down),
                down_revision=down,
                entity=entity,
                operations=tuple(operations),
                message=message or "; ".join(op.describe() for op in operations)[:200],
                shape_digest=shape_digest,
            )
            self._revisions.append(revision)
            self._by_id[revision.revision] = len(self._revisions) - 1
            self._heads[entity] = revision.revision
            self._emit(revision, "propose")
            return revision

    # -- applying --------------------------------------------------------

    def apply(self, revision_id: str) -> Revision:
        """Mark a revision applied. The ledger records; it does not execute DDL."""
        with self._lock:
            index = self._by_id.get(revision_id)
            if index is None:
                raise MigrationError(f"unknown revision: {revision_id!r}")
            revision = self._revisions[index]
            if revision.applied:
                return revision
            pending = self.pending(revision.entity)
            if pending and pending[0].revision != revision_id:
                raise MigrationError(
                    f"revision {revision_id[:12]!r} is out of order: "
                    f"{pending[0].revision[:12]!r} must be applied first"
                )
            applied = Revision(
                revision=revision.revision, down_revision=revision.down_revision,
                entity=revision.entity, operations=revision.operations,
                message=revision.message, shape_digest=revision.shape_digest,
                applied=True, event_id=revision.event_id, ts_ns=revision.ts_ns,
            )
            self._revisions[index] = applied
            self._emit(applied, "apply")
            return applied

    def apply_all(self, entity: str = "") -> list[Revision]:
        return [self.apply(r.revision) for r in self.pending(entity)]

    def revert(self, revision_id: str, *, force: bool = False) -> Revision:
        """Record the inverse of an applied revision as a new revision."""
        index = self._by_id.get(revision_id)
        if index is None:
            raise MigrationError(f"unknown revision: {revision_id!r}")
        revision = self._revisions[index]
        if not revision.applied:
            raise MigrationError(f"revision {revision_id[:12]!r} was never applied")
        inverse = revision.downgrade_ops()
        if not inverse and not force:
            lossy = [op.describe() for op in revision.operations if op.inverse() is None]
            raise MigrationError(
                f"revision {revision_id[:12]!r} is not reversible: {'; '.join(lossy)}. "
                "Pass force=True to record a structural-only revert."
            )
        if not inverse:
            inverse = [op.inverse() or Operation(OpKind.ADD_FIELD, op.entity, op.field_name,
                                                 to_type=op.from_type)
                       for op in reversed(revision.operations)]
        return self.record(
            revision.entity, inverse,
            message=f"revert {revision.revision[:12]}: {revision.message}"[:200],
        )

    # -- reading ---------------------------------------------------------

    def revisions(self, entity: str = "") -> list[Revision]:
        return [r for r in self._revisions if not entity or r.entity == entity]

    def pending(self, entity: str = "") -> list[Revision]:
        return [r for r in self.revisions(entity) if not r.applied]

    def head(self, entity: str) -> str:
        return self._heads.get(entity, "")

    def get(self, revision_id: str) -> Revision | None:
        index = self._by_id.get(revision_id)
        return self._revisions[index] if index is not None else None

    def chain(self, entity: str) -> list[str]:
        """Revision ids for an entity, oldest first."""
        return [r.revision for r in self.revisions(entity)]

    def entities(self) -> list[str]:
        return sorted(self._heads)

    # -- persistence -----------------------------------------------------

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"revisions": [r.to_dict() for r in self._revisions]}, indent=2),
            encoding="utf-8",
        )
        return target

    def load(self, path: str | Path) -> int:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        with self._lock:
            self._revisions.clear()
            self._by_id.clear()
            self._heads.clear()
            for raw in data.get("revisions", []):
                revision = Revision.from_dict(raw)
                self._revisions.append(revision)
                self._by_id[revision.revision] = len(self._revisions) - 1
                self._heads[revision.entity] = revision.revision
        return len(self._revisions)

    def _emit(self, revision: Revision, operation: str) -> None:
        if self.flow is None:
            return
        event = self.flow.emit(
            EventKind.MIGRATION,
            actor=self.actor,
            paths=[f"migration.{slug(revision.entity)}"],
            payload={
                "revision": revision.revision,
                "down_revision": revision.down_revision,
                "entity": revision.entity,
                "operations": [op.describe() for op in revision.operations],
                "reversible": revision.reversible,
                "lossy": revision.lossy,
                "applied": revision.applied,
                "shape_digest": revision.shape_digest,
            },
            labels={"operation": operation},
        )
        object.__setattr__(revision, "event_id", event.id)

    def report(self) -> dict[str, Any]:
        return {
            "revisions": len(self._revisions),
            "applied": sum(1 for r in self._revisions if r.applied),
            "pending": len(self.pending()),
            "entities": self.entities(),
            "heads": dict(self._heads),
            "irreversible": [r.revision for r in self._revisions if not r.reversible],
        }

    def __iter__(self) -> Iterator[Revision]:
        return iter(list(self._revisions))

    def __len__(self) -> int:
        return len(self._revisions)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<MigrationLedger revisions={len(self._revisions)} pending={len(self.pending())}>"


def _revision_id(entity: str, operations: Sequence[Operation], down: str) -> str:
    """Content-addressed revision id — stable for the same change on the same parent."""
    return digest(
        {"entity": entity, "down": down, "ops": [op.to_dict() for op in operations]}, length=8
    )
