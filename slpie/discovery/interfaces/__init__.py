"""Discoverers for interface contracts — the shapes services agree on.

REST, event-driven, GraphQL, gRPC and MQTT descriptions all answer the same
question in different notation: *what can be called, and what flows where*. Each
discoverer here turns one of those notations into API, topic and schema nodes
with the operation's own line cited, so "who breaks if this field changes" has
an answer that terminates in a file a human can open.

Direction is the recurring hazard. AsyncAPI 2.x inverts the words `publish` and
`subscribe` relative to every reader's intuition, and getting it backwards
reverses every event edge in the graph — see
:mod:`slpie.discovery.interfaces.asyncapi`.

**Incomplete.** OpenAPI and AsyncAPI are written; GraphQL, gRPC and MQTT are
not, and are therefore not exported. Nothing in this package is registered in
:mod:`slpie.discovery.registry` yet, so it is inert until phase 8 is finished.
"""

from __future__ import annotations

from . import asyncapi, openapi

__all__ = ["openapi", "asyncapi"]
