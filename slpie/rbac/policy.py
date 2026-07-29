"""Roles as a config file, so maintenance is configuration and not a release.

This is the maintenance answer. A customer needs a role their organisation calls
`platform-approver` with permissions nobody else has; the options are a fork, a
plugin they have to write in Python, or a file. It is a file — `slpie.rbac.yaml`
— read by the same stdlib YAML reader the environment manifest uses, so it adds
no dependency and no build step.

```yaml
apiVersion: slpie/v1
kind: AccessPolicy

roles:
  viewer:
    description: read the graph and its findings
    permissions:
      - allow graph.read on "*"
      - allow findings.read on "*"

  payments-operator:
    inherits: [viewer]
    assurance: STRONG
    permissions:
      - allow deploy.apply on "env:prod/payments"
      - deny  deploy.destroy on "*"

separations:
  - name: change-approval
    roles: [payments-operator, payments-approver]
    reason: whoever requests a production change must not approve it
```

Three decisions worth defending:

**Permissions are written as one line, not as a nested object.** `allow
graph.read on "*"` is reviewable in a pull request by someone who does not know
this system. A four-key mapping per permission is more "structured" and nobody
reads it.

**The file cannot grant more than the code allows.** Loading validates against
the same `RoleGraph` rules — cycles, dangling parents, depth — so a config file
cannot produce a model the engine would refuse to build.

**System roles cannot be overridden.** A customer file that redefines `admin`
is rejected by name rather than silently merged, because a shipped role whose
meaning varies per deployment makes every support conversation start from zero.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..environment.schema import parse_yaml
from ..identity.principal import Assurance
from .model import Effect, Permission, RbacError
from .role import Role, RoleGraph, Separation

#: `allow graph.read on "env:prod/*" if business-hours` — the whole grammar.
_RULE = re.compile(
    r"""^\s*
    (?P<effect>allow|deny)\s+
    (?P<action>[^\s]+)
    (?:\s+on\s+(?P<resource>"[^"]*"|'[^']*'|[^\s]+))?
    (?:\s+if\s+(?P<condition>[A-Za-z0-9_.-]+))?
    (?:\s+requiring\s+(?P<assurance>[A-Za-z_]+))?
    \s*$""",
    re.VERBOSE | re.IGNORECASE,
)


class PolicyError(RbacError):
    """A policy file was malformed. The message names the line."""


def parse_rule(text: str) -> Permission:
    """One permission from one line.

    Errors quote the offending line verbatim. A config parser whose error is
    "invalid permission" costs the reader a bisect; one that shows the line
    costs them nothing.
    """
    match = _RULE.match(text)
    if match is None:
        raise PolicyError(
            f"cannot read permission {text!r}; expected "
            f"'allow <action> on <resource>' or 'deny <action> on <resource>'"
        )

    resource = (match.group("resource") or "*").strip("\"'")
    assurance_name = (match.group("assurance") or "STANDARD").upper()
    try:
        assurance = Assurance[assurance_name]
    except KeyError:
        raise PolicyError(
            f"unknown assurance {assurance_name!r} in {text!r}; expected one of "
            f"{', '.join(level.name for level in Assurance)}"
        ) from None

    return Permission(
        action=match.group("action"),
        resource=resource,
        effect=Effect(match.group("effect").lower()),
        assurance=assurance,
        condition=match.group("condition") or "",
        description=text.strip(),
    )


def load_policy(document: Mapping[str, Any], *, base: RoleGraph | None = None) -> RoleGraph:
    """Build a role graph from a parsed policy document.

    `base` carries the shipped system roles. Customer roles are layered on top
    and may *inherit* from system roles but never replace them, which is what
    keeps an upgrade from silently changing what a customer's `admin` means.
    """
    if not isinstance(document, Mapping):
        raise PolicyError("a policy document must be a mapping")

    kind = str(document.get("kind") or "AccessPolicy")
    if kind != "AccessPolicy":
        raise PolicyError(f"expected kind: AccessPolicy, found {kind!r}")

    graph = RoleGraph()
    if base is not None:
        for role in base:
            graph.add(role)
        for separation in base.separations:
            graph._separations.append(separation)   # already validated in `base`

    declared = document.get("roles") or {}
    if not isinstance(declared, Mapping):
        raise PolicyError("`roles:` must be a mapping of name to definition")

    for name, body in declared.items():
        graph.add(_role(str(name), body, graph))

    graph.validate()

    for entry in document.get("separations") or ():
        graph.separate(_separation(entry))

    return graph


def _role(name: str, body: Any, graph: RoleGraph) -> Role:
    existing = graph.get(name)
    if existing is not None and existing.system:
        raise PolicyError(
            f"role {name!r} is a system role and cannot be redefined in a policy "
            f"file; define a new role that inherits from it"
        )
    if not isinstance(body, Mapping):
        raise PolicyError(f"role {name!r} must be a mapping")

    rules = body.get("permissions") or ()
    if isinstance(rules, str):
        rules = [rules]

    assurance_name = str(body.get("assurance") or "STANDARD").upper()
    try:
        assurance = Assurance[assurance_name]
    except KeyError:
        raise PolicyError(
            f"role {name!r} declares unknown assurance {assurance_name!r}"
        ) from None

    inherits = body.get("inherits") or ()
    if isinstance(inherits, str):
        inherits = [inherits]

    try:
        return Role(
            name=name,
            permissions=tuple(parse_rule(str(rule)) for rule in rules),
            inherits=tuple(str(parent) for parent in inherits),
            description=str(body.get("description") or ""),
            assurance=assurance,
        )
    except RbacError as exc:
        raise PolicyError(f"role {name!r}: {exc}") from exc


def _separation(entry: Any) -> Separation:
    if not isinstance(entry, Mapping):
        raise PolicyError("each entry under `separations:` must be a mapping")
    roles = entry.get("roles") or ()
    if isinstance(roles, str):
        roles = [roles]
    try:
        return Separation(
            name=str(entry.get("name") or ""),
            roles=frozenset(str(role) for role in roles),
            reason=str(entry.get("reason") or ""),
        )
    except RbacError as exc:
        raise PolicyError(f"separation {entry.get('name')!r}: {exc}") from exc


def load_file(path: Path | str, *, base: RoleGraph | None = None) -> RoleGraph:
    """Read `slpie.rbac.yaml` from disk."""
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyError(f"cannot read policy file {target}: {exc}") from exc
    try:
        document = parse_yaml(text)
    except Exception as exc:  # noqa: BLE001 - the reader raises its own types
        raise PolicyError(f"{target} is not valid YAML: {exc}") from exc
    return load_policy(document, base=base)


def dump_policy(graph: RoleGraph) -> str:
    """Render a role graph back to policy text.

    Round-tripping matters more than it looks: it is how a model assembled in
    code becomes a reviewable file, and how the audit report can show the
    reviewer exactly what they are approving.
    """
    lines = ["apiVersion: slpie/v1", "kind: AccessPolicy", "", "roles:"]
    for role in sorted(graph, key=lambda item: item.name):
        lines.append(f"  {role.name}:")
        if role.description:
            lines.append(f"    description: {role.description}")
        if role.inherits:
            lines.append(f"    inherits: [{', '.join(sorted(role.inherits))}]")
        if role.assurance is not Assurance.STANDARD:
            lines.append(f"    assurance: {role.assurance.name}")
        if role.permissions:
            lines.append("    permissions:")
            for permission in role.permissions:
                suffix = f" if {permission.condition}" if permission.condition else ""
                requirement = (
                    f" requiring {permission.assurance.name}"
                    if permission.assurance is not Assurance.STANDARD else ""
                )
                lines.append(
                    f'      - {permission.effect.value} {permission.action} '
                    f'on "{permission.resource}"{suffix}{requirement}'
                )
    if graph.separations:
        lines.append("")
        lines.append("separations:")
        for separation in graph.separations:
            lines.append(f"  - name: {separation.name}")
            lines.append(f"    roles: [{', '.join(sorted(separation.roles))}]")
            lines.append(f"    reason: {separation.reason}")
    return "\n".join(lines) + "\n"


# --- what ships in the box -----------------------------------------------


def system_roles() -> RoleGraph:
    """The five roles every deployment starts with.

    Five, not fifty. A shipped role model that tries to anticipate every
    organisation produces a hundred roles nobody uses and one they edit; a small
    one that customers extend by file produces a model they understand. Each is
    marked `system`, so a policy file can inherit from them and cannot redefine
    them.
    """
    return RoleGraph([
        Role(
            name="viewer",
            description="read the graph, its evidence and its findings",
            system=True,
            permissions=(
                parse_rule('allow graph.read on "*"'),
                parse_rule('allow findings.read on "*"'),
                parse_rule('allow evidence.read on "*"'),
            ),
        ),
        Role(
            name="analyst",
            description="ask questions, run scans, and export artifacts",
            inherits=("viewer",),
            system=True,
            permissions=(
                parse_rule('allow scan.run on "*"'),
                parse_rule('allow question.ask on "*"'),
                parse_rule('allow artifact.generate on "*"'),
            ),
        ),
        Role(
            name="operator",
            description="manage connectors and the environments they reach",
            inherits=("analyst",),
            assurance=Assurance.STRONG,
            system=True,
            permissions=(
                parse_rule('allow connector.grant on "*"'),
                parse_rule('allow connector.revoke on "*"'),
                parse_rule('allow environment.bind on "*"'),
                # The one action that changes somebody else's infrastructure.
                parse_rule('deny deploy.destroy on "*"'),
            ),
        ),
        Role(
            name="approver",
            description="approve production changes; deliberately cannot make them",
            inherits=("viewer",),
            assurance=Assurance.STRONG,
            system=True,
            permissions=(
                parse_rule('allow change.approve on "*"'),
                parse_rule('deny deploy.apply on "*"'),
            ),
        ),
        Role(
            name="administrator",
            description="the whole platform, including access itself",
            inherits=("operator",),
            assurance=Assurance.STRONG,
            system=True,
            permissions=(
                parse_rule('allow access.bind on "*" requiring STRONG'),
                parse_rule('allow access.review on "*"'),
                parse_rule('allow policy.write on "*" requiring STRONG'),
            ),
        ),
    ], separations=[
        Separation(
            name="change-approval",
            roles=frozenset({"operator", "approver"}),
            reason=(
                "whoever can apply a production change must not be able to "
                "approve it; this is the control every audit asks for by name"
            ),
        ),
        Separation(
            name="access-self-grant",
            roles=frozenset({"administrator", "approver"}),
            reason=(
                "an administrator who can also approve can grant themselves "
                "anything and sign it off"
            ),
        ),
    ])
