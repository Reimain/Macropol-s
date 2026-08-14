"""The reference registries, and the discipline that keeps them honest.

The load-bearing tests here are not about content — content changes. They are
about the rules that stop a hand-written survey rotting: every protocol cites a
spec, every cited tool records *why* it was cited, and every claim that this
system implements an idea names the module that does. A registry without those
constraints becomes marketing within two releases.
"""

from __future__ import annotations

import pytest

from gratimos.reference import (
    Answered,
    Governance,
    Layer,
    Protocol,
    Reference,
    ReferenceError,
    Registry,
    Term,
    Tool,
    protocol_registry,
)


@pytest.fixture
def protocols():
    return protocol_registry()


@pytest.fixture
def paper():
    return Reference()


# --- the discipline -------------------------------------------------------


def test_a_protocol_without_a_spec_url_is_refused():
    with pytest.raises(ReferenceError, match="ages into confident wrongness"):
        Protocol(id="x", name="X", owner="someone", governance=Governance.VENDOR,
                 layer=Layer.AGENT_TO_AGENT, spec="", summary="does things")


def test_every_recorded_protocol_cites_a_specification(protocols):
    for protocol in protocols:
        assert protocol.spec.startswith("http"), protocol.id
        assert protocol.summary


def test_the_registry_can_list_everything_it_rests_on(protocols):
    sources = protocols.sources()
    assert len(sources) == len(protocols)
    assert all(url.startswith("https://") for url in sources)


def test_a_cited_tool_must_record_why_it_was_cited():
    with pytest.raises(ReferenceError, match="trivia"):
        Tool(key="x", name="X", org="Y", what="a thing", cited_for="")


def test_a_claim_to_implement_something_must_name_the_module():
    with pytest.raises(ReferenceError, match="unlocatable claim"):
        Term(key="x", name="X", definition="a definition", matters="because",
             answered=Answered.IMPLEMENTED, module="")


def test_every_implemented_claim_names_a_module_that_exists(paper):
    """A registry that points at modules we do not have is worse than silence."""
    import importlib

    for term in paper.terms:
        if term.answered in (Answered.IMPLEMENTED, Answered.PARTIAL):
            for name in (part.strip() for part in term.module.split(",")):
                importlib.import_module(name)


# --- protocol facts that are routinely got wrong --------------------------


def test_a2a_is_recorded_as_google_and_the_linux_foundation(protocols):
    a2a = protocols.require("a2a")
    assert "Google" in a2a.owner
    assert "Linux Foundation" in a2a.owner
    assert a2a.governance is Governance.FOUNDATION


def test_microsoft_is_recorded_as_an_adopter_of_a2a_not_its_author(protocols):
    """The misattribution this registry exists partly to prevent."""
    a2a = protocols.require("a2a")
    assert any("Microsoft" in name for name in a2a.adopters)
    assert "Microsoft" not in a2a.owner
    assert "adopter" in a2a.note


def test_the_microsoft_sdk_is_the_agent_framework_and_it_speaks_both(protocols):
    framework = protocols.require("microsoft-agent-framework")
    assert framework.owner == "Microsoft"
    assert set(framework.speaks) == {"mcp", "a2a"}


def test_mcp_is_anthropics_and_solves_a_different_problem_from_a2a(protocols):
    mcp = protocols.require("mcp")
    assert mcp.owner == "Anthropic"
    assert mcp.layer is Layer.AGENT_TO_TOOL
    assert protocols.require("a2a").layer is Layer.AGENT_TO_AGENT
    assert "not an alternative" in mcp.note


def test_the_registry_separates_the_layers_that_are_not_substitutes(protocols):
    assert {p.id for p in protocols.at_layer(Layer.AGENT_TO_AGENT)} >= {"a2a", "acp"}
    assert {p.id for p in protocols.at_layer(Layer.ORCHESTRATION)} >= {
        "microsoft-agent-framework", "openai-agents-sdk", "google-adk"
    }


def test_foundation_governed_protocols_can_be_singled_out(protocols):
    """Single-vendor governance is a real adoption risk, so it is queryable."""
    neutral = {p.id for p in protocols.foundation_governed()}
    assert "a2a" in neutral
    assert "openai-agents-sdk" not in neutral


def test_what_speaks_a_given_protocol_is_answerable(protocols):
    speakers = {p.id for p in protocols.interoperating_with("a2a")}
    assert "microsoft-agent-framework" in speakers
    assert "google-adk" in speakers


def test_recording_the_same_protocol_twice_is_refused(protocols):
    with pytest.raises(ReferenceError, match="already recorded"):
        protocols.add(protocols.require("a2a"))


def test_an_unknown_protocol_lists_the_known_ones(protocols):
    with pytest.raises(ReferenceError, match="known: a2a"):
        protocols.require("nonsense")


def test_the_registry_renders_by_layer(protocols):
    text = protocols.render()
    assert "agent_to_agent" in text
    assert "a2a-protocol.org" in text


# --- our own A2A implementation matches the recorded protocol -------------


def test_the_a2a_module_implements_the_concepts_the_registry_records(protocols):
    """The registry and the code must not drift apart silently."""
    from gratimos import a2a

    recorded = set(protocols.require("a2a").concepts)
    implemented = set(dir(a2a.types))
    for concept in ("AgentCard", "AgentSkill", "Task", "TaskState", "Message",
                    "Artifact"):
        assert concept in recorded
        assert concept in implemented


def test_our_agent_card_is_served_from_the_well_known_path():
    from gratimos.a2a.types import AGENT_CARD_PATH

    assert AGENT_CARD_PATH == "/.well-known/agent-card.json"


# --- the paper ------------------------------------------------------------


def test_the_four_priorities_are_recorded_in_order(paper):
    priorities = paper.priorities()
    assert len(priorities) == 4
    assert priorities[0].startswith("Ensure widespread access")
    assert "agent-ready" in priorities[1]
    assert "peer review" in priorities[3].lower()


def test_the_central_claim_is_recorded_as_a_term(paper):
    bottleneck = paper.term("validation-bottleneck")
    assert "widening, not closing" in bottleneck.definition


def test_calibration_is_recorded_as_a_gap_rather_than_claimed(paper):
    """We derive confidence; we have never measured whether it is calibrated."""
    calibration = paper.term("calibration")
    assert calibration.answered is Answered.OPEN
    assert calibration.module == ""


def test_provenance_records_are_recorded_as_a_gap(paper):
    assert paper.term("provenance-record").answered is Answered.OPEN


def test_the_registry_reports_its_gaps_rather_than_only_its_answers(paper):
    """A reference listing only what we already do is a brochure."""
    gaps = {term.key for term in paper.gaps()}
    assert {"calibration", "provenance-record"} <= gaps


def test_the_generator_verifier_pattern_maps_to_the_modules_that_do_it(paper):
    term = paper.term("generator-verifier-pairing")
    assert term.answered is Answered.IMPLEMENTED
    assert "guard" in term.module


def test_the_pyramid_tiers_are_both_recorded(paper):
    """Frontier models at the base, scaffolding, then customisation on top."""
    assert paper.term("scaffolding") is not None
    assert paper.term("customisation") is not None
    assert "pyramid" in paper.term("scaffolding").definition


def test_every_named_system_carries_the_argument_it_was_cited_for(paper):
    for tool in paper.named_tools:
        assert tool.cited_for.strip()


def test_alphafold_is_recorded_as_the_calibration_exemplar(paper):
    assert "calibration" in paper.tool("alphafold").cited_for.lower()


def test_co_scientist_is_recorded_with_the_decade_in_days_result(paper):
    cited = paper.tool("co-scientist").cited_for
    assert "decade" in cited
    assert "training data" in cited


def test_opensafely_is_recorded_as_the_agent_ready_data_model(paper):
    assert "never leave" in paper.tool("opensafely").what


def test_the_physical_limit_is_recorded_as_not_ours_to_solve(paper):
    assert paper.term("lab-sets-the-pace").answered is Answered.NOT_APPLICABLE


def test_the_reference_renders_with_its_gaps_last(paper):
    text = paper.render()
    assert "four priorities" in text
    assert "what we do not answer yet" in text
    assert text.index("four priorities") < text.index("what we do not answer yet")


def test_the_whole_reference_serialises(paper, protocols):
    assert paper.to_dict()["source"].startswith("https://deepmind.google/")
    assert len(protocols.to_dict()["protocols"]) == len(protocols)
