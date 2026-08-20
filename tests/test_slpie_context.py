"""§31 — the metadata index, and the skill generated from it.

The index makes one claim: *it reads the registries rather than restating them*.
Every test here exists to hold that claim up, because an index maintained beside
the code is worth less than no index at all — it is confidently wrong, and the
confidence is the damage.
"""

from __future__ import annotations

import json

import pytest

from slpie.compose.flow import Kind
from slpie.compose.registry import VerbRegistry, registry
from slpie.compose.verb import Param, Verb
from slpie.context import ContextIndex, Facet, FacetKind, Link, Relation, build
from slpie.context.index import fingerprint
from slpie.context.skill import artifacts, render_json, render_markdown, write

from _walk import REPOSITORY


@pytest.fixture(scope="module")
def index() -> ContextIndex:
    """One index for the module. Building it walks three trees."""
    return build()


# -- the facet -----------------------------------------------------------


def test_a_facet_addresses_itself_by_kind_and_name() -> None:
    facet = Facet(FacetKind.VERB, "findings", source="a.py:1")
    assert facet.id == "verb:findings"
    assert facet.path == "a.py"


def test_a_facet_without_a_source_is_unanchored_rather_than_dropped() -> None:
    """The honesty rule, at facet level.

    An index that silently omitted what it could not place would describe a
    smaller, cleaner product than the one that exists — the same failure
    `Verdict.INDETERMINATE` exists to prevent in the judge.
    """
    assert not Facet(FacetKind.SECTION, "31").anchored
    assert Facet(FacetKind.SECTION, "31", source="docs/x.md:4").anchored


def test_the_digest_moves_when_a_link_appears() -> None:
    """Links take part in the digest, or a rewiring would go unnoticed."""
    bare = Facet(FacetKind.SCREEN, "graph", source="a.py:1")
    wired = bare.with_links(Link(Relation.READS, "route:GET /api/graph"))
    assert bare.digest != wired.digest


def test_adding_links_is_order_independent() -> None:
    """Builders discover edges from either end, so order must not reach the digest."""
    one = Link(Relation.READS, "route:a")
    two = Link(Relation.RUNS, "verb:b")
    base = Facet(FacetKind.SCREEN, "x", source="a.py:1")
    assert base.with_links(one, two).digest == base.with_links(two, one).digest


# -- the index -----------------------------------------------------------


def test_the_index_is_reproducible_over_an_unchanged_tree(index: ContextIndex) -> None:
    """Two runs agree, or the digest cannot gate anything."""
    assert build().digest == index.digest
    assert len(index) > 400


def test_every_link_points_at_a_facet_that_exists(index: ContextIndex) -> None:
    """No dangling links.

    This is the index's own honesty check and it is load-bearing: a screen
    reading a route nobody serves, or a test covering a deleted module, is drift
    with an address attached. It only works as a signal while the number is
    zero — 736 links pointing at real code under the wrong kind, which is what
    the first build produced, would have buried a genuine one in noise.
    """
    assert index.dangling == (), "\n".join(
        f"{source} → {link}" for source, link in index.dangling[:20]
    )


def test_almost_every_facet_resolves_to_a_file_and_a_line(index: ContextIndex) -> None:
    assert index.coverage > 0.95
    # What remains unanchored is sections nobody documented, never code.
    assert all(item.kind is FacetKind.SECTION for item in index.unanchored)


def test_the_index_is_read_not_restated() -> None:
    """A verb registered in a test appears in the index with no file edited.

    The whole design rests on this. If the index held its own list of verbs it
    would be a second registry, and two registries disagree by the second
    release — which is the drift §24 exists to prevent, reintroduced one level
    up.
    """
    invented = Verb(
        name="zzz-invented", group="context", produces=Kind.REPORT,
        summary="a verb that exists only inside this test",
        params=(Param("only", "str", "nothing"),),
        run=lambda flow, arguments, context: flow,
    )
    verbs = VerbRegistry(list(registry()) + [invented])
    index = build(verbs=verbs, routes=(), screens=())

    facet = index.get("verb:zzz-invented")
    assert facet is not None
    assert facet.summary == "a verb that exists only inside this test"
    assert "kind:report" in facet.linked(Relation.PRODUCES)


def test_a_verb_reaches_its_route_and_the_screen_that_runs_it(
    index: ContextIndex,
) -> None:
    """The chain a reader actually follows, in both directions."""
    connected = {item.id for item in index.connected("verb:findings")}
    assert "route:POST /api/v/findings" in connected
    assert "screen:findings" in connected

    readers = {item.id for item in index.into("route:GET /api/findings")}
    assert "screen:findings" in readers


def test_a_screen_knows_the_views_that_hang_off_it(index: ContextIndex) -> None:
    """`Screen.parent` reaches the index as an inbound `parent` link."""
    views = {item.name for item in index.into("screen:graph", Relation.PARENT)}
    assert {"node", "impact", "cycles"} <= views


def test_a_package_owns_its_own_children_and_not_its_grandchildren(
    index: ContextIndex,
) -> None:
    """A tree where the root owns everything is not a tree."""
    owned = set(index.get("package:slpie.compose").linked(Relation.OWNS))
    assert "module:slpie.compose.registry" in owned
    assert "package:slpie.compose.verbs" in owned
    assert "module:slpie.compose.verbs.analysis" not in owned


def test_a_package_relative_import_resolves_inside_its_own_package(
    index: ContextIndex,
) -> None:
    """`from .registry import x` in `compose/__init__.py` is a sibling, not a cousin.

    The AST projection anchors relative imports to the *parent* of the importing
    module, which is right for a module and wrong for an `__init__.py`. Left
    uncorrected, every package in the repository re-exported its children as
    siblings — 215 links pointing at modules that do not exist.
    """
    imports = set(index.get("package:slpie.compose").linked(Relation.IMPORTS))
    assert any(target.endswith("slpie.compose.registry") for target in imports)
    assert not any(
        target in ("module:slpie.registry", "package:slpie.registry")
        for target in imports
    )


def test_sections_are_claimed_by_code_never_listed_here(index: ContextIndex) -> None:
    """A section exists because a module says it implements it.

    The alternative is a hand-written section→package map, which is right the
    day it is written and wrong two renames later.
    """
    claimers = {item.id for item in index.into("section:24", Relation.CLAIMS)}
    assert "module:slpie.compose.registry" in claimers
    assert index.get("section:24") is not None


def test_search_is_deterministic(index: ContextIndex) -> None:
    assert index.search("findings") == index.search("findings")
    assert index.search("") == ()


def test_connected_covers_both_directions(index: ContextIndex) -> None:
    """'What does this read' and 'who reads this' are one question."""
    assert index.get("screen:findings").id in {
        item.id for item in index.connected("route:GET /api/findings")
    }


# -- the skill -----------------------------------------------------------


def test_the_committed_skill_matches_its_generator(index: ContextIndex) -> None:
    """The permanent fix, rather than one regeneration.

    Same `--check` discipline as the four contract emitters: a stale committed
    artifact fails here rather than misleading whoever reads it next.
    """
    stale = write(index, check=True)
    assert not stale, "\n".join(stale) + "\n\nrun `slpie context --skill`"


def test_the_hand_written_half_is_not_generated() -> None:
    """`SKILL.md` is authored, and the generator must never claim it.

    The split is the design: what changes rarely is written, what changes every
    commit is generated. A generator that overwrote `SKILL.md` would erase the
    invariants on the next run.
    """
    assert "SKILL.md" not in artifacts(build())
    skill = (REPOSITORY / ".claude" / "skills" / "slpie" / "SKILL.md").read_text()
    assert skill.startswith("---\nname: slpie\n")
    assert "Never merge to `main`" in skill


def test_the_generated_map_names_the_command_that_rebuilds_it(
    index: ContextIndex,
) -> None:
    body = render_markdown(index)
    assert "slpie context --skill" in body
    assert index.digest in body


def test_the_json_map_carries_every_facet(index: ContextIndex) -> None:
    body = json.loads(render_json(index))
    assert body["digest"] == index.digest
    assert len(body["facets"]) == len(index)
    assert {item["id"] for item in body["facets"]} == {item.id for item in index}


def test_the_root_pointer_does_not_restate_the_skill() -> None:
    """`CLAUDE.md` points; it does not duplicate.

    Two documents both trying to be the orientation is how they drift, and the
    one nobody regenerates wins by being read first.
    """
    body = (REPOSITORY / "CLAUDE.md").read_text()
    assert ".claude/skills/slpie/SKILL.md" in body
    assert len(body.splitlines()) < 40


# -- the cache -----------------------------------------------------------


def test_an_unchanged_tree_is_answered_from_cache() -> None:
    """`build()` parses six hundred modules and took 2.6s every call.

    That made `slpie context query` unusable interactively and made
    `POST /api/v/context` an expensive uncached route. The fingerprint stats the
    same files instead of parsing them, which is the standard trade every build
    system makes.
    """
    import time

    first = build()
    started = time.perf_counter()
    second = build()
    warm = time.perf_counter() - started

    assert second is first, "an unchanged tree should not be re-parsed"
    assert warm < 0.5, f"a cached build took {warm:.2f}s — the cache is not being hit"


def test_touching_a_module_lets_the_cache_go(tmp_path) -> None:
    """Stat, not content: size and mtime are what the fingerprint watches."""
    before = fingerprint()
    target = REPOSITORY / "slpie" / "context" / "facet.py"
    original = target.stat().st_mtime_ns
    try:
        target.touch()
        assert fingerprint() != before
        assert build() is not None
    finally:
        import os
        os.utime(target, ns=(original, original))


def test_a_caller_with_its_own_registry_never_gets_the_cached_index() -> None:
    """The correctness half, and the one worth guarding.

    A caller supplying its own registry is asking about something other than the
    running product. Handing it the cached answer would be wrong in a way that
    is very hard to see — the index would simply be about the wrong thing, with
    no error anywhere.
    """
    running = build()
    empty = build(verbs=VerbRegistry(), routes=(), screens=())

    assert empty is not running
    assert not [item for item in empty if item.kind is FacetKind.VERB]
    assert [item for item in running if item.kind is FacetKind.VERB]


def test_fresh_forces_a_rebuild() -> None:
    assert build(fresh=True) is not build(fresh=True)


def test_the_disk_cache_makes_a_second_process_fast() -> None:
    """The in-memory cache did nothing for the CLI.

    Every `slpie context query` is a fresh interpreter, so a memory-only cache
    left the command at 2.7 seconds — which is the surface that needed it most.
    """
    import subprocess, sys, time

    subprocess.run([sys.executable, "-m", "slpie.cli", "context", "--digest"],
                   capture_output=True, cwd=REPOSITORY, check=True)
    started = time.perf_counter()
    done = subprocess.run([sys.executable, "-m", "slpie.cli", "context", "--digest"],
                          capture_output=True, cwd=REPOSITORY, check=True)
    warm = time.perf_counter() - started

    assert warm < 1.5, f"a second process took {warm:.2f}s — the disk cache is not being read"
    assert done.stdout.decode().strip() == build().digest


def test_a_corrupt_cache_is_ignored_rather_than_raised(tmp_path) -> None:
    """A cache that can fail the caller is worse than no cache.

    The fallback is to build, which is correct and merely slower, so every
    failure takes the same quiet path: truncated, wrong contract, wrong digest.
    """
    from slpie.context.index import _load, _store

    index = build()
    _store(tmp_path, "mark", index)
    path = tmp_path / ".slpie" / "cache" / "context-mark.json"
    assert path.is_file()
    assert _load(tmp_path, "mark") is not None

    path.write_text("{ not json", encoding="utf-8")
    assert _load(tmp_path, "mark") is None


def test_a_cache_whose_digest_disagrees_with_its_contents_is_refused(tmp_path) -> None:
    """Believing the stored digest would let a corrupt file stay corrupt."""
    import json

    from slpie.context.index import _load, _store

    _store(tmp_path, "mark", build())
    path = tmp_path / ".slpie" / "cache" / "context-mark.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["digest"] = "0" * 64
    path.write_text(json.dumps(body), encoding="utf-8")

    assert _load(tmp_path, "mark") is None


def test_the_cache_keeps_only_the_current_entry(tmp_path) -> None:
    from slpie.context.index import _store

    index = build()
    _store(tmp_path, "one", index)
    _store(tmp_path, "two", index)
    held = sorted(p.name for p in (tmp_path / ".slpie" / "cache").glob("context-*.json"))
    assert held == ["context-two.json"]
