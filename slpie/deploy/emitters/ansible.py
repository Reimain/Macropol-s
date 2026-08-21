"""A playbook, an inventory and roles — for the estate with hosts and no cluster.

Terraform provisions *cloud* resources and stops where the cloud does: it can
create a managed database, and on two of the three clouds it cannot create a
RabbitMQ because there is no managed RabbitMQ to create. Kubernetes and Helm
assume a cluster. systemd assumes the dependencies already exist on the box.

That leaves a real and extremely common case with no emitter: **a handful of
Linux hosts, and everything must be installed onto them.** That is Ansible's,
and it is the honest answer to the gap the Terraform emitter reports rather
than a second way to do what another emitter already does.

── It is idempotent or it is a shell script with extra steps ────────────

Every task here is declarative — a package state, a service state, a user, a
templated file. None of them is `command:` or `shell:`, which is what makes a
second run a no-op instead of a second install. That property is the whole
reason to reach for Ansible over the install script the systemd emitter already
writes, so a test asserts it: an emitted play containing a shell task would mean
this emitter has stopped being worth its existence.

── Why the broker gets its own role ─────────────────────────────────────

RabbitMQ is not a detail of the worker role. It is the thing that makes a
worker's death survivable — `task_acks_late` returns an unacknowledged unit to
the queue — and an estate that installed it as a side effect of installing
workers would have no way to run it on its own host, which is where it belongs.
"""

from __future__ import annotations

from typing import Mapping

from ..manifest import Deployment
from ._common import command_for, environment_for, header, port_for

NAME = "ansible"

ROOT = "/opt/slpie"
USER = "slpie"


def render(deployment: Deployment) -> Mapping[str, str]:
    files = {
        "ansible.cfg": _config(deployment),
        "inventory.ini": _inventory(deployment),
        "site.yml": _playbook(deployment),
        "group_vars/all.yml": _variables(deployment),
        "roles/slpie/tasks/main.yml": _slpie_role(deployment),
        "roles/slpie/templates/slpie.env.j2": _templated(
            deployment, _environment_template()),
        "roles/slpie/templates/slpie.service.j2": _templated(
            deployment, _unit_template()),
    }
    for store in deployment.persistence.stores:
        role = ROLES.get(store.engine)
        if role:
            files[f"roles/{store.engine}/tasks/main.yml"] = role(deployment, store)
    return files


def _config(deployment: Deployment) -> str:
    return "\n".join(header(deployment) + [
        "",
        "[defaults]",
        "inventory = inventory.ini",
        "host_key_checking = True",
        # Deliberate: a run that silently continued past a failed host would
        # leave half an estate deployed and report success.
        "any_errors_fatal = True",
        "stdout_callback = yaml",
    ]) + "\n"


def _inventory(deployment: Deployment) -> str:
    """One group per component, plus one per declared store.

    Hosts are *not* invented. The file lists the groups with a comment where the
    addresses go, because an emitter that guessed `10.0.0.1` would produce a
    playbook that runs confidently against somebody else's machine.
    """
    lines = header(deployment) + [""]
    for component in deployment.components:
        lines += [
            f"[{component.name}]",
            f"# one host per replica — {component.size} declared"
            + (f", scaling to {component.maximum}" if component.elastic else ""),
            "",
        ]
    for store in deployment.persistence.stores:
        if store.engine in ROLES:
            lines += [f"[{store.engine}]", "# the host that runs it", ""]
    return "\n".join(lines) + "\n"


def _playbook(deployment: Deployment) -> str:
    """Stores first, then the platform. The order is the dependency.

    A worker that starts before its broker exists fails its first connection
    and retries, which works and fills the log with noise that looks like a
    fault. Ordering the plays removes the question.
    """
    plays = header(deployment) + ["", "---"]

    for store in deployment.persistence.stores:
        if store.engine not in ROLES:
            continue
        plays += [
            f"- name: {store.name} ({store.engine})",
            f"  hosts: {store.engine}",
            "  become: true",
            f"  roles: [{store.engine}]",
            "",
        ]

    for component in deployment.components:
        plays += [
            f"- name: slpie {component.name}",
            f"  hosts: {component.name}",
            "  become: true",
            "  roles:",
            "    - role: slpie",
            "      vars:",
            f"        component: {component.name}",
            f"        command: {list(command_for(component))}",
            f"        port: {port_for(component)}",
            "",
        ]
    return "\n".join(plays) + "\n"


def _variables(deployment: Deployment) -> str:
    lines = header(deployment) + ["", "---", f"environment_name: {deployment.environment}",
                                  f"slpie_root: {ROOT}", f"slpie_user: {USER}", "",
                                  "slpie_env:"]
    for key, value in environment_for(deployment).items():
        lines.append(f"  {key}: \"{value}\"")
    lines += [
        "",
        "# Secrets are *not* here. Put them in an Ansible vault or pass them with",
        "# `-e`; a rendered file in a repository is the wrong home for a password,",
        "# and an emitter that wrote one would be making that decision for you.",
        "rabbitmq_password: \"{{ vault_rabbitmq_password | default('') }}\"",
        "postgres_password: \"{{ vault_postgres_password | default('') }}\"",
    ]
    return "\n".join(lines) + "\n"


def _slpie_role(deployment: Deployment) -> str:
    return "\n".join(header(deployment) + [
        "",
        "---",
        "- name: A system user that owns nothing else",
        "  ansible.builtin.user:",
        "    name: \"{{ slpie_user }}\"",
        "    system: true",
        "    home: \"{{ slpie_root }}\"",
        "    create_home: true",
        "",
        "- name: The install root",
        "  ansible.builtin.file:",
        "    path: \"{{ slpie_root }}\"",
        "    state: directory",
        "    owner: \"{{ slpie_user }}\"",
        "    mode: \"0755\"",
        "",
        "- name: Python, and pip to install with",
        "  ansible.builtin.package:",
        "    name: [python3, python3-pip, python3-venv]",
        "    state: present",
        "",
        "- name: SLPIE, in its own virtualenv",
        "  ansible.builtin.pip:",
        "    name: \"slpie[enterprise]\"",
        "    virtualenv: \"{{ slpie_root }}/venv\"",
        "    virtualenv_command: python3 -m venv",
        "  become_user: \"{{ slpie_user }}\"",
        "  notify: restart slpie",
        "",
        "- name: The environment file",
        "  ansible.builtin.template:",
        "    src: slpie.env.j2",
        "    dest: \"{{ slpie_root }}/slpie.env\"",
        "    owner: \"{{ slpie_user }}\"",
        # 0640: readable by the service user and nobody else. It carries
        # connection strings.
        "    mode: \"0640\"",
        "  notify: restart slpie",
        "",
        "- name: The unit",
        "  ansible.builtin.template:",
        "    src: slpie.service.j2",
        "    dest: \"/etc/systemd/system/slpie-{{ component }}.service\"",
        "    mode: \"0644\"",
        "  notify: restart slpie",
        "",
        "- name: Running, and enabled at boot",
        "  ansible.builtin.systemd:",
        "    name: \"slpie-{{ component }}\"",
        "    state: started",
        "    enabled: true",
        "    daemon_reload: true",
    ]) + "\n"


def _templated(deployment: Deployment, body: str) -> str:
    """A Jinja template carrying both banners, which are not the same statement.

    Ours says the file came from the deployment manifest and that editing it
    here is pointless. Ansible's — inside the body — says the *rendered* copy on
    the host is not the place to edit either. A reader can arrive at either
    copy, and each needs to be told where the real source is.
    """
    return "\n".join(f"{{# {line} #}}" for line in header(deployment, comment="")) + "\n" + body


def _environment_template() -> str:
    return (
        "# Rendered by Ansible from group_vars. Do not edit on the host.\n"
        "{% for key, value in slpie_env.items() %}\n"
        "{{ key }}={{ value }}\n"
        "{% endfor %}\n"
    )


def _unit_template() -> str:
    return "\n".join([
        "# Rendered by Ansible. Do not edit on the host.",
        "[Unit]",
        "Description=SLPIE {{ component }} ({{ environment_name }})",
        "After=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        "User={{ slpie_user }}",
        "WorkingDirectory={{ slpie_root }}",
        "EnvironmentFile={{ slpie_root }}/slpie.env",
        "ExecStart={{ slpie_root }}/venv/bin/{{ command | join(' ') }}",
        "Restart=on-failure",
        "KillSignal=SIGTERM",
        "TimeoutStopSec={{ drain_grace | default('5m') }}",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
    ]) + "\n"


def _rabbitmq_role(deployment: Deployment, store) -> str:
    """The broker, with the management plugin and a real user.

    The `guest` account is left alone rather than used: RabbitMQ refuses it
    over anything but loopback, so a deployment that relied on it would work in
    a container and fail the moment a worker was on another host — which is the
    only configuration where a broker is doing anything.
    """
    return "\n".join(header(deployment) + [
        "",
        "---",
        "- name: RabbitMQ",
        "  ansible.builtin.package:",
        "    name: rabbitmq-server",
        "    state: present",
        "",
        "- name: Running, and enabled at boot",
        "  ansible.builtin.systemd:",
        "    name: rabbitmq-server",
        "    state: started",
        "    enabled: true",
        "",
        "- name: The management plugin",
        "  # Not cosmetic: queue depth comes from its HTTP API, and depth is what",
        "  # the elasticity curve is computed from. Without it the platform can",
        "  # run scans and cannot explain its own replica count.",
        "  community.rabbitmq.rabbitmq_plugin:",
        "    names: rabbitmq_management",
        "    state: enabled",
        "",
        "- name: A user that is not guest",
        "  community.rabbitmq.rabbitmq_user:",
        "    user: slpie",
        "    password: \"{{ rabbitmq_password }}\"",
        "    vhost: /",
        "    configure_priv: .*",
        "    read_priv: .*",
        "    write_priv: .*",
        "    tags: management",
        "    state: present",
        "  no_log: true",
        "",
        "- name: guest reaches nothing beyond loopback",
        "  # RabbitMQ's own default, asserted rather than assumed: a package",
        "  # that shipped it open would be open here too.",
        "  community.rabbitmq.rabbitmq_user:",
        "    user: guest",
        "    state: absent",
    ]) + "\n"


def _postgres_role(deployment: Deployment, store) -> str:
    return "\n".join(header(deployment) + [
        "",
        "---",
        "- name: PostgreSQL",
        "  ansible.builtin.package:",
        "    name: [postgresql, python3-psycopg2]",
        "    state: present",
        "",
        "- name: Running, and enabled at boot",
        "  ansible.builtin.systemd:",
        "    name: postgresql",
        "    state: started",
        "    enabled: true",
        "",
        "- name: The role SLPIE connects as",
        "  community.postgresql.postgresql_user:",
        "    name: slpie",
        "    password: \"{{ postgres_password }}\"",
        "  become_user: postgres",
        "  no_log: true",
        "",
        "- name: The database",
        "  community.postgresql.postgresql_db:",
        "    name: slpie",
        "    owner: slpie",
        "  become_user: postgres",
    ]) + "\n"


def _redis_role(deployment: Deployment, store) -> str:
    return "\n".join(header(deployment) + [
        "",
        "---",
        "- name: Redis, as the result backend",
        "  ansible.builtin.package:",
        "    name: redis-server",
        "    state: present",
        "",
        "- name: Running, and enabled at boot",
        "  ansible.builtin.systemd:",
        "    name: redis-server",
        "    state: started",
        "    enabled: true",
    ]) + "\n"


#: Stores this emitter knows how to install. One outside the table is a managed
#: service and is reported rather than installed.
ROLES = {
    "rabbitmq": _rabbitmq_role,
    "postgres": _postgres_role,
    "redis": _redis_role,
}


def gaps(deployment: Deployment) -> tuple[str, ...]:
    found = [
        "the inventory lists groups and no hosts: an emitter that guessed an "
        "address would produce a playbook that runs confidently against "
        "somebody else's machine. Fill it in before running anything.",
        "secrets are referenced and never written. `rabbitmq_password` and "
        "`postgres_password` expect an Ansible vault or `-e`; a rendered file "
        "in a repository is the wrong home for either.",
    ]
    elastic = [item.name for item in deployment.components if item.elastic]
    if elastic:
        found.append(
            f"Ansible has no autoscaler: {', '.join(elastic)} is installed on "
            f"the hosts in its group. Scaling is adding a host to the inventory "
            f"and re-running."
        )
    external = sorted(
        f"{store.name} ({store.engine})" for store in deployment.persistence.stores
        if store.engine not in ROLES
    )
    if external:
        found.append(
            f"{', '.join(external)} is a managed service and is not installed "
            f"onto a host; point the matching environment variable at a real one."
        )
    return tuple(found)
