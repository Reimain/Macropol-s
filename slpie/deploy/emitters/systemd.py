"""Units and an install script — on-prem, with no orchestrator at all.

The emitter for the estate that has a machine and no cluster, which is more of
them than the industry's documentation suggests. It is also the one that makes
the air-gapped claim concrete: nothing here needs a registry, a control plane or
a network.
"""

from __future__ import annotations

from typing import Mapping

from ..manifest import Deployment
from ._common import command_for, environment_for, header, port_for

NAME = "systemd"

#: Where the units expect to find things. Stated once so the install script and
#: the unit files cannot disagree about it.
ROOT = "/opt/slpie"
USER = "slpie"


def render(deployment: Deployment) -> Mapping[str, str]:
    files: dict[str, str] = {}
    for component in deployment.components:
        files[f"slpie-{component.name}.service"] = _unit(deployment, component)
    files["slpie.env"] = _environment(deployment)
    files["install.sh"] = _install(deployment)
    return files


def _unit(deployment: Deployment, component) -> str:
    # A range becomes a templated unit: `slpie-workers@1`, `@2`, and the
    # operator enables as many as they want. That is the closest thing systemd
    # has to a pool, and it is honest about being manual.
    templated = component.elastic
    name = f"slpie-{component.name}"
    lines = header(deployment) + [
        "",
        "[Unit]",
        f"Description=SLPIE {component.name} ({deployment.environment})",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"User={USER}",
        f"WorkingDirectory={ROOT}",
        f"EnvironmentFile={ROOT}/slpie.env",
        "ExecStart=" + " ".join(command_for(component)),
        "Restart=on-failure",
        "RestartSec=5",
        # The drain grace, honoured. A worker killed mid-scan drops
        # observations the ledger never recorded, which is the failure §23's
        # deallocation protocol exists to prevent.
        f"TimeoutStopSec={deployment.elasticity.drain_grace}",
        "KillSignal=SIGTERM",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
    ]
    if templated:
        lines.insert(
            len(header(deployment)) + 2,
            f"# Templated: enable one instance per worker, "
            f"{component.minimum}–{component.maximum} declared",
        )
    return "\n".join(lines) + "\n"


def _environment(deployment: Deployment) -> str:
    lines = header(deployment) + [""]
    lines += [f"{key}={value}" for key, value in environment_for(deployment).items()]
    return "\n".join(lines) + "\n"


def _install(deployment: Deployment) -> str:
    """The script, written to be *read* before it is run.

    `set -euo pipefail` first, because an install script that carries on after a
    failed step leaves a machine in a state nobody described.
    """
    units = [f"slpie-{item.name}" for item in deployment.components]
    templated = {
        f"slpie-{item.name}" for item in deployment.components if item.elastic
    }

    lines = header(deployment) + [
        "set -euo pipefail",
        "",
        f'# SLPIE — {deployment.environment}, on systemd. Read it before running it.',
        "",
        f'id -u {USER} >/dev/null 2>&1 || useradd --system --home {ROOT} {USER}',
        f'install -d -o {USER} -g {USER} {ROOT}',
        f'install -m 0640 -o {USER} -g {USER} slpie.env {ROOT}/slpie.env',
        "",
        "install -m 0644 slpie-*.service /etc/systemd/system/",
        "systemctl daemon-reload",
        "",
    ]
    for unit in units:
        if unit in templated:
            component = next(
                item for item in deployment.components if f"slpie-{item.name}" == unit
            )
            lines.append(
                f'for n in $(seq 1 {component.minimum}); do '
                f'systemctl enable --now "{unit}@$n"; done'
            )
        else:
            lines.append(f'systemctl enable --now {unit}')
    lines += ["", "systemctl --no-pager status " + " ".join(sorted(units)) + " || true"]
    return "\n".join(lines) + "\n"


def gaps(deployment: Deployment) -> tuple[str, ...]:
    found = []
    elastic = [item.name for item in deployment.components if item.elastic]
    if elastic:
        found.append(
            f"systemd has no autoscaler: {', '.join(elastic)} is emitted as a "
            f"templated unit started at its floor. Scaling is `systemctl enable`."
        )
    stores = [
        store.name for store in deployment.persistence.stores
        if store.engine != "sqlite"
    ]
    if stores:
        found.append(
            f"{', '.join(sorted(stores))} must already exist on this host or be "
            f"reachable from it: these units start SLPIE, not its dependencies."
        )
    if deployment.regions.replicas:
        found.append("systemd is one machine; declared replica regions are not rendered.")
    return tuple(found)
