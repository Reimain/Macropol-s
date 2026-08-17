"""Competitive intelligence that would survive due diligence.

The failure mode this module is built against is not a wrong number — it is a
comparison table that flatters us, was filled in from memory, and falls apart the
first time somebody opens one of the links. So the assertions here are mostly
about what the module *refuses* to do.

The strongest of them is `test_the_comparison_does_not_flatter_us`: the recorded
field must beat us on something. A scorecard we win on every row is a scorecard
nobody outside the building believes, and a type system cannot catch that — only
a test looking at the finished table can.
"""

from __future__ import annotations

import pytest

from slpie.compose import Composition, Context, Kind
from slpie.rivals import (
    CAPABILITIES,
    RECORDED,
    Capability,
    Coverage,
    Evidence,
    Leverage,
    Rival,
    Segment,
    field,
    opportunities,
    positioning,
    rival_registry,
)
from slpie.rivals.gap import gaps, render
from slpie.rivals.rival import RivalError

# --- an uncheckable claim will not construct --------------------------------


def test_a_capability_claim_without_a_source_is_refused():
    """The rule the whole module rests on."""
    with pytest.raises(RivalError, match="requires a source"):
        Capability("blast_radius", Coverage.FULL)


def test_not_having_checked_is_an_honest_answer():
    """`UNKNOWN` needs no citation, because it claims nothing."""
    assert Capability("blast_radius", Coverage.UNKNOWN).coverage is Coverage.UNKNOWN


def test_evidence_must_cite_something_openable():
    with pytest.raises(RivalError, match="not a URL"):
        Evidence(source="I read it somewhere", checked="2026-07")


def test_evidence_must_carry_the_month_it_was_checked():
    """A claim with no date cannot be told from a stale one."""
    with pytest.raises(RivalError, match="YYYY-MM"):
        Evidence(source="https://example.com", checked="last spring")


def test_a_rival_with_no_homepage_is_refused():
    with pytest.raises(RivalError, match="no homepage"):
        Rival(id="x", name="X", vendor="V", segments=(Segment.SCA,),
              homepage="ask around", summary="something")


def test_a_rival_nobody_described_is_refused():
    with pytest.raises(RivalError, match="no summary"):
        Rival(id="x", name="X", vendor="V", segments=(Segment.SCA,),
              homepage="https://example.com", summary="   ")


def test_a_capability_cannot_be_assessed_twice():
    cite = Evidence(source="https://example.com", checked="2026-07")
    with pytest.raises(RivalError, match="twice"):
        Rival(
            id="x", name="X", vendor="V", segments=(Segment.SCA,),
            homepage="https://example.com", summary="a product",
            capabilities=(
                Capability("blast_radius", Coverage.FULL, cite),
                Capability("blast_radius", Coverage.NONE, cite),
            ),
        )


# --- the recorded field -----------------------------------------------------


def test_every_recorded_product_cites_a_page_that_could_be_opened():
    for rival in rival_registry():
        assert rival.homepage.startswith("https://"), rival.id
        for assessment in rival.capabilities:
            if assessment.evidence is not None:
                assert assessment.evidence.source.startswith("https://"), (
                    f"{rival.id}/{assessment.capability}"
                )


def test_every_recorded_product_was_checked_this_period():
    """A record older than the file's own stamp is a record nobody re-read."""
    for rival in rival_registry():
        stale = rival.stale(before=RECORDED)
        assert not stale, f"{rival.id} has assessments older than {RECORDED}: {stale}"


def test_every_capability_is_assessed_for_every_product():
    """A blank cell reads as a cross. Silence must be spelled UNKNOWN."""
    names = {name for name, _ in CAPABILITIES}
    for rival in rival_registry():
        assessed = {item.capability for item in rival.capabilities}
        assert assessed == names, f"{rival.id} is missing {names - assessed}"


def test_no_capability_is_assessed_that_is_not_in_the_list():
    names = {name for name, _ in CAPABILITIES}
    for rival in rival_registry():
        for item in rival.capabilities:
            assert item.capability in names, f"{rival.id} scores an unlisted row"


# --- the part that keeps it credible ----------------------------------------


def test_the_comparison_does_not_flatter_us():
    """The field must beat us somewhere, or nobody will believe any of it.

    Not a style preference. A table where we win every row is the single most
    reliable signal that a competitive analysis was written by the vendor, and a
    buyer's first move is to stop reading it.
    """
    behind = [item for item in opportunities() if item.leverage is Leverage.FAR]

    assert behind, (
        "no capability is recorded as one the field leads on. Either the "
        "capability list was chosen to flatter us, or somebody deleted the "
        "honest rows"
    )


def test_where_we_are_behind_is_reported_beside_where_we_lead():
    text = positioning()

    assert "What we do that the recorded field mostly does not" in text
    assert "Where the field is ahead of us" in text


def test_a_gap_is_computed_rather_than_asserted():
    """Nobody types 'we are the only ones who do X'."""
    found = {gap.capability: gap for gap in gaps()}

    reconciliation = found["declared_vs_observed"]
    assert reconciliation.mean_coverage == 0.0
    assert len(reconciliation.absent_from) == len(rival_registry())

    # And a capability the field genuinely leads on is not reported as thin.
    assert not found["vulnerability_matching"].thin


def test_an_unverified_column_is_not_mistaken_for_white_space():
    """Four unknowns and a `NONE` is our ignorance, not an opportunity."""
    cite = Evidence(source="https://example.com", checked=RECORDED)
    unchecked = Rival(
        id="u", name="U", vendor="V", segments=(Segment.SCA,),
        homepage="https://example.com", summary="mostly unverified",
        capabilities=tuple(
            Capability(name, Coverage.UNKNOWN) for name, _ in CAPABILITIES
        ),
    )
    assert unchecked.verified_share == 0.0
    assert Capability("blast_radius", Coverage.FULL, cite).coverage.verified


def test_the_verified_share_is_reported_so_a_thin_record_cannot_hide():
    body = field()

    assert 0.0 <= body["verified_share"] <= 1.0
    for record in body["rivals"]:
        assert "verified_share" in record


def test_unknown_scores_zero_but_does_not_count_against_the_product():
    """It counts against our confidence instead, which is where it belongs."""
    assert Coverage.UNKNOWN.score == 0.0
    assert not Coverage.UNKNOWN.verified
    assert Coverage.NONE.verified, "a checked absence is a real finding"


# --- ranking ----------------------------------------------------------------


def test_what_we_already_ship_outranks_what_we_would_have_to_build():
    shipped = [i for i in opportunities() if i.leverage is Leverage.SHIPPED]
    far = [i for i in opportunities() if i.leverage is Leverage.FAR]

    assert shipped and far
    assert min(i.rank for i in shipped) > max(i.rank for i in far)


def test_an_unverified_opportunity_never_ranks():
    from slpie.rivals.gap import Gap, Opportunity

    blind = Opportunity(
        gap=Gap("x", "", 0.0, 0.0, (), ()), leverage=Leverage.UNVERIFIED,
    )
    assert blind.rank == 0.0
    assert not Leverage.UNVERIFIED.actionable


def test_opportunities_come_back_best_first():
    ranks = [item.rank for item in opportunities()]

    assert ranks == sorted(ranks, reverse=True)


# --- it composes ------------------------------------------------------------


def test_the_field_is_reachable_as_a_verb(verbs):
    result = Composition.read("rivals", verbs=verbs).run(Context())

    assert result.ok
    assert result.flow.kind is Kind.REPORT
    assert result.flow.size == len(rival_registry())
    assert "recorded" in result.flow.facts


def test_the_gaps_are_reachable_as_a_verb(verbs):
    result = Composition.read("rivals --gaps", verbs=verbs).run(Context())

    assert result.ok
    assert "Positioning" in result.flow.facts["rivals"]


def test_the_analysis_pipes_into_the_shaping_verbs(verbs):
    """`rivals --gaps | json` is the data-room export."""
    result = Composition.read("rivals --gaps | json", verbs=verbs).run(Context())

    assert result.ok


def test_the_table_renders_every_product_and_every_capability():
    text = render()

    for name, _ in CAPABILITIES:
        assert name in text
    for rival in rival_registry():
        assert rival.id[:9] in text
