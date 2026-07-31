"""The validation plane: measured confidence, recorded runs, spent budget.

The tests that carry weight are the ones about refusing to answer. A calibration
report from eleven observations, a recalibration from a bin of two, a budget
that overspends silently, a report that lists only what it validated — each is a
plausible-looking implementation that would make the system less honest than
saying nothing.
"""

from __future__ import annotations

import json
import math

import pytest

from gratimos.validate import (
    MIN_SAMPLES,
    TOLERANCE,
    Budget,
    BudgetError,
    CalibrationError,
    Calibrator,
    Candidate,
    Observation,
    Outcome,
    Queue,
    Run,
    RunLedger,
    Unit,
    entropy,
    redact,
    wilson,
)


def feed(calibrator: Calibrator, confidence: float, *, hits: int, misses: int) -> None:
    for _ in range(hits):
        calibrator.observe(confidence, True)
    for _ in range(misses):
        calibrator.observe(confidence, False)


# --- the interval ---------------------------------------------------------


def test_wilson_never_leaves_the_unit_interval_at_the_extremes():
    """Where the naive interval breaks, and where calibration data lives."""
    low, high = wilson(10, 10)
    assert 0.0 <= low <= high <= 1.0
    assert low < 1.0          # not a zero-width interval, unlike the naive form


def test_wilson_narrows_as_evidence_accumulates():
    small = wilson(8, 10)
    large = wilson(800, 1000)
    assert (large[1] - large[0]) < (small[1] - small[0])


def test_an_empty_sample_admits_everything():
    assert wilson(0, 0) == (0.0, 1.0)


# --- calibration ----------------------------------------------------------


def test_a_confidence_outside_zero_to_one_is_refused():
    with pytest.raises(CalibrationError, match="not a probability"):
        Observation(confidence=1.4, correct=True)


def test_a_calibrator_needs_at_least_two_bins():
    with pytest.raises(CalibrationError, match="at least two bins"):
        Calibrator(bins=1)


def test_too_few_observations_produce_unknown_rather_than_a_number():
    """Eleven observations cannot distinguish miscalibration from noise."""
    calibrator = Calibrator()
    feed(calibrator, 0.9, hits=6, misses=5)
    report = calibrator.report()
    assert not report.known
    assert report.ece is None
    assert "fewer than the" in report.verdict


def test_an_honest_predictor_is_reported_as_calibrated():
    calibrator = Calibrator()
    feed(calibrator, 0.9, hits=90, misses=10)
    feed(calibrator, 0.5, hits=50, misses=50)
    feed(calibrator, 0.2, hits=20, misses=80)
    report = calibrator.report()
    assert report.known
    assert report.calibrated
    assert report.ece < 0.05


def test_an_overconfident_predictor_is_caught_and_named():
    calibrator = Calibrator()
    feed(calibrator, 0.9, hits=50, misses=50)
    report = calibrator.report()
    assert not report.calibrated
    assert report.overconfidence > report.underconfidence
    assert "overconfident" in report.verdict


def test_an_underconfident_predictor_is_caught_separately():
    """Not the same fault: this wastes validation budget rather than misplacing trust."""
    calibrator = Calibrator()
    feed(calibrator, 0.3, hits=80, misses=20)
    report = calibrator.report()
    assert not report.calibrated
    assert report.underconfidence > report.overconfidence
    assert "underconfident" in report.verdict


def test_over_and_under_confidence_are_never_averaged_into_one_number():
    """A predictor wrong in both directions must not look honest."""
    calibrator = Calibrator()
    feed(calibrator, 0.9, hits=50, misses=50)      # overconfident
    feed(calibrator, 0.1, hits=50, misses=50)      # underconfident
    report = calibrator.report()
    assert report.overconfidence > 0
    assert report.underconfidence > 0
    assert not report.calibrated


def test_a_thin_bin_is_excluded_from_the_headline_error():
    calibrator = Calibrator()
    feed(calibrator, 0.5, hits=50, misses=50)
    calibrator.observe(0.95, False)               # one lonely observation
    report = calibrator.report()
    thin = [bucket for bucket in report.bins if bucket.count and bucket.thin]
    assert thin and thin[0].thin
    assert report.calibrated                       # the thin bin did not condemn it


def test_a_small_bin_is_not_condemned_on_a_point_estimate():
    """Its interval is half the number line wide; calling it wrong invents findings."""
    calibrator = Calibrator()
    feed(calibrator, 0.5, hits=40, misses=40)
    feed(calibrator, 0.75, hits=4, misses=2)
    assert calibrator.report().calibrated


def test_the_brier_score_rewards_being_both_right_and_sure():
    sharp = Calibrator()
    feed(sharp, 0.95, hits=95, misses=5)
    hedging = Calibrator()
    feed(hedging, 0.5, hits=95, misses=5)
    assert sharp.report().brier < hedging.report().brier


def test_the_worst_bin_is_identified_for_the_report():
    calibrator = Calibrator()
    feed(calibrator, 0.5, hits=50, misses=50)
    feed(calibrator, 0.9, hits=10, misses=40)
    worst = calibrator.report().worst
    assert worst is not None and worst.low == pytest.approx(0.9)


def test_the_render_marks_the_dishonest_bins():
    calibrator = Calibrator()
    feed(calibrator, 0.9, hits=20, misses=80)
    assert "!" in calibrator.report().render()


def test_reports_can_be_scoped_to_one_source():
    calibrator = Calibrator()
    for _ in range(40):
        calibrator.observe(0.9, True, source="lockfile")
    for _ in range(40):
        calibrator.observe(0.9, False, source="heuristic")
    assert calibrator.report(source="lockfile").calibrated
    assert not calibrator.report(source="heuristic").calibrated


# --- recalibration --------------------------------------------------------


def test_recalibration_does_nothing_until_there_is_evidence():
    """Adjusting from noise is how a system argues itself into a wrong number."""
    calibrator = Calibrator()
    feed(calibrator, 0.9, hits=2, misses=8)
    assert not calibrator.adjusts()
    assert calibrator.adjust(0.9) == 0.9


def test_recalibration_pulls_an_overconfident_score_down():
    calibrator = Calibrator()
    feed(calibrator, 0.9, hits=30, misses=70)
    adjusted = calibrator.adjust(0.9)
    assert adjusted < 0.9
    assert adjusted > 0.3           # shrunk toward the claim, not replaced by it


def test_recalibration_pushes_an_underconfident_score_up():
    calibrator = Calibrator()
    feed(calibrator, 0.25, hits=70, misses=30)
    assert calibrator.adjust(0.25) > 0.25


def test_a_thin_bin_barely_moves_the_number():
    """Two observations must not overturn a stated confidence."""
    calibrator = Calibrator()
    feed(calibrator, 0.5, hits=40, misses=40)
    calibrator.observe(0.85, False)
    calibrator.observe(0.85, False)
    assert calibrator.adjust(0.85) > 0.6


def test_a_well_evidenced_bin_moves_it_most_of_the_way():
    calibrator = Calibrator()
    feed(calibrator, 0.9, hits=100, misses=300)
    assert calibrator.adjust(0.9) < 0.45


def test_an_adjusted_confidence_stays_a_probability():
    calibrator = Calibrator()
    feed(calibrator, 0.99, hits=0, misses=60)
    assert 0.0 <= calibrator.adjust(0.99) <= 1.0


# --- run records ----------------------------------------------------------


def test_a_run_must_name_its_actor():
    with pytest.raises(Exception, match="dead end"):
        Run(question="why", actor="  ")


def test_a_run_is_content_addressed():
    first = Run(question="q", actor="claude", prompt="p", answer="a", started_at=1.0)
    same = Run(question="q", actor="claude", prompt="p", answer="a", started_at=1.0)
    other = Run(question="q", actor="claude", prompt="p", answer="b", started_at=1.0)
    assert first.id == same.id
    assert first.id != other.id


@pytest.mark.parametrize("secret,kind", [
    ("sk-abcdefghijklmnopqrstuvwxyz012345", "openai-key"),
    ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "github-token"),
    ("AKIAIOSFODNN7EXAMPLE", "aws-key"),
    ("AIzaSyA1234567890abcdefghijklmnopqrstuvw", "google-key"),
    ("Bearer abcdefghijklmnopqrstuvwxyz0123456789", "bearer"),
    ("postgres://user:hunter2@db.internal/orders", "url-credentials"),
])
def test_credentials_are_masked_before_they_are_ever_stored(secret, kind):
    cleaned, redactions = redact(f"please use {secret} to fetch it")
    assert secret not in cleaned
    assert kind in {item.kind for item in redactions}


def test_an_unrecognised_high_entropy_token_is_still_caught():
    key = "Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MHF3ZXJ0eXVpb3A"
    cleaned, redactions = redact(f"token={key}")
    assert key not in cleaned
    assert redactions[0].kind == "high-entropy"


def test_ordinary_prose_and_digests_survive_redaction():
    text = ("the graph rebuilt from sequence 0 and the digest was "
            "a1b2c3d4e5f60718293a4b5c6d7e8f90")
    cleaned, redactions = redact(text)
    assert cleaned == text
    assert not redactions


def test_entropy_separates_key_material_from_english():
    assert entropy("the quick brown fox jumps over") < 4.2
    assert entropy("Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MHF3ZXJ0") > 4.2


def test_a_prompt_carrying_a_credential_is_reported_as_a_leak():
    ledger = RunLedger()
    with ledger.record("claude-opus-5", question="check this") as run:
        run.prompt = "use sk-abcdefghijklmnopqrstuvwxyz012345"
        run.answer = "done"
    assert ledger.leaks()
    assert ledger.runs[0].leaked


def test_the_recorder_writes_a_record_even_when_the_call_fails():
    """A ledger where nothing ever goes wrong is the least useful audit trail."""
    ledger = RunLedger()
    with pytest.raises(RuntimeError):
        with ledger.record("claude-opus-5", question="check") as run:
            run.prompt = "hello"
            raise RuntimeError("the model timed out")
    assert len(ledger) == 1
    assert not ledger.runs[0].succeeded
    assert "timed out" in ledger.runs[0].error


def test_a_reviewer_can_ask_which_run_produced_a_claim():
    ledger = RunLedger()
    with ledger.record("claude-opus-5", question="does retry back off?") as run:
        run.answer = "yes"
        run.claims.append("claim:retry-backoff")
    found = ledger.for_claim("claim:retry-backoff")
    assert len(found) == 1
    assert found[0].actor == "claude-opus-5"


def test_appending_the_same_run_twice_is_a_no_op():
    ledger = RunLedger()
    run = Run(question="q", actor="a", started_at=1.0)
    ledger.append(run)
    ledger.append(run)
    assert len(ledger) == 1


def test_spend_is_reported_per_actor():
    ledger = RunLedger()
    ledger.append(Run(question="q", actor="claude", cost=0.02, tokens_in=100,
                      started_at=1.0))
    ledger.append(Run(question="q2", actor="claude", cost=0.03, tokens_in=200,
                      started_at=2.0))
    ledger.append(Run(question="q3", actor="local", cost=0.0, started_at=3.0))
    spend = ledger.spend()
    assert spend["claude"]["runs"] == 2
    assert spend["claude"]["cost"] == pytest.approx(0.05)
    assert spend["local"]["cost"] == 0.0


def test_a_ledger_survives_a_restart(tmp_path):
    path = tmp_path / "runs.jsonl"
    first = RunLedger(path)
    with first.record("claude-opus-5", question="check") as run:
        run.answer = "yes"
        run.claims.append("claim:x")

    second = RunLedger(path)
    assert len(second) == 1
    assert second.for_claim("claim:x")


def test_one_corrupt_line_does_not_cost_the_whole_audit_trail(tmp_path):
    path = tmp_path / "runs.jsonl"
    ledger = RunLedger(path)
    ledger.append(Run(question="q", actor="a", claims=("claim:x",), started_at=1.0))
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    ledger.append(Run(question="q2", actor="a", started_at=2.0))

    assert len(RunLedger(path)) == 2


def test_the_short_record_is_short():
    ledger = RunLedger()
    with ledger.record("claude-opus-5", question="why does this depend on that?") as run:
        run.prompt = "a very long prompt " * 200
        run.answer = "because " * 200
        run.claims.append("claim:x")
    summary = ledger.runs[0].summarise()
    assert len(summary) < 900
    assert "claim:x" in summary


# --- the budget -----------------------------------------------------------


def test_a_candidate_with_no_cost_is_refused():
    """A free validation would outrank everything that declared its cost."""
    with pytest.raises(BudgetError, match="outrank everything honest"):
        Candidate(id="x", statement="s", confidence=0.5, cost=0.0)


def test_uncertainty_peaks_in_the_middle_and_vanishes_at_the_extremes():
    middle = Candidate(id="a", statement="s", confidence=0.5)
    certain = Candidate(id="b", statement="s", confidence=0.98)
    denied = Candidate(id="c", statement="s", confidence=0.02)
    assert middle.uncertainty == 1.0
    assert certain.uncertainty < 0.1
    assert denied.uncertainty < 0.1


def test_a_claim_we_are_already_sure_of_is_not_worth_checking():
    unsure = Candidate(id="a", statement="s", confidence=0.5, dependents=3)
    sure = Candidate(id="b", statement="s", confidence=0.99, dependents=3)
    assert unsure.value > sure.value * 10


def test_leverage_is_sub_linear_so_one_hub_cannot_take_the_budget():
    one = Candidate(id="a", statement="s", confidence=0.5, dependents=1)
    hundred = Candidate(id="b", statement="s", confidence=0.5, dependents=100)
    assert hundred.value > one.value
    assert hundred.value < one.value * 10        # not 100×


def test_an_expensive_validation_must_be_worth_more_to_win():
    cheap = Candidate(id="a", statement="s", confidence=0.5, dependents=1, cost=1)
    dear = Candidate(id="b", statement="s", confidence=0.5, dependents=8, cost=40)
    assert cheap.value > dear.value


def test_the_value_explains_itself():
    text = Candidate(id="a", statement="s", confidence=0.5, dependents=7).explain()
    assert "uncertainty" in text and "leverage" in text and "cost" in text


def test_a_budget_refuses_to_overspend_rather_than_going_negative():
    budget = Budget(total=10, unit=Unit.CALLS)
    budget.spend(9)
    with pytest.raises(BudgetError, match="available"):
        budget.spend(5)


def test_a_reserve_is_held_back_from_ordinary_spending():
    budget = Budget(total=10, reserve=3)
    assert budget.available == 7
    with pytest.raises(BudgetError, match="held in reserve"):
        budget.spend(8)
    assert budget.spend(8, urgent=True) == 2      # escalation may reach it


def test_a_reserve_that_consumes_the_budget_is_refused():
    with pytest.raises(BudgetError, match="means nothing may ever be spent"):
        Budget(total=10, reserve=10)


def test_the_queue_spends_on_the_most_informative_claims_first():
    queue = Queue([
        Candidate(id="certain", statement="already known", confidence=0.99,
                  dependents=50),
        Candidate(id="pivotal", statement="could go either way", confidence=0.5,
                  dependents=50),
        Candidate(id="minor", statement="nothing rests on it", confidence=0.5,
                  dependents=0),
    ])
    assert queue.ranked()[0].id == "pivotal"

    report = queue.drain(Budget(total=1), lambda candidate: True)
    assert [outcome.candidate.id for outcome in report.checked] == ["pivotal"]


def test_the_deferred_queue_is_the_headline_not_the_leftovers():
    queue = Queue([
        Candidate(id=f"c{index}", statement=f"claim {index}", confidence=0.5,
                  dependents=index)
        for index in range(20)
    ])
    report = queue.drain(Budget(total=3), lambda candidate: True)

    assert len(report.checked) == 3
    assert len(report.deferred) == 17
    assert report.most_valuable_deferred is not None
    assert "deferred 17" in report.render()
    assert report.coverage == pytest.approx(0.15)


def test_the_report_says_how_much_of_the_available_gain_was_captured():
    queue = Queue([
        Candidate(id="big", statement="pivotal", confidence=0.5, dependents=200),
        *[Candidate(id=f"small{i}", statement="minor", confidence=0.95)
          for i in range(10)],
    ])
    report = queue.drain(Budget(total=1), lambda candidate: True)
    assert report.efficiency > 0.5          # one call took most of the value


def test_a_failing_validator_does_not_strand_the_rest_of_the_queue():
    def flaky(candidate: Candidate):
        if candidate.id == "bad":
            raise RuntimeError("the validator crashed")
        return True

    queue = Queue([
        Candidate(id="bad", statement="s", confidence=0.5, dependents=99),
        Candidate(id="good", statement="s", confidence=0.5, dependents=5),
    ])
    report = queue.drain(Budget(total=10), flaky)

    assert len(report.checked) == 2
    failed = [o for o in report.checked if not o.validated]
    assert "the validator crashed" in failed[0].note


def test_a_failed_validation_still_costs_because_the_call_was_made():
    queue = Queue([Candidate(id="bad", statement="s", confidence=0.5, cost=2.0)])
    budget = Budget(total=10)
    queue.drain(budget, lambda candidate: (_ for _ in ()).throw(RuntimeError("x")))
    assert budget.spent == 2.0


def test_a_validated_claim_leaves_the_queue_and_a_deferred_one_stays():
    queue = Queue([
        Candidate(id="a", statement="s", confidence=0.5, dependents=9),
        Candidate(id="b", statement="s", confidence=0.5, dependents=1),
    ])
    queue.drain(Budget(total=1), lambda candidate: True)
    assert "a" not in queue
    assert "b" in queue


def test_re_queueing_keeps_the_more_informative_version():
    """A later crawl finding more dependents must raise the priority, not be dropped."""
    queue = Queue([Candidate(id="a", statement="s", confidence=0.5, dependents=1)])
    queue.add(Candidate(id="a", statement="s", confidence=0.5, dependents=100))
    assert queue.ranked()[0].dependents == 100


def test_a_preview_costs_nothing_and_says_what_a_budget_would_reach():
    queue = Queue([
        Candidate(id=f"c{i}", statement="s", confidence=0.5, dependents=i)
        for i in range(10)
    ])
    would, would_not = queue.preview(Budget(total=4))
    assert len(would) == 4
    assert len(would_not) == 6
    assert len(queue) == 10          # nothing was consumed


def test_draining_is_reproducible():
    def build():
        return Queue([
            Candidate(id=f"c{i}", statement="s", confidence=0.5, dependents=3)
            for i in range(6)
        ])

    first = build().drain(Budget(total=2), lambda c: True)
    second = build().drain(Budget(total=2), lambda c: True)
    assert [o.candidate.id for o in first.checked] == \
        [o.candidate.id for o in second.checked]


# --- the three together ---------------------------------------------------


def test_the_plane_closes_the_loop_from_spending_to_measuring_to_correcting():
    """Budget spends, ledger records, calibrator learns, next score is corrected.

    This is the whole point of the package: the system finds out whether its own
    confidence was worth anything, and adjusts what it claims next time.
    """
    ledger = RunLedger()
    calibrator = Calibrator()

    # Forty claims asserted at 0.9. The world says only a quarter hold.
    queue = Queue([
        Candidate(id=f"c{index}", statement=f"claim {index}", confidence=0.9,
                  dependents=index % 7)
        for index in range(40)
    ])

    def validate(candidate: Candidate) -> Outcome:
        holds = int(candidate.id[1:]) % 4 == 0
        with ledger.record("claude-opus-5", question=candidate.statement) as run:
            run.answer = "yes" if holds else "no"
            run.claims.append(candidate.id)
            run.cost = 0.01
        calibrator.observe(candidate.confidence, holds, subject=candidate.id)
        return Outcome(candidate=candidate, validated=holds, confidence=0.95)

    report = queue.drain(Budget(total=40, unit=Unit.CALLS), validate)

    assert len(report.checked) == 40
    assert len(ledger) == 40
    assert ledger.for_claim("c0")

    measured = calibrator.report()
    assert measured.known
    assert not measured.calibrated
    assert "overconfident" in measured.verdict

    # And the correction: 0.9 is no longer asserted at face value.
    assert calibrator.adjust(0.9) < 0.6
    assert ledger.status()["cost"] == pytest.approx(0.40)
