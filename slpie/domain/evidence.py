"""Evidence, and the confidence derived from it.

The specification's hardest requirement is *evidence over heuristics*. That is
only real if no caller can assign a confidence number — otherwise the first
discoverer under deadline pressure writes ``confidence=0.95`` and the guarantee
is gone. So confidence here is a **pure function** of the evidence supporting a
claim, and there is no setter anywhere in the platform.

Two properties make the function defensible rather than arbitrary:

* **Independence.** Corroboration is grouped by ``(kind, source uri)``. Reading
  the same lockfile twice is one piece of evidence, not two. Only genuinely
  independent observations compound.
* **A heuristic ceiling.** Evidence drawn *only* from reflection, dynamic
  loading, or name matching is capped, no matter how much of it accumulates.
  A thousand guesses do not make a fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from ..errors import EvidenceRequired
from .identity import digest

#: Confidence at or above which a claim is treated as settled for reporting.
SETTLED = 0.90
#: Ceiling for claims supported only by heuristic evidence.
HEURISTIC_CEILING = 0.60
#: Ceiling for anything inferred rather than observed.
#:
#: Certainty is reserved for a resolved lockfile pin. Without this, enough
#: corroborating inferences round to 1.00 and become indistinguishable from a
#: fact — which is precisely the collapse the evidence model exists to prevent.
INFERENCE_CEILING = 0.99


class EvidenceKind(str, Enum):
    """How a fact came to be known. The ordering here *is* the trust model."""

    LOCKFILE_PIN = "lockfile_pin"              # resolved and pinned by a package manager
    RUNTIME_TRACE = "runtime_trace"            # observed actually executing
    MANIFEST_DECLARED = "manifest_declared"    # declared in a package manifest
    DECLARED = "declared"                      # declared in the SLPIE environment manifest
    STATIC_IMPORT = "static_import"            # a parsed import/require/use statement
    IAC_DECLARATION = "iac_declaration"        # Terraform/K8s/Helm resource
    BUILD_CONFIG = "build_config"              # build system configuration
    CONTAINER_MANIFEST = "container_manifest"  # Dockerfile / image manifest
    GENERATED_CODE = "generated_code"          # emitted by a generator we can attribute
    ANNOTATION = "annotation"                  # decorator / attribute / annotation
    CONFIG_REFERENCE = "config_reference"      # a name appearing in configuration
    DI_REGISTRATION = "di_registration"        # dependency-injection registration
    REFLECTION = "reflection"                  # reflective lookup
    DYNAMIC_LOAD = "dynamic_load"              # runtime module loading by computed name
    NAME_HEURISTIC = "name_heuristic"          # matched by naming convention alone

    @property
    def base_confidence(self) -> float:
        return _BASE[self]

    @property
    def heuristic(self) -> bool:
        """True for kinds that infer rather than observe."""
        return self in _HEURISTIC

    @property
    def observed(self) -> bool:
        """True when the evidence reflects reality rather than intent."""
        return self in (EvidenceKind.RUNTIME_TRACE, EvidenceKind.LOCKFILE_PIN)


_BASE: dict[EvidenceKind, float] = {
    EvidenceKind.LOCKFILE_PIN: 1.00,
    EvidenceKind.RUNTIME_TRACE: 0.98,
    EvidenceKind.MANIFEST_DECLARED: 0.95,
    EvidenceKind.DECLARED: 0.92,
    EvidenceKind.STATIC_IMPORT: 0.90,
    EvidenceKind.IAC_DECLARATION: 0.88,
    EvidenceKind.BUILD_CONFIG: 0.85,
    EvidenceKind.CONTAINER_MANIFEST: 0.85,
    EvidenceKind.GENERATED_CODE: 0.80,
    EvidenceKind.ANNOTATION: 0.75,
    EvidenceKind.CONFIG_REFERENCE: 0.70,
    EvidenceKind.DI_REGISTRATION: 0.65,
    EvidenceKind.REFLECTION: 0.45,
    EvidenceKind.DYNAMIC_LOAD: 0.40,
    EvidenceKind.NAME_HEURISTIC: 0.25,
}

_HEURISTIC = frozenset({
    EvidenceKind.REFLECTION,
    EvidenceKind.DYNAMIC_LOAD,
    EvidenceKind.NAME_HEURISTIC,
})


class ValidationStatus(str, Enum):
    """Whether a claim has been checked — a *separate axis* from confidence.

    A declared REST endpoint observed speaking gRPC is not "less certain". It is
    wrong, and must surface as a finding rather than as a slightly lower number.
    """

    UNVERIFIED = "unverified"      # single source, nothing to check it against
    CORROBORATED = "corroborated"  # independent sources agree
    CONFIRMED = "confirmed"        # observed in reality (runtime or lockfile)
    CONTRADICTED = "contradicted"  # independent sources disagree


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """Where a piece of evidence physically lives."""

    uri: str
    line: int = 0
    column: int = 0
    span: str = ""
    commit_sha: str = ""

    @property
    def reference(self) -> str:
        """`file.py:42` — the form a human wants in an explanation."""
        base = self.uri.rsplit("/", 1)[-1] or self.uri
        return f"{base}:{self.line}" if self.line else base

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"uri": self.uri}
        if self.line:
            out["line"] = self.line
        if self.column:
            out["column"] = self.column
        if self.span:
            out["span"] = self.span
        if self.commit_sha:
            out["commit_sha"] = self.commit_sha
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceLocation":
        return cls(
            uri=data["uri"], line=int(data.get("line", 0)),
            column=int(data.get("column", 0)), span=data.get("span", ""),
            commit_sha=data.get("commit_sha", ""),
        )

    def __str__(self) -> str:
        return self.reference


@dataclass(frozen=True, slots=True)
class Evidence:
    """One justification for one claim.

    ``excerpt`` is the literal text that justifies the assertion. It is not
    decoration: an explanation that cannot show the line it read is not an
    explanation, and the UI renders it verbatim.
    """

    kind: EvidenceKind
    location: SourceLocation
    extractor: str
    extractor_version: str = "0"
    content_digest: str = ""
    excerpt: str = ""
    observed_at: int = 0
    labels: Mapping[str, str] = field(default_factory=dict)

    @property
    def id(self) -> str:
        """Content-addressed: identical observations are the same evidence."""
        return digest({
            "kind": self.kind.value,
            "location": self.location.to_dict(),
            "extractor": self.extractor,
            "extractor_version": self.extractor_version,
            "content_digest": self.content_digest,
            "excerpt": self.excerpt,
        })

    @property
    def independence_key(self) -> tuple[str, str]:
        """What makes two observations independent: a different kind, or a different file.

        Two `STATIC_IMPORT` findings in the same file are one observation for
        confidence purposes; the same fact seen in a lockfile *and* an import is
        two.
        """
        return (self.kind.value, self.location.uri)

    @property
    def base_confidence(self) -> float:
        return self.kind.base_confidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "location": self.location.to_dict(),
            "extractor": self.extractor,
            "extractor_version": self.extractor_version,
            "content_digest": self.content_digest,
            "excerpt": self.excerpt[:500],
            "observed_at": self.observed_at,
            "labels": dict(self.labels),
            "base_confidence": self.base_confidence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Evidence":
        return cls(
            kind=EvidenceKind(data["kind"]),
            location=SourceLocation.from_dict(data["location"]),
            extractor=data.get("extractor", "unknown"),
            extractor_version=data.get("extractor_version", "0"),
            content_digest=data.get("content_digest", ""),
            excerpt=data.get("excerpt", ""),
            observed_at=int(data.get("observed_at", 0)),
            labels=dict(data.get("labels", {})),
        )

    def __str__(self) -> str:
        return f"{self.kind.value}@{self.location.reference}"


@dataclass(frozen=True, slots=True)
class ConfidenceDerivation:
    """The audit trail for a single confidence number.

    Returned by :meth:`Confidence.explain` and rendered directly in the UI, so
    a reviewer can see that 0.97 came from a manifest plus two independent
    imports rather than from somebody's judgement.
    """

    value: float
    groups: tuple[tuple[str, str, float], ...]   # (kind, uri, base)
    rule: str
    capped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "rule": self.rule,
            "capped": self.capped,
            "contributions": [
                {"kind": k, "source": u, "base": b} for k, u, b in self.groups
            ],
        }

    def __str__(self) -> str:
        return f"{self.value:.2f} — {self.rule}"


class Confidence:
    """Derives confidence from evidence. Has no state and no setter."""

    @staticmethod
    def combine(evidence: Sequence[Evidence], *, subject: str = "") -> float:
        return Confidence.explain(evidence, subject=subject).value

    @staticmethod
    def explain(evidence: Sequence[Evidence], *, subject: str = "") -> ConfidenceDerivation:
        """Compute the confidence *and* the derivation that produced it."""
        if not evidence:
            raise EvidenceRequired(subject)

        # Collapse to independent observations before combining anything.
        groups: dict[tuple[str, str], float] = {}
        for item in evidence:
            key = item.independence_key
            groups[key] = max(groups.get(key, 0.0), item.base_confidence)

        contributions = tuple(
            (kind, uri, base) for (kind, uri), base in sorted(groups.items())
        )

        if any(kind == EvidenceKind.LOCKFILE_PIN.value for kind, _, _ in contributions):
            return ConfidenceDerivation(
                value=1.0, groups=contributions,
                rule="a resolved lockfile pin is a fact, not an inference",
            )

        # Noisy-OR: independent observations reinforce, but never reach certainty.
        residual = 1.0
        for _kind, _uri, base in contributions:
            residual *= 1.0 - base
        value = 1.0 - residual

        only_heuristic = all(
            EvidenceKind(kind).heuristic for kind, _, _ in contributions
        )
        if only_heuristic and value > HEURISTIC_CEILING:
            return ConfidenceDerivation(
                value=HEURISTIC_CEILING, groups=contributions, capped=True,
                rule=(
                    f"supported only by heuristic evidence; capped at "
                    f"{HEURISTIC_CEILING} regardless of corroboration"
                ),
            )

        if value > INFERENCE_CEILING:
            return ConfidenceDerivation(
                value=INFERENCE_CEILING, groups=contributions, capped=True,
                rule=(
                    f"{len(contributions)} independent observations, but nothing was "
                    f"resolved or executed; inference is capped at {INFERENCE_CEILING}"
                ),
            )

        rule = (
            f"single {contributions[0][0]} observation"
            if len(contributions) == 1
            else f"{len(contributions)} independent observations combined"
        )
        return ConfidenceDerivation(value=round(value, 4), groups=contributions, rule=rule)

    @staticmethod
    def status(evidence: Sequence[Evidence], *, contradicted: bool = False) -> ValidationStatus:
        """Classify a claim on the validation axis."""
        if contradicted:
            return ValidationStatus.CONTRADICTED
        if not evidence:
            raise EvidenceRequired()
        if any(item.kind.observed for item in evidence):
            return ValidationStatus.CONFIRMED
        independent = {item.independence_key for item in evidence}
        return (
            ValidationStatus.CORROBORATED if len(independent) > 1
            else ValidationStatus.UNVERIFIED
        )


def strongest(evidence: Iterable[Evidence]) -> Evidence | None:
    """The single most trustworthy observation — what an explanation leads with."""
    items = list(evidence)
    return max(items, key=lambda e: e.base_confidence) if items else None
