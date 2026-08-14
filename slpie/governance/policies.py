"""Declarative policies — rules an organisation writes without writing code.

A policy file is **data, and it is never executed**. There is no ``eval``, no
``exec``, no ``import`` and no expression language in this module: a policy is a
closed vocabulary of conditions (:data:`OPERATORS`) evaluated against node
attributes by :meth:`Condition.holds`. That is a deliberate refusal rather than
an unimplemented feature. A governance system whose rule files can execute is a
remote code execution vector wearing a compliance badge — the files are read from
globs in a manifest, and a manifest is exactly the kind of thing that arrives by
pull request from somebody you have never met.

The one pattern-matching operator, ``matches``, is an **fnmatch glob**, not a
regular expression. Globs cannot backtrack, so no policy file can turn a
governance run into a hang, and nobody has to review a third-party rule pack for
catastrophic regexes.

A policy file looks like this::

    policies:
      - id: no-network-copyleft
        title: AGPL dependencies are not permitted in the hosted product
        kind: license_incompatible
        severity: high
        subjects: [package]
        all_of:
          - property: license
            one_of: [AGPL-3.0-only, AGPL-3.0-or-later, SSPL-1.0]
        remediation: replace the dependency or obtain a commercial licence

Files are found through the manifest's ``security.policies`` globs. A file that
will not parse is reported as an error and skipped, not raised: one malformed
policy must not cost the organisation the fifty rules that were fine.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..domain.evidence import Evidence, EvidenceKind, SourceLocation, strongest
from ..domain.finding import Finding, FindingKind, Remediation
from ..domain.identity import Purl
from ..domain.lifecycle import Severity
from ..environment.schema import parse_yaml
from ..errors import GovernanceError, ManifestError
from .rules import Rule, RuleContext, RuleSet

#: Every operator a policy file may use. Anything else is a load error, so a
#: typo becomes a message rather than a rule that silently never fires.
OPERATORS = (
    "equals", "not_equals", "one_of", "none_of", "contains", "not_contains",
    "matches", "not_matches", "present", "absent", "greater_than", "less_than",
)

#: Node attributes addressable by name in addition to ``properties`` keys.
INTRINSIC = ("name", "kind", "version", "identity", "lifecycle", "risk",
             "compliance", "architecture", "confidence", "type", "namespace")

_EXTENSIONS = (".yaml", ".yml", ".json")


def _attribute(node: Any, name: str) -> Any:
    """Read one addressable attribute from a node.

    Intrinsics first, then declared properties. A policy author writes
    ``property: license`` and does not need to know whether the licence is a
    field on the node or something a discoverer attached.
    """
    if name == "identity":
        return node.identity.to_string()
    if name == "kind":
        return node.kind.value
    if name in ("type", "namespace"):
        identity = node.identity
        if isinstance(identity, Purl):
            return identity.type if name == "type" else identity.namespace
        return "" if name == "namespace" else identity.kind
    if name in INTRINSIC:
        value = getattr(node, name, None)
        return value.value if hasattr(value, "value") else value
    return node.properties.get(name)


@dataclass(frozen=True, slots=True)
class Condition:
    """One test against one node attribute. Data, evaluated by this method only."""

    property: str
    operator: str
    value: Any = None

    def __post_init__(self) -> None:
        if not self.property:
            raise GovernanceError("a condition must name a property")
        if self.operator not in OPERATORS:
            raise GovernanceError(
                f"unknown policy operator {self.operator!r}; "
                f"expected one of {', '.join(OPERATORS)}"
            )

    def holds(self, node: Any) -> bool:
        found = _attribute(node, self.property)
        operator = self.operator

        if operator == "present":
            return found not in (None, "", (), [], {})
        if operator == "absent":
            return found in (None, "", (), [], {})
        if found is None:
            # A node that never stated the attribute is not violating a rule
            # about its value. Silence is not a claim.
            return False

        text = str(found)
        if operator == "equals":
            return _same(found, self.value)
        if operator == "not_equals":
            return not _same(found, self.value)
        if operator == "one_of":
            return any(_same(found, option) for option in _as_sequence(self.value))
        if operator == "none_of":
            return not any(_same(found, option) for option in _as_sequence(self.value))
        if operator == "contains":
            return str(self.value).casefold() in text.casefold()
        if operator == "not_contains":
            return str(self.value).casefold() not in text.casefold()
        if operator == "matches":
            return fnmatchcase(text.casefold(), str(self.value).casefold())
        if operator == "not_matches":
            return not fnmatchcase(text.casefold(), str(self.value).casefold())
        if operator in ("greater_than", "less_than"):
            left, right = _numeric(found), _numeric(self.value)
            if left is None or right is None:
                return False
            return left > right if operator == "greater_than" else left < right
        return False  # pragma: no cover - guarded by __post_init__

    def to_dict(self) -> dict[str, Any]:
        return {"property": self.property, self.operator: self.value}

    def __str__(self) -> str:
        if self.operator in ("present", "absent"):
            return f"{self.property} is {self.operator}"
        return f"{self.property} {self.operator.replace('_', ' ')} {self.value!r}"


def _same(left: Any, right: Any) -> bool:
    """Case-insensitive comparison for text, exact for everything else."""
    if isinstance(left, str) or isinstance(right, str):
        return str(left).casefold() == str(right).casefold()
    return left == right


def _as_sequence(value: Any) -> tuple[Any, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,)


def _numeric(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class Policy:
    """One declarative rule, as written in a policy file."""

    id: str
    title: str
    severity: Severity = Severity.MEDIUM
    kind: FindingKind = FindingKind.POLICY_VIOLATION
    subjects: tuple[str, ...] = ()          # node kinds; empty means every kind
    all_of: tuple[Condition, ...] = ()
    any_of: tuple[Condition, ...] = ()
    detail: str = ""
    remediation: str = ""
    source: str = ""
    line: int = 0

    def __post_init__(self) -> None:
        if not self.id:
            raise GovernanceError("a policy must have an id")
        if not self.all_of and not self.any_of:
            # A policy with no conditions matches every node in the graph. That
            # is never what anybody meant, and the failure mode is forty
            # thousand findings.
            raise GovernanceError(f"policy {self.id!r} states no conditions")
        if not self.remediation:
            raise GovernanceError(f"policy {self.id!r} states no remediation")

    def violated_by(self, node: Any) -> bool:
        """Whether this node breaks the policy."""
        if self.subjects and node.kind.value not in self.subjects:
            return False
        if self.all_of and not all(condition.holds(node) for condition in self.all_of):
            return False
        if self.any_of and not any(condition.holds(node) for condition in self.any_of):
            return False
        return True

    @property
    def reason(self) -> str:
        """Why a node matched, in the author's own words plus the conditions."""
        parts = [str(c) for c in self.all_of]
        if self.any_of:
            parts.append("(" + " or ".join(str(c) for c in self.any_of) + ")")
        return " and ".join(parts)

    def evidence(self) -> Evidence:
        """The policy file itself — where a reader goes to argue with the rule."""
        return Evidence(
            kind=EvidenceKind.DECLARED,
            location=SourceLocation(self.source or "slpie://policy", line=self.line),
            extractor="governance.policies",
            extractor_version="1",
            excerpt=f"{self.id}: {self.reason}",
        )

    def to_rule(self) -> Rule:
        """Compile to a :class:`Rule`. The compilation is a closure, not code.

        Nothing is generated, parsed as Python, or executed. The returned rule
        holds this frozen dataclass and walks the graph asking it questions.
        """
        policy = self

        def matches(context: RuleContext) -> bool:
            return context.graph is not None

        def evaluate(context: RuleContext) -> list[Finding]:
            raised: list[Finding] = []
            for node in context.nodes(live=True):
                if not policy.violated_by(node):
                    continue
                observed = strongest(node.evidence)
                raised.append(Finding(
                    kind=policy.kind,
                    severity=policy.severity,
                    subject=node.id,
                    title=f"{node.display} violates {policy.id}: {policy.title}",
                    detail=(
                        policy.detail
                        or f"{node.display} matched the declared policy because "
                           f"{policy.reason}."
                    ),
                    code=policy.id,
                    evidence=tuple(e for e in (policy.evidence(), observed) if e),
                    remediation=Remediation(
                        summary=policy.remediation, action="policy", target=node.display,
                    ),
                    rule_id=policy.id,
                    properties={
                        "policy": policy.id,
                        "policy_source": policy.source,
                        "matched": policy.reason,
                    },
                ))
            return raised

        return Rule(
            id=policy.id,
            title=policy.title,
            kind=policy.kind,
            severity=policy.severity,
            evaluate=evaluate,
            matches=matches,
            remediation=policy.remediation,
            description=policy.detail,
            tags=("declarative", "policy"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "severity": self.severity.value,
            "kind": self.kind.value, "subjects": list(self.subjects),
            "all_of": [c.to_dict() for c in self.all_of],
            "any_of": [c.to_dict() for c in self.any_of],
            "detail": self.detail, "remediation": self.remediation,
            "source": self.source, "line": self.line,
        }


# --- parsing -------------------------------------------------------------


def parse_condition(entry: Mapping[str, Any]) -> Condition:
    """One condition mapping: a property plus exactly one operator."""
    if not isinstance(entry, Mapping):
        raise GovernanceError(f"a condition must be a mapping, got {type(entry).__name__}")
    name = entry.get("property") or entry.get("attribute")
    if not name:
        raise GovernanceError(f"condition {dict(entry)!r} names no property")

    used = [key for key in entry if key in OPERATORS]
    if len(used) != 1:
        raise GovernanceError(
            f"condition on {name!r} must use exactly one operator, found {used or 'none'}"
        )
    return Condition(property=str(name), operator=used[0], value=entry[used[0]])


def parse_policy(entry: Mapping[str, Any], *, source: str = "") -> Policy:
    """One policy mapping from a policy document."""
    if not isinstance(entry, Mapping):
        raise GovernanceError(f"a policy must be a mapping, got {type(entry).__name__}")

    try:
        severity = Severity(str(entry.get("severity", "medium")).lower())
    except ValueError:
        raise GovernanceError(
            f"policy {entry.get('id', '?')!r} has unknown severity "
            f"{entry.get('severity')!r}"
        ) from None
    try:
        kind = FindingKind(str(entry.get("kind", "policy_violation")).lower())
    except ValueError:
        raise GovernanceError(
            f"policy {entry.get('id', '?')!r} has unknown finding kind "
            f"{entry.get('kind')!r}"
        ) from None

    conditions = entry.get("all_of") or entry.get("when") or ()
    alternatives = entry.get("any_of") or ()

    return Policy(
        id=str(entry.get("id", "")),
        title=str(entry.get("title", entry.get("id", ""))),
        severity=severity,
        kind=kind,
        subjects=tuple(str(s) for s in _as_sequence(entry.get("subjects") or ())),
        all_of=tuple(parse_condition(c) for c in conditions),
        any_of=tuple(parse_condition(c) for c in alternatives),
        detail=str(entry.get("detail", "")),
        remediation=str(entry.get("remediation", "")),
        source=source,
        line=int(entry.get("line", 0) or 0),
    )


def parse_policy_document(document: Any, *, source: str = "") -> list[Policy]:
    """A whole policy file: ``policies:``, a bare list, or a single policy."""
    if isinstance(document, Mapping):
        entries = document.get("policies")
        if entries is None:
            entries = [document] if document.get("id") else []
    elif isinstance(document, (list, tuple)):
        entries = list(document)
    elif document is None:
        entries = []
    else:
        raise GovernanceError(f"{source or 'policy document'} is not a policy file")

    return [parse_policy(entry, source=source) for entry in entries]


def load_policy_file(path: str | Path) -> list[Policy]:
    """Read one policy file. JSON or the manifest's YAML subset, by extension."""
    location = Path(path)
    try:
        text = location.read_text(encoding="utf-8")
    except OSError as error:
        raise GovernanceError(f"cannot read policy file {location}: {error}") from None

    try:
        if location.suffix.lower() == ".json":
            document = json.loads(text)
        else:
            document = parse_yaml(text)
    except (ValueError, ManifestError) as error:
        raise GovernanceError(f"{location} is not a valid policy file: {error}") from None

    return parse_policy_document(document, source=location.as_uri())


@dataclass(frozen=True, slots=True)
class PolicySet:
    """Everything the manifest's globs resolved to, and everything that failed."""

    policies: tuple[Policy, ...] = ()
    errors: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()

    def to_ruleset(self, *, name: str = "policies") -> RuleSet:
        return RuleSet((policy.to_rule() for policy in self.policies), name=name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policies": [p.to_dict() for p in self.policies],
            "errors": list(self.errors),
            "sources": list(self.sources),
        }

    def __len__(self) -> int:
        return len(self.policies)

    def __iter__(self) -> Any:
        return iter(self.policies)


def policy_paths(patterns: Iterable[str], root: str | Path = ".") -> tuple[Path, ...]:
    """Resolve the manifest's ``security.policies`` globs against a root.

    A pattern naming a directory expands to the policy files inside it, because
    ``policies: [governance/]`` is what people write and refusing it teaches them
    nothing.
    """
    base = Path(root)
    found: list[Path] = []
    for pattern in patterns:
        candidate = base / pattern
        if candidate.is_dir():
            found.extend(
                p for p in sorted(candidate.rglob("*")) if p.suffix.lower() in _EXTENSIONS
            )
            continue
        if candidate.is_file():
            found.append(candidate)
            continue
        found.extend(
            p for p in sorted(base.glob(pattern))
            if p.is_file() and p.suffix.lower() in _EXTENSIONS
        )
    # Deduplicate while keeping a stable order: two globs commonly overlap.
    seen: dict[str, Path] = {}
    for path in found:
        seen.setdefault(str(path.resolve()), path)
    return tuple(seen.values())


def load_policies(
    patterns: Iterable[str] = (),
    *,
    root: str | Path = ".",
    manifest: Any = None,
) -> PolicySet:
    """Load every policy the manifest declares.

    A file that will not parse becomes a recorded error and the rest still load.
    The alternative — raising — means one bad merge disables governance entirely,
    which is the failure mode most likely to go unnoticed.
    """
    globs = tuple(patterns)
    if manifest is not None and not globs:
        globs = tuple(manifest.security.policies)

    policies: list[Policy] = []
    errors: list[str] = []
    sources: list[str] = []
    seen: set[str] = set()

    for path in policy_paths(globs, root):
        try:
            loaded = load_policy_file(path)
        except GovernanceError as error:
            errors.append(str(error))
            continue
        sources.append(str(path))
        for policy in loaded:
            if policy.id in seen:
                errors.append(f"duplicate policy id {policy.id!r} in {path}")
                continue
            seen.add(policy.id)
            policies.append(policy)

    return PolicySet(tuple(policies), tuple(errors), tuple(sources))
