"""One registry, and built-ins register through it exactly like anyone else.

That is the whole design. If built-in discoverers took a private path, the
plugin seam would be tested only by third-party code nobody had written yet, and
its first real user would find every rough edge. Because the built-ins go
through :meth:`Registry.register`, the seam is exercised on every run.

An in-process plugin and an external one are indistinguishable to a caller:
:meth:`Registry.discover` returns the same :class:`DiscoveryResult` either way,
and a quarantined external plugin becomes a gap rather than an exception.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from ..core.commands import QuarantinePlugin, RegisterPlugin
from ..domain.evidence import EvidenceKind
from ..domain.finding import Gap, GapKind
from ..errors import PluginError, PluginProtocolError, PluginQuarantined
from .external import ExternalPlugin
from .manifest import MANIFEST_FILENAME, ExtensionPoint, PluginManifest, Runtime
from .protocol import DiscoveryResult, Observation
from .sandbox import DEFAULT_LIMITS, Limits

#: An in-process plugin is any callable with this shape.
InProcess = Callable[..., DiscoveryResult]


@dataclass(slots=True)
class Registration:
    """One registered extension, in process or out of it."""

    manifest: PluginManifest
    handler: InProcess | None = None
    external: ExternalPlugin | None = None
    calls: int = 0
    failures: int = 0
    observations: int = 0

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def quarantined(self) -> bool:
        return bool(self.external and self.external.quarantined)

    def status(self) -> dict[str, Any]:
        if self.external is not None:
            return {**self.external.status(), "observations": self.observations}
        return {
            "id": self.id,
            "version": self.manifest.version,
            "runtime": self.manifest.runtime.value,
            "alive": True,
            "quarantined": False,
            "calls": self.calls,
            "failures": self.failures,
            "observations": self.observations,
        }


class Registry:
    """Holds every extension the platform knows about."""

    def __init__(self, *, commands: Any = None, limits: Limits = DEFAULT_LIMITS) -> None:
        self.commands = commands
        self.limits = limits
        self._plugins: dict[str, Registration] = {}
        self._gaps: list[Gap] = []
        self._lock = threading.RLock()

    # -- registration ----------------------------------------------------

    def register(
        self,
        manifest: PluginManifest,
        handler: InProcess | None = None,
        *,
        limits: Limits | None = None,
    ) -> Registration:
        """Register one extension. The only way anything gets in.

        A built-in passes a manifest and a callable; an external plugin passes a
        manifest whose runtime is `external`. There is no third path.
        """
        with self._lock:
            if manifest.id in self._plugins:
                raise PluginError(f"plugin {manifest.id!r} is already registered")

            if manifest.external:
                if handler is not None:
                    raise PluginError(
                        f"{manifest.id!r} declares an external runtime but was given "
                        f"an in-process handler"
                    )
                registration = Registration(
                    manifest=manifest,
                    external=ExternalPlugin(
                        manifest=manifest, limits=limits or self.limits
                    ),
                )
            else:
                if handler is None:
                    raise PluginError(f"in-process plugin {manifest.id!r} has no handler")
                registration = Registration(manifest=manifest, handler=handler)

            self._plugins[manifest.id] = registration

        if self.commands is not None:
            self.commands.dispatch(RegisterPlugin(
                plugin_id=manifest.id, version=manifest.version,
                kinds=(manifest.kind.value,), runtime=manifest.runtime.value,
            ))
        return registration

    def register_builtin(
        self,
        plugin_id: str,
        handler: InProcess,
        *,
        kind: ExtensionPoint = ExtensionPoint.DISCOVERER,
        handles: Sequence[str] = (),
        produces: Sequence[str] = (),
        evidence_kinds: Sequence[EvidenceKind] = (),
        priority: int = 100,
        version: str = "1",
        description: str = "",
    ) -> Registration:
        """Convenience for built-ins — the same path, less ceremony."""
        return self.register(
            PluginManifest(
                id=plugin_id, version=version, kind=kind,
                runtime=Runtime.IN_PROCESS, entrypoint=(plugin_id,),
                handles=tuple(handles), produces=tuple(produces),
                evidence_kinds=tuple(evidence_kinds), priority=priority,
                description=description, source="built-in",
            ),
            handler,
        )

    def load(self, path: str | Path, *, limits: Limits | None = None) -> Registration:
        """Register an external plugin from its manifest on disk."""
        return self.register(PluginManifest.load(path), limits=limits)

    def discover_plugins(self, directory: str | Path) -> tuple[Registration, ...]:
        """Load every plugin under a directory.

        A directory whose manifest is broken is skipped with a recorded gap
        rather than aborting the load — one malformed third-party plugin must
        not stop the platform from starting.
        """
        found: list[Registration] = []
        root = Path(directory)
        if not root.is_dir():
            return ()

        for manifest_path in sorted(root.glob(f"*/{MANIFEST_FILENAME}")):
            try:
                found.append(self.load(manifest_path))
            except PluginError as error:
                self._gaps.append(Gap(
                    kind=GapKind.PLUGIN_UNAVAILABLE,
                    subject=str(manifest_path.parent.name),
                    detail=f"could not load plugin: {error}",
                    remediation=f"fix {manifest_path}",
                    confidence_impact=0.1,
                ))
        return tuple(found)

    def unregister(self, plugin_id: str) -> bool:
        with self._lock:
            registration = self._plugins.pop(plugin_id, None)
        if registration is None:
            return False
        if registration.external is not None:
            registration.external.stop()
        return True

    # -- lookup ----------------------------------------------------------

    def get(self, plugin_id: str) -> Registration | None:
        return self._plugins.get(plugin_id)

    def of_kind(self, kind: ExtensionPoint) -> tuple[Registration, ...]:
        return tuple(
            sorted(
                (r for r in self._plugins.values() if r.manifest.kind is kind),
                key=lambda r: (r.manifest.priority, r.id),
            )
        )

    def for_uri(self, uri: str, *, kind: ExtensionPoint = ExtensionPoint.DISCOVERER) -> tuple[Registration, ...]:
        """Which plugins claim this file, in priority order.

        Several may claim one file, and that is correct: a `package.json` is
        read by the npm discoverer for dependencies and by the workspace
        discoverer for structure. They produce different observations about the
        same source, and both are evidence.
        """
        return tuple(
            registration for registration in self.of_kind(kind)
            if not registration.quarantined and registration.manifest.matches(uri)
        )

    @property
    def plugins(self) -> tuple[Registration, ...]:
        return tuple(self._plugins.values())

    def __len__(self) -> int:
        return len(self._plugins)

    def __iter__(self) -> Iterator[Registration]:
        return iter(self._plugins.values())

    def __contains__(self, plugin_id: object) -> bool:
        return plugin_id in self._plugins

    # -- running ---------------------------------------------------------

    def discover(self, uri: str, **context: Any) -> DiscoveryResult:
        """Run every plugin claiming a source, merging what they produce.

        A plugin that raises is quarantined and its failure recorded, but the
        others still run. One broken analyzer must not cost the platform the
        observations the rest produced from the same file.
        """
        observations: list[Observation] = []
        errors: list[str] = []

        for registration in self.for_uri(uri):
            try:
                result = self._run(registration, uri, **context)
            except PluginQuarantined as error:
                errors.append(str(error))
                self._record_quarantine(registration, str(error))
                continue
            except PluginProtocolError as error:
                registration.failures += 1
                errors.append(str(error))
                continue
            except Exception as error:  # a plugin may fail in any way at all
                registration.failures += 1
                reason = f"{type(error).__name__}: {error}"
                errors.append(f"{registration.id}: {reason}")
                if registration.external is not None:
                    registration.external.quarantine(reason)
                    self._record_quarantine(registration, reason)
                continue

            registration.calls += 1
            registration.observations += len(result.observations)
            observations.extend(result.observations)
            errors.extend(result.errors)

        return DiscoveryResult(observations=tuple(observations), errors=tuple(errors))

    def _run(self, registration: Registration, uri: str, **context: Any) -> DiscoveryResult:
        if registration.external is not None:
            return registration.external.discover(
                uri,
                digest=str(context.get("digest", "")),
                content=str(context.get("content", "")),
            )
        assert registration.handler is not None
        result = registration.handler(uri, **context)
        if not isinstance(result, DiscoveryResult):
            raise PluginProtocolError(
                f"{registration.id}: returned {type(result).__name__}, "
                f"expected a DiscoveryResult"
            )
        return self._validate(registration, result)

    def _validate(
        self, registration: Registration, result: DiscoveryResult
    ) -> DiscoveryResult:
        """Hold in-process plugins to the same evidence rules as external ones.

        A built-in that could claim any evidence kind while a third-party plugin
        could not would make the constraint a statement about trust rather than
        about correctness.
        """
        allowed = registration.manifest.evidence_kinds
        kept: list[Observation] = []
        errors: list[str] = list(result.errors)

        for observation in result.observations:
            if observation.evidence is None:
                errors.append(
                    f"{registration.id}: observation {observation.kind!r} carries no evidence"
                )
                continue
            if allowed and observation.evidence.kind not in allowed:
                errors.append(
                    f"{registration.id}: claimed undeclared evidence kind "
                    f"{observation.evidence.kind.value!r}"
                )
                continue
            kept.append(observation)

        return DiscoveryResult(
            observations=tuple(kept), errors=tuple(errors), skipped=result.skipped,
        )

    def _record_quarantine(self, registration: Registration, reason: str) -> None:
        if self.commands is not None:
            self.commands.dispatch(QuarantinePlugin(
                plugin_id=registration.id, reason=reason,
            ))

    # -- reporting -------------------------------------------------------

    def gaps(self) -> tuple[Gap, ...]:
        """Plugins that could not be loaded or had to be withdrawn."""
        found = list(self._gaps)
        for registration in self._plugins.values():
            if registration.external is not None and registration.external.quarantined:
                found.append(registration.external.gap())
        return tuple(found)

    def status(self) -> dict[str, Any]:
        return {
            "count": len(self._plugins),
            "plugins": [registration.status() for registration in self._plugins.values()],
            "gaps": [gap.to_dict() for gap in self.gaps()],
        }

    def shutdown(self) -> None:
        for registration in self._plugins.values():
            if registration.external is not None:
                registration.external.stop()

    def __enter__(self) -> "Registry":
        return self

    def __exit__(self, *_exception: Any) -> None:
        self.shutdown()

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Registry {len(self._plugins)} plugins>"
