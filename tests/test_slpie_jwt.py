"""JWS verification, checked against real RSA signatures.

There is no mock key and no captured fixture here. The test helper generates a
genuine RSA keypair with Miller–Rabin primality testing and signs with PKCS#1
v1.5, so `verify_pkcs1_v15` is verifying arithmetic that another piece of
arithmetic actually produced. A fixture would prove only that the fixture still
decodes.

The attack cases are the point of the file. A verifier that accepts `alg: none`,
or lets a token nominate HS256 against an RSA public key, is not a verifier —
and both mistakes look completely reasonable in review.
"""

from __future__ import annotations

import hashlib
import json
import random
import time

import pytest

from slpie.identity.jwt import (
    DEFAULT_LEEWAY,
    ClaimRejected,
    JwtError,
    Key,
    SignatureInvalid,
    TokenExpired,
    b64url_encode,
    parse,
    verify,
    verify_signature,
)

# --- a real RSA keypair, generated here ---------------------------------


def _probably_prime(candidate: int, *, rounds: int = 24, rng: random.Random) -> bool:
    """Miller–Rabin. Enough rounds that a composite slipping through is fantasy."""
    if candidate < 2:
        return False
    for small in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if candidate % small == 0:
            return candidate == small

    remainder, exponent = candidate - 1, 0
    while remainder % 2 == 0:
        remainder //= 2
        exponent += 1

    for _ in range(rounds):
        witness = rng.randrange(2, candidate - 1)
        value = pow(witness, remainder, candidate)
        if value in (1, candidate - 1):
            continue
        for _ in range(exponent - 1):
            value = value * value % candidate
            if value == candidate - 1:
                break
        else:
            return False
    return True


def _prime(bits: int, rng: random.Random) -> int:
    while True:
        candidate = rng.getrandbits(bits) | (1 << (bits - 1)) | 1
        if _probably_prime(candidate, rng=rng):
            return candidate


class RsaKeypair:
    """A real, small RSA key. 1024 bits: genuine arithmetic, quick to generate."""

    def __init__(self, bits: int = 1024, seed: int = 20260729) -> None:
        rng = random.Random(seed)
        half = bits // 2
        while True:
            p, q = _prime(half, rng), _prime(half, rng)
            if p == q:
                continue
            self.n = p * q
            if self.n.bit_length() != bits:
                continue
            self.e = 65537
            totient = (p - 1) * (q - 1)
            if totient % self.e == 0:
                continue
            self.d = pow(self.e, -1, totient)
            break
        self.size = (self.n.bit_length() + 7) // 8

    def public(self, *, kid: str = "test-key", alg: str = "RS256", issuer: str = "") -> Key:
        return Key(kid=kid, kind="RSA", alg=alg, n=self.n, e=self.e, issuer=issuer)

    def sign(self, message: bytes, *, hash_name: str = "sha256") -> bytes:
        """PKCS#1 v1.5 signing — the mirror of what the verifier undoes."""
        prefixes = {
            "sha256": bytes.fromhex("3031300d060960864801650304020105000420"),
            "sha384": bytes.fromhex("3041300d060960864801650304020205000430"),
            "sha512": bytes.fromhex("3051300d060960864801650304020305000440"),
        }
        digest = hashlib.new(hash_name, message).digest()
        block = prefixes[hash_name] + digest
        padding = b"\xff" * (self.size - len(block) - 3)
        encoded = b"\x00\x01" + padding + b"\x00" + block
        signed = pow(int.from_bytes(encoded, "big"), self.d, self.n)
        return signed.to_bytes(self.size, "big")

    def token(self, payload: dict, *, header: dict | None = None,
              hash_name: str = "sha256") -> str:
        head = {"alg": "RS256", "typ": "JWT", "kid": "test-key"}
        head.update(header or {})
        parts = (
            b64url_encode(json.dumps(head, separators=(",", ":")).encode())
            + "."
            + b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
        )
        return parts + "." + b64url_encode(self.sign(parts.encode(), hash_name=hash_name))


@pytest.fixture(scope="module")
def keypair():
    return RsaKeypair()


@pytest.fixture
def claims():
    now = time.time()
    return {
        "iss": "https://accounts.google.com",
        "sub": "1078412",
        "aud": "slpie.example.com",
        "exp": now + 3600,
        "iat": now,
        "email": "ada@example.com",
        "email_verified": True,
    }


# --- parsing -------------------------------------------------------------


def test_a_token_without_three_parts_is_refused():
    with pytest.raises(JwtError, match="three dot-separated parts"):
        parse("only.two")


def test_a_token_whose_payload_is_not_json_is_refused():
    with pytest.raises(JwtError, match="not JSON"):
        parse(f"{b64url_encode(b'{}')}.{b64url_encode(b'not json')}.x")


def test_a_token_whose_payload_is_a_bare_array_is_refused():
    with pytest.raises(JwtError, match="must both be objects"):
        parse(f"{b64url_encode(b'{}')}.{b64url_encode(b'[1,2]')}.x")


def test_an_audience_is_read_whether_it_is_a_string_or_an_array(keypair, claims):
    single = parse(keypair.token(claims))
    assert single.audiences() == ("slpie.example.com",)
    many = parse(keypair.token({**claims, "aud": ["a", "b"]}))
    assert many.audiences() == ("a", "b")


# --- signature verification ---------------------------------------------


def test_a_genuine_rs256_signature_verifies(keypair, claims):
    token = verify(
        keypair.token(claims), [keypair.public()],
        issuer="https://accounts.google.com", audience="slpie.example.com",
    )
    assert token.subject == "1078412"


@pytest.mark.parametrize("algorithm,hash_name",
                         [("RS256", "sha256"), ("RS384", "sha384"), ("RS512", "sha512")])
def test_every_supported_rsa_algorithm_verifies(keypair, claims, algorithm, hash_name):
    token = keypair.token(claims, header={"alg": algorithm}, hash_name=hash_name)
    assert verify(token, [keypair.public(alg=algorithm)]).subject == "1078412"


def test_a_tampered_payload_fails_even_though_it_decodes(keypair, claims):
    head, payload, signature = keypair.token(claims).split(".")
    forged = b64url_encode(json.dumps({**claims, "sub": "attacker"}).encode())
    with pytest.raises(SignatureInvalid):
        verify(f"{head}.{forged}.{signature}", [keypair.public()])


def test_a_signature_of_the_wrong_length_is_refused_rather_than_zero_extended(
    keypair, claims
):
    head, payload, signature = keypair.token(claims).split(".")
    with pytest.raises(SignatureInvalid):
        verify(f"{head}.{payload}.{signature[:-8]}", [keypair.public()])


def test_a_signature_larger_than_the_modulus_is_refused(keypair, claims):
    parsed = parse(keypair.token(claims))
    oversized = (keypair.n + 1).to_bytes(keypair.size + 1, "big")[: keypair.size]
    forged = f"{parsed.signing_input.decode()}.{b64url_encode(oversized)}"
    with pytest.raises(SignatureInvalid):
        verify(forged, [keypair.public()])


def test_a_second_keypair_does_not_verify_the_first_ones_tokens(claims):
    signer, other = RsaKeypair(seed=1), RsaKeypair(seed=2)
    with pytest.raises(SignatureInvalid):
        verify(signer.token(claims), [other.public()])


# --- the attacks ---------------------------------------------------------


def test_alg_none_is_refused_by_name(keypair, claims):
    head = b64url_encode(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    body = b64url_encode(json.dumps(claims).encode())
    with pytest.raises(SignatureInvalid, match="alg=none"):
        verify(f"{head}.{body}.", [keypair.public()])


def test_a_token_cannot_nominate_hmac_against_an_rsa_key(keypair, claims):
    """Algorithm confusion: the public key is public, so it is a known HMAC secret."""
    import hmac as _hmac

    head = b64url_encode(json.dumps({"alg": "HS256", "kid": "test-key"}).encode())
    body = b64url_encode(json.dumps(claims).encode())
    public_material = keypair.n.to_bytes(keypair.size, "big")
    forged = _hmac.new(public_material, f"{head}.{body}".encode(), "sha256").digest()

    with pytest.raises(SignatureInvalid, match="the key decides"):
        verify(f"{head}.{body}.{b64url_encode(forged)}", [keypair.public()])


def test_an_unsupported_algorithm_is_refused_rather_than_ignored(keypair, claims):
    head = b64url_encode(json.dumps({"alg": "ES256", "kid": "test-key"}).encode())
    body = b64url_encode(json.dumps(claims).encode())
    with pytest.raises(SignatureInvalid, match="not verifiable here"):
        verify(f"{head}.{body}.{b64url_encode(b'x' * 64)}", [keypair.public()])


def test_verifying_against_no_keys_refuses_instead_of_passing(keypair, claims):
    with pytest.raises(SignatureInvalid, match="no verification keys"):
        verify(keypair.token(claims), [])


def test_an_unknown_kid_says_the_issuer_may_have_rotated(keypair, claims):
    with pytest.raises(SignatureInvalid, match="rotated"):
        verify(keypair.token(claims), [keypair.public(kid="another-key")])


# --- claim checking ------------------------------------------------------


def test_an_expired_token_is_refused_with_how_long_ago(keypair, claims):
    stale = {**claims, "exp": time.time() - 7200, "iat": time.time() - 10800}
    with pytest.raises(TokenExpired, match="expired"):
        verify(keypair.token(stale), [keypair.public()])


def test_a_token_just_inside_the_leeway_still_verifies(keypair, claims):
    edge = {**claims, "exp": time.time() - (DEFAULT_LEEWAY / 2)}
    assert verify(keypair.token(edge), [keypair.public()]).subject == "1078412"


def test_a_token_not_yet_valid_is_refused(keypair, claims):
    future = {**claims, "nbf": time.time() + 3600}
    with pytest.raises(ClaimRejected, match="not valid for another"):
        verify(keypair.token(future), [keypair.public()])


def test_a_token_issued_in_the_future_is_refused(keypair, claims):
    skewed = {**claims, "iat": time.time() + 7200}
    with pytest.raises(ClaimRejected, match="issued in the future"):
        verify(keypair.token(skewed), [keypair.public()])


def test_the_wrong_issuer_is_refused(keypair, claims):
    with pytest.raises(ClaimRejected, match="not 'https://appleid.apple.com'"):
        verify(keypair.token(claims), [keypair.public()],
               issuer="https://appleid.apple.com")


def test_a_token_minted_for_another_application_is_refused(keypair, claims):
    with pytest.raises(ClaimRejected, match="different application"):
        verify(keypair.token(claims), [keypair.public()], audience="someone.else.com")


def test_one_audience_out_of_several_accepted_is_enough(keypair, claims):
    token = verify(keypair.token(claims), [keypair.public()],
                   audience=["other.com", "slpie.example.com"])
    assert token.subject == "1078412"


def test_a_replayed_token_from_another_sign_in_is_caught_by_the_nonce(keypair, claims):
    with pytest.raises(ClaimRejected, match="nonce"):
        verify(keypair.token({**claims, "nonce": "theirs"}), [keypair.public()],
               nonce="ours")


def test_a_missing_required_claim_is_named(keypair, claims):
    claims.pop("sub")
    with pytest.raises(ClaimRejected, match="missing required claims: sub"):
        verify(keypair.token(claims), [keypair.public()])


def test_the_signature_is_checked_before_the_claims(keypair, claims):
    """An attacker must not learn which claim would have failed."""
    expired_and_forged = {**claims, "exp": time.time() - 9999}
    head, payload, _ = keypair.token(expired_and_forged).split(".")
    with pytest.raises(SignatureInvalid):
        verify(f"{head}.{payload}.{b64url_encode(b'0' * keypair.size)}",
               [keypair.public()])


# --- JWK ingestion -------------------------------------------------------


def test_a_google_shaped_jwk_document_loads(keypair):
    from slpie.identity.jwt import b64url_encode as enc

    jwk = {
        "kty": "RSA", "kid": "test-key", "alg": "RS256", "use": "sig",
        "n": enc(keypair.n.to_bytes(keypair.size, "big")),
        "e": enc((65537).to_bytes(3, "big")),
    }
    key = Key.from_jwk(jwk, issuer="https://accounts.google.com")
    assert key.n == keypair.n
    assert key.issuer == "https://accounts.google.com"


def test_an_elliptic_curve_jwk_is_refused_with_a_reason():
    with pytest.raises(JwtError, match="ES256 is not implemented"):
        Key.from_jwk({"kty": "EC", "crv": "P-256", "x": "AA", "y": "AA"})


def test_a_key_without_an_algorithm_is_pinned_rather_than_left_open():
    assert Key(kid="k", kind="RSA", n=3233, e=17).alg == "RS256"


def test_a_symmetric_key_with_no_secret_is_refused():
    with pytest.raises(JwtError, match="no secret"):
        Key(kid="k", kind="oct")
