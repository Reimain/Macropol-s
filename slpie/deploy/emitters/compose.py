"""`docker-compose.yaml` — a single host, and the emitter the acceptance run uses.

Compose is first among the six because it is the one that can be *stood up in a
test*. §18's acceptance is reconciliation applied to the platform itself —
deploy SLPIE, then point SLPIE at the deployment and scan it — and that needs a
platform which exists on one machine with no cluster and no cloud account.
"""

from __future__ import annotations

from typing import Mapping

from ..manifest import Deployment
from ._common import (
    command_for,
    docker_memory,
    environment_for,
    header,
    image,
    port_for,
)

NAME = "compose"


def render(deployment: Deployment) -> Mapping[str, str]:
    lines = header(deployment)
    lines += ["", "services:"]

    # The declared stores first, because a component that depends on one and
    # starts before it fails its first read. Compose orders by `depends_on`,
    # and this emitter is the only one that can honestly provide them: on a
    # single host a database *is* another container.
    for name, block in sorted(_stores(deployment).items()):
        lines += block

    for component in deployment.components:
        lines += _service(deployment, component)

    volumes = _volumes(deployment)
    if volumes:
        lines += ["", "volumes:"] + [f"  {name}:" for name in volumes]

    return {"docker-compose.yaml": "\n".join(lines) + "\n"}


def _service(deployment: Deployment, component) -> list[str]:
    body = [
        "",
        f"  {component.name}:",
        f"    image: {image()}",
        "    command: [" + ", ".join(f'"{part}"' for part in command_for(component)) + "]",
        "    restart: unless-stopped",
    ]

    if port := port_for(component):
        body.append(f'    ports: ["{port}:{port}"]')

    # Compose has no autoscaler. A range is honoured at its floor and the fact
    # is stated in the file rather than left for the operator to discover from
    # a queue that never drains — the same reason `gaps()` reports it.
    if component.elastic:
        body.append(
            f"    # declared elastic {component.minimum}–{component.maximum}; "
            f"compose has no autoscaler, so this is the floor"
        )
    body.append(f"    deploy:")
    body.append(f"      replicas: {component.size}")

    limits = []
    if component.cpu:
        limits.append(f"          cpus: \"{component.cpu}\"")
    if component.memory:
        limits.append(f"          memory: {docker_memory(component.memory)}")
    if limits:
        body += ["      resources:", "        limits:"] + limits

    values = environment_for(deployment)
    if values:
        body.append("    environment:")
        body += [f"      {key}: \"{value}\"" for key, value in values.items()]

    depends = _depends(deployment, component)
    if depends:
        body.append("    depends_on: [" + ", ".join(depends) + "]")
    return body


#: Stores this emitter can actually run as a container, and the image for each.
#: A store outside this table is a managed service: it is *not* emitted, and
#: `gaps()` says so rather than a service quietly appearing that nothing backs.
#: Ports are a *tuple* per engine rather than one number, and that is not
#: generality for its own sake: RabbitMQ needs two — 5672 for the protocol and
#: 15672 for the management API — and the first version of this emitted two
#: `expose:` keys in one service block. Compose keeps the last of a duplicated
#: key, so the render was valid YAML that silently dropped the broker port.
CONTAINERISED = {
    "postgres": ("postgres:16", (5432,), "/var/lib/postgresql/data"),
    "redis": ("redis:7-alpine", (6379,), "/data"),
    # The management image rather than plain `rabbitmq:3`. It is the same
    # broker plus the HTTP API that queue depth comes from — and depth is the
    # number §23's elasticity curve is computed from, so a deployment without
    # it can run scans and cannot explain its own replica count.
    "rabbitmq": ("rabbitmq:3-management", (5672, 15672), "/var/lib/rabbitmq"),
}


def _stores(deployment: Deployment) -> dict[str, list[str]]:
    """The declared stores, as services. Keyed by service name.

    Emitted rather than assumed. An earlier version wrote `depends_on:
    [postgres, redis]` and emitted neither, so the file was syntactically
    perfect and `docker compose up` failed on a service that did not exist —
    the class of defect this whole phase exists to make impossible, produced by
    the tool that is supposed to prevent it.
    """
    services: dict[str, list[str]] = {}
    for store in deployment.persistence.stores:
        recipe = CONTAINERISED.get(store.engine)
        if recipe is None:
            continue
        image_name, ports, data = recipe
        body = [
            "",
            f"  {store.engine}:",
            f"    image: {image_name}",
            "    restart: unless-stopped",
            "    expose: [" + ", ".join(f'"{port}"' for port in ports) + "]",
            f"    volumes: [\"{store.name}-data:{data}\"]",
        ]
        if store.engine == "rabbitmq":
            body += [
                "    environment:",
                "      RABBITMQ_DEFAULT_USER: slpie",
                "      RABBITMQ_DEFAULT_PASS: \"${RABBITMQ_PASSWORD:?set a password}\"",
                "    healthcheck:",
                '      test: ["CMD", "rabbitmq-diagnostics", "-q", "ping"]',
                "      interval: 10s",
                "      retries: 10",
            ]
        if store.engine == "postgres":
            body += [
                "    environment:",
                "      POSTGRES_USER: slpie",
                "      POSTGRES_DB: slpie",
                # From the environment, never written into the file. A rendered
                # artifact carrying a real password is a credential somebody is
                # about to commit.
                "      POSTGRES_PASSWORD: \"${POSTGRES_PASSWORD:?set a password}\"",
                "    healthcheck:",
                '      test: ["CMD-SHELL", "pg_isready -U slpie"]',
                "      interval: 5s",
                "      retries: 10",
            ]
        services[store.engine] = body
    return services


def _depends(deployment: Deployment, component) -> list[str]:
    """What must be up first — only stores this file actually emits."""
    return sorted(_stores(deployment))


def _volumes(deployment: Deployment) -> list[str]:
    """A named volume per store that has one. Nothing else gets a volume.

    An emitted volume nothing mounts is harmless and confusing, and a mounted
    volume that is not declared stops the file from loading — so the two lists
    are derived from the same table.
    """
    return sorted(
        f"{store.name}-data" for store in deployment.persistence.stores
        if store.engine in CONTAINERISED
    )


def gaps(deployment: Deployment) -> tuple[str, ...]:
    """What compose cannot express, said plainly rather than rendered wrong."""
    found = []
    elastic = [item.name for item in deployment.components if item.elastic]
    if elastic:
        found.append(
            f"compose has no autoscaler: {', '.join(elastic)} declared a range "
            f"and is rendered at its floor. Use kubernetes for elasticity."
        )
    if len(deployment.regions.replicas) > 0:
        found.append(
            f"compose is a single host: the {len(deployment.regions.replicas)} "
            f"declared replica region(s) are not rendered."
        )
    external = sorted(
        f"{store.name} ({store.engine})" for store in deployment.persistence.stores
        if store.engine not in CONTAINERISED
    )
    if external:
        found.append(
            f"{', '.join(external)} is a managed service and is not rendered as "
            f"a container; point the matching environment variable at a real one."
        )
    return tuple(found)
