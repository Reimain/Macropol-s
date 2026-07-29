"""The contract — one source for the stdlib server, FastAPI and every client.

The headline property is **parity**: the OpenAPI document and the stdlib server
expose the same route set. If they ever disagree a test fails here, rather than a
client discovering it as a 404 in production.

The second is that the contract carries the **type graph**. `x-slpie-consumes` and
`x-slpie-produces` travel in the document, and the generated TypeScript ships a
`validate()` that uses them — so a client rejects `findings | attach` before
sending it, which is the whole point of typing the pipe.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slpie.compose import Kind, registry
from slpie.ui.api import Api, Request, Response
from slpie.ui.contract import CONTRACT_VERSION, openapi, route_set, typescript


@pytest.fixture()
def api() -> Api:
    return Api(engine=None)


@pytest.fixture()
def verbs():
    return registry()


@pytest.fixture()
def repository(tmp_path: Path) -> Path:
    (tmp_path / "package.json").write_text(
        '{"name": "demo", "version": "1.0.0",'
        ' "dependencies": {"lodash": "^3.0.0"}}',
        encoding="utf-8",
    )
    (tmp_path / "package-lock.json").write_text(
        '{"name": "demo", "lockfileVersion": 3, "packages": {'
        '"node_modules/lodash": {"version": "4.17.21"}}}',
        encoding="utf-8",
    )
    return tmp_path


def call(api: Api, method: str, path: str, body: dict | None = None,
         query: dict | None = None) -> Response:
    return api.handle(Request(
        method=method, path=path, query=query or {}, body=body or {},
    ))


# --- the registry is authoritative --------------------------------------


def test_every_verb_has_a_route_without_anybody_wiring_it(api: Api, verbs):
    """That is what makes the registry authoritative rather than merely adjacent:
    there is no file to edit and so nowhere to forget."""
    served = {path for _method, path in api.routes}

    for verb in verbs:
        assert f"/api/v/{verb.name}" in served, f"{verb.name} has no route"


def test_the_contract_and_the_server_expose_the_same_routes(api: Api, verbs):
    declared = route_set(verbs=verbs, routes=api.routes)
    served = {f"{method} {path}" for method, path in api.routes}

    assert declared == served, (
        f"contract-only: {sorted(declared - served)}; "
        f"server-only: {sorted(served - declared)}"
    )


def test_the_contract_is_a_valid_openapi_document(api: Api, verbs):
    document = openapi(verbs=verbs, routes=api.routes)

    assert document["openapi"] == "3.1.0"
    assert document["info"]["version"] == CONTRACT_VERSION
    assert document["paths"]
    assert "Flow" in document["components"]["schemas"]
    # It has to survive serialisation: a client fetches it over the wire.
    assert json.loads(json.dumps(document))


def test_the_contract_carries_the_type_graph_so_a_client_can_check_locally(verbs):
    document = openapi(verbs=verbs)
    entry = document["paths"]["/api/v/link"]["post"]

    assert entry["x-slpie-consumes"] == "observations"
    assert entry["x-slpie-produces"] == "resolution"
    assert "constraints" in entry["x-slpie-successors"]


def test_a_mutating_verb_is_marked_in_the_contract(verbs):
    entry = openapi(verbs=verbs)["paths"]["/api/v/target"]["post"]

    assert entry["x-slpie-mutates"] is True
    assert "confirmed" in entry["requestBody"]["content"][
        "application/json"]["schema"]["properties"]


def test_a_required_parameter_is_required_in_the_schema(verbs):
    schema = openapi(verbs=verbs)["paths"]["/api/v/search"]["post"][
        "requestBody"]["content"]["application/json"]["schema"]

    assert "query" in schema["required"]


def test_a_parameters_choices_become_an_enum(verbs):
    schema = openapi(verbs=verbs)["paths"]["/api/v/findings"]["post"][
        "requestBody"]["content"]["application/json"]["schema"]

    assert schema["properties"]["severity"]["enum"] == [
        "critical", "high", "medium", "low", "info",
    ]


def test_read_routes_are_marked_cacheable_and_mutating_ones_are_not(verbs):
    paths = openapi(verbs=verbs)["paths"]

    assert paths["/api/v/status"]["post"]["x-slpie-cacheable"] is True
    assert paths["/api/v/target"]["post"]["x-slpie-cacheable"] is False


# --- the generated client -----------------------------------------------


def test_the_typescript_client_covers_every_verb(verbs):
    source = typescript(verbs=verbs)

    for verb in verbs:
        assert f"/api/v/{verb.name}" in source, f"{verb.name} is not callable"
        assert json.dumps(verb.name) in source


def test_the_typescript_client_ships_the_type_check_not_just_the_calls(verbs):
    source = typescript(verbs=verbs)

    assert "export function validate" in source
    assert "VERB_TYPES" in source
    assert '"observations"' in source
    assert "refused" in source, "a refusal must be distinguishable from a failure"


def test_the_generated_client_is_deterministic(verbs):
    """A client regenerated on two machines must be byte-identical, or every
    developer's diff is noise."""
    assert typescript(verbs=verbs) == typescript(verbs=verbs)


def test_the_flow_shape_the_client_expects_matches_what_the_server_sends(
    api: Api, repository: Path, verbs,
):
    response = call(api, "POST", "/api/v/discover", {"path": str(repository)})
    source = typescript(verbs=verbs)

    for field in ("kind", "size", "stages", "confidence", "grounded", "digest",
                  "gaps", "reasoning", "value", "facts"):
        assert field in response.body, f"the server omits {field}"
        assert f"{field}" in source, f"the client does not declare {field}"


# --- the composition surface --------------------------------------------


def test_a_composition_can_be_checked_over_http_without_running_it(api: Api):
    response = call(api, "POST", "/api/compose/validate",
                    {"pipeline": "discover . | link | findings"})

    assert response.status == 200
    assert response.body["ok"] is True
    assert response.body["produces"] == "findings"
    assert "explain" in response.body


def test_an_impossible_composition_is_refused_over_http_with_both_kinds_named(
    api: Api,
):
    response = call(api, "POST", "/api/compose/validate",
                    {"pipeline": "findings | attach"})

    assert response.body["ok"] is False
    assert "FINDINGS" in response.body["explanation"]


def test_a_malformed_pipeline_is_reported_rather_than_raising(api: Api):
    response = call(api, "POST", "/api/compose/validate",
                    {"pipeline": "search 'unbalanced"})

    assert response.body["ok"] is False
    assert "unbalanced" in response.body["explanation"]


def test_a_whole_composition_runs_over_http(api: Api, repository: Path):
    response = call(api, "POST", "/api/run",
                    {"pipeline": f"discover {repository} | link | findings"})

    assert response.status == 200
    assert response.body["ok"] is True
    assert response.body["flow"]["kind"] == "findings"
    assert response.body["flow"]["digest"]


def test_a_failing_stage_is_a_400_carrying_the_partial_flow(api: Api):
    """The caller's composition did not work out; they need the partial answer and
    the reason, not a server error."""
    response = call(api, "POST", "/api/run",
                    {"pipeline": "discover /nonexistent-path-xyz | link"})

    assert response.status == 400
    assert response.body["ok"] is False
    assert response.body["failed"] == "discover"


def test_one_verb_can_be_called_on_its_own(api: Api, repository: Path):
    response = call(api, "POST", "/api/v/discover", {"path": str(repository)})

    assert response.status == 200
    assert response.body["kind"] == "observations"
    assert response.body["size"] > 0


def test_a_verb_handed_the_wrong_kind_is_refused_before_it_runs(api: Api):
    response = call(api, "POST", "/api/v/link", {})

    assert response.status == 400
    assert "OBSERVATIONS" in response.body["error"]
    assert response.body["type"] == "TypeMismatch"


def test_a_mutating_verb_over_http_is_refused_by_the_same_guard(api: Api):
    response = call(api, "POST", "/api/v/target", {"to": "live"})

    assert response.status == 403
    assert response.body["refused"] is True
    assert "the same guard" in response.body["error"]


def test_an_upstream_flow_can_be_handed_to_a_verb_over_http(
    api: Api, repository: Path,
):
    """Server-side composition without serialising a whole pipeline per stage."""
    from slpie.compose.wire import encode
    from slpie.compose import Composition, Context

    produced = Composition.read(f"discover {repository}").run(Context())
    upstream = json.loads(encode(produced.flow))

    response = call(api, "POST", "/api/v/link", {"upstream": upstream})

    assert response.status == 200
    assert response.body["kind"] == "resolution"


def test_an_unknown_verb_parameter_is_a_400_naming_the_known_ones(
    api: Api, repository: Path,
):
    response = call(api, "POST", "/api/v/discover",
                    {"path": str(repository), "dpeth": 3})

    assert response.status == 400
    assert "dpeth" in response.body["error"]


def test_the_verb_registry_is_served_for_the_clients_to_build_from(api: Api, verbs):
    response = call(api, "GET", "/api/verbs")

    assert {entry["name"] for entry in response.body["verbs"]} == set(verbs.names)
    assert response.body["graph"]


def test_the_manual_is_served_so_a_client_needs_no_help_text_of_its_own(api: Api):
    response = call(api, "GET", "/api/manual")

    assert response.body["primer"]
    assert response.body["recipes"]
    assert response.body["verbs"]


def test_the_planner_is_reachable_over_http(api: Api):
    response = call(api, "POST", "/api/plan",
                    {"question": "what is wrong before release?"})

    assert response.body["understood"] is True
    assert response.body["pipeline"] == "discover | findings --severity high"


def test_the_contract_is_served_by_the_server_it_describes(api: Api):
    response = call(api, "GET", "/api/contract")

    assert response.body["openapi"] == "3.1.0"
    assert "/api/v/discover" in response.body["paths"]


# --- the same answer through two surfaces -------------------------------


def test_the_cli_and_the_http_api_produce_the_same_digest(
    api: Api, repository: Path,
):
    """Different surfaces must not be different answers. This is the assertion
    that keeps the projection honest."""
    from slpie.compose import Composition, Context

    pipeline = f"discover {repository} | link | findings"

    over_http = call(api, "POST", "/api/run", {"pipeline": pipeline})
    in_process = Composition.read(pipeline).run(Context())

    assert over_http.body["flow"]["digest"] == in_process.flow.digest


def test_a_handler_that_chose_its_status_keeps_it(api: Api):
    """A handler returning a Response was re-wrapped, so its body became another
    Response and the status it deliberately set was lost."""
    response = call(api, "POST", "/api/v/target", {"to": "live"})

    assert response.status == 403
    assert isinstance(response.body, dict)
    assert "error" in response.body
