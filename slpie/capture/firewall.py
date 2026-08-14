"""The capture firewall. Ordered chains, default deny, explicit deny wins.

A firewall does not ask "does this look fine?" — it evaluates an ordered chain
against every packet, defaults to deny, distinguishes refusing loudly from
refusing silently, and logs every verdict. Each of those has a direct counterpart
here, and the analogy is load-bearing rather than decorative.

**Four verdicts, and the fourth is not redundant:**

    ACCEPT      routed to its store, projected into the graph
    QUARANTINE  stored and digested, not projected — held where it can be inspected
    REJECT      refused, and the caller is told which rule and why
    DROP        refused, and the caller is told nothing

`QUARANTINE` earns the design. An unrecognised format must not be dropped — that
silently loses data the operator believes was captured — and must not be accepted —
that poisons the graph with whatever the wrong parser produced. Holding it,
digested and listed, is the only honest third option.

`REJECT` versus `DROP` is the genuinely firewall-shaped distinction. A refusal
normally explains itself, because an operator debugging their own manifest needs
the reason. But where the input is hostile *the reason is the leak*: telling a
caller which rule tripped hands them the map for finding one that does not. So a
rule may declare itself silent, and `DROP` returns nothing to the caller while
recording the full reason where only the operator can read it.

**Default deny and explicit-deny-wins are taken from `slpie/rbac/engine.py`**,
which already resolves exactly this problem for authorisation. They are mirrored
deliberately rather than reimplemented: a chain that matches nothing yields
`QUARANTINE`, never `ACCEPT`; and a later `ACCEPT` can never override an earlier
`DROP`, however precisely it matches. "Most specific wins" is how a well-meaning
narrow allow-rule punches a hole through a broad deny, and the hole is invisible in
the rule list.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from ..domain.finding import Gap, GapKind
from .format import Depth, Format


class Verdict(str, Enum):
    """What the chain decided. Ordered by severity of refusal."""

    ACCEPT = "accept"
    QUARANTINE = "quarantine"
    REJECT = "reject"
    DROP = "drop"

    @property
    def admitted(self) -> bool:
        """Whether the data reaches a store at all."""
        return self in (Verdict.ACCEPT, Verdict.QUARANTINE)

    @property
    def projected(self) -> bool:
        """Whether it becomes nodes and edges. Only ACCEPT does."""
        return self is Verdict.ACCEPT

    @property
    def explains_itself(self) -> bool:
        """Whether the caller learns why. DROP deliberately does not."""
        return self is not Verdict.DROP

    @property
    def denial(self) -> bool:
        return self in (Verdict.REJECT, Verdict.DROP)


@dataclass(frozen=True, slots=True)
class Candidate:
    """What is being offered to the chain. Everything a rule may test."""

    uri: str
    size: int
    format: Format
    digest: str = ""
    zone: str = ""                        # from the manifest's security boundaries
    classification: str = ""              # pii, secret, public …
    source: str = "filesystem"
    labels: Mapping[str, str] = field(default_factory=dict)

    @property
    def filename(self) -> str:
        return self.uri.rsplit("/", 1)[-1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri, "size": self.size, "digest": self.digest,
            "format": self.format.to_dict(), "zone": self.zone,
            "classification": self.classification, "source": self.source,
            "labels": dict(self.labels),
        }


@dataclass(frozen=True, slots=True)
class Rule:
    """One rule in a chain. Ordered, and every one is evaluated.

    `silent` is what makes a rule a DROP rather than a REJECT. It is set only where
    the reason itself would be a leak, and it changes what the *caller* sees, never
    what is recorded.
    """

    name: str
    verdict: Verdict
    matches: Callable[[Candidate], bool]
    reason: str = ""
    silent: bool = False
    depth: Depth = Depth.STRUCTURE        # how deep this rule needs to look

    def applies(self, candidate: Candidate) -> bool:
        """Whether it matches. A rule that raises does not match — and is loud.

        A raising rule must not silently pass data through, and must not silently
        block it either: it is recorded as an error on the evaluation so a broken
        rule is visible rather than quietly permissive.
        """
        return bool(self.matches(candidate))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "verdict": self.verdict.value,
            "reason": self.reason, "silent": self.silent,
            "depth": self.depth.label,
        }

    def __str__(self) -> str:
        return f"{self.name} → {self.verdict.value}"


@dataclass(frozen=True, slots=True)
class Hit:
    """One rule's outcome, recorded whether or not it decided the verdict."""

    rule: str
    matched: bool
    verdict: Verdict | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule, "matched": self.matched,
            "verdict": self.verdict.value if self.verdict else "",
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class Decision:
    """The verdict, and the full chain evaluation behind it.

    `explain()` reads like `iptables -L -v` on purpose: the chain, the hits, the
    verdict, the reason. An operator debugging why their file did not appear needs
    the whole evaluation, not the answer.
    """

    candidate: Candidate
    verdict: Verdict
    rule: str = ""
    reason: str = ""
    hits: tuple[Hit, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def admitted(self) -> bool:
        return self.verdict.admitted

    @property
    def projected(self) -> bool:
        return self.verdict.projected

    def caller_view(self) -> dict[str, Any]:
        """What the *caller* is told. A DROP tells them nothing but the verdict.

        This is the whole REJECT/DROP distinction, and it is enforced here rather
        than left to each surface — a single caller-facing renderer that forgot
        would leak the rule set one message at a time.
        """
        if not self.verdict.explains_itself:
            return {"verdict": self.verdict.value, "uri": self.candidate.uri}
        return {
            "verdict": self.verdict.value, "uri": self.candidate.uri,
            "rule": self.rule, "reason": self.reason,
        }

    def to_dict(self) -> dict[str, Any]:
        """The operator's view. Everything, including a DROP's reason."""
        return {
            "verdict": self.verdict.value, "rule": self.rule,
            "reason": self.reason, "admitted": self.admitted,
            "projected": self.projected,
            "candidate": self.candidate.to_dict(),
            "chain": [hit.to_dict() for hit in self.hits],
            "errors": list(self.errors),
        }

    def explain(self) -> str:
        lines = [
            f"  {self.candidate.filename}  ({self.candidate.format})",
            f"  chain:",
        ]
        for hit in self.hits:
            mark = "✓" if hit.matched else "·"
            note = f" → {hit.verdict.value}" if hit.verdict else ""
            error = f"  [{hit.error}]" if hit.error else ""
            lines.append(f"    {mark} {hit.rule}{note}{error}")
        lines.append("")
        lines.append(f"  verdict: {self.verdict.value.upper()}"
                     + (f" by {self.rule}" if self.rule else " by default"))
        if self.reason:
            lines.append(f"  reason:  {self.reason}")
        return "\n".join(lines)

    def gap(self) -> Gap | None:
        """A quarantined or refused capture is a gap in what the graph knows."""
        if self.verdict is Verdict.ACCEPT:
            return None
        return Gap(
            kind=GapKind.PARSE_FAILURE if self.verdict is Verdict.QUARANTINE
            else GapKind.ACCESS_DENIED,
            subject=self.candidate.uri,
            detail=self.reason or f"{self.verdict.value} by {self.rule or 'default'}",
            remediation=(
                "held in quarantine, digested and retrievable; nothing from it "
                "reached the graph"
                if self.verdict is Verdict.QUARANTINE
                else "it did not enter the platform"
            ),
            confidence_impact=0.1,
        )

    def __str__(self) -> str:
        return f"{self.verdict.value.upper()} {self.candidate.filename}"


class Chain:
    """An ordered rule chain. Default deny; explicit deny always wins."""

    def __init__(self, name: str = "capture", rules: Iterable[Rule] = ()) -> None:
        self.name = name
        self._rules: list[Rule] = list(rules)

    def add(self, rule: Rule) -> Rule:
        self._rules.append(rule)
        return rule

    def insert(self, index: int, rule: Rule) -> Rule:
        self._rules.insert(index, rule)
        return rule

    @property
    def rules(self) -> tuple[Rule, ...]:
        return tuple(self._rules)

    def evaluate(self, candidate: Candidate) -> Decision:
        """Run the whole chain. Every rule's outcome is recorded.

        The chain is evaluated in full rather than short-circuiting on the first
        deny, because `explain()` is most of the value here: an operator asking why
        their file did not appear needs to see which rules matched and which did
        not, not only the one that won.
        """
        hits: list[Hit] = []
        errors: list[str] = []
        denied: tuple[Rule, ...] | None = None
        accepted: Rule | None = None
        quarantined: Rule | None = None

        for rule in self._rules:
            try:
                matched = rule.applies(candidate)
            except Exception as error:  # noqa: BLE001 - a rule may be third-party
                # A broken rule is loud. Treating it as "did not match" would make
                # a crashing deny-rule silently permissive, which is the worst
                # possible failure mode for a firewall.
                message = f"{rule.name} raised: {type(error).__name__}: {error}"
                errors.append(message)
                hits.append(Hit(rule=rule.name, matched=False, error=message))
                continue

            hits.append(Hit(
                rule=rule.name, matched=matched,
                verdict=rule.verdict if matched else None,
            ))
            if not matched:
                continue

            if rule.verdict.denial and denied is None:
                # Explicit deny wins, and the *first* one wins, so the chain reads
                # top to bottom exactly as it is written.
                denied = (rule,)
            elif rule.verdict is Verdict.QUARANTINE and quarantined is None:
                quarantined = rule
            elif rule.verdict is Verdict.ACCEPT and accepted is None:
                accepted = rule

        if denied is not None:
            rule = denied[0]
            return Decision(
                candidate=candidate, verdict=rule.verdict, rule=rule.name,
                reason=rule.reason, hits=tuple(hits), errors=tuple(errors),
            )

        if errors:
            # A rule failed and we do not know what it would have said. Admitting
            # the data on the strength of the rules that *did* run would be
            # assuming the broken one was permissive.
            return Decision(
                candidate=candidate, verdict=Verdict.QUARANTINE,
                rule="chain-error",
                reason=(
                    f"{len(errors)} rule(s) failed to evaluate, so the chain's "
                    f"decision is unknown; held rather than assumed safe"
                ),
                hits=tuple(hits), errors=tuple(errors),
            )

        if quarantined is not None:
            return Decision(
                candidate=candidate, verdict=Verdict.QUARANTINE,
                rule=quarantined.name, reason=quarantined.reason,
                hits=tuple(hits), errors=tuple(errors),
            )

        if accepted is not None:
            return Decision(
                candidate=candidate, verdict=Verdict.ACCEPT, rule=accepted.name,
                reason=accepted.reason, hits=tuple(hits), errors=tuple(errors),
            )

        # Default deny. A chain that matched nothing quarantines rather than
        # accepting — the whole point of a default is what happens when nobody
        # thought about this case.
        return Decision(
            candidate=candidate, verdict=Verdict.QUARANTINE, rule="",
            reason=(
                "no rule matched, and the default is to hold rather than admit: "
                "an unrecognised capture that reached the graph would put "
                "whatever the wrong parser produced into it"
            ),
            hits=tuple(hits), errors=tuple(errors),
        )

    @property
    def depth(self) -> Depth:
        """The deepest any rule needs. What the caller must parse to evaluate it.

        Computed rather than configured, so a chain never asks for a MODEL parse of
        every file when its rules only test the format name.
        """
        return max((rule.depth for rule in self._rules), default=Depth.PROBE)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "rules": [rule.to_dict() for rule in self._rules],
            "depth": self.depth.label,
            "default": Verdict.QUARANTINE.value,
        }

    def render(self) -> str:
        lines = [f"  chain {self.name}", ""]
        for index, rule in enumerate(self._rules, start=1):
            mark = " (silent)" if rule.silent else ""
            lines.append(f"    {index:>2}. {rule.name:<28} {rule.verdict.value}{mark}")
        lines.append("")
        lines.append(f"    default: {Verdict.QUARANTINE.value}")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._rules)

    def __bool__(self) -> bool:
        """A chain is always truthy, even empty.

        Without this, `__len__` makes an empty chain falsy, and the natural
        `chain or default_chain()` silently swaps in the default — so a caller
        deliberately passing a bare chain gets the full one instead. An empty
        chain is a real chain that quarantines everything, and that is a
        meaningful thing to want.
        """
        return True

    def __iter__(self) -> Iterator[Rule]:
        return iter(self._rules)


# --- the default chain ---------------------------------------------------

#: Nothing over this is a manifest. Larger captures are held rather than parsed.
MAX_CAPTURE = 32 * 1024 * 1024

#: Filenames that are secrets by convention. Dropped *silently*: telling a caller
#: "your id_rsa was refused by the private-key rule" confirms the file exists and
#: names the rule to route around.
SECRET_NAMES = (
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".npmrc", ".pypirc",
    ".netrc", "credentials", ".htpasswd", "shadow",
)

SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")


def default_chain() -> Chain:
    """The chain every capture passes unless an operator replaces it.

    Ordered deliberately: silent drops first, then loud rejections, then
    quarantines, then the accepts. Because explicit deny wins and the first deny
    wins, putting the drops first makes the file read the way it behaves.
    """
    return Chain("capture", (
        Rule(
            name="private-key-material", verdict=Verdict.DROP, silent=True,
            depth=Depth.PROBE,
            matches=lambda item: (
                item.filename.lower() in SECRET_NAMES
                or item.filename.lower().endswith(SECRET_SUFFIXES)
            ),
            reason=(
                "the filename indicates private key material. Dropped silently: "
                "naming the rule would confirm the file exists and tell a caller "
                "what to rename it to"
            ),
        ),
        Rule(
            name="executable-image", verdict=Verdict.DROP, silent=True,
            depth=Depth.PROBE,
            matches=lambda item: item.format.name in ("elf",),
            reason=(
                "a compiled binary tells us nothing we can cite and may be "
                "hostile input to a parser"
            ),
        ),
        Rule(
            name="too-large", verdict=Verdict.REJECT, depth=Depth.PROBE,
            matches=lambda item: item.size > MAX_CAPTURE,
            reason=(
                f"larger than {MAX_CAPTURE} bytes. Nothing this size is a "
                f"manifest, and reading it would cost more than the information "
                f"it could hold"
            ),
        ),
        Rule(
            name="contradicts-its-name", verdict=Verdict.QUARANTINE,
            depth=Depth.STRUCTURE,
            matches=lambda item: item.format.contradicts_extension,
            reason=(
                "the content does not match the extension. Held rather than "
                "parsed: routing it by either signal would be guessing which one "
                "is the mistake"
            ),
        ),
        Rule(
            name="unidentified", verdict=Verdict.QUARANTINE, depth=Depth.STRUCTURE,
            matches=lambda item: not item.format.known,
            reason=(
                "the format could not be identified. Held, digested and listed: "
                "dropping it loses data the operator believes was captured, and "
                "accepting it poisons the graph"
            ),
        ),
        Rule(
            name="low-confidence-identification", verdict=Verdict.QUARANTINE,
            depth=Depth.STRUCTURE,
            matches=lambda item: item.format.confidence < 0.3,
            reason=(
                "only the filename identified it, which is a name heuristic at "
                "0.25 and not enough to choose a parser on"
            ),
        ),
        Rule(
            name="known-format", verdict=Verdict.ACCEPT, depth=Depth.STRUCTURE,
            matches=lambda item: item.format.known and item.format.confidence >= 0.3,
            reason="identified by content with enough confidence to route",
        ),
    ))


def digest_of(payload: bytes) -> str:
    """Content address for a capture. Quarantine keeps this even when it keeps
    nothing else, so a held file is retrievable and comparable."""
    return hashlib.blake2b(payload, digest_size=16).hexdigest()
