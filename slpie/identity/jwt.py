"""JWS verification, in the standard library alone.

Signing in with Google or Apple means verifying an RS256-signed ID token, and
the reflex is to reach for `PyJWT` and `cryptography`. That would put a
compiled, CVE-bearing dependency in the kernel to perform an operation that is,
for *verification*, one line of arithmetic:

    signature ** e mod n

`pow(int, int, int)` is a native, fast modular exponentiation. There is no
private key here and no key generation — the hard parts of RSA are all on the
signing side, which is Google's problem, not ours. What remains is PKCS#1 v1.5
padding, a DER prefix, and a digest comparison, all of which `hashlib` and
`hmac` already provide.

So this module verifies real Google and Apple ID tokens with nothing installed.
That is not a party trick: it is what lets the kernel stay stdlib-only while
still being the thing an enterprise actually logs into.

**What is deliberately absent.** There is no signing. There is no `alg: none`
path — a token whose header says `none` is rejected before anything else looks
at it, because accepting it is the oldest JWT vulnerability there is. And the
algorithm is taken from the *key*, never from the token's own header, so a
token cannot nominate HS256 against an RSA public key and have its "signature"
checked as an HMAC of that key's public material.

ES256 is not implemented. Apple and Google both sign with RS256, so this covers
them; an ES256 issuer is refused with a message saying so rather than being
waved through.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..errors import SlpieError


class JwtError(SlpieError):
    """A token was malformed, unverifiable, or verified-but-unacceptable."""


class SignatureInvalid(JwtError):
    """The signature did not verify against the key it names."""


class TokenExpired(JwtError):
    """The token verified, but its validity window has passed."""


class ClaimRejected(JwtError):
    """The token verified, but a claim is not one we accept."""


#: Algorithms this module can verify. `none` is not merely unlisted — it is
#: rejected by name below, because a mis-typed allow-list is exactly how it gets
#: back in.
SUPPORTED = frozenset({"RS256", "RS384", "RS512", "HS256", "HS384", "HS512"})

#: DER-encoded DigestInfo prefixes for PKCS#1 v1.5. These encode "the following
#: bytes are a SHA-n digest", and checking them is what stops a signature over
#: one digest being replayed as a signature over another.
_DIGEST_INFO: dict[str, bytes] = {
    "sha256": bytes.fromhex("3031300d060960864801650304020105000420"),
    "sha384": bytes.fromhex("3041300d060960864801650304020205000430"),
    "sha512": bytes.fromhex("3051300d060960864801650304020305000440"),
}

_HASH_FOR: dict[str, str] = {
    "RS256": "sha256", "RS384": "sha384", "RS512": "sha512",
    "HS256": "sha256", "HS384": "sha384", "HS512": "sha512",
}

#: Tolerance for clock disagreement between us and the issuer. Sixty seconds is
#: the conventional figure; larger turns "expired" into a suggestion.
DEFAULT_LEEWAY = 60.0


def b64url_decode(value: str) -> bytes:
    """Base64url without padding, which is what JOSE actually emits."""
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except Exception as exc:  # noqa: BLE001 - binascii raises several types
        raise JwtError(f"not valid base64url: {value[:24]!r}") from exc


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_int(value: str) -> int:
    """A JWK's big-endian base64url integer — `n` and `e` arrive this way."""
    return int.from_bytes(b64url_decode(value), "big")


@dataclass(frozen=True, slots=True)
class Key:
    """One verification key. Its `alg` is authoritative, the token's is not.

    A key that did not state an algorithm is pinned to the one implied by its
    type, rather than inheriting whatever the token proposes. Letting the token
    choose is the "algorithm confusion" attack: present an RSA public key as an
    HMAC secret and sign with it, since the public key is, by definition,
    public.
    """

    kid: str
    kind: str                       # "RSA" | "oct"
    alg: str = ""
    n: int = 0
    e: int = 0
    secret: bytes = b""
    issuer: str = ""

    def __post_init__(self) -> None:
        if self.kind == "RSA" and (self.n <= 0 or self.e <= 0):
            raise JwtError(f"RSA key {self.kid!r} is missing its modulus or exponent")
        if self.kind == "oct" and not self.secret:
            raise JwtError(f"symmetric key {self.kid!r} has no secret")
        if self.kind not in ("RSA", "oct"):
            raise JwtError(
                f"key {self.kid!r} is of unsupported type {self.kind!r}; "
                f"only RSA and oct are verifiable here"
            )
        if not self.alg:
            object.__setattr__(self, "alg", "RS256" if self.kind == "RSA" else "HS256")
        if self.alg not in SUPPORTED:
            raise JwtError(
                f"key {self.kid!r} names algorithm {self.alg!r}, which this "
                f"verifier does not implement"
            )

    @property
    def modulus_bytes(self) -> int:
        return (self.n.bit_length() + 7) // 8

    @classmethod
    def from_jwk(cls, jwk: Mapping[str, Any], *, issuer: str = "") -> "Key":
        kind = str(jwk.get("kty") or "")
        if kind == "RSA":
            return cls(
                kid=str(jwk.get("kid") or ""), kind="RSA",
                alg=str(jwk.get("alg") or ""),
                n=b64url_int(str(jwk["n"])), e=b64url_int(str(jwk["e"])),
                issuer=issuer,
            )
        if kind == "oct":
            return cls(
                kid=str(jwk.get("kid") or ""), kind="oct",
                alg=str(jwk.get("alg") or ""),
                secret=b64url_decode(str(jwk["k"])), issuer=issuer,
            )
        raise JwtError(
            f"JWK of type {kind!r} cannot be verified here; RS256 covers Google "
            f"and Apple, and ES256 is not implemented"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kid": self.kid, "kty": self.kind, "alg": self.alg,
            "issuer": self.issuer, "bits": self.n.bit_length() or len(self.secret) * 8,
        }


def verify_pkcs1_v15(digest: bytes, signature: bytes, key: Key, hash_name: str) -> bool:
    """RSASSA-PKCS1-v1_5 verification, from the arithmetic up.

    Recovers the encoded message with one modular exponentiation and then checks
    it has *exactly* the shape RFC 8017 §9.2 specifies:

        0x00 || 0x01 || 0xFF…(≥8) || 0x00 || DigestInfo || H

    Reconstructing the expected block and comparing whole is deliberate. The
    classic Bleichenbacher forgery works against verifiers that *parse* the
    padding and stop at the digest, because a lax parser will accept trailing
    garbage on a low-exponent key. Comparing the entire block leaves nowhere to
    put it.
    """
    if key.kind != "RSA":
        return False
    size = key.modulus_bytes
    if len(signature) != size:
        # A signature of the wrong length is malformed, not merely wrong; int
        # conversion would silently zero-extend it.
        return False

    encoded = int.from_bytes(signature, "big")
    if encoded >= key.n:
        return False

    recovered = pow(encoded, key.e, key.n).to_bytes(size, "big")

    prefix = _DIGEST_INFO[hash_name]
    padding_length = size - len(prefix) - len(digest) - 3
    if padding_length < 8:
        # RFC 8017 requires at least eight 0xFF octets. A modulus too small to
        # hold them is too small to trust.
        return False

    expected = b"\x00\x01" + b"\xff" * padding_length + b"\x00" + prefix + digest
    return hmac.compare_digest(recovered, expected)


@dataclass(frozen=True, slots=True)
class Token:
    """A parsed JWS, before anybody has decided whether to believe it."""

    raw: str
    header: Mapping[str, Any]
    payload: Mapping[str, Any]
    signature: bytes
    signing_input: bytes

    @property
    def alg(self) -> str:
        return str(self.header.get("alg") or "")

    @property
    def kid(self) -> str:
        return str(self.header.get("kid") or "")

    @property
    def issuer(self) -> str:
        return str(self.payload.get("iss") or "")

    @property
    def subject(self) -> str:
        return str(self.payload.get("sub") or "")

    def audiences(self) -> tuple[str, ...]:
        """`aud` is a string or an array, and both forms appear in the wild."""
        raw = self.payload.get("aud")
        if isinstance(raw, str):
            return (raw,)
        if isinstance(raw, (list, tuple)):
            return tuple(str(entry) for entry in raw)
        return ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "alg": self.alg, "kid": self.kid, "iss": self.issuer,
            "sub": self.subject, "aud": list(self.audiences()),
        }


def parse(token: str) -> Token:
    """Split and decode a compact JWS. **This verifies nothing.**

    Named `parse` rather than `decode` on purpose: several breaches have come
    from a function called `decode` being read as a function that checks
    something. Use :func:`verify` for that.
    """
    parts = token.strip().split(".")
    if len(parts) != 3:
        raise JwtError(
            f"a compact JWS has three dot-separated parts; this has {len(parts)}"
        )
    head, body, signature = parts
    if not head or not body:
        raise JwtError("the header and payload of a JWS cannot be empty")

    try:
        header = json.loads(b64url_decode(head))
        payload = json.loads(b64url_decode(body))
    except json.JSONDecodeError as exc:
        raise JwtError(f"the header or payload is not JSON: {exc}") from exc

    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise JwtError("the header and payload of a JWS must both be objects")

    return Token(
        raw=token, header=header, payload=payload,
        signature=b64url_decode(signature),
        signing_input=f"{head}.{body}".encode("ascii"),
    )


def verify_signature(token: Token, key: Key) -> None:
    """Check the signature. Raises rather than returning a boolean.

    A boolean gets ignored; an exception does not. Given how often "verify"
    functions are called without their result being read, this is worth the
    slight awkwardness.
    """
    algorithm = key.alg   # the key's, never the token's
    declared = token.alg

    if declared.lower() == "none":
        raise SignatureInvalid(
            "the token declares alg=none; an unsigned token is not a credential"
        )
    if declared not in SUPPORTED:
        raise SignatureInvalid(
            f"token algorithm {declared!r} is not verifiable here "
            f"(supported: {', '.join(sorted(SUPPORTED))})"
        )
    if declared != algorithm:
        # The confusion attack, refused explicitly so the log says what happened.
        raise SignatureInvalid(
            f"token declares {declared!r} but key {key.kid or '<unnamed>'!r} is "
            f"an {algorithm!r} key; the key decides, and they disagree"
        )

    hash_name = _HASH_FOR[algorithm]
    if algorithm.startswith("HS"):
        expected = hmac.new(key.secret, token.signing_input, hash_name).digest()
        if not hmac.compare_digest(expected, token.signature):
            raise SignatureInvalid("HMAC signature does not match")
        return

    digest = hashlib.new(hash_name, token.signing_input).digest()
    if not verify_pkcs1_v15(digest, token.signature, key, hash_name):
        raise SignatureInvalid(
            f"RSA signature does not verify against key {key.kid or '<unnamed>'!r}"
        )


def verify(
    token: str,
    keys: Iterable[Key],
    *,
    issuer: str = "",
    audience: str | Sequence[str] = "",
    nonce: str = "",
    now: float | None = None,
    leeway: float = DEFAULT_LEEWAY,
    require: Sequence[str] = ("exp", "iat", "iss", "sub"),
) -> Token:
    """Verify a token end to end, and return it only if every check passed.

    Order matters and is the reverse of what reads naturally: the signature is
    checked *before* the claims, so an attacker cannot learn anything from the
    error message about a token they did not sign.
    """
    parsed = parse(token)
    moment = time.time() if now is None else now

    candidates = [key for key in keys]
    if not candidates:
        raise SignatureInvalid(
            "no verification keys were supplied; a token cannot be verified "
            "against nothing, and is therefore refused"
        )

    # Match on kid when the token names one. When it does not — Apple omits it
    # on some paths — every key is tried, which is correct but is why key sets
    # are kept small and per-issuer.
    if parsed.kid:
        matching = [key for key in candidates if key.kid == parsed.kid]
        if not matching:
            raise SignatureInvalid(
                f"the token was signed with key {parsed.kid!r}, which is not in "
                f"the key set; the issuer may have rotated and the cache is stale"
            )
        candidates = matching

    last: Exception | None = None
    for key in candidates:
        try:
            verify_signature(parsed, key)
            break
        except SignatureInvalid as exc:
            last = exc
    else:
        raise SignatureInvalid(str(last) if last else "no key verified the signature")

    _check_claims(
        parsed, issuer=issuer, audience=audience, nonce=nonce,
        now=moment, leeway=leeway, require=require,
    )
    return parsed


def _check_claims(
    token: Token,
    *,
    issuer: str,
    audience: str | Sequence[str],
    nonce: str,
    now: float,
    leeway: float,
    require: Sequence[str],
) -> None:
    payload = token.payload

    missing = [claim for claim in require if claim not in payload]
    if missing:
        raise ClaimRejected(
            f"the token verified but is missing required claims: {', '.join(missing)}"
        )

    expiry = payload.get("exp")
    if expiry is not None:
        if not isinstance(expiry, (int, float)):
            raise ClaimRejected("`exp` must be a number of seconds since the epoch")
        if now > float(expiry) + leeway:
            raise TokenExpired(
                f"the token expired {now - float(expiry):.0f}s ago "
                f"(leeway {leeway:.0f}s)"
            )

    not_before = payload.get("nbf")
    if isinstance(not_before, (int, float)) and now + leeway < float(not_before):
        raise ClaimRejected(
            f"the token is not valid for another {float(not_before) - now:.0f}s"
        )

    issued = payload.get("iat")
    if isinstance(issued, (int, float)) and now + leeway < float(issued):
        # An issuance time in the future means our clock or theirs is wrong;
        # either way the validity window cannot be trusted.
        raise ClaimRejected("the token claims to have been issued in the future")

    if issuer and token.issuer != issuer:
        raise ClaimRejected(
            f"the token was issued by {token.issuer!r}, not {issuer!r}"
        )

    if audience:
        wanted = {audience} if isinstance(audience, str) else set(audience)
        if not wanted & set(token.audiences()):
            raise ClaimRejected(
                f"the token's audience {list(token.audiences())} does not include "
                f"any of {sorted(wanted)}; it was minted for a different application"
            )

    if nonce and str(payload.get("nonce") or "") != nonce:
        # Without this a valid token captured from another sign-in replays here.
        raise ClaimRejected("the token's nonce does not match the one we issued")
