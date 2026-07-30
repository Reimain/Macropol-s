"""The judgements. Each is a deterministic query over the projection.

Every rule here is a *question about the graph* rather than a bespoke walk, which
is what makes them uniform, testable in isolation, and runnable against somebody
else's tree rather than only against ours.

Two rules deserve their reasoning stated, because they are what turn a registry
into a judge:

**`citations`** enforces that every reference entry names a source. The registry's
own docstring warns that "a hand-written landscape survey ages into confident
wrongness faster than any code does", and an unsourced claim is that wrongness with
no way to check it.

**`honesty`** audits the registry *in both directions*. A claim pointing at a
module that no longer exists is `VIOLATED` — obviously. Less obviously, a `gaps()`
entry claiming we do not do something, contradicted by a module that does, is also
`VIOLATED`. A gaps list that only ever grows is marketing; one audited both ways is
a fact.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from .judge import Judgement, Verdict, inapplicable, indeterminate, upheld, violated
from .project import Import, Module, Projection

#: Standard-library top-level names. `sys.stdlib_module_names` is authoritative and
#: version-correct, which matters: a hand-maintained list is wrong the moment the
#: interpreter adds a module, and it fails *open* — a new stdlib module would be
#: reported as a third-party dependency in a kernel that forbids them.
STDLIB = frozenset(sys.stdlib_module_names) | {"__future__"}


@dataclass(frozen=True, slots=True)
class Rule:
    """One judgement, declared as data so the rule set is inspectable."""

    name: str
    summary: str
    judge: Callable[[Projection, dict[str, Any]], list[Judgement]]
    remediation: str = ""

    def run(self, projection: Projection, **options: Any) -> list[Judgement]:
        return self.judge(projection, options)

    def __str__(self) -> str:
        return f"{self.name}: {self.summary}"


# --- import boundaries ---------------------------------------------------


def _single_import(
    projection: Projection,
    options: dict[str, Any],
) -> list[Judgement]:
    """Exactly one module in a ring may import another package.

    Expressed as a *query* — "who imports X from ring Y" — rather than as a bespoke
    file walk, so it runs unchanged against any tree with the same shape.
    """
    ring = str(options.get("ring", ""))
    target = str(options.get("target", ""))
    allowed = str(options.get("allowed", ""))
    rule = str(options.get("rule", "single-import"))

    if not projection.in_ring(ring):
        return [inapplicable(rule, ring, f"there is no {ring}/ in this tree")]

    importers = projection.importers_of(target, ring=ring)
    found: list[Judgement] = []

    for module in importers:
        if module == allowed:
            continue
        imports = [
            item for item in projection.imports_into(target, ring=ring)
            if item.module == module
        ]
        source = projection.get(module)
        found.append(violated(
            rule, module,
            detail=(
                f"{module} imports {target}, but only {allowed} may. A second "
                f"bridge is a second place the boundary can rot"
            ),
            evidence=tuple(
                source.evidence_at(item.line, f"import {item.target}")
                for item in imports[:3]
            ) if source else (),
            remediation=f"route it through {allowed}, or move the code there",
        ))

    if not found:
        detail = (
            f"{allowed} is the only module in {ring}/ importing {target}"
            if allowed in importers else
            f"nothing in {ring}/ imports {target}"
        )
        found.append(upheld(rule, f"{ring}/ → {target}", detail))

    return found


def _ring_purity(projection: Projection, options: dict[str, Any]) -> list[Judgement]:
    """A ring that must not depend on anything outside the standard library."""
    ring = str(options.get("ring", ""))
    rule = str(options.get("rule", "purity"))
    exempt = frozenset(options.get("exempt", ()))

    modules = projection.in_ring(ring)
    if not modules:
        return [inapplicable(rule, ring, f"there is no {ring}/ in this tree")]

    found: list[Judgement] = []
    for item in projection.third_party(ring=ring, stdlib=STDLIB):
        if item.top in exempt:
            continue
        module = projection.get(item.module)
        found.append(violated(
            rule, item.module,
            detail=(
                f"{item.module} imports {item.target}, which is neither standard "
                f"library nor local to this tree"
            ),
            evidence=(
                module.evidence_at(item.line, f"import {item.target}"),
            ) if module else (),
            remediation=(
                f"move it to a ring that may depend on {item.top}, or replace it "
                f"with a stdlib equivalent"
            ),
        ))

    if not found:
        found.append(upheld(
            rule, ring,
            f"{len(modules)} module(s) in {ring}/ import only the standard "
            f"library and each other",
        ))
    return found


def _ring_direction(projection: Projection, options: dict[str, Any]) -> list[Judgement]:
    """An inner ring must never import an outer one."""
    inner = str(options.get("inner", ""))
    outer = str(options.get("outer", ""))
    rule = str(options.get("rule", "ring-direction"))

    if not projection.in_ring(inner) or not projection.in_ring(outer):
        return [inapplicable(rule, f"{inner} → {outer}", "one of the rings is absent")]

    offenders = projection.imports_into(outer, ring=inner)
    if not offenders:
        return [upheld(
            rule, f"{inner} → {outer}",
            f"nothing in {inner}/ imports {outer}/; the dependency points inward",
        )]

    return [
        violated(
            rule, item.module,
            detail=f"{item.module} imports {item.target}, inverting the rings",
            evidence=(
                projection.get(item.module).evidence_at(item.line)
                if projection.get(item.module) else None,
            ) if projection.get(item.module) else (),
            remediation=f"invert it: {outer}/ may depend on {inner}/, never the reverse",
        )
        for item in offenders
    ]


# --- what the judge cannot see ------------------------------------------


def _readability(projection: Projection, options: dict[str, Any]) -> list[Judgement]:
    """Files that could not be parsed. INDETERMINATE, never a silent pass.

    A judge reporting a clean tree while a tenth of it failed to parse has told the
    operator something false, and the green result is the reason nobody looks
    again.
    """
    rule = "readable"
    found: list[Judgement] = []

    for module in projection.unparsed:
        found.append(indeterminate(
            rule, module.name,
            detail=(
                f"{module.error}; nothing in this file was examined, so no rule "
                f"below has judged it"
            ),
            remediation="fix the file, or exclude it deliberately rather than by accident",
        ))

    if not found:
        found.append(upheld(
            rule, str(projection.root.name),
            f"all {len(projection.parsed)} module(s) parsed",
        ))
    return found


def _dynamic(projection: Projection, options: dict[str, Any]) -> list[Judgement]:
    """Runtime imports the static projection cannot follow.

    Scoped to `__import__` and `importlib.import_module` only. It once included
    `getattr` and fired sixty times on ordinary attribute access — a rule that
    flags idiomatic Python as a blind spot gets switched off, and takes the real
    findings with it.
    """
    rule = "statically-visible"
    sites = projection.dynamic_sites()

    if not sites:
        return [upheld(
            rule, str(projection.root.name),
            "no runtime import hides a dependency from the boundary rules",
        )]

    found: list[Judgement] = []
    for name, line in sites[:60]:
        module = projection.get(name)
        found.append(indeterminate(
            rule, name,
            detail=(
                "an import here is resolved at runtime, so the boundary rules "
                "above cannot see what it reaches"
            ),
            evidence=(module.evidence_at(line),) if module else (),
            remediation="import it statically where the boundary rules must see it",
        ))
    return found


# --- the reference registry judges itself -------------------------------


def _citations(projection: Projection, options: dict[str, Any]) -> list[Judgement]:
    """Every reference entry names a source that parses as a URL."""
    rule = "citations"
    entries = list(options.get("entries", ()))
    if not entries:
        return [inapplicable(rule, "reference registry", "no registry was supplied")]

    found: list[Judgement] = []
    for entry in entries:
        identifier = str(
            getattr(entry, "id", "")
            or getattr(entry, "identifier", "")
            or getattr(entry, "term", "")
            or entry
        )
        source = str(
            getattr(entry, "source", "")
            or getattr(entry, "spec", "")
            or getattr(entry, "url", "")
        )
        if not source:
            found.append(violated(
                rule, identifier,
                detail=(
                    "the entry cites no source. A hand-written survey ages into "
                    "confident wrongness, and an unsourced claim cannot be checked"
                ),
                remediation="add the URL the claim can be verified against",
            ))
        elif not source.startswith(("http://", "https://")):
            found.append(violated(
                rule, identifier,
                detail=f"{source!r} is not a checkable URL",
                remediation="cite something a reader can open",
            ))

    if not found:
        found.append(upheld(
            rule, "reference registry",
            f"all {len(entries)} entries cite a checkable source",
        ))
    return found


def _honesty(projection: Projection, options: dict[str, Any]) -> list[Judgement]:
    """The registry's claims, audited against the graph — in both directions.

    A claim pointing at a deleted module is a violation, obviously. The other
    direction is the one that matters: a `gaps()` entry saying we do not do
    something, contradicted by a module that does, is *also* a violation. A gaps
    list that only ever grows is marketing.
    """
    rule = "honesty"
    claims = list(options.get("claims", ()))
    gaps = list(options.get("gaps", ()))

    if not claims and not gaps:
        return [inapplicable(rule, "reference registry", "no claims were supplied")]

    found: list[Judgement] = []
    known = {module.name for module in projection.modules}

    for claim in claims:
        subject = str(getattr(claim, "term", claim))
        module = str(getattr(claim, "answered_by", "") or getattr(claim, "module", ""))
        if not module:
            continue
        if not _resolves(module, known):
            found.append(violated(
                rule, subject,
                detail=(
                    f"the registry says {subject!r} is answered by {module}, which "
                    f"is not in this tree. The claim outlived the code"
                ),
                remediation=f"point it at the module that answers it now, or record it as a gap",
            ))

    for gap in gaps:
        subject = str(getattr(gap, "term", gap))
        module = str(getattr(gap, "answered_by", "") or getattr(gap, "module", ""))
        if module and _resolves(module, known):
            found.append(violated(
                rule, subject,
                detail=(
                    f"{subject!r} is recorded as a gap, but {module} exists and "
                    f"answers it. A gaps list that only grows is marketing"
                ),
                remediation="move it out of gaps(); it is no longer one",
            ))

    if not found:
        found.append(upheld(
            rule, "reference registry",
            f"{len(claims)} claim(s) and {len(gaps)} gap(s) agree with the graph",
        ))
    return found


def _resolves(module: str, known: Iterable[str]) -> bool:
    """Whether a claimed module exists, allowing a package to stand for its parts."""
    names = set(known)
    if module in names:
        return True
    return any(name.startswith(f"{module}.") for name in names)


def _declared(projection: Projection, options: dict[str, Any]) -> list[Judgement]:
    """Every third-party import is declared as a dependency, and vice versa.

    This is the judgement that works on *anybody's* repository, which is what makes
    the audit a product rather than an internal test. Two directions, and both are
    real defects:

    * **imported but not declared** — a phantom dependency. It works on the
      developer's machine because something else pulled it in transitively, and it
      breaks on a clean install. The transitive provider is free to drop it in a
      patch release, so this is a build that is fine until it suddenly is not.
    * **declared but never imported** — dead weight in the install and, more
      importantly, in the vulnerability surface. Every declared dependency is
      something a scanner reports CVEs against and somebody has to triage.

    The import-name to distribution-name mapping is reused from
    `slpie/linking/linkers.py` rather than restated: `yaml` is `pyyaml` and `cv2` is
    `opencv-python`, and a second table would drift from the first.
    """
    rule = str(options.get("rule", "declared-dependencies"))
    ring = str(options.get("ring", ""))
    declared = {_canonical(name) for name in options.get("declared", ())}
    exempt = {_canonical(name) for name in options.get("exempt", ())}

    if not declared:
        return [inapplicable(
            rule, ring or "tree",
            "no declared dependency list was supplied to compare against",
        )]
    if ring and not projection.in_ring(ring):
        return [inapplicable(rule, ring, f"there is no {ring}/ in this tree")]

    from ..linking.linkers import IMPORT_TO_DISTRIBUTION, STDLIB_HINT

    found: list[Judgement] = []
    imported: set[str] = set()
    first_seen: dict[str, Import] = {}

    for item in projection.third_party(ring=ring, stdlib=STDLIB):
        top = item.top
        if top in STDLIB_HINT:
            continue
        distribution = _canonical(IMPORT_TO_DISTRIBUTION.get(top, top))
        imported.add(distribution)
        # An import inside a `try` is a deliberate optional whose absence the
        # author already handles. Reporting it as an undeclared dependency flags
        # correct defensive code, which is how a useful rule gets muted.
        if not item.guarded:
            first_seen.setdefault(distribution, item)

    # A distribution seen only behind a guard is a deliberate optional, so it is
    # dropped from the undeclared check unless it is declared anyway.
    imported = {
        name for name in imported if name in first_seen or name in declared
    }

    for distribution in sorted(imported - declared - exempt):
        item = first_seen[distribution]
        module = projection.get(item.module)
        found.append(violated(
            rule, distribution,
            detail=(
                f"{item.module} imports {item.target}"
                + (" inside a function" if item.inside_function else "")
                + f", but {distribution} is not a declared dependency. It works "
                f"here because something else pulls it in, and breaks on a clean "
                f"install the day that provider drops it"
            ),
            evidence=(
                module.evidence_at(item.line, f"import {item.target}"),
            ) if module else (),
            remediation=f"declare {distribution}, or stop importing it",
        ))

    # The other direction cannot be decided at all when the tree loads modules by
    # name at runtime. This rule reported nine tree-sitter grammars as unused
    # against a repository that loads every one of them through
    # `importlib.import_module(config.ts_module)` — ruling confidently on a tree
    # where the judge had *already* declared itself blind. That is precisely the
    # failure INDETERMINATE exists to prevent, and the rule was committing it.
    unused = sorted(declared - imported - exempt)
    dynamic = projection.dynamic_sites()

    if unused and dynamic:
        module, line = dynamic[0]
        source = projection.get(module)
        found.append(indeterminate(
            rule, ", ".join(unused[:6]) + ("…" if len(unused) > 6 else ""),
            detail=(
                f"{len(unused)} declared dependencies have no static import, but "
                f"this tree resolves imports at runtime in {len(dynamic)} place(s) "
                f"— a module loaded by name is invisible here, so whether these "
                f"are unused cannot be decided"
            ),
            evidence=(source.evidence_at(line),) if source else (),
            remediation=(
                "check them against the runtime import sites, or import them "
                "statically where the audit can see them"
            ),
        ))
    else:
        for distribution in unused:
            found.append(violated(
                rule, distribution,
                detail=(
                    f"{distribution} is declared but never imported. It is "
                    f"installed, and every declared dependency is something a "
                    f"scanner reports CVEs against that somebody has to triage"
                ),
                remediation=f"remove {distribution}, or move it to an optional extra",
            ))

    if not found:
        found.append(upheld(
            rule, ring or "tree",
            f"{len(imported)} imported distribution(s) all declared, and nothing "
            f"declared goes unused",
        ))
    return found


def _canonical(name: str) -> str:
    """PEP 503 normalisation. `Tree_Sitter.Python` and `tree-sitter-python` are one."""
    import re

    return re.sub(r"[-_.]+", "-", str(name).strip()).lower()


# --- the set -------------------------------------------------------------


def rules() -> tuple[Rule, ...]:
    """Every judgement this build can make."""
    return (
        Rule("readable", "every file parsed, or said why not", _readability),
        Rule("statically-visible", "no dependency hides behind a dynamic import",
             _dynamic),
        Rule("single-import", "exactly one module crosses a named boundary",
             _single_import),
        Rule("purity", "a ring depends on nothing outside the stdlib", _ring_purity),
        Rule("ring-direction", "dependencies point inward, never outward",
             _ring_direction),
        Rule("declared-dependencies",
             "every third-party import is declared, and every declaration used",
             _declared),
        Rule("citations", "every reference entry names a checkable source",
             _citations),
        Rule("honesty", "the registry's claims and gaps agree with the graph",
             _honesty),
    )


def by_name() -> dict[str, Rule]:
    return {rule.name: rule for rule in rules()}
