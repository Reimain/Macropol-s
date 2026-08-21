"""`LISTEN`/`NOTIFY` as an `EventBus` transport.

The piece phase 16 needs and phase 15 is the right place to build: a Celery
worker's observations have to reach a web server's SSE stream, and those are
different processes. In-process the `EventBus` fans out through a Python set of
subscribers; across processes it needs a wire, and Postgres already has one.

**Why not Redis, which is in the same extra.** The events being carried are
already durable in the ledger a transaction earlier; what crosses the wire is a
*notification that a sequence exists*, not the event itself. Postgres delivers
that notification **inside the same transaction that appended the record**, so
a subscriber cannot be told about a sequence that later rolls back. A separate
broker would be a second thing to run and a second place for the two facts to
disagree.

**The payload is a sequence, never an event.** `NOTIFY` truncates at 8000 bytes
and a subscriber that parsed a truncated event would act on half a fact. So the
wire carries `{"since": n}` and the subscriber reads the ledger — which is the
single-writer/replicated-reader shape §23 already decided, applied between two
processes rather than two regions.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterator

CHANNEL = "slpie_ledger"


def announce(cursor: Any, sequence: int, *, channel: str = CHANNEL) -> None:
    """Tell listeners the ledger has reached `sequence`.

    Takes a cursor rather than a connection on purpose: the caller passes the
    one already inside the append transaction, so the notification commits with
    the record or not at all.
    """
    # `pg_notify(channel, payload)` rather than the `NOTIFY` statement.
    #
    # `NOTIFY` is a utility statement and takes no parameters at all — not for
    # the payload and not for the channel — so building it means interpolating
    # both into SQL. `pg_notify` is an ordinary function: both arguments bind,
    # and this path reaches SQL with no text substitution whatsoever. Found by
    # running it; the first version interpolated the payload and Postgres
    # refused with a syntax error at `$1`, which is the good kind of failure.
    cursor.execute(
        "SELECT pg_notify(%s, %s)", (channel, json.dumps({"since": sequence})),
    )


def listen(connection: Any, *, channel: str = CHANNEL) -> None:
    """Subscribe this connection. It must be in autocommit."""
    if not connection.autocommit:
        raise RuntimeError(
            "LISTEN needs an autocommit connection; inside a transaction the "
            "subscription is not visible until commit and notifications queue "
            "behind it"
        )
    with connection.cursor() as cursor:
        cursor.execute(f"LISTEN {_identifier(channel)}")


def follow(connection: Any, *, timeout: float | None = None) -> Iterator[int]:
    """Yield each sequence announced, as it arrives.

    Blocking, so it belongs in a thread or a worker loop. `timeout` is for
    tests: without one this never returns, which is correct for a daemon and
    unusable for a test that has to finish.
    """
    for notification in connection.notifies(timeout=timeout):
        payload = notification.payload
        try:
            yield int(json.loads(payload)["since"])
        except (TypeError, ValueError, KeyError):
            # A malformed payload is somebody else using the channel. Skipping
            # it is right; crashing the feed over it is not.
            continue


def bridge(connection: Any, deliver: Callable[[int], None], *, timeout: float = 1.0) -> int:
    """Drain whatever has been announced, handing each sequence to `deliver`.

    Returns how many were delivered, so a caller can tell "nothing arrived"
    from "the bridge is not running" — which is the distinction a health check
    needs and a bare `None` cannot make.
    """
    delivered = 0
    for sequence in follow(connection, timeout=timeout):
        deliver(sequence)
        delivered += 1
    return delivered


def _identifier(name: str) -> str:
    """A channel name safe to interpolate.

    `LISTEN` is a utility statement and takes no parameters, so this is the one
    place — the *only* place, now that the send path uses `pg_notify` — where a
    name reaches SQL as text. Restricting the alphabet is what makes that safe,
    and rejecting rather than escaping keeps the rule legible.
    """
    if not name.replace("_", "").isalnum():
        raise ValueError(f"channel {name!r} is not a plain identifier")
    return name
