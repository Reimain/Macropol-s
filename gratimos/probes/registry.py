"""Probe registry: deciding who reads what.

Every probe scores a target; the highest score wins and the runner-up is
recorded. Keeping the runner-up matters — when a ``.txt`` full of CSV gets read
as text, the trace shows that the CSV probe was a close second, which is the
difference between a five-minute fix and an afternoon.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..ids import now_ns
from .base import BaseProbe, Capture, Probe, Target, iter_targets
from .database import SqliteProbe
from .documents import JsonProbe, TextProbe, YamlProbe
from .media import ImageProbe, VideoProbe
from .network import STRICT_POLICY, AccessPolicy, ApiProbe
from .runtime import ExecPolicy, ShellProbe
from .tabular import CsvProbe, XlsxProbe

#: A target scoring below this is not captured at all.
MIN_CONFIDENCE = 0.15


@dataclass(frozen=True, slots=True)
class Match:
    """Which probe claimed a target, and who else wanted it."""

    probe: Probe
    confidence: float
    runner_up: str = ""
    runner_up_confidence: float = 0.0

    @property
    def contested(self) -> bool:
        """True when the second choice was nearly as confident as the first."""
        return bool(self.runner_up) and (self.confidence - self.runner_up_confidence) < 0.15

    def to_dict(self) -> dict[str, Any]:
        return {
            "probe": self.probe.name,
            "confidence": round(self.confidence, 3),
            "runner_up": self.runner_up,
            "runner_up_confidence": round(self.runner_up_confidence, 3),
            "contested": self.contested,
        }


@dataclass(slots=True)
class Discovery:
    """The result of pointing the kernel at an environment."""

    root: str
    captures: list[Capture] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    contested: list[dict[str, Any]] = field(default_factory=list)
    ts_ns: int = field(default_factory=now_ns)

    @property
    def payloads(self) -> list[Any]:
        return [payload for capture in self.captures for payload in capture.payloads]

    @property
    def rows(self) -> int:
        return sum(capture.rows for capture in self.captures)

    def by_probe(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for capture in self.captures:
            counts[capture.probe] = counts.get(capture.probe, 0) + 1
        return dict(sorted(counts.items()))

    def warnings(self) -> list[str]:
        return [f"{c.target.entity}: {w}" for c in self.captures for w in c.warnings]

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "captures": len(self.captures),
            "payloads": len(self.payloads),
            "rows": self.rows,
            "by_probe": self.by_probe(),
            "skipped": self.skipped,
            "contested": self.contested,
            "warnings": self.warnings()[:50],
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Discovery {self.root} captures={len(self.captures)} rows={self.rows}>"


class ProbeRegistry:
    """Holds the probe set and routes targets to it."""

    def __init__(self, probes: Sequence[Probe] = ()) -> None:
        self._probes: list[Probe] = list(probes)
        self._lock = threading.RLock()

    def register(self, probe: Probe) -> Probe:
        with self._lock:
            self._probes = [p for p in self._probes if p.name != probe.name] + [probe]
        return probe

    def unregister(self, name: str) -> bool:
        with self._lock:
            before = len(self._probes)
            self._probes = [p for p in self._probes if p.name != name]
            return len(self._probes) != before

    def get(self, name: str) -> Probe | None:
        return next((p for p in self._probes if p.name == name), None)

    def names(self) -> list[str]:
        return sorted(p.name for p in self._probes)

    # -- routing ---------------------------------------------------------

    def rank(self, target: Target) -> list[tuple[Probe, float]]:
        scored: list[tuple[Probe, float]] = []
        for probe in self._probes:
            try:
                score = float(probe.sniff(target))
            except Exception:  # noqa: BLE001 - a broken probe must not stop discovery
                score = 0.0
            if score > 0:
                scored.append((probe, score))
        return sorted(scored, key=lambda item: item[1], reverse=True)

    def match(self, target: Target) -> Match | None:
        ranked = self.rank(target)
        if not ranked or ranked[0][1] < MIN_CONFIDENCE:
            return None
        probe, score = ranked[0]
        if len(ranked) > 1:
            return Match(probe, score, ranked[1][0].name, ranked[1][1])
        return Match(probe, score)

    def capture(self, target: Target | str | Path, *, limit: int = 0) -> Capture | None:
        """Capture one target with its best-matching probe."""
        resolved = target if isinstance(target, Target) else Target.of(target)
        match = self.match(resolved)
        if match is None:
            return None
        try:
            capture = match.probe.capture(resolved, limit=limit)
        except Exception as exc:  # noqa: BLE001 - report, do not abort the scan
            capture = Capture(probe=match.probe.name, target=resolved, confidence=match.confidence)
            capture.warnings.append(f"{type(exc).__name__}: {exc}")
        capture.confidence = match.confidence
        if match.contested:
            capture.warnings.append(
                f"contested: {match.runner_up} scored {match.runner_up_confidence:.2f} "
                f"against {match.probe.name} at {match.confidence:.2f}"
            )
        return capture

    def scan(
        self,
        root: str | Path,
        *,
        limit: int = 0,
        recursive: bool = True,
        max_files: int = 0,
        targets: Iterable[Target] | None = None,
    ) -> Discovery:
        """Walk an environment and capture everything a probe recognizes."""
        discovery = Discovery(root=str(root))
        candidates = targets if targets is not None else iter_targets(root, recursive=recursive)
        for index, target in enumerate(candidates):
            if max_files and index >= max_files:
                discovery.skipped.append({"uri": "…", "reason": f"max_files={max_files} reached"})
                break
            match = self.match(target)
            if match is None:
                discovery.skipped.append({"uri": target.uri, "reason": "no probe claimed this target"})
                continue
            if match.contested:
                discovery.contested.append({"uri": target.uri, **match.to_dict()})
            capture = self.capture(target, limit=limit)
            if capture is not None and (capture.payloads or capture.warnings):
                discovery.captures.append(capture)
        return discovery

    def __len__(self) -> int:
        return len(self._probes)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<ProbeRegistry probes={self.names()}>"


def default_registry(
    *,
    access_policy: AccessPolicy = STRICT_POLICY,
    exec_policy: ExecPolicy | None = None,
) -> ProbeRegistry:
    """The probe set the kernel boots with.

    Network and shell probes take their policies here, so the security posture
    of a run is decided once, at construction, rather than per call site.
    """
    return ProbeRegistry([
        JsonProbe(),
        CsvProbe(),
        XlsxProbe(),
        SqliteProbe(),
        YamlProbe(),
        ImageProbe(),
        VideoProbe(),
        ShellProbe(exec_policy),
        ApiProbe(access_policy),
        TextProbe(),  # last: the widest net, lowest confidence
    ])


__all__ = [
    "MIN_CONFIDENCE",
    "BaseProbe",
    "Capture",
    "Discovery",
    "Match",
    "Probe",
    "ProbeRegistry",
    "Target",
    "default_registry",
]
