"""The metadata index: everything in this product, and what connects it.

§31's spine. One question motivates it — *what is connected to this?* — asked
about a verb, a screen, a module or a plan section, and answered without a
search. The console answers that question about an environment; nothing
answered it about the platform itself, so every session rediscovered the same
340 modules by grep and the knowledge lived in a transcript.

**The rule that stops this being a second registry: `build()` reads, it does
not restate.** Verbs come from `slpie/compose/registry.py`, routes from
`Api.routes`, screens from `slpie/ui/contract.py`, modules and imports from the
AST projection at `slpie/audit/project.py`, components from the exports of the
browser's own modules, tests from `tests/`, and plan sections from the `§NN`
references modules already write in their own docstrings. Nothing is typed in
twice, so nothing can disagree. The index is wrong only if the code is.

That is also why there is no hand-written section→package map here. A map like
that is right the day it is written and wrong two renames later, which is the
exact drift this file exists to prevent. Instead a package *claims* its section
in its own docstring and the index believes the claim — and reports the sections
nobody claims rather than inventing an owner for them.

**Reproducible by construction.** Every collection is sorted, every link set is
ordered, and the digest is content-addressed over the ordered facet digests —
the same trick as the snapshot `root_digest` (§12) and `slpie audit --digest`
(§25). Two runs over an unchanged tree agree, and a moved module changes the
digest and names the facet that moved.

Stdlib only, ring 0. This reads source files and enum members; it imports no
framework and runs offline.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from ..audit.project import Module, Projection, project
from .facet import Facet, FacetKind, Link, Relation

#: The trees the index walks, in the order it walks them. `slpie` first because
#: it is the product; `tests` last because a test facet's links point at the
#: modules it covers, which must exist by then.
TREES = ("slpie", "gratimos", "tests")

#: A plan-section reference, as modules already write it in their docstrings:
#: "This is the hub of §24", "the keystone of §24's one registry".
SECTION = re.compile(r"§(\d{1,2})")

#: A native ESM export, for the browser component vocabulary. Only `function`
#: exports are components — a `const` export is a table of values, not something
#: a screen can be asked to draw.
JS_EXPORT = re.compile(r"^export\s+function\s+(\w+)", re.M)

#: A test that names its subject. `test_slpie_compose.py` → `slpie/compose`.
TEST_MODULE = re.compile(r"^test_(.+)\.py$")


def _repository() -> Path:
    """The repository root, from this file's own location.

    Four parents up: `index.py` → `context/` → `slpie/` → the root. Wrong when
    the package is installed into `site-packages`, which is deliberate rather
    than unhandled: `build()` then finds no `tests/` and no browser assets, and
    the facets that would have come from them are simply absent. An index of
    what is present beats a crash or a fabricated path.
    """
    return Path(__file__).resolve().parent.parent.parent


def _line_of(path: Path, needle: str) -> int:
    """The 1-based line a literal first appears on, or 0.

    A scan rather than a parse, because the things being located — a `Screen(`
    literal, a `name="findings"` keyword — are arguments inside a call, and an
    AST walk to recover an argument's line is more machinery than reading the
    file. Deterministic either way: first match wins, and files are read once.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0
    index = text.find(needle)
    if index < 0:
        return 0
    return text.count("\n", 0, index) + 1


def _at(root: Path, path: Path, line: int = 0) -> str:
    """`file:line`, relative to the repository, or empty when unresolvable."""
    if not path.exists():
        return ""
    try:
        relative = path.resolve().relative_to(root).as_posix()
    except ValueError:
        relative = path.as_posix()
    return f"{relative}:{line}" if line else relative


def _first_sentence(text: str) -> str:
    """The opening line of a docstring — the summary every module already has."""
    head = (text or "").strip().split("\n", 1)[0].strip()
    return head


class ContextIndex:
    """Every facet, and the queries that make the connections useful."""

    def __init__(self, facets: Iterable[Facet] = (), *, root: Path | None = None) -> None:
        self.root = root or _repository()
        self._facets: dict[str, Facet] = {}
        for facet in facets:
            self.add(facet)

    # -- construction ----------------------------------------------------

    def add(self, facet: Facet) -> Facet:
        """Add a facet, or merge its links into the one already held.

        Merging rather than replacing is what lets the builders run in any
        order and discover edges from either end — a screen declares the routes
        it reads, and the route builder separately knows nothing about screens.
        The first builder to describe a facet wins on title and summary; every
        builder contributes links.
        """
        held = self._facets.get(facet.id)
        if held is None:
            self._facets[facet.id] = facet
            return facet
        merged = held.with_links(*facet.links)
        if not merged.source and facet.source:
            merged = Facet(
                kind=merged.kind, name=merged.name, title=merged.title,
                summary=merged.summary or facet.summary, source=facet.source,
                links=merged.links, tags=merged.tags,
            )
        self._facets[merged.id] = merged
        return merged

    # -- access ----------------------------------------------------------

    @property
    def facets(self) -> tuple[Facet, ...]:
        """Sorted, always. Filesystem order must never reach the digest."""
        return tuple(self._facets[key] for key in sorted(self._facets))

    def get(self, facet_id: str) -> Facet | None:
        return self._facets.get(facet_id)

    def of_kind(self, kind: FacetKind) -> tuple[Facet, ...]:
        return tuple(item for item in self.facets if item.kind is kind)

    @property
    def unanchored(self) -> tuple[Facet, ...]:
        """Facets that resolve to no file. Counted, never hidden."""
        return tuple(item for item in self.facets if not item.anchored)

    @property
    def coverage(self) -> float:
        """The share of facets that resolve to a file and a line."""
        if not self._facets:
            return 0.0
        return round(1 - len(self.unanchored) / len(self._facets), 4)

    @property
    def dangling(self) -> tuple[tuple[str, Link], ...]:
        """Links pointing at a facet that does not exist.

        The index's own honesty check, and the thing `slpie/audit/` turns into a
        `VIOLATED` verdict: a screen reading a route nobody serves, or a test
        covering a module that was deleted, is drift with a file and a line
        attached rather than a string nobody rereads.
        """
        return tuple(
            (facet.id, link)
            for facet in self.facets
            for link in facet.links
            if link.target not in self._facets
        )

    # -- queries ---------------------------------------------------------

    def out(self, facet_id: str, relation: Relation | None = None) -> tuple[Facet, ...]:
        """What this facet reaches."""
        facet = self._facets.get(facet_id)
        if facet is None:
            return ()
        targets = [
            link.target for link in facet.links
            if relation is None or link.relation is relation
        ]
        return tuple(
            self._facets[target] for target in sorted(set(targets))
            if target in self._facets
        )

    def into(self, facet_id: str, relation: Relation | None = None) -> tuple[Facet, ...]:
        """What reaches this facet.

        Computed rather than stored. Storing both directions is how they come to
        disagree, and an index that disagrees with itself is worse than none.
        """
        return tuple(
            facet for facet in self.facets
            if any(
                link.target == facet_id
                and (relation is None or link.relation is relation)
                for link in facet.links
            )
        )

    def connected(self, facet_id: str, *, depth: int = 1) -> tuple[Facet, ...]:
        """Everything within `depth` hops, either direction. The whole point.

        This is the query the skill and the console both ask: *what is connected
        to this?* Both directions, because "what does this screen read" and "who
        reads this route" are the same question asked from opposite ends, and a
        reader orienting themselves needs both.
        """
        seen = {facet_id}
        frontier = {facet_id}
        for _ in range(max(0, depth)):
            found: set[str] = set()
            for current in frontier:
                found.update(item.id for item in self.out(current))
                found.update(item.id for item in self.into(current))
            frontier = found - seen
            seen |= found
            if not frontier:
                break
        return tuple(
            self._facets[key] for key in sorted(seen - {facet_id})
            if key in self._facets
        )

    def search(self, text: str, *, limit: int = 20) -> tuple[Facet, ...]:
        """Substring match over id, title and summary. Deterministic ordering."""
        needle = text.lower().strip()
        if not needle:
            return ()
        hits = [
            facet for facet in self.facets
            if needle in facet.id.lower()
            or needle in facet.title.lower()
            or needle in facet.summary.lower()
        ]
        # An exact id match ranks first; everything else keeps sorted order, so
        # two runs of the same query return the same list in the same order.
        hits.sort(key=lambda item: (needle != item.name.lower(), item.id))
        return tuple(hits[:limit])

    # -- the digest ------------------------------------------------------

    @property
    def digest(self) -> str:
        """Content-addressed over the ordered facet digests.

        Same trick as §12's snapshot `root_digest` and §25's audit digest: an
        unchanged tree yields an unchanged value, so "the product has not
        drifted" is one comparable string a CI job can pin.
        """
        body = "\n".join(f"{facet.id} {facet.digest}" for facet in self.facets)
        return hashlib.blake2b(body.encode("utf-8"), digest_size=32).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for facet in self.facets:
            counts[facet.kind.value] = counts.get(facet.kind.value, 0) + 1
        return {
            "digest": self.digest,
            "facets": len(self._facets),
            "counts": {key: counts[key] for key in sorted(counts)},
            "coverage": self.coverage,
            "unanchored": [item.id for item in self.unanchored],
            "dangling": [
                {"from": source, **link.to_dict()} for source, link in self.dangling
            ],
        }

    def __len__(self) -> int:
        return len(self._facets)

    def __iter__(self) -> Iterator[Facet]:
        return iter(self.facets)


# --- the builders -------------------------------------------------------
#
# Each one reads a source that already exists and emits facets. None of them
# holds a list of names: a verb added to the registry, a route added to the
# server, a component exported from a browser module and a test file added to
# `tests/` all appear here with no file below this line edited. That is the
# property the whole index rests on, and `test_the_index_is_read_not_restated`
# is what keeps it true.


def _verb_sources(root: Path) -> dict[str, str]:
    """Where each verb is declared, by scanning the verb packages once.

    A verb is a `Verb(name="findings", …)` literal inside a factory function,
    not a top-level definition, so the AST projection cannot name it — it sees
    the enclosing function. Scanning for the keyword literal is deterministic
    and finds the real line, which is what a reader following the index needs.
    """
    found: dict[str, str] = {}
    slpie = root / "slpie"
    if not slpie.is_dir():
        return found
    # Two homes, because the codebase has two: the compose families, and a
    # package's own `verbs.py` beside the capability it wraps. Scanning only the
    # first left `verb:context` and its route unanchored the moment this package
    # registered itself — the index catching its own author out, which is the
    # behaviour that makes it worth having.
    paths = sorted(
        {*(slpie / "compose" / "verbs").rglob("*.py"), *slpie.glob("*/verbs.py")}
    )
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in re.finditer(r'name=["\']([a-z][a-z0-9-]*)["\']', text):
            name = match.group(1)
            if name in found:
                continue
            line = text.count("\n", 0, match.start()) + 1
            found[name] = _at(root, path, line)
    return found


def _add_modules(index: ContextIndex, root: Path) -> None:
    """Modules, packages, their imports, and the plan sections they claim."""
    for tree in TREES:
        base = root / tree
        if not base.is_dir():
            continue
        projection: Projection = project(base)
        for module in projection:
            if tree == "tests":
                _add_test(index, root, base, module)
                continue
            _add_module(index, root, base, tree, module)


def _qualified(tree: str, module: Module) -> str:
    """`compose.registry` under the `slpie` tree → `slpie.compose.registry`.

    The projection names modules relative to the tree it was given, so the tree
    name has to be put back for the id to be unambiguous across rings — without
    it `gratimos.shell` and a hypothetical `slpie.shell` would collide.
    """
    return tree if module.name == tree else f"{tree}.{module.name}"


def _add_module(
    index: ContextIndex, root: Path, base: Path, tree: str, module: Module,
) -> None:
    name = _qualified(tree, module)
    path = Path(module.path)
    package = path.name == "__init__.py"
    kind = FacetKind.PACKAGE if package else FacetKind.MODULE

    links: list[Link] = []
    for item in module.imports:
        target = item.target
        if item.relative:
            # A relative import is resolved by the projection into a dotted name
            # already rooted at the tree, so it needs the same qualification.
            #
            # With one correction the projection cannot make: it resolves
            # `from .client import X` against the *parent* of the importing
            # module's dotted name, which is right for `a2a/server.py` and wrong
            # for `a2a/__init__.py`, because a package's own name is already the
            # package. Uncorrected, every `__init__.py` in the repository
            # re-exported its children as siblings — 215 links pointing at
            # modules that do not exist. The projection cannot tell the two
            # cases apart without knowing the file is an `__init__.py`; here we
            # do know, so the fix belongs here rather than in a shared walker
            # whose behaviour three boundary tests depend on.
            if package:
                target = _package_relative(module.name, target, item.relative)
            target = target if target.startswith(f"{tree}.") else f"{tree}.{target}"
        if not any(target == item_tree or target.startswith(f"{item_tree}.")
                   for item_tree in TREES):
            continue                      # stdlib and third-party are not facets
        links.append(Link(Relation.IMPORTS, f"module:{target}"))

    for section in sorted({match.group(1) for match in SECTION.finditer(module.doc)}):
        links.append(Link(Relation.CLAIMS, f"section:{int(section)}"))

    if package:
        for child in projected_children(base, tree, path):
            links.append(Link(Relation.OWNS, child))

    index.add(Facet(
        kind=kind, name=name,
        title=name.rsplit(".", 1)[-1],
        summary=_first_sentence(module.doc),
        source=_at(root, path, 1) if module.parsed else _at(root, path),
        links=tuple(links),
        tags=("unparsed",) if not module.parsed else (),
    ))


def projected_children(base: Path, tree: str, init: Path) -> tuple[str, ...]:
    """The facet ids a package directly owns — its modules and sub-packages.

    Directory listing rather than a name-prefix match over the projection: a
    prefix match would make `slpie.compose` own `slpie.compose.verbs.governance`
    as well as `slpie.compose.verbs`, and a tree where everything is owned by
    the root is not a tree.
    """
    directory = init.parent
    prefix = _dotted_dir(base, directory, tree)
    owned: list[str] = []
    for child in sorted(directory.iterdir()):
        if child.name.startswith((".", "_")) and child.name != "__init__.py":
            continue
        if child.is_dir() and (child / "__init__.py").is_file():
            owned.append(f"package:{prefix}.{child.name}")
        elif child.suffix == ".py" and child.name != "__init__.py":
            owned.append(f"module:{prefix}.{child.stem}")
    return tuple(owned)


def _dotted_dir(base: Path, directory: Path, tree: str) -> str:
    relative = directory.resolve().relative_to(base.resolve())
    parts = [part for part in relative.parts if part not in (".", "")]
    return ".".join([tree, *parts])


def _add_test(index: ContextIndex, root: Path, base: Path, module: Module) -> None:
    """One facet per test file, linked to what it covers.

    File granularity, not function: 2400 test facets would drown every other
    kind and answer a question nobody asks. What a reader wants is *which file
    exercises this module*, and that is derived from the test's own imports
    rather than from its name — a naming convention would be a second source of
    truth about coverage, and would be wrong for every test that outgrew its
    filename.
    """
    path = Path(module.path)
    if not path.name.startswith("test_"):
        return
    links = [
        Link(Relation.COVERS, f"module:{item.target}")
        for item in module.imports
        if not item.relative and item.top in TREES and item.top != "tests"
    ]
    index.add(Facet(
        kind=FacetKind.TEST, name=path.stem,
        title=path.stem.replace("test_", "").replace("_", " "),
        summary=_first_sentence(module.doc),
        source=_at(root, path, 1),
        links=tuple(links),
    ))


def _add_kinds(index: ContextIndex, root: Path) -> None:
    from ..compose.flow import Kind

    source = _at(root, root / "slpie" / "compose" / "flow.py",
                 _line_of(root / "slpie" / "compose" / "flow.py", "class Kind"))
    for kind in Kind:
        index.add(Facet(
            kind=FacetKind.KIND, name=kind.value,
            title=kind.value.upper(),
            summary=f"What flows between stages as {kind.value.upper()}.",
            source=source,
        ))


def _add_verbs(index: ContextIndex, root: Path, verbs: Any) -> None:
    sources = _verb_sources(root)
    for verb in verbs:
        links = [
            Link(Relation.CONSUMES, f"kind:{verb.consumes.value}"),
            Link(Relation.PRODUCES, f"kind:{verb.produces.value}"),
            Link(Relation.PROJECTS, f"route:POST /api/v/{verb.name}"),
        ]
        index.add(Facet(
            kind=FacetKind.VERB, name=verb.name,
            title=verb.name, summary=verb.summary,
            source=sources.get(verb.name, ""),
            links=tuple(links),
            tags=tuple(filter(None, (
                verb.group, "mutates" if verb.mutates else "",
                "source" if verb.consumes.value == "nothing" else "",
            ))),
        ))


def _add_routes(index: ContextIndex, root: Path, routes: Sequence[tuple[str, str]]) -> None:
    api = root / "slpie" / "ui" / "api.py"
    for method, path in sorted(routes):
        name = f"{method} {path}"
        generated = path.startswith("/api/v/")
        source = ""
        if generated:
            verb = index.get(f"verb:{path.rsplit('/', 1)[-1]}")
            source = verb.source if verb else ""
        else:
            line = _line_of(api, f'"{path}"')
            source = _at(root, api, line) if line else ""
        index.add(Facet(
            kind=FacetKind.ROUTE, name=name, title=name,
            summary=("Generated from the verb registry."
                     if generated else "Declared on the API route table."),
            source=source,
            tags=("generated",) if generated else ("declared",),
        ))


def _add_screens(index: ContextIndex, root: Path, screens: Sequence[Any]) -> None:
    contract = root / "slpie" / "ui" / "contract.py"
    app = root / "slpie" / "ui" / "app" / "screens"
    for screen in screens:
        line = _line_of(contract, f'Screen("{screen.key}"')
        if not line:
            # A generated inspector. Anchored to the function that generates it,
            # which is where a reader has to look to understand why it exists.
            line = _line_of(contract, "def screens(")
        links = [Link(Relation.READS, f"route:{read}") for read in screen.reads]
        links += [Link(Relation.RUNS, f"verb:{name}") for name in screen.verbs]
        if screen.parent:
            links.append(Link(Relation.PARENT, f"screen:{screen.parent}"))
        if screen.authored:
            links.append(Link(Relation.RENDERS, f"component:screen-{screen.key}"))
        index.add(Facet(
            kind=FacetKind.SCREEN, name=screen.key,
            title=screen.title, summary=screen.summary,
            source=_at(root, contract, line),
            links=tuple(links),
            tags=tuple(filter(None, (
                screen.section,
                "authored" if screen.authored else "generated",
                "destination" if screen.is_destination else "view",
            ))),
        ))
        if screen.authored:
            module = app / f"{screen.key}.js"
            index.add(Facet(
                kind=FacetKind.COMPONENT, name=f"screen-{screen.key}",
                title=f"{screen.title} screen",
                summary=f"The hand-built module for the {screen.title} screen.",
                source=_at(root, module, 1),
                tags=("screen",),
            ))


def _add_components(index: ContextIndex, root: Path) -> None:
    """The browser's component vocabulary, read from its own exports.

    This is what makes "components as keys" checkable from Python: the
    dictionary is the set of functions the `ui/` modules actually export, so a
    screen block naming a component that does not exist is a dangling link the
    index reports rather than a blank area somebody finds in a browser.
    """
    ui = root / "slpie" / "ui" / "app" / "ui"
    if not ui.is_dir():
        return
    for path in sorted(ui.glob("*.js")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in JS_EXPORT.finditer(text):
            name = match.group(1)
            index.add(Facet(
                kind=FacetKind.COMPONENT, name=name, title=name,
                summary=f"Exported by ui/{path.name}.",
                source=_at(root, path, text.count("\n", 0, match.start()) + 1),
                tags=(path.stem,),
            ))


def _add_sections(index: ContextIndex, root: Path) -> None:
    """Plan sections, from the claims modules make in their own docstrings.

    Derived, never listed. A section exists here because at least one module
    says it implements it — so the sections *nobody* claims are visible by
    their absence, which is a more useful signal than a hand-written map that
    asserts an owner for every one of them.
    """
    claimed: set[int] = set()
    for facet in index.facets:
        for target in facet.linked(Relation.CLAIMS):
            claimed.add(int(target.split(":", 1)[1]))

    docs = root / "docs"
    for number in sorted(claimed):
        source = ""
        for candidate in sorted(docs.glob("*.md")) if docs.is_dir() else ():
            line = _line_of(candidate, f"§{number}")
            if line:
                source = _at(root, candidate, line)
                break
        index.add(Facet(
            kind=FacetKind.SECTION, name=str(number),
            title=f"§{number}",
            summary="A plan section claimed by at least one module.",
            source=source,
        ))


#: The last index built with no arguments, and the tree fingerprint it was
#: built from. One entry, because there is one running product.
_CACHE: tuple[str, "ContextIndex"] | None = None

#: Where the built index is kept between processes.
#:
#: An in-process cache does nothing for the CLI, which is the surface that
#: needed it most: every `slpie context query` is a new interpreter, so a
#: memory-only cache left it at 2.7 seconds a call. The facets are already
#: serialisable — that is what `index.json` is — so the same shape is written
#: beside the tree and reloaded when the fingerprint still matches.
CACHE_DIR = Path(".slpie") / "cache"


def fingerprint(root: Path | None = None) -> str:
    """A cheap digest of the trees: every file's path, size and mtime.

    Stat, not read. Parsing six hundred modules costs seconds; stating them
    costs milliseconds, and the question "has anything changed" does not need
    the contents — only whether the bytes could have.

    Deliberately not a content hash. A content hash would be exact and would
    also cost most of what it is trying to save; mtime-and-size is the standard
    trade every build system makes, and the failure it admits — a file rewritten
    within the same nanosecond at the same length — is one nothing here can
    produce.
    """
    base = root or _repository()
    parts: list[str] = []
    for tree in TREES:
        directory = base / tree
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.py")):
            if any(skip in path.parts for skip in ("__pycache__", ".git")):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            parts.append(f"{path.relative_to(base)}\x1f{stat.st_size}\x1f{stat.st_mtime_ns}")
    return hashlib.blake2b("\n".join(parts).encode("utf-8"), digest_size=16).hexdigest()


def _cache_file(root: Path, mark: str) -> Path:
    return root / CACHE_DIR / f"context-{mark}.json"


def _load(root: Path, mark: str) -> "ContextIndex | None":
    """The index from disk, or nothing. Never raises.

    A cache that can fail the caller is worse than no cache: the fallback is
    simply to build, which is correct and merely slower, so every failure here —
    missing, truncated, written by another version, unreadable — takes the same
    quiet path.
    """
    path = _cache_file(root, mark)
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
        if body.get("contract") != CACHE_CONTRACT:
            return None
        index = ContextIndex(root=root)
        for item in body["facets"]:
            index.add(Facet.from_dict(item))
        # The digest is recomputed, not trusted. A cache file whose stored
        # digest disagrees with its own contents is corrupt, and believing the
        # stored one would let it stay corrupt silently.
        if index.digest != body.get("digest"):
            return None
        return index
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _store(root: Path, mark: str, index: "ContextIndex") -> None:
    """Write the index, and drop the entries this one supersedes."""
    path = _cache_file(root, mark)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written to a sibling and moved, so a reader never sees half a file.
        temporary = path.with_suffix(".partial")
        temporary.write_text(json.dumps({
            "contract": CACHE_CONTRACT,
            "digest": index.digest,
            "facets": [facet.to_dict() for facet in index],
        }), encoding="utf-8")
        temporary.replace(path)
        for stale in path.parent.glob("context-*.json"):
            if stale != path:
                stale.unlink(missing_ok=True)
    except OSError:
        pass                    # an unwritable cache is not an error, only slower


#: Bumped when the stored shape changes, so an old file is ignored rather than
#: misread. Cheaper than a migration for something that can always be rebuilt.
CACHE_CONTRACT = 1


def build(
    *,
    root: Path | str | None = None,
    verbs: Any = None,
    routes: Sequence[tuple[str, str]] | None = None,
    screens: Sequence[Any] | None = None,
    fresh: bool = False,
) -> ContextIndex:
    """Read every source and project it into one index.

    Every argument defaults to the live thing, so `build()` with no arguments
    describes the running product. They exist for tests, which need to build an
    index over a registry they control rather than the whole platform.

    **Only the no-argument call is cached.** A caller that supplied its own
    registry is asking about something other than the running product, and
    handing it a cached answer about the running product would be wrong in a way
    that is very hard to see — the index would simply be about the wrong thing.
    So the cache is consulted only when every argument is absent.
    """
    global _CACHE

    default = (root is None and verbs is None and routes is None and screens is None)
    mark = ""
    if default and not fresh:
        mark = fingerprint()
        if _CACHE is not None and _CACHE[0] == mark:
            return _CACHE[1]
        held = _load(_repository(), mark)
        if held is not None:
            _CACHE = (mark, held)
            return held

    base = Path(root).resolve() if root else _repository()
    index = ContextIndex(root=base)

    if verbs is None:
        from ..compose.registry import registry
        verbs = registry()
    if routes is None:
        from ..ui.api import Api
        routes = tuple(Api(engine=None).routes)
    if screens is None:
        from ..ui.contract import screens as screen_manifest
        screens = screen_manifest(verbs=verbs, routes=routes)

    _add_kinds(index, base)
    _add_verbs(index, base, verbs)
    _add_routes(index, base, routes)
    _add_components(index, base)
    _add_screens(index, base, screens)
    _add_modules(index, base)
    _add_sections(index, base)
    _resolve_links(index)

    if default and not fresh:
        # Fingerprint again rather than reusing the one from the top: a file
        # edited *while* the index was being built would otherwise be cached
        # under the pre-edit mark and served until something else changed.
        settled = fingerprint()
        _CACHE = (settled, index)
        _store(base, settled, index)
    return index


def _resolve_links(index: ContextIndex) -> None:
    """Settle `module:` versus `package:` on links, once every facet exists.

    A builder emitting a link cannot know whether `slpie.compose` is a module or
    a package — that depends on a file it has not walked yet. Guessing at emit
    time produced 736 dangling links pointing at real code under the wrong kind,
    which would have made `dangling` useless as an honesty signal: a genuine
    drift would have been invisible in the noise.

    So the kind is settled here, when the whole tree is known, and only when the
    other kind genuinely exists. A link to something that is neither is left
    alone — it is *supposed* to dangle, because that is a fact about the product
    worth reporting rather than a bookkeeping detail to tidy away.
    """
    known = {facet.id for facet in index.facets}
    swap = {"module:": "package:", "package:": "module:"}

    for facet in index.facets:
        repaired: list[Link] = []
        changed = False
        for link in facet.links:
            target = link.target
            head, _, tail = target.partition(":")
            if not tail or tail.endswith("."):
                # A relative import that resolved to nothing. Dropping it is
                # right: it names no module, so it is not a fact about anything.
                changed = True
                continue
            if target not in known:
                other = f"{swap.get(head + ':', '')}{tail}"
                if other and other in known:
                    link = Link(link.relation, other)
                    changed = True
            repaired.append(link)
        if changed:
            index._facets[facet.id] = Facet(
                kind=facet.kind, name=facet.name, title=facet.title,
                summary=facet.summary, source=facet.source,
                links=tuple(sorted(set(repaired))), tags=facet.tags,
            )


def _strip(dotted: str, levels: int) -> str:
    """Drop `levels` trailing segments from a dotted name."""
    parts = dotted.split(".") if dotted else []
    kept = parts[: len(parts) - levels] if levels <= len(parts) else []
    return ".".join(kept)


def _package_relative(name: str, resolved: str, level: int) -> str:
    """Re-anchor a relative import that was resolved against the wrong base.

    `_resolve_relative` in the AST projection anchors `from .x import y` to the
    *parent* of the importing module's dotted name. That is correct for
    `a2a/server.py` and wrong for `a2a/__init__.py`, where the module's own name
    already **is** the package — so every `__init__.py` in the repository
    resolved its children one level too high.

    The projection cannot fix this without knowing the file is an `__init__.py`,
    and three boundary tests depend on its behaviour, so the correction lives
    here. Both bases are recomputed and the prefix is swapped, which handles
    `from ..verb import Verb` inside `compose/verbs/__init__.py` as well as the
    single-dot case — the version that special-cased `level == 1` left thirteen
    links pointing at modules that do not exist.
    """
    theirs = _strip(_strip(name, 1), level - 1)     # what the projection used
    ours = _strip(name, level - 1)                  # what a package needs
    if theirs and resolved.startswith(f"{theirs}."):
        tail = resolved[len(theirs) + 1:]
    elif not theirs:
        tail = resolved
    else:
        return resolved
    return f"{ours}.{tail}" if ours else tail
