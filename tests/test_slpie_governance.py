"""Governance — many findings, never one verdict, and never a silent skip.

The themes, in the order they matter:

**A check that did not run must not read as a check that passed.** This is the
one that governs the whole design. A rule lacking the data it needs declines, an
exception makes it abstain, and both travel out as gaps — because a findings list
is read as "everything that is wrong", and neither state supports that reading.

**Every finding cites something.** `Finding` refuses to exist without evidence,
so a rule that accuses without citing cannot construct one. That is invariant 1
reaching all the way to the report.

**A rule's meaning cannot drift silently.** `source_digest` covers the rule's own
logic, not just its name, so a finding raised last month can be compared against
the rule as it stands today.

**Secrets never appear in the output.** A scanner that copied credentials into the
ledger, the API and every report would be a second place the secret leaks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slpie.compose import Composition, Context, Kind, registry
from slpie.domain.evidence import Evidence, EvidenceKind, SourceLocation
from slpie.domain.finding import Finding, FindingKind
from slpie.domain.lifecycle import Severity
from slpie.errors import GovernanceError
from slpie.governance.builtins import FAMILIES, builtins
from slpie.governance.rules import (
    Rule,
    RuleContext,
    RuleSet,
    cite,
    packages,
    register_rule_family,
    registered_rules,
)
from slpie.governance.security.secrets import redact, secret_rules, shannon
from slpie.governance.security.supplychain import edit_distance, supplychain_rules
from slpie.governance.view import view_of
from slpie.plugins.protocol import Observation

from _trees import EXAMPLE_AWS_KEY, write_npm


def evidence(uri: str, line: int = 1,
             kind: EvidenceKind = EvidenceKind.LOCKFILE_PIN) -> Evidence:
    return Evidence(
        kind=kind, location=SourceLocation(uri, line=line),
        extractor="test", excerpt=f"{uri}:{line}",
    )


def a_tree() -> list[Observation]:
    """One AGPL package, one unpinned range, one package with no licence."""
    return [
        Observation(
            kind="declares", subject="pkg:npm/lodahs@4.17.21",
            evidence=evidence("file:///r/package-lock.json", 3),
            properties={"license": "AGPL-3.0", "integrity": "sha512-aaa"},
        ),
        Observation(
            kind="declares", subject="pkg:npm/quiet@1.0.0",
            evidence=evidence("file:///r/package-lock.json", 4),
            properties={"integrity": "sha512-bbb"},
        ),
        Observation(
            kind="depends_on", subject="pkg:npm/app@1.0.0",
            object="pkg:npm/quiet",
            evidence=evidence("file:///r/package.json", 5,
                              EvidenceKind.MANIFEST_DECLARED),
            properties={"range": "*"},
        ),
    ]


@pytest.fixture()
def graph():
    with view_of(a_tree()) as built:
        yield built


# --- the governing property: a skipped check is never a pass ---------------


def test_a_rule_without_its_data_declines_rather_than_guessing(graph):
    """The licence rules need a distribution context and refuse to invent one."""
    evaluation = builtins().evaluate(RuleContext(graph=graph))

    assert evaluation.declined, "rules without their data declined"
    assert not any(
        finding.kind is FindingKind.LICENSE_INCOMPATIBLE
        for finding in evaluation
    ), "no compatibility verdict was reached without a stated context"


def test_supplying_the_context_makes_the_declining_rule_run(graph):
    evaluation = builtins().evaluate(RuleContext(
        graph=graph, facts={"distribution": "network_service"},
    ))

    assert any(
        finding.kind is FindingKind.LICENSE_INCOMPATIBLE for finding in evaluation
    ), "AGPL in a hosted service is the case this family exists for"


def test_agpl_is_fine_internally_and_not_over_a_network(graph):
    """The answer genuinely changes with the context, which is why it is required."""
    internal = builtins(only=("licenses",)).evaluate(RuleContext(
        graph=graph, facts={"distribution": "internal_only"},
    ))
    hosted = builtins(only=("licenses",)).evaluate(RuleContext(
        graph=graph, facts={"distribution": "network_service"},
    ))

    def incompatible(evaluation):
        return [f for f in evaluation if f.kind is FindingKind.LICENSE_INCOMPATIBLE]

    assert not incompatible(internal)
    assert incompatible(hosted)


def test_a_raising_rule_abstains_and_the_others_still_run(graph):
    def explode(_context: RuleContext) -> list[Finding]:
        raise RuntimeError("nope")

    broken = Rule(
        id="test.broken", title="always raises", kind=FindingKind.POLICY_VIOLATION,
        severity=Severity.HIGH, evaluate=explode, remediation="fix the rule",
    )
    ruleset = builtins()
    ruleset.add(broken)

    evaluation = ruleset.evaluate(RuleContext(
        graph=graph, facts={"distribution": "network_service"},
    ))

    assert evaluation.error_count == 1
    assert evaluation.errors[0].rule_id == "test.broken"
    assert len(evaluation) > 0, "one broken rule must not cost the other thirteen"


def test_a_rule_returning_something_that_is_not_a_finding_is_recorded(graph):
    def wrong(_context: RuleContext):
        return ["not a finding"]

    ruleset = RuleSet((Rule(
        id="test.wrong", title="returns junk", kind=FindingKind.POLICY_VIOLATION,
        severity=Severity.LOW, evaluate=wrong, remediation="return Findings",
    ),))
    evaluation = ruleset.evaluate(RuleContext(graph=graph))

    assert evaluation.error_count == 1
    assert "expected a Finding" in evaluation.errors[0].message


# --- the verb, and the gaps it carries -------------------------------------


@pytest.fixture()
def repository(tmp_path: Path) -> Path:
    write_npm(
        tmp_path, name="shop", license="MIT",
        declared={"lodahs": "^4.0.0", "quiet": "*"},
        resolved={
            "lodahs": {"version": "4.17.21", "license": "AGPL-3.0"},
            "quiet": {"version": "1.0.0"},
        },
    )
    (tmp_path / "settings.py").write_text(EXAMPLE_AWS_KEY, encoding="utf-8")
    (tmp_path / "popular.json").write_text(
        '{"npm": ["lodash", "react", "express"]}', encoding="utf-8",
    )
    return tmp_path


def run(pipeline: str, root: Path, verbs):
    return Composition.read(pipeline, verbs=verbs).run(Context(root=str(root)))


def test_govern_produces_findings_over_a_real_tree(repository, verbs):
    result = run(f"discover {repository} | govern", repository, verbs)

    assert result.ok
    assert result.flow.kind is Kind.FINDINGS
    assert result.flow.size > 0


def test_declined_rules_travel_as_a_gap_not_as_a_footnote(repository, verbs):
    """A clean list from checks that never ran is the wrong kind of clean."""
    result = run(f"discover {repository} | govern", repository, verbs)

    assert any(
        "declined" in gap.detail for gap in result.flow.gaps
    ), "the operator is told which checks did not run"
    assert result.flow.confidence < 1.0, "and the answer is discounted by it"


def test_an_abstaining_rule_becomes_a_gap_on_the_flow(repository, verbs, monkeypatch):
    from slpie.governance import builtins as builtins_module

    def explode(_context: RuleContext) -> list[Finding]:
        raise RuntimeError("the feed was unreadable")

    original = builtins_module.builtins

    def with_broken(**options):
        ruleset = original(**options)
        ruleset.add(Rule(
            id="test.exploding", title="raises", kind=FindingKind.POLICY_VIOLATION,
            severity=Severity.HIGH, evaluate=explode, remediation="fix it",
        ))
        return ruleset

    monkeypatch.setattr(builtins_module, "builtins", with_broken)
    result = run(f"discover {repository} | govern", repository, verbs)

    assert result.ok, "the composition still completes"
    assert any("test.exploding abstained" in gap.detail for gap in result.flow.gaps)


def test_govern_scans_the_tree_it_was_given_not_the_working_directory(
    repository, verbs, tmp_path,
):
    """The secret scan read the cwd before this was fixed.

    `discover /a/b | govern` was reporting credentials found in whatever
    directory the process happened to start in, attributed to /a/b — findings
    about a different codebase entirely, and they looked completely real.
    """
    elsewhere = tmp_path.parent / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    (elsewhere / "leak.py").write_text(
        'GITHUB_TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"\n',
        encoding="utf-8",
    )

    result = Composition.read(
        f"discover {repository} | govern", verbs=verbs,
    ).run(Context(root=str(elsewhere)))

    cited = [
        item.location.uri
        for finding in result.flow.items for item in finding.evidence
    ]
    assert not any("elsewhere" in uri for uri in cited), (
        "a finding was raised about a tree that was never governed"
    )
    assert any("settings.py" in uri for uri in cited), (
        "and the tree that was governed is the one that was read"
    )


def test_a_finding_survives_explain_with_its_file_and_line(repository, verbs):
    result = run(
        f"discover {repository} | govern --severity critical | explain",
        repository, verbs,
    )

    assert result.ok
    assert "explanation" in result.flow.facts
    assert "settings.py" in result.flow.facts["explanation"]


def test_govern_composes_into_the_shell_filters(repository, verbs):
    result = run(
        f"discover {repository} | govern | sort --field severity --desc | head --count 2",
        repository, verbs,
    )

    assert result.ok
    assert result.flow.size <= 2


def test_the_rules_verb_lists_what_this_build_checks(verbs):
    result = Composition.read("rules", verbs=verbs).run(Context())

    assert result.ok
    assert result.flow.size == len(builtins())
    assert "set digest" in result.flow.facts["rules"]


# --- each family --------------------------------------------------------


def test_a_transposed_name_is_one_edit_away():
    """Levenshtein scores a swap as 2, which misses the commonest typosquat."""
    assert edit_distance("lodahs", "lodash") == 1
    assert edit_distance("reqeusts", "requests") == 1
    assert edit_distance("abcd", "wxyz") > 2


def test_typosquats_are_reported_against_a_supplied_list(graph):
    evaluation = supplychain_rules().evaluate(RuleContext(
        graph=graph, facts={"popular_packages": {"npm": ["lodash"]}},
    ))

    squats = [f for f in evaluation if f.kind is FindingKind.TYPOSQUAT_SUSPECT]
    assert len(squats) == 1, "one package, judged once"
    assert "lodash" in squats[0].detail


def test_typosquat_declines_entirely_without_a_popularity_list(graph):
    evaluation = supplychain_rules().evaluate(RuleContext(graph=graph))

    assert not [f for f in evaluation if f.kind is FindingKind.TYPOSQUAT_SUSPECT]
    assert evaluation.declined >= 1, "declined, rather than inventing a list"


def test_the_popular_package_itself_is_not_reported_as_its_own_squat(graph):
    evaluation = supplychain_rules().evaluate(RuleContext(
        graph=graph, facts={"popular_packages": {"npm": ["lodahs", "lodash"]}},
    ))

    squats = [f for f in evaluation if f.kind is FindingKind.TYPOSQUAT_SUSPECT]
    assert not squats, "a package on the list is the real one"


def test_an_unpinned_range_is_reported(graph):
    evaluation = supplychain_rules().evaluate(RuleContext(graph=graph))

    unpinned = [f for f in evaluation if f.kind is FindingKind.UNPINNED_DEPENDENCY]
    assert unpinned
    assert unpinned[0].evidence, "and it cites the manifest that declared it"


def test_unmaintained_declines_without_a_supplied_clock(graph):
    """The same tree must not answer differently depending on the date."""
    evaluation = supplychain_rules().evaluate(RuleContext(
        graph=graph, facts={"last_released": {"pkg:npm/lodahs": 0}},
    ))

    assert not [
        f for f in evaluation if f.kind is FindingKind.UNMAINTAINED_PACKAGE
    ]


def test_unmaintained_reports_an_age_once_a_clock_is_given(graph):
    now = 1_800_000_000
    evaluation = supplychain_rules().evaluate(RuleContext(
        graph=graph, now=now,
        facts={"last_released": {"pkg:npm/lodahs": now - 86400 * 900}},
    ))

    stale = [f for f in evaluation if f.kind is FindingKind.UNMAINTAINED_PACKAGE]
    assert stale
    assert stale[0].properties["days_since_release"] == 900


def test_a_package_with_no_licence_is_reported_separately_from_an_unreadable_one(graph):
    from slpie.governance.security.licenses import license_rules

    evaluation = license_rules().evaluate(RuleContext(graph=graph))
    rules_that_fired = {finding.rule_id for finding in evaluation}

    assert "license.undeclared" in rules_that_fired
    assert "license.incompatible" not in rules_that_fired, "no context was supplied"


# --- secrets: found, and never echoed --------------------------------------


SOURCES = {
    "file:///r/app/settings.py": (
        'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n'
        'CLIENT_SECRET = "8f4Ka92LmQz7XpR3vNb1TcYw0EdHjUiO"\n'
        'DEBUG = True\n'
        'API_KEY = "your-api-key-here"\n'
    ),
    "file:///r/tests/fixtures.py": (
        'GITHUB_TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"\n'
    ),
}


def test_an_issuer_pattern_is_found_at_critical():
    evaluation = secret_rules().evaluate(RuleContext(sources=SOURCES))

    aws = [f for f in evaluation if f.properties.get("detector") == "aws-access-key-id"]
    assert aws
    assert aws[0].severity is Severity.CRITICAL


def test_the_secret_never_appears_anywhere_in_the_finding():
    """A scanner that echoes the credential is a second place it leaks."""
    evaluation = secret_rules().evaluate(RuleContext(sources=SOURCES))

    rendered = json.dumps([f.to_dict() for f in evaluation], default=str)
    for secret in (
        "AKIAIOSFODNN7EXAMPLE",
        "8f4Ka92LmQz7XpR3vNb1TcYw0EdHjUiO",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    ):
        assert secret not in rendered, "the finding carries the secret"
    assert "AKIA********" in rendered, "and it carries enough to recognise it"


def test_a_fixture_directory_lowers_the_severity_rather_than_hiding_the_hit():
    evaluation = secret_rules().evaluate(RuleContext(sources=SOURCES))

    fixtures = [f for f in evaluation if "fixtures.py" in f.subject]
    assert fixtures, "still reported"
    assert all(f.severity is Severity.LOW for f in fixtures), "at low severity"
    assert all(f.properties.get("allowed_because") for f in fixtures), (
        "and it says which allowance applied, so the silence is auditable"
    )


def test_an_allowance_matches_a_directory_name_and_not_a_prefix_of_one():
    """An over-broad allowance is worse than none, because it fails quietly.

    `"/test" in uri` matched `/test_probe0` — and would equally match a real
    `/testing-production-keys/`. A live credential there was silently downgraded
    to LOW and dropped straight out of any `--severity critical` release gate.
    """
    lookalikes = {
        "file:///r/testing-production-keys/prod.py": (
            'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n'
        ),
    }
    evaluation = secret_rules().evaluate(RuleContext(sources=lookalikes))

    assert evaluation, "the credential is still found"
    assert evaluation[0].severity is Severity.CRITICAL, (
        "a directory merely starting with 'test' is not a fixture directory"
    )
    assert not evaluation[0].properties.get("allowed_because")


def test_an_obvious_placeholder_is_not_reported():
    evaluation = secret_rules().evaluate(RuleContext(sources=SOURCES))

    assert not [f for f in evaluation if "your-api-key-here" in str(f.to_dict())]


def test_entropy_is_reported_as_the_heuristic_it_is():
    evaluation = secret_rules().evaluate(RuleContext(sources=SOURCES))

    guesses = [f for f in evaluation if f.rule_id == "secret.entropy"]
    assert guesses
    assert any("heuristic" in f.detail for f in guesses), (
        "a guess presented at the confidence of a match teaches people to "
        "ignore both"
    )


def test_redaction_keeps_the_length_and_drops_the_value():
    rendered = redact("supersecretvalue1234")
    assert rendered.startswith("supe")
    assert "secretvalue1234" not in rendered
    assert "20 chars" in rendered


def test_entropy_separates_a_key_from_a_sentence():
    assert shannon("8f4Ka92LmQz7XpR3vNb1TcYw0EdHjUiO") > 4.0
    assert shannon("the quick brown fox") < 4.0


# --- boundaries ------------------------------------------------------------


MANIFEST = """apiVersion: slpie/v1
environment: acme
target: simulated
security:
  boundaries:
    - name: cardholder-data
      contains: [payments]
      classification: pci-dss
codebase:
  - root: ./services/payments
"""


def boundary_context(observations, manifest_text: str = MANIFEST):
    from slpie.environment.loader import loads

    return loads(manifest_text), observations


def test_a_boundary_matching_nothing_is_reported_rather_than_passing_quietly():
    """The most dangerous clean report this family can produce."""
    from slpie.environment.loader import loads
    from slpie.governance.security.boundaries import boundary_rules

    with view_of(a_tree()) as built:
        evaluation = boundary_rules().evaluate(RuleContext(
            graph=built, manifest=loads(MANIFEST),
        ))

    empty = [f for f in evaluation if f.rule_id == "boundary.empty"]
    assert empty, "a boundary that cannot fail looks exactly like one that holds"
    assert empty[0].severity is Severity.HIGH
    assert empty[0].evidence, "and it cites the manifest that declared it"


def test_leaving_a_boundary_is_reported_with_the_edge_that_crosses():
    from slpie.environment.loader import loads
    from slpie.governance.security.boundaries import boundary_rules

    crossing = [
        Observation(
            kind="depends_on", subject="urn:slpie:service:payments/api",
            object="pkg:npm/analytics@1.0.0",
            evidence=evidence("file:///r/services/payments/package.json", 9,
                              EvidenceKind.MANIFEST_DECLARED),
        ),
    ]
    with view_of(crossing) as built:
        evaluation = boundary_rules().evaluate(RuleContext(
            graph=built, manifest=loads(MANIFEST),
        ))

    egress = [f for f in evaluation if f.rule_id == "boundary.egress"]
    assert egress, "payments reaching analytics leaves the cardholder zone"
    assert egress[0].evidence
    assert "pci-dss" in egress[0].detail


def test_boundaries_decline_entirely_without_a_manifest(graph):
    from slpie.governance.security.boundaries import boundary_rules

    evaluation = boundary_rules().evaluate(RuleContext(graph=graph))

    assert len(evaluation) == 0
    assert evaluation.declined == len(boundary_rules())


# --- the machinery ---------------------------------------------------------


def test_a_rule_without_a_remediation_is_refused_at_construction():
    with pytest.raises(GovernanceError, match="remediation"):
        Rule(
            id="test.silent", title="complains", kind=FindingKind.POLICY_VIOLATION,
            severity=Severity.LOW, evaluate=lambda _c: [],
        )


def test_a_duplicate_rule_id_is_refused_rather_than_shadowing():
    ruleset = builtins()
    existing = ruleset.rules[0]
    with pytest.raises(GovernanceError, match="already registered"):
        ruleset.add(existing)


def test_changing_a_rules_logic_changes_its_digest():
    """A finding raised last month must not be compared against a different rule."""
    def one(_context: RuleContext) -> list[Finding]:
        return []

    def two(_context: RuleContext) -> list[Finding]:
        return []          # same behaviour, different text

    def build(evaluate):
        return Rule(
            id="test.drift", title="t", kind=FindingKind.POLICY_VIOLATION,
            severity=Severity.LOW, evaluate=evaluate, remediation="r",
        )

    assert build(one).source_digest == build(one).source_digest
    assert build(one).source_digest != build(two).source_digest


def test_the_builtin_set_digest_is_stable_across_construction():
    assert builtins().digest == builtins().digest


def test_every_builtin_family_is_registered_unconditionally():
    ruleset = builtins()
    tags = {tag for rule in ruleset for tag in rule.tags}

    for family in FAMILIES:
        assert any(
            rule.id.startswith(family[:6]) or family[:6] in " ".join(rule.tags)
            for rule in ruleset
        ) or family in {"advisories"}, f"{family} is absent from the built-in set"
    assert "security" in tags


def test_families_register_through_the_path_a_plugin_uses():
    """Built-ins that took a private route would leave the seam untested."""
    from slpie.governance.builtins import register_builtins
    from slpie.plugins.registry import Registry

    registry_ = register_builtins(Registry())
    merged = registered_rules(registry_)

    assert len(merged) == len(builtins())


def test_one_package_is_judged_once(graph):
    """The graph holds a range node and a pin node for the same package."""
    seen = [node.coordinate for node in packages(RuleContext(graph=graph))]

    assert len(seen) == len(set(seen))


def test_the_judged_node_is_the_one_that_knows_the_most(graph):
    chosen = {
        node.coordinate: node for node in packages(RuleContext(graph=graph))
    }
    lodahs = chosen["pkg:npm/lodahs"]

    assert lodahs.version == "4.17.21", "the pin, not the bare coordinate"
    assert lodahs.properties.get("license") == "AGPL-3.0"


def test_cite_flattens_an_edges_evidence_rather_than_nesting_it():
    """`(edge.evidence,)` produced a tuple of tuples that broke at render time."""
    one = evidence("file:///r/a.json")
    assert cite((one,)) == (one,)
    assert cite(one) == (one,)
    assert cite(None, (one,)) == (one,)
    assert cite(None, ()) == ()
    assert all(isinstance(item, Evidence) for item in cite((one, one)))


def test_the_offline_view_holds_the_same_nodes_the_graph_would():
    """Rules cannot tell a scan from a database, because it is the same store."""
    with view_of(a_tree()) as built:
        found = built.nodes(live=True)

    assert found
    assert all(hasattr(node, "identity") and hasattr(node, "evidence")
               for node in found)


def test_an_evaluation_is_its_own_finding_list(graph):
    evaluation = builtins().evaluate(RuleContext(graph=graph))

    assert len(evaluation) == len(evaluation.findings)
    assert list(evaluation) == list(evaluation.findings)
    assert evaluation.by_severity()


def test_every_finding_cites_evidence(repository, verbs):
    """Invariant 1, reaching all the way to the report."""
    result = run(
        f"discover {repository} | govern --popular {repository}/popular.json "
        f"--distribution network_service",
        repository, verbs,
    )

    for finding in result.flow.items:
        assert finding.evidence, f"{finding.title} accuses without citing"
        assert all(item.location.uri for item in finding.evidence)


def test_findings_are_ranked_worst_first(repository, verbs):
    result = run(f"discover {repository} | govern", repository, verbs)
    ranks = [finding.severity.rank for finding in result.flow.items]

    assert ranks == sorted(ranks, reverse=True)


# --- what the screen reads ----------------------------------------------------


def test_govern_records_what_it_raised(repository, verbs, tmp_path):
    """The Findings screen reads a projection, and nothing was filling it.

    `RaiseFinding`, `FINDING_RAISED` and the findings projection have all
    existed since phase 2, and the command was dispatched by exactly one unit
    test — so an estate with twenty open findings showed an empty list, which
    reads as "nothing is wrong". That is the most expensive thing an empty
    state can say.
    """
    from slpie.core.queries import OpenFindings
    from slpie.engine import Engine

    engine = Engine.from_text(
        "apiVersion: slpie/v1\nenvironment: acme\ntarget: simulated\n"
        f"codebase:\n  - root: {repository}\n"
    )
    engine.declare()

    result = Composition.read(f"discover {repository} | govern", verbs=verbs).run(
        Context(root=str(repository), engine=engine))

    assert result.ok, result.error
    assert result.flow.facts["recorded"] == len(result.flow.items)
    on_screen = engine.queries.ask(OpenFindings()).value
    assert on_screen, "the projection the Findings screen reads is still empty"


def test_govern_without_an_engine_still_answers(repository, verbs):
    """`discover . | govern` from a directory with no environment is a
    legitimate way to use this verb, and it has nowhere to record."""
    result = run(f"discover {repository} | govern", repository, verbs)

    assert result.ok
    assert result.flow.facts["recorded"] == 0
    assert result.flow.items, "the findings themselves must still be produced"
