"""The deployment topology, as declared. Frozen, ordered, and cheap to read.

Parsing this costs milliseconds because it is a *declaration*, not a search —
the same property that makes the environment manifest fast, for the same reason.
You state the topology; planning it is a diff against what is running, and
rendering it is text generation. Neither needs to look at anything.

── One thing that is deliberately not a dict ────────────────────────────

The manifest arrives as nested mappings and could be passed around that way.
It is not, and the reason is the one every typed model here has: an emitter
reaching for `shape["replias"]` returns `None` and renders a topology with no
replicas in it, silently. A dataclass raises at the boundary, once, where the
line number is still known.

Unstated fields keep their defaults rather than becoming `None`, so an emitter
never has to ask whether a value is absent or zero — a distinction that has no
meaning for a replica count and every meaning for a budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from ..errors import ManifestError
from .schema import parse_yaml, validate

DEFAULT_FILENAME = "slpie.deployment.yaml"


class DeployTarget(str, Enum):
    """The one tag, and the same gate as `simulated | live`."""

    PLAN = "plan"
    APPLY = "apply"


class Platform(str, Enum):
    KUBERNETES = "kubernetes"
    COMPOSE = "compose"
    NOMAD = "nomad"
    SYSTEMD = "systemd"


class Cloud(str, Enum):
    AWS = "aws"
    GCP = "gcp"
    AZURE = "azure"
    ONPREM = "onprem"


@dataclass(frozen=True, slots=True)
class Component:
    """One thing that runs, and how much of it.

    `replicas` and `(min, max)` are both allowed because they say different
    things: a fixed count is a decision, and a range is a delegation to the
    elasticity curve. Collapsing them would lose which one the operator meant.
    """

    name: str
    replicas: int = 0
    minimum: int = 0
    maximum: int = 0
    cpu: float = 0.0
    memory: str = ""
    ingress: str = ""
    queues: tuple[str, ...] = ()

    @property
    def elastic(self) -> bool:
        """Whether this component's size is the curve's to decide."""
        return self.maximum > self.minimum

    @property
    def size(self) -> int:
        """How many to start with. The floor for an elastic component."""
        return self.replicas or self.minimum or 1

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "size": self.size, "elastic": self.elastic}
        if self.replicas:
            out["replicas"] = self.replicas
        if self.elastic:
            out["min"], out["max"] = self.minimum, self.maximum
        if self.cpu:
            out["cpu"] = self.cpu
        if self.memory:
            out["memory"] = self.memory
        if self.ingress:
            out["ingress"] = self.ingress
        if self.queues:
            out["queues"] = list(self.queues)
        return out


@dataclass(frozen=True, slots=True)
class Elasticity:
    """How the pool grows, and how reluctantly it shrinks.

    The windows are asymmetric on purpose and it is not a tuning accident:
    being briefly over-provisioned costs money, and being briefly
    under-provisioned costs *correctness*, because a scan that times out is a
    gap in an answer.
    """

    curve: str = "logarithmic"
    target_queue_depth: int = 50
    scale_up_window: str = "30s"
    scale_down_window: str = "10m"
    drain_grace: str = "5m"

    def to_dict(self) -> dict[str, Any]:
        return {
            "curve": self.curve,
            "target_queue_depth": self.target_queue_depth,
            "scale_up_window": self.scale_up_window,
            "scale_down_window": self.scale_down_window,
            "drain_grace": self.drain_grace,
        }


@dataclass(frozen=True, slots=True)
class Budget:
    """The forced policymaker's thresholds.

    Zero means unstated rather than free. A ceiling of zero would make every
    deployment permanently over budget, which is a finding nobody would read
    twice.
    """

    monthly_ceiling: float = 0.0
    currency: str = "USD"
    warn_at: float = 0.75
    idle_after: str = "20m"
    region_egress_ceiling: float = 0.0

    @property
    def stated(self) -> bool:
        return self.monthly_ceiling > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "monthly_ceiling": self.monthly_ceiling, "currency": self.currency,
            "warn_at": self.warn_at, "idle_after": self.idle_after,
            "region_egress_ceiling": self.region_egress_ceiling,
            "stated": self.stated,
        }


@dataclass(frozen=True, slots=True)
class Regions:
    """Where the ledger writes, and where the graph is merely read."""

    primary: str = ""
    replicas: tuple[str, ...] = ()
    freshness_budget: str = "30s"

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary, "replicas": list(self.replicas),
            "freshness_budget": self.freshness_budget,
        }


@dataclass(frozen=True, slots=True)
class Store:
    """One persistent thing, and what backs it."""

    name: str
    engine: str
    size: str = ""
    retention: str = ""
    partition: str = ""
    bucket: str = ""
    replicas: int = 0

    def to_dict(self) -> dict[str, Any]:
        out = {"name": self.name, "engine": self.engine}
        for key in ("size", "retention", "partition", "bucket"):
            if value := getattr(self, key):
                out[key] = value
        if self.replicas:
            out["replicas"] = self.replicas
        return out


@dataclass(frozen=True, slots=True)
class Persistence:
    stores: tuple[Store, ...] = ()

    def get(self, name: str) -> Store | None:
        return next((store for store in self.stores if store.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {store.name: store.to_dict() for store in self.stores}


@dataclass(frozen=True, slots=True)
class Deployment:
    """A whole declared deployment. The thing every emitter renders from."""

    environment: str
    target: DeployTarget = DeployTarget.PLAN
    platform: Platform = Platform.COMPOSE
    cloud: Cloud = Cloud.ONPREM
    components: tuple[Component, ...] = ()
    elasticity: Elasticity = field(default_factory=Elasticity)
    budget: Budget = field(default_factory=Budget)
    regions: Regions = field(default_factory=Regions)
    persistence: Persistence = field(default_factory=Persistence)
    source_uri: str = ""

    def component(self, name: str) -> Component | None:
        return next((item for item in self.components if item.name == name), None)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.components)

    def to_dict(self) -> dict[str, Any]:
        return {
            "environment": self.environment,
            "target": self.target.value,
            "platform": self.platform.value,
            "cloud": self.cloud.value,
            "topology": {item.name: item.to_dict() for item in self.components},
            "elasticity": self.elasticity.to_dict(),
            "budget": self.budget.to_dict(),
            "regions": self.regions.to_dict(),
            "persistence": self.persistence.to_dict(),
            "source_uri": self.source_uri,
        }


def load(path: str | Path) -> Deployment:
    """Read a deployment manifest from disk."""
    found = Path(path)
    if not found.is_file():
        raise ManifestError(f"no deployment manifest at {found}")
    return loads(found.read_text(encoding="utf-8"), source_uri=found.resolve().as_uri())


def loads(text: str, *, source_uri: str = "") -> Deployment:
    """Read a deployment manifest from text, validated first."""
    document = validate(parse_yaml(text))
    return _build(document, source_uri=source_uri)


def _build(document: Mapping[str, Any], *, source_uri: str = "") -> Deployment:
    return Deployment(
        environment=str(document["environment"]),
        target=DeployTarget(document.get("target", "plan")),
        platform=Platform(document.get("platform", "compose")),
        cloud=Cloud(document.get("cloud", "onprem")),
        # Sorted by name, so two reads of one file produce one model and a
        # rendered artifact can be diffed against the last one.
        components=tuple(
            _component(name, shape)
            for name, shape in sorted((document.get("topology") or {}).items())
        ),
        elasticity=_elasticity(document.get("elasticity") or {}),
        budget=_budget(document.get("budget") or {}),
        regions=_regions(document.get("regions") or {}),
        persistence=_persistence(document.get("persistence") or {}),
        source_uri=source_uri,
    )


def _component(name: str, shape: Mapping[str, Any]) -> Component:
    return Component(
        name=str(name),
        replicas=int(shape.get("replicas", 0) or 0),
        minimum=int(shape.get("min", 0) or 0),
        maximum=int(shape.get("max", 0) or 0),
        cpu=float(shape.get("cpu", 0) or 0),
        memory=str(shape.get("memory", "") or ""),
        ingress=str(shape.get("ingress", "") or ""),
        queues=tuple(str(item) for item in (shape.get("queues") or ())),
    )


def _elasticity(shape: Mapping[str, Any]) -> Elasticity:
    return Elasticity(
        curve=str(shape.get("curve", "logarithmic")),
        target_queue_depth=int(shape.get("target_queue_depth", 50) or 50),
        scale_up_window=str(shape.get("scale_up_window", "30s")),
        scale_down_window=str(shape.get("scale_down_window", "10m")),
        drain_grace=str(shape.get("drain_grace", "5m")),
    )


def _budget(shape: Mapping[str, Any]) -> Budget:
    ceiling, currency = _money(shape.get("monthly_ceiling"))
    egress, _ = _money(shape.get("region_egress_ceiling"))
    return Budget(
        monthly_ceiling=ceiling,
        currency=currency or str(shape.get("currency", "USD")),
        warn_at=float(shape.get("warn_at", 0.75) or 0.75),
        idle_after=str(shape.get("idle_after", "20m")),
        region_egress_ceiling=egress,
    )


def _money(value: Any) -> tuple[float, str]:
    """`4000 USD` and `4000` both work; the currency travels with the number.

    Written as one field in the manifest because that is how an operator writes
    a budget, and split here because arithmetic on `"4000 USD"` is not a thing
    anybody should be doing later.
    """
    if value is None:
        return 0.0, ""
    if isinstance(value, (int, float)):
        return float(value), ""
    parts = str(value).split()
    try:
        amount = float(parts[0])
    except (ValueError, IndexError):
        raise ManifestError(f"a budget must start with a number; found {value!r}") from None
    return amount, parts[1] if len(parts) > 1 else ""


def _regions(shape: Mapping[str, Any]) -> Regions:
    return Regions(
        primary=str(shape.get("primary", "") or ""),
        replicas=tuple(str(item) for item in (shape.get("replicas") or ())),
        freshness_budget=str(shape.get("freshness_budget", "30s")),
    )


def _persistence(shape: Mapping[str, Any]) -> Persistence:
    return Persistence(stores=tuple(
        Store(
            name=str(name),
            engine=str(store.get("engine", "")),
            size=str(store.get("size", "") or ""),
            retention=str(store.get("retention", "") or ""),
            partition=str(store.get("partition", "") or ""),
            bucket=str(store.get("bucket", "") or ""),
            replicas=int(store.get("replicas", 0) or 0),
        )
        for name, store in sorted(shape.items())
    ))
