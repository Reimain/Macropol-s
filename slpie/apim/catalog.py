"""The API catalogue, projected from the verb registry.

**One API per verb group, plus one per read family.** This is the most important
decision in the section: a hand-maintained list of APIs drifts within a week,
and then the Developer Portal documents operations the gateway does not enforce
and the gateway enforces operations nobody can find. Projecting it is the same
move `Api._register_composition` makes for routes, for the same reason — there
is nowhere to forget to wire something.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence

from ..compose.registry import VerbRegistry, registry as default_registry
from .action import action_for
from .lifecycle import ApiState, advance, describe


@dataclass(frozen=True, slots=True)
class Operation:
    """One callable thing on one API."""

    method: str
    path: str
    action: str
    resource: str = "*"
    summary: str = ""
    mutates: bool = False
    cacheable: bool = False
    consumes: str = "any"
    produces: str = "same"
    #: Empty means "inherit the API's default", so a tier is stated once per API
    #: and overridden only where an operation genuinely differs.
    throttle: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "path": self.path,
            "action": self.action,
            "resource": self.resource,
            "summary": self.summary,
            "mutates": self.mutates,
            "cacheable": self.cacheable,
            "consumes": self.consumes,
            "produces": self.produces,
            "throttle": self.throttle,
        }


@dataclass(frozen=True, slots=True)
class ApiDefinition:
    """One API: a name, a version, a state, and the operations it serves."""

    api_id: str
    name: str
    version: str = "v1"
    state: ApiState = ApiState.PUBLISHED
    operations: tuple[Operation, ...] = ()
    default_throttle: str = "gold"
    visibility: str = "public"          # public | restricted | private
    tags: tuple[str, ...] = ()
    documentation: str = ""
    deprecated_at: float = 0.0
    sunset_at: float = 0.0

    @property
    def serves(self) -> bool:
        return self.state.serves

    def throttle_for(self, operation: Operation) -> str:
        return operation.throttle or self.default_throttle

    def advance(self, target: ApiState, *, reason: str = "", actor: str = "") -> "ApiDefinition":
        """A new definition in the new state. Append-only in spirit: the caller
        keeps the old one if it wants the history."""
        moved = advance(self.state, target, reason=reason, actor=actor)
        return ApiDefinition(
            api_id=self.api_id, name=self.name, version=self.version, state=moved,
            operations=self.operations, default_throttle=self.default_throttle,
            visibility=self.visibility, tags=self.tags,
            documentation=self.documentation,
            deprecated_at=self.deprecated_at, sunset_at=self.sunset_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_id": self.api_id,
            "name": self.name,
            "version": self.version,
            "visibility": self.visibility,
            "default_throttle": self.default_throttle,
            "tags": list(self.tags),
            "documentation": self.documentation,
            "operations": [operation.to_dict() for operation in self.operations],
            **describe(self.state),
        }


#: Action families that are not verb groups, mapped to the API they belong to.
#: Anything absent becomes an API of its own name, which is the right default:
#: an unrecognised family should surface as an API nobody has documented rather
#: than be quietly folded into one that is.
_FAMILY_TO_API = {
    "workspace": "admin",
    "dataset": "admin",
    "compose": "platform",
    "apim": "platform",
}

#: POSTs that read. `POST /api/v/discover` takes a body and changes nothing —
#: the method is a transport detail, and treating every POST as a mutation would
#: throttle the read path at the write path's tier.
_READ_POSTS = frozenset({
    "/api/ask", "/api/plan", "/api/compose/validate", "/api/run",
})


@dataclass
class ApiCatalog:
    """Every API this build serves."""

    apis: dict[str, ApiDefinition] = field(default_factory=dict)

    def __iter__(self) -> Iterator[ApiDefinition]:
        return iter(sorted(self.apis.values(), key=lambda item: item.api_id))

    def __len__(self) -> int:
        return len(self.apis)

    def get(self, api_id: str) -> ApiDefinition | None:
        return self.apis.get(api_id)

    def add(self, definition: ApiDefinition) -> ApiDefinition:
        self.apis[definition.api_id] = definition
        return definition

    def for_route(self, method: str, path: str) -> tuple[ApiDefinition, Operation] | None:
        """Which API serves this call, and which operation it is."""
        for definition in self.apis.values():
            for operation in definition.operations:
                if operation.method == method and operation.path == path:
                    return definition, operation
        return None

    @classmethod
    def from_registry(
        cls,
        *,
        verbs: VerbRegistry | None = None,
        routes: Sequence[tuple[str, str]] = (),
    ) -> "ApiCatalog":
        """The catalogue, derived. Adding a verb adds an operation."""
        verbs = verbs if verbs is not None else default_registry()
        catalog = cls()

        grouped: dict[str, list[Operation]] = {}
        for verb in verbs:
            mapped = action_for("POST", f"/api/v/{verb.name}", verbs=verbs)
            grouped.setdefault(verb.group, []).append(Operation(
                method="POST",
                path=f"/api/v/{verb.name}",
                action=mapped.action,
                resource=mapped.resource,
                summary=verb.summary,
                mutates=verb.mutates,
                cacheable=not verb.mutates,
                consumes=verb.consumes.value if verb.consumes else "nothing",
                produces=verb.produces.value,
                # A verb that changes the environment is not a verb to allow at
                # ten a second. The tier is derived from what it does rather
                # than assigned, so a new mutating verb is throttled correctly
                # without anybody remembering to say so.
                throttle="bronze" if verb.mutates else "",
            ))

        for group, operations in sorted(grouped.items()):
            catalog.add(ApiDefinition(
                api_id=group,
                name=group.replace("-", " ").title(),
                operations=tuple(sorted(operations, key=lambda item: item.path)),
                tags=("verbs",),
                documentation=f"The {group} verbs, as HTTP.",
            ))

        # Every remaining route joins the API named by its action family, so
        # `GET /api/findings` sits on the same API as `POST /api/v/findings` —
        # one action, two transports. Any route the catalogue does not place is
        # a route the gateway would wave through, so placement is total by
        # construction rather than by a list somebody maintains.
        extra: dict[str, list[Operation]] = {}
        for method, path in sorted(routes):
            if path.startswith("/api/v/"):
                continue
            mapped = action_for(method, path, verbs=verbs)
            family = mapped.action.split(".")[0]
            api_id = family if family in grouped else _FAMILY_TO_API.get(family, family)
            extra.setdefault(api_id, []).append(Operation(
                method=method, path=path,
                action=mapped.action, resource=mapped.resource,
                summary=f"{method} {path}",
                mutates=method == "POST" and path not in _READ_POSTS,
                cacheable=method == "GET" and path != "/api/stream",
                throttle="unlimited" if mapped.open_to_all else "",
            ))

        for api_id, operations in sorted(extra.items()):
            held = catalog.get(api_id)
            if held is not None:
                catalog.add(ApiDefinition(
                    api_id=held.api_id, name=held.name, version=held.version,
                    state=held.state,
                    operations=tuple(sorted(
                        held.operations + tuple(operations),
                        key=lambda item: (item.path, item.method),
                    )),
                    default_throttle=held.default_throttle,
                    visibility=held.visibility, tags=held.tags,
                    documentation=held.documentation,
                ))
                continue
            catalog.add(ApiDefinition(
                api_id=api_id,
                name=api_id.title(),
                operations=tuple(sorted(operations, key=lambda item: item.path)),
                default_throttle="unlimited" if api_id == "platform" else "silver",
                visibility="public" if api_id == "platform" else "restricted",
                tags=("reads",),
            ))

        return catalog

    def to_dict(self) -> dict[str, Any]:
        return {"apis": [definition.to_dict() for definition in self]}
