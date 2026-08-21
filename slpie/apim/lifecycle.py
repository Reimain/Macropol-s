"""An API's life, as a closed transition table.

The table is the whole design. An open set of states with ad-hoc guards spread
across handlers is how an API ends up published and retired at once, and the
symptom is a consumer holding a key to something that no longer exists.

`RETIRED` is terminal and never reopens, which mirrors
`connectors/keyring.py:GrantStatus.is_terminal` for the same reason: a
subscription to a retired API must not become live again because somebody
un-retired it, since the consumers were told it was gone and acted on that.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from .errors import ApimError, LifecycleRefused


class ApiState(str, Enum):
    """Where an API is in its life."""

    CREATED = "created"          # visible to its publisher only
    PUBLISHED = "published"      # discoverable and subscribable
    DEPRECATED = "deprecated"    # still served, with Deprecation and Sunset
    BLOCKED = "blocked"          # refused at the gateway, not withdrawn
    RETIRED = "retired"          # gone; 410 for ever

    @property
    def is_terminal(self) -> bool:
        return self is ApiState.RETIRED

    @property
    def serves(self) -> bool:
        """Whether a call to this API reaches a handler at all."""
        return self in (ApiState.PUBLISHED, ApiState.DEPRECATED)


#: Every legal move. Anything absent is refused, which is the point of writing
#: it as data — a reader can see the whole machine without tracing handlers.
TRANSITIONS: Mapping[ApiState, tuple[ApiState, ...]] = {
    ApiState.CREATED: (ApiState.PUBLISHED, ApiState.RETIRED),
    ApiState.PUBLISHED: (
        ApiState.DEPRECATED, ApiState.BLOCKED, ApiState.CREATED, ApiState.RETIRED,
    ),
    ApiState.DEPRECATED: (ApiState.PUBLISHED, ApiState.RETIRED),
    ApiState.BLOCKED: (ApiState.PUBLISHED, ApiState.RETIRED),
    ApiState.RETIRED: (),
}

#: Moves that take a consumer's access away. These demand a stated reason, for
#: the same argument `Keyring.revoke` makes: "we accepted this" and "we stopped
#: this" both have to be answerable later, with a name attached.
REASON_REQUIRED = frozenset({ApiState.RETIRED, ApiState.BLOCKED, ApiState.DEPRECATED})


def advance(
    current: ApiState,
    target: ApiState,
    *,
    reason: str = "",
    actor: str = "",
) -> ApiState:
    """The new state, or a refusal naming what this one can reach."""
    allowed = TRANSITIONS.get(current, ())
    if target not in allowed:
        raise LifecycleRefused(
            current.value, target.value, tuple(state.value for state in allowed),
        )
    if target in REASON_REQUIRED and not reason.strip():
        who = f" by {actor}" if actor else ""
        raise ApimError(
            f"moving an API to {target.value!r}{who} takes access away from its "
            f"consumers and needs a stated reason"
        )
    return target


def describe(state: ApiState) -> dict[str, Any]:
    """The state and what it can reach, for the publisher screen."""
    return {
        "state": state.value,
        "serves": state.serves,
        "terminal": state.is_terminal,
        "may_reach": [target.value for target in TRANSITIONS.get(state, ())],
        "reason_required": [
            target.value for target in TRANSITIONS.get(state, ())
            if target in REASON_REQUIRED
        ],
    }
