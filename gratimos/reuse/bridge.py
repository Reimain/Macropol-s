"""The one Gratimos module that imports SLPIE. Everything else goes through it.

SLPIE already owns two things this package needs and must not reimplement: SPDX
expression parsing with obligation analysis (`slpie/domain/license.py`) and
version-range algebra across five ecosystems (`slpie/domain/version.py`). Both
are load-bearing for a reuse decision — a licence gate that gets AGPL wrong is
worse than no gate, and a runtime check that mis-parses `>=3.11,<4` silently
admits packages that cannot run.

**The direction is the awkward part and is worth naming.** SLPIE calls Gratimos
for code generation, through exactly one module
(`slpie/artifacts/codegen.py`), enforced by
`tests/test_slpie_boundaries.py::test_exactly_one_slpie_module_may_import_gratimos`.
This module is the mirror of that arrangement, held to the same rule by
`tests/test_reuse_boundaries.py`: one file, one direction of import, asserted
rather than agreed. Two packages that each reach into the other in a dozen
places are one tangled package; two that meet at a named seam in each direction
are still two packages.

**It degrades rather than lying.** If SLPIE is not importable — Gratimos
installed alone, a partial checkout — the bridge reports itself unavailable and
the gate refuses every candidate whose licence it therefore cannot evaluate.
That is deliberately inconvenient. The alternative, waving licences through when
the evaluator is missing, would make an absent dependency look like a clean bill
of health, which is the one failure mode a compliance gate must not have.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Resolved once at import. A module-level flag rather than a try/except at each
#: call site, so "is the evaluator here?" has exactly one answer per process.
try:  # pragma: no cover - exercised by whichever branch the install produces
    from slpie.domain.license import (
        Distribution,
        LicenseError,
        Linkage,
        Obligation,
        check_compatibility,
        normalize,
    )
    from slpie.domain.version import Version, parse_range

    AVAILABLE = True
    UNAVAILABLE_REASON = ""
except ImportError as exc:  # pragma: no cover - only on a partial install
    Distribution = Linkage = Obligation = None  # type: ignore[assignment]
    LicenseError = Exception  # type: ignore[assignment]
    check_compatibility = normalize = None  # type: ignore[assignment]
    Version = parse_range = None  # type: ignore[assignment]

    AVAILABLE = False
    UNAVAILABLE_REASON = (
        f"slpie.domain is not importable ({exc}); licence and runtime constraints "
        f"cannot be evaluated, so every candidate subject to one is refused"
    )


@dataclass(frozen=True, slots=True)
class Verdict:
    """A bridged answer, in Gratimos' own vocabulary.

    Deliberately not SLPIE's `CompatibilityVerdict`: that type would then appear
    in Gratimos signatures, and the single-import boundary would be a formality
    while the coupling spread anyway.
    """

    ok: bool
    reason: str
    remediation: str = ""
    detail: str = ""

    def __bool__(self) -> bool:
        return self.ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "reason": self.reason,
            "remediation": self.remediation, "detail": self.detail,
        }


def unavailable(subject: str) -> Verdict:
    return Verdict(ok=False, reason=UNAVAILABLE_REASON or f"cannot evaluate {subject}")


def licence_ok(
    declared: str,
    *,
    project_licence: str = "",
    distribution: str = "network_service",
    linkage: str = "dynamic",
) -> Verdict:
    """Whether a dependency's licence can be absorbed in the stated context.

    The context is passed through rather than defaulted away, because the answer
    genuinely changes with it: AGPL in an internal tool is fine and AGPL in a
    hosted product is a source-disclosure obligation, and a gate that always
    assumed one of those would be wrong half the time.
    """
    if not AVAILABLE:
        return unavailable(f"licence {declared!r}")
    if not declared.strip():
        return Verdict(
            ok=False,
            reason="no licence is declared, so its obligations cannot be evaluated",
            remediation="check the repository for a LICENSE file and record it",
        )

    try:
        verdict = check_compatibility(
            declared,
            project=project_licence or None,
            distribution=Distribution(distribution),
            linkage=Linkage(linkage),
        )
    except (LicenseError, ValueError) as exc:
        return Verdict(
            ok=False,
            reason=f"licence {declared!r} could not be evaluated: {exc}",
            remediation="record the actual licence text, or add a LicenseRef mapping",
        )

    return Verdict(
        ok=verdict.compatible,
        reason=verdict.reason,
        remediation=verdict.remediation,
        detail=verdict.obligation.value,
    )


def licence_obligation(declared: str) -> str:
    """The obligation class alone — permissive, weak-copyleft, and so on.

    Used for ranking rather than gating: two compatible candidates are not
    equally cheap to absorb, and a permissive one should win over a
    weak-copyleft one all else being equal.
    """
    if not AVAILABLE or not declared.strip():
        return "unknown"
    try:
        return normalize(declared).obligation.value
    except (LicenseError, ValueError):
        return "unknown"


def runtime_ok(required: str, supported: str, *, ecosystem: str = "generic") -> Verdict:
    """Whether a package supporting `supported` can run on `required`.

    `required` is a concrete version the project runs on ("3.11"); `supported`
    is the package's declared range (">=3.7", "^18 || ^20"). An empty range is
    "unstated", which is *allowed* — most packages declare nothing and refusing
    all of them would empty the candidate list — but the caller is told, so the
    unknown can be surfaced as a gap rather than passing as a check.
    """
    if not AVAILABLE:
        return unavailable(f"runtime {required!r}")
    if not supported.strip():
        return Verdict(
            ok=True,
            reason="the package declares no runtime range; compatibility is unverified",
            detail="unstated",
        )
    if not required.strip():
        return Verdict(ok=True, reason="no runtime was required", detail="unconstrained")

    try:
        allowed = parse_range(supported, ecosystem=ecosystem)
        wanted = Version.parse(required)
    except Exception as exc:
        return Verdict(
            ok=False,
            reason=f"could not compare runtime {required!r} against {supported!r}: {exc}",
            remediation="state the runtime as a plain version, e.g. 3.11",
        )

    if allowed.allows(wanted):
        return Verdict(ok=True, reason=f"{supported} admits {required}", detail=str(allowed))
    return Verdict(
        ok=False,
        reason=f"{supported} does not admit {required}",
        remediation="find a release supporting this runtime, or raise the project's runtime",
        detail=str(allowed),
    )


def status() -> dict[str, Any]:
    return {"available": AVAILABLE, "reason": UNAVAILABLE_REASON}
