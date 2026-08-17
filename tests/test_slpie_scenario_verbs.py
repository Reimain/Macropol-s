"""`simulate` and `fire` — twelve scenarios, finally reachable from a surface.

The capability existed on `Engine` from phase 4 and no surface could reach it:
no verb, no CLI route, no manual page, no `/api/verbs` entry. By §24's own
argument that is drift — a capability the platform has and cannot be asked for
is indistinguishable, from outside, from one it does not have.

So the assertions here are mostly about *reachability*, and the one that matters
most is `test_every_scenario_is_reachable_by_name`: it reads the scenario
registry rather than a list written here, so adding a scenario without exposing
it fails this file instead of going unnoticed for a phase.
"""

from __future__ import annotations

import pytest

from slpie.compose import Composition, Context
from slpie.compose.pipeline import CompositionError
from slpie.engine import Engine
from slpie.environment import loads
from slpie.simulator.scenarios import available

MANIFEST = """apiVersion: slpie/v1
environment: acme
target: simulated
security:
  boundaries:
    - name: cardholder-data
      contains: [payments]
codebase:
  - root: ./services/payments
    language: npm
  - root: ./services/api
    language: python
network:
  - name: payments-api
    url: https://api.acme.com/v1
    kind: rest
"""


@pytest.fixture()
def engine():
    return Engine.create(loads(MANIFEST, source_uri="file:///r/slpie.environment.yaml"))


@pytest.fixture()
def confirmed(engine, tmp_path):
    """A context that has already said yes.

    Both verbs write to a filesystem, so both are `mutates` and a composition
    containing either is refused without confirmation. Every test below that is
    *not* about the gate uses this, so the gate is asserted once rather than
    worked around everywhere.
    """
    return Context(root=str(tmp_path), engine=engine, confirmed=True)


# --- the gate ---------------------------------------------------------------


def test_a_composition_that_would_write_is_refused_without_confirmation(
    verbs, engine, tmp_path,
):
    """The same guard the live target uses, applied to a composition."""
    composition = Composition.read("simulate", verbs=verbs)

    with pytest.raises(CompositionError) as refused:
        composition.run(Context(root=str(tmp_path), engine=engine))

    assert "simulate" in str(refused.value)
    # Refused *before* stage one, which is the property worth having: nothing
    # has been written when the operator is asked.
    assert engine.world is None


# --- simulate ---------------------------------------------------------------


def test_simulate_writes_real_artifacts_rather_than_reporting_that_it_would(
    verbs, engine, confirmed,
):
    result = Composition.read("simulate", verbs=verbs).run(confirmed)

    assert result.ok, result.error
    root = engine.world.root
    assert root.exists()
    assert (root / "payments" / "package-lock.json").is_file(), (
        "the npm codebase was declared but no lockfile reached the disk"
    )
    assert result.flow.facts["artifacts"] > 0


def test_simulate_reports_where_it_put_things(verbs, engine, confirmed):
    """A world materialised somewhere nobody can name is not inspectable."""
    result = Composition.read("simulate", verbs=verbs).run(confirmed)

    assert result.flow.facts["root"] == str(engine.world.root)
    assert str(engine.world.root) in result.flow.reasoning.steps[-1].claim


def test_simulate_honours_where_it_was_told_to_write(verbs, engine, tmp_path):
    """`at`, not `root`.

    `Context.root` already means "the tree being examined". A parameter called
    `root` would have its default materialised into `arguments` and win the `or`
    chain against the context — the shadowing defect `changed` already carried.
    """
    wanted = tmp_path / "somewhere-else"
    result = Composition.read(f"simulate --at {wanted}", verbs=verbs).run(
        Context(root=str(tmp_path), engine=Engine.create(
            loads(MANIFEST, source_uri="file:///r/slpie.environment.yaml"),
        ), confirmed=True),
    )

    assert result.ok, result.error
    assert result.flow.facts["root"] == str(wanted)
    assert wanted.exists()


def test_what_simulate_produces_is_what_attach_and_scan_then_see(verbs, confirmed):
    """The point of the verb: the rest of the platform works on what it wrote.

    Three invocations, not one pipeline. `simulate`, `attach` and `scan` are all
    source verbs — each reads the engine rather than the flow — and giving
    `attach` an upstream kind would also make `findings | attach` type-check,
    which is the refusal the composition suite uses as its canonical example.
    """
    for stage in ("simulate", "attach", "scan"):
        result = Composition.read(stage, verbs=verbs).run(confirmed)
        assert result.ok, f"{stage}: {result.error}"

    assert result.flow.facts.get("files_read", 0) > 0, (
        "scan completed having read nothing, which means the materialised "
        "world was not the tree it looked at"
    )


# --- fire -------------------------------------------------------------------


def test_every_scenario_is_reachable_by_name(verbs):
    """Read from the registry, so a new scenario cannot be added unexposed."""
    declared = verbs.require("fire").params[0].choices

    assert set(declared) == set(available()), (
        "the `fire` verb's scenario list has drifted from the registry"
    )
    assert len(declared) == 12


def test_firing_a_scenario_reports_what_it_expects_the_platform_to_conclude(
    verbs, confirmed,
):
    """The expectations are data, so a caller can assert instead of narrate."""
    Composition.read("simulate", verbs=verbs).run(confirmed)
    result = Composition.read("fire cve --package lodash", verbs=verbs).run(confirmed)

    assert result.ok, result.error
    assert result.flow.facts["scenario"] == "cve"
    assert "vulnerable_dependency" in result.flow.facts["expect_findings"]
    assert result.flow.facts["changed"] > 0


def test_a_scenario_expecting_a_gap_says_so_rather_than_a_finding(verbs, confirmed):
    """Two of the twelve predict a *gap*. Conflating them would hide the case
    where the platform correctly declines rather than correctly detects."""
    Composition.read("simulate", verbs=verbs).run(confirmed)
    result = Composition.read("fire partial-scan", verbs=verbs).run(confirmed)

    assert result.ok, result.error
    assert result.flow.facts["expect_gaps"], (
        "partial-scan is a scenario about what the platform cannot see; an "
        "empty gap expectation means the outcome lost that"
    )
    assert not result.flow.facts["expect_findings"]


def test_firing_without_a_world_says_what_to_do_about_it(verbs, engine, tmp_path):
    result = Composition.read("fire cve", verbs=verbs).run(
        Context(root=str(tmp_path), engine=engine, confirmed=True),
    )

    assert not result.ok
    assert "simulate" in result.error


def test_an_unknown_scenario_is_refused_before_anything_runs(verbs, confirmed):
    """Refused at validation, not at execution.

    `choices` is checked while the composition is being type-checked, so a
    mistyped scenario name in a four-stage pipeline never gets as far as
    materialising a world — and the refusal lists the alternatives rather than
    sending somebody to read the source.
    """
    with pytest.raises(CompositionError) as refused:
        Composition.read("fire nonsense", verbs=verbs).run(confirmed)

    assert "cve" in str(refused.value)
    assert "boundary-breach" in str(refused.value)


# --- the projection ---------------------------------------------------------


def test_both_verbs_are_visible_everywhere_a_verb_should_be(verbs):
    """Registering without wiring is the failure §24 exists to make loud."""
    from slpie.manual import page_for

    for name in ("simulate", "fire"):
        verb = verbs.require(name)
        assert verb.examples, f"{name} has no example, so nothing checks it runs"
        assert verb.summary
        assert page_for(name, verbs=verbs), f"{name} has no manual page"


# --- the acceptance script --------------------------------------------------


def test_the_acceptance_script_covers_the_whole_registry_by_construction(verbs):
    """`acceptance.py` reads coverage from the registry, not from a list.

    The point of the script is that registering a verb without exercising it
    fails a run. That only holds if the *denominator* is the registry, so this
    asserts the denominator rather than re-running the script — which takes
    forty seconds and belongs in its own CI job, not in the suite.
    """
    import acceptance

    run = acceptance.Run(executed={verb.name for verb in verbs})
    assert acceptance.claim_verb_coverage(run, verbs).held

    run.executed.discard(next(iter(run.executed)))
    lacking = acceptance.claim_verb_coverage(run, verbs)
    assert not lacking.held, "dropping a verb from the run still passed"
    assert lacking.lines, "the failure does not name which verb went unexercised"


def test_the_acceptance_script_will_not_quote_a_cost_model_it_cannot_defend():
    """A linear fit with a negative intercept is the model failing, not a finding."""
    from tools.measure import fit

    honest = fit([
        {"observations": 1_000, "scan_mb": 14.0},
        {"observations": 10_000, "scan_mb": 50.0},
        {"observations": 100_000, "scan_mb": 410.0},
    ])
    assert honest["usable"], honest

    # The shape the real corpus has: cheap in the middle, dearer at the top, so
    # the best-fit line has to start below zero to reach the last point.
    superlinear = fit([
        {"observations": 1_000, "scan_mb": 10.0},
        {"observations": 30_000, "scan_mb": 133.0},
        {"observations": 80_000, "scan_mb": 338.0},
        {"observations": 350_000, "scan_mb": 2620.0},
    ])
    assert superlinear["fixed_mb"] < 0
    assert not superlinear["usable"], (
        "a fit that says scanning an empty tree frees memory was reported as "
        "usable, which is how an indefensible number reaches a document"
    )
