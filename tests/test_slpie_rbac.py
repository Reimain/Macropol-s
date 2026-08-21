"""RBAC: the decisions, the refusals, and the access review that finds the holes.

The tests that matter most here are the ones that would pass on a naive
implementation and should not: deny beating a later broader allow, a separation
rule that cannot be evaded by adding a level of inheritance, an unregistered
condition denying rather than being skipped, and a resource wildcard that must
not cross a path separator.
"""

from __future__ import annotations

import time

import pytest

from slpie.identity import Assurance, AuthMethod, Principal, PrincipalKind
from slpie.rbac import (
    MAX_ELEVATION,
    AccessEngine,
    Effect,
    Kind,
    Outcome,
    Permission,
    PolicyError,
    RbacError,
    Role,
    RoleGraph,
    Scope,
    Separation,
    Severity,
    allow,
    deny,
    dump_policy,
    load_policy,
    matches_action,
    matches_resource,
    parse_rule,
    review,
    system_roles,
)

DAY = 86400.0


def human(subject="ada", *, assurance=Assurance.STANDARD, **settings) -> Principal:
    return Principal(
        issuer="https://acme.okta.com", subject=subject,
        method=AuthMethod.OIDC, assurance=assurance, **settings,
    )


# --- resource and action matching ----------------------------------------


@pytest.mark.parametrize("pattern,resource", [
    ("*", "repo:acme/payments"),
    ("repo:acme/payments", "repo:acme/payments"),
    ("repo:acme/*", "repo:acme/payments"),
    ("repo:acme/**", "repo:acme/payments/deep/tree"),
    ("repo:**", "repo:anything/at/all"),
    ("repo:", "repo:acme/payments"),
])
def test_a_pattern_covers_the_resources_it_should(pattern, resource):
    assert matches_resource(pattern, resource)


@pytest.mark.parametrize("pattern,resource", [
    ("repo:acme/*", "repo:acme/payments/nested"),
    ("repo:acme", "repo:acme/payments"),
    ("repo:acme/payments", "repo:acme/billing"),
    ("db:acme/*", "repo:acme/payments"),
])
def test_a_pattern_does_not_cover_what_it_should_not(pattern, resource):
    assert not matches_resource(pattern, resource)


def test_a_single_wildcard_never_crosses_a_path_separator():
    """`repo:acme/*` means one segment, not "anything below acme"."""
    assert matches_resource("repo:acme/*", "repo:acme/payments")
    assert not matches_resource("repo:acme/*", "repo:acme/payments/src")


@pytest.mark.parametrize("pattern,action,expected", [
    ("*", "graph.read", True),
    ("graph.read", "graph.read", True),
    ("graph.*", "graph.read", True),
    ("graph.*", "graphql.read", False),
    ("graph.read", "graph.write", False),
])
def test_action_patterns_match_by_prefix_not_by_substring(pattern, action, expected):
    assert matches_action(pattern, action) is expected


def test_a_permission_with_an_empty_resource_is_refused():
    with pytest.raises(RbacError, match="write '\\*' if you genuinely mean"):
        Permission(action="graph.read", resource="")


# --- scopes ---------------------------------------------------------------


def test_a_global_scope_reaches_everything():
    assert Scope.GLOBAL.contains(Scope(tenant="acme", realm="prod"))


def test_a_tenant_reaches_its_realms_but_a_realm_does_not_reach_its_tenant():
    tenant = Scope(tenant="acme")
    realm = Scope(tenant="acme", realm="prod")
    assert tenant.contains(realm)
    assert not realm.contains(tenant)


def test_one_tenant_never_reaches_another():
    assert not Scope(tenant="acme").contains(Scope(tenant="other"))


# --- the role graph -------------------------------------------------------


def test_an_empty_role_is_refused():
    with pytest.raises(RbacError, match="grants nothing and inherits nothing"):
        Role(name="ghost")


def test_a_role_cannot_inherit_from_itself():
    with pytest.raises(RbacError, match="cannot inherit from itself"):
        Role(name="a", inherits=("a",), permissions=(allow("x"),))


def test_an_inheritance_cycle_is_refused_with_the_cycle_printed():
    with pytest.raises(RbacError, match="inheritance cycle"):
        RoleGraph([
            Role(name="a", inherits=("b",), permissions=(allow("x"),)),
            Role(name="b", inherits=("a",), permissions=(allow("y"),)),
        ])


def test_a_dangling_parent_is_refused():
    with pytest.raises(RbacError, match="inherits undefined role"):
        RoleGraph([Role(name="a", inherits=("nowhere",), permissions=(allow("x"),))])


def test_inheritance_deeper_than_the_bound_is_refused():
    roles = [Role(name="r0", permissions=(allow("x"),))]
    for index in range(1, 12):
        roles.append(Role(name=f"r{index}", inherits=(f"r{index - 1}",),
                          permissions=(allow(f"a{index}"),)))
    with pytest.raises(RbacError, match="levels deep"):
        RoleGraph(roles)


def test_effective_permissions_include_inherited_ones():
    graph = RoleGraph([
        Role(name="base", permissions=(allow("graph.read"),)),
        Role(name="derived", inherits=("base",), permissions=(allow("scan.run"),)),
    ])
    actions = {p.action for p in graph.effective_permissions("derived")}
    assert actions == {"graph.read", "scan.run"}


def test_a_system_role_cannot_be_redefined():
    graph = system_roles()
    with pytest.raises(RbacError, match="cannot be redefined"):
        graph.replace(Role(name="viewer", permissions=(allow("*", "*"),)))


def test_a_separation_naming_one_role_is_refused():
    with pytest.raises(RbacError, match="nothing to separate"):
        Separation(name="x", roles=frozenset({"a"}), reason="because")


def test_a_separation_without_a_reason_is_refused():
    with pytest.raises(RbacError, match="unexplained control"):
        Separation(name="x", roles=frozenset({"a", "b"}), reason="")


def test_a_separation_cannot_be_evaded_by_adding_a_level_of_inheritance():
    """The check that a naive implementation gets wrong."""
    graph = RoleGraph([
        Role(name="operator", permissions=(allow("deploy.apply"),)),
        Role(name="approver", permissions=(allow("change.approve"),)),
        Role(name="lead", inherits=("approver",), permissions=(allow("team.read"),)),
    ], separations=[
        Separation(name="sod", roles=frozenset({"operator", "approver"}),
                   reason="requester must not approve"),
    ])
    conflicts = graph.conflicts({"operator", "lead"})
    assert conflicts and conflicts[0][1] == frozenset({"operator", "approver"})


# --- decisions ------------------------------------------------------------


@pytest.fixture
def engine():
    return AccessEngine(system_roles())


def test_a_principal_with_no_role_is_denied_and_told_so(engine):
    decision = engine.check(human(), "graph.read")
    assert decision.outcome is Outcome.DENIED_NO_RULE
    assert "holds no role" in decision.reason


def test_a_granted_action_is_allowed_and_names_the_rule(engine):
    ada = human()
    engine.bind(ada.urn, "viewer")
    decision = engine.check(ada, "graph.read", "repo:acme/payments")
    assert decision.allowed
    assert decision.role == "viewer"
    assert "graph.read" in decision.reason


def test_an_action_nobody_granted_is_denied_by_default(engine):
    ada = human()
    engine.bind(ada.urn, "viewer")
    decision = engine.check(ada, "deploy.apply", "env:prod")
    assert decision.outcome is Outcome.DENIED_NO_RULE
    assert "no rule in viewer grants" in decision.reason


def test_an_explicit_deny_beats_a_broader_allow_added_later():
    """The property that specificity-ordered engines get wrong."""
    graph = RoleGraph([
        Role(name="contractor",
             permissions=(deny("deploy.apply", "env:prod/**"),)),
        Role(name="everything", permissions=(allow("*", "*"),)),
    ])
    engine = AccessEngine(graph)
    ada = human()
    engine.bind(ada.urn, "contractor")
    engine.bind(ada.urn, "everything")

    decision = engine.check(ada, "deploy.apply", "env:prod/payments")
    assert decision.outcome is Outcome.DENIED_BY_RULE
    assert decision.role == "contractor"
    assert "refused by rule" in decision.reason


def test_a_denial_explains_itself_well_enough_to_paste_into_a_ticket(engine):
    ada = human()
    engine.bind(ada.urn, "viewer")
    text = engine.check(ada, "deploy.apply", "env:prod").explain()
    assert text.startswith("denied: deploy.apply on env:prod")
    assert "viewer" in text


def test_an_expired_session_is_denied_before_any_rule_is_read(engine):
    stale = human(expires_at=100.0)
    engine.bind(stale.urn, "administrator")
    decision = engine.check(stale, "graph.read", now=200.0)
    assert decision.outcome is Outcome.DENIED_EXPIRED
    assert decision.obligation == "sign in again"


def test_too_weak_a_sign_in_is_denied_with_a_step_up_obligation(engine):
    weak = human(assurance=Assurance.WEAK)
    engine.bind(weak.urn, "operator")
    decision = engine.check(weak, "connector.grant", "connector:github")
    assert decision.outcome is Outcome.DENIED_ASSURANCE
    assert decision.obligation == "re-authenticate at STRONG or above"
    assert decision.outcome.recoverable


def test_the_same_request_succeeds_once_the_assurance_is_raised(engine):
    strong = human(assurance=Assurance.STRONG)
    engine.bind(strong.urn, "operator")
    assert engine.check(strong, "connector.grant", "connector:github").allowed


def test_require_raises_with_the_explanation_in_the_message(engine):
    ada = human()
    engine.bind(ada.urn, "viewer")
    with pytest.raises(RbacError, match="denied: deploy.apply"):
        engine.require(ada, "deploy.apply", "env:prod")


def test_scope_confines_a_role_to_its_tenant(engine):
    ada = human()
    engine.bind(ada.urn, "viewer", scope=Scope(tenant="acme"))
    assert engine.check(ada, "graph.read", scope=Scope(tenant="acme")).allowed
    assert not engine.check(ada, "graph.read", scope=Scope(tenant="other")).allowed


def test_a_tenant_binding_reaches_a_realm_inside_it(engine):
    ada = human()
    engine.bind(ada.urn, "viewer", scope=Scope(tenant="acme"))
    assert engine.check(
        ada, "graph.read", scope=Scope(tenant="acme", realm="prod")
    ).allowed


def test_the_engine_can_list_everything_a_principal_may_do(engine):
    ada = human()
    engine.bind(ada.urn, "analyst")
    permitted = engine.permitted(ada, "repo:acme/payments")
    assert "scan.run" in permitted
    assert "graph.read" in permitted
    assert "deploy.apply" not in permitted


def test_an_approver_is_deliberately_unable_to_deploy(engine):
    """The separation is in the role definition, not only in the policy."""
    ada = human(assurance=Assurance.STRONG)
    engine.bind(ada.urn, "approver")
    assert engine.check(ada, "change.approve", "change:1234").allowed
    assert not engine.check(ada, "deploy.apply", "env:prod").allowed


# --- separation of duties at assignment ----------------------------------


def test_assigning_a_conflicting_role_is_refused_at_the_moment_it_happens(engine):
    ada = human(assurance=Assurance.STRONG)
    engine.bind(ada.urn, "operator")
    with pytest.raises(RbacError, match="change-approval forbids"):
        engine.bind(ada.urn, "approver")


def test_the_refusal_quotes_the_reason_the_control_exists(engine):
    ada = human()
    engine.bind(ada.urn, "operator")
    with pytest.raises(RbacError, match="must not be able to approve"):
        engine.bind(ada.urn, "approver")


# --- elevation ------------------------------------------------------------


def test_an_elevation_must_state_why(engine):
    with pytest.raises(RbacError, match="must state why"):
        engine.bind(human().urn, "administrator", elevation=True, expires_at=1.0)


def test_an_elevation_must_expire(engine):
    with pytest.raises(RbacError, match="must expire"):
        engine.bind(human().urn, "administrator", elevation=True, reason="incident")


def test_an_elevation_cannot_outlast_the_cap(engine):
    with pytest.raises(RbacError, match="may not exceed"):
        engine.elevate(human().urn, "administrator", reason="incident",
                       seconds=MAX_ELEVATION + 1, now=0.0)


def test_an_elevation_grants_access_and_then_stops(engine):
    ada = human(assurance=Assurance.STRONG)
    engine.elevate(ada.urn, "administrator", reason="SEV-1", seconds=3600, now=0.0)
    assert engine.check(ada, "access.review", now=100.0).allowed
    assert not engine.check(ada, "access.review", now=7200.0).allowed


# --- custom conditions ----------------------------------------------------


def test_a_condition_plugs_in_by_name_without_touching_the_kernel():
    graph = RoleGraph([
        Role(name="oncall",
             permissions=(allow("deploy.apply", "env:prod", condition="business-hours"),)),
    ])
    engine = AccessEngine(graph)
    engine.register_condition(
        "business-hours", lambda principal, resource, context: context.get("hour", 0) < 18
    )
    ada = human()
    engine.bind(ada.urn, "oncall")

    assert engine.check(ada, "deploy.apply", "env:prod", context={"hour": 10}).allowed
    refused = engine.check(ada, "deploy.apply", "env:prod", context={"hour": 22})
    assert refused.outcome is Outcome.DENIED_CONDITION


def test_an_unregistered_condition_denies_rather_than_being_skipped():
    """A typo in a policy file must not silently widen access."""
    graph = RoleGraph([
        Role(name="oncall",
             permissions=(allow("deploy.apply", "env:prod", condition="busines-hours"),)),
    ])
    engine = AccessEngine(graph)
    ada = human()
    engine.bind(ada.urn, "oncall")
    decision = engine.check(ada, "deploy.apply", "env:prod")
    assert decision.outcome is Outcome.DENIED_CONDITION
    assert "not registered" in decision.reason


def test_a_condition_that_raises_denies_rather_than_assuming():
    def broken(principal, resource, context):
        raise RuntimeError("the directory is down")

    graph = RoleGraph([
        Role(name="oncall",
             permissions=(allow("deploy.apply", "env:prod", condition="check"),)),
    ])
    engine = AccessEngine(graph, conditions={"check": broken})
    ada = human()
    engine.bind(ada.urn, "oncall")
    decision = engine.check(ada, "deploy.apply", "env:prod")
    assert decision.outcome is Outcome.DENIED_CONDITION
    assert "refusing rather than assuming" in decision.reason


def test_registering_a_condition_twice_is_refused(engine):
    engine.register_condition("x", lambda p, r, c: True)
    with pytest.raises(RbacError, match="already registered"):
        engine.register_condition("x", lambda p, r, c: False)


# --- the audit trail ------------------------------------------------------


def test_denials_are_recorded_and_allows_are_not_by_default(engine):
    ada = human()
    engine.bind(ada.urn, "viewer")
    engine.check(ada, "graph.read")
    engine.check(ada, "deploy.apply")
    assert len(engine.decisions) == 1
    assert not engine.decisions[0].allowed


def test_allows_can_be_recorded_when_asked_for():
    engine = AccessEngine(system_roles(), record_allows=True)
    ada = human()
    engine.bind(ada.urn, "viewer")
    engine.check(ada, "graph.read")
    assert len(engine.decisions) == 1


def test_revoking_supersedes_rather_than_deletes(engine):
    ada = human()
    engine.bind(ada.urn, "viewer")
    engine.unbind(ada.urn, "viewer", revoked_by="admin")
    assert engine.roles_of(ada.urn) == frozenset()
    assert len(engine.history(ada.urn, "viewer")) == 2


def test_the_engine_can_be_asked_who_held_what_at_any_point(engine):
    ada = human()
    engine.bind(ada.urn, "viewer")
    after_grant = engine.status()["events"]
    engine.unbind(ada.urn, "viewer")

    holders = engine.at(after_grant)
    assert holders[(ada.urn, "viewer", "*")].active
    assert engine.roles_of(ada.urn) == frozenset()


def test_every_decision_can_be_emitted_to_a_ledger():
    seen: list[str] = []
    engine = AccessEngine(system_roles(), emit=lambda d: seen.append(d.outcome.value))
    ada = human()
    engine.bind(ada.urn, "viewer")
    engine.check(ada, "graph.read")
    engine.check(ada, "deploy.apply")
    assert seen == ["allowed", "denied_no_rule"]


# --- policy files ---------------------------------------------------------


def test_a_permission_line_parses():
    permission = parse_rule('allow graph.read on "repo:acme/*"')
    assert permission.effect is Effect.ALLOW
    assert permission.resource == "repo:acme/*"


def test_a_permission_line_carries_a_condition_and_an_assurance():
    permission = parse_rule(
        'deny deploy.apply on "env:prod" if business-hours requiring STRONG'
    )
    assert permission.effect is Effect.DENY
    assert permission.condition == "business-hours"
    assert permission.assurance is Assurance.STRONG


def test_an_unreadable_permission_line_quotes_the_line():
    with pytest.raises(PolicyError, match="maybe graph.read"):
        parse_rule("maybe graph.read")


def test_an_unknown_assurance_lists_the_valid_ones():
    with pytest.raises(PolicyError, match="expected one of"):
        parse_rule('allow x on "*" requiring VERY_STRONG')


POLICY = {
    "apiVersion": "slpie/v1",
    "kind": "AccessPolicy",
    "roles": {
        "payments-operator": {
            "description": "run payments deployments",
            "inherits": ["analyst"],
            "assurance": "STRONG",
            "permissions": [
                'allow deploy.apply on "env:prod/payments"',
                'deny deploy.destroy on "*"',
            ],
        },
    },
    "separations": [
        {"name": "payments-sod",
         "roles": ["payments-operator", "approver"],
         "reason": "the deployer must not approve their own change"},
    ],
}


def test_a_customer_role_layers_on_top_of_the_shipped_ones():
    graph = load_policy(POLICY, base=system_roles())
    assert "payments-operator" in graph
    actions = {p.action for p in graph.effective_permissions("payments-operator")}
    assert "graph.read" in actions          # inherited from analyst → viewer
    assert "deploy.apply" in actions


def test_a_customer_file_cannot_redefine_a_system_role():
    document = {"kind": "AccessPolicy",
                "roles": {"viewer": {"permissions": ['allow "*" on "*"']}}}
    with pytest.raises(PolicyError, match="cannot be redefined"):
        load_policy(document, base=system_roles())


def test_a_policy_naming_the_wrong_kind_is_refused():
    with pytest.raises(PolicyError, match="expected kind: AccessPolicy"):
        load_policy({"kind": "Deployment"})


def test_a_policy_error_names_the_role_it_came_from():
    document = {"kind": "AccessPolicy",
                "roles": {"broken": {"permissions": ["nonsense here"]}}}
    with pytest.raises(PolicyError, match="role 'broken'"):
        load_policy(document)


def test_a_policy_file_round_trips_through_text(tmp_path):
    from slpie.rbac import load_file

    graph = load_policy(POLICY, base=system_roles())
    path = tmp_path / "slpie.rbac.yaml"
    path.write_text(dump_policy(graph), encoding="utf-8")

    reloaded = load_file(path)
    assert set(reloaded.names()) == set(graph.names())
    assert {s.name for s in reloaded.separations} == {s.name for s in graph.separations}


def test_the_shipped_model_is_small_enough_to_read():
    graph = system_roles()
    assert len(graph) == 5
    assert {s.name for s in graph.separations} == {"change-approval", "access-self-grant"}


# --- the access review ----------------------------------------------------


def test_a_clean_model_reports_no_exceptions():
    engine = AccessEngine(RoleGraph([
        Role(name="viewer", permissions=(allow("graph.read", "repo:acme/payments"),)),
    ]))
    ada = human()
    engine.bind(ada.urn, "viewer")
    report = review(engine, known_principals=[ada.urn])
    assert report.clean


def test_the_review_finds_a_wildcard_grant():
    engine = AccessEngine(RoleGraph([
        Role(name="god", permissions=(allow("*", "*"),)),
    ]))
    report = review(engine)
    findings = report.of_kind(Kind.WILDCARD_GRANT)
    assert findings and findings[0].severity is Severity.HIGH
    assert findings[0].remediation


def test_the_review_finds_an_emergency_that_keeps_happening(engine):
    """Three break-glasses to one role is a standing need wearing a costume."""
    ada = human(assurance=Assurance.STRONG)
    for day in range(3):
        engine.elevate(ada.urn, "administrator", reason=f"SEV-1 #{day}",
                       seconds=3600, now=day * DAY)
    findings = review(engine, now=3 * DAY).of_kind(Kind.REPEATED_ELEVATION)
    assert findings and findings[0].severity is Severity.HIGH
    assert "standing role" in findings[0].remediation


def test_a_single_elevation_is_not_reported(engine):
    ada = human(assurance=Assurance.STRONG)
    engine.elevate(ada.urn, "administrator", reason="SEV-1", seconds=3600, now=0.0)
    assert not review(engine, now=3 * DAY).of_kind(Kind.REPEATED_ELEVATION)


def test_the_review_finds_a_separation_violation_that_predates_the_rule():
    """Bindings made before a separation was introduced must still be caught."""
    graph = RoleGraph([
        Role(name="operator", permissions=(allow("deploy.apply"),)),
        Role(name="approver", permissions=(allow("change.approve"),)),
    ])
    engine = AccessEngine(graph)
    ada = human()
    engine.bind(ada.urn, "operator")
    engine.bind(ada.urn, "approver")

    graph.separate(Separation(name="sod", roles=frozenset({"operator", "approver"}),
                              reason="requester must not approve"))
    report = review(engine)
    findings = report.of_kind(Kind.SEPARATION_VIOLATED)
    assert findings and findings[0].severity is Severity.CRITICAL
    assert "requester must not approve" in " ".join(findings[0].evidence)


def test_the_review_finds_production_reachable_from_a_weak_sign_in():
    engine = AccessEngine(RoleGraph([
        Role(name="deployer", assurance=Assurance.WEAK, permissions=(
            Permission(action="deploy.apply", resource="env:prod",
                       assurance=Assurance.WEAK),
        )),
    ]))
    findings = review(engine).of_kind(Kind.WEAK_ASSURANCE_HIGH_PRIVILEGE)
    assert findings and findings[0].severity is Severity.HIGH


def test_the_review_finds_a_binding_that_lapsed_but_was_never_revoked(engine):
    ada = human()
    engine.bind(ada.urn, "viewer", expires_at=100.0, now=0.0)
    findings = review(engine, now=10 * DAY).of_kind(Kind.EXPIRED_NOT_REVOKED)
    assert findings


def test_the_review_finds_a_role_held_by_nobody_in_the_directory(engine):
    engine.bind("urn:slpie:principal:human/ghost", "viewer")
    findings = review(engine, known_principals=["urn:slpie:principal:human/real"])
    assert findings.of_kind(Kind.ORPHANED_BINDING)


def test_no_orphan_is_reported_when_no_directory_was_supplied(engine):
    engine.bind("urn:slpie:principal:human/ghost", "viewer")
    assert not review(engine).of_kind(Kind.ORPHANED_BINDING)


def test_unused_roles_are_not_guessed_at_when_nothing_was_measured(engine):
    ada = human()
    engine.bind(ada.urn, "viewer", now=0.0)
    assert not review(engine, now=200 * DAY).of_kind(Kind.UNUSED_ROLE)


def test_a_role_granted_long_ago_and_never_exercised_is_reported(engine):
    ada = human()
    engine.bind(ada.urn, "viewer", now=0.0)
    findings = review(engine, exercised={}, now=200 * DAY).of_kind(Kind.UNUSED_ROLE)
    assert findings and findings[0].severity is Severity.LOW


def test_findings_are_ranked_with_the_worst_first():
    engine = AccessEngine(system_roles())
    ada = human(assurance=Assurance.STRONG)
    for day in range(3):
        engine.elevate(ada.urn, "administrator", reason="SEV-1", seconds=3600,
                       now=day * DAY)
    report = review(engine, now=3 * DAY)
    ranks = [finding.severity.rank for finding in report.findings]
    assert ranks == sorted(ranks)


def test_the_review_renders_as_text_for_a_ticket():
    engine = AccessEngine(RoleGraph([Role(name="god", permissions=(allow("*", "*"),))]))
    text = review(engine).render()
    assert "access review" in text
    assert "wildcard" in text.lower()


def test_a_compliance_gate_can_ask_only_for_what_should_block_it():
    engine = AccessEngine(RoleGraph([Role(name="god", permissions=(allow("*", "*"),))]))
    report = review(engine)
    assert report.blocking
    assert all(f.severity in (Severity.CRITICAL, Severity.HIGH) for f in report.blocking)


# --- the headline ---------------------------------------------------------


def test_the_shipped_model_survives_its_own_access_review():
    """The model we ship must not fail the audit we ship with it."""
    engine = AccessEngine(system_roles())
    ada = human(assurance=Assurance.STRONG)
    engine.bind(ada.urn, "analyst")

    report = review(engine, known_principals=[ada.urn])
    blocking = [f for f in report.blocking if f.kind is not Kind.WILDCARD_GRANT]
    assert not blocking, report.render()


def test_a_customer_extends_the_model_by_file_and_the_engine_enforces_it():
    """The maintenance story, end to end: config in, enforced decisions out."""
    graph = load_policy(POLICY, base=system_roles())
    engine = AccessEngine(graph)

    ada = human("ada", assurance=Assurance.STRONG)
    engine.bind(ada.urn, "payments-operator")

    assert engine.check(ada, "deploy.apply", "env:prod/payments").allowed
    assert not engine.check(ada, "deploy.apply", "env:prod/billing").allowed
    assert engine.check(ada, "deploy.destroy", "env:prod/payments").outcome \
        is Outcome.DENIED_BY_RULE

    # And the separation declared in that same file is enforced at assignment.
    with pytest.raises(RbacError, match="payments-sod"):
        engine.bind(ada.urn, "approver")
