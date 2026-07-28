"""The ontology, the inference engine, and the loop that turns the agent off."""

from __future__ import annotations

import random
import time

import pytest

from gratimos.distill import (
    DistillationLoop,
    Judgement,
    KnowledgeBase,
    Route,
    Verdict,
    Volatility,
)
from gratimos.distill.knowledge import DAY, ESCALATION_FLOOR, RECALL_CEILING
from gratimos.ontology import Concept, Domain, Ontology, OntologyError, base_ontology
from gratimos.ontology.need import (
    Constraint,
    ConstraintKind,
    Need,
    extract_concepts,
    need_from_text,
)
from gratimos.reason import (
    Engine,
    Fact,
    FactBase,
    Pattern,
    Provenance,
    ReasoningError,
    Rule,
    RuleSet,
    combine_confidence,
    reuse_rules,
)


@pytest.fixture
def ontology():
    return base_ontology()


@pytest.fixture
def engine(ontology):
    built = Engine(FactBase(), reuse_rules())
    for concept in ontology:
        for parent in concept.broader:
            built.facts.assert_fact(
                Fact(concept.name, "is-a", parent, provenance=Provenance.DECLARED)
            )
    return built


# --- the concept lattice -------------------------------------------------


def test_subsumption_runs_one_way_only(ontology):
    """Asking for jitter and being handed plain retry is a wrong answer."""
    assert ontology.satisfies("retry-with-jitter", "retry")
    assert not ontology.satisfies("retry", "retry-with-jitter")


def test_an_unrelated_concept_satisfies_nothing(ontology):
    assert not ontology.satisfies("circuit-breaker", "retry")
    assert ontology.distance("circuit-breaker", "retry") == -1


def test_distance_ranks_an_exact_match_above_a_general_one(ontology):
    assert ontology.distance("retry", "retry") == 0
    assert ontology.distance("retry-with-jitter", "retry-with-backoff") == 1
    assert ontology.distance("retry-with-jitter", "retry") == 2


def test_a_concept_can_specialise_more_than_one_parent(ontology):
    """`async-http-client` is both an http client and an async thing. Forcing a
    single parent would lose one of those facts."""
    assert ontology.satisfies("async-http-client", "http-client")
    assert ontology.satisfies("async-http-client", "async-io")


def test_synonyms_fold_so_two_spellings_are_one_concept(ontology):
    for spelling in ("Exponential Backoff", "exponential_backoff", "backoff"):
        assert ontology.canonical(spelling) == "retry-with-backoff"
    assert ontology.canonical("HTTP Client") == "http-client"
    assert ontology.canonical("serialization") == "serialisation"


def test_the_closure_carries_everything_a_concept_implies(ontology):
    assert ontology.closure(["retry-with-jitter"]) == {
        "retry-with-jitter", "retry-with-backoff", "retry", "resilience",
    }


def test_an_unknown_term_passes_through_rather_than_raising(ontology):
    """A need may mention something the lattice has not learned. Losing it
    would be worse than carrying it unresolved."""
    assert ontology.canonical("Quantum Flux") == "quantum-flux"
    assert ontology.get("quantum-flux") is None


def test_a_dangling_parent_is_refused():
    with pytest.raises(OntologyError, match="unknown parent"):
        Ontology([Concept(name="a", broader=("nowhere",))]).validate()


def test_a_cycle_is_refused():
    cyclic = Ontology([
        Concept(name="x", broader=("y",)), Concept(name="y", broader=("x",)),
    ])
    with pytest.raises(OntologyError, match="cycle"):
        cyclic.validate()


def test_a_duplicate_concept_is_refused():
    with pytest.raises(OntologyError, match="already defined"):
        Ontology([Concept(name="a"), Concept(name="a")])


# --- the need signature --------------------------------------------------


def test_three_phrasings_of_one_need_produce_one_key(ontology):
    """The property the whole loop rests on. If these hash differently the
    knowledge base never hits and the agent is called every time."""
    signatures = {
        need_from_text(text, ontology=ontology).signature
        for text in (
            "I need to retry failed HTTP requests with exponential backoff",
            "HTTP client retry, exponential backoff",
            "something for retrying http calls using backoff",
        )
    }
    assert len(signatures) == 1


def test_a_different_need_produces_a_different_key(ontology):
    left = need_from_text("retry http with backoff", ontology=ontology)
    right = need_from_text("I need a circuit breaker", ontology=ontology)
    assert left.signature != right.signature


def test_concept_order_cannot_change_the_key():
    left = Need(concepts=("retry", "http-client"))
    right = Need(concepts=("http-client", "retry"))
    assert left.signature == right.signature


def test_constraints_are_part_of_the_key():
    """The same capability under a licence we cannot absorb is a different
    question, and must not be answered from the other one's cache entry."""
    plain = Need(concepts=("retry",))
    constrained = Need(
        concepts=("retry",),
        constraints=(Constraint(ConstraintKind.LICENCE, "MIT"),),
    )
    assert plain.signature != constrained.signature


def test_hard_constraints_are_distinguished_from_preferences():
    licence = Constraint(ConstraintKind.LICENCE, "MIT")
    size = Constraint(ConstraintKind.SIZE, "small")

    assert licence.is_hard
    assert not size.is_hard


def test_a_longer_phrase_is_matched_before_its_parts(ontology):
    """`exponential backoff` must not be consumed as `backoff` with
    `exponential` left dangling."""
    assert "retry-with-backoff" in extract_concepts("exponential backoff", ontology)


def test_a_need_with_nothing_recognisable_is_refused(ontology):
    """An empty need matches every candidate and caches under a key meaning
    'anything' — the worst possible entry to have."""
    with pytest.raises(ValueError, match="no known concepts"):
        need_from_text("please do the thing with the stuff", ontology=ontology)


# --- facts and patterns --------------------------------------------------


def test_a_fact_cannot_contain_a_variable():
    with pytest.raises(ReasoningError, match="cannot contain variables"):
        Fact("?pkg", "provides", "retry")


def test_a_fact_is_identified_by_its_claim_not_its_route():
    """Two routes to the same conclusion are one fact — which is what lets
    forward chaining reach a fixpoint."""
    observed = Fact("a", "provides", "retry", provenance=Provenance.OBSERVED)
    derived = Fact("a", "provides", "retry", provenance=Provenance.DERIVED)
    assert observed.id == derived.id


def test_a_pattern_binds_consistently_across_a_rule():
    pattern = Pattern("?a", "depends-on", "?b")
    bindings = pattern.match(Fact("x", "depends-on", "y", provenance=Provenance.OBSERVED))

    assert bindings == {"?a": "x", "?b": "y"}
    # The same variable cannot bind two ways.
    assert pattern.match(
        Fact("z", "depends-on", "y", provenance=Provenance.OBSERVED), bindings
    ) is None


def test_a_weaker_restatement_never_downgrades_what_is_known():
    base = FactBase()
    base.assert_fact(Fact("a", "provides", "retry", provenance=Provenance.OBSERVED))
    base.assert_fact(Fact("a", "provides", "retry", provenance=Provenance.ASSUMED))

    assert base.facts[0].provenance is Provenance.OBSERVED


def test_confidence_is_the_weakest_premise_not_the_product():
    """Multiplying would make a five-step derivation from certainties look like
    a guess; averaging would let one bad premise hide behind four good ones."""
    assert combine_confidence([0.9, 0.9, 0.9, 0.9]) == pytest.approx(0.9)
    assert combine_confidence([0.9, 0.3]) == pytest.approx(0.3)
    assert combine_confidence([0.9], factor=0.5) == pytest.approx(0.45)


def test_inference_never_reaches_certainty():
    assert combine_confidence([1.0, 1.0]) < 1.0


# --- rules ---------------------------------------------------------------


def test_a_rule_concluding_about_an_unbound_variable_is_refused():
    """It would raise every time it fired. Better here, where the author is
    looking, than at 3am in a crawl."""
    with pytest.raises(ReasoningError, match="unbound"):
        Rule(
            name="bad",
            when=(Pattern("?a", "provides", "retry"),),
            then=Pattern("?b", "satisfies", "?c"),
        )


def test_a_rule_needs_antecedents_and_a_valid_strength():
    with pytest.raises(ReasoningError, match="no antecedents"):
        Rule(name="empty", when=(), then=Pattern("a", "b", "c"))
    with pytest.raises(ReasoningError, match="strength"):
        Rule(
            name="over", when=(Pattern("?a", "b", "c"),),
            then=Pattern("?a", "d", "e"), strength=1.5,
        )


def test_heuristic_rules_are_marked_as_such():
    rules = reuse_rules()
    assert rules.get("name-suggests-capability").heuristic
    assert not rules.get("licence-from-declaration").heuristic


# --- forward chaining ----------------------------------------------------


def test_forward_chaining_reaches_a_fixpoint(engine):
    report = engine.learn(
        Fact("pkg:a", "validated-provides", "retry-with-jitter",
             provenance=Provenance.VALIDATED),
    )
    assert report.count > 0
    assert not report.saturated          # it stopped because nothing else followed

    # Running again derives nothing new.
    assert engine.saturate().count == 0


def test_one_validation_answers_questions_nobody_asked(engine):
    """The background half: a single verdict propagates through the lattice."""
    engine.learn(
        Fact("pkg:a", "validated-provides", "retry-with-jitter",
             provenance=Provenance.VALIDATED),
    )

    assert engine.holds("pkg:a", "provides", "retry")
    assert engine.holds("pkg:a", "provides", "resilience")
    assert not engine.holds("pkg:a", "provides", "circuit-breaker")


def test_a_conclusion_weakens_as_it_generalises(engine):
    engine.learn(
        Fact("pkg:a", "validated-provides", "retry-with-jitter",
             provenance=Provenance.VALIDATED),
    )
    specific = engine.ask("pkg:a", "provides", "retry-with-jitter").confidence
    general = engine.ask("pkg:a", "provides", "resilience").confidence

    assert specific > general


def test_a_heuristic_conclusion_stays_weak(engine):
    """A thousand naming coincidences do not make a fact."""
    engine.learn(Fact("pkg:b", "named-like", "retry", provenance=Provenance.OBSERVED))

    proof = engine.ask("pkg:b", "provides", "retry")
    assert proof.proven
    assert proof.confidence < 0.5


def test_health_signals_block_a_candidate(engine):
    engine.learn(Fact("pkg:old", "archived", "true", provenance=Provenance.OBSERVED))

    assert engine.holds("pkg:old", "unmaintained", "true")
    assert engine.ask("pkg:old", "blocked", "?why").values_of("?why") == ("unmaintained",)


# --- backward chaining ---------------------------------------------------


def test_backward_chaining_finds_every_candidate(engine):
    engine.learn(
        Fact("need:r", "requires", "retry", provenance=Provenance.DECLARED),
        Fact("pkg:good", "validated-provides", "retry-with-jitter",
             provenance=Provenance.VALIDATED),
        Fact("pkg:good", "licence-compatible-with", "need:r",
             provenance=Provenance.OBSERVED),
        Fact("pkg:weak", "named-like", "retry", provenance=Provenance.OBSERVED),
        Fact("pkg:weak", "licence-compatible-with", "need:r",
             provenance=Provenance.OBSERVED),
    )

    proof = engine.ask("?pkg", "satisfies", "need:r")
    assert set(proof.values_of("?pkg")) == {"pkg:good", "pkg:weak"}


def test_a_validated_candidate_outranks_a_guessed_one(engine):
    """Both are offered — excluding the guess silently would be worse — but the
    confidence has to say which one is actually believed."""
    engine.learn(
        Fact("need:r", "requires", "retry", provenance=Provenance.DECLARED),
        Fact("pkg:good", "validated-provides", "retry-with-jitter",
             provenance=Provenance.VALIDATED),
        Fact("pkg:good", "licence-compatible-with", "need:r",
             provenance=Provenance.OBSERVED),
        Fact("pkg:weak", "named-like", "retry", provenance=Provenance.OBSERVED),
        Fact("pkg:weak", "licence-compatible-with", "need:r",
             provenance=Provenance.OBSERVED),
    )

    good = engine.ask("pkg:good", "satisfies", "need:r").confidence
    weak = engine.ask("pkg:weak", "satisfies", "need:r").confidence
    assert good > weak


def test_a_proof_from_memory_carries_that_facts_confidence(engine):
    """Reporting 1.0 because no rule had to fire would make a remembered guess
    look certain."""
    engine.facts.assert_fact(
        Fact("pkg:a", "provides", "retry", provenance=Provenance.ASSUMED)
    )
    proof = engine.ask("pkg:a", "provides", "retry")

    assert proof.proven
    assert proof.confidence == pytest.approx(Provenance.ASSUMED.base_confidence)


def test_an_unprovable_goal_says_so(engine):
    proof = engine.ask("pkg:nothing", "satisfies", "need:nothing")

    assert not proof.proven
    assert not proof
    assert "not proven" in proof.render()


def test_a_self_supporting_goal_terminates(engine):
    """A rule whose only support is the goal itself must not recurse forever."""
    engine.rules.define(
        "circular", when=[("?a", "provides", "?b")], then=("?a", "provides", "?b"),
    )
    assert not engine.ask("pkg:none", "provides", "retry").proven


def test_an_explanation_walks_back_to_what_was_observed(engine):
    engine.learn(
        Fact("need:r", "requires", "retry", provenance=Provenance.DECLARED),
        Fact("pkg:a", "validated-provides", "retry-with-jitter",
             provenance=Provenance.VALIDATED),
        Fact("pkg:a", "licence-compatible-with", "need:r",
             provenance=Provenance.OBSERVED),
    )
    conclusion = next(f for f in engine.facts if f.predicate == "satisfies")

    chain = engine.explain(conclusion)
    assert chain[-1].id == conclusion.id
    assert any(not step.provenance.is_inferred for step in chain)


def test_an_inference_citing_nothing_is_reported(engine):
    engine.facts.assert_fact(
        Fact("pkg:a", "provides", "retry", provenance=Provenance.DERIVED)
    )
    assert len(engine.facts.ungrounded()) == 1


# --- the knowledge base --------------------------------------------------


def a_verdict(**overrides):
    defaults = dict(
        signature="sig-1", subject="pkg:pypi/tenacity", claim="provides",
        value="retry-with-jitter", confidence=0.93,
        volatility=Volatility.MODERATE, validator="claude-opus-5",
    )
    return Verdict(**{**defaults, **overrides})


def test_a_verdict_decays_on_its_volatility_half_life():
    verdict = a_verdict(volatility=Volatility.MODERATE)
    fresh = verdict.current_confidence()
    half_life = verdict.current_confidence(now=time.time() + 120 * DAY)

    assert half_life == pytest.approx(fresh / 2, abs=0.01)


def test_different_claims_decay_at_different_rates():
    """A licence and a maintenance status must not share a TTL."""
    now = time.time() + 30 * DAY
    licence = a_verdict(volatility=Volatility.SLOW).current_confidence(now=now)
    maintenance = a_verdict(volatility=Volatility.FAST).current_confidence(now=now)

    assert licence > maintenance


def test_an_immutable_claim_never_decays():
    verdict = a_verdict(volatility=Volatility.IMMUTABLE)
    assert verdict.current_confidence(now=time.time() + 10_000 * DAY) > 0.9


def test_a_recollection_is_never_worth_as_much_as_the_judgement():
    """It is a memory of a judgement, not the judgement itself."""
    assert a_verdict(confidence=1.0).current_confidence() <= RECALL_CEILING


def test_recall_hits_only_while_the_verdict_is_still_good():
    base = KnowledgeBase()
    base.record(a_verdict())

    assert base.recall("sig-1").hit
    assert not base.recall("sig-1", now=time.time() + 400 * DAY).hit


def test_a_stale_recall_explains_itself_in_the_terms_that_rejected_it():
    """An explanation evaluated at a different clock than the decision is worse
    than none."""
    base = KnowledgeBase()
    base.record(a_verdict())

    recall = base.recall("sig-1", now=time.time() + 400 * DAY)
    assert "400 days" in recall.reason
    assert str(ESCALATION_FLOOR) in recall.reason


def test_invalidation_retires_a_verdict_at_once():
    """Waiting for decay would leave a wrong answer in service for a half-life."""
    base = KnowledgeBase()
    base.record(a_verdict())

    assert base.invalidate("pkg:pypi/tenacity", claim="provides") == 1
    assert not base.recall("sig-1").hit


def test_a_superseded_verdict_is_kept_rather_than_deleted():
    base = KnowledgeBase()
    base.record(a_verdict())
    base.invalidate("pkg:pypi/tenacity")

    assert base.about("pkg:pypi/tenacity") == ()
    assert len(base.about("pkg:pypi/tenacity", include_superseded=True)) == 1


def test_a_verdict_crosses_into_the_engine_as_a_distilled_fact():
    fact = a_verdict().as_fact()

    assert fact.provenance is Provenance.DISTILLED
    assert fact.confidence < Provenance.VALIDATED.base_confidence


def test_the_base_survives_a_round_trip(tmp_path):
    """The base is the asset — losing it means paying for it again."""
    base = KnowledgeBase()
    base.record(a_verdict())
    base.save(tmp_path / "kb.json")

    restored = KnowledgeBase.load(tmp_path / "kb.json")
    assert len(restored) == 1
    assert restored.recall("sig-1").hit


def test_loading_a_base_that_is_not_there_is_empty_not_fatal(tmp_path):
    assert len(KnowledgeBase.load(tmp_path / "nothing.json")) == 0


def test_pruning_drops_what_can_no_longer_help():
    base = KnowledgeBase()
    base.record(a_verdict(volatility=Volatility.VOLATILE))

    assert base.prune(now=time.time() + 365 * DAY) == 1
    assert len(base) == 0


# --- the distillation loop ----------------------------------------------


class CountingValidator:
    """Stands in for Claude, and counts how often it is actually consulted."""

    name = "test-validator"

    def __init__(self, value: str = "retry-with-jitter") -> None:
        self.calls = 0
        self.priors: list[int] = []
        self.value = value

    def validate(self, need, prior=()):
        self.calls += 1
        self.priors.append(len(prior))
        return [Judgement(
            subject="pkg:pypi/tenacity", claim="provides", value=self.value,
            confidence=0.93, volatility=Volatility.MODERATE,
        )]


@pytest.fixture
def phrasings():
    return (
        "I need to retry failed HTTP requests with exponential backoff",
        "HTTP client retry, exponential backoff",
        "something for retrying http calls using backoff",
    )


def test_the_agent_is_called_once_for_every_phrasing_of_one_need(ontology, phrasings):
    """This is the claim the whole subsystem exists to make good on."""
    validator = CountingValidator()
    loop = DistillationLoop(validator=validator, audit_rate=0.0)

    routes = [
        loop.resolve(need_from_text(text, ontology=ontology)).route
        for text in phrasings
    ]

    assert validator.calls == 1
    assert routes[0] is Route.ESCALATED_UNKNOWN
    assert all(route is Route.RECALLED for route in routes[1:])


def test_a_recalled_answer_reports_that_no_agent_was_called(ontology, phrasings):
    loop = DistillationLoop(validator=CountingValidator(), audit_rate=0.0)
    loop.resolve(need_from_text(phrasings[0], ontology=ontology))

    turn = loop.resolve(need_from_text(phrasings[1], ontology=ontology))
    assert not turn.agent_called
    assert turn.answered
    assert "agent not called" in turn.explanation


def test_a_decayed_verdict_escalates_as_if_it_were_absent(ontology, phrasings):
    """The floor is on decayed confidence, not on presence."""
    validator = CountingValidator()
    loop = DistillationLoop(validator=validator, audit_rate=0.0)
    loop.resolve(need_from_text(phrasings[0], ontology=ontology))

    turn = loop.resolve(
        need_from_text(phrasings[0], ontology=ontology), now=time.time() + 400 * DAY,
    )
    assert turn.route is Route.ESCALATED_STALE
    assert validator.calls == 2


def test_a_stale_verdict_is_handed_over_as_a_prior_not_an_answer(ontology, phrasings):
    """An agent told what was previously believed can correct it explicitly,
    which is how a wrong verdict is retired rather than merely aged out."""
    validator = CountingValidator()
    loop = DistillationLoop(validator=validator, audit_rate=0.0)
    loop.resolve(need_from_text(phrasings[0], ontology=ontology))

    loop.resolve(
        need_from_text(phrasings[0], ontology=ontology), now=time.time() + 400 * DAY,
    )
    assert validator.priors[-1] == 1


def test_a_contradicting_judgement_retires_the_old_verdict(ontology, phrasings):
    need = need_from_text(phrasings[0], ontology=ontology)
    loop = DistillationLoop(validator=CountingValidator("retry"), audit_rate=0.0)
    loop.resolve(need)

    loop.validator = CountingValidator("circuit-breaker")
    loop.resolve(need, force=True)

    live = loop.knowledge.about("pkg:pypi/tenacity")
    assert [verdict.value for verdict in live] == ["circuit-breaker"]


def test_forcing_a_fresh_look_bypasses_a_usable_recall(ontology, phrasings):
    validator = CountingValidator()
    loop = DistillationLoop(validator=validator, audit_rate=0.0)
    need = need_from_text(phrasings[0], ontology=ontology)

    loop.resolve(need)
    turn = loop.resolve(need, force=True)

    assert turn.route is Route.ESCALATED_FORCED
    assert validator.calls == 2


def test_a_sample_of_hits_is_revalidated_anyway(ontology, phrasings):
    """Without it the base can only ever confirm itself, and a systematically
    wrong verdict is never caught."""
    validator = CountingValidator()
    loop = DistillationLoop(
        validator=validator, audit_rate=0.5, rng=random.Random(3),
    )
    need = need_from_text(phrasings[0], ontology=ontology)

    loop.resolve(need)
    routes = [loop.resolve(need).route for _ in range(20)]

    assert Route.AUDITED in routes
    assert Route.RECALLED in routes


def test_a_loop_with_no_validator_admits_it_rather_than_inventing(ontology, phrasings):
    loop = DistillationLoop(validator=None)
    turn = loop.resolve(need_from_text(phrasings[0], ontology=ontology))

    assert turn.route is Route.UNAVAILABLE
    assert not turn.answered
    assert "no validator" in turn.explanation


def test_a_recorded_verdict_propagates_through_the_engine(ontology, engine, phrasings):
    """One validation answers every question it implies, not only the one asked."""
    loop = DistillationLoop(
        validator=CountingValidator(), engine=engine, audit_rate=0.0,
    )
    turn = loop.resolve(need_from_text(phrasings[0], ontology=ontology))

    assert turn.propagated > 0
    assert engine.holds("pkg:pypi/tenacity", "provides", "retry")
    assert engine.holds("pkg:pypi/tenacity", "provides", "resilience")


def test_the_loop_reports_how_much_work_it_absorbed(ontology, phrasings):
    loop = DistillationLoop(validator=CountingValidator(), audit_rate=0.0)
    for text in phrasings:
        loop.resolve(need_from_text(text, ontology=ontology))

    status = loop.status()
    assert status["turns"] == 3
    assert status["agent_calls"] == 1
    assert status["avoidance_rate"] == pytest.approx(2 / 3, abs=1e-4)


def test_an_invalid_audit_rate_is_refused():
    with pytest.raises(ValueError, match="fraction"):
        DistillationLoop(audit_rate=1.5)
