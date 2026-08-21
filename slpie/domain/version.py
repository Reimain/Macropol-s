"""Versions and ranges across ecosystems that disagree about both.

Every package manager invented its own version grammar and its own range
syntax, and a dependency graph spanning npm, PyPI, Maven, Cargo and Go has to
compare them all. This module normalises versions into a comparable form and
parses each ecosystem's range dialect into one shared representation:
a disjunction of conjunctions of comparators.

One deliberate refusal: **an unparseable range is an error, never a wildcard.**
Silently widening `>=1.0,<2.0 || whatever-this-is` to "anything" would make a
constraint solver confidently wrong. Callers catch :class:`VersionError` and
record a gap instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from functools import total_ordering
from typing import Any, Iterable, Sequence

from ..errors import VersionError

_SEMVER = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?"
    r"(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)
_EPOCH = re.compile(r"^(\d+)!")
#: PEP 440 / Maven style pre-release markers glued to the release segment.
_MARKER = re.compile(r"(?<=[0-9])(?=(a|b|c|rc|alpha|beta|dev|post|pre|final|release)\b)", re.I)
_SPLIT = re.compile(r"[.\-_+]")
_HAS_DIGIT = re.compile(r"\d")
#: Alternating letter/digit runs, so `rc1` ranks as the marker `rc` then `1`.
_RUNS = re.compile(r"\d+|[A-Za-z]+")


def _atoms(part: str) -> list[Any]:
    """`post1` -> `["post", 1]`. Without this a marker glued to its number is
    an unrecognised string and loses its ordering weight entirely."""
    return [int(run) if run.isdigit() else run.lower() for run in _RUNS.findall(part)]

#: Ordering weight for common pre-release markers. Lower sorts earlier.
_MARKER_RANK: dict[str, int] = {
    "dev": -5, "alpha": -4, "a": -4, "beta": -3, "b": -3,
    "pre": -2, "c": -1, "rc": -1,
    "final": 1, "release": 1, "ga": 1, "post": 2, "sp": 3,
}


@total_ordering
@dataclass(frozen=True, slots=True)
class Version:
    """A comparable version.

    ``release`` is the numeric backbone; ``prerelease`` carries anything that
    sorts *before* the plain release, per semver rule 11. Build metadata is
    retained for display but excluded from comparison, also per semver.
    """

    release: tuple[int, ...]
    prerelease: tuple[Any, ...] = ()
    build: str = ""
    raw: str = ""
    epoch: int = 0
    is_semver: bool = False

    # -- parsing ---------------------------------------------------------

    @classmethod
    def parse(cls, value: str) -> "Version":
        text = (value or "").strip()
        if not text:
            raise VersionError("empty version")
        original = text
        text = text.lstrip("=vV").strip()
        if not _HAS_DIGIT.search(text):
            # `latest`, `master`, a typo, or a token that was never a version at
            # all. Guessing here is how a solver ends up confidently wrong.
            raise VersionError(f"not a version: {value!r}")

        epoch = 0
        if match := _EPOCH.match(text):
            epoch = int(match.group(1))
            text = text[match.end():]

        if match := _SEMVER.match(text):
            pre = match.group("prerelease")
            return cls(
                release=(int(match["major"]), int(match["minor"]), int(match["patch"])),
                prerelease=cls._prerelease_parts(pre) if pre else (),
                build=match.group("build") or "",
                raw=original,
                epoch=epoch,
                is_semver=True,
            )
        return cls._parse_loose(text, original, epoch)

    @classmethod
    def _parse_loose(cls, text: str, original: str, epoch: int) -> "Version":
        """Maven `1.0.0.RELEASE`, PEP 440 `1.0rc1`, Go pseudo-versions, and friends."""
        head, _, build = text.partition("+")
        head = _MARKER.sub("-", head)          # `1.0rc1` -> `1.0-rc1`
        head, _, pre = head.partition("-")

        release: list[int] = []
        trailing: list[Any] = []
        for part in _SPLIT.split(head):
            if part == "":
                continue
            if part.isdigit():
                # A numeric part after a non-numeric one is no longer the backbone.
                (trailing if trailing else release).append(int(part))
            else:
                trailing.extend(_atoms(part))
        if not release:
            release = [0]

        prerelease = cls._prerelease_parts(pre) if pre else ()
        if trailing:
            prerelease = (*prerelease, *trailing) if prerelease else tuple(trailing)
        return cls(
            release=tuple(release), prerelease=prerelease, build=build,
            raw=original, epoch=epoch, is_semver=False,
        )

    @staticmethod
    def _prerelease_parts(text: str) -> tuple[Any, ...]:
        parts: list[Any] = []
        for part in _SPLIT.split(text):
            if part == "":
                continue
            parts.extend(_atoms(part))
        return tuple(parts)

    # -- comparison ------------------------------------------------------

    def _key(self) -> tuple[Any, ...]:
        # Pad to a common width so 1.2 and 1.2.0 compare equal.
        release = tuple(self.release) + (0,) * (4 - len(self.release))
        return (self.epoch, release[:4], self._marker_class(), self._prerelease_key())

    def _marker_class(self) -> int:
        """Where a qualified version sits relative to the bare release.

        Semver rule 11 puts pre-releases *before* the release. But Maven and PEP
        440 also have qualifiers on the far side — `1.0.0.RELEASE` and `1.0.post1`
        come *after* `1.0.0` — so this is three-valued, not two.
        """
        if not self.prerelease:
            return 1
        for part in self.prerelease:
            rank = _MARKER_RANK.get(part) if isinstance(part, str) else None
            if rank is not None:
                return 2 if rank > 0 else 0
        return 0

    def _prerelease_key(self) -> tuple[Any, ...]:
        out: list[tuple[int, Any]] = []
        for part in self.prerelease:
            if isinstance(part, int):
                out.append((0, part))          # numeric identifiers sort lowest
            else:
                rank = _MARKER_RANK.get(part)
                out.append((1, rank) if rank is not None else (2, part))
        return tuple(out)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Version) and self._key() == other._key()

    def __lt__(self, other: "Version") -> bool:
        return self._key() < other._key()

    def __hash__(self) -> int:
        return hash(self._key())

    # -- accessors -------------------------------------------------------

    @property
    def major(self) -> int:
        return self.release[0] if self.release else 0

    @property
    def minor(self) -> int:
        return self.release[1] if len(self.release) > 1 else 0

    @property
    def patch(self) -> int:
        return self.release[2] if len(self.release) > 2 else 0

    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)

    @property
    def is_stable(self) -> bool:
        return not self.prerelease and self.major > 0

    def bump(self, level: str = "patch") -> "Version":
        major, minor, patch = self.major, self.minor, self.patch
        if level == "major":
            major, minor, patch = major + 1, 0, 0
        elif level == "minor":
            minor, patch = minor + 1, 0
        else:
            patch += 1
        return Version(release=(major, minor, patch), raw=f"{major}.{minor}.{patch}", is_semver=True)

    def change_from(self, other: "Version") -> str:
        """Which level changed — the input to upgrade risk scoring."""
        if self.major != other.major:
            return "major"
        if self.minor != other.minor:
            return "minor"
        if self.patch != other.patch:
            return "patch"
        return "none" if self == other else "prerelease"

    def __str__(self) -> str:
        return self.raw or self.text

    @property
    def text(self) -> str:
        base = ".".join(str(p) for p in self.release)
        if self.prerelease:
            base += "-" + ".".join(str(p) for p in self.prerelease)
        if self.build:
            base += "+" + self.build
        return f"{self.epoch}!{base}" if self.epoch else base


# --- ranges --------------------------------------------------------------


class Op(str, Enum):
    EQ = "="
    NE = "!="
    LT = "<"
    LTE = "<="
    GT = ">"
    GTE = ">="


@dataclass(frozen=True, slots=True)
class Comparator:
    """A single bound. Sugar like `^` and `~` is expanded away at parse time."""

    op: Op
    version: Version

    def allows(self, candidate: Version) -> bool:
        if self.op is Op.EQ:
            return candidate == self.version
        if self.op is Op.NE:
            return candidate != self.version
        if self.op is Op.LT:
            return candidate < self.version
        if self.op is Op.LTE:
            return candidate <= self.version
        if self.op is Op.GT:
            return candidate > self.version
        return candidate >= self.version

    def __str__(self) -> str:
        return f"{self.op.value}{self.version.text}"


@dataclass(frozen=True, slots=True)
class VersionRange:
    """A disjunction of conjunctions: ``(a AND b) OR (c)``.

    Every ecosystem's dialect collapses into this shape, which is also the form
    a PubGrub-style solver wants.
    """

    clauses: tuple[tuple[Comparator, ...], ...]
    raw: str = ""
    ecosystem: str = "generic"

    def allows(self, candidate: Version | str) -> bool:
        version = candidate if isinstance(candidate, Version) else Version.parse(candidate)
        if self.is_any:
            return True
        return any(
            all(comparator.allows(version) for comparator in clause)
            for clause in self.clauses
        )

    @property
    def is_any(self) -> bool:
        return not self.clauses or all(not clause for clause in self.clauses)

    @property
    def is_exact(self) -> bool:
        return (
            len(self.clauses) == 1 and len(self.clauses[0]) == 1
            and self.clauses[0][0].op is Op.EQ
        )

    @property
    def exact_version(self) -> Version | None:
        return self.clauses[0][0].version if self.is_exact else None

    def best(self, candidates: Iterable[Version | str]) -> Version | None:
        """Highest allowed version — the default upgrade target."""
        allowed = [
            v if isinstance(v, Version) else Version.parse(v)
            for v in candidates
        ]
        allowed = [v for v in allowed if self.allows(v)]
        return max(allowed) if allowed else None

    @classmethod
    def any(cls) -> "VersionRange":
        return cls(clauses=((),), raw="*")

    @classmethod
    def exact(cls, version: Version | str) -> "VersionRange":
        parsed = version if isinstance(version, Version) else Version.parse(version)
        return cls(clauses=((Comparator(Op.EQ, parsed),),), raw=parsed.text)

    def __str__(self) -> str:
        if self.raw:
            return self.raw
        return " || ".join(" ".join(str(c) for c in clause) or "*" for clause in self.clauses)

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw, "ecosystem": self.ecosystem, "any": self.is_any,
            "clauses": [[str(c) for c in clause] for clause in self.clauses],
        }


# --- range parsing -------------------------------------------------------

_WILDCARD = {"*", "x", "X", ""}
_OP_RE = re.compile(r"^(>=|<=|==|!=|~=|\^|~|>|<|=)?\s*(.+)$")


def _bounds_for_caret(version: Version) -> list[Comparator]:
    """npm/Cargo `^`: compatible within the leftmost non-zero component."""
    lower = Comparator(Op.GTE, version)
    if version.major > 0:
        upper = Version(release=(version.major + 1, 0, 0), is_semver=True)
    elif version.minor > 0:
        upper = Version(release=(0, version.minor + 1, 0), is_semver=True)
    else:
        upper = Version(release=(0, 0, version.patch + 1), is_semver=True)
    return [lower, Comparator(Op.LT, upper)]


def _bounds_for_tilde(version: Version, *, specified: int) -> list[Comparator]:
    """`~`: patch-level changes when minor is given, minor-level when it is not."""
    lower = Comparator(Op.GTE, version)
    if specified >= 2:
        upper = Version(release=(version.major, version.minor + 1, 0), is_semver=True)
    else:
        upper = Version(release=(version.major + 1, 0, 0), is_semver=True)
    return [lower, Comparator(Op.LT, upper)]


def _bounds_for_wildcard(parts: list[str]) -> list[Comparator]:
    """`1.2.*` / `1.x` — a prefix match expressed as a half-open interval."""
    fixed = [int(p) for p in parts if p not in _WILDCARD and p.isdigit()]
    if not fixed:
        return []
    lower = Version(release=tuple(fixed + [0] * (3 - len(fixed))), is_semver=True)
    upper_parts = fixed[:-1] + [fixed[-1] + 1]
    upper = Version(release=tuple(upper_parts + [0] * (3 - len(upper_parts))), is_semver=True)
    return [Comparator(Op.GTE, lower), Comparator(Op.LT, upper)]


def _parse_comparator(token: str, *, ecosystem: str) -> list[Comparator]:
    token = token.strip()
    if not token or token in _WILDCARD:
        return []

    match = _OP_RE.match(token)
    if not match:
        raise VersionError(f"cannot parse constraint {token!r}")
    op_text, value = match.group(1) or "", match.group(2).strip()

    # A wildcard is a whole *component* — `1.2.*`, `1.x`. Substring matching
    # would misread `1.0.0-x86` as "any 1.x", which is a very different range.
    parts = _SPLIT.split(value.lstrip("vV"))
    if op_text in ("", "=", "==") and any(p in _WILDCARD for p in parts):
        return _bounds_for_wildcard(parts)

    version = Version.parse(value)
    specified = len([p for p in _SPLIT.split(value.lstrip("vV")) if p.isdigit()])

    if op_text == "^":
        return _bounds_for_caret(version)
    if op_text == "~":
        return _bounds_for_tilde(version, specified=specified)
    if op_text == "~=":
        # PEP 440 compatible-release: `~=1.4.2` means `>=1.4.2, <1.5.0`.
        if specified < 2:
            raise VersionError(f"~= requires at least two components: {token!r}")
        upper_parts = list(version.release[: specified - 1])
        upper_parts[-1] += 1
        upper = Version(release=tuple(upper_parts), is_semver=False)
        return [Comparator(Op.GTE, version), Comparator(Op.LT, upper)]
    if op_text in (">=", "<=", ">", "<", "!="):
        return [Comparator(Op(op_text), version)]
    return [Comparator(Op.EQ, version)]


def _parse_maven(spec: str) -> tuple[tuple[Comparator, ...], ...]:
    """Maven interval notation: `[1.0,2.0)`, `[1.0]`, `(,1.0]`."""
    clauses: list[tuple[Comparator, ...]] = []
    for part in re.findall(r"[\[\(][^\]\)]*[\]\)]", spec):
        open_bracket, close_bracket = part[0], part[-1]
        body = part[1:-1]
        if "," not in body:
            clauses.append((Comparator(Op.EQ, Version.parse(body)),))
            continue
        low, _, high = body.partition(",")
        bounds: list[Comparator] = []
        if low.strip():
            bounds.append(Comparator(
                Op.GTE if open_bracket == "[" else Op.GT, Version.parse(low)
            ))
        if high.strip():
            bounds.append(Comparator(
                Op.LTE if close_bracket == "]" else Op.LT, Version.parse(high)
            ))
        clauses.append(tuple(bounds))
    if not clauses:
        # A bare Maven version is a *soft* requirement meaning "at least this".
        return ((Comparator(Op.GTE, Version.parse(spec)),),)
    return tuple(clauses)


def parse_range(spec: str, *, ecosystem: str = "generic") -> VersionRange:
    """Parse an ecosystem's range dialect into the shared representation.

    Raises :class:`VersionError` on anything it cannot represent — an
    unparseable constraint must never silently become a wildcard.
    """
    raw = (spec or "").strip()
    if not raw or raw in _WILDCARD or raw.lower() in ("any", "latest", "*"):
        return VersionRange(clauses=((),), raw=raw or "*", ecosystem=ecosystem)

    if ecosystem in ("maven", "gradle") and ("[" in raw or "(" in raw):
        return VersionRange(clauses=_parse_maven(raw), raw=raw, ecosystem=ecosystem)

    # npm's `||` is the only OR syntax in wide use; PEP 440 and Cargo use commas
    # purely as AND.
    clauses: list[tuple[Comparator, ...]] = []
    for alternative in raw.split("||"):
        tokens = [t for t in re.split(r",|\s+", alternative.strip()) if t]
        comparators: list[Comparator] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            # npm hyphen ranges: `1.2.3 - 2.3.4`
            if token == "-" and comparators and index + 1 < len(tokens):
                comparators[-1] = Comparator(Op.GTE, comparators[-1].version)
                comparators.append(Comparator(Op.LTE, Version.parse(tokens[index + 1])))
                index += 2
                continue
            # An operator separated from its version by a space.
            if token in (">=", "<=", ">", "<", "=", "==", "!=", "~=", "^", "~") and index + 1 < len(tokens):
                token = token + tokens[index + 1]
                index += 1
            comparators.extend(_parse_comparator(token, ecosystem=ecosystem))
            index += 1
        clauses.append(tuple(comparators))

    return VersionRange(clauses=tuple(clauses), raw=raw, ecosystem=ecosystem)
