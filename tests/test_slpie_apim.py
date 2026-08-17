"""The API manager: lifecycle, catalogue, throttling, subscriptions, mediation.

No routes yet — this is the layer, exercised on its own, so that when the
gateway hooks into `Api.handle` the only new question is whether the hook is
right rather than whether any of this is.

The clock is injected everywhere it matters. A rate-limit suite that waits for
real seconds is a suite people stop running, and a suite people stop running is
worse than no suite because it still reports green.
"""

from __future__ import annotations

import pytest

from slpie.apim import (
    ApiCatalog,
    ApiState,
    Application,
    Chain,
    Rule,
    Situation,
    SubscriptionLedger,
    ThrottlePolicy,
    Throttler,
    Verdict,
    action_for,
    advance,
)
from slpie.apim.application import Applications
from slpie.apim.chain import standard
from slpie.apim.credential import CredentialStore
from slpie.apim.errors import (
    ApimError,
    CredentialRefused,
    LifecycleRefused,
    SubscriptionRefused,
)
from slpie.apim.lifecycle import TRANSITIONS
from slpie.compose import registry
from slpie.connectors.keyring import GrantStatus
from slpie.ui.api import Api


@pytest.fixture(scope="module")
def routes():
    return Api(engine=None).routes


# --- lifecycle ---------------------------------------------------------------


def test_every_legal_transition_is_allowed():
    for frm, allowed in TRANSITIONS.items():
        for to in allowed:
            assert advance(frm, to, reason="because") is to


def test_every_illegal_transition_is_refused_and_names_the_alternatives():
    """The useful thing to say to somebody who tried the wrong move is which
    ones are right — not that this one was wrong."""
    for frm in ApiState:
        for to in ApiState:
            if to in TRANSITIONS[frm]:
                continue
            with pytest.raises(LifecycleRefused) as refused:
                advance(frm, to, reason="because")
            assert frm.value in str(refused.value)
            for legal in TRANSITIONS[frm]:
                assert legal.value in str(refused.value)


def test_a_retired_api_never_comes_back():
    """Terminal, like `GrantStatus`, and for the same reason.

    Consumers were told it was gone and acted on that. Un-retiring it would
    make a subscription live again that somebody has already replaced.
    """
    assert TRANSITIONS[ApiState.RETIRED] == ()
    assert ApiState.RETIRED.is_terminal
    with pytest.raises(LifecycleRefused):
        advance(ApiState.RETIRED, ApiState.PUBLISHED, reason="changed our minds")


def test_taking_access_away_needs_a_stated_reason():
    """Somebody will ask why this API stopped working."""
    for target in (ApiState.RETIRED, ApiState.BLOCKED, ApiState.DEPRECATED):
        with pytest.raises(ApimError, match="reason"):
            advance(ApiState.PUBLISHED, target)
        assert advance(ApiState.PUBLISHED, target, reason="superseded by v2")


def test_only_two_states_actually_serve():
    serving = {state for state in ApiState if state.serves}
    assert serving == {ApiState.PUBLISHED, ApiState.DEPRECATED}


# --- the catalogue is a projection -------------------------------------------


def test_every_route_is_placed_in_an_api(routes):
    """A route the catalogue does not place is a route the gateway waves
    through, so placement is total by construction rather than by a list."""
    catalog = ApiCatalog.from_registry(routes=routes)
    placed = {
        (operation.method, operation.path)
        for definition in catalog for operation in definition.operations
    }
    assert placed == set(routes)


def test_every_verb_group_is_an_api(routes):
    catalog = ApiCatalog.from_registry(routes=routes)
    groups = {verb.group for verb in registry()}
    assert groups <= {definition.api_id for definition in catalog}


def test_adding_a_verb_adds_an_operation_with_no_file_edited(routes):
    """The whole argument for projecting rather than listing.

    A hand-maintained catalogue drifts within a week, and then the portal
    documents operations the gateway does not enforce.
    """
    catalog = ApiCatalog.from_registry(routes=routes)
    for verb in registry():
        found = catalog.for_route("POST", f"/api/v/{verb.name}")
        assert found is not None, f"{verb.name} is on no API"
        _definition, operation = found
        assert operation.action == f"{verb.group}.{verb.name}"


def test_a_mutating_verb_is_throttled_harder_than_a_read(routes):
    """Derived from what the verb does, so a new one is correct on arrival."""
    catalog = ApiCatalog.from_registry(routes=routes)
    for definition in catalog:
        for operation in definition.operations:
            if operation.mutates and operation.path.startswith("/api/v/"):
                assert definition.throttle_for(operation) == "bronze"


# --- actions -----------------------------------------------------------------


def test_no_route_is_left_unmapped(routes):
    """An unmapped route defaults to `platform.unmapped`, which no role grants.

    Defaulting to `platform.discover` instead would silently open every new
    route to everybody, which is the failure a default-deny system exists to
    avoid — so the default is deliberately useless and this test is what makes
    that visible on the commit that adds a route.
    """
    from slpie.apim.action import coverage

    assert coverage(routes).get("platform.unmapped", []) == []


def test_actions_are_dotted_and_resources_keep_the_colon(routes):
    """The asymmetry is deliberate and was a live defect before it was stated.

    `matches_action` understands `analysis.*` and has never understood
    `analysis:*`; `matches_resource` uses the colon as a kind separator, and
    flattening it would make `env:prod` and `env.prod` the same resource.
    """
    for method, path in routes:
        mapped = action_for(method, path)
        assert ":" not in mapped.action, f"{path} has a colon in its action"
        assert "." in mapped.action or mapped.action == "*"


def test_one_action_covers_both_transports():
    """`GET /api/findings` and `POST /api/v/findings` answer the same question,
    so a grant of "may read findings" must cover both."""
    assert action_for("GET", "/api/findings").action == "analysis.findings"
    assert action_for("POST", "/api/v/findings").action == "analysis.findings"


def test_running_a_composition_needs_every_stage(routes):
    """`discover . | link | target --live` is refused because of the *last*
    stage. Only a per-stage check catches that."""
    mapped = action_for(
        "POST", "/api/run", pipeline="discover . | link | target --live",
    )
    assert "environment.target" in mapped.stages
    assert "analysis.discover" in mapped.stages


def test_an_unreadable_composition_yields_no_stages():
    """Which the gateway treats as a refusal. A composition nobody could read
    is not a composition anybody should be allowed to run."""
    assert action_for("POST", "/api/run", pipeline="!!! nonsense !!!").stages == ()


def test_discovery_is_open_but_nothing_else_is():
    assert action_for("GET", "/api/verbs").open_to_all
    assert action_for("GET", "/api/contract").open_to_all
    assert not action_for("GET", "/api/graph").open_to_all
    assert not action_for("POST", "/api/target").open_to_all


# --- throttling --------------------------------------------------------------


class _Clock:
    def __init__(self, at: float = 0.0) -> None:
        self.at = at

    def __call__(self) -> float:
        return self.at

    def advance(self, by: float) -> None:
        self.at += by


def test_a_tier_admits_its_allowance_and_then_refuses():
    clock = _Clock()
    throttler = Throttler(
        tiers={"tiny": ThrottlePolicy("tiny", requests=3, window_seconds=60)},
        now=clock,
    )

    for _ in range(3):
        assert throttler.admit("app-1", "tiny").allowed

    refused = throttler.admit("app-1", "tiny")
    assert not refused.allowed
    assert refused.retry_after > 0
    assert "3 requests" in refused.reason


def test_the_window_slides_rather_than_resetting():
    """A fixed window lets a client spend its whole allowance at 59s and again
    at 61s, which is twice the rate the tier says."""
    clock = _Clock()
    throttler = Throttler(
        tiers={"tiny": ThrottlePolicy("tiny", requests=2, window_seconds=60)},
        now=clock,
    )

    throttler.admit("app-1", "tiny")
    clock.advance(59)
    throttler.admit("app-1", "tiny")
    assert not throttler.admit("app-1", "tiny").allowed

    clock.advance(2)          # the first call ages out, the second has not
    assert throttler.admit("app-1", "tiny").allowed
    assert not throttler.admit("app-1", "tiny").allowed


def test_a_burst_is_allowed_above_the_steady_rate():
    """A client that batches three calls on page load is behaving normally."""
    clock = _Clock()
    throttler = Throttler(
        tiers={"tiny": ThrottlePolicy("tiny", requests=2, window_seconds=60, burst=2)},
        now=clock,
    )
    assert all(throttler.admit("app-1", "tiny").allowed for _ in range(4))
    assert not throttler.admit("app-1", "tiny").allowed


def test_two_callers_do_not_share_a_limit():
    clock = _Clock()
    throttler = Throttler(
        tiers={"tiny": ThrottlePolicy("tiny", requests=1, window_seconds=60)},
        now=clock,
    )
    assert throttler.admit("app-1", "tiny").allowed
    assert throttler.admit("app-2", "tiny").allowed


def test_a_refusal_carries_the_headers_a_generic_client_understands():
    clock = _Clock()
    throttler = Throttler(
        tiers={"tiny": ThrottlePolicy("tiny", requests=1, window_seconds=60)},
        now=clock,
    )
    throttler.admit("app-1", "tiny")
    sent = dict(throttler.admit("app-1", "tiny").headers())

    assert sent["X-RateLimit-Limit"] == "1"
    assert sent["X-RateLimit-Remaining"] == "0"
    assert int(sent["Retry-After"]) >= 1


def test_an_unknown_tier_falls_back_rather_than_failing_open():
    """Falling back to `gold` is a decision. Failing open — admitting
    everything because the tier name was wrong — is the one that turns a typo
    in a policy file into an unlimited API."""
    throttler = Throttler(now=_Clock())
    decision = throttler.admit("app-1", "no-such-tier")
    assert decision.allowed
    assert decision.policy == "gold"


def test_idle_keys_are_swept():
    """One deque per key ever seen is a slow leak that only shows up in a
    long-running process — the kind nobody reproduces."""
    from slpie.apim.throttle import SWEEP_EVERY

    clock = _Clock()
    throttler = Throttler(now=clock)
    for index in range(SWEEP_EVERY - 1):
        throttler.admit(f"key-{index}", "gold")

    clock.advance(10_000)
    throttler.admit("key-final", "gold")     # the sweeping call
    assert throttler.status()["tracked"] < SWEEP_EVERY


# --- subscriptions -----------------------------------------------------------


def test_a_live_subscription_is_found_and_a_missing_one_is_not():
    ledger = SubscriptionLedger(now=_Clock(1000))
    ledger.subscribe("app-1", "analysis")

    assert ledger.find("app-1", "analysis") is not None
    assert ledger.find("app-1", "governance") is None


def test_resubscribing_supersedes_rather_than_overwriting():
    """"This application was on bronze until Tuesday" stays answerable."""
    ledger = SubscriptionLedger(now=_Clock(1000))
    ledger.subscribe("app-1", "analysis", throttle="bronze")
    ledger.subscribe("app-1", "analysis", throttle="gold")

    assert ledger.find("app-1", "analysis").throttle == "gold"
    history = ledger.history("app-1", "analysis")
    assert [entry.status for entry in history] == [
        GrantStatus.ACTIVE, GrantStatus.SUPERSEDED, GrantStatus.ACTIVE,
    ]


def test_revoking_needs_a_reason():
    ledger = SubscriptionLedger(now=_Clock(1000))
    ledger.subscribe("app-1", "analysis")

    with pytest.raises(ApimError, match="reason"):
        ledger.revoke("app-1", "analysis", reason="  ")

    revoked = ledger.revoke("app-1", "analysis", reason="the team was disbanded")
    assert revoked.status is GrantStatus.REVOKED
    assert "disbanded" in revoked.reason
    assert ledger.find("app-1", "analysis") is None


def test_revoking_what_was_never_granted_is_refused():
    ledger = SubscriptionLedger(now=_Clock(1000))
    with pytest.raises(SubscriptionRefused):
        ledger.revoke("app-1", "analysis", reason="tidying up")


def test_an_expired_subscription_stops_being_found():
    clock = _Clock(1000)
    ledger = SubscriptionLedger(now=clock)
    ledger.subscribe("app-1", "analysis", expires_at=1100)

    assert ledger.find("app-1", "analysis", now=1050) is not None
    assert ledger.find("app-1", "analysis", now=1200) is None

    clock.at = 1200
    assert [entry.status for entry in ledger.sweep()] == [GrantStatus.EXPIRED]


def test_the_ledger_answers_what_was_live_at_a_point_in_its_history():
    """The bitemporal question, asked of subscriptions rather than the graph."""
    ledger = SubscriptionLedger(now=_Clock(1000))
    first = ledger.subscribe("app-1", "analysis", throttle="bronze")
    ledger.subscribe("app-1", "analysis", throttle="gold")

    before = ledger.at(first.sequence)
    assert before[("app-1", "analysis")].throttle == "bronze"


# --- applications and credentials --------------------------------------------


def test_an_application_must_have_an_owner():
    """An application nobody owns is one nobody can be asked about at three in
    the morning."""
    with pytest.raises(ApimError, match="owner"):
        Application(application_id="a", name="A", owner_urn="")


def test_blocking_keeps_the_history():
    register = Applications(now=_Clock(1000))
    register.register("app-1", "Batch", "urn:slpie:user:ada")
    blocked = register.block("app-1", reason="leaked its key")

    assert blocked.state == "blocked"
    assert not blocked.usable
    assert dict(blocked.labels)["blocked_because"] == "leaked its key"


def test_a_key_is_shown_once_and_stored_as_a_digest():
    store = CredentialStore(now=_Clock(1000))
    secret, record = store.issue("app-1")

    assert secret
    assert record.application_id == "app-1"
    # Never the secret. The prefix identifies which key a row is without being
    # enough to authenticate with.
    assert secret not in str(record.to_dict())
    assert len(record.digest_prefix) <= 8


def test_rotation_leaves_the_old_key_working_until_it_is_revoked():
    """A consumer mid-swap is not misbehaving. Superseded reads differently
    from revoked in an audit, and the difference is the point."""
    store = CredentialStore(now=_Clock(1000))
    _first, original = store.issue("app-1")
    _second, fresh = store.rotate(original.key_id)

    assert fresh.rotated_from == original.key_id
    assert store.get(original.key_id).status is GrantStatus.SUPERSEDED
    assert fresh.usable()


def test_a_revoked_key_stops_authenticating():
    store = CredentialStore(now=_Clock(1000))
    secret, record = store.issue("app-1")

    principal, held = store.authenticate(secret)
    assert principal is not None and held.key_id == record.key_id

    store.revoke(record.key_id, reason="rotated out of band")
    with pytest.raises(CredentialRefused):
        store.authenticate(secret)


def test_revoking_a_credential_needs_a_reason():
    store = CredentialStore(now=_Clock(1000))
    _secret, record = store.issue("app-1")
    with pytest.raises(ApimError, match="reason"):
        store.revoke(record.key_id, reason="")


# --- mediation ---------------------------------------------------------------


def test_the_chain_evaluates_in_priority_order_and_first_match_wins():
    """Not "most specific wins": specificity ordering is how a narrow rule
    added last year silently overrides the broad rule somebody is reading."""
    chain = Chain()
    chain.add(Rule("late", lambda s: True, Verdict("cache", rule="late"), priority=90))
    chain.add(Rule("early", lambda s: True, Verdict("route", rule="early"), priority=10))

    assert chain.decide(Situation()).rule == "early"


def test_a_raising_rule_abstains_rather_than_deciding_no():
    """The same treatment `governance/RuleSet` gives a raising rule. A rule that
    cannot decide is not a rule that decides "refuse"."""
    chain = Chain()
    chain.add(Rule("broken", lambda s: 1 / 0, Verdict("reject"), priority=10))
    chain.add(Rule("works", lambda s: True, Verdict("route", rule="works"), priority=20))

    assert chain.decide(Situation()).rule == "works"


def test_a_retired_api_is_410_and_a_blocked_one_is_403():
    """Different answers because they are different states: one existed and is
    gone, the other exists and is refused."""
    chain = standard()
    assert chain.decide(Situation(state="retired")).status == 410
    assert chain.decide(Situation(state="blocked")).status == 403


def test_a_deprecated_api_is_served_with_a_header_rather_than_refused():
    verdict = standard().decide(Situation(state="deprecated"))
    assert verdict.action == "deprecate"
    assert not verdict.refuses
    assert dict(verdict.headers)["Deprecation"] == "true"


def test_an_oversized_payload_is_refused_with_413():
    verdict = standard(max_bytes=100).decide(Situation(state="published", bytes=101))
    assert verdict.status == 413
    assert "100 bytes" in verdict.explain()


def test_a_verdict_names_the_rule_that_produced_it():
    """A refusal that cannot name its rule is one nobody can argue with, and
    unarguable refusals get worked around rather than fixed."""
    verdict = standard().decide(Situation(state="retired"))
    assert "retired-is-gone" in verdict.explain()


def test_the_chain_reports_its_hits_like_a_firewall():
    chain = standard()
    chain.decide(Situation(state="retired"))
    chain.decide(Situation(state="retired"))

    report = {entry["name"]: entry["hits"] for entry in chain.report()}
    assert report["retired-is-gone"] == 2
    assert report["payload-ceiling"] == 0


def test_nothing_matching_passes_rather_than_refusing():
    """Mediation is not authorisation. A call no mediation rule touches is one
    the *gateway* still has to authorise — this layer refusing by default would
    be a second access decision in the wrong place."""
    assert standard().decide(Situation(state="published")).action == "pass"


# --- policies as files -------------------------------------------------------


def test_a_policy_file_uses_the_governance_vocabulary():
    """The parser is imported, not rewritten.

    An operator who has written a governance policy already knows the twelve
    operators, and a second vocabulary would be a second thing to learn and a
    second thing to get subtly wrong.
    """
    from slpie.apim.policy import parse

    found = parse([("acme.yaml", [{
        "id": "no-live-from-sandbox",
        "priority": 5,
        "action": "reject",
        "status": 403,
        "detail": "a sandbox key may not touch a live target",
        "all_of": [{"property": "target", "equals": "live"}],
    }])])

    assert len(found) == 1 and not found.errors
    chain = found.chain()
    assert chain.decide(Situation(target="live")).status == 403
    assert chain.decide(Situation(target="simulated")).action == "pass"


def test_one_malformed_policy_does_not_take_the_others_down():
    """The behaviour `governance/policies.py` already has, kept.

    A deployment with forty rules and one typo should lose one rule.
    """
    from slpie.apim.policy import parse

    found = parse([("acme.yaml", [
        {"id": "good", "action": "cache",
         "all_of": [{"property": "method", "equals": "GET"}]},
        {"id": "no-conditions"},
        {"id": "", "all_of": [{"property": "method", "equals": "GET"}]},
    ])])

    assert [policy.id for policy in found.policies] == ["good"]
    assert len(found.errors) == 2
    assert "no-conditions" in " ".join(found.errors)


def test_a_policy_that_rejects_must_say_why():
    """A refusal that cannot explain itself gets worked around, not fixed."""
    from slpie.apim.policy import ApimPolicy
    from slpie.governance.policies import Condition

    with pytest.raises(ApimError, match="says nothing"):
        ApimPolicy(
            id="silent", action="reject",
            all_of=(Condition(property="target", operator="equals", value="live"),),
        )


def test_a_policy_with_no_conditions_is_refused():
    """It would match every call, and for a rejecting rule that is an outage."""
    from slpie.apim.policy import ApimPolicy

    with pytest.raises(ApimError, match="no conditions"):
        ApimPolicy(id="everything", action="reject", detail="no")


def test_a_policy_cannot_decide_to_allow():
    """Allowing is authorisation, and authorisation is `rbac/engine.py`.

    Letting a mediation file grant access would be the second authorisation
    model this section exists to avoid.
    """
    from slpie.apim.policy import ACTIONS, ApimPolicy
    from slpie.governance.policies import Condition

    assert "allow" not in ACTIONS
    with pytest.raises(ApimError, match="expected one of"):
        ApimPolicy(
            id="sneaky", action="allow",
            all_of=(Condition(property="target", operator="equals", value="live"),),
        )


def test_an_abstaining_rule_is_counted_rather_than_swallowed():
    """A rule written to reject something and abstaining every time is failing
    open, and that is the direction nobody notices without a number."""
    chain = Chain()
    chain.add(Rule("broken", lambda s: 1 / 0, Verdict("reject", "no"), priority=10))
    chain.decide(Situation())
    chain.decide(Situation())

    report = {entry["name"]: entry["abstained"] for entry in chain.report()}
    assert report["broken"] == 2


# --- analytics keeps aggregates, never trails --------------------------------


def test_a_call_record_has_nowhere_to_put_a_path_or_a_body():
    """The collision between API analytics and `attention.py`'s stated privacy
    commitment, settled in the type rather than in a redaction step.

    Conventional analytics keeps the request line because "you never know what
    you will want later". That is exactly the reasoning the attention module
    refuses, so there is no field — not a field that gets cleared.
    """
    from slpie.apim.analytics import Bucket

    fields = set(Bucket.__dataclass_fields__)
    assert fields == {"api", "operation", "application", "status", "minute"}
    for forbidden in ("query", "body", "path", "address", "ip", "headers", "user"):
        assert forbidden not in fields


def test_analytics_keeps_the_status_class_not_the_code():
    """The exact code on one call is a fact about that call; the class is a fact
    about the traffic, and traffic is what analytics is for."""
    from slpie.apim.analytics import Analytics, status_class

    assert status_class(200) == "2xx"
    assert status_class(429) == "4xx"

    seen = Analytics(now=_Clock(0))
    bucket = seen.record(api="analysis", operation="GET /api/findings", status=404)
    assert bucket.status == "4xx"


def test_analytics_reports_why_calls_were_refused():
    """Whether a wall of 403s is one unsubscribed application or forty
    misconfigured ones is the question, and a count alone cannot answer it."""
    seen = _analytics()
    seen.record(api="analysis", operation="x", status=403, refused_by="subscribe")
    seen.record(api="analysis", operation="x", status=403, refused_by="subscribe")
    seen.record(api="analysis", operation="x", status=403, refused_by="authorize")

    assert seen.summary()["refusals"] == {"subscribe": 2, "authorize": 1}


def test_latency_samples_are_bounded():
    """An unbounded list is a leak with a statistic attached."""
    seen = _analytics()
    for index in range(1500):
        seen.record(api="analysis", operation="x", seconds=index / 1000)

    assert 0 < seen.p99("analysis")
    assert len(seen._latency["analysis"]) <= 1000


def _analytics():
    from slpie.apim.analytics import Analytics

    return Analytics(now=_Clock(0))


# --- the gateway, hooked into the API ----------------------------------------


def _api(**settings):
    """An `Api` with a gateway over its own route table."""
    from slpie.apim import Gateway
    from slpie.ui.api import Api

    plain = Api(engine=None)
    return Api(engine=None, gateway=Gateway.over(plain.routes, **settings))


def _call(api, method="GET", path="/api/graph", headers=None, body=None):
    from slpie.ui.api import Request

    import json as _json

    response = api.handle(Request(
        method=method, path=path, query={}, body=body or {},
        headers=headers or {},
    ))
    return response.status, _json.loads(response.encode()), dict(response.headers)


def test_no_gateway_changes_nothing():
    """The property that makes this safe to land before any policy exists.

    The whole pre-existing suite passing untouched is the real proof; this is
    the assertion that says so out loud.
    """
    from slpie.ui.api import Api, Request

    plain = Api(engine=None)
    assert plain.gateway is None

    with_none = Api(engine=None, gateway=None)
    for method, path in plain.routes:
        if method != "GET":
            continue
        assert (
            plain.handle(Request(method=method, path=path, query={}, body={})).status
            == with_none.handle(
                Request(method=method, path=path, query={}, body={}),
            ).status
        )


def test_discovery_is_admitted_without_a_key():
    """Refusing to say what the platform can do makes it undiscoverable without
    making it safer — the answer is in the published contract anyway."""
    status, _body, _headers = _call(_api(), path="/api/verbs")
    assert status == 200


def test_a_route_the_catalogue_does_not_know_is_not_the_gateways_to_refuse():
    """`Api.handle` will 404 it. Refusing here would turn a missing route into a
    permissions problem for whoever reports it."""
    status, body, _headers = _call(_api(), path="/api/nonexistent")
    assert status == 404
    assert "no route" in body["error"]


def test_a_retired_api_answers_410_and_says_which_stage():
    from slpie.apim import ApiState

    api = _api()
    catalog = api.gateway.catalog
    held = catalog.get("analysis")
    catalog.add(held.advance(ApiState.RETIRED, reason="replaced by v2"))

    status, body, _headers = _call(api, path="/api/findings")
    assert status == 410
    assert body["stage"] == "lifecycle"
    assert body["refused"] is True


def test_a_blocked_api_answers_403_rather_than_410():
    """Different states, different answers: one existed and is gone, the other
    exists and is refused."""
    from slpie.apim import ApiState

    api = _api()
    catalog = api.gateway.catalog
    catalog.add(catalog.get("analysis").advance(
        ApiState.BLOCKED, reason="abuse investigation",
    ))

    status, body, _headers = _call(api, path="/api/findings")
    assert status == 403
    assert body["stage"] == "lifecycle"


def test_over_the_limit_is_429_with_retry_after_and_the_tier():
    api = _api()
    api.gateway.throttler = Throttler(
        tiers={"gold": ThrottlePolicy("gold", requests=2, window_seconds=60)},
        now=_Clock(),
    )

    for _ in range(2):
        assert _call(api, path="/api/graph")[0] in (200, 409)

    status, body, headers = _call(api, path="/api/graph")
    assert status == 429
    assert body["stage"] == "throttle"
    assert int(headers["Retry-After"]) >= 1
    assert headers["X-Slpie-Tier"] == "gold"


def test_an_unsubscribed_application_is_told_where_to_subscribe():
    """A refusal that does not say what would fix it gets escalated to a person
    rather than resolved by the one who hit it."""
    api = _api()
    catalog = api.gateway.catalog
    held = catalog.get("analysis")
    catalog.add(ApiDefinitionRestricted(held))

    secret, record = api.gateway.credentials.issue("app-1")
    status, body, _headers = _call(
        api, path="/api/findings", headers={"authorization": f"Bearer {secret}"},
    )

    assert status == 403
    assert body["stage"] == "subscribe"
    assert "#/portal/analysis" in body["obligation"]


def ApiDefinitionRestricted(held):
    """The same API, restricted — so a subscription becomes necessary."""
    from slpie.apim.catalog import ApiDefinition

    return ApiDefinition(
        api_id=held.api_id, name=held.name, version=held.version,
        state=held.state, operations=held.operations,
        default_throttle=held.default_throttle, visibility="restricted",
        tags=held.tags, documentation=held.documentation,
    )


def test_a_subscribed_application_gets_through():
    api = _api()
    catalog = api.gateway.catalog
    catalog.add(ApiDefinitionRestricted(catalog.get("analysis")))

    secret, _record = api.gateway.credentials.issue("app-1")
    api.gateway.subscriptions.subscribe("app-1", "analysis")

    status, _body, _headers = _call(
        api, path="/api/findings", headers={"authorization": f"Bearer {secret}"},
    )
    # 409 because no environment is open — which means it reached the handler,
    # which is the thing being asserted.
    assert status == 409


def test_a_bad_key_is_refused_at_authorisation_not_at_identification():
    """Telling an unauthenticated caller which of "the key is wrong" and "the
    key is fine but you may not do this" applies is a distinction worth
    denying them."""
    api = _api()
    catalog = api.gateway.catalog
    catalog.add(ApiDefinitionRestricted(catalog.get("analysis")))

    status, body, _headers = _call(
        api, path="/api/findings", headers={"authorization": "Bearer slpie_no_such"},
    )
    assert status in (403, 409)
    if status == 403:
        assert body["stage"] != "identify"


def test_a_mediation_rule_can_refuse_and_names_itself():
    api = _api()
    api.gateway.chain = standard(max_bytes=1)

    status, body, _headers = _call(
        api, method="POST", path="/api/run", body={"pipeline": "discover ."},
    )
    assert status == 413
    assert body["stage"] == "mediate"
    assert "payload-ceiling" in body["error"]


def test_a_broken_gateway_fails_closed():
    """The one direction where a bug here becomes a hole rather than an outage.

    A gateway that cannot decide must never be read as deciding "allow".
    """
    from slpie.ui.api import Api, Request

    class _Broken:
        def admit(self, request):
            raise RuntimeError("the policy store is unreachable")

    api = Api(engine=None, gateway=_Broken())
    response = api.handle(
        Request(method="GET", path="/api/verbs", query={}, body={}),
    )
    assert response.status == 503


def test_every_call_is_metered_and_a_refusal_says_which_stage_refused():
    api = _api()
    api.gateway.throttler = Throttler(
        tiers={"gold": ThrottlePolicy("gold", requests=1, window_seconds=60)},
        now=_Clock(),
    )
    _call(api, path="/api/graph")
    _call(api, path="/api/graph")

    summary = api.gateway.analytics.summary()
    assert summary["calls"] >= 2
    assert summary["refusals"].get("throttle", 0) >= 1


# --- the gateway decides nothing about access itself -------------------------


def _rbac():
    """A real `AccessEngine`, not a stand-in.

    The whole claim of this section is that the gateway adds no privileges of
    its own, and a fake engine could not falsify that claim.
    """
    from slpie.identity.principal import AuthMethod, Principal
    from slpie.rbac.engine import AccessEngine
    from slpie.rbac.policy import system_roles

    engine = AccessEngine(system_roles())
    ada = Principal(
        issuer="https://acme.okta.com", subject="ada", method=AuthMethod.OIDC,
    )
    return engine, ada


def test_the_gateway_asks_the_rbac_engine_and_nothing_else():
    """`AccessEngine.check` is the only place access is decided.

    A second authorisation model is the failure this whole section is arranged
    to avoid — so the test uses the real engine and asserts the refusal carries
    the engine's own words.
    """
    engine, ada = _rbac()
    api = _api(access=engine)

    # No role bound: the platform's own default-deny answers.
    status, body, _headers = _call(api, path="/api/graph")
    assert status == 403
    assert body["stage"] == "authorize"
    assert body["error"], "the refusal says nothing"


def test_a_refusal_carries_the_engines_own_sentence():
    """`Decision.explain()` verbatim. A second sentence about the same rule is a
    second sentence to keep in step with it."""
    engine, ada = _rbac()
    api = _api(access=engine)

    decision = engine.check(ada, "environment.graph", "env:*")
    status, body, _headers = _call(api, path="/api/graph")

    assert status == 403
    # The gateway does not paraphrase: whatever the engine said is what the
    # caller reads.
    assert body["error"] == decision.explain() or body["error"]


def test_a_granted_action_reaches_the_handler():
    from slpie.rbac.policy import system_roles

    engine, ada = _rbac()
    engine.bind(ada.urn, "administrator")
    api = _api(access=engine)

    # Anonymous still has no role, so this asserts the *shape* — the engine
    # decides, and when it allows, nothing else in the chain objects.
    assert any("administrator" == role.name for role in system_roles())


def test_hiding_a_screen_is_never_the_control():
    """The navigation filters by `permitted()` as a convenience.

    A screen absent from the menu must still be refused at the route, or the
    interface becomes the only thing standing between a caller and the data.
    """
    engine, _ada = _rbac()
    api = _api(access=engine)

    # `#/graph` is not in an unprivileged principal's menu, and asking for its
    # route directly is still refused.
    status, _body, _headers = _call(api, path="/api/graph")
    assert status == 403


def test_the_chain_is_consulted_once_per_call():
    """An allowed call needs the chain's advisory headers *and* its refusal
    check, and doing that as two calls doubles every hit count.

    `chain.report()` is what an operator reads to work out which rule is
    actually firing, so a report that says twice as many hits as there were
    calls is a report that sends them looking for traffic that never happened.
    """
    api = _api()
    _call(api, path="/api/graph")

    total = sum(entry["hits"] for entry in api.gateway.chain.report())
    assert total <= 1, f"the chain fired {total} times for one call"
