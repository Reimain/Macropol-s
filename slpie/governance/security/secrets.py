"""Secrets in source, found by pattern and by entropy — and never echoed.

Two detectors, because each catches what the other cannot:

* **patterns** — issuer-specific shapes (`AKIA…`, `ghp_…`, `sk-…`, a PEM header).
  High precision: an AWS access key id is not plausibly anything else, so these
  are reported at CRITICAL without hedging.
* **entropy** — a long high-entropy string assigned to a name that sounds like a
  credential. Lower precision by construction, so it is reported at HIGH and says
  in its own detail that it is a heuristic. A detector that presented a guess with
  the same confidence as a match would train people to ignore both.

**The finding never contains the secret.** This is the decision that governs the
whole module and it is easy to get wrong: an excerpt is exactly what `Evidence`
carries everywhere else in the platform, and a secret finding built the ordinary
way would copy the credential into the ledger, the SBOM, the HTTP response and
every report — turning a scanner into a second place the secret leaks. So
`Evidence.excerpt` here holds a redacted rendering, and the finding cites the
file and line so a human can go and look at the real thing where it already is.

The suppression path matters too. A test fixture's fake key is a genuine false
positive, and a scanner with no way to say so gets switched off within a week —
so `allow` patterns are read from the caller's facts and the rule states which
allowance silenced what.
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable, Mapping

from ...domain.evidence import Evidence, EvidenceKind, SourceLocation
from ...domain.finding import Finding, FindingKind, Remediation
from ...domain.lifecycle import Severity
from ..rules import Rule, RuleContext, RuleSet

#: Issuer-specific shapes. Each is precise enough that a match is a credential
#: rather than a coincidence, which is what justifies CRITICAL without a hedge.
PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("aws-access-key-id", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", "an AWS access key id"),
    ("github-token", r"\bgh[pousr]_[A-Za-z0-9]{36,}\b", "a GitHub token"),
    ("slack-token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b", "a Slack token"),
    ("stripe-key", r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b", "a Stripe API key"),
    ("google-api-key", r"\bAIza[0-9A-Za-z_-]{35}\b", "a Google API key"),
    ("openai-key", r"\bsk-[A-Za-z0-9]{20,}\b", "an OpenAI-style API key"),
    ("private-key", r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----",
     "a private key block"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
     "a signed JWT"),
    ("basic-auth-url", r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/:@]+:[^\s/@]{4,}@",
     "credentials embedded in a URL"),
)

#: An assignment to a name that sounds like a credential. The *name* is half the
#: signal — a high-entropy string assigned to `HASH` or `SALT` is usually neither
#: a secret nor interesting.
ASSIGNMENT = re.compile(
    r"""(?P<name>[A-Za-z_][A-Za-z0-9_.\-]{2,40})\s*[:=]\s*
        (?P<quote>['"]?)(?P<value>[A-Za-z0-9+/=_\-.]{16,120})(?P=quote)""",
    re.VERBOSE,
)

CREDENTIAL_NAMES = re.compile(
    r"(?:secret|token|password|passwd|pwd|api[_-]?key|apikey|access[_-]?key|"
    r"private[_-]?key|client[_-]?secret|auth|credential|bearer)",
    re.IGNORECASE,
)

#: Below this, a string is structured text rather than a key. 4.0 bits/char keeps
#: base64-ish secrets and drops ordinary identifiers and English.
ENTROPY_FLOOR = 4.0

#: Values that are obviously placeholders. These are not "probably fine" — they
#: are literally the strings people write to mean "not a real one".
PLACEHOLDER = re.compile(
    r"^(?:x{4,}|0+|1234|changeme|placeholder|example|dummy|fake|test|sample|"
    r"your[_-]?\w+|<[^>]+>|\$\{[^}]+\}|\{\{[^}]+\}\})$",
    re.IGNORECASE,
)

#: Directory *names* a credential-shaped string is expected to live in.
#:
#: Matched as whole path segments, never as substrings. The substring form was
#: written first and was wrong in the dangerous direction: `/test` matched
#: `/test_probe0`, and would equally match a real directory called
#: `/testing-production-keys/` — so a live credential in it was silently
#: downgraded to LOW and dropped out of any `--severity critical` gate. An
#: over-broad allowance is worse than no allowance, because it fails quietly.
EXPECTED = frozenset({
    "test", "tests", "testdata", "spec", "specs", "fixtures", "fixture",
    "example", "examples", "sample", "samples", "docs", "doc", "vendor",
    "testing", "__tests__", "mocks",
})


def shannon(text: str) -> float:
    """Bits of entropy per character."""
    if not text:
        return 0.0
    counts: dict[str, int] = {}
    for character in text:
        counts[character] = counts.get(character, 0) + 1
    length = len(text)
    return -sum(
        (count / length) * math.log2(count / length) for count in counts.values()
    )


def redact(value: str) -> str:
    """A secret, rendered so the rendering is not itself a secret.

    Four leading characters is enough for a human to recognise which credential
    is meant and far too little to use. The length is kept because "a 40-char
    value" helps identify it, and the tail is dropped entirely.
    """
    head = value[:4]
    return f"{head}{'*' * 8} ({len(value)} chars)"


def _allowed(context: RuleContext, uri: str, value: str) -> str:
    """The allowance that silences this hit, or empty.

    Returns the *reason* rather than a boolean so the rule can say which
    allowance applied. A scanner that silently drops a hit is one nobody can
    audit, and the first question after a real leak is always "why did we not
    see this".
    """
    for pattern in context.fact("secret_allow", ()) or ():
        try:
            if re.search(str(pattern), uri) or re.search(str(pattern), value):
                return f"matched the configured allowance {pattern!r}"
        except re.error:
            continue
    # Whole segments. `path.split("/")` on a uri gives the directory names, and
    # comparing those is what stops `tests` from matching `test_probe0` or
    # `testing-production-keys`.
    segments = {part.lower() for part in uri.split("/") if part}
    shared = segments & EXPECTED
    if shared:
        return f"lives in a {sorted(shared)[0]!r} directory, where fixtures are expected"
    return ""


def _evidence(uri: str, line: int, rendering: str) -> Evidence:
    """Evidence for a secret. The excerpt is redacted, always."""
    return Evidence(
        kind=EvidenceKind.STATIC_IMPORT,
        location=SourceLocation(uri, line=line),
        extractor="governance.secrets",
        excerpt=rendering,
    )


def _lines(text: str) -> Iterable[tuple[int, str]]:
    return enumerate(text.splitlines(), start=1)


def pattern_secret_rule() -> Rule:
    """Issuer-specific credential shapes in scanned source."""
    compiled = tuple((name, re.compile(pattern), what) for name, pattern, what in PATTERNS)

    def matches(context: RuleContext) -> bool:
        return bool(context.sources)

    def evaluate(context: RuleContext) -> list[Finding]:
        raised: list[Finding] = []
        for uri, text in context.sources.items():
            for line, content in _lines(text):
                for name, expression, what in compiled:
                    found = expression.search(content)
                    if found is None:
                        continue
                    value = found.group(0)
                    excuse = _allowed(context, uri, value)
                    raised.append(Finding(
                        kind=FindingKind.SECRET_EXPOSED,
                        severity=Severity.LOW if excuse else Severity.CRITICAL,
                        subject=f"{uri}#L{line}",
                        title=(
                            f"{what} appears in "
                            f"{uri.rsplit('/', 1)[-1]}:{line}"
                        ),
                        detail=(
                            f"{what} was found in source. "
                            + (
                                f"Reported at low severity because it {excuse}."
                                if excuse else
                                "Treat it as compromised: rotate it, then remove "
                                "it from history — deleting the line leaves it in "
                                "every clone."
                            )
                        ),
                        code=name,
                        # Redacted. The finding travels to the ledger, the API and
                        # every report, and a scanner that copied the credential
                        # into all of them would be a second leak.
                        evidence=(_evidence(uri, line, redact(value)),),
                        remediation=Remediation(
                            summary=(
                                "rotate the credential, then purge it from version "
                                "control history"
                            ),
                            action="replace", breaking=False,
                        ),
                        rule_id="secret.pattern",
                        properties={
                            "detector": name, "redacted": redact(value),
                            "allowed_because": excuse,
                        },
                    ))
        return raised

    return Rule(
        id="secret.pattern",
        title="a credential appears in source",
        kind=FindingKind.SECRET_EXPOSED,
        severity=Severity.CRITICAL,
        evaluate=evaluate,
        matches=matches,
        remediation="rotate the credential, then purge it from history",
        description=(
            "issuer-specific shapes only, so a match is a credential rather than a "
            "coincidence; the finding carries a redaction, never the secret"
        ),
        tags=("security", "secrets"),
    )


def entropy_secret_rule(*, floor: float = ENTROPY_FLOOR) -> Rule:
    """High-entropy values assigned to credential-shaped names."""

    def matches(context: RuleContext) -> bool:
        return bool(context.sources)

    def evaluate(context: RuleContext) -> list[Finding]:
        raised: list[Finding] = []
        for uri, text in context.sources.items():
            for line, content in _lines(text):
                for found in ASSIGNMENT.finditer(content):
                    name = found.group("name")
                    value = found.group("value")
                    if not CREDENTIAL_NAMES.search(name):
                        continue
                    if PLACEHOLDER.match(value):
                        continue
                    entropy = shannon(value)
                    if entropy < floor:
                        continue

                    excuse = _allowed(context, uri, value)
                    raised.append(Finding(
                        kind=FindingKind.SECRET_EXPOSED,
                        severity=Severity.LOW if excuse else Severity.HIGH,
                        subject=f"{uri}#L{line}",
                        title=(
                            f"{name} is assigned a high-entropy value in "
                            f"{uri.rsplit('/', 1)[-1]}:{line}"
                        ),
                        detail=(
                            f"{len(value)} characters at {entropy:.1f} bits per "
                            f"character, assigned to a name that reads as a "
                            f"credential. This is a heuristic, not a match: it "
                            f"catches secrets no pattern knows about and it does "
                            f"flag the occasional hash."
                            + (f" Reported at low severity because it {excuse}."
                               if excuse else "")
                        ),
                        code="entropy",
                        evidence=(_evidence(uri, line, f"{name} = {redact(value)}"),),
                        remediation=Remediation(
                            summary=(
                                "if it is a credential, rotate it and move it to the "
                                "keyring; if it is not, add an allowance so the "
                                "scanner stays worth reading"
                            ),
                            action="replace",
                        ),
                        rule_id="secret.entropy",
                        properties={
                            "name": name, "entropy": round(entropy, 2),
                            "length": len(value), "redacted": redact(value),
                            "allowed_because": excuse,
                        },
                    ))
        return raised

    return Rule(
        id="secret.entropy",
        title="a high-entropy value is assigned to a credential-shaped name",
        kind=FindingKind.SECRET_EXPOSED,
        severity=Severity.HIGH,
        evaluate=evaluate,
        matches=matches,
        remediation="rotate it if real; add an allowance if not",
        description=(
            "a heuristic, and says so in every finding it raises — presenting a "
            "guess at the same confidence as a match teaches people to ignore both"
        ),
        tags=("security", "secrets", "heuristic"),
    )


def secret_rules(*, floor: float = ENTROPY_FLOOR) -> RuleSet:
    """The secrets family, as a set for registration."""
    return RuleSet(
        (pattern_secret_rule(), entropy_secret_rule(floor=floor)),
        name="secrets",
    )
