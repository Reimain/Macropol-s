"""Route → (action, resource), derived rather than listed.

Eighty-one routes and one hand-maintained table would be eighty-one chances to
forget an entry, and a forgotten entry is a route with no authorisation rather
than a route that fails loudly. So the pair is *computed* from the registry, the
same way the routes themselves are.

The shape is thirteen wildcardable families over forty-eight leaves, so an
operator writes `allow analysis.* on "*"` and gets the analysis family — which
is what they meant. That only works because actions are dotted:
`matches_action` has never understood `workspace:*`, which is why
`slpie/workspace/plane.py` spelled its actions with a colon and could not be
granted by any wildcard at all.

Note the asymmetry, which is deliberate: **actions are dotted, resources keep
the colon.** `env:prod/*`, `dataset:sales` and `repo:acme/payments` use the
colon as a kind separator, and flattening it there would make `env:prod` and
`env.prod` the same resource.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..compose.registry import VerbRegistry, registry as default_registry

#: Routes anybody may call, authenticated or not. Discovery is not a privilege:
#: refusing to say what the platform can do makes it undiscoverable without
#: making it safer, since the answer is in the published contract anyway.
OPEN = frozenset({
    "/api/routes", "/api/contract", "/api/verbs", "/api/manual", "/api/screens",
    "/api/stream/status",
    # Which shells exist and what each can draw. Open with the rest of
    # discovery, and for one reason more: a console that cannot ask this cannot
    # tell a reader *why* a screen is unavailable, and would fall back to
    # silently omitting it — which is the drift the route exists to close.
    "/api/shells",
    # The words the interface renders with (§31). Open for the same reason the
    # rest are, plus one of its own: a console refused its lexicon has no
    # labels, which is a broken product rather than a guarded one. It discloses
    # nothing about an environment — only what this platform calls its own
    # nouns — and an unrecognised profile yields the defaults rather than
    # confirming which profiles exist.
    "/api/lexicon",
})

DISCOVER = "platform.discover"

#: The hand-declared read routes, mapped to the same action as the verb that
#: answers the same question. One action, two transports — so a grant of
#: `analysis.findings` covers `GET /api/findings` and `POST /api/v/findings`
#: alike, which is what an operator granting "may read findings" means.
READS: Mapping[str, tuple[str, str]] = {
    "/api/status": ("environment.status", "env:{environment}"),
    "/api/manifest": ("environment.declare", "env:{environment}"),
    "/api/station": ("environment.attach", "env:{environment}"),
    "/api/graph": ("environment.graph", "env:{environment}"),
    "/api/node": ("environment.graph", "env:{environment}"),
    "/api/search": ("environment.search", "env:{environment}"),
    "/api/impact": ("environment.impact", "env:{environment}"),
    "/api/cycles": ("environment.graph", "env:{environment}"),
    "/api/reconcile": ("environment.reconcile", "env:{environment}"),
    "/api/findings": ("analysis.findings", "*"),
    "/api/history": ("dispatch.history", "*"),
    "/api/causation": ("dispatch.history", "*"),
    "/api/integrity": ("platform.ledger", "*"),
    "/api/projections": ("platform.ledger", "*"),
    "/api/scenarios": ("environment.fire", "env:{environment}"),
    "/api/stream": ("platform.stream", "*"),
    "/api/ask": ("intelligence.ask", "*"),
    "/api/plan": ("intelligence.plan", "*"),
    "/api/compose/validate": (DISCOVER, "*"),
    "/api/scan": ("environment.scan", "env:{environment}"),
    "/api/scenario": ("environment.fire", "env:{environment}"),
    "/api/snapshot": ("platform.seal", "*"),
    "/api/target": ("environment.target", "env:{environment}"),
    "/api/admin/workspaces": ("workspace.read", "tenant:*"),
    "/api/admin/quota": ("workspace.read", "tenant:*"),
    "/api/admin/datasets": ("dataset.read", "*"),
}

#: `/api/run` is the one route whose action cannot be a single value: it takes a
#: whole composition, so every stage's action applies and a deny on any of them
#: refuses the call. `discover . | link | target --live` must be refused because
#: of the *last* stage, and only a per-stage check catches that.
COMPOSITION = "/api/run"


@dataclass(frozen=True, slots=True)
class ActionMap:
    """What one route needs, resolved."""

    action: str
    resource: str
    #: Set only for `/api/run`, where the caller must hold every one.
    stages: tuple[str, ...] = ()
    open_to_all: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "resource": self.resource,
            "stages": list(self.stages),
            "open": self.open_to_all,
        }


def action_for(
    method: str,
    path: str,
    *,
    verbs: VerbRegistry | None = None,
    environment: str = "*",
    pipeline: str = "",
) -> ActionMap:
    """The action and resource this call needs.

    `pipeline` is only read for `/api/run`, and an unparseable one yields no
    stages — which the gateway treats as a refusal rather than as permission,
    because a composition nobody could read is not a composition anybody should
    be allowed to run.
    """
    verbs = verbs if verbs is not None else default_registry()

    if path in OPEN:
        return ActionMap(DISCOVER, "*", open_to_all=True)

    if path.startswith("/api/v/"):
        name = path[len("/api/v/"):]
        verb = verbs.get(name) if hasattr(verbs, "get") else None
        if verb is None:
            for candidate in verbs:
                if candidate.name == name:
                    verb = candidate
                    break
        if verb is not None:
            resource = (
                f"env:{environment}" if verb.group == "environment" else "*"
            )
            return ActionMap(f"{verb.group}.{verb.name}", resource)
        return ActionMap(f"unknown.{name}", "*")

    if path.startswith("/api/apim/"):
        noun = path[len("/api/apim/"):].split("/")[0] or "catalog"
        return ActionMap(f"apim.{noun}.read", "*")

    if path == COMPOSITION:
        stages = _stages(pipeline, verbs)
        return ActionMap(
            "compose.run", f"env:{environment}", stages=stages,
        )

    known = READS.get(path)
    if known:
        action, resource = known
        return ActionMap(action, resource.format(environment=environment))

    # An unmapped route is not open. Defaulting to `platform.discover` here
    # would silently grant every new route to everybody, which is the failure
    # mode a default-deny system exists to avoid.
    return ActionMap("platform.unmapped", "*")


def _stages(pipeline: str, verbs: VerbRegistry) -> tuple[str, ...]:
    found: list[str] = []
    for part in (pipeline or "").split("|"):
        name = part.strip().split(" ")[0].strip()
        if not name:
            continue
        for verb in verbs:
            if verb.name == name:
                found.append(f"{verb.group}.{verb.name}")
                break
    return tuple(found)


def families(verbs: VerbRegistry | None = None) -> tuple[str, ...]:
    """The wildcardable action families, for a role editor to offer."""
    verbs = verbs if verbs is not None else default_registry()
    return tuple(sorted({f"{verb.group}.*" for verb in verbs} | {
        "platform.*", "apim.*", "workspace.*", "dataset.*", "compose.*",
    }))


def coverage(
    routes: Sequence[tuple[str, str]], *, verbs: VerbRegistry | None = None,
) -> dict[str, list[str]]:
    """Every route, grouped by the action it needs.

    The gateway screen renders this, and a test asserts no route lands in
    `platform.unmapped` — which is how a route added without an action is
    caught on the commit that adds it.
    """
    grouped: dict[str, list[str]] = {}
    for method, path in routes:
        mapped = action_for(method, path, verbs=verbs)
        grouped.setdefault(mapped.action, []).append(f"{method} {path}")
    return {action: sorted(paths) for action, paths in sorted(grouped.items())}
