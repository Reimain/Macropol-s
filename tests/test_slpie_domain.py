"""Versions, licences, findings and reasoning paths.

The rest of Phase 1: the algebras the constraint solver, the compliance engine
and the console are all built on.
"""

from __future__ import annotations

import pytest

from slpie.domain.evidence import Evidence, EvidenceKind, SourceLocation
from slpie.domain.finding import (
    Finding,
    FindingKind,
    Gap,
    GapKind,
    Remediation,
    aggregate_risk,
    merge_gaps,
    rank,
)
from slpie.domain.license import (
    Distribution,
    Linkage,
    Obligation,
    check_compatibility,
    combined_obligation,
    normalize,
    parse_expression,
    spdx_ids,
)
from slpie.domain.lifecycle import RiskClass, Severity
from slpie.domain.reasoning import (
    Clarification,
    Enrichment,
    Guidance,
    Option,
    Question,
    ReasoningPath,
    ReasoningStep,
    rank_questions,
    trace_to_evidence,
)
from slpie.domain.version import Version, VersionRange, parse_range
from slpie.errors import EvidenceRequired, LicenseError, VersionError

LOCKFILE = Evidence(
    kind=EvidenceKind.LOCKFILE_PIN,
    location=SourceLocation("file:///r/package-lock.json", line=1204),
    extractor="npm",
    excerpt='"lodash": {"version": "4.17.20"}',
)
IMPORT = Evidence(
    kind=EvidenceKind.STATIC_IMPORT,
    location=SourceLocation("file:///r/src/a.js", line=3),
    extractor="js",
    excerpt="require('lodash')",
)


# --- versions ------------------------------------------------------------


def test_semver_precedence_puts_prereleases_before_their_release():
    ordered = sorted(Version.parse(v) for v in [
        "1.0.0", "1.0.0-rc.1", "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-beta", "1.0.1",
    ])
    assert [v.raw for v in ordered] == [
        "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-beta", "1.0.0-rc.1", "1.0.0", "1.0.1",
    ]


def test_numeric_components_compare_as_numbers_not_as_text():
    assert Version.parse("1.10.0") > Version.parse("1.2.0")
    assert Version.parse("1.0.0") < Version.parse("1.0.10")


def test_post_release_qualifiers_sort_after_the_bare_release():
    # Maven's `.RELEASE` and PEP 440's `.post1` come after, unlike a prerelease.
    assert Version.parse("1.0.0.RELEASE") > Version.parse("1.0.0")
    assert Version.parse("1.0.post1") > Version.parse("1.0")
    assert Version.parse("1.0.0-rc1") < Version.parse("1.0.0")


def test_missing_components_are_zero_and_build_metadata_is_not_compared():
    assert Version.parse("1.2") == Version.parse("1.2.0")
    assert Version.parse("1.0.0+aaa") == Version.parse("1.0.0+bbb")


def test_an_epoch_outranks_the_release_number_entirely():
    assert Version.parse("1!1.0") > Version.parse("2.0")


def test_a_token_that_was_never_a_version_is_rejected_rather_than_guessed():
    for text in ("latest-and-greatest", "master", "see-the-changelog"):
        with pytest.raises(VersionError):
            Version.parse(text)


def test_caret_ranges_expand_within_the_leftmost_non_zero_component():
    assert _bounds("^1.2.3", "npm") == [">=1.2.3", "<2.0.0"]
    assert _bounds("^0.2.3", "npm") == [">=0.2.3", "<0.3.0"]
    assert _bounds("^0.0.3", "npm") == [">=0.0.3", "<0.0.4"]


def test_tilde_ranges_depend_on_how_many_components_were_given():
    assert _bounds("~1.2.3", "npm") == [">=1.2.3", "<1.3.0"]
    assert _bounds("~1", "npm") == [">=1", "<2.0.0"]


def test_pep440_compatible_release_pins_the_penultimate_component():
    assert _bounds("~=1.4.2", "pypi") == [">=1.4.2", "<1.5"]
    range_ = parse_range("~=1.4.2", ecosystem="pypi")
    assert range_.allows("1.4.9") and not range_.allows("1.5.0")


def test_wildcards_become_half_open_intervals():
    assert _bounds("1.2.*", "pypi") == [">=1.2.0", "<1.3.0"]
    assert _bounds("1.x", "npm") == [">=1.0.0", "<2.0.0"]


def test_an_x_inside_a_version_is_not_a_wildcard():
    # `1.0.0-x86` is an exact version, not "anything in the 1 series".
    range_ = parse_range("1.0.0-x86")
    assert range_.is_exact
    assert not range_.allows("1.5.0")


def test_maven_interval_notation_parses_to_bounds():
    assert _bounds("[1.0,2.0)", "maven") == [">=1.0", "<2.0"]
    assert _bounds("(,1.0]", "maven") == ["<=1.0"]
    assert _bounds("[1.0]", "maven") == ["=1.0"]


def test_npm_disjunctions_and_hyphen_ranges():
    disjunction = parse_range("^1.0.0 || ^2.0.0", ecosystem="npm")
    assert len(disjunction.clauses) == 2
    assert disjunction.allows("1.9.0") and disjunction.allows("2.3.0")
    assert not disjunction.allows("3.0.0")

    hyphen = parse_range("1.2.3 - 2.3.4", ecosystem="npm")
    assert hyphen.allows("2.3.4") and not hyphen.allows("2.3.5")


def test_an_unparseable_range_raises_rather_than_widening_to_anything():
    # Silently becoming a wildcard is how a solver ends up confidently wrong.
    with pytest.raises(VersionError):
        parse_range(">=nonsense")


def test_an_explicit_wildcard_is_still_a_wildcard():
    for text in ("*", "", "latest", "any"):
        assert parse_range(text).is_any


def test_best_picks_the_highest_allowed_candidate():
    range_ = parse_range("^1.2.3", ecosystem="npm")
    assert str(range_.best(["1.0.0", "1.5.2", "2.1.0", "1.9.9"])) == "1.9.9"
    assert range_.best(["3.0.0"]) is None


def test_change_level_drives_upgrade_risk():
    assert Version.parse("2.0.0").change_from(Version.parse("1.9.9")) == "major"
    assert Version.parse("1.3.0").change_from(Version.parse("1.2.9")) == "minor"
    assert Version.parse("1.2.4").change_from(Version.parse("1.2.3")) == "patch"
    assert Version.parse("1.2.3").change_from(Version.parse("1.2.3")) == "none"


def _bounds(spec, ecosystem="generic"):
    parsed = parse_range(spec, ecosystem=ecosystem)
    assert len(parsed.clauses) == 1
    return [str(comparator) for comparator in parsed.clauses[0]]


# --- licences ------------------------------------------------------------


def test_a_disjunction_lets_you_choose_the_least_demanding_branch():
    expression = parse_expression("MIT OR GPL-3.0-only")

    assert expression.is_choice
    assert expression.obligation is Obligation.PERMISSIVE
    assert [t.identifier for t in expression.preferred_option()] == ["MIT"]


def test_a_conjunction_imposes_every_obligation_at_once():
    expression = parse_expression("MIT AND GPL-3.0-only")

    assert not expression.is_choice
    assert expression.obligation is Obligation.STRONG_COPYLEFT


def test_parentheses_distribute_into_disjunctive_normal_form():
    expression = parse_expression("(MIT OR Apache-2.0) AND Unicode-DFS-2016")

    assert len(expression.options) == 2
    assert all(len(option) == 2 for option in expression.options)
    assert expression.obligation is Obligation.PERMISSIVE


def test_an_exception_can_neutralise_the_copyleft_it_qualifies():
    plain = parse_expression("GPL-2.0-only")
    excepted = parse_expression("GPL-2.0-only WITH Classpath-exception-2.0")

    assert plain.obligation is Obligation.STRONG_COPYLEFT
    assert excepted.obligation is Obligation.PERMISSIVE


def test_colloquial_licence_names_resolve_to_spdx_identifiers():
    assert spdx_ids("Apache 2.0") == ("Apache-2.0",)
    assert spdx_ids("GPLv3") == ("GPL-3.0-only",)
    # `+` means "or later", which is a different licence set from `-only`.
    assert spdx_ids("gpl-3.0+") == ("GPL-3.0-or-later",)


def test_a_malformed_expression_is_an_error():
    for text in ("", "MIT AND", "(MIT OR Apache-2.0", "MIT WITH", "AND MIT"):
        with pytest.raises(LicenseError):
            parse_expression(text)


def test_an_unrecognisable_licence_becomes_an_explicit_unknown_not_a_lost_fact():
    expression = normalize("see LICENSE file")

    assert expression.obligation is Obligation.UNKNOWN
    assert expression.unknown_terms
    # And that is treated as non-compliant until somebody resolves it.
    assert not check_compatibility(expression, distribution=Distribution.INTERNAL)


def test_network_copyleft_turns_on_the_distribution_context():
    saas = check_compatibility("AGPL-3.0-only", distribution=Distribution.NETWORK_SERVICE)
    internal = check_compatibility("AGPL-3.0-only", distribution=Distribution.INTERNAL)

    assert not saas and "network_service" in saas.reason
    assert internal


def test_strong_copyleft_turns_on_conveyance_not_on_use():
    shipped = check_compatibility("GPL-3.0-only", distribution=Distribution.DISTRIBUTED_BINARY)
    hosted = check_compatibility("GPL-3.0-only", distribution=Distribution.NETWORK_SERVICE)

    assert not shipped
    assert hosted


def test_a_copyleft_project_may_consume_a_copyleft_dependency():
    verdict = check_compatibility(
        "GPL-3.0-only", project="GPL-3.0-only",
        distribution=Distribution.DISTRIBUTED_BINARY,
    )
    assert verdict


def test_weak_copyleft_turns_on_linkage():
    static = check_compatibility(
        "LGPL-3.0-only", distribution=Distribution.DISTRIBUTED_BINARY, linkage=Linkage.STATIC,
    )
    dynamic = check_compatibility(
        "LGPL-3.0-only", distribution=Distribution.DISTRIBUTED_BINARY, linkage=Linkage.DYNAMIC,
    )

    assert not static
    assert dynamic


def test_an_incompatibility_arrives_with_something_to_do_about_it():
    verdict = check_compatibility("AGPL-3.0-only", distribution=Distribution.NETWORK_SERVICE)
    assert verdict.remediation


def test_the_strictest_obligation_wins_across_a_whole_dependency_set():
    assert combined_obligation(["MIT", "Apache-2.0"]) is Obligation.PERMISSIVE
    assert combined_obligation(["MIT", "LGPL-3.0-only"]) is Obligation.WEAK_COPYLEFT
    assert combined_obligation(["MIT", "AGPL-3.0-only"]) is Obligation.NETWORK_COPYLEFT


# --- findings and gaps ---------------------------------------------------


def a_finding(**overrides):
    defaults = dict(
        kind=FindingKind.VULNERABLE_DEPENDENCY, severity=Severity.HIGH,
        subject="pkg:npm/lodash@4.17.20", title="Prototype pollution",
        code="CVE-2020-8203", evidence=(LOCKFILE,),
    )
    return Finding(**{**defaults, **overrides})


def test_a_finding_cannot_be_raised_without_evidence():
    with pytest.raises(EvidenceRequired):
        a_finding(evidence=())


def test_the_same_condition_rediscovered_is_the_same_finding():
    # This is what makes "new since baseline" and suppression work at all.
    assert a_finding().id == a_finding(severity=Severity.LOW, evidence=(IMPORT,)).id
    assert a_finding().id != a_finding(code="CVE-2021-23337").id


def test_a_waiver_records_its_reason_and_never_erases_the_finding():
    waived = a_finding().suppress("not reachable from any entry point")

    assert waived.suppressed and waived.suppression_reason
    assert not waived.blocks_release
    assert waived.risk is RiskClass.NONE
    # The finding itself is still there, in the ledger and in the report.
    assert waived.id == a_finding().id


def test_a_waiver_without_a_reason_is_refused():
    with pytest.raises(ValueError):
        a_finding().suppress("")


def test_findings_rank_by_severity_then_by_how_much_they_would_disturb():
    small = a_finding(code="A", remediation=Remediation("bump", blast_radius=2))
    large = a_finding(code="B", remediation=Remediation("bump", blast_radius=90))
    minor = a_finding(code="C", severity=Severity.LOW)
    waived = a_finding(code="D", severity=Severity.CRITICAL).suppress("accepted")

    assert [f.code for f in rank([minor, small, waived, large])] == ["B", "A", "C", "D"]


def test_a_node_inherits_the_worst_unwaived_finding():
    findings = [
        a_finding(code="A", severity=Severity.LOW),
        a_finding(code="B", severity=Severity.CRITICAL).suppress("accepted"),
        a_finding(code="C", severity=Severity.MEDIUM),
    ]
    assert aggregate_risk(findings) is RiskClass.MODERATE
    assert aggregate_risk([]) is RiskClass.NONE


def test_reconciliation_findings_are_recognisable_as_a_family():
    assert FindingKind.DECLARED_NOT_FOUND.is_reconciliation
    assert FindingKind.UNDECLARED_ELEMENT.family == "reconciliation"
    assert not FindingKind.VULNERABLE_DEPENDENCY.is_reconciliation


def test_a_gap_the_operator_can_close_is_distinguished_from_a_platform_limit():
    refused = Gap(GapKind.CAPABILITY_REFUSED, "payments-repo", "static-analysis refused",
                  remediation="grant read access", confidence_impact=0.3)
    missing_analyser = Gap(GapKind.PLUGIN_UNAVAILABLE, "svc/rust", "no Rust analyser",
                           confidence_impact=0.15)

    assert refused.actionable
    assert not missing_analyser.actionable


def test_merging_gaps_keeps_the_worst_impact_for_each_distinct_gap():
    light = Gap(GapKind.LOW_CONFIDENCE, "n1", "weak path", confidence_impact=0.1)
    heavy = Gap(GapKind.LOW_CONFIDENCE, "n1", "weak path", confidence_impact=0.4)
    other = Gap(GapKind.NOT_ATTACHED, "n2", "never registered", confidence_impact=0.2)

    merged = merge_gaps([light, other], [heavy])
    assert len(merged) == 2
    assert merged[0].confidence_impact == 0.4


# --- reasoning -----------------------------------------------------------


def a_path():
    return ReasoningPath(question="what breaks if lodash 5 lands?").then(
        ReasoningStep("lodash 4.17.20 is pinned", layer="L1", evidence=(LOCKFILE,),
                      confidence=1.0, operation="resolve")
    ).then(
        ReasoningStep("src/a.js imports lodash", layer="L4", evidence=(IMPORT,),
                      confidence=0.9, operation="traverse")
    )


def test_a_chain_is_only_as_strong_as_its_weakest_step():
    path = a_path()
    # Averaging would let one weak hop hide behind a lockfile pin.
    assert path.confidence == 0.9
    assert path.weakest.layer == "L4"


def test_an_explanation_terminates_in_files_and_line_numbers():
    path = a_path()

    assert path.grounded
    assert path.sources == ("package-lock.json:1204", "a.js:3")
    assert "package-lock.json:1204" in path.render()


def test_a_step_that_cites_nothing_makes_the_whole_path_ungrounded():
    path = a_path().then(ReasoningStep("therefore it is safe", layer="L10"))
    assert not path.grounded


def test_an_empty_path_is_not_grounded_and_carries_no_confidence():
    empty = ReasoningPath()
    assert not empty.grounded
    assert empty.confidence == 0.0


def test_guidance_is_discounted_by_what_it_could_not_see():
    gap = Gap(GapKind.CAPABILITY_REFUSED, "payments-repo", "refused",
              remediation="grant read access", confidence_impact=0.3)
    complete = Guidance(answer=["src/a.js"], reasoning=a_path())
    limited = Guidance(answer=["src/a.js"], reasoning=a_path(), gaps=(gap,))

    assert complete.complete and complete.confidence == 0.9
    assert not limited.complete
    assert limited.confidence == pytest.approx(0.63)
    assert limited.actionable_gaps == (gap,)


def test_next_questions_are_ranked_by_what_answering_them_would_buy():
    ranked = rank_questions([
        Question("which callers use _.merge?", information_gain=0.4),
        Question("is 4.17.21 safe?", information_gain=0.8),
        Question("who owns this package?", information_gain=0.1),
    ], limit=2)

    assert [q.text for q in ranked] == ["is 4.17.21 safe?", "which callers use _.merge?"]


def test_a_conclusion_traces_back_through_enrichments_to_raw_evidence():
    risk = Enrichment(subject="n1", attribute="risk", value="high", layer="L9",
                      derived_from=(LOCKFILE.id,), confidence=0.9)
    priority = Enrichment(subject="n1", attribute="priority", value=1, layer="L10",
                          derived_from=(risk.id,))

    traced = trace_to_evidence(
        priority, enrichments={risk.id: risk}, evidence={LOCKFILE.id: LOCKFILE},
    )
    assert traced == (LOCKFILE,)


def test_an_enrichment_asserted_out_of_nowhere_is_not_grounded():
    assert not Enrichment(subject="n1", attribute="x", value=1, layer="L5").grounded


def test_an_ambiguous_question_is_answered_with_a_question_rather_than_a_guess():
    clarification = Clarification(
        "which payments did you mean?",
        options=(
            Option("the payments service", "urn:slpie:service:payments/api", kind="service"),
            Option("the payments package", "pkg:npm/payments", kind="package"),
        ),
        original_question="what breaks in payments?",
    )

    payload = clarification.to_dict()
    assert payload["type"] == "clarification"
    assert len(payload["options"]) == 2
    assert payload["original_question"] == "what breaks in payments?"
