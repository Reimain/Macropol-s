"""FastAPI over the same route table, and nothing more.

Ring 1 does not get its own API. It gets a *transport* for the one ring 0
already has: `Api.routes` is the route table, `Api.handle` is the single entry
point, and the gateway, the RBAC check, the live-target guard and the version
headers all live behind it. This package translates ASGI to `Request` and
`Response` back, which is the whole job.

That is §16's rule, and it is not stylistic. The live gate is the class of thing
that must have exactly one implementation: a second one is a second place to
forget the confirmation, and the forgetting is invisible until somebody points
it at production.
"""

from __future__ import annotations

from .adapter import create_app, route_set

__all__ = ["create_app", "route_set"]
