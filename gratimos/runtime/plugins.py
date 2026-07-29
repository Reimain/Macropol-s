"""Plug and deplug, at runtime, without the kernel ever importing customer code.

The maintenance problem this solves: a customer needs behaviour nobody else
wants. The usual answers are a fork (which they maintain forever, and which
makes every upgrade a merge), a pull request into the product (which everyone
else then carries), or a config flag (which works until the third customer asks
for a fourth variant). None of those scale past a handful of deployments.

The answer here is that customer code is named in configuration and loaded by
dotted path at *activation* time. The kernel has no import of it, no compile-time
knowledge of it, and no test that depends on it. An upgrade cannot break a
customer's extension by changing an import, because there is no import.

```toml
[[extension]]
id      = "acme.jira-linker"
point   = "linker"
factory = "acme_slpie.linkers:JiraLinker"
enabled = true

[extension.settings]
project = "PAY"
```

Four properties that make this safe rather than merely flexible:

* **Failure is contained.** An extension that raises on load, on activation or
  in use is quarantined with its traceback recorded, and the kernel carries on
  degraded. One customer's broken plugin must not be an outage.
* **Deplug is real.** Extensions can be deactivated and reactivated at runtime,
  and deactivation runs their teardown. A "plugin system" you can only restart
  out of is a startup flag with extra steps.
* **Dependencies are declared and ordered.** An extension that requires another
  activates after it, and a cycle is refused at registration rather than
  producing an activation order that depends on dictionary iteration.
* **The contract is the extension point, not the class.** An extension declares
  which point it serves; the host checks the object provides that point's
  required methods before accepting it. Duck typing discovered at the first
  production call is not a contract.
"""

from __future__ import annotations

import importlib
import time
import traceback
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from ..errors import GratimosError
from .config import Config


class ExtensionError(GratimosError):
    """An extension could not be declared, loaded, activated or deactivated."""


class State(str, Enum):
    """Where an extension is in its lifecycle."""

    DECLARED = "declared"        # named in config, not yet loaded
    LOADED = "loaded"            # imported and constructed
    ACTIVE = "active"            # participating
    INACTIVE = "inactive"        # deplugged, teardown run, reactivatable
    QUARANTINED = "quarantined"  # it failed; the kernel carries on without it

    @property
    def usable(self) -> bool:
        return self is State.ACTIVE


@dataclass(frozen=True, slots=True)
class Point:
    """An extension point: a name, and the methods anything serving it must have.

    Stating the required methods is what turns "implements the protocol" from an
    aspiration into something checked at activation. The failure then names the
    missing method at start-up rather than raising `AttributeError` from inside
    a request six hours later.
    """

    name: str
    methods: tuple[str, ...] = ()
    description: str = ""
    singular: bool = False       # at most one active extension may serve it

    def satisfied_by(self, instance: Any) -> tuple[bool, str]:
        missing = [
            method for method in self.methods
            if not callable(getattr(instance, method, None))
        ]
        if missing:
            return False, (
                f"does not provide {', '.join(missing)}, which extension point "
                f"{self.name!r} requires"
            )
        return True, f"provides every method {self.name!r} requires"


@dataclass(frozen=True, slots=True)
class Extension:
    """One declared extension. Data until it is activated."""

    id: str
    point: str
    factory: str                       # "package.module:Attribute"
    enabled: bool = True
    requires: tuple[str, ...] = ()
    settings: Mapping[str, Any] = field(default_factory=dict)
    description: str = ""

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ExtensionError("an extension must have an id")
        if ":" not in self.factory:
            raise ExtensionError(
                f"extension {self.id!r} has factory {self.factory!r}; expected "
                f"'package.module:Attribute' so the kernel need not import it "
                f"until activation"
            )
        if self.id in self.requires:
            raise ExtensionError(f"extension {self.id!r} cannot require itself")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "point": self.point, "factory": self.factory,
            "enabled": self.enabled, "requires": list(self.requires),
            "description": self.description,
        }

    def __str__(self) -> str:
        return f"{self.id} ({self.point})"


@dataclass(slots=True)
class Slot:
    """An extension plus its live state. The host's bookkeeping."""

    extension: Extension
    state: State = State.DECLARED
    instance: Any = None
    error: str = ""
    traceback: str = ""
    activated_at: float = 0.0
    failures: int = 0

    @property
    def id(self) -> str:
        return self.extension.id

    @property
    def healthy(self) -> bool:
        return self.state is State.ACTIVE and not self.error

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.extension.to_dict(),
            "state": self.state.value,
            "error": self.error,
            "failures": self.failures,
            "activated_at": self.activated_at,
        }

    def __str__(self) -> str:
        suffix = f" — {self.error}" if self.error else ""
        return f"{self.id} [{self.state.value}]{suffix}"


class Host:
    """The plug/deplug plane. Owns lifecycle, isolation and ordering."""

    def __init__(self, points: Iterable[Point] = (), *, config: Config | None = None) -> None:
        self._points: dict[str, Point] = {}
        self._slots: dict[str, Slot] = {}
        self.config = config
        for point in points:
            self.define(point)

    # -- points -----------------------------------------------------------

    def define(self, point: Point) -> Point:
        if point.name in self._points:
            raise ExtensionError(f"extension point {point.name!r} is already defined")
        self._points[point.name] = point
        return point

    def point(self, name: str) -> Point | None:
        return self._points.get(name)

    @property
    def points(self) -> tuple[Point, ...]:
        return tuple(self._points.values())

    # -- declaration ------------------------------------------------------

    def declare(self, extension: Extension) -> Slot:
        """Register an extension. Nothing is imported yet."""
        if extension.id in self._slots:
            raise ExtensionError(f"extension {extension.id!r} is already declared")
        if extension.point not in self._points:
            raise ExtensionError(
                f"extension {extension.id!r} serves unknown point "
                f"{extension.point!r} (defined: "
                f"{', '.join(sorted(self._points)) or 'none'})"
            )
        slot = Slot(extension=extension)
        self._slots[extension.id] = slot
        self._check_cycles()
        return slot

    def declare_all(self, extensions: Iterable[Extension]) -> tuple[Slot, ...]:
        return tuple(self.declare(extension) for extension in extensions)

    def _check_cycles(self) -> None:
        def walk(name: str, seen: tuple[str, ...]) -> None:
            if name in seen:
                chain = " → ".join([*seen[seen.index(name):], name])
                raise ExtensionError(f"extension dependency cycle: {chain}")
            slot = self._slots.get(name)
            if slot is None:
                return
            for dependency in slot.extension.requires:
                walk(dependency, (*seen, name))

        for identifier in self._slots:
            walk(identifier, ())

    def order(self) -> tuple[str, ...]:
        """Activation order: dependencies first, then declaration order.

        Stable rather than merely correct — an activation order that varies run
        to run makes an intermittent failure impossible to reproduce.
        """
        ordered: list[str] = []
        placed: set[str] = set()

        def place(name: str) -> None:
            if name in placed or name not in self._slots:
                return
            placed.add(name)
            for dependency in self._slots[name].extension.requires:
                place(dependency)
            ordered.append(name)

        for identifier in self._slots:
            place(identifier)
        return tuple(ordered)

    # -- lifecycle --------------------------------------------------------

    def plug(self, identifier: str) -> Slot:
        """Import, construct, check the contract, and activate. Never raises.

        Contained on purpose: a customer's extension failing to import is a
        degraded deployment, not a dead one. The failure is recorded on the slot
        with its traceback so `health()` can report it and an operator can fix
        it without reading the process log.
        """
        slot = self._require(identifier)
        if slot.state is State.ACTIVE:
            return slot
        if not slot.extension.enabled:
            slot.state = State.INACTIVE
            slot.error = "disabled in configuration"
            return slot

        for dependency in slot.extension.requires:
            required = self._slots.get(dependency)
            if required is None or not required.healthy:
                return self._quarantine(
                    slot, f"requires {dependency!r}, which is not active", ""
                )

        try:
            instance = self._construct(slot)
        except Exception as exc:  # noqa: BLE001 - customer code, anything is possible
            return self._quarantine(slot, str(exc) or exc.__class__.__name__,
                                    traceback.format_exc())

        point = self._points[slot.extension.point]
        ok, reason = point.satisfied_by(instance)
        if not ok:
            return self._quarantine(slot, reason, "")

        if point.singular:
            others = [
                other for other in self._slots.values()
                if other.id != slot.id
                and other.extension.point == point.name
                and other.state is State.ACTIVE
            ]
            if others:
                return self._quarantine(
                    slot,
                    f"point {point.name!r} admits one active extension and "
                    f"{others[0].id!r} already holds it",
                    "",
                )

        starter = getattr(instance, "activate", None)
        if callable(starter):
            try:
                starter()
            except Exception as exc:  # noqa: BLE001
                return self._quarantine(slot, f"activate() failed: {exc}",
                                        traceback.format_exc())

        slot.instance = instance
        slot.state = State.ACTIVE
        slot.error = ""
        slot.traceback = ""
        slot.activated_at = time.time()
        return slot

    def _construct(self, slot: Slot) -> Any:
        module_name, _, attribute = slot.extension.factory.partition(":")
        module = importlib.import_module(module_name)
        factory = getattr(module, attribute, None)
        if factory is None:
            raise ExtensionError(
                f"{module_name} has no attribute {attribute!r}"
            )
        settings = dict(slot.extension.settings)
        if self.config is not None:
            # Kernel configuration is offered, never imposed: a factory that
            # takes no `config` keyword still works.
            merged = self.config.section(f"extension.{slot.id}")
            settings = {**merged, **settings}
        try:
            return factory(**settings) if settings else factory()
        except TypeError:
            # A factory that does not take these keywords is given none rather
            # than being declared broken — the settings may be for its own use.
            return factory()

    def deplug(self, identifier: str, *, reason: str = "") -> Slot:
        """Deactivate, running teardown. Reactivatable afterwards."""
        slot = self._require(identifier)
        if slot.state is not State.ACTIVE:
            slot.state = State.INACTIVE
            return slot

        dependents = [
            other.id for other in self._slots.values()
            if identifier in other.extension.requires and other.state is State.ACTIVE
        ]
        for dependent in dependents:
            self.deplug(dependent, reason=f"{identifier} was deplugged")

        stopper = getattr(slot.instance, "deactivate", None)
        if callable(stopper):
            try:
                stopper()
            except Exception as exc:  # noqa: BLE001
                slot.error = f"deactivate() failed: {exc}"
                slot.traceback = traceback.format_exc()

        slot.instance = None
        slot.state = State.INACTIVE
        if reason and not slot.error:
            slot.error = reason
        return slot

    def start(self) -> tuple[Slot, ...]:
        """Activate everything declared, in dependency order."""
        return tuple(self.plug(identifier) for identifier in self.order())

    def stop(self) -> tuple[Slot, ...]:
        """Deactivate everything, dependents first."""
        return tuple(self.deplug(identifier) for identifier in reversed(self.order()))

    def _quarantine(self, slot: Slot, error: str, trace: str) -> Slot:
        slot.state = State.QUARANTINED
        slot.error = error
        slot.traceback = trace
        slot.instance = None
        slot.failures += 1
        return slot

    def _require(self, identifier: str) -> Slot:
        slot = self._slots.get(identifier)
        if slot is None:
            raise ExtensionError(
                f"no extension named {identifier!r} "
                f"(declared: {', '.join(sorted(self._slots)) or 'none'})"
            )
        return slot

    # -- use --------------------------------------------------------------

    def get(self, identifier: str) -> Any:
        slot = self._slots.get(identifier)
        return slot.instance if slot is not None and slot.healthy else None

    def serving(self, point: str) -> tuple[Any, ...]:
        """Every live instance behind an extension point, in activation order."""
        return tuple(
            self._slots[identifier].instance
            for identifier in self.order()
            if self._slots[identifier].extension.point == point
            and self._slots[identifier].healthy
        )

    def one(self, point: str) -> Any:
        """The single instance behind a singular point, or None."""
        serving = self.serving(point)
        return serving[0] if serving else None

    def call(self, point: str, method: str, *arguments: Any, **keywords: Any
             ) -> tuple[Any, ...]:
        """Invoke a method on every extension at a point, isolating failures.

        A raising extension is quarantined and the remaining ones still run.
        Letting one plugin's exception abort the fan-out would make every
        deployment as reliable as its least reliable extension.
        """
        results: list[Any] = []
        for identifier in self.order():
            slot = self._slots[identifier]
            if slot.extension.point != point or not slot.healthy:
                continue
            handler = getattr(slot.instance, method, None)
            if not callable(handler):
                continue
            try:
                results.append(handler(*arguments, **keywords))
            except Exception as exc:  # noqa: BLE001
                self._quarantine(slot, f"{method}() raised: {exc}",
                                 traceback.format_exc())
        return tuple(results)

    # -- reporting --------------------------------------------------------

    @property
    def slots(self) -> tuple[Slot, ...]:
        return tuple(self._slots[identifier] for identifier in self.order())

    def health(self) -> dict[str, Any]:
        """What is running, what failed, and why. The operator's view."""
        by_state: dict[str, list[str]] = {}
        for slot in self._slots.values():
            by_state.setdefault(slot.state.value, []).append(slot.id)
        return {
            "points": sorted(self._points),
            "extensions": len(self._slots),
            "active": len([s for s in self._slots.values() if s.healthy]),
            "quarantined": {
                slot.id: slot.error
                for slot in self._slots.values() if slot.state is State.QUARANTINED
            },
            "states": {state: sorted(names) for state, names in by_state.items()},
            "degraded": any(
                slot.state is State.QUARANTINED for slot in self._slots.values()
            ),
        }

    def render(self) -> str:
        lines = [f"extensions: {len(self._slots)} declared across "
                 f"{len(self._points)} points"]
        lines.extend(f"  {slot}" for slot in self.slots)
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._slots)

    def __contains__(self, identifier: object) -> bool:
        return identifier in self._slots

    def __iter__(self) -> Iterator[Slot]:
        return iter(self.slots)

    def __repr__(self) -> str:  # pragma: no cover - display only
        health = self.health()
        return f"<Host {health['active']}/{health['extensions']} active>"


def extensions_from_config(config: Config) -> tuple[Extension, ...]:
    """Read `[[extension]]` blocks out of a loaded configuration.

    Config carries flattened keys, so an array of tables arrives as
    `extension.0.id`, `extension.1.id` and so on. Reassembling here means the
    file format is the deployment's concern and the host only ever sees
    `Extension` objects.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for key in config.keys():
        if not key.startswith("extension."):
            continue
        _, _, remainder = key.partition(".")
        index, _, field_name = remainder.partition(".")
        if not field_name:
            continue
        grouped.setdefault(index, {})[field_name] = config.get(key)

    found: list[Extension] = []
    for index in sorted(grouped):
        body = grouped[index]
        if "id" not in body or "factory" not in body:
            continue
        settings = {
            name[len("settings."):]: value
            for name, value in body.items() if name.startswith("settings.")
        }
        requires = body.get("requires") or ()
        found.append(Extension(
            id=str(body["id"]),
            point=str(body.get("point") or ""),
            factory=str(body["factory"]),
            enabled=bool(body.get("enabled", True)),
            requires=tuple(str(item) for item in requires),
            settings=settings,
            description=str(body.get("description") or ""),
        ))
    return tuple(found)
