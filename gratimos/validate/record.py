"""Run records — what was asked, what came back, and what it produced.

The paper's fourth priority is empowering peer reviewers, and its concrete ask
is "short records detailing the prompts and outputs that produced the" result.
The reason is blunt: writing quality has stopped being a discriminator, so a
reviewer who cannot inspect how a claim was produced cannot assess it at all.

This system already records domain events and reasoning paths. It has never
recorded *agent runs* — the prompt, the model, the answer, the cost, and which
claims came out. Without that, "why does the graph believe this?" terminates at
"an agent said so", which is exactly the dead end the evidence model was built
to avoid everywhere else.

Three properties:

* **Content-addressed and append-only.** A run's id is a digest of what it
  actually contained, so two records claiming the same id claim the same
  content, and a record cannot be quietly edited afterwards.
* **Secrets are redacted on the way in, not on the way out.** A ledger that
  stores the key and hides it at render time is a ledger that leaks the first
  time somebody serialises it differently. The prompt is masked before it is
  ever held.
* **Claims point back.** `for_claim()` answers "which run produced this?" —
  the query a reviewer actually has, and the one that makes the record worth
  keeping rather than merely worthy.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from ..errors import GratimosError


class RecordError(GratimosError):
    """A run record was malformed, or the ledger was asked something it cannot answer."""


#: Credential shapes worth catching by pattern. Specific enough that a broad
#: "anything that looks random" rule does not redact half a stack trace and
#: teach people to switch redaction off — but lengths are *ranges* rather than
#: exact counts. A redactor that misses a real credential because it was one
#: character off the canonical length is brittle, and the entropy sweep below
#: would then catch it under the useless name "high-entropy" instead of saying
#: which vendor's key just went into a prompt.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}")),
    ("github-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("aws-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("bearer", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("url-credentials", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:@/]+:[^\s@/]+@")),
)

#: Shannon entropy above which a long unbroken token is treated as a secret.
#: Tuned so base64 key material trips it and a hex digest or a sentence does not.
ENTROPY_FLOOR = 4.2
ENTROPY_MIN_LENGTH = 32

#: How much of a prompt or an answer is kept. A record nobody will read is not
#: a record; the paper asks for *short* ones.
EXCERPT = 4000


def entropy(text: str) -> float:
    """Shannon entropy in bits per character."""
    if not text:
        return 0.0
    counts: dict[str, int] = {}
    for character in text:
        counts[character] = counts.get(character, 0) + 1
    total = len(text)
    return -sum(
        (count / total) * math.log2(count / total) for count in counts.values()
    )


@dataclass(frozen=True, slots=True)
class Redaction:
    """One masked secret. Kept so the record says a secret *was* there."""

    kind: str
    hint: str          # a few leading characters, enough to recognise, not to use

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "hint": self.hint}


def redact(text: str) -> tuple[str, tuple[Redaction, ...]]:
    """Mask credentials, and report what was masked.

    Returns the redactions rather than swallowing them, because "this prompt
    contained an AWS key" is itself a finding — and a record that silently drops
    the fact hides a credential leak into a log rather than surfacing it.
    """
    found: list[Redaction] = []
    cleaned = text

    for kind, pattern in _PATTERNS:
        def mask(match: re.Match[str], _kind: str = kind) -> str:
            raw = match.group(0)
            found.append(Redaction(kind=_kind, hint=raw[:6]))
            return f"«{_kind} redacted»"

        cleaned = pattern.sub(mask, cleaned)

    # Anything left that is long, unbroken and high-entropy is key material we
    # do not have a pattern for yet.
    def sweep(match: re.Match[str]) -> str:
        token = match.group(0)
        if entropy(token) >= ENTROPY_FLOOR:
            found.append(Redaction(kind="high-entropy", hint=token[:6]))
            return "«high-entropy redacted»"
        return token

    cleaned = re.sub(rf"\S{{{ENTROPY_MIN_LENGTH},}}", sweep, cleaned)
    return cleaned, tuple(found)


@dataclass(frozen=True, slots=True)
class Run:
    """One agent invocation, recorded so a reviewer can check it."""

    question: str
    actor: str                       # model or validator identity, with version
    prompt: str = ""
    answer: str = ""
    signature: str = ""              # the need this served, if any
    claims: tuple[str, ...] = ()     # what came out, by id
    started_at: float = 0.0
    finished_at: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    error: str = ""
    redactions: tuple[Redaction, ...] = ()
    inputs_digest: str = ""

    def __post_init__(self) -> None:
        if not self.actor.strip():
            raise RecordError(
                "a run must name its actor; 'an agent said so' is the dead end "
                "this record exists to prevent"
            )

    @property
    def id(self) -> str:
        """Content-addressed, so a record cannot be edited without becoming another."""
        material = "\x1f".join([
            self.question, self.actor, self.prompt, self.answer,
            f"{self.started_at:.6f}", ",".join(self.claims),
        ])
        return hashlib.blake2b(material.encode("utf-8"), digest_size=16).hexdigest()

    @property
    def duration(self) -> float:
        return max(self.finished_at - self.started_at, 0.0)

    @property
    def succeeded(self) -> bool:
        return not self.error

    @property
    def leaked(self) -> bool:
        """Whether a credential was found in what was sent to the model."""
        return bool(self.redactions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "actor": self.actor,
            "signature": self.signature,
            "prompt": self.prompt,
            "answer": self.answer,
            "claims": list(self.claims),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration": round(self.duration, 4),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost": self.cost,
            "error": self.error,
            "succeeded": self.succeeded,
            "redactions": [item.to_dict() for item in self.redactions],
            "inputs_digest": self.inputs_digest,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Run":
        return cls(
            question=data.get("question", ""), actor=data["actor"],
            prompt=data.get("prompt", ""), answer=data.get("answer", ""),
            signature=data.get("signature", ""),
            claims=tuple(data.get("claims", ())),
            started_at=data.get("started_at", 0.0),
            finished_at=data.get("finished_at", 0.0),
            tokens_in=data.get("tokens_in", 0), tokens_out=data.get("tokens_out", 0),
            cost=data.get("cost", 0.0), error=data.get("error", ""),
            redactions=tuple(
                Redaction(kind=item["kind"], hint=item["hint"])
                for item in data.get("redactions", ())
            ),
            inputs_digest=data.get("inputs_digest", ""),
        )

    def summarise(self) -> str:
        """The short record the paper asks for — one screen, not a transcript."""
        head = f"{self.actor} · {self.duration:.2f}s"
        if self.tokens_in or self.tokens_out:
            head += f" · {self.tokens_in}→{self.tokens_out} tokens"
        if self.cost:
            head += f" · {self.cost:.4f}"
        lines = [f"run {self.id[:12]} — {head}", f"  asked: {self.question}"]
        if self.prompt:
            lines.append(f"  prompt: {_clip(self.prompt, 200)}")
        if self.answer:
            lines.append(f"  answer: {_clip(self.answer, 200)}")
        if self.claims:
            lines.append(f"  produced: {', '.join(self.claims)}")
        if self.redactions:
            kinds = ", ".join(sorted({item.kind for item in self.redactions}))
            lines.append(f"  ! credentials were present in the prompt: {kinds}")
        if self.error:
            lines.append(f"  failed: {self.error}")
        return "\n".join(lines)

    def __str__(self) -> str:
        return f"{self.actor}: {self.question[:60]}"


def _clip(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


class Recorder:
    """Wraps a call so the record is written whether or not it succeeds.

    A recorder that only records successes produces a ledger where nothing ever
    goes wrong, which is the least useful audit trail there is — the failures
    are what a reviewer wants.
    """

    def __init__(self, ledger: "RunLedger", actor: str, *, question: str = "",
                 signature: str = "", now=None) -> None:
        self.ledger = ledger
        self.actor = actor
        self.question = question
        self.signature = signature
        self._now = now if now is not None else time.time
        self._started = 0.0
        self.prompt = ""
        self.answer = ""
        self.claims: list[str] = []
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost = 0.0
        self.run: Run | None = None

    def __enter__(self) -> "Recorder":
        self._started = self._now()
        return self

    def __exit__(self, kind, value, traceback) -> bool:
        prompt, prompt_redactions = redact(self.prompt[:EXCERPT])
        answer, answer_redactions = redact(self.answer[:EXCERPT])
        self.run = self.ledger.append(Run(
            question=self.question, actor=self.actor, signature=self.signature,
            prompt=prompt, answer=answer, claims=tuple(self.claims),
            started_at=self._started, finished_at=self._now(),
            tokens_in=self.tokens_in, tokens_out=self.tokens_out, cost=self.cost,
            error="" if value is None else f"{kind.__name__}: {value}",
            redactions=prompt_redactions + answer_redactions,
        ))
        return False        # never swallow the exception


class RunLedger:
    """Every agent run, append-only, queryable by what it produced."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._runs: list[Run] = []
        self._by_id: dict[str, Run] = {}
        self._by_claim: dict[str, list[str]] = {}
        self.path = Path(path) if path is not None else None
        if self.path is not None and self.path.exists():
            self.load()

    def append(self, run: Run) -> Run:
        """Record a run. Re-appending identical content is a no-op, not an error."""
        existing = self._by_id.get(run.id)
        if existing is not None:
            return existing
        self._runs.append(run)
        self._by_id[run.id] = run
        for claim in run.claims:
            self._by_claim.setdefault(claim, []).append(run.id)
        if self.path is not None:
            self._persist(run)
        return run

    def record(self, actor: str, **settings: Any) -> Recorder:
        """`with ledger.record("claude-opus-5") as run:` — the usual entry point."""
        return Recorder(self, actor, **settings)

    # -- the reviewer's questions ------------------------------------------

    def get(self, run_id: str) -> Run | None:
        return self._by_id.get(run_id)

    def for_claim(self, claim: str) -> tuple[Run, ...]:
        """Which run produced this? The question the whole record exists for."""
        return tuple(self._by_id[run_id] for run_id in self._by_claim.get(claim, ()))

    def by_actor(self, actor: str) -> tuple[Run, ...]:
        return tuple(run for run in self._runs if run.actor == actor)

    def failures(self) -> tuple[Run, ...]:
        return tuple(run for run in self._runs if not run.succeeded)

    def leaks(self) -> tuple[Run, ...]:
        """Runs whose prompt carried a credential. A security finding, not trivia."""
        return tuple(run for run in self._runs if run.leaked)

    @property
    def runs(self) -> tuple[Run, ...]:
        return tuple(self._runs)

    # -- durability --------------------------------------------------------

    def _persist(self, run: Run) -> None:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(run.to_dict(), separators=(",", ":")) + "\n")

    def load(self) -> int:
        """Read a JSON-lines ledger. A corrupt line is skipped, not fatal.

        One unreadable line must not cost the whole audit trail — the remaining
        records are still evidence, and refusing to start would tempt somebody
        to delete the file.
        """
        assert self.path is not None
        loaded = 0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                run = Run.from_dict(json.loads(line))
            except (json.JSONDecodeError, KeyError, RecordError):
                continue
            if run.id not in self._by_id:
                self._runs.append(run)
                self._by_id[run.id] = run
                for claim in run.claims:
                    self._by_claim.setdefault(claim, []).append(run.id)
                loaded += 1
        return loaded

    # -- reporting ---------------------------------------------------------

    def spend(self) -> dict[str, Any]:
        """What the agents have cost. The other half of the bottleneck argument."""
        by_actor: dict[str, dict[str, float]] = {}
        for run in self._runs:
            entry = by_actor.setdefault(
                run.actor, {"runs": 0, "cost": 0.0, "tokens": 0, "seconds": 0.0}
            )
            entry["runs"] += 1
            entry["cost"] += run.cost
            entry["tokens"] += run.tokens_in + run.tokens_out
            entry["seconds"] += run.duration
        return by_actor

    def status(self) -> dict[str, Any]:
        return {
            "runs": len(self._runs),
            "claims": len(self._by_claim),
            "failures": len(self.failures()),
            "leaks": len(self.leaks()),
            "actors": sorted({run.actor for run in self._runs}),
            "cost": round(sum(run.cost for run in self._runs), 6),
            "durable": self.path is not None,
        }

    def __len__(self) -> int:
        return len(self._runs)

    def __iter__(self) -> Iterator[Run]:
        return iter(tuple(self._runs))

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<RunLedger {len(self._runs)} runs>"
