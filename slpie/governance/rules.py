"""Rules, and the rule set that runs all of them.

The governing decision in this module is that :meth:`RuleSet.evaluate` produces
**many findings, not one verdict**. A gate that answers "pass" or "fail" tells an
engineer nothing they can act on, and a rule engine that stops at the first match
makes the order rules were registered in a hidden input to the answer. Every rule
whose ``matches`` predicate accepts the context is run, and everything each of
them produces is collected.

The second decision follows from the first: **a rule that raises abstains.** It
is recorded as a :class:`RuleError` and the evaluation continues. Forty rules
that work are worth more than the illusion of completeness one broken rule would
buy by aborting the run, and a platform that goes silent because one analyser hit
an unexpected ``None`` is a platform that fails exactly when the ecosystem is
strange enough to be worth looking at.

Every rule carries a ``source_digest`` — a fingerprint of its own definition,
including the text of its ``matches`` and ``evaluate`` functions. Findings are
stamped with it, so a finding raised last month can be checked against the rule
as it stands today: if the digest moved, the rule's *meaning* moved, and the
comparison is between two different questions rather than two answers to one.
"""

from __future__ import annotations

import inspect
import textwrap
from collections.abc import Sequence as AbcSequence
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Iterator, Mapping

from ..domain.evidence import Evidence
from ..domain.finding import Finding, FindingKind, Remediation, rank
from ..domain.identity import digest
from ..domain.lifecycle import Severity
from ..errors import PolicyError
from ..plugins.manifest import ExtensionPoint


@dataclass(frozen=True, slots=True)
class RuleContext:
    """Everything a rule is allowed to look at.

    A rule reads; it never writes. ``graph`` is a :class:`~slpie.graph.store.GraphView`
    precisely so that a rule *cannot* mutate the thing it is judging — a rule with
    write access would make the finding list a function of evaluation order.

    ``sources`` and ``facts`` are supplied by the caller. Nothing in this package
    fetches: advisory documents, popular-package lists and file contents are
    handed to the rules as data, which is what makes a governance run
    reproducible offline and identical in the simulator and against a real
    environment.
    """

    graph: Any = None
    manifest: Any = None
    sources: Mapping[str, str] = field(default_factory=dict)
    facts: Mapping[str, Any] = field(default_factory=dict)
    now: int = 0                       # epoch seconds; 0 means "not supplied"
    source_uri: str = ""               # what a finding with no better location cites

    def fact(self, name: str, default: Any = None) -> Any:
        return self.facts.get(name, default)

    def nodes(self, **criteria: Any) -> tuple[Any, ...]:
        """Live nodes from the graph, or nothing when no graph was supplied."""
        if self.graph is None:
            return ()
        return tuple(self.graph.nodes(**criteria))


def cite(*candidates: Any) -> tuple[Evidence, ...]:
    """Evidence for a finding, from the first candidate that carries any.

    `Finding` refuses to exist without evidence, which is invariant 1 reaching
    all the way to the report — so every rule needs this, and every rule that
    hand-rolled it made the same two mistakes. `Edge.evidence` is *already* a
    tuple, so `(edge.evidence,)` produces a tuple containing a tuple and any
    renderer reaching for `.location` raises. `strongest()` returns `None` for a
    node with no evidence, so `(strongest(x),)` produces `(None,)`, which passes
    the emptiness check and fails later.

    Both are quiet: the finding is constructed successfully and breaks at render
    time, one stage away from the rule that caused it.
    """
    found: list[Evidence] = []
    for candidate in candidates:
        if candidate is None:
            continue
        if isinstance(candidate, (tuple, list)):
            found.extend(item for item in candidate if item is not None)
        else:
            found.append(candidate)
        if found:
            return tuple(found[:4])
    return ()


def packages(context: RuleContext) -> tuple[Any, ...]:
    """Live package nodes, one per coordinate, most specific spelling kept.

    The graph legitimately holds `pkg:npm/lodash` and `pkg:npm/lodash@4.17.21`
    as separate nodes: a manifest range and a lockfile pin are different
    evidence about the same thing, and the projection is right not to throw one
    away. A *rule* judging both is wrong in two directions at once — it reports
    every finding twice, and the versionless node carries none of the pin's
    metadata, so `license.undeclared` fires against a package whose licence is
    recorded on the node right next to it.

    So rules judge one package once, and they judge the spelling that knows the
    most. Deduplication belongs here rather than in the projection, because the
    projection's job is to keep evidence and this one's is to reach a verdict.
    """
    best: dict[str, Any] = {}
    for node in context.nodes(live=True):
        if not getattr(node.kind, "carries_version", False):
            continue
        coordinate = node.coordinate or node.id
        current = best.get(coordinate)
        if current is None or _specificity(node) > _specificity(current):
            best[coordinate] = node
    return tuple(best.values())


def _specificity(node: Any) -> tuple[int, int]:
    """How much a node knows: a version first, then how much metadata."""
    return (
        1 if getattr(node, "version", "") else 0,
        len(getattr(node, "properties", {}) or {}),
    )


def always(context: RuleContext) -> bool:
    """The default ``matches``: this rule has an opinion about everything."""
    return True


def _fingerprint_callable(function: Callable[..., Any]) -> str:
    """The text of a rule's own logic, for the definition digest.

    Source text is preferred because it is what a reviewer reads. Captured
    values are deliberately *not* included: a rule closed over this week's
    advisory feed is the same rule it was last week, and folding the data into
    the digest would make every rule look like it had been rewritten daily.
    """
    target = getattr(function, "func", function)          # unwrap functools.partial
    target = getattr(target, "__wrapped__", target)
    try:
        return textwrap.dedent(inspect.getsource(target))
    except (OSError, TypeError):
        code = getattr(target, "__code__", None)
        if code is None:  # pragma: no cover - builtins and C callables
            return repr(target)
        return f"{code.co_name}/{code.co_argcount}/{code.co_code.hex()}"


@dataclass(frozen=True, slots=True)
class Rule:
    """One thing the platform knows how to check.

    ``matches`` is separate from ``evaluate`` so that a rule can decline cheaply.
    A licence rule with no configured distribution context, or an advisory rule
    with an empty database, should not walk forty thousand package nodes to
    discover it has nothing to say.
    """

    id: str
    title: str
    kind: FindingKind
    severity: Severity
    evaluate: Callable[[RuleContext], Iterable[Finding]]
    matches: Callable[[RuleContext], bool] = always
    remediation: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()
    version: str = "1"

    def __post_init__(self) -> None:
        if not self.id:
            raise PolicyError("a rule must have an id")
        if not callable(self.evaluate):
            raise PolicyError(f"rule {self.id!r} has no evaluate function")
        if not callable(self.matches):
            raise PolicyError(f"rule {self.id!r} has a non-callable matches predicate")
        if not self.remediation:
            # A rule that cannot say what to do about its own findings produces
            # complaints. Refused at construction rather than at report time.
            raise PolicyError(f"rule {self.id!r} states no remediation")

    @property
    def source_digest(self) -> str:
        """A fingerprint of this rule's definition, so its meaning cannot drift.

        Covers the identity, the classification, the stated remediation *and* the
        logic itself. Change any of them and the digest changes, which is how a
        finding raised by "rule X" a month ago is prevented from being silently
        compared against a rule X that now means something else.
        """
        return digest({
            "id": self.id,
            "title": self.title,
            "kind": self.kind.value,
            "severity": self.severity.value,
            "remediation": self.remediation,
            "version": self.version,
            "tags": list(self.tags),
            "matches": _fingerprint_callable(self.matches),
            "evaluate": _fingerprint_callable(self.evaluate),
        })

    def applies(self, context: RuleContext) -> bool:
        return bool(self.matches(context))

    def finding(
        self,
        *,
        subject: str,
        title: str,
        evidence: tuple[Evidence, ...],
        detail: str = "",
        code: str = "",
        severity: Severity | None = None,
        kind: FindingKind | None = None,
        remediation: Remediation | None = None,
        related: tuple[str, ...] = (),
        properties: Mapping[str, Any] | None = None,
    ) -> Finding:
        """Raise a finding attributed to this rule.

        Every rule in this package goes through here, which is what guarantees
        the two things a report is useless without: the rule's fingerprint and a
        non-empty remediation.
        """
        return Finding(
            kind=kind or self.kind,
            severity=severity or self.severity,
            subject=subject,
            title=title,
            detail=detail,
            code=code,
            evidence=evidence,
            remediation=remediation or Remediation(summary=self.remediation),
            rule_id=self.id,
            rule_digest=self.source_digest,
            related=related,
            properties=dict(properties or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "kind": self.kind.value,
            "severity": self.severity.value, "remediation": self.remediation,
            "description": self.description, "tags": list(self.tags),
            "version": self.version, "source_digest": self.source_digest,
        }

    def __str__(self) -> str:
        return f"{self.id} ({self.severity.value}/{self.kind.value})"


@dataclass(frozen=True, slots=True)
class RuleError:
    """A rule that abstained, and why.

    Kept as data rather than logged because it belongs in the report: an
    evaluation with three findings and one abstention is a *different* answer
    from an evaluation with three findings, and a reader has to be told which
    one they are holding.
    """

    rule_id: str
    rule_digest: str
    error_type: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id, "rule_digest": self.rule_digest,
            "error": self.error_type, "message": self.message,
        }

    def __str__(self) -> str:
        return f"{self.rule_id} abstained: {self.error_type}: {self.message}"


class Evaluation(AbcSequence):
    """The result of running a rule set: a sequence of findings, plus what broke.

    It *is* the finding list — ``len()``, indexing and iteration all address the
    findings directly — so callers that only want findings never have to unwrap
    anything. The abstentions ride along on :attr:`errors` because dropping them
    would let a silently broken rule set look like a clean environment.
    """

    __slots__ = ("_findings", "errors", "evaluated", "declined")

    def __init__(
        self,
        findings: Iterable[Finding] = (),
        errors: Iterable[RuleError] = (),
        *,
        evaluated: int = 0,
        declined: int = 0,
    ) -> None:
        self._findings: tuple[Finding, ...] = tuple(findings)
        self.errors: tuple[RuleError, ...] = tuple(errors)
        self.evaluated = evaluated
        self.declined = declined

    # -- sequence ---------------------------------------------------------

    def __len__(self) -> int:
        return len(self._findings)

    def __getitem__(self, index: Any) -> Any:
        return self._findings[index]

    def __iter__(self) -> Iterator[Finding]:
        return iter(self._findings)

    # -- accessors --------------------------------------------------------

    @property
    def findings(self) -> tuple[Finding, ...]:
        return self._findings

    @property
    def error_count(self) -> int:
        """How many rules abstained. Non-zero means the answer has holes."""
        return len(self.errors)

    @property
    def blocking(self) -> tuple[Finding, ...]:
        return tuple(f for f in self._findings if f.blocks_release)

    def of_kind(self, kind: FindingKind) -> tuple[Finding, ...]:
        return tuple(f for f in self._findings if f.kind is kind)

    def by_family(self) -> dict[str, tuple[Finding, ...]]:
        families: dict[str, list[Finding]] = {}
        for finding in self._findings:
            families.setdefault(finding.family, []).append(finding)
        return {name: tuple(items) for name, items in sorted(families.items())}

    def by_severity(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for finding in self._findings:
            counts[finding.severity.value] = counts.get(finding.severity.value, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [f.to_dict() for f in self._findings],
            "errors": [e.to_dict() for e in self.errors],
            "counts": {
                "findings": len(self._findings),
                "blocking": len(self.blocking),
                "rules_evaluated": self.evaluated,
                "rules_declined": self.declined,
                "rules_abstained": self.error_count,
            },
            "by_severity": self.by_severity(),
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        return (
            f"<Evaluation {len(self._findings)} findings, "
            f"{self.error_count} abstentions from {self.evaluated} rules>"
        )


class RuleSet:
    """An ordered collection of rules that runs all of them."""

    def __init__(self, rules: Iterable[Rule] = (), *, name: str = "rules") -> None:
        self.name = name
        self._rules: dict[str, Rule] = {}
        for rule in rules:
            self.add(rule)

    # -- composition ------------------------------------------------------

    def add(self, rule: Rule) -> Rule:
        """Register one rule. Duplicate ids are refused, never overwritten.

        Two rules sharing an id would collide in the finding identity, which is
        what suppression and "new since baseline" are keyed on — the second would
        quietly inherit the first's waivers.
        """
        if rule.id in self._rules:
            raise PolicyError(f"rule {rule.id!r} is already registered in {self.name!r}")
        self._rules[rule.id] = rule
        return rule

    def extend(self, rules: Iterable[Rule]) -> "RuleSet":
        for rule in rules:
            self.add(rule)
        return self

    def merge(self, other: "RuleSet") -> "RuleSet":
        return self.extend(other)

    def remove(self, rule_id: str) -> bool:
        return self._rules.pop(rule_id, None) is not None

    def get(self, rule_id: str) -> Rule | None:
        return self._rules.get(rule_id)

    @property
    def rules(self) -> tuple[Rule, ...]:
        return tuple(self._rules.values())

    @property
    def digest(self) -> str:
        """The fingerprint of the whole set — every rule definition it contains."""
        return digest([rule.source_digest for rule in self._rules.values()])

    def __len__(self) -> int:
        return len(self._rules)

    def __iter__(self) -> Iterator[Rule]:
        return iter(self._rules.values())

    def __contains__(self, rule_id: object) -> bool:
        return rule_id in self._rules

    # -- evaluation -------------------------------------------------------

    def evaluate(
        self,
        context: RuleContext,
        *,
        only: Iterable[str] = (),
        skip: Iterable[str] = (),
    ) -> Evaluation:
        """Run every matching rule and return everything they found.

        Not the first finding, not a verdict: all of them. A rule that raises is
        recorded as an abstention and the run continues — one broken rule must
        never blind the platform to the other forty.
        """
        selected = frozenset(only)
        excluded = frozenset(skip)

        findings: list[Finding] = []
        errors: list[RuleError] = []
        evaluated = 0
        declined = 0

        for rule in self._rules.values():
            if selected and rule.id not in selected:
                continue
            if rule.id in excluded:
                continue
            try:
                if not rule.applies(context):
                    declined += 1
                    continue
                # Materialised inside the guard: a generator that raises on its
                # third element is just as broken as one that raises immediately.
                produced = list(rule.evaluate(context))
            except Exception as error:      # a rule may fail in any way at all
                errors.append(RuleError(
                    rule_id=rule.id,
                    rule_digest=_safe_digest(rule),
                    error_type=type(error).__name__,
                    message=str(error) or repr(error),
                ))
                continue

            evaluated += 1
            for finding in produced:
                if not isinstance(finding, Finding):
                    errors.append(RuleError(
                        rule_id=rule.id, rule_digest=_safe_digest(rule),
                        error_type="TypeError",
                        message=f"produced {type(finding).__name__}, expected a Finding",
                    ))
                    continue
                findings.append(_attribute(finding, rule))

        return Evaluation(
            rank(findings), errors, evaluated=evaluated, declined=declined,
        )


def _attribute(finding: Finding, rule: Rule) -> Finding:
    """Stamp a finding with the rule that raised it, if it did not do so itself."""
    if finding.rule_id and finding.rule_digest:
        return finding
    return replace(
        finding,
        rule_id=finding.rule_id or rule.id,
        rule_digest=finding.rule_digest or _safe_digest(rule),
    )


def _safe_digest(rule: Rule) -> str:
    try:
        return rule.source_digest
    except Exception:  # pragma: no cover - a fingerprint must never be the failure
        return ""


# --- registration --------------------------------------------------------


class RuleFamily:
    """A rule set behind the plugin registry's callable contract.

    Rule families register through :class:`~slpie.plugins.registry.Registry` at
    the ``RULE`` extension point — the same public path a third-party rule pack
    takes. Built-ins that took a private route would leave the seam untested
    until somebody outside the project tried to use it.
    """

    __slots__ = ("ruleset",)

    def __init__(self, ruleset: RuleSet) -> None:
        self.ruleset = ruleset

    def __call__(self, context: RuleContext) -> Evaluation:
        return self.ruleset.evaluate(context)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<RuleFamily {self.ruleset.name} {len(self.ruleset)} rules>"


def register_rule_family(
    registry: Any,
    ruleset: RuleSet,
    *,
    plugin_id: str = "",
    version: str = "1",
    description: str = "",
) -> Any:
    """Register a rule set with the plugin registry. Returns the Registration."""
    return registry.register_builtin(
        plugin_id or f"governance.{ruleset.name}",
        RuleFamily(ruleset),
        kind=ExtensionPoint.RULE,
        produces=tuple(rule.id for rule in ruleset),
        version=version,
        description=description or f"{len(ruleset)} governance rules",
    )


def registered_rules(registry: Any) -> RuleSet:
    """Every rule family registered at the RULE extension point, as one set."""
    merged = RuleSet(name="registered")
    for registration in registry.of_kind(ExtensionPoint.RULE):
        family = registration.handler
        if isinstance(family, RuleFamily):
            merged.extend(family.ruleset)
    return merged
