"""Licence strings as discoverers actually find them, made into SPDX.

`slpie.domain.license` parses SPDX expressions and analyses obligations. It is
correct and it assumes it is given SPDX. Discoverers are not: they read whatever
a publisher typed, and publishers typed this —

    "MIT"                                     already SPDX
    {"type": "ISC"}                           npm, before SPDX existed
    [{"type": "MIT"}, {"type": "Apache-2.0"}] npm's legacy array
    "License :: OSI Approved :: MIT License"  a PyPI classifier
    "NOASSERTION"                             GitHub, meaning "we don't know"
    "Apache 2"                                a human, approximately
    "BSD"                                     ambiguous between three licences

This module turns those into an SPDX expression or into nothing. **Nothing is
the important case.** An unrecognised licence string must not become
`NOASSERTION` dressed as a licence, and it must not be guessed: `"BSD"` alone
is genuinely ambiguous between BSD-2-Clause, BSD-3-Clause and BSD-4-Clause,
which carry different obligations, and picking one produces a compliance answer
somebody will rely on.

So the contract is: return the SPDX expression when confident, and an empty
string plus a stated reason when not. The governance layer treats an empty
licence as a finding, which is right — "we could not determine the licence" is
actionable, and a wrong licence is not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..domain.license import (
    LicenseExpression,
    canonical_id,
    normalize,
    parse_expression,
)
from ..errors import LicenseError

#: Values that explicitly mean "unknown", and must never become a licence.
UNKNOWN = frozenset({
    "", "noassertion", "none", "unknown", "unlicensed", "see license",
    "see licence", "see license file", "proprietary", "other", "custom",
    "n/a", "na", "-",
})

#: Strings people write that map to exactly one SPDX identifier without guessing.
#: Everything here is unambiguous; anything requiring a choice is deliberately
#: absent, however common.
SYNONYMS: dict[str, str] = {
    "apache 2": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "the apache license, version 2.0": "Apache-2.0",
    "mit license": "MIT",
    "the mit license": "MIT",
    "expat": "MIT",
    "isc license": "ISC",
    "mozilla public license 2.0": "MPL-2.0",
    "mpl 2.0": "MPL-2.0",
    "eclipse public license 2.0": "EPL-2.0",
    "gnu general public license v2": "GPL-2.0-only",
    "gnu general public license v3": "GPL-3.0-only",
    "gnu lesser general public license v3": "LGPL-3.0-only",
    "gnu affero general public license v3": "AGPL-3.0-only",
    "the unlicense": "Unlicense",
    "public domain": "CC0-1.0",
    "cc0": "CC0-1.0",
    "zlib license": "Zlib",
    "boost software license 1.0": "BSL-1.0",
}

#: Strings that are *genuinely* ambiguous, with what they are ambiguous between.
#: Listed so the refusal can say why rather than merely failing.
AMBIGUOUS: dict[str, tuple[str, ...]] = {
    "bsd": ("BSD-2-Clause", "BSD-3-Clause", "BSD-4-Clause"),
    "bsd license": ("BSD-2-Clause", "BSD-3-Clause", "BSD-4-Clause"),
    "gpl": ("GPL-2.0-only", "GPL-3.0-only", "GPL-2.0-or-later"),
    "gplv2": ("GPL-2.0-only", "GPL-2.0-or-later"),
    "gplv3": ("GPL-3.0-only", "GPL-3.0-or-later"),
    "lgpl": ("LGPL-2.1-only", "LGPL-3.0-only"),
    "creative commons": ("CC-BY-4.0", "CC-BY-SA-4.0", "CC0-1.0"),
    "artistic": ("Artistic-1.0", "Artistic-2.0"),
}

#: `License :: OSI Approved :: MIT License` → the part worth mapping.
_CLASSIFIER = re.compile(r"^License\s*::\s*(?:OSI Approved\s*::\s*)?(?P<name>.+)$")

#: Trailing noise publishers add: "MIT License (MIT)", "Apache-2.0 license".
_TRAILING = re.compile(r"\s*(?:\([^)]*\)|licen[cs]e)\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Normalised:
    """A licence reading, and why it says what it says."""

    expression: str
    confident: bool
    reason: str = ""
    original: str = ""
    candidates: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.expression)

    @property
    def parsed(self) -> LicenseExpression | None:
        if not self.expression:
            return None
        try:
            return parse_expression(self.expression)
        except LicenseError:
            return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "expression": self.expression, "confident": self.confident,
            "reason": self.reason, "original": self.original,
            "candidates": list(self.candidates),
        }

    def __str__(self) -> str:
        return self.expression or f"unknown ({self.reason})"


def _forms(text: str) -> tuple[str, ...]:
    """Every spelling worth looking up, most literal first.

    Returning several rather than one is the fix for a real bug: stripping a
    trailing "license" before the synonym lookup made every synonym that *ends*
    in "license" unreachable — "the mit license", "apache software license",
    "isc license" and the rest were in the table and could never match.
    """
    stripped = text.strip()
    lowered = stripped.lower()
    found = [lowered]

    classifier = _CLASSIFIER.match(stripped)
    if classifier:
        found.append(classifier.group("name").strip().lower())

    for candidate in list(found):
        trimmed = _TRAILING.sub("", candidate).strip()
        if trimmed and trimmed not in found:
            found.append(trimmed)

    return tuple(found)


def normalize_license(value: Any) -> Normalised:
    """Whatever a discoverer found → an SPDX expression, or an honest nothing.

    Handles npm's three shapes, PyPI classifiers, GitHub's `NOASSERTION`, and
    the approximations people type. Refuses to guess where a guess would be a
    compliance answer somebody relies on.
    """
    original = _render(value)

    if isinstance(value, Mapping):
        return normalize_license(value.get("type") or value.get("name") or "")

    if isinstance(value, (list, tuple)):
        readings = [normalize_license(item) for item in value]
        found = [reading.expression for reading in readings if reading.expression]
        if not found:
            return Normalised(
                "", False, "no entry in the list resolved to an SPDX identifier",
                original,
            )
        if len(found) == 1:
            return Normalised(found[0], True, "one entry resolved", original)
        # npm's legacy array offered a choice, which is `OR` — never `AND`.
        # Reading it as `AND` would compound obligations the publisher never
        # imposed and refuse packages that are fine.
        return Normalised(
            " OR ".join(found), True,
            "a legacy licence array offers a choice, so it is read as OR",
            original,
        )

    if not isinstance(value, str):
        return Normalised("", False, f"{type(value).__name__} is not a licence", original)

    text = value.strip()
    if text.lower() in UNKNOWN:
        return Normalised(
            "", False,
            f"{text or 'an empty value'!r} states that the licence is unknown",
            original,
        )

    forms = _forms(text)

    # Ambiguity is checked *before* the SPDX pass, and the order is the whole
    # point. `normalize("BSD")` happens to parse, so trying SPDX first lets the
    # exact string this module exists to refuse straight through — wearing the
    # confidence of a successful parse.
    for form in forms:
        if form in AMBIGUOUS:
            options = AMBIGUOUS[form]
            return Normalised(
                "", False,
                f"{text!r} is ambiguous between {', '.join(options)}, which carry "
                f"different obligations; record the actual identifier",
                original, candidates=options,
            )

    # A curated exact match beats the parser. `normalize("the MIT License")`
    # returns that phrase with no unknown terms flagged, so running the SPDX
    # pass first swallowed every human spelling before the table was consulted.
    # Where this module knows exactly what a string means, that wins.
    for form in forms:
        if form in SYNONYMS:
            return Normalised(
                SYNONYMS[form], True, f"{text!r} is a known spelling", original
            )

    # Already SPDX, including compound expressions.
    try:
        expression = normalize(text)
        if not expression.unknown_terms:
            return Normalised(
                expression.to_string(), True, "already a valid SPDX expression", original
            )
    except LicenseError:
        pass

    # A last attempt at the identifier itself, e.g. "apache-2.0" in any case.
    for form in forms:
        try:
            candidate = normalize(canonical_id(form))
        except LicenseError:
            continue
        if not candidate.unknown_terms:
            return Normalised(
                candidate.to_string(), True, "resolved after normalising case",
                original,
            )

    return Normalised(
        "", False,
        f"{text!r} is not a recognised SPDX identifier and is not a known "
        f"spelling of one; guessing would produce a compliance answer nobody "
        f"could defend",
        original,
    )


def _render(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return str(value.get("type") or value.get("name") or value)
    if isinstance(value, (list, tuple)):
        return ", ".join(_render(item) for item in value)
    return str(value)


def from_classifiers(classifiers: Sequence[str]) -> Normalised:
    """PyPI's `License ::` classifiers → one expression.

    Several classifiers on one package usually means a dual licence, so they
    are joined with `OR` — the same reading as npm's legacy array, for the same
    reason: the publisher offered a choice.
    """
    readings = [
        normalize_license(entry) for entry in classifiers
        if entry.strip().startswith("License")
    ]
    found = [reading.expression for reading in readings if reading.expression]
    if not found:
        ambiguous = [r for r in readings if r.candidates]
        if ambiguous:
            return ambiguous[0]
        return Normalised("", False, "no licence classifier resolved", "")
    unique = list(dict.fromkeys(found))
    if len(unique) == 1:
        return Normalised(unique[0], True, "one licence classifier", "")
    return Normalised(
        " OR ".join(unique), True,
        "several licence classifiers, read as a choice", "",
    )
