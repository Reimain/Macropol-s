"""Reuse assessment: the gate that excludes, the ranking that explains itself.

The licence cases are the ones that matter most. A gate that gets AGPL-under-SaaS
wrong is worse than no gate at all, because it produces a compliance answer
somebody will rely on.
"""

from __future__ import annotations

import pytest

from gratimos.crawl import (
    Artifact,
    Crawler,
    Fetcher,
    GitHubSource,
    Origin,
    PyPISource,
    RecordedTransport,
    SourceRegistry,
    official_policy,
)
from gratimos.distill import DistillationLoop, Judgement, KnowledgeBase, Route
from gratimos.ontology.need import Constraint, ConstraintKind, Need, need_from_text
from gratimos.reason import Engine, Fact, FactBase, Provenance, reuse_rules
from gratimos.reuse import (
    Blocker,
    Candidate,
    ConceptMatch,
    Gate,
    Ranker,
    ReuseAssessor,
    bridge,
    build_candidate,
    licence_obligation,
    licence_ok,
    runtime_ok,
)

ALLOW_ALL = "User-agent: *\nAllow: /\n"

pytestmark = pytest.mark.skipif(
    not bridge.AVAILABLE,
    reason="the licence and version evaluators come from slpie.domain",
)


def test_the_licence_bridge_is_available_in_this_repository():
    """Deliberately outside the skip above, which it exists to police.

    `bridge.AVAILABLE` is a real seam: Gratimos installs without SLPIE, and the
    49 tests below cannot run in that configuration. But *this* repository ships
    both packages, so a False here means an import broke — and the module-level
    `skipif` would report that as 49 quiet skips on a green run. This is the one
    test that turns it red.
    """
    assert bridge.AVAILABLE, (
        f"slpie.domain is not importable from the licence bridge, so the reuse "
        f"suite would silently skip: {bridge.UNAVAILABLE_REASON}"
    )


# --- fixtures ------------------------------------------------------------


@pytest.fixture
def need():
    return need_from_text(
        "I need to retry failed HTTP requests with exponential backoff",
        constraints=[Constraint(ConstraintKind.ECOSYSTEM, "pypi")],
    )


def artifact(**overrides) -> Artifact:
    settings = {
        "ecosystem": "pypi",
        "name": "retry",
        "version": "1.0.0",
        "summary": "retry with exponential backoff",
        "licence": "MIT",
        "keywords": ("retry", "backoff"),
        "requires_runtime": ">=3.8",
        "dependencies": ("decorator",),
        "released_at": None,
        "origin": Origin(source="pypi", url="https://pypi.org/pypi/retry/json"),
        "raw": {"info": {}},
    }
    settings.update(overrides)
    return Artifact(**settings)


def validated(purl: str, concept: str, confidence: float = 0.95) -> Fact:
    return Fact(
        subject=purl, predicate="provides", object=concept,
        provenance=Provenance.VALIDATED, confidence=confidence, source="agent",
    )


# --- the bridge ----------------------------------------------------------


def test_the_bridge_reports_whether_the_evaluators_are_present():
    assert bridge.status()["available"] is bridge.AVAILABLE


def test_an_undeclared_licence_is_refused_rather_than_assumed_permissive():
    verdict = licence_ok("")
    assert not verdict
    assert "no licence is declared" in verdict.reason


def test_an_unrecognised_licence_identifier_is_refused():
    assert not licence_ok("Totally-Made-Up-1.0")


def test_agpl_is_refused_for_a_network_service_and_admitted_internally():
    assert not licence_ok("AGPL-3.0-only", distribution="network_service")
    assert licence_ok("AGPL-3.0-only", distribution="internal")


def test_gpl_is_admitted_for_a_service_and_refused_in_a_shipped_binary():
    assert licence_ok("GPL-3.0-only", distribution="network_service")
    assert not licence_ok("GPL-3.0-only", distribution="distributed_binary")


def test_a_licence_choice_resolves_to_its_least_demanding_branch():
    assert licence_ok("MIT OR GPL-3.0-only", distribution="distributed_binary")


def test_the_obligation_class_is_available_for_ranking():
    assert licence_obligation("Apache-2.0") == "permissive"
    assert licence_obligation("MPL-2.0") == "weak_copyleft"
    assert licence_obligation("") == "unknown"


def test_an_unstated_runtime_range_is_unverified_rather_than_incompatible():
    verdict = runtime_ok("3.11", "")
    assert verdict.ok
    assert verdict.detail == "unstated"


def test_a_runtime_range_is_actually_evaluated():
    assert runtime_ok("3.11", ">=3.8")
    assert not runtime_ok("3.7", ">=3.8")
    assert not runtime_ok("3.11", ">=3.8,<3.10")


# --- candidates ----------------------------------------------------------


def test_an_exact_concept_match_scores_a_perfect_fit(need):
    candidate = build_candidate(
        artifact(), need, facts=[validated("pkg:pypi/retry@1.0.0", "retry-with-backoff")]
    )
    match = next(m for m in candidate.matches if m.required == "retry-with-backoff")
    assert match.exact
    assert match.fit == 1.0


def test_a_more_specific_capability_satisfies_a_broader_need(need):
    candidate = build_candidate(
        artifact(), need, facts=[validated("pkg:pypi/retry@1.0.0", "retry-with-jitter")]
    )
    match = next(m for m in candidate.matches if m.required == "retry")
    assert match.matched
    assert not match.exact
    assert 0.0 < match.fit < 1.0


def test_a_broader_capability_does_not_satisfy_a_more_specific_need(need):
    candidate = build_candidate(
        artifact(), need, facts=[validated("pkg:pypi/retry@1.0.0", "resilience")]
    )
    match = next(m for m in candidate.matches if m.required == "retry-with-backoff")
    assert not match.matched


def test_a_nearer_match_wins_over_a_better_evidenced_distant_one(need):
    candidate = build_candidate(
        artifact(),
        need,
        facts=[
            validated("pkg:pypi/retry@1.0.0", "retry-with-backoff", confidence=0.40),
            validated("pkg:pypi/retry@1.0.0", "retry-with-jitter", confidence=0.99),
        ],
    )
    match = next(m for m in candidate.matches if m.required == "retry-with-backoff")
    assert match.exact


def test_coverage_and_fit_answer_different_questions(need):
    candidate = build_candidate(
        artifact(), need, facts=[validated("pkg:pypi/retry@1.0.0", "retry-with-backoff")]
    )
    assert candidate.coverage < 1.0  # http-client is not offered
    assert not candidate.complete


def test_a_candidate_with_no_capability_claim_says_so(need):
    candidate = build_candidate(artifact(), need, facts=[])
    assert candidate.evidence == 0.0
    assert any("no capability claim" in note for note in candidate.notes)


def test_a_candidate_carries_what_the_engine_concluded_blocks_it(need):
    blocked = Fact(
        subject="pkg:pypi/retry@1.0.0", predicate="blocked", object="deprecated",
        provenance=Provenance.DERIVED, source="engine",
    )
    candidate = build_candidate(artifact(), need, facts=[blocked])
    assert candidate.blocked_reasons == ("deprecated",)


# --- the gate ------------------------------------------------------------


def test_an_unknown_distribution_is_refused_at_construction():
    with pytest.raises(ValueError, match="unknown distribution"):
        Gate(distribution="saas")


def test_a_permissive_candidate_clears_the_gate(need):
    result = Gate().check(build_candidate(artifact(), need))
    assert result.passed
    assert result.obligation == "permissive"


def test_a_candidate_with_no_licence_is_blocked(need):
    result = Gate().check(build_candidate(artifact(licence=""), need))
    assert not result.passed
    assert result.blockers[0].kind is ConstraintKind.LICENCE


def test_every_blocker_carries_a_remediation(need):
    result = Gate().check(build_candidate(artifact(licence="AGPL-3.0-only"), need))
    assert not result.passed
    assert all(blocker.remediation for blocker in result.blockers)


def test_the_same_candidate_passes_or_fails_on_the_distribution_context(need):
    candidate = build_candidate(artifact(licence="AGPL-3.0-only"), need)
    assert not Gate(distribution="network_service").check(candidate).passed
    assert Gate(distribution="internal").check(candidate).passed


def test_a_need_may_exclude_a_licence_the_obligation_analysis_would_allow(need):
    excluding = Need(
        concepts=need.concepts,
        constraints=(*need.constraints,
                     Constraint(ConstraintKind.LICENCE, "MPL-2.0", negated=True)),
    )
    result = Gate().check(build_candidate(artifact(licence="MPL-2.0"), excluding))
    assert not result.passed
    assert "excludes" in result.blockers[0].reason


def test_an_incompatible_runtime_blocks(need):
    result = Gate(runtime="python>=3.11").check(
        build_candidate(artifact(requires_runtime=">=3.6,<3.9"), need)
    )
    assert not result.passed
    assert result.blockers[0].kind is ConstraintKind.RUNTIME


def test_an_unstated_runtime_becomes_a_gap_not_a_block(need):
    result = Gate(runtime="python>=3.11").check(
        build_candidate(artifact(requires_runtime=""), need)
    )
    assert result.passed
    assert any("unverified" in entry for entry in result.unverified)


def test_the_wrong_ecosystem_blocks(need):
    result = Gate().check(
        build_candidate(artifact(ecosystem="npm", requires_runtime=""), need)
    )
    assert not result.passed
    assert result.blockers[0].kind is ConstraintKind.ECOSYSTEM


def test_an_excluded_dependency_blocks(need):
    constrained = Need(
        concepts=need.concepts,
        constraints=(*need.constraints,
                     Constraint(ConstraintKind.DEPENDENCY, "decorator", negated=True)),
    )
    result = Gate().check(build_candidate(artifact(), constrained))
    assert not result.passed
    assert result.blockers[0].kind is ConstraintKind.DEPENDENCY


def test_a_yanked_release_blocks(need):
    assert not Gate().check(build_candidate(artifact(yanked=True), need)).passed


def test_a_deprecated_package_blocks_unless_deprecation_is_accepted(need):
    candidate = build_candidate(artifact(deprecated=True), need)
    assert not Gate().check(candidate).passed
    accepted = Gate(allow_deprecated=True).check(candidate)
    assert accepted.passed
    assert accepted.warnings


def test_filtering_returns_both_halves_fully_explained(need):
    candidates = [
        build_candidate(artifact(name="good"), need),
        build_candidate(artifact(name="bad", licence=""), need),
    ]
    admitted, refused = Gate().filter(candidates)
    assert [r.candidate.name for r in admitted] == ["good"]
    assert [r.candidate.name for r in refused] == ["bad"]
    assert refused[0].blockers


# --- ranking -------------------------------------------------------------


def test_an_unknown_ranking_component_is_refused():
    with pytest.raises(ValueError, match="unknown ranking components"):
        Ranker({"vibes": 1.0})


def test_a_score_is_reconstructable_from_its_components(need):
    score = Ranker(now=0.0).score(build_candidate(artifact(), need), obligation="permissive")
    divisor = sum(component.weight for component in score.components)
    expected = sum(c.value * c.weight for c in score.components) / divisor
    assert score.total == pytest.approx(expected, abs=1e-4)


def test_the_explanation_names_every_component_that_counted(need):
    score = Ranker(now=0.0).score(build_candidate(artifact(), need), obligation="permissive")
    for component in score.components:
        assert component.name in score.explanation


def test_a_missing_signal_is_dropped_rather_than_scored_zero(need):
    quiet = build_candidate(artifact(released_at=None), need)
    score = Ranker(now=0.0).score(quiet, obligation="permissive")
    assert score.component("maintenance") is None
    assert any("maintenance" in entry for entry in score.unmeasured)


def test_a_package_that_reports_less_is_not_penalised_for_reporting_less(need):
    loud = build_candidate(artifact(stars=1000, released_at=0.0), need)
    quiet = build_candidate(artifact(stars=None, released_at=0.0), need)
    ranker = Ranker(now=0.0)
    scores = (
        ranker.score(loud, obligation="permissive").total,
        ranker.score(quiet, obligation="permissive").total,
    )
    # Popularity is the weakest input, so its absence must barely move the total.
    assert abs(scores[0] - scores[1]) < 0.05


def test_a_validated_candidate_outranks_an_identical_guessed_one(need):
    purl = "pkg:pypi/retry@1.0.0"
    checked = build_candidate(artifact(), need, facts=[validated(purl, "retry-with-backoff")])
    guessed = build_candidate(
        artifact(),
        need,
        facts=[Fact(subject=purl, predicate="provides", object="retry-with-backoff",
                    provenance=Provenance.DERIVED, confidence=0.34, source="engine")],
    )
    ranker = Ranker(now=0.0)
    assert (
        ranker.score(checked, obligation="permissive").total
        > ranker.score(guessed, obligation="permissive").total
    )


def test_a_permissive_candidate_outranks_an_otherwise_identical_copyleft_one(need):
    candidate = build_candidate(artifact(), need)
    ranker = Ranker(now=0.0)
    assert (
        ranker.score(candidate, obligation="permissive").total
        > ranker.score(candidate, obligation="strong_copyleft").total
    )


def test_a_stale_package_scores_worse_on_maintenance(need):
    import time

    now = time.time()
    fresh = build_candidate(artifact(released_at=now - 10 * 86400), need)
    stale = build_candidate(artifact(released_at=now - 1500 * 86400), need)
    ranker = Ranker(now=now)
    assert ranker.score(fresh).component("maintenance").value == 1.0
    assert ranker.score(stale).component("maintenance").value == 0.0


def test_ranking_ties_break_reproducibly(need):
    candidates = [
        build_candidate(artifact(name=name, released_at=None), need)
        for name in ("zeta", "alpha", "mu")
    ]
    ranked = Ranker(now=0.0).rank(candidates)
    assert [candidate.name for candidate, _ in ranked] == ["alpha", "mu", "zeta"]


# --- the assessment ------------------------------------------------------


@pytest.fixture
def world():
    transport = RecordedTransport()
    transport.record("https://pypi.org/robots.txt", ALLOW_ALL)
    transport.record("https://api.github.com/robots.txt", ALLOW_ALL)
    transport.record(
        "https://pypi.org/pypi/retry/json",
        {
            "info": {
                "name": "retry",
                "version": "0.9.2",
                "summary": "easy to use retry decorator with exponential backoff",
                "classifiers": ["License :: OSI Approved :: MIT License"],
                "requires_python": ">=3.7",
                "keywords": "retry backoff",
            },
            "urls": [{"upload_time_iso_8601": "2026-05-01T00:00:00Z"}],
        },
    )
    transport.record(
        "https://pypi.org/pypi/backoff/json",
        {
            "info": {
                "name": "backoff",
                "version": "2.2.1",
                "summary": "function decoration for backoff and retry",
                "classifiers": [
                    "License :: OSI Approved :: GNU Affero General Public License v3"
                ],
                "requires_python": ">=3.7",
                "keywords": "retry backoff",
            },
            "urls": [{"upload_time_iso_8601": "2026-05-01T00:00:00Z"}],
        },
    )
    fetcher = Fetcher(
        official_policy(interval=0.0), transport=transport, sleep=lambda seconds: None
    )
    engine = Engine(FactBase(), reuse_rules())
    crawler = Crawler(SourceRegistry(PyPISource(fetcher)), engine=engine, fetcher=fetcher)
    return crawler, engine


class Claude:
    """A stand-in teacher, so the loop's arithmetic is exercised for real."""

    name = "claude"

    def __init__(self) -> None:
        self.calls = 0

    def validate(self, need, prior=()):
        self.calls += 1
        return [
            Judgement(
                subject=entry["purl"],
                claim="validated-provides",
                value="retry-with-backoff",
                confidence=0.95,
                rationale="the summary describes exponential backoff",
            )
            for entry in need.context.get("candidates", ())
            if "backoff" in entry["summary"]
        ]


def test_an_assessment_admits_the_permissive_candidate_and_refuses_the_agpl_one(
    world, need
):
    crawler, engine = world
    assessment = ReuseAssessor(crawler=crawler, engine=engine).assess(need)
    assert assessment.best.purl == "pkg:pypi/retry@0.9.2"
    assert [result.candidate.name for result in assessment.rejected] == ["backoff"]


def test_a_refusal_is_part_of_the_answer_not_an_omission(world, need):
    crawler, engine = world
    assessment = ReuseAssessor(crawler=crawler, engine=engine).assess(need)
    refusal = assessment.rejected[0]
    assert "AGPL-3.0-only" in refusal.blockers[0].reason
    assert "backoff" in assessment.reasoning


def test_an_answer_resting_on_naming_alone_says_so(world, need):
    crawler, engine = world
    assessment = ReuseAssessor(crawler=crawler, engine=engine).assess(need)
    assert not assessment.best.confident
    assert any("naming alone" in gap for gap in assessment.gaps)


def test_validation_raises_the_evidence_and_removes_the_gap(world, need):
    crawler, engine = world
    loop = DistillationLoop(KnowledgeBase(), Claude(), engine=engine, audit_rate=0.0)
    assessment = ReuseAssessor(crawler=crawler, engine=engine, loop=loop).assess(need)
    assert assessment.agent_called
    assert assessment.best.confident
    assert not any("naming alone" in gap for gap in assessment.gaps)


def test_a_second_phrasing_of_one_need_does_not_call_the_agent_again(world, need):
    crawler, engine = world
    claude = Claude()
    loop = DistillationLoop(KnowledgeBase(), claude, engine=engine, audit_rate=0.0)
    assessor = ReuseAssessor(crawler=crawler, engine=engine, loop=loop)

    assessor.assess(need)
    second = assessor.assess(
        need_from_text(
            "HTTP client retry, exponential backoff",
            constraints=[Constraint(ConstraintKind.ECOSYSTEM, "pypi")],
        )
    )
    assert claude.calls == 1
    assert second.turn.route is Route.RECALLED
    assert second.best.confident


def test_attaching_candidates_never_changes_the_cache_key(world, need):
    crawler, engine = world
    loop = DistillationLoop(KnowledgeBase(), Claude(), engine=engine, audit_rate=0.0)
    assessor = ReuseAssessor(crawler=crawler, engine=engine, loop=loop)
    assessor.assess(need)
    # The assessor asserts this internally; recorded here because a regression
    # would be silent — the agent would simply be called every single time.
    assert loop.knowledge.recall(need.signature).hit


def test_without_a_validator_the_missing_judgement_is_reported(world, need):
    crawler, engine = world
    loop = DistillationLoop(KnowledgeBase(), None, engine=engine)
    assessment = ReuseAssessor(crawler=crawler, engine=engine, loop=loop).assess(need)
    assert assessment.turn.route is Route.UNAVAILABLE
    assert any("no validator" in gap for gap in assessment.gaps)


def test_an_assessor_with_nothing_to_search_says_so_rather_than_answering_emptily(need):
    assessment = ReuseAssessor().assess(need)
    assert not assessment.answered
    assert any("no crawler is configured" in gap for gap in assessment.gaps)


def test_an_over_constrained_need_is_named_as_such(world, need):
    crawler, engine = world
    strict = Need(
        concepts=need.concepts,
        constraints=(*need.constraints, Constraint(ConstraintKind.RUNTIME, "python2.7")),
    )
    assessment = ReuseAssessor(
        crawler=crawler, engine=engine, gate=Gate(runtime="python2.7")
    ).assess(strict)
    assert not assessment.answered
    assert any("over-constrained" in gap for gap in assessment.gaps)


def test_the_reasoning_records_every_step_that_produced_the_answer(world, need):
    crawler, engine = world
    assessment = ReuseAssessor(crawler=crawler, engine=engine).assess(need)
    reasoning = assessment.reasoning
    assert "crawled pypi" in reasoning
    assert "refused" in reasoning
    assert "admitted" in reasoning
    assert "gaps:" in reasoning


def test_an_assessment_serialises_whole(world, need):
    crawler, engine = world
    payload = ReuseAssessor(crawler=crawler, engine=engine).assess(need).to_dict()
    assert payload["best"]["purl"] == "pkg:pypi/retry@0.9.2"
    assert payload["rejected"][0]["blockers"]
    assert payload["crawl"]["terms"]
