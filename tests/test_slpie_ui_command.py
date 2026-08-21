"""`slpie ui` — the front door, which was advertised for four phases and missing.

`pyproject.toml` names the command, `slpie/demo/runner.py` tells the reader to run
it, and `docs/AUDIT.md` recorded that it does not exist. Nothing failed, because
nothing asserted it: an advertised command with no test is indistinguishable from
a working one until somebody types it.

The whole surface is driven in-process through `Cli`, which takes its streams as
arguments for exactly this reason. `--once` binds, reports and returns, so the
serving path is exercised without a test that never comes back.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from slpie.compose import registry
from slpie.ui import UiServer
from slpie.ui.api import Api, Request

MANIFEST = """apiVersion: slpie/v1
environment: acme
target: simulated
codebase:
  - root: ./services/payments
"""


# --- it is a command, and deliberately not a verb ---------------------------


def test_ui_is_a_command_not_a_verb():
    """A blocking server has no `Flow` to consume or produce.

    Registering it would make `discover . | ui` type-check in the composition
    checker and in the browser's copy of it, and then never return. §24's rule is
    that every *capability* is a verb; serving is a process lifecycle, which is
    why `help`, `manual`, `contract` and `demo` are commands too.
    """
    assert "ui" not in registry()


def test_the_overview_advertises_only_commands_that_exist(cli):
    """The reason this section exists, generalised so it cannot recur.

    Every `slpie <word>` in the generated usage block must be a real command or a
    real verb. `ui` sat in `pyproject.toml` and in the demo's closing line for
    four phases while `slpie ui` fell through to the composition parser.
    """
    code, out, _ = cli(["help"])
    assert code == 0

    # The usage block indents by four; the banner line above it indents by two.
    advertised = {
        line.split()[1]
        for line in out.splitlines()
        if line.startswith("    slpie ") and len(line.split()) > 1
    }
    advertised.discard("'<verb>")
    advertised.discard("<verb>")

    verbs = registry()
    for command in sorted(advertised):
        assert command in verbs or command in {
            "help", "manual", "contract", "ui", "demo", "plan", "version", "verbs",
            "routine",
        }, f"`slpie {command}` is advertised in the overview and is not a command"

    assert "ui" in advertised, "the overview does not mention the interface at all"


# --- starting it ------------------------------------------------------------


def test_ui_starts_and_reports_where_it_is_listening(cli):
    code, out, err = cli(["ui", "--port", "0", "--once"])

    assert code == 0, err
    assert out.startswith("http://127.0.0.1:"), out
    assert err == ""


def test_ui_starts_without_an_environment_and_says_so(cli, tmp_path):
    """The half of the platform that needs no manifest is the half a newcomer wants.

    The verb catalogue, the manual, the contract and the composition type-checker
    are all answerable with nothing declared. Refusing to start without a manifest
    would put the front door behind the thing the front door explains.
    """
    code, out, _ = cli(["ui", "--root", str(tmp_path), "--port", "0", "--once"])

    assert code == 0
    assert "no environment" in out


def test_ui_opens_the_environment_under_the_root_it_was_given(cli, tmp_path):
    """`--root`, honoured — not the process working directory.

    `_engine` reads `Path("slpie.environment.yaml")` relative to the CWD, which is
    the defect `docs/AUDIT.md` records against `slpie --root ... status`. New code
    does not get to repeat it.
    """
    (tmp_path / "slpie.environment.yaml").write_text(MANIFEST)

    code, out, err = cli(["ui", "--root", str(tmp_path), "--port", "0", "--once"])

    assert code == 0, err
    assert "acme" in out, out
    assert "no environment" not in out


# --- refusing badly-formed invocations --------------------------------------


@pytest.mark.parametrize("argv,expected", [
    (["ui", "--port"], "needs a value"),
    (["ui", "--port", "eight"], "takes a number"),
    (["ui", "--nonsense"], "unknown option"),
])
def test_ui_reports_a_bad_flag_rather_than_raising(cli, argv, expected):
    code, out, err = cli(argv)

    assert code == 2, "a usage mistake must not look like a successful run"
    assert expected in err
    assert "Traceback" not in err


def test_ui_refuses_a_root_that_is_not_a_directory(cli, tmp_path):
    missing = tmp_path / "nowhere"
    code, _, err = cli(["ui", "--root", str(missing), "--port", "0", "--once"])

    assert code == 2
    assert "not a directory" in err


# --- serving with no engine -------------------------------------------------


def test_the_server_runs_without_an_engine():
    """`Makefile`'s `ui` target passed `engine=None` and raised every time.

    It was never run, so nothing noticed. The constructor now treats a missing
    environment as a supported way to start rather than as an argument error.
    """
    server = UiServer(engine=None, port=0)
    try:
        server.start()
        with urllib.request.urlopen(server.url, timeout=10) as response:
            assert response.status == 200
    finally:
        server.stop()


def test_a_route_with_no_environment_says_so_instead_of_faulting():
    """409, not 500.

    "Ask again once an environment is open" is a different answer from "the
    server broke", and a client renders them differently. Before this, every
    engine-backed route on an engine-less server produced a 500 quoting
    `'NoneType' object has no attribute ...`, which sends an operator to read
    logs about a defect that is not there.
    """
    api = Api(engine=None)
    response = api.handle(Request(method="GET", path="/api/status", query={}, body={}))

    assert response.status == 409
    body = json.loads(response.encode())
    assert body["type"] == "NoEnvironment"
    assert "slpie.environment.yaml" in body["error"]


def test_the_registry_backed_routes_answer_with_no_environment():
    """The half that works regardless — asserted, so it stays that way."""
    api = Api(engine=None)

    for path in ("/api/verbs", "/api/routes", "/api/manual", "/api/contract"):
        response = api.handle(Request(method="GET", path=path, query={}, body={}))
        assert response.status == 200, f"{path} needs an environment and should not"
        assert json.loads(response.encode())


def test_the_audit_no_longer_records_the_command_as_missing():
    """The document that recorded the gap is the document that must be corrected.

    Left alone it would go on telling the next reader to build something that is
    now built, which is the drift `docs/AUDIT.md` exists to catch.
    """
    audit = Path(__file__).resolve().parent.parent / "docs" / "AUDIT.md"
    if not audit.is_file():  # pragma: no cover - the file is committed
        pytest.skip("docs/AUDIT.md is not present in this checkout")

    text = audit.read_text(encoding="utf-8")
    assert "slpie ui" in text, "the audit no longer mentions the command at all"
    assert "There is no `ui`" not in text, (
        "the audit still records the gap as open after it was closed"
    )
    assert "Closed by §30 step 0" in text


# --- the API manager is reachable -------------------------------------------


def test_the_gateway_can_actually_be_turned_on(cli):
    """A layer that exists and cannot be enabled is, from outside, a layer that
    does not exist — which is §24's own argument about capabilities.

    `slpie/apim/` was built before anything could reach it. This is the flag
    that closes that, and the assertion that it stays closed.
    """
    code, out, err = cli(["ui", "--port", "0", "--gateway", "--once"])

    assert code == 0, err
    assert "API manager on" in out


def test_the_server_passes_the_gateway_through_to_the_api():
    from slpie.apim import Gateway

    plain = Api(engine=None)
    gateway = Gateway.over(plain.routes)
    server = UiServer(engine=None, port=0, gateway=gateway)
    try:
        assert server._server.api.gateway is gateway
    finally:
        server.stop()


def test_without_the_flag_no_gateway_is_attached(cli):
    """The default is off, and off means the hook is inert rather than lenient."""
    server = UiServer(engine=None, port=0)
    try:
        assert server._server.api.gateway is None
    finally:
        server.stop()


def test_stopping_a_server_that_never_started_returns():
    """`shutdown()` waits for `serve_forever` to acknowledge it.

    A server that never started never acknowledges anything, so the call blocks
    for ever — and it blocks in the shape everybody writes: construct, inspect,
    close in a `finally`. Found by writing exactly that and watching the suite
    hang rather than fail, which is the worse way to find it.
    """
    server = UiServer(engine=None, port=0)
    server.stop()          # must return; a hang here is the regression
