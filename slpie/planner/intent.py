"""Reading a question well enough to compose an answer, with no model.

This is the deterministic backend, and it has to be genuinely useful rather than a
placeholder, because it is the one that works inside the simulator, in an
air-gapped network, and with no API key. The Claude backend is the flexible one;
this is the one that is always there.

It works because the pipe is typed. Intent recognition here does **not** try to
understand the question — it extracts two things:

* a **target kind**: what shape of answer is wanted. "what breaks" wants IMPACT,
  "what is wrong" wants FINDINGS, "does this resolve" wants a SOLUTION.
* a **subject**: the package, service or path the question is about.

The registry's type graph then supplies the actual composition, because
`paths_to(kind)` already enumerates every pipeline that produces it, shortest
first. So the planner never invents a verb and never emits an invalid pipeline —
it *cannot*, since it only ever picks routes the graph already contains. That is
the whole reason this offline path is honest rather than a fiction.

What it will not do is guess. A question that matches nothing yields no intent,
and the planner then asks back rather than running something arbitrary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..compose.flow import Kind

#: Phrases that indicate the shape of answer wanted, most specific first.
#: Ordering matters: "what breaks if" is about impact, and it also contains
#: "what", so a less specific pattern placed earlier would swallow it.
SIGNALS: tuple[tuple[tuple[str, ...], Kind, str], ...] = (
    (
        ("what breaks", "blast radius", "who depends", "what depends",
         "impact of", "downstream", "affected by", "ripple"),
        Kind.IMPACT,
        "asks what else is reached, which is reverse reachability over the graph",
    ),
    (
        ("resolve", "version conflict", "constraint", "compatible",
         "can i upgrade", "safe to upgrade", "satisfiable"),
        Kind.SOLUTION,
        "asks whether the declared ranges can all hold at once",
    ),
    (
        ("what is wrong", "whats wrong", "problems", "issues", "vulnerab",
         "risk", "violations", "must i fix", "before release", "block",
         "severity", "findings"),
        Kind.FINDINGS,
        "asks for a ranked list of what is wrong",
    ),
    (
        ("declared", "reconcile", "drift", "undeclared", "shadow",
         "match the manifest", "as documented"),
        Kind.FINDINGS,
        "asks where declaration and reality disagree",
    ),
    (
        ("cannot see", "not see", "blind spot", "blind", "missing", "gaps",
         "not covered", "how confident", "how sure", "unknown to you"),
        Kind.GAPS,
        "asks what limits the answers, which is the gap list",
    ),
    (
        ("why", "explain", "how do you know", "justify", "evidence for",
         "prove", "provenance"),
        Kind.REPORT,
        "asks for the reasoning rather than the value",
    ),
    (
        ("what packages", "dependencies", "what is in", "inventory",
         "list", "how many", "count", "sbom", "bill of materials"),
        Kind.RESOLUTION,
        "asks for the inventory, which is the merged identity set",
    ),
    (
        ("architecture", "services", "topology", "structure", "components"),
        Kind.NODES,
        "asks about the graph's shape",
    ),
)

#: A package-ish token: `lodash`, `@types/node`, `com.acme:payments`, `pkg:npm/x`.
_SUBJECT = re.compile(
    r"(?:pkg:[a-z]+/[^\s]+)"
    r"|(?:@[\w.-]+/[\w.-]+)"
    r"|(?:[\w.-]+:[\w.-]+)"
    r"|(?:[a-z][\w.-]{2,})",
    re.IGNORECASE,
)

#: Words that look like subjects and are not. Without this, "what breaks if
#: lodash 5 lands" yields "breaks" as the subject and the plan targets nothing.
_STOPWORDS = frozenset({
    "what", "which", "where", "when", "why", "how", "who", "whose",
    "breaks", "break", "broken", "happens", "happen", "lands", "land",
    "does", "did", "will", "would", "should", "could", "can", "may",
    "the", "this", "that", "these", "those", "there", "here",
    "and", "but", "for", "with", "from", "into", "onto", "about",
    "are", "was", "were", "been", "being", "have", "has", "had",
    "any", "all", "some", "none", "not", "now",
    "upgrade", "upgrading", "update", "version", "versions", "package",
    "packages", "dependency", "dependencies", "service", "services",
    "wrong", "problems", "issues", "risk", "risks", "findings", "gaps",
    "declared", "reconcile", "drift", "explain", "evidence", "resolve",
    "conflict", "conflicts", "constraint", "constraints", "compatible",
    "release", "before", "after", "must", "need", "needs", "fix",
    "repository", "repo", "project", "codebase", "system", "environment",
    "everything", "anything", "something", "nothing",
    "actually", "really", "truly", "just", "still", "even", "also",
    "mine", "yours", "ours", "them", "they", "their",
})

#: A path-ish token, so "what is in ./services/payments" targets that directory.
_PATH = re.compile(r"(?:\.{1,2}/[\w./-]+)|(?:/[\w./-]{2,})")


@dataclass(frozen=True, slots=True)
class Intent:
    """What shape of answer a question wants, and what it is about."""

    question: str
    target: Kind | None = None
    subject: str = ""
    path: str = ""
    severity: str = ""
    reason: str = ""
    matched: tuple[str, ...] = ()

    @property
    def understood(self) -> bool:
        return self.target is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "target": self.target.value if self.target else "",
            "subject": self.subject, "path": self.path,
            "severity": self.severity, "reason": self.reason,
            "matched": list(self.matched), "understood": self.understood,
        }

    def __str__(self) -> str:
        if not self.understood:
            return f"{self.question!r}: not understood"
        return (
            f"{self.question!r} → {self.target.label}"
            + (f" about {self.subject}" if self.subject else "")
        )


def read(question: str) -> Intent:
    """A question → an intent. Never guesses a target it did not see evidence for."""
    text = (question or "").strip()
    lowered = text.lower()
    if not lowered:
        return Intent(question=text, reason="the question is empty")

    target: Kind | None = None
    reason = ""
    matched: list[str] = []
    for phrases, kind, why in SIGNALS:
        hits = [phrase for phrase in phrases if phrase in lowered]
        if hits and target is None:
            target, reason = kind, why
            matched.extend(hits)
        elif hits:
            matched.extend(hits)

    return Intent(
        question=text, target=target, subject=_subject(text),
        path=_path(text), severity=_severity(lowered),
        reason=reason or (
            "no phrase in the question named a kind of answer; the planner "
            "will ask back rather than pick one"
        ),
        matched=tuple(dict.fromkeys(matched)),
    )


def _subject(text: str) -> str:
    """The most likely thing being asked about. Empty when nothing stands out."""
    for token in _SUBJECT.findall(text):
        cleaned = token.strip(".,;:!?\"'")
        if not cleaned or cleaned.lower() in _STOPWORDS:
            continue
        if cleaned.startswith(("./", "/", "../")):
            continue
        # A bare number is a version, not a subject.
        if cleaned.replace(".", "").isdigit():
            continue
        return cleaned
    return ""


def _path(text: str) -> str:
    found = _PATH.findall(text)
    return found[0] if found else ""


def _severity(lowered: str) -> str:
    for level in ("critical", "high", "medium", "low"):
        if level in lowered:
            return level
    # "must fix before release" is a severity statement without the word.
    if "before release" in lowered or "block" in lowered:
        return "high"
    return ""
