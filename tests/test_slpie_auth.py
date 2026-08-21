"""Sign-in and the connector keyring: the path from a credential to a capability.

The headline case is at the bottom — a Google ID token goes in, and the
connectors that sign-in unlocks become available with their capabilities
registered, without anything being configured. That is the behaviour the whole
design exists to produce.
"""

from __future__ import annotations

import time

import pytest

from slpie.connectors import (
    ConnectorError,
    ConnectorKind,
    ConnectorSpec,
    GrantStatus,
    Keyring,
    KeyringEvent,
    Scope,
    builtin_catalogue,
)
from slpie.identity import (
    ApiKeyProvider,
    Assurance,
    AuthMethod,
    ClaimMap,
    EmailLinkProvider,
    IdentityError,
    OidcProvider,
    PhoneOtpProvider,
    Principal,
    PrincipalKind,
    ProviderRegistry,
    apple,
    custom,
    google,
)
from tests.test_slpie_jwt import RsaKeypair

SECRET = b"a" * 32
SALT = b"b" * 16


@pytest.fixture(scope="module")
def keypair():
    return RsaKeypair()


def id_token(keypair, *, issuer, audience, **overrides):
    now = time.time()
    claims = {
        "iss": issuer, "sub": "1078412", "aud": audience,
        "exp": now + 3600, "iat": now,
        "email": "ada@example.com", "email_verified": True,
        "name": "Ada Lovelace",
    }
    claims.update(overrides)
    return keypair.token(claims)


# --- principals ----------------------------------------------------------


def test_a_principal_is_keyed_on_issuer_and_subject_not_on_email():
    first = Principal(issuer="https://accounts.google.com", subject="42",
                      email="ada@old.com")
    renamed = Principal(issuer="https://accounts.google.com", subject="42",
                        email="ada@new.com")
    assert first.urn == renamed.urn


def test_the_same_subject_from_two_issuers_is_two_people():
    federated = Principal(issuer="https://accounts.google.com", subject="42")
    internal = Principal(issuer="https://idp.acme.internal", subject="42")
    assert federated.urn != internal.urn


def test_a_principal_must_name_its_issuer():
    with pytest.raises(IdentityError, match="must name the issuer"):
        Principal(issuer="", subject="42")


def test_an_agent_with_nobody_behind_it_is_refused():
    with pytest.raises(IdentityError, match="must act on someone's authority"):
        Principal(issuer="urn:slpie:idp:apikey", subject="bot",
                  kind=PrincipalKind.AGENT)


def test_an_agent_never_out_ranks_the_human_who_delegated_to_it():
    human = Principal(issuer="https://accounts.google.com", subject="42",
                      assurance=Assurance.WEAK)
    agent = human.delegate("scanner-1")
    assert agent.assurance is Assurance.WEAK
    assert agent.on_behalf_of == human.urn


def test_assurance_is_ordered_and_compared_as_such():
    assert Assurance.STRONG.satisfies(Assurance.STANDARD)
    assert not Assurance.WEAK.satisfies(Assurance.STANDARD)


def test_requiring_more_assurance_than_was_proved_says_what_to_do():
    weak = Principal(issuer="urn:slpie:idp:phone", subject="+15550100000",
                     method=AuthMethod.PHONE_OTP, assurance=Assurance.WEAK)
    with pytest.raises(IdentityError, match="re-authenticate with a stronger method"):
        weak.require(Assurance.STRONG)


def test_a_phone_number_is_masked_when_a_principal_is_rendered():
    principal = Principal(issuer="i", subject="s", phone="+1 (555) 010-9876")
    assert principal.to_dict()["phone"] == "***9876"


def test_raw_claims_never_cross_the_serialisation_boundary():
    principal = Principal(issuer="i", subject="s",
                          claims={"ssn": "should-never-appear"})
    assert "should-never-appear" not in str(principal.to_dict())


# --- OIDC providers ------------------------------------------------------


def test_a_google_id_token_produces_a_principal(keypair):
    provider = google(audience="slpie.example.com", keys=[keypair.public()])
    principal = provider.authenticate(
        id_token(keypair, issuer="https://accounts.google.com",
                 audience="slpie.example.com")
    )
    assert principal.method is AuthMethod.GOOGLE
    assert principal.email == "ada@example.com"
    assert principal.display == "Ada Lovelace"
    assert principal.assurance is Assurance.STANDARD


def test_a_provider_without_an_audience_is_refused_at_construction():
    with pytest.raises(IdentityError, match="any token minted"):
        OidcProvider(name="x", issuer="https://issuer", audience="")


def test_a_token_from_another_issuer_is_refused(keypair):
    provider = google(audience="slpie.example.com", keys=[keypair.public()])
    with pytest.raises(IdentityError, match="sign-in failed"):
        provider.authenticate(
            id_token(keypair, issuer="https://evil.example",
                     audience="slpie.example.com")
        )


def test_google_refuses_an_unverified_email(keypair):
    provider = google(audience="slpie.example.com", keys=[keypair.public()])
    with pytest.raises(IdentityError, match="not an identity"):
        provider.authenticate(
            id_token(keypair, issuer="https://accounts.google.com",
                     audience="slpie.example.com", email_verified=False)
        )


def test_apple_sends_email_verified_as_a_string_and_it_is_read_correctly(keypair):
    provider = apple(audience="com.acme.slpie", keys=[keypair.public()])
    principal = provider.authenticate(
        id_token(keypair, issuer="https://appleid.apple.com",
                 audience="com.acme.slpie", email_verified="true")
    )
    assert principal.email_verified is True


def test_the_string_false_is_not_treated_as_truthy(keypair):
    provider = apple(audience="com.acme.slpie", keys=[keypair.public()])
    principal = provider.authenticate(
        id_token(keypair, issuer="https://appleid.apple.com",
                 audience="com.acme.slpie", email_verified="false")
    )
    assert principal.email_verified is False


def test_apple_still_signs_in_when_it_omits_the_email_entirely(keypair):
    """Apple sends the address only on first authorisation; `sub` is the identity."""
    provider = apple(audience="com.acme.slpie", keys=[keypair.public()])
    stripped = {"iss": "https://appleid.apple.com", "sub": "001234.abc",
                "aud": "com.acme.slpie", "exp": time.time() + 600,
                "iat": time.time()}
    principal = provider.authenticate(keypair.token(stripped))
    assert principal.subject == "001234.abc"
    assert principal.email == ""


def test_mfa_asserted_by_the_issuer_raises_the_assurance(keypair):
    provider = google(audience="slpie.example.com", keys=[keypair.public()])
    principal = provider.authenticate(
        id_token(keypair, issuer="https://accounts.google.com",
                 audience="slpie.example.com", amr=["pwd", "mfa"])
    )
    assert principal.assurance is Assurance.STRONG


def test_assurance_is_not_raised_without_the_issuer_saying_so(keypair):
    provider = google(audience="slpie.example.com", keys=[keypair.public()])
    principal = provider.authenticate(
        id_token(keypair, issuer="https://accounts.google.com",
                 audience="slpie.example.com", amr=["pwd"])
    )
    assert principal.assurance is Assurance.STANDARD


def test_an_enterprise_idp_is_a_configuration_call_not_a_code_change(keypair):
    okta = custom(name="acme-okta", issuer="https://acme.okta.com",
                  audience="slpie", keys=[keypair.public()],
                  claim_map=ClaimMap(email="upn", display="displayName"))
    principal = okta.authenticate(
        id_token(keypair, issuer="https://acme.okta.com", audience="slpie",
                 upn="ada@acme.com", displayName="Ada L")
    )
    assert principal.email == "ada@acme.com"
    assert principal.display == "Ada L"


def test_a_jwks_document_loads_and_skips_keys_we_cannot_verify(keypair):
    from slpie.identity.jwt import b64url_encode

    document = {"keys": [
        {"kty": "EC", "crv": "P-256", "x": "AA", "y": "AA", "kid": "ec"},
        {"kty": "RSA", "kid": "test-key", "alg": "RS256", "use": "sig",
         "n": b64url_encode(keypair.n.to_bytes(keypair.size, "big")),
         "e": b64url_encode((65537).to_bytes(3, "big"))},
    ]}
    provider = OidcProvider.from_jwks(
        document, name="google", issuer="https://accounts.google.com",
        audience="slpie.example.com", method=AuthMethod.GOOGLE,
    )
    assert len(provider.keys) == 1
    assert provider.authenticate(
        id_token(keypair, issuer="https://accounts.google.com",
                 audience="slpie.example.com")
    ).subject == "1078412"


def test_rotating_keys_returns_a_new_provider_rather_than_mutating(keypair):
    original = google(audience="slpie.example.com", keys=[keypair.public()])
    rotated = original.with_keys([keypair.public(kid="new-key")])
    assert original.keys[0].kid == "test-key"
    assert rotated.keys[0].kid == "new-key"


# --- email and phone -----------------------------------------------------


def test_a_magic_link_signs_someone_in():
    provider = EmailLinkProvider(SECRET)
    challenge = provider.issue("Ada@Example.COM")
    principal = provider.authenticate(challenge.token)
    assert principal.email == "ada@example.com"
    assert principal.assurance is Assurance.WEAK


def test_an_altered_magic_link_is_refused():
    provider = EmailLinkProvider(SECRET)
    challenge = provider.issue("ada@example.com")
    body, _, signature = challenge.token.partition(".")
    with pytest.raises(IdentityError, match="altered or was not issued here"):
        provider.authenticate(f"{body}x.{signature}")


def test_a_link_signed_with_another_secret_is_refused():
    issued = EmailLinkProvider(SECRET).issue("ada@example.com")
    with pytest.raises(IdentityError, match="not issued here"):
        EmailLinkProvider(b"z" * 32).authenticate(issued.token)


def test_an_expired_magic_link_says_how_long_they_last():
    provider = EmailLinkProvider(SECRET, ttl=900)
    challenge = provider.issue("ada@example.com", now=1000.0)
    with pytest.raises(IdentityError, match="valid for 15 minutes"):
        provider.authenticate(challenge.token, now=1000.0 + 901)


def test_a_short_challenge_secret_is_refused():
    with pytest.raises(IdentityError, match="at least 32 bytes"):
        EmailLinkProvider(b"short")


def test_something_that_is_not_an_email_is_refused():
    with pytest.raises(IdentityError, match="not an email address"):
        EmailLinkProvider(SECRET).issue("not-an-address")


def test_a_one_time_code_signs_someone_in():
    provider = PhoneOtpProvider(SECRET)
    challenge = provider.issue("+1 (555) 010-0000", now=1000.0)
    principal = provider.authenticate(challenge.token, phone="+15550100000", now=1000.0)
    assert principal.phone_verified
    assert principal.assurance is Assurance.WEAK


def test_a_number_typed_differently_still_matches():
    provider = PhoneOtpProvider(SECRET)
    challenge = provider.issue("+15550100000", now=1000.0)
    assert provider.authenticate(
        challenge.token, phone="+1 (555) 010-0000", now=1000.0
    ).phone == "+15550100000"


def test_a_code_issued_just_before_a_window_boundary_still_works():
    provider = PhoneOtpProvider(SECRET, ttl=300)
    challenge = provider.issue("+15550100000", now=299.0)
    assert provider.authenticate(challenge.token, phone="+15550100000", now=301.0)


def test_a_code_two_windows_old_does_not_work():
    provider = PhoneOtpProvider(SECRET, ttl=300)
    challenge = provider.issue("+15550100000", now=0.0)
    with pytest.raises(IdentityError, match="not correct or has expired"):
        provider.authenticate(challenge.token, phone="+15550100000", now=900.0)


def test_guessing_a_code_burns_the_challenge_after_a_few_attempts():
    provider = PhoneOtpProvider(SECRET)
    provider.issue("+15550100000", now=1000.0)
    for _ in range(5):
        with pytest.raises(IdentityError):
            provider.authenticate("000000", phone="+15550100000", now=1000.0)
    with pytest.raises(IdentityError, match="burned"):
        provider.authenticate("000000", phone="+15550100000", now=1000.0)


def test_a_code_presented_without_its_number_is_refused():
    with pytest.raises(IdentityError, match="with its phone number"):
        PhoneOtpProvider(SECRET).authenticate("123456")


# --- API keys ------------------------------------------------------------


def test_an_api_key_authenticates_a_service():
    provider = ApiKeyProvider(SALT)
    secret, _ = provider.register("ci-runner", label="CI")
    principal = provider.authenticate(secret)
    assert principal.kind is PrincipalKind.SERVICE
    assert principal.display == "CI"


def test_an_api_key_is_never_recoverable_from_the_store():
    provider = ApiKeyProvider(SALT)
    secret, record = provider.register("ci-runner")
    assert secret not in record.digest
    assert record.digest != secret


def test_an_unknown_api_key_is_refused():
    with pytest.raises(IdentityError, match="not recognised"):
        ApiKeyProvider(SALT).authenticate("slpie_deadbeef_nonsense")


def test_a_revoked_api_key_stops_working():
    provider = ApiKeyProvider(SALT)
    secret, record = provider.register("ci-runner")
    assert provider.revoke(record.key_id)
    with pytest.raises(IdentityError, match="not recognised"):
        provider.authenticate(secret)


def test_an_expired_api_key_is_refused():
    provider = ApiKeyProvider(SALT)
    secret, _ = provider.register("ci-runner", expires_at=100.0)
    with pytest.raises(IdentityError, match="expired"):
        provider.authenticate(secret, now=200.0)


def test_an_agent_key_carries_the_human_who_delegated():
    provider = ApiKeyProvider(SALT)
    secret, _ = provider.register("scanner", kind=PrincipalKind.AGENT,
                                  on_behalf_of="urn:slpie:principal:human/abc")
    assert provider.authenticate(secret).on_behalf_of.endswith("abc")


# --- the provider registry -----------------------------------------------


def test_a_caller_names_a_provider_and_gets_a_principal(keypair):
    registry = ProviderRegistry(
        google(audience="slpie.example.com", keys=[keypair.public()]),
        EmailLinkProvider(SECRET),
    )
    principal = registry.authenticate(
        "google",
        id_token(keypair, issuer="https://accounts.google.com",
                 audience="slpie.example.com"),
    )
    assert principal.method is AuthMethod.GOOGLE


def test_an_unconfigured_provider_lists_what_is_available():
    registry = ProviderRegistry(EmailLinkProvider(SECRET))
    with pytest.raises(IdentityError, match="available: email"):
        registry.authenticate("okta", "whatever")


def test_registering_the_same_provider_twice_is_refused():
    registry = ProviderRegistry(EmailLinkProvider(SECRET))
    with pytest.raises(IdentityError, match="already registered"):
        registry.register(EmailLinkProvider(SECRET))


# --- connector declarations ----------------------------------------------


def test_a_connector_that_anything_can_unlock_is_refused():
    with pytest.raises(ConnectorError, match="names no authentication method"):
        ConnectorSpec(id="x", kind=ConnectorKind.CUSTOM, title="X",
                      methods=(), capabilities=("read",))


def test_a_connector_that_grants_nothing_is_refused():
    with pytest.raises(ConnectorError, match="would change nothing observable"):
        ConnectorSpec(id="x", kind=ConnectorKind.CUSTOM, title="X",
                      methods=(AuthMethod.API_KEY,), capabilities=())


def test_a_scope_nobody_could_consent_to_is_refused():
    with pytest.raises(ConnectorError, match="is not consent"):
        Scope("repo", "")


def test_a_read_only_connector_asking_for_write_scopes_is_refused():
    with pytest.raises(ConnectorError, match="one of the two is wrong"):
        ConnectorSpec(
            id="x", kind=ConnectorKind.CUSTOM, title="X",
            methods=(AuthMethod.API_KEY,), capabilities=("read",),
            scopes=(Scope("w", "write things", writes=True),), read_only=True,
        )


def test_a_production_connector_cannot_accept_weak_assurance():
    with pytest.raises(ConnectorError, match="accepts weak assurance"):
        ConnectorSpec(
            id="db", kind=ConnectorKind.DATABASE, title="DB",
            methods=(AuthMethod.EMAIL_LINK,), capabilities=("schema-read",),
            assurance=Assurance.WEAK,
        )


def test_a_connector_says_why_a_sign_in_does_not_unlock_it():
    drive = builtin_catalogue().require("google-drive")
    ok, reason = drive.unlocked_by(AuthMethod.APPLE, Assurance.STANDARD)
    assert not ok
    assert "not by apple" in reason


def test_a_connector_says_when_the_assurance_is_too_low():
    aws = builtin_catalogue().require("aws")
    ok, reason = aws.unlocked_by(AuthMethod.API_KEY, Assurance.STANDARD)
    assert not ok
    assert "requires STRONG assurance" in reason


def test_a_partially_scoped_connector_grants_nothing_rather_than_a_subset():
    github = builtin_catalogue().require("github")
    assert github.capabilities_for(["repo.read"]) == ()
    assert github.capabilities_for(["repo.read", "pr.read"]) == github.capabilities


def test_the_catalogue_is_browsable_before_anybody_signs_in():
    catalogue = builtin_catalogue()
    assert len(catalogue) >= 15
    assert "document-read" in catalogue.capabilities()


def test_the_catalogue_can_say_what_a_sign_in_would_unlock():
    unlocked = builtin_catalogue().unlocked_by(AuthMethod.GOOGLE, Assurance.STANDARD)
    assert "google-drive" in {spec.id for spec in unlocked}


# --- the keyring ---------------------------------------------------------


@pytest.fixture
def keyring():
    return Keyring(builtin_catalogue())


@pytest.fixture
def okta(keypair):
    """An enterprise IdP sign-in — the identity most connectors are unlocked by."""
    provider = custom(name="acme-okta", issuer="https://acme.okta.com",
                      audience="slpie", keys=[keypair.public()])
    return provider.authenticate(
        id_token(keypair, issuer="https://acme.okta.com", audience="slpie")
    )


@pytest.fixture
def ada(keypair):
    return google(audience="slpie.example.com", keys=[keypair.public()]).authenticate(
        id_token(keypair, issuer="https://accounts.google.com",
                 audience="slpie.example.com")
    )


def test_signing_in_with_google_offers_what_google_unlocks(keyring, ada):
    available = {spec.id for spec in keyring.available(ada)}
    assert "google-drive" in available
    assert "gcp" not in available          # GCP wants STRONG; this sign-in is STANDARD
    assert "postgres" not in available


def test_the_offer_includes_what_is_unavailable_and_why(keyring, ada):
    offers = {offer.spec.id: offer for offer in keyring.offer(ada)}
    assert not offers["postgres"].available
    assert "not by google" in offers["postgres"].reason


def test_granting_a_connector_activates_its_capabilities(keyring, ada):
    activation = keyring.grant(ada, "google-drive")
    assert activation.complete
    assert "document-read" in activation.capabilities
    assert keyring.can(ada.urn, "document-read")


def test_a_capability_nobody_granted_is_not_available(keyring, ada):
    keyring.grant(ada, "google-drive")
    assert not keyring.can(ada.urn, "cloud-inventory")


def test_granting_defaults_to_required_scopes_and_not_to_everything(keyring, ada):
    activation = keyring.grant(ada, "google-drive")
    assert activation.grant.scopes == ("files.read",)


def test_a_partial_grant_is_degraded_rather_than_failed(keypair):
    strong = google(audience="slpie.example.com", keys=[keypair.public()]).authenticate(
        id_token(keypair, issuer="https://accounts.google.com",
                 audience="slpie.example.com", amr=["mfa"])
    )
    keyring = Keyring(builtin_catalogue())
    activation = keyring.grant(strong, "gcp", scopes=["cost.read"])
    assert not activation.complete
    assert activation.capabilities == ()
    assert [scope.name for scope in activation.degraded] == ["inventory.read"]


def test_granting_an_undeclared_scope_is_refused(keyring, ada):
    with pytest.raises(ConnectorError, match="does not define scope"):
        keyring.grant(ada, "google-drive", scopes=["files.delete"])


def test_a_sign_in_that_cannot_unlock_a_connector_is_refused_and_recorded(keyring, ada):
    with pytest.raises(ConnectorError, match="not by google"):
        keyring.grant(ada, "postgres")
    refusals = [grant for grant in keyring.log if grant.connector_id == "postgres"]
    assert refusals and refusals[0].reason


def test_a_refused_grant_does_not_become_current_state(keyring, ada):
    with pytest.raises(ConnectorError):
        keyring.grant(ada, "postgres")
    assert keyring.grant_for(ada.urn, "postgres") is None


def test_an_expired_session_cannot_grant_anything(keyring):
    stale = Principal(issuer="https://accounts.google.com", subject="42",
                      method=AuthMethod.GOOGLE, expires_at=100.0)
    with pytest.raises(ConnectorError, match="session has expired"):
        keyring.grant(stale, "google-drive", now=200.0)


def test_revoking_takes_the_capability_away(keyring, ada):
    keyring.grant(ada, "google-drive")
    assert keyring.revoke(ada.urn, "google-drive", reason="offboarded")
    assert not keyring.can(ada.urn, "document-read")


def test_a_revocation_must_state_a_reason(keyring, ada):
    keyring.grant(ada, "google-drive")
    with pytest.raises(ConnectorError, match="must state a reason"):
        keyring.revoke(ada.urn, "google-drive", reason="  ")


def test_offboarding_revokes_everything_one_principal_authorised(keyring, okta):
    keyring.grant(okta, "github", scopes=["repo.read", "pr.read"])
    keyring.grant(okta, "jira")
    assert keyring.revoke_all(okta.urn, reason="left the company") == 2
    assert keyring.capabilities_of(okta.urn) == ()


def test_a_lapsed_grant_becomes_an_event_rather_than_an_absent_row(keyring, ada):
    keyring.grant(ada, "google-drive", ttl=100.0, now=1000.0)
    lapsed = keyring.sweep(now=2000.0)
    assert [grant.connector_id for grant in lapsed] == ["google-drive"]
    assert lapsed[0].status is GrantStatus.EXPIRED


def test_a_grant_nearing_expiry_is_reported_before_it_breaks(keyring, ada):
    keyring.grant(ada, "google-drive", ttl=86400.0, now=1000.0)
    assert [g.connector_id for g in keyring.expiring(now=1000.0)] == ["google-drive"]


def test_re_granting_supersedes_rather_than_overwrites(keyring, okta):
    keyring.grant(okta, "github", scopes=["repo.read", "pr.read"])
    keyring.grant(okta, "github", scopes=["repo.read", "pr.read", "actions.read"])
    history = keyring.history(okta.urn, "github")
    assert [grant.status for grant in history] == [
        GrantStatus.ACTIVE, GrantStatus.SUPERSEDED, GrantStatus.ACTIVE
    ]


def test_the_keyring_can_be_asked_who_had_access_at_any_point(keyring, ada):
    keyring.grant(ada, "google-drive")
    after_grant = keyring.status()["sequence"]
    keyring.revoke(ada.urn, "google-drive", reason="rotated")

    at_the_time = keyring.at(after_grant)
    assert at_the_time[("google-drive", ada.urn)].status is GrantStatus.ACTIVE
    assert keyring.grant_for(ada.urn, "google-drive") is None


def test_no_secret_is_stored_in_a_grant(keyring, ada):
    activation = keyring.grant(ada, "google-drive", credential="ya29.super-secret")
    rendered = str(activation.grant.to_dict())
    assert "ya29.super-secret" not in rendered
    assert activation.grant.credential_digest


def test_a_rotated_credential_is_detected_without_holding_the_old_one(keyring, ada):
    keyring.grant(ada, "google-drive", credential="token-v1")
    assert keyring.rotated(ada.urn, "google-drive", "token-v2")
    assert not keyring.rotated(ada.urn, "google-drive", "token-v1")


def test_every_transition_is_emitted_for_the_ledger(ada):
    seen: list[tuple[KeyringEvent, str]] = []
    keyring = Keyring(builtin_catalogue(),
                      emit=lambda event, grant: seen.append((event, grant.connector_id)))
    keyring.grant(ada, "google-drive")
    keyring.revoke(ada.urn, "google-drive", reason="done")
    assert seen == [
        (KeyringEvent.GRANTED, "google-drive"),
        (KeyringEvent.REVOKED, "google-drive"),
    ]


# --- the headline --------------------------------------------------------


def test_one_google_sign_in_lights_up_its_connectors_with_no_configuration(
    keypair, keyring
):
    """The behaviour the whole design exists to produce.

    An ID token arrives. Nothing is configured, no file is edited, nothing
    restarts — and the platform can now read documents and source control.
    """
    registry = ProviderRegistry(google(audience="slpie.example.com",
                                       keys=[keypair.public()]))

    ada = registry.authenticate(
        "google",
        id_token(keypair, issuer="https://accounts.google.com",
                 audience="slpie.example.com"),
    )

    unlocked = keyring.available(ada)
    # Exactly the connectors a *Google* identity vouches for. Not GitHub — a
    # Google account is not a GitHub account, and pretending otherwise is how
    # integration platforms end up over-granting.
    assert {spec.id for spec in unlocked} == {"google-drive"}

    for spec in unlocked:
        keyring.grant(ada, spec.id)

    assert keyring.can(ada.urn, "document-read")
    assert keyring.status()["active"] == len(unlocked)


def test_an_enterprise_sign_in_unlocks_the_whole_working_set(keyring, okta):
    """The commercial case: one IdP, and the platform is connected.

    This is why the seam is at the identity provider. Onboarding a customer's
    Okta or Entra tenant is a configuration entry, and it lights up source
    control, registries, issue tracking and observability at once.
    """
    unlocked = {spec.id for spec in keyring.available(okta)}
    assert {"github", "gitlab", "npm", "pypi", "jira", "datadog"} <= unlocked

    for connector_id in sorted(unlocked):
        keyring.grant(okta, connector_id)

    capabilities = set(keyring.capabilities_of(okta.urn))
    assert {"scm-read", "registry-read", "issue-read", "runtime-trace"} <= capabilities


def test_an_agent_inherits_only_what_was_granted_to_it(keypair, keyring):
    """Delegation carries authority forward without widening it."""
    ada = google(audience="slpie.example.com", keys=[keypair.public()]).authenticate(
        id_token(keypair, issuer="https://accounts.google.com",
                 audience="slpie.example.com")
    )
    keyring.grant(ada, "google-drive")

    agent = ada.delegate("scanner-1")
    assert agent.method is ada.method     # it can reach what its human could
    assert keyring.capabilities_of(agent.urn) == ()

    keyring.grant(agent, "google-drive")
    assert keyring.can(agent.urn, "document-read")
    assert keyring.grant_for(agent.urn, "google-drive").on_behalf_of == ada.urn
