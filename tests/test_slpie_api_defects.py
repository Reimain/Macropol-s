"""The defects §30 step 1 closes, each with the assertion that would have caught it.

None of these were found by a failing test. They were found by reading, which is
the expensive way — so every one of them gets a test that fails if it comes back,
and where the defect was a value written twice, the test derives the value rather
than restating it.
"""

from __future__ import annotations

import json

from slpie.core.events import EventKind, emit
from slpie.rbac.model import matches_action
from slpie.ui.api import Api, Request, Response, answered
from slpie.ui.stream import CLIENT_BUFFER, EventStream
from slpie.workspace.plane import DATASET_ACTION, WORKSPACE_ACTION


# --- the wildcard that granted nothing --------------------------------------


def test_a_dotted_wildcard_covers_the_workspace_actions():
    """The defect: `matches_action` has only ever understood dotted prefixes.

    `workspace:create` could therefore not be granted by any wildcard at all.
    An operator writing the obvious rule got a silent refusal, and no test
    noticed because every test spelled the action out in full — which is exactly
    the case that worked.
    """
    assert matches_action("workspace.*", WORKSPACE_ACTION)
    assert matches_action("dataset.*", DATASET_ACTION)
    assert matches_action("*", WORKSPACE_ACTION)


def test_the_workspace_actions_are_dotted_like_every_other_action():
    assert ":" not in WORKSPACE_ACTION
    assert ":" not in DATASET_ACTION


def test_the_colon_form_still_resolves():
    """Policy files already contain it, so it has to keep working.

    Normalised rather than refused: a hard failure here would lock out the very
    rules that grant access. It cannot be a `DeprecationWarning` either —
    `pyproject.toml` turns those into errors for `slpie.*`, so warning would
    convert a legacy policy file into a crash.
    """
    assert matches_action("workspace:create", "workspace.create")
    assert matches_action("workspace.create", "workspace:create")
    assert matches_action("workspace:*", "workspace.create")


def test_resources_keep_the_colon():
    """Only the action position is normalised.

    `env:prod`, `dataset:sales` and `repo:acme/payments` use the colon as a kind
    separator, and flattening it there would make `env:prod` and `env.prod` the
    same resource.
    """
    from slpie.rbac.model import matches_resource

    assert matches_resource("env:*", "env:prod")
    assert not matches_resource("env:prod", "env.prod")


# --- the version that never left the process --------------------------------


class _Result:
    """The shape `QueryResult` presents to `answered`."""

    def __init__(self, value, version=7, projection="graph", stale=False):
        self.value = value
        self.version = version
        self.projection = projection
        self.stale = stale


def test_a_query_answer_carries_the_version_it_was_computed_at():
    """Six routes called `.value` and discarded the rest.

    Without a version a client redrawing on every event cannot tell which of two
    in-flight responses is newer, so it paints whichever lands last and the
    screen goes backwards. That is the failure `QueryResult.version` exists to
    prevent, and it could not, because the number stopped at the API.
    """
    response = answered(_Result({"nodes": 3}))
    headers = dict(response.headers)

    assert headers["X-Slpie-Version"] == "7"
    assert headers["X-Slpie-Projection"] == "graph"
    assert headers["X-Slpie-Stale"] == "0"
    assert response.body == {"nodes": 3}


def test_an_envelope_route_keeps_both_its_shape_and_its_version():
    """`/api/findings` wraps the value; the wrapper must not cost the header."""
    answer = _Result([1, 2, 3], version=12)
    response = answered(answer, {"findings": answer.value})

    assert response.body == {"findings": [1, 2, 3]}
    assert dict(response.headers)["X-Slpie-Version"] == "12"


def test_headers_already_set_are_not_overwritten():
    original = Response({}, headers=(("X-Slpie-Ledger-Version", "5"),))
    combined = original.with_headers(("X-Slpie-Ledger-Version", "9"))

    assert dict(combined.headers)["X-Slpie-Ledger-Version"] == "5"


# --- the node route that could not say "absent" -----------------------------


def test_asking_for_a_node_that_is_not_there_is_a_404_not_an_empty_200():
    """A client cannot tell "absent" from "present but empty" when both are 200.

    Both the catalogue and the findings detail view hang off this route, so a
    retired node rendered as a blank panel rather than as the supersession it is.
    """
    class _Graph:
        def node(self, _identifier):
            return None

    class _Engine:
        graph = _Graph()
        ledger = None

    api = Api(engine=_Engine())
    response = api.handle(Request(
        method="GET", path="/api/node", query={"id": "urn:slpie:absent"}, body={},
    ))

    assert response.status == 404
    assert json.loads(response.encode())["type"] == "NoSuchNode"


def test_asking_for_a_node_without_an_id_says_what_is_missing():
    api = Api(engine=None)
    response = api.handle(Request(method="GET", path="/api/node", query={}, body={}))

    assert response.status == 400
    assert "?id=" in json.loads(response.encode())["error"]


# --- the live feed, resumable ------------------------------------------------


def _fire(stream: EventStream, count: int, *, start: int = 1) -> None:
    for index in range(count):
        stream.handle(emit(EventKind.NODE_ASSERTED, f"n{index}").sequenced(start + index))


def test_a_reconnecting_client_receives_exactly_what_it_missed():
    """`Last-Event-ID` replay. Without it every reconnect lost a window silently."""
    stream = EventStream()
    _fire(stream, 10)

    resumed = stream.connect("tab", since=6)

    assert resumed.events.qsize() == 4, "a resume replayed the wrong number of events"
    sequences = [resumed.events.get_nowait()[0] for _ in range(4)]
    assert sequences == [7, 8, 9, 10]


def test_a_new_client_gets_a_backlog_rather_than_the_whole_history():
    """The other case, which conflating with the first is what lost events."""
    stream = EventStream()
    _fire(stream, 10)

    fresh = stream.connect("new-tab", backlog=3)
    assert fresh.events.qsize() == 3
    assert fresh.events.get_nowait()[0] == 8


def test_resuming_from_beyond_the_retained_window_reports_the_shortfall():
    """Told, not silently short-changed.

    A partial replay a client cannot distinguish from a complete one is how a
    view diverges from the platform and stays that way. The count is its cue to
    refetch rather than to patch.
    """
    stream = EventStream()
    _fire(stream, CLIENT_BUFFER + 50)

    stale = stream.connect("long-asleep", since=1)

    assert stale.dropped > 0, (
        "a client resuming from before the retained window was handed a partial "
        "replay with no indication that it was partial"
    )


def test_every_data_frame_carries_an_id_and_the_connection_carries_a_retry():
    stream = EventStream()
    _fire(stream, 2)
    client = stream.connect("tab", since=0)

    frames = []
    follower = stream.follow(client)
    for _ in range(4):
        frames.append(next(follower).decode("utf-8"))

    assert frames[0].startswith("retry: ")
    body = "".join(frames)
    assert "id: 1\n" in body and "id: 2\n" in body


def test_the_stream_reports_how_far_back_it_can_replay():
    stream = EventStream()
    _fire(stream, 3)

    status = stream.status()
    assert status["oldest"] == 1
    assert status["delivered"] == 3
    assert status["retry_ms"] > 0


# --- the feed, discoverable --------------------------------------------------


def test_the_stream_is_in_the_route_table():
    """It was in none of the 75 routes, so no generated client could find it.

    Intercepted before the table in `server.py` for a real reason — an open
    `text/event-stream` is not a JSON body — but being unroutable and being
    undocumented are different things, and it was both.
    """
    api = Api(engine=None)
    assert ("GET", "/api/stream") in api.routes
    assert ("GET", "/api/stream/status") in api.routes


def test_fetching_the_stream_says_to_use_the_right_transport():
    """Better than a 404, which would imply the feed does not exist."""
    api = Api(engine=None)
    response = api.handle(Request(method="GET", path="/api/stream", query={}, body={}))

    assert response.status == 400
    body = json.loads(response.encode())
    assert body["transport"] == "sse"
    assert "EventSource" in body["error"]


def test_stream_status_answers_before_anything_is_attached():
    api = Api(engine=None)
    response = api.handle(
        Request(method="GET", path="/api/stream/status", query={}, body={}),
    )

    assert response.status == 200
    assert json.loads(response.encode())["attached"] is False


# --- headers reach the API ---------------------------------------------------


def test_a_request_carries_its_headers_case_insensitively():
    """The gateway identifies callers from `Authorization`, and cannot if it
    never sees it. HTTP header names are case-insensitive; a lookup that is not
    would authenticate depending on which client sent the request."""
    request = Request(
        method="GET", path="/api/status", query={}, body={},
        headers={"authorization": "Bearer slpie_x_y"},
    )

    assert request.header("Authorization") == "Bearer slpie_x_y"
    assert request.header("AUTHORIZATION") == "Bearer slpie_x_y"
    assert request.header("missing", "none") == "none"


def test_every_existing_construction_of_a_request_still_works():
    """Three fields were added; all of them default."""
    request = Request(method="GET", path="/api/status", query={}, body={})

    assert request.headers == {}
    assert request.principal is None
    assert request.context == {}


# --- the committed clients ---------------------------------------------------


def test_the_committed_clients_match_the_generator():
    """The permanent fix for a problem regenerating once cannot solve.

    `clients/` held a TypeScript client covering 25 of 48 verbs and an OpenAPI
    document with 52 of 77 paths. Nothing failed, because nothing read those
    files — they were generated once and the registry moved on without them.

    Regenerating them fixes today. This fixes every day after it: drift becomes
    a red test on the commit that causes it, which is the same discipline
    `tools/notebooks/build.py --check` already applies to the notebooks.
    """
    from tools.clients import build

    assert build(check=True) == 0, (
        "the committed clients have drifted from the verb registry — "
        "run `python -m tools.clients`"
    )


def test_the_generated_client_covers_every_verb():
    """The denominator is the registry, so a new verb cannot be quietly missed."""
    from slpie.compose import registry
    from tools.clients import targets

    client = next(
        body for path, body in targets().items() if path.name == "slpie-client.ts"
    )
    missing = [verb.name for verb in registry() if f'"{verb.name}"' not in client]
    assert not missing, f"the generated client does not mention {missing}"
