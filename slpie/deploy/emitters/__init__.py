"""One model, many emitters — so a fourth platform is a registration, not a fork.

Every emitter takes the same `Deployment` and returns `{path: text}`. Nothing
writes to disk here: a caller decides where the text goes, which is what lets
`deploy render` print to a terminal, write to `./deploy/`, and be tested without
a filesystem at all.

── Two properties, both asserted rather than intended ───────────────────

**Deterministic.** Nothing reads the clock, the environment or a random source,
and every mapping is walked in sorted order. The same manifest renders
byte-identically twice, which is what makes a rendered artifact reviewable in a
diff — the reason §18 wants it reviewable *before* anything runs.

**Complete or absent.** An emitter that cannot express something says so in
`gaps()` rather than emitting a file with the awkward part left out. A
`docker-compose.yaml` that silently dropped the elasticity range would deploy a
fixed pool and look correct, and the operator would find out from the bill.
"""

from __future__ import annotations

from typing import Callable, Mapping, Protocol

from ..manifest import Deployment
from . import ansible, compose, helm, kubernetes, pipelines, systemd, terraform


class Emitter(Protocol):
    """What a platform emitter must be. Registered exactly as a plugin is."""

    name: str

    def render(self, deployment: Deployment) -> Mapping[str, str]:
        """`{relative path: file text}`. Never touches the filesystem."""

    def gaps(self, deployment: Deployment) -> tuple[str, ...]:
        """What this emitter could not express, in an operator's words."""


#: The registry, as data. `ExtensionPoint.ARTIFACT` is how a third party joins:
#: a plugin registering here gets `deploy render --emitter <name>` with no
#: kernel change, which is invariant 6 applied to deployment.
EMITTERS: dict[str, Emitter] = {
    module.NAME: module            # type: ignore[misc]
    for module in (compose, kubernetes, systemd, helm, terraform, pipelines, ansible)
}


def names() -> tuple[str, ...]:
    return tuple(sorted(EMITTERS))


def emitter(name: str) -> Emitter | None:
    return EMITTERS.get(name)


def render(deployment: Deployment, *, emitter: str) -> dict[str, str]:
    """Render one deployment through one emitter."""
    chosen = EMITTERS.get(emitter)
    if chosen is None:
        raise KeyError(
            f"no emitter named {emitter!r}; this build has {', '.join(names())}"
        )
    return dict(chosen.render(deployment))


def gaps(deployment: Deployment, *, emitter: str) -> tuple[str, ...]:
    chosen = EMITTERS.get(emitter)
    return tuple(chosen.gaps(deployment)) if chosen else ()


def default_for(deployment: Deployment) -> str:
    """The emitter a manifest implies, so `render` needs no flag in the common case."""
    return deployment.platform.value if deployment.platform.value in EMITTERS else "compose"


__all__ = ["EMITTERS", "Emitter", "default_for", "emitter", "gaps", "names", "render"]
