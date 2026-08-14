"""Target selection — one tag, and the per-element overrides hybrid allows.

:class:`Target` itself lives in :mod:`slpie.environment.manifest`, because it is
part of what a manifest declares. What lives here is the *decision*: given a
manifest and an element, which side answers.

Hybrid exists for the case that actually happens during a cutover — most of the
environment still simulated, one service pointed at the real thing — and it is
gated exactly as strictly as full live, because one live element is enough to
touch production.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..environment.manifest import Manifest, Target


@dataclass(frozen=True, slots=True)
class TargetSelection:
    """Which side answers for each element, and why.

    ``overrides`` maps element name to target and is only consulted under
    HYBRID. Under SIMULATED or LIVE the answer is uniform, which is what makes
    the two pure cases trivially auditable.
    """

    default: Target = Target.SIMULATED
    overrides: Mapping[str, Target] = field(default_factory=dict)
    confirmed: bool = False

    @classmethod
    def from_manifest(cls, manifest: Manifest, *, confirmed: bool = False) -> "TargetSelection":
        overrides = {}
        if manifest.target is Target.HYBRID:
            for declaration in manifest.declarations:
                stated = declaration.attributes.get("target")
                if stated in ("simulated", "live"):
                    overrides[declaration.name] = Target(stated)
        return cls(default=manifest.target, overrides=overrides, confirmed=confirmed)

    def for_element(self, element: str) -> Target:
        if self.default is Target.HYBRID:
            # Under hybrid, anything not explicitly pointed at the real
            # environment stays simulated. The safe side is the default.
            return self.overrides.get(element, Target.SIMULATED)
        return self.default

    @property
    def touches_reality(self) -> bool:
        """Whether any element resolves to the live environment."""
        if self.default is Target.LIVE:
            return True
        return any(target is Target.LIVE for target in self.overrides.values())

    @property
    def live_elements(self) -> tuple[str, ...]:
        if self.default is Target.LIVE:
            return ("*",)
        return tuple(sorted(
            name for name, target in self.overrides.items() if target is Target.LIVE
        ))

    def to_dict(self) -> dict[str, Any]:
        return {
            "default": self.default.value,
            "overrides": {k: v.value for k, v in self.overrides.items()},
            "confirmed": self.confirmed,
            "touches_reality": self.touches_reality,
            "live_elements": list(self.live_elements),
        }

    def __str__(self) -> str:
        if not self.overrides:
            return self.default.value
        return f"{self.default.value} ({len(self.overrides)} overridden)"
