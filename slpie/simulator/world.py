"""The simulated world — an environment manifest, made real on a temp filesystem.

`SimulatedWorld.build(manifest)` writes every local declaration out as genuine
artifacts and binds the ordinary :class:`FilesystemConnector` to them. From that
point the platform cannot tell it is in a simulation: the discoverers are the
same objects, reading real bytes off a real disk, through the same connector
class a live target uses.

The point of the exercise is the last line of :meth:`bind`. Given the same
manifest with `target: live`, the resolver builds connectors pointed at the real
checkout instead — and nothing above the binding layer is written differently
for the two cases, which is why proving a case here is evidence about what will
happen there.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..binding.guard import Guard
from ..binding.resolver import Binding, FilesystemConnector, Resolver
from ..binding.target import TargetSelection
from ..environment.manifest import Declaration, DeclarationKind, Manifest, Target
from ..errors import BindingError
from .clock import ControlledClock
from .faults import FaultInjector
from .materialize import Artifact, container_artifacts, materialize


@dataclass(slots=True)
class SimulatedWorld:
    """A materialised environment, and the bindings that reach into it."""

    manifest: Manifest
    root: Path
    artifacts: list[Artifact] = field(default_factory=list)
    clock: ControlledClock = field(default_factory=ControlledClock)
    faults: FaultInjector = field(default_factory=FaultInjector)
    resolver: Resolver | None = None
    _owned: bool = True

    # -- construction ----------------------------------------------------

    @classmethod
    def build(
        cls,
        manifest: Manifest,
        *,
        root: str | Path | None = None,
        packages: Sequence[Mapping[str, Any]] = (),
        containers: bool = True,
        git: bool = True,
    ) -> "SimulatedWorld":
        """Materialise a manifest as real files.

        ``root`` is created if not given, and removed by :meth:`tear_down`.
        Passing one keeps the world after the process exits, which is how you
        inspect what the simulator actually wrote when a case surprises you.
        """
        owned = root is None
        base = Path(root) if root is not None else Path(tempfile.mkdtemp(prefix="slpie-world-"))
        base.mkdir(parents=True, exist_ok=True)

        world = cls(manifest=manifest, root=base, _owned=owned)
        for declaration in manifest.declarations:
            written = materialize(declaration, base, packages=packages, git=git)
            world.artifacts.extend(written)
            if containers and declaration.kind is DeclarationKind.CODEBASE:
                world.artifacts.extend(container_artifacts(base / declaration.name))
        return world

    def bind(self, *, confirmed: bool = False) -> Resolver:
        """Bind every declaration, honouring the manifest's own target.

        A world built for a `target: live` manifest binds to the real locations.
        That is not a contradiction — the simulator materialised a world, and
        the manifest still decides who answers. Keeping that decision in one
        place is what stops "simulated" from leaking upward as a special case.
        """
        selection = TargetSelection.from_manifest(self.manifest, confirmed=confirmed)
        resolver = Resolver(guard=Guard(selection=selection))

        for kind in DeclarationKind:
            resolver.register(kind.value, Target.SIMULATED, self._simulated_factory)
            resolver.register(kind.value, Target.LIVE, self._live_factory)

        resolver.bind_all(self.manifest)
        self.resolver = resolver
        return resolver

    def _simulated_factory(
        self, declaration: Declaration, *, target: Target, **_context: Any
    ):
        connector = FilesystemConnector(
            logical_root=declaration.location.rstrip("/"),
            physical_root=self.root / declaration.name,
            mode="simulated",
        )
        return self.faults.wrap(declaration.name, connector)

    def _live_factory(self, declaration: Declaration, *, target: Target, **_context: Any):
        if not declaration.is_local:
            from ..binding.connector import RefusingConnector

            return RefusingConnector(
                element=declaration.name,
                reason=f"no live connector for {declaration.location}",
                mode="live",
            )
        connector = FilesystemConnector(
            logical_root=declaration.location.rstrip("/"),
            physical_root=Path(declaration.location),
            mode="live",
        )
        return self.faults.wrap(declaration.name, connector)

    # -- inspection ------------------------------------------------------

    def path_for(self, element: str) -> Path:
        return self.root / element

    def files(self, element: str = "") -> tuple[Path, ...]:
        base = self.root / element if element else self.root
        return tuple(sorted(
            path for path in base.rglob("*")
            if path.is_file() and ".git" not in path.parts
        ))

    def artifacts_of(self, kind: str) -> tuple[Artifact, ...]:
        return tuple(artifact for artifact in self.artifacts if artifact.kind == kind)

    def read(self, element: str, relative: str) -> str:
        """Read a materialised file directly — for asserting what was written."""
        path = self.root / element / relative
        if not path.is_file():
            raise BindingError(f"{relative} was not materialised for {element}")
        return path.read_text(encoding="utf-8")

    def rewrite(self, element: str, relative: str, content: str) -> Path:
        """Change a materialised file, so a rescan has something to notice.

        This is how incremental recomputation gets exercised: change one file in
        a real world and assert that only its region was recomputed.
        """
        path = self.root / element / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def remove(self, element: str, relative: str) -> None:
        path = self.root / element / relative
        if path.is_file():
            path.unlink()

    # -- lifecycle -------------------------------------------------------

    def tear_down(self) -> None:
        """Remove the world, if we created its directory."""
        if self._owned and self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> "SimulatedWorld":
        return self

    def __exit__(self, *_exception: Any) -> None:
        self.tear_down()

    def to_dict(self) -> dict[str, Any]:
        return {
            "environment": self.manifest.environment,
            "target": self.manifest.target.value,
            "root": str(self.root),
            "artifacts": len(self.artifacts),
            "kinds": sorted({artifact.kind for artifact in self.artifacts}),
            "faults": self.faults.to_dict()["faults"],
            "clock": self.clock.instant,
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        return (
            f"<SimulatedWorld {self.manifest.environment} "
            f"{len(self.artifacts)} artifacts at {self.root}>"
        )
