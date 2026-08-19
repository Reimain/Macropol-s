"""§31 step 7 — the two §14 seams that did not exist.

`TaskRunner` and `ResourceMeter`. Both ring 0, both with a default that changes
nothing, and the second of those claims is the one worth testing hardest: a seam
that quietly alters behaviour when nobody asked for it is worse than no seam,
because every existing test then re-verifies a new execution model while
claiming to verify the old one.
"""

from __future__ import annotations

import pytest

from slpie.core.meter import Measurement, ResourceMeter
from slpie.core.tasks import InlineRunner, RecordingRunner, Result, default_runner
from slpie.discovery import Scanner
from slpie.discovery.registry import register_builtins
from slpie.plugins.registry import Registry
from slpie.domain.evidence import EvidenceKind


# -- the runner ----------------------------------------------------------


def test_the_default_runner_runs_units_here_and_in_order() -> None:
    """Order is part of the contract.

    Discovery merges observations by subject and confidence follows from
    corroboration, so two runs over one estate must produce one graph. A runner
    returning results as they finished would make the merge order depend on
    which worker happened to be quickest.
    """
    seen: list[str] = []
    results = default_runner().run([
        ("a", lambda: seen.append("a") or 1),
        ("b", lambda: seen.append("b") or 2),
        ("c", lambda: seen.append("c") or 3),
    ])
    assert seen == ["a", "b", "c"]
    assert [item.unit for item in results] == ["a", "b", "c"]
    assert [item.value for item in results] == [1, 2, 3]


def test_a_failed_unit_is_an_outcome_not_an_exception() -> None:
    """A scan of forty elements where one refused is thirty-nine plus a gap.

    Raising would throw away the thirty-nine, which is how people end up with
    scans they do not trust and then stop running.
    """
    results = InlineRunner().run([
        ("good", lambda: "ok"),
        ("bad", lambda: 1 / 0),
        ("also-good", lambda: "ok"),
    ])
    assert [item.ok for item in results] == [True, False, True]
    assert "ZeroDivisionError" in results[1].error
    assert results[1].value is None


def test_a_result_carries_which_unit_produced_it() -> None:
    """Deallocation is the part that loses data (§23).

    A short list of results looks exactly like a clean scan of a smaller estate,
    so a result has to say which unit it belongs to for the difference to be
    visible at all.
    """
    outcome = Result(unit="payments", value=1, duration_ns=5)
    assert outcome.unit == "payments"
    assert outcome.to_dict()["ok"] is True


def test_the_scanner_submits_one_unit_per_element(tmp_path) -> None:
    """The grain a worker pool needs.

    One unit for the whole scan would be a seam that looks right and cannot be
    used, because there would be nothing to spread.
    """
    from slpie.binding.resolver import Binding, FilesystemConnector
    from slpie.binding.target import Target

    for name in ("one", "two", "three"):
        root = tmp_path / name
        root.mkdir()
        (root / "package.json").write_text(
            '{"dependencies": {"left-pad": "1.0.0"}}', encoding="utf-8")

    runner = RecordingRunner()
    scanner = Scanner(register_builtins(Registry()), runner=runner)
    bindings = [
        Binding(element=name, target=Target.SIMULATED,
                connector=FilesystemConnector(
                    str(tmp_path / name), str(tmp_path / name)))
        for name in ("one", "two", "three")
    ]
    scanner.scan(bindings)

    assert runner.submitted == ["one", "two", "three"]
    assert runner.batches == 1, "the scan should submit once, not per element"


def test_the_default_scanner_needs_no_runner() -> None:
    """`Scanner(registry)` still works, and is what every existing caller does."""
    assert Scanner(register_builtins(Registry())).runner.name == "inline"


# -- the meter -----------------------------------------------------------


def test_a_measurement_is_evidence_on_the_same_ladder() -> None:
    """Not a parallel telemetry system.

    A scaling decision has the same provenance chain as a dependency edge, or
    "we are running twelve workers" terminates in a shrug while everything else
    terminates in a file and a line.
    """
    meter = ResourceMeter()
    with meter.measure("scan") as stage:
        stage.units = 4

    evidence = meter.evidence("acme-production")
    assert len(evidence) == 1
    assert evidence[0].kind is EvidenceKind.RUNTIME_TRACE
    assert evidence[0].base_confidence == 0.98
    assert "4 unit(s)" in evidence[0].excerpt


def test_unmeasured_memory_is_absent_rather_than_zero() -> None:
    """Zero bytes reads as "used no memory", which is a claim and a false one.

    The same distinction the platform makes everywhere else between an empty
    result and a question nobody asked.
    """
    quiet = ResourceMeter()
    with quiet.measure("scan"):
        pass
    assert "peak_bytes" not in quiet.to_dict()["stages"][0]
    assert quiet.measurements[0].memory_traced is False

    watched = ResourceMeter(memory=True)
    with watched.measure("scan"):
        [object() for _ in range(2000)]
    assert watched.to_dict()["stages"][0]["peak_bytes"] > 0
    assert len(watched.evidence()) == 2      # duration, and memory


def test_a_stage_that_raises_is_still_measured() -> None:
    """The failed stage is exactly the one whose cost is interesting."""
    meter = ResourceMeter()
    with pytest.raises(RuntimeError):
        with meter.measure("scan") as stage:
            stage.units = 1
            raise RuntimeError("boom")
    assert len(meter) == 1
    assert meter.measurements[0].stage == "scan"


def test_the_meter_does_not_stop_tracing_it_did_not_start() -> None:
    """Another measurer may already be tracing.

    `acceptance.py` traces around a whole run; a meter inside it turning
    `tracemalloc` off on the way out would silently blind the outer measurement,
    and the symptom would be a baseline that mysteriously reads zero.
    """
    import tracemalloc

    tracemalloc.start()
    try:
        meter = ResourceMeter(memory=True)
        with meter.measure("inner"):
            pass
        assert tracemalloc.is_tracing(), "the meter stopped somebody else's trace"
    finally:
        tracemalloc.stop()


def test_the_meter_records_nothing_until_asked() -> None:
    assert len(ResourceMeter()) == 0
    assert ResourceMeter().evidence() == ()


# -- the seams stay in ring 0 -------------------------------------------


def test_both_seams_are_stdlib_only() -> None:
    """Invariant 4. A protocol that needed Celery to be importable would have
    put the queue in the kernel by the back door."""
    import sys

    from _walk import imported_roots

    from slpie.core import meter, tasks

    for module in (tasks, meter):
        roots = imported_roots(__import__("pathlib").Path(module.__file__))
        outside = roots - set(sys.stdlib_module_names) - {"slpie", "__future__"}
        assert not outside, f"{module.__name__} imports {outside}"
