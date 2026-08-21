"""The deployment manifest's shape, refused rather than guessed.

The YAML reader is `slpie/environment/schema.py`'s — reused, not rewritten.
That module already reads the subset a manifest uses and refuses the rest, and
a second parser would be a second set of bugs about the same file format.

What is here is the *shape*: which sections exist, which values are closed sets,
and what a malformed field means. The position is the one the environment
manifest already takes and it is worth restating because it is unusual: **an
unknown section is an error, not a warning.** A misspelled `topolgy:` that
parsed cleanly would mean deploying an environment with no components in it,
confidently. A configuration file silently misread is worse than one that fails
to load.
"""

from __future__ import annotations

from typing import Any

from ..environment.schema import parse_yaml as parse_yaml
from ..errors import ManifestError

#: Sections a deployment manifest may declare. Anything else is a typo.
SECTIONS = (
    "apiVersion", "kind", "environment", "target",
    "topology", "elasticity", "budget", "regions", "persistence",
    "platform", "cloud",
)

SUPPORTED_API_VERSIONS = ("slpie/v1",)

#: The one tag, mirroring `simulated | live` on the environment manifest. `plan`
#: renders and diffs; `apply` is the dangerous direction and is gated.
TARGETS = ("plan", "apply")

#: Where it runs. Closed, because an emitter exists per platform and a platform
#: with no emitter would be a manifest that validates and cannot be rendered.
PLATFORMS = ("kubernetes", "compose", "nomad", "systemd")

#: Whose infrastructure. Closed for the same reason the platform list is: it
#: selects the Terraform modules, and an unknown value would emit nothing.
CLOUDS = ("aws", "gcp", "azure", "onprem")

#: Engines a persistence entry may name.
ENGINES = (
    "postgres", "sqlite", "s3", "gcs", "azure-blob", "filesystem",
    # `rabbitmq` before `redis` because it is what the task runner is built
    # for: a broker with acknowledgements, so a worker killed mid-scan returns
    # its unit to the queue instead of leaving it in a list nobody is watching.
    # Redis stays declarable — it is the right *result backend*, and plenty of
    # estates already run one — but a queue is not what it is good at.
    "rabbitmq", "redis",
)

#: Growth curves. `logarithmic` is the default and `linear` must be chosen
#: deliberately — §23's argument is that linear autoscaling on queue depth
#: oscillates structurally rather than as a tuning problem.
CURVES = ("logarithmic", "linear")

#: A component must say how many of it there are, one way or the other.
SIZED = ("replicas", "min", "max")


def validate(document: Any) -> dict[str, Any]:
    """Check a parsed deployment manifest before anything renders it."""
    if not isinstance(document, dict):
        raise ManifestError("a deployment manifest must be a mapping at the top level")

    unknown = [key for key in document if key not in SECTIONS]
    if unknown:
        raise ManifestError(
            f"unknown deployment section(s): {', '.join(sorted(unknown))}; "
            f"expected any of {', '.join(SECTIONS)}"
        )

    api_version = document.get("apiVersion")
    if api_version is None:
        raise ManifestError("a deployment manifest must declare apiVersion")
    if api_version not in SUPPORTED_API_VERSIONS:
        raise ManifestError(
            f"unsupported apiVersion {api_version!r}; "
            f"this build understands {', '.join(SUPPORTED_API_VERSIONS)}"
        )

    kind = document.get("kind", "Deployment")
    if kind != "Deployment":
        # Named rather than ignored: an environment manifest fed to `deploy` is
        # a mistake somebody made, and the error should say which file they
        # meant rather than complaining about a missing `topology`.
        raise ManifestError(
            f"kind must be Deployment; found {kind!r}. An environment manifest "
            f"goes to `slpie declare`, not to `slpie deploy`."
        )

    if not document.get("environment"):
        raise ManifestError("a deployment manifest must name its environment")

    target = document.get("target", "plan")
    if target not in TARGETS:
        raise ManifestError(
            f"target must be one of {', '.join(TARGETS)}; found {target!r}"
        )

    _closed(document, "platform", PLATFORMS, default="compose")
    _closed(document, "cloud", CLOUDS, default="onprem")

    _topology(document.get("topology"))
    _elasticity(document.get("elasticity"))
    _budget(document.get("budget"))
    _regions(document.get("regions"))
    _persistence(document.get("persistence"))

    return document


def _closed(document: dict[str, Any], key: str, allowed: tuple[str, ...], *, default: str) -> None:
    value = document.get(key, default)
    if value not in allowed:
        raise ManifestError(
            f"{key} must be one of {', '.join(allowed)}; found {value!r}"
        )


def _topology(section: Any) -> None:
    if section is None:
        raise ManifestError("a deployment manifest must declare a topology")
    if not isinstance(section, dict):
        raise ManifestError("the topology section must be a mapping of component to shape")
    if not section:
        raise ManifestError("the topology section declares no components")

    for name, shape in section.items():
        if not isinstance(shape, dict):
            raise ManifestError(
                f"topology.{name} must be a mapping, found {type(shape).__name__}"
            )
        if not any(field in shape for field in SIZED):
            raise ManifestError(
                f"topology.{name} must state a size: one of {', '.join(SIZED)}"
            )
        # A range that cannot be satisfied is caught here rather than by an
        # autoscaler at three in the morning.
        low, high = shape.get("min"), shape.get("max")
        if low is not None and high is not None and int(low) > int(high):
            raise ManifestError(
                f"topology.{name} has min {low} above max {high}"
            )


def _elasticity(section: Any) -> None:
    if section is None:
        return
    if not isinstance(section, dict):
        raise ManifestError("the elasticity section must be a mapping")
    curve = section.get("curve", "logarithmic")
    if curve not in CURVES:
        raise ManifestError(
            f"elasticity.curve must be one of {', '.join(CURVES)}; found {curve!r}"
        )


def _budget(section: Any) -> None:
    if section is None:
        return
    if not isinstance(section, dict):
        raise ManifestError("the budget section must be a mapping")
    warn = section.get("warn_at", 0.75)
    if not isinstance(warn, (int, float)) or not 0 < float(warn) <= 1:
        raise ManifestError(
            f"budget.warn_at is a fraction of the ceiling, above 0 and at most 1; "
            f"found {warn!r}"
        )


def _regions(section: Any) -> None:
    if section is None:
        return
    if not isinstance(section, dict):
        raise ManifestError("the regions section must be a mapping")
    if section.get("replicas") is not None and not isinstance(section["replicas"], list):
        raise ManifestError("regions.replicas must be a list of region names")
    primary = section.get("primary", "")
    if primary and primary in (section.get("replicas") or []):
        # The ledger has one writer. A region that is both would make "which
        # region is authoritative" ambiguous, and §23's whole caching story
        # rests on that question having one answer.
        raise ManifestError(
            f"region {primary!r} is both the primary and a replica; the ledger "
            f"has exactly one writer"
        )


def _persistence(section: Any) -> None:
    if section is None:
        return
    if not isinstance(section, dict):
        raise ManifestError("the persistence section must be a mapping")
    for name, store in section.items():
        if not isinstance(store, dict):
            raise ManifestError(f"persistence.{name} must be a mapping")
        engine = store.get("engine")
        if engine is None:
            raise ManifestError(f"persistence.{name} must name an engine")
        if engine not in ENGINES:
            raise ManifestError(
                f"persistence.{name}.engine must be one of {', '.join(ENGINES)}; "
                f"found {engine!r}"
            )
