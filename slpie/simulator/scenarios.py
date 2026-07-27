"""Scenarios — conditions fired at a world, so agentic testing has something to test.

"Assert the platform detects a CVE" is only meaningful if a CVE can actually
*land*. A scenario changes the materialised world — rewrites a lockfile, ages a
package, breaks a contract, refuses a capability — and the platform then meets
the new state through the same discovery path it always uses. Nothing is
injected into the graph directly, because a scenario that wrote its own findings
would be testing the test.

Each scenario returns an :class:`Outcome` describing what it changed and what
the platform is expected to notice. The expectation is data, not a docstring, so
the UI can show "fired: CVE on lodash — expected: 1 vulnerable_dependency
finding" and then show whether that is what happened.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .clock import DAY
from .faults import Fault, FaultKind
from .world import SimulatedWorld


@dataclass(frozen=True, slots=True)
class Outcome:
    """What a scenario did, and what the platform ought to make of it."""

    scenario: str
    changed: tuple[str, ...] = ()
    expect_findings: tuple[str, ...] = ()
    expect_gaps: tuple[str, ...] = ()
    detail: str = ""
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "changed": list(self.changed),
            "expect_findings": list(self.expect_findings),
            "expect_gaps": list(self.expect_gaps),
            "detail": self.detail,
            "parameters": dict(self.parameters),
        }

    def __str__(self) -> str:
        return f"{self.scenario}: {self.detail}"


Scenario = Callable[..., Outcome]
_REGISTRY: dict[str, Scenario] = {}


def scenario(name: str) -> Callable[[Scenario], Scenario]:
    def register(function: Scenario) -> Scenario:
        _REGISTRY[name] = function
        function.scenario_name = name  # type: ignore[attr-defined]
        return function

    return register


def available() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def fire(name: str, world: SimulatedWorld, **parameters: Any) -> Outcome:
    """Run a scenario against a world by name — what the CLI and UI call."""
    handler = _REGISTRY.get(name)
    if handler is None:
        raise KeyError(
            f"no scenario named {name!r}; available: {', '.join(available())}"
        )
    return handler(world, **parameters)


# --- dependency conditions ----------------------------------------------


@scenario("cve")
def cve_lands(
    world: SimulatedWorld,
    *,
    package: str = "lodash",
    version: str = "4.17.20",
    element: str = "",
) -> Outcome:
    """A vulnerable version is already what the lockfile pins.

    Written into the lockfile rather than announced to the graph, so the
    platform has to *find* it — through the same lockfile discoverer that runs
    against a real repository.
    """
    changed = _rewrite_lock(world, element, package, version)
    return Outcome(
        scenario="cve",
        changed=changed,
        expect_findings=("vulnerable_dependency",),
        detail=f"{package} pinned at {version}, a version with a known advisory",
        parameters={"package": package, "version": version},
    )


@scenario("major-bump")
def major_bump(
    world: SimulatedWorld,
    *,
    package: str = "lodash",
    version: str = "5.0.0",
    element: str = "",
) -> Outcome:
    """A new major lands upstream, so the declared range no longer admits it."""
    changed = _rewrite_lock(world, element, package, version, keep_range=True)
    return Outcome(
        scenario="major-bump",
        changed=changed,
        expect_findings=("constraint_conflict",),
        detail=(
            f"{package} {version} is available, but the declared caret range "
            f"cannot reach across a major boundary"
        ),
        parameters={"package": package, "version": version},
    )


@scenario("unmaintained")
def unmaintained(world: SimulatedWorld, *, years: float = 3.0) -> Outcome:
    """Time passes and nothing is released. Cheap, because the clock is ours."""
    world.clock.advance_days(years * 365)
    return Outcome(
        scenario="unmaintained",
        expect_findings=("unmaintained_package",),
        detail=f"{years} years pass with no upstream release",
        parameters={"years": years},
    )


@scenario("duplicate-versions")
def duplicate_versions(
    world: SimulatedWorld, *, package: str = "semver", element: str = ""
) -> Outcome:
    """Two versions of one package in the same tree — the classic bloat finding."""
    target = element or _first_codebase(world)
    lock = _load_lock(world, target)
    if lock is None:
        return Outcome(scenario="duplicate-versions", detail="no lockfile to modify")

    lock["packages"][f"node_modules/other/node_modules/{package}"] = {
        "version": "6.3.0",
        "resolved": f"https://registry.npmjs.org/{package}/-/{package}-6.3.0.tgz",
        "license": "ISC",
    }
    world.rewrite(target, "package-lock.json", json.dumps(lock, indent=2) + "\n")
    return Outcome(
        scenario="duplicate-versions",
        changed=(f"{target}/package-lock.json",),
        expect_findings=("duplicate_versions",),
        detail=f"two versions of {package} resolved in one tree",
        parameters={"package": package},
    )


@scenario("license-change")
def license_change(
    world: SimulatedWorld,
    *,
    package: str = "lodash",
    license: str = "AGPL-3.0-only",
    element: str = "",
) -> Outcome:
    """A dependency relicenses under something the project cannot absorb."""
    target = element or _first_codebase(world)
    lock = _load_lock(world, target)
    if lock is None:
        return Outcome(scenario="license-change", detail="no lockfile to modify")

    key = f"node_modules/{package}"
    if key in lock["packages"]:
        lock["packages"][key]["license"] = license
    world.rewrite(target, "package-lock.json", json.dumps(lock, indent=2) + "\n")
    return Outcome(
        scenario="license-change",
        changed=(f"{target}/package-lock.json",),
        expect_findings=("license_incompatible",),
        detail=f"{package} is now {license}",
        parameters={"package": package, "license": license},
    )


# --- environment conditions ---------------------------------------------


@scenario("service-dies")
def service_dies(world: SimulatedWorld, *, element: str = "") -> Outcome:
    """An element stops answering entirely."""
    target = element or _first_codebase(world)
    world.faults.inject(Fault(
        kind=FaultKind.UNAVAILABLE, element=target,
        reason="the element stopped responding",
    ))
    return Outcome(
        scenario="service-dies",
        expect_gaps=("not_attached",),
        detail=f"{target} is unreachable; everything resting on it becomes a gap",
        parameters={"element": target},
    )


@scenario("capability-refused")
def capability_refused(
    world: SimulatedWorld, *, element: str = "", capability: str = "static-analysis"
) -> Outcome:
    """An element grants some access and withholds the rest.

    The most common real condition and the one worth getting right: the platform
    must still answer, and must say which part of the answer it could not check.
    """
    target = element or _first_codebase(world)
    world.faults.refuse(
        target, capability, reason="the source tree is vendored and cannot be analysed"
    )
    return Outcome(
        scenario="capability-refused",
        expect_gaps=("capability_refused",),
        detail=f"{target} refuses {capability}",
        parameters={"element": target, "capability": capability},
    )


@scenario("contract-broken")
def contract_broken(world: SimulatedWorld, *, element: str = "") -> Outcome:
    """An API drops an operation its consumers still call."""
    target = element or _first_network(world)
    if target is None:
        return Outcome(scenario="contract-broken", detail="no network element declared")

    try:
        document = json.loads(world.read(target, "openapi.json"))
    except (BaseException,):
        return Outcome(scenario="contract-broken", detail="no OpenAPI document to break")

    document.get("paths", {}).pop("/orders", None)
    world.rewrite(target, "openapi.json", json.dumps(document, indent=2) + "\n")
    return Outcome(
        scenario="contract-broken",
        changed=(f"{target}/openapi.json",),
        expect_findings=("schema_drift",),
        detail=f"{target} no longer exposes /orders",
        parameters={"element": target},
    )


@scenario("boundary-breach")
def boundary_breach(world: SimulatedWorld, *, element: str = "") -> Outcome:
    """Code inside a security boundary starts calling something outside it."""
    target = element or _first_codebase(world)
    world.rewrite(
        target, "src/exfil.js",
        "const https = require('https');\n"
        "// egress to an endpoint the manifest never declared\n"
        "https.request('https://analytics.undeclared.invalid/collect');\n",
    )
    return Outcome(
        scenario="boundary-breach",
        changed=(f"{target}/src/exfil.js",),
        expect_findings=("boundary_violation",),
        detail=f"{target} gains undeclared egress from inside its boundary",
        parameters={"element": target},
    )


@scenario("shadow-dependency")
def shadow_dependency(
    world: SimulatedWorld, *, package: str = "left-pad", element: str = ""
) -> Outcome:
    """Something is imported that no manifest ever declared."""
    target = element or _first_codebase(world)
    world.rewrite(
        target, "src/shadow.js",
        f"const shadow = require('{package}');\nmodule.exports = shadow;\n",
    )
    return Outcome(
        scenario="shadow-dependency",
        changed=(f"{target}/src/shadow.js",),
        expect_findings=("phantom_dependency",),
        detail=f"{package} is imported but appears in no manifest",
        parameters={"package": package},
    )


@scenario("declaration-drift")
def declaration_drift(world: SimulatedWorld, *, element: str = "") -> Outcome:
    """The manifest says REST; the element is observed speaking gRPC."""
    target = element or _first_network(world)
    if target is None:
        return Outcome(scenario="declaration-drift", detail="no network element declared")

    world.faults.inject(Fault(
        kind=FaultKind.CONTRADICT, element=target,
        replacement={"x-protocol": "grpc"},
        reason="the observed protocol contradicts the declaration",
    ))
    return Outcome(
        scenario="declaration-drift",
        expect_findings=("contradicted",),
        detail=f"{target} is declared rest and observed speaking grpc",
        parameters={"element": target},
    )


@scenario("partial-scan")
def partial_scan(world: SimulatedWorld, *, element: str = "", fraction: float = 0.5) -> Outcome:
    """An element returns only part of its contents.

    Worse than a refusal, because the platform gets an answer that *looks*
    complete. The scan must notice the shortfall and report it as a gap.
    """
    target = element or _first_codebase(world)
    world.faults.inject(Fault(
        kind=FaultKind.PARTIAL_DATA, element=target, fraction=fraction,
        reason="the element returned a truncated listing",
    ))
    return Outcome(
        scenario="partial-scan",
        expect_gaps=("low_confidence",),
        detail=f"{target} returns {int(fraction * 100)}% of its files",
        parameters={"element": target, "fraction": fraction},
    )


# --- helpers -------------------------------------------------------------


def _first_codebase(world: SimulatedWorld) -> str:
    from ..environment.manifest import DeclarationKind

    declarations = world.manifest.of_kind(DeclarationKind.CODEBASE)
    if not declarations:
        raise ValueError("this world declares no codebase to act on")
    return declarations[0].name


def _first_network(world: SimulatedWorld) -> str | None:
    from ..environment.manifest import DeclarationKind

    declarations = world.manifest.of_kind(DeclarationKind.NETWORK)
    return declarations[0].name if declarations else None


def _load_lock(world: SimulatedWorld, element: str) -> dict[str, Any] | None:
    try:
        return json.loads(world.read(element, "package-lock.json"))
    except Exception:
        return None


def _rewrite_lock(
    world: SimulatedWorld,
    element: str,
    package: str,
    version: str,
    *,
    keep_range: bool = False,
) -> tuple[str, ...]:
    """Pin a package at a version in the materialised lockfile.

    ``keep_range`` leaves package.json's declared range alone, which is exactly
    what makes a major bump a *conflict* rather than an upgrade — the resolved
    version no longer satisfies what was asked for.
    """
    target = element or _first_codebase(world)
    lock = _load_lock(world, target)
    if lock is None:
        return ()

    key = f"node_modules/{package}"
    entry = lock["packages"].setdefault(key, {"license": "MIT"})
    entry["version"] = version
    entry["resolved"] = (
        f"https://registry.npmjs.org/{package}/-/{package}-{version}.tgz"
    )
    world.rewrite(target, "package-lock.json", json.dumps(lock, indent=2) + "\n")
    changed = [f"{target}/package-lock.json"]

    if not keep_range:
        try:
            manifest = json.loads(world.read(target, "package.json"))
        except Exception:
            return tuple(changed)
        manifest.setdefault("dependencies", {})[package] = f"^{version}"
        world.rewrite(target, "package.json", json.dumps(manifest, indent=2) + "\n")
        changed.append(f"{target}/package.json")

    return tuple(changed)
