"""Discovering, approving, and applying transformations.

The transformations folder is the one place operators write code that the kernel
executes. So it is treated as a supply chain: every file is inspected before it
is trusted, its approved content is fingerprinted, and re-inspection happens
whenever that fingerprint moves. A transformation that changes on disk is a new
transformation and gets re-approved from scratch.

Applying one is recorded on the ContextFlow with the digests of the payload
going in and coming out, so the effect of a transformation can be located,
audited, and rolled back like any other mutation.
"""

from __future__ import annotations

import fnmatch
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..contextflow import ContextFlow, EventKind
from ..errors import TransformError
from ..ids import digest, new_id, now_ns, slug
from ..meta import Wrapped
from .policy import DEFAULT_POLICY, SandboxPolicy
from .sandbox import ENTRYPOINT, Inspection, Sandbox, SandboxResult, inspect_file

#: Files ignored during discovery.
_SKIP_PREFIXES = ("_", ".")


@dataclass(slots=True)
class Transformation:
    """One approved (or rejected) transformation file."""

    name: str
    path: Path
    inspection: Inspection
    source_digest: str
    applies_to: str = "*"
    description: str = ""
    version: str = "0"
    requires: tuple[str, ...] = ()
    id: str = field(default_factory=lambda: new_id("txf"))
    runs: int = 0
    failures: int = 0

    @property
    def approved(self) -> bool:
        return self.inspection.approved

    def matches(self, entity: str) -> bool:
        return fnmatch.fnmatch(entity, self.applies_to)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "path": str(self.path),
            "approved": self.approved,
            "applies_to": self.applies_to,
            "description": self.description,
            "version": self.version,
            "source_digest": self.source_digest,
            "imports": self.inspection.imports,
            "violations": self.inspection.reasons,
            "runs": self.runs,
            "failures": self.failures,
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        state = "approved" if self.approved else f"rejected ({len(self.inspection.violations)})"
        return f"<Transformation {self.name} {state} applies_to={self.applies_to!r}>"


@dataclass(slots=True)
class TransformOutcome:
    """The result of applying one transformation to one payload."""

    transformation: str
    entity: str
    ok: bool
    payload: Wrapped | None = None
    result: SandboxResult | None = None
    error: str = ""
    event_id: str = ""
    ts_ns: int = field(default_factory=now_ns)

    def to_dict(self) -> dict[str, Any]:
        return {
            "transformation": self.transformation,
            "entity": self.entity,
            "ok": self.ok,
            "error": self.error,
            "event_id": self.event_id,
            "result": self.result.to_dict() if self.result else None,
            "payload_id": self.payload.id if self.payload else None,
        }


class TransformRegistry:
    """The transformations folder, inspected and callable."""

    def __init__(
        self,
        root: str | Path,
        *,
        policy: SandboxPolicy = DEFAULT_POLICY,
        flow: ContextFlow | None = None,
        actor: str = "transforms",
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.policy = policy
        self.flow = flow
        self.actor = actor
        self.sandbox = Sandbox(policy)
        self._transforms: dict[str, Transformation] = {}
        self._lock = threading.RLock()

    # -- discovery -------------------------------------------------------

    def discover(self, *, reinspect: bool = True) -> list[Transformation]:
        """Scan the folder, inspecting anything new or changed."""
        found: list[Transformation] = []
        for path in sorted(self.root.glob("*.py")):
            if path.name.startswith(_SKIP_PREFIXES):
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except OSError:
                continue
            fingerprint = digest(source)
            with self._lock:
                existing = next(
                    (t for t in self._transforms.values() if t.path == path), None
                )
                if existing is not None and existing.source_digest == fingerprint and not reinspect:
                    found.append(existing)
                    continue
                if existing is not None and existing.source_digest == fingerprint:
                    found.append(existing)
                    continue
                found.append(self._register(path, source, fingerprint))
        return found

    def _register(self, path: Path, source: str, fingerprint: str) -> Transformation:
        inspection = inspect_file(path, policy=self.policy)
        metadata = inspection.metadata
        name = str(metadata.get("NAME") or slug(path.stem))
        transformation = Transformation(
            name=name,
            path=path,
            inspection=inspection,
            source_digest=fingerprint,
            applies_to=str(metadata.get("APPLIES_TO") or "*"),
            description=str(metadata.get("DESCRIPTION") or ""),
            version=str(metadata.get("VERSION") or "0"),
            requires=tuple(metadata.get("REQUIRES") or ()),
        )
        self._transforms[name] = transformation
        if self.flow is not None:
            self.flow.emit(
                EventKind.POLICY,
                actor=self.actor,
                paths=[f"transform.{slug(name)}"],
                payload={
                    "transformation": name,
                    "path": str(path),
                    "approved": transformation.approved,
                    "source_digest": fingerprint,
                    "applies_to": transformation.applies_to,
                    "violations": inspection.reasons,
                    "imports": inspection.imports,
                },
                labels={"operation": "inspect"},
            )
        return transformation

    # -- access ----------------------------------------------------------

    def get(self, name: str) -> Transformation | None:
        return self._transforms.get(name)

    def approved(self) -> list[Transformation]:
        return [t for t in self._transforms.values() if t.approved]

    def rejected(self) -> list[Transformation]:
        return [t for t in self._transforms.values() if not t.approved]

    def for_entity(self, entity: str) -> list[Transformation]:
        return [t for t in self.approved() if t.matches(entity)]

    def names(self) -> list[str]:
        return sorted(self._transforms)

    # -- application -----------------------------------------------------

    def apply(
        self,
        transformation: Transformation | str,
        payload: Wrapped,
        *,
        context: Mapping[str, Any] | None = None,
        reinfer: bool = True,
    ) -> TransformOutcome:
        """Run one transformation over one payload."""
        target = (
            transformation if isinstance(transformation, Transformation)
            else self._transforms.get(transformation)
        )
        if target is None:
            raise TransformError(f"unknown transformation: {transformation!r}")
        if not target.approved:
            return TransformOutcome(
                transformation=target.name, entity=payload.name, ok=False,
                error=f"transformation is not approved: {'; '.join(target.inspection.reasons[:2])}",
            )

        merged_context = {
            "entity": payload.name,
            "shape": payload.shape.to_dict(),
            "source": payload.provenance.source,
            "rows": payload.rows,
            **dict(context or {}),
        }
        before = payload.digest
        result = self.sandbox.run(
            target.path, payload.records(), context=merged_context, inspection=target.inspection
        )
        target.runs += 1

        if not result.ok:
            target.failures += 1
            outcome = TransformOutcome(
                transformation=target.name, entity=payload.name, ok=False,
                result=result, error=result.error,
            )
            self._emit(target, payload, outcome, before, "")
            return outcome

        produced = Wrapped.shaped(
            result.records,
            payload.shape,
            provenance=payload.provenance.descend(payload.id, op="transform", by=target.name),
        )
        if reinfer:
            produced = produced.reinfer()
        outcome = TransformOutcome(
            transformation=target.name, entity=payload.name, ok=True,
            payload=produced, result=result,
        )
        self._emit(target, payload, outcome, before, produced.digest)
        return outcome

    def apply_all(
        self, payload: Wrapped, *, context: Mapping[str, Any] | None = None
    ) -> list[TransformOutcome]:
        """Run every transformation matching this entity, in sequence.

        Each transformation sees the output of the last, so ordering is by
        name — deterministic, and visible in the folder listing.
        """
        outcomes: list[TransformOutcome] = []
        current = payload
        for transformation in sorted(self.for_entity(payload.name), key=lambda t: t.name):
            outcome = self.apply(transformation, current, context=context)
            outcomes.append(outcome)
            if not outcome.ok:
                break
            current = outcome.payload or current
        return outcomes

    def _emit(
        self, target: Transformation, payload: Wrapped, outcome: TransformOutcome,
        before: str, after: str,
    ) -> None:
        if self.flow is None:
            return
        event = self.flow.emit(
            EventKind.TRANSFORM,
            actor=self.actor,
            paths=[f"data.{payload.name}"],
            payload={
                "transformation": target.name,
                "version": target.version,
                "source_digest": target.source_digest,
                "entity": payload.name,
                "ok": outcome.ok,
                "rows_in": outcome.result.rows_in if outcome.result else payload.rows,
                "rows_out": outcome.result.rows_out if outcome.result else 0,
                "payload_before": before,
                "payload_after": after,
                "shape_digest": outcome.payload.shape.digest if outcome.payload else payload.shape.digest,
                "error": outcome.error,
                "duration_s": outcome.result.duration_s if outcome.result else 0.0,
            },
            labels={"transformation": target.name},
        )
        outcome.event_id = event.id

    # -- reporting -------------------------------------------------------

    def report(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "discovered": len(self._transforms),
            "approved": [t.name for t in self.approved()],
            "rejected": {t.name: t.inspection.reasons for t in self.rejected()},
            "policy": self.policy.to_dict(),
            "runs": {t.name: {"runs": t.runs, "failures": t.failures} for t in self._transforms.values()},
        }

    def __len__(self) -> int:
        return len(self._transforms)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<TransformRegistry root={self.root} approved={len(self.approved())}>"


TRANSFORM_TEMPLATE = '''"""Transformation: {name}

Runs in the Gratimos sandbox. Available imports are limited to the standard
library data modules; there is no filesystem, network, or subprocess access.
"""

NAME = "{name}"
APPLIES_TO = "{applies_to}"
DESCRIPTION = "{description}"
VERSION = "1"


def {entrypoint}(records, context):
    """Return the transformed records.

    Args:
        records: list of dicts, one per row of the incoming payload.
        context: entity name, the observed shape, and a `log(*parts)` helper.

    Returns:
        A list of dicts. Shapes are re-inferred from what you return, so it is
        fine to add, drop, or retype fields.
    """
    context["log"]("transforming", len(records), "rows of", context["entity"])
    return list(records)
'''


def scaffold(root: str | Path, name: str, *, applies_to: str = "*", description: str = "") -> Path:
    """Write a starter transformation file."""
    path = Path(root).expanduser().resolve() / f"{slug(name)}.py"
    if path.exists():
        raise TransformError(f"transformation already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        TRANSFORM_TEMPLATE.format(
            name=name, applies_to=applies_to,
            description=description or f"Transformation for {applies_to}",
            entrypoint=ENTRYPOINT,
        ),
        encoding="utf-8",
    )
    return path
