"""Licences as expressions, not strings.

A dependency's licence field is rarely a single identifier. It is an SPDX
*expression* — `MIT OR Apache-2.0`, `(MIT AND BSD-3-Clause)`,
`GPL-2.0-only WITH Classpath-exception-2.0` — and the difference matters: a
disjunction lets you *choose* the compatible branch, a conjunction imposes every
obligation at once. Flattening that to a label is how a compliance report ends
up wrong in the safe-looking direction.

So expressions are parsed into a small tree, and compatibility is evaluated over
the tree with the distribution context taken into account. The same package can
be perfectly compliant inside a service and a violation inside a shipped binary;
the context is an input, never an assumption.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Sequence

from ..errors import LicenseError


class Obligation(str, Enum):
    """What a licence family demands in return.

    This is the axis compatibility actually turns on. Two permissive licences
    with different names rarely conflict; a strong copyleft and a proprietary
    distribution always do.
    """

    PUBLIC_DOMAIN = "public_domain"
    PERMISSIVE = "permissive"            # MIT, BSD, Apache — attribution
    WEAK_COPYLEFT = "weak_copyleft"      # LGPL, MPL, EPL — file/library scope
    STRONG_COPYLEFT = "strong_copyleft"  # GPL — whole derived work
    NETWORK_COPYLEFT = "network_copyleft"  # AGPL — triggers on network use
    PROPRIETARY = "proprietary"
    UNKNOWN = "unknown"                  # unstated, or an identifier we cannot resolve

    @property
    def rank(self) -> int:
        """How demanding, ascending. Drives "the strictest obligation wins"."""
        return _OBLIGATION_RANK[self]

    @property
    def viral(self) -> bool:
        return self in (
            Obligation.WEAK_COPYLEFT,
            Obligation.STRONG_COPYLEFT,
            Obligation.NETWORK_COPYLEFT,
        )


_OBLIGATION_RANK = {
    Obligation.PUBLIC_DOMAIN: 0,
    Obligation.PERMISSIVE: 1,
    Obligation.WEAK_COPYLEFT: 2,
    Obligation.STRONG_COPYLEFT: 3,
    Obligation.NETWORK_COPYLEFT: 4,
    Obligation.PROPRIETARY: 5,
    Obligation.UNKNOWN: 6,
}


class Distribution(str, Enum):
    """How the software reaches its users — the context copyleft triggers on."""

    INTERNAL = "internal"        # never leaves the organisation
    NETWORK_SERVICE = "network_service"  # SaaS: AGPL triggers, GPL does not
    DISTRIBUTED_BINARY = "distributed_binary"
    DISTRIBUTED_SOURCE = "distributed_source"

    @property
    def triggers_network_copyleft(self) -> bool:
        return self is not Distribution.INTERNAL

    @property
    def triggers_copyleft(self) -> bool:
        return self in (
            Distribution.DISTRIBUTED_BINARY,
            Distribution.DISTRIBUTED_SOURCE,
        )


class Linkage(str, Enum):
    """How the dependency is incorporated — what weak copyleft turns on."""

    DYNAMIC = "dynamic"    # LGPL is satisfied
    STATIC = "static"      # LGPL imposes its terms
    SOURCE = "source"      # vendored or copied in
    SEPARATE = "separate"  # a separate process; not a derived work


#: SPDX identifier → obligation family. Deliberately partial: an identifier
#: absent here resolves to UNKNOWN and raises a finding rather than being
#: assumed permissive.
_FAMILIES: dict[str, Obligation] = {
    "cc0-1.0": Obligation.PUBLIC_DOMAIN,
    "unlicense": Obligation.PUBLIC_DOMAIN,
    "0bsd": Obligation.PUBLIC_DOMAIN,
    "wtfpl": Obligation.PUBLIC_DOMAIN,
    "mit": Obligation.PERMISSIVE,
    "mit-0": Obligation.PERMISSIVE,
    "isc": Obligation.PERMISSIVE,
    "bsd-2-clause": Obligation.PERMISSIVE,
    "bsd-3-clause": Obligation.PERMISSIVE,
    "apache-2.0": Obligation.PERMISSIVE,
    "python-2.0": Obligation.PERMISSIVE,
    "psf-2.0": Obligation.PERMISSIVE,
    "zlib": Obligation.PERMISSIVE,
    "postgresql": Obligation.PERMISSIVE,
    "ms-pl": Obligation.PERMISSIVE,
    "bsl-1.0": Obligation.PERMISSIVE,
    "unicode-dfs-2016": Obligation.PERMISSIVE,
    "openssl": Obligation.PERMISSIVE,
    "ncsa": Obligation.PERMISSIVE,
    "artistic-2.0": Obligation.PERMISSIVE,
    "afl-2.1": Obligation.PERMISSIVE,
    "cc-by-4.0": Obligation.PERMISSIVE,
    "cc-by-sa-4.0": Obligation.WEAK_COPYLEFT,
    "mpl-2.0": Obligation.WEAK_COPYLEFT,
    "mpl-1.1": Obligation.WEAK_COPYLEFT,
    "epl-1.0": Obligation.WEAK_COPYLEFT,
    "epl-2.0": Obligation.WEAK_COPYLEFT,
    "cddl-1.0": Obligation.WEAK_COPYLEFT,
    "lgpl-2.1-only": Obligation.WEAK_COPYLEFT,
    "lgpl-2.1-or-later": Obligation.WEAK_COPYLEFT,
    "lgpl-3.0-only": Obligation.WEAK_COPYLEFT,
    "lgpl-3.0-or-later": Obligation.WEAK_COPYLEFT,
    "gpl-2.0-only": Obligation.STRONG_COPYLEFT,
    "gpl-2.0-or-later": Obligation.STRONG_COPYLEFT,
    "gpl-3.0-only": Obligation.STRONG_COPYLEFT,
    "gpl-3.0-or-later": Obligation.STRONG_COPYLEFT,
    "agpl-3.0-only": Obligation.NETWORK_COPYLEFT,
    "agpl-3.0-or-later": Obligation.NETWORK_COPYLEFT,
    "sspl-1.0": Obligation.NETWORK_COPYLEFT,
    "elastic-2.0": Obligation.PROPRIETARY,
    "bsl-1.1": Obligation.PROPRIETARY,
    "proprietary": Obligation.PROPRIETARY,
    "commercial": Obligation.PROPRIETARY,
    "nonstandard": Obligation.UNKNOWN,
}

#: Legacy and colloquial spellings seen in real manifests, mapped to SPDX.
_ALIASES: dict[str, str] = {
    "apache": "apache-2.0", "apache2": "apache-2.0", "apache 2.0": "apache-2.0",
    "apache license 2.0": "apache-2.0", "asl 2.0": "apache-2.0", "apache-2": "apache-2.0",
    "bsd": "bsd-3-clause", "new bsd": "bsd-3-clause", "bsd3": "bsd-3-clause",
    "simplified bsd": "bsd-2-clause", "bsd2": "bsd-2-clause",
    "mit license": "mit", "the mit license": "mit", "expat": "mit",
    "gpl": "gpl-3.0-or-later", "gplv2": "gpl-2.0-only", "gplv3": "gpl-3.0-only",
    "gpl-2.0": "gpl-2.0-only", "gpl-3.0": "gpl-3.0-only", "gpl2": "gpl-2.0-only",
    "gpl3": "gpl-3.0-only", "gpl-2.0+": "gpl-2.0-or-later", "gpl-3.0+": "gpl-3.0-or-later",
    "lgpl": "lgpl-3.0-or-later", "lgplv2.1": "lgpl-2.1-only", "lgplv3": "lgpl-3.0-only",
    "lgpl-2.1": "lgpl-2.1-only", "lgpl-3.0": "lgpl-3.0-only",
    "lgpl-2.1+": "lgpl-2.1-or-later", "lgpl-3.0+": "lgpl-3.0-or-later",
    "agpl": "agpl-3.0-or-later", "agpl-3.0": "agpl-3.0-only", "agplv3": "agpl-3.0-only",
    "mpl": "mpl-2.0", "mpl2": "mpl-2.0", "mozilla public license 2.0": "mpl-2.0",
    "epl": "epl-2.0", "python software foundation license": "python-2.0",
    "cc0": "cc0-1.0", "public domain": "cc0-1.0", "unlicensed": "proprietary",
    "isc license": "isc", "zlib/libpng": "zlib",
}

#: Exceptions that neutralise the copyleft they qualify.
_NEUTRALISING_EXCEPTIONS = frozenset({
    "classpath-exception-2.0",
    "gcc-exception-3.1",
    "llvm-exception",
    "linux-syscall-note",
    "autoconf-exception-3.0",
    "bison-exception-2.2",
})


def canonical_id(text: str) -> str:
    """Best-effort SPDX identifier for whatever a manifest actually contained."""
    cleaned = (text or "").strip().strip("\"'")
    lowered = cleaned.lower()
    if lowered in _ALIASES:
        lowered = _ALIASES[lowered]
    if lowered in _FAMILIES:
        # Recover canonical SPDX casing where we know it.
        return _CANONICAL_CASE.get(lowered, cleaned)
    return cleaned


def obligation_of(identifier: str) -> Obligation:
    """The obligation family of one identifier. Unknown stays unknown."""
    lowered = (identifier or "").strip().strip("\"'").lower()
    lowered = _ALIASES.get(lowered, lowered)
    if lowered in _FAMILIES:
        return _FAMILIES[lowered]
    # `LicenseRef-` is SPDX's escape hatch for anything not on the list.
    if lowered.startswith("licenseref-"):
        return Obligation.UNKNOWN
    return Obligation.UNKNOWN


_CANONICAL_CASE: dict[str, str] = {
    "mit": "MIT", "mit-0": "MIT-0", "isc": "ISC", "apache-2.0": "Apache-2.0",
    "bsd-2-clause": "BSD-2-Clause", "bsd-3-clause": "BSD-3-Clause", "0bsd": "0BSD",
    "cc0-1.0": "CC0-1.0", "unlicense": "Unlicense", "wtfpl": "WTFPL", "zlib": "Zlib",
    "mpl-2.0": "MPL-2.0", "mpl-1.1": "MPL-1.1", "epl-1.0": "EPL-1.0", "epl-2.0": "EPL-2.0",
    "cddl-1.0": "CDDL-1.0", "python-2.0": "Python-2.0", "psf-2.0": "PSF-2.0",
    "postgresql": "PostgreSQL", "ms-pl": "MS-PL", "bsl-1.0": "BSL-1.0", "bsl-1.1": "BSL-1.1",
    "lgpl-2.1-only": "LGPL-2.1-only", "lgpl-2.1-or-later": "LGPL-2.1-or-later",
    "lgpl-3.0-only": "LGPL-3.0-only", "lgpl-3.0-or-later": "LGPL-3.0-or-later",
    "gpl-2.0-only": "GPL-2.0-only", "gpl-2.0-or-later": "GPL-2.0-or-later",
    "gpl-3.0-only": "GPL-3.0-only", "gpl-3.0-or-later": "GPL-3.0-or-later",
    "agpl-3.0-only": "AGPL-3.0-only", "agpl-3.0-or-later": "AGPL-3.0-or-later",
    "sspl-1.0": "SSPL-1.0", "elastic-2.0": "Elastic-2.0",
}


# --- expressions ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LicenseTerm:
    """One licence, optionally qualified by an exception."""

    identifier: str
    exception: str = ""

    @property
    def obligation(self) -> Obligation:
        base = obligation_of(self.identifier)
        if base.viral and self.exception.lower() in _NEUTRALISING_EXCEPTIONS:
            # A Classpath exception is the whole reason OpenJDK is usable at all.
            return Obligation.PERMISSIVE
        return base

    @property
    def known(self) -> bool:
        return self.obligation is not Obligation.UNKNOWN

    def to_string(self) -> str:
        return f"{self.identifier} WITH {self.exception}" if self.exception else self.identifier

    def __str__(self) -> str:
        return self.to_string()


@dataclass(frozen=True, slots=True)
class LicenseExpression:
    """A parsed SPDX expression: terms combined by AND / OR.

    Stored in disjunctive normal form — ``options`` is a tuple of alternatives,
    each a conjunction of terms that apply *together*. `MIT OR Apache-2.0` gives
    two options; `MIT AND GPL-3.0-only` gives one option with two terms.
    """

    options: tuple[tuple[LicenseTerm, ...], ...]
    raw: str = ""

    @classmethod
    def parse(cls, text: str) -> "LicenseExpression":
        return parse_expression(text)

    @property
    def terms(self) -> tuple[LicenseTerm, ...]:
        seen: dict[str, LicenseTerm] = {}
        for option in self.options:
            for term in option:
                seen.setdefault(term.to_string(), term)
        return tuple(seen.values())

    @property
    def is_choice(self) -> bool:
        """True when the consumer may pick a branch — a remediation in itself."""
        return len(self.options) > 1

    @property
    def obligation(self) -> Obligation:
        """The obligation you incur if you take the *least* demanding option.

        Within one option every term binds, so the strictest wins; across options
        you choose, so the most permissive wins.
        """
        if not self.options:
            return Obligation.UNKNOWN
        per_option = [
            max((term.obligation for term in option), key=lambda o: o.rank)
            for option in self.options
            if option
        ]
        return min(per_option, key=lambda o: o.rank) if per_option else Obligation.UNKNOWN

    @property
    def unknown_terms(self) -> tuple[LicenseTerm, ...]:
        return tuple(term for term in self.terms if not term.known)

    def preferred_option(self) -> tuple[LicenseTerm, ...]:
        """The branch a compliance officer would choose — least demanding."""
        if not self.options:
            return ()
        return min(
            self.options,
            key=lambda option: max(
                (term.obligation.rank for term in option), default=Obligation.UNKNOWN.rank
            ),
        )

    def to_string(self) -> str:
        if self.raw:
            return self.raw
        rendered = [" AND ".join(t.to_string() for t in option) for option in self.options]
        return " OR ".join(f"({r})" if " AND " in r else r for r in rendered)

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "expression": self.to_string(),
            "obligation": self.obligation.value,
            "is_choice": self.is_choice,
            "options": [[t.to_string() for t in option] for option in self.options],
            "unknown": [t.to_string() for t in self.unknown_terms],
        }

    def __str__(self) -> str:
        return self.to_string()


_TOKEN = re.compile(r"\(|\)|\bAND\b|\bOR\b|\bWITH\b|[^\s()]+", re.I)


def parse_expression(text: str) -> LicenseExpression:
    """Parse an SPDX licence expression into disjunctive normal form.

    Raises :class:`LicenseError` on malformed input. An unparseable expression
    is a compliance gap to report, not a licence to assume.
    """
    raw = (text or "").strip()
    if not raw:
        raise LicenseError("empty licence expression")

    # Colloquial names contain spaces ("Apache 2.0", "Mozilla Public License 2.0").
    # Resolve the whole string first — tokenizing it would split the name apart.
    if raw.lower().strip("\"'") in _ALIASES:
        return LicenseExpression(
            options=((LicenseTerm(identifier=canonical_id(raw)),),), raw=raw,
        )

    tokens = _TOKEN.findall(raw)
    if not tokens:
        raise LicenseError(f"no licence identifiers in {raw!r}")

    position = 0

    def peek() -> str | None:
        return tokens[position] if position < len(tokens) else None

    def take() -> str:
        nonlocal position
        token = tokens[position]
        position += 1
        return token

    def parse_or() -> tuple[tuple[LicenseTerm, ...], ...]:
        options = list(parse_and())
        while (token := peek()) and token.upper() == "OR":
            take()
            options.extend(parse_and())
        return tuple(options)

    def parse_and() -> tuple[tuple[LicenseTerm, ...], ...]:
        left = parse_atom()
        while (token := peek()) and token.upper() == "AND":
            take()
            right = parse_atom()
            # Distribute AND over OR to keep disjunctive normal form.
            left = tuple(a + b for a in left for b in right)
        return left

    def parse_atom() -> tuple[tuple[LicenseTerm, ...], ...]:
        token = peek()
        if token is None:
            raise LicenseError(f"unexpected end of licence expression in {raw!r}")
        if token == "(":
            take()
            inner = parse_or()
            if peek() != ")":
                raise LicenseError(f"unbalanced parentheses in {raw!r}")
            take()
            return inner
        if token in (")",) or token.upper() in ("AND", "OR", "WITH"):
            raise LicenseError(f"unexpected {token!r} in {raw!r}")
        # `GPL-3.0+` keeps its `+`: it means "or later", which is a different
        # licence set from `GPL-3.0-only` and the alias table knows the difference.
        identifier = canonical_id(take())
        exception = ""
        if (nxt := peek()) and nxt.upper() == "WITH":
            take()
            following = peek()
            if following is None or following.upper() in ("AND", "OR", ")"):
                raise LicenseError(f"WITH without an exception in {raw!r}")
            exception = take()
        return ((LicenseTerm(identifier=identifier, exception=exception),),)

    options = parse_or()
    if position != len(tokens):
        raise LicenseError(f"trailing tokens in licence expression {raw!r}")
    return LicenseExpression(options=options, raw=raw)


# --- compatibility -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompatibilityVerdict:
    """Whether a dependency's licence is usable here, and why.

    ``reason`` is written to be shown to a human unedited — a compliance finding
    that says only "incompatible" makes the engineer reading it do the work
    again.
    """

    compatible: bool
    obligation: Obligation
    reason: str
    remediation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "compatible": self.compatible,
            "obligation": self.obligation.value,
            "reason": self.reason,
            "remediation": self.remediation,
        }

    def __bool__(self) -> bool:
        return self.compatible


def check_compatibility(
    dependency: LicenseExpression | str,
    *,
    project: LicenseExpression | str | None = None,
    distribution: Distribution = Distribution.NETWORK_SERVICE,
    linkage: Linkage = Linkage.DYNAMIC,
) -> CompatibilityVerdict:
    """Evaluate one dependency licence in a stated distribution context.

    The context is required rather than assumed because the answer genuinely
    changes: AGPL in an internal tool is fine, AGPL in a SaaS product is a
    source-disclosure obligation, and GPL in a shipped binary is another thing
    again.
    """
    expression = (
        dependency if isinstance(dependency, LicenseExpression)
        else parse_expression(dependency)
    )
    project_expression = (
        None if project is None
        else project if isinstance(project, LicenseExpression)
        else parse_expression(project)
    )

    obligation = expression.obligation
    choice_hint = (
        f" The expression offers a choice ({expression.to_string()}); "
        f"electing {' AND '.join(t.to_string() for t in expression.preferred_option())} "
        f"is the least demanding branch."
        if expression.is_choice else ""
    )

    if obligation is Obligation.UNKNOWN:
        return CompatibilityVerdict(
            compatible=False, obligation=obligation,
            reason=(
                f"licence {expression.to_string()!r} is not a recognised SPDX identifier, "
                f"so its obligations cannot be evaluated"
            ),
            remediation="record the actual licence text, or add a LicenseRef mapping",
        )

    if obligation is Obligation.PROPRIETARY:
        return CompatibilityVerdict(
            compatible=False, obligation=obligation,
            reason=f"{expression.to_string()} is a proprietary licence requiring an explicit grant",
            remediation="attach the negotiated licence grant as a waiver, or replace the dependency",
        )

    if obligation in (Obligation.PUBLIC_DOMAIN, Obligation.PERMISSIVE):
        return CompatibilityVerdict(
            compatible=True, obligation=obligation,
            reason=f"{expression.to_string()} is permissive; attribution is the only obligation."
                   + choice_hint,
        )

    if obligation is Obligation.NETWORK_COPYLEFT:
        if distribution.triggers_network_copyleft:
            return CompatibilityVerdict(
                compatible=False, obligation=obligation,
                reason=(
                    f"{expression.to_string()} triggers on {distribution.value}: network use "
                    f"obliges you to offer the complete corresponding source of the whole work"
                ),
                remediation=(
                    "publish the corresponding source, obtain a commercial licence, "
                    "or replace the dependency"
                ) + choice_hint,
            )
        return CompatibilityVerdict(
            compatible=True, obligation=obligation,
            reason=f"{expression.to_string()} is network copyleft but this is {distribution.value}, "
                   f"which does not convey the work",
        )

    if obligation is Obligation.STRONG_COPYLEFT:
        if distribution.triggers_copyleft:
            compatible = bool(
                project_expression
                and project_expression.obligation.viral
                and project_expression.obligation.rank >= obligation.rank
            )
            if compatible:
                return CompatibilityVerdict(
                    compatible=True, obligation=obligation,
                    reason=(
                        f"{expression.to_string()} is strong copyleft and the project is "
                        f"{project_expression.to_string()}, which carries the same obligations"
                    ),
                )
            return CompatibilityVerdict(
                compatible=False, obligation=obligation,
                reason=(
                    f"{expression.to_string()} is strong copyleft and this is "
                    f"{distribution.value}: the entire derived work must be licensed alike"
                ),
                remediation=(
                    "relicense the project, isolate the dependency behind a process boundary, "
                    "or replace it"
                ) + choice_hint,
            )
        return CompatibilityVerdict(
            compatible=True, obligation=obligation,
            reason=f"{expression.to_string()} is strong copyleft but {distribution.value} does not "
                   f"convey the work, so the obligation is not triggered",
        )

    # Weak copyleft: the linkage is what decides.
    if linkage in (Linkage.STATIC, Linkage.SOURCE) and distribution.triggers_copyleft:
        return CompatibilityVerdict(
            compatible=False, obligation=obligation,
            reason=(
                f"{expression.to_string()} is weak copyleft and the dependency is "
                f"{linkage.value}-linked into a {distribution.value}, which extends its "
                f"terms to the combined work"
            ),
            remediation="link dynamically, or publish the modified library sources"
                        + choice_hint,
        )
    return CompatibilityVerdict(
        compatible=True, obligation=obligation,
        reason=(
            f"{expression.to_string()} is weak copyleft, limited to the library itself under "
            f"{linkage.value} linkage"
        ),
    )


def combined_obligation(expressions: Iterable[LicenseExpression | str]) -> Obligation:
    """The strictest obligation across a set — what a whole SBOM inherits."""
    obligations = [
        (e if isinstance(e, LicenseExpression) else parse_expression(e)).obligation
        for e in expressions
    ]
    if not obligations:
        return Obligation.UNKNOWN
    return max(obligations, key=lambda o: o.rank)


def normalize(text: str) -> LicenseExpression:
    """Parse loosely, never guess. Unparseable input becomes an explicit unknown.

    Discovery meets `licence: "see LICENSE file"` constantly. That is a real
    observation and belongs in the graph — as UNKNOWN, which raises a finding,
    rather than as an error that loses the fact entirely.
    """
    try:
        return parse_expression(text)
    except LicenseError:
        raw = (text or "").strip() or "NOASSERTION"
        return LicenseExpression(
            options=((LicenseTerm(identifier=raw),),), raw=raw,
        )


def spdx_ids(expression: LicenseExpression | str) -> tuple[str, ...]:
    """Flat identifier list — the shape SBOM emitters want."""
    parsed = expression if isinstance(expression, LicenseExpression) else normalize(expression)
    return tuple(term.identifier for term in parsed.terms)
