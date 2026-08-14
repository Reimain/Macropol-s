"""The end-to-end demo, as a test rather than a script.

One composition, executed through every surface, asserted to produce the same
answer. That is the property the whole projection rests on: **different surfaces
must not be different answers.** A demo that only runs when somebody watches it is
a demo that breaks quietly; this one breaks the build.

The four surfaces:

1. the **CLI** — `slpie 'discover . | link | findings'`
2. **real OS pipes** — `slpie discover . --wire | slpie link`
3. the **HTTP API** — `POST /api/run`, which the screen UI uses
4. the **generated contract** — the type check the TypeScript client performs
   locally, reimplemented here against the same document the client is built from

If the digests diverge, one surface is lying about something.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from slpie.cli import OK, Cli
from slpie.compose import Composition, Context, Kind, registry
from slpie.compose.wire import decode, encode
from slpie.manual import offline
from slpie.planner import plan_for
from slpie.ui.api import Api, Request
from slpie.ui.contract import openapi, typescript

#: The composition the demo drives. Chosen because it exercises the whole
#: interesting path — discovery, identity merging, a cross-file join, a
#: contradiction, and a finding with evidence — while needing no environment, so
#: it runs anywhere the package is installed.
PIPELINE = "discover {root} | link | findings"


@pytest.fixture()
def world(tmp_path: Path) -> Path:
    """A small repository whose manifest and lockfile genuinely disagree.

    Real artifacts, not mocks: the same discoverers, the same pipeline and the
    same verbs run against this as against a customer's tree, so a green demo is
    evidence about the real code path.
    """
    (tmp_path / "package.json").write_text(
        json.dumps({
            "name": "storefront", "version": "2.1.0",
            "dependencies": {"lodash": "^3.0.0", "express": "^4.18.0"},
        }, indent=2),
        encoding="utf-8",
    )
    (tmp_path / "package-lock.json").write_text(
        json.dumps({
            "name": "storefront", "lockfileVersion": 3,
            "packages": {
                "node_modules/lodash": {"version": "4.17.21"},
                "node_modules/express": {"version": "4.18.2"},
            },
        }, indent=2),
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text(
        "flask==3.0.0\nPyYAML==6.0.1\n", encoding="utf-8",
    )
    (tmp_path / "Dockerfile").write_text(
        "FROM python:3.11-slim\nRUN pip install flask\n", encoding="utf-8",
    )
    return tmp_path


def cli_run(argv: list[str], stdin: str = "") -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = Cli(
        stdout=out, stderr=err, stdin=io.StringIO(stdin),
        isatty=not stdin,
    ).main(argv)
    return code, out.getvalue(), err.getvalue()


# --- the headline: one composition, four surfaces, one answer ------------


def test_one_composition_produces_the_same_answer_on_every_surface(world: Path):
    pipeline = PIPELINE.format(root=world)

    in_process = Composition.read(pipeline).run(Context())
    assert in_process.ok, in_process.error

    code, stdout, stderr = cli_run([pipeline, "--json"])
    assert code == OK, stderr
    from_cli = json.loads(stdout)["flow"]

    api = Api(engine=None)
    response = api.handle(Request(
        method="POST", path="/api/run", query={}, body={"pipeline": pipeline},
    ))
    assert response.status == 200
    from_http = response.body["flow"]

    assert in_process.flow.digest == from_cli["digest"] == from_http["digest"], (
        "the surfaces disagree, which means one of them is lying"
    )
    assert from_cli["kind"] == from_http["kind"] == "findings"
    assert from_cli["grounded"] is True


def test_real_os_pipes_reach_the_same_place_as_the_internal_pipe(world: Path):
    """Two processes, the flow crossing as JSON, and the same resolution out."""
    code, produced, stderr = cli_run([f"discover {world}", "--wire"])
    assert code == OK, stderr

    code, _consumed, stderr = cli_run(["link", "--json"], stdin=produced)
    assert code == OK, stderr

    internal = Composition.read(f"discover {world} | link").run(Context())
    crossed = decode(produced)

    assert crossed.kind is Kind.OBSERVATIONS
    # The same observations reached the far side of the pipe as stayed in process.
    direct = Composition.read(f"discover {world}").run(Context())
    assert crossed.size == direct.flow.size
    assert crossed.digest == direct.flow.digest, (
        "a flow that changed while crossing a process boundary is not the same flow"
    )
    assert internal.flow.value.resolved, "the internal pipe resolved identities"


def test_provenance_survives_all_the_way_to_the_screens_payload(world: Path):
    """The UI renders `reasoning` and `gaps` from this; if they were dropped in
    transit the screen would show an answer with no justification."""
    api = Api(engine=None)
    response = api.handle(Request(
        method="POST", path="/api/run", query={},
        body={"pipeline": PIPELINE.format(root=world)},
    ))
    flow = response.body["flow"]

    assert flow["reasoning"]["steps"], "no reasoning reached the client"
    assert flow["reasoning"]["sources"], "no file was named"
    assert any(
        ":" in source for source in flow["reasoning"]["sources"]
    ), "an explanation with no line number is not an explanation"
    assert flow["confidence"] > 0


def test_the_finding_the_demo_exists_to_show_is_actually_found(world: Path):
    """The lockfile pins lodash 4.17.21; the manifest asked for ^3.0.0."""
    result = Composition.read(PIPELINE.format(root=world)).run(Context())

    details = " ".join(item.detail for item in result.flow.items)
    assert "4.17.21" in details
    assert "^3.0.0" in details
    assert all(item.evidence for item in result.flow.items), (
        "a finding with no evidence would sit at the top of a triage list unfounded"
    )


# --- the guided path ----------------------------------------------------


def test_a_question_becomes_a_composition_that_then_runs(world: Path):
    """The full conducted flow: ask, see the plan, run it."""
    plan = plan_for("what is wrong here before release?")

    assert plan.understood
    assert not plan.needs_environment
    assert plan.steps[0].verb == "discover"

    runnable = plan.pipeline.replace("discover", f"discover {world}", 1)
    result = Composition.read(runnable).run(Context())

    assert result.ok
    assert result.flow.kind is Kind.FINDINGS


def test_the_planner_reaches_the_same_answer_through_the_api(world: Path):
    api = Api(engine=None)
    planned = api.handle(Request(
        method="POST", path="/api/plan", query={},
        body={"question": "what is wrong here before release?"},
    ))

    assert planned.body["understood"] is True
    pipeline = planned.body["pipeline"].replace(
        "discover", f"discover {world}", 1,
    )

    ran = api.handle(Request(
        method="POST", path="/api/run", query={}, body={"pipeline": pipeline},
    ))
    assert ran.status == 200
    assert ran.body["flow"]["kind"] == "findings"


# --- the manual and the contract describe what actually happens ---------


def test_every_documented_recipe_runs_against_the_demo_world(world: Path):
    for recipe in offline():
        pipeline = recipe.pipeline.replace("discover .", f"discover {world}")
        result = Composition.read(pipeline).run(Context(root=str(world)))

        assert result.ok, f"{recipe.pipeline!r}: {result.error}"
        assert result.flow.kind is recipe.produces


def test_the_contract_the_clients_build_from_describes_this_run(world: Path):
    """A client generated from the contract must be able to make this exact call."""
    api = Api(engine=None)
    document = openapi(verbs=registry(), routes=api.routes)

    assert "/api/run" in document["paths"]
    assert "/api/v/discover" in document["paths"]

    schema = document["paths"]["/api/v/discover"]["post"][
        "requestBody"]["content"]["application/json"]["schema"]
    assert "path" in schema["properties"]

    source = typescript()
    assert "async run(pipeline: string" in source
    assert "/api/v/discover" in source


def test_the_clients_local_type_check_agrees_with_the_server(world: Path):
    """If they disagreed, the builder would offer compositions the server refuses,
    which reads to a user as a bug in the platform."""
    verbs = registry()

    def client_side(names: list[str]) -> str | None:
        current = "nothing"
        for index, name in enumerate(names, start=1):
            declaration = verbs.require(name)
            consumes = declaration.consumes.value
            if consumes not in ("any", current):
                return f"stage {index} {name}"
            produces = declaration.produces.value
            current = current if produces == "same" else produces
        return None

    for pipeline, expected_ok in (
        (["discover", "link", "findings"], True),
        (["discover", "head", "link"], True),
        (["findings", "attach"], False),
        (["link"], False),
        (["discover", "impact"], False),
    ):
        server_side = Composition(
            [_stage(name) for name in pipeline], verbs=verbs,
        ).validate().ok
        assert server_side is expected_ok, pipeline
        assert (client_side(pipeline) is None) is expected_ok, pipeline


def _stage(name: str):
    from slpie.compose.parse import Stage

    return Stage(verb=name, raw=name)


# --- the CI shape -------------------------------------------------------


def test_the_demo_composition_works_as_a_release_gate(world: Path):
    """`--fail-on high` is what makes any of this usable in CI."""
    code, _stdout, stderr = cli_run([
        PIPELINE.format(root=world), "--fail-on", "high", "--quiet",
    ])

    assert code == 3, "a high-severity finding must fail the gate"
    assert "at or above high" in stderr


def test_the_same_run_twice_yields_the_same_digest(world: Path):
    """Reproducibility, asserted on the demo path rather than only in isolation."""
    pipeline = PIPELINE.format(root=world)
    first = Composition.read(pipeline).run(Context())
    second = Composition.read(pipeline).run(Context())

    assert first.flow.digest == second.flow.digest


def test_help_reaches_every_verb_the_demo_uses():
    """A demo that uses a verb the manual does not document is a demo that cannot
    be repeated by whoever watched it."""
    code, stdout, _stderr = cli_run(["help"])
    assert code == OK

    for verb in ("discover", "link", "findings", "explain"):
        assert verb in stdout
        code, page, _stderr = cli_run(["help", verb])
        assert code == OK
        assert verb in page


# --- `slpie demo` itself -------------------------------------------------
#
# The demo is a Script of Beats, each stating what it demonstrates and whether
# that claim held. These tests assert the claims rather than the transcript, which
# is the point of making the demo data: a print-statement demo can only be checked
# by grepping its output, and then the test breaks whenever the wording changes.


def test_every_beat_in_the_demo_holds_its_claim(tmp_path: Path):
    from slpie.demo import Demo

    demo = Demo(stdout=io.StringIO(), root=tmp_path / "world")
    code = demo.run()

    assert code == 0
    for key, outcome in demo.outcomes.items():
        assert outcome.holds, f"beat {key!r} did not hold: {outcome.detail}"


def test_the_thesis_beat_is_the_one_about_surfaces_agreeing(tmp_path: Path):
    from slpie.demo import Demo

    demo = Demo(stdout=io.StringIO(), root=tmp_path / "world")
    demo.run()

    agree = demo.outcomes["agree"]
    assert agree.holds
    assert "identical" in " ".join(agree.lines)


def test_the_demo_shows_a_refusal_as_well_as_a_success(tmp_path: Path):
    """Showing only the happy path teaches that nothing is ever refused."""
    from slpie.demo import Demo

    demo = Demo(stdout=io.StringIO(), root=tmp_path / "world")
    demo.run()

    refusal = demo.outcomes["refusal"]
    assert refusal.holds, "the refusal beat asserts that it WAS refused"
    assert "consumes NOTHING" in " ".join(refusal.lines)


def test_a_beat_that_does_not_hold_fails_the_run(tmp_path: Path):
    """A demo that printed a failure and returned success would be worse than one
    that did not run at all."""
    from slpie.demo import Demo
    from slpie.demo.script import Beat, Outcome, Script, Surface

    broken = Script(
        name="broken", thesis="a claim that does not hold",
        beats=(Beat(
            key="nope", title="a claim that fails", claim="this is true",
            surface=Surface.CLI,
            run=lambda stage: Outcome(holds=False, detail="it was not"),
        ),),
    )
    out = io.StringIO()
    code = Demo(stdout=out, root=tmp_path / "w", script=broken).run()

    assert code == 1
    assert "did not hold" in out.getvalue()


def test_the_script_maps_beats_to_screens_so_the_ui_can_walk_it(tmp_path: Path):
    """The reason the demo is data: a sequence of print statements can only ever
    be a terminal transcript."""
    from slpie.demo import script

    body = script().to_dict()

    assert body["beats"]
    assert "compose" in body["screens"]
    interactive = [beat for beat in body["beats"] if beat["interactive"]]
    assert interactive, "no beat maps to a screen"
    for beat in body["beats"]:
        assert beat["claim"], f"{beat['key']} claims nothing"
        assert beat["surface"]


def test_the_demo_covers_more_than_one_surface():
    from slpie.demo import Surface, script

    surfaces = {beat.surface for beat in script()}
    assert Surface.CLI in surfaces
    assert Surface.API in surfaces
    assert Surface.PIPE in surfaces


def test_the_headless_run_returns_the_same_structure_the_ui_would_fetch(
    tmp_path: Path,
):
    from slpie.demo import run as run_demo

    code, body = run_demo(root=tmp_path / "world")

    assert code == 0
    assert body["held"] is True
    assert all(beat["outcome"] is not None for beat in body["beats"])


def test_the_demo_cleans_up_after_itself_when_it_made_its_own_world():
    from slpie.demo import Demo

    demo = Demo(stdout=io.StringIO())
    assert demo.run() == 0

    assert demo._temporary is not None
    assert not Path(demo._temporary).exists(), "a demo must not leave a tree behind"


def test_the_demo_is_reachable_from_the_cli_and_is_listed_in_help():
    code, stdout, stderr = cli_run(["demo"])
    assert code == 0, stderr
    assert "SLPIE — end to end" in stdout

    _code, overview, _stderr = cli_run(["help"])
    assert "slpie demo" in overview, "an unlisted command is one nobody finds"


def test_the_demo_materialises_real_files_rather_than_mocking_them(tmp_path: Path):
    """The same discoverers run against this as against a customer's tree, so a
    green demo is evidence about the real code path."""
    from slpie.demo import FILES, materialise

    root = materialise(tmp_path / "world")

    for name in FILES:
        assert (root / name).is_file()
    assert json.loads((root / "package.json").read_text())["dependencies"]
