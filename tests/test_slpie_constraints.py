"""Constraint solving, and the failure message that makes it worth having.

The headline case is `test_an_unsatisfiable_solve_names_both_requirements`. A
solver that returns "unsatisfiable" and stops has told the user that they have a
problem and nothing about which problem — they then bisect the dependency tree
by hand, which is the work the solver existed to do. So the conflict is part of
the return type and it names the pair and both windows.

The second theme is refusing to be confidently wrong: an unreadable range admits
nothing rather than everything, an unknown package produces a named conflict
rather than an assumption that some satisfying version exists somewhere, and a
step budget running out is reported as giving up rather than as a proof.
"""

from __future__ import annotations

import pytest

from slpie.errors import ReasoningError
from slpie.reasoning.constraints import (
    BacktrackingSolver,
    Change,
    Requirement,
    Solution,
    StaticIndex,
    classify,
    overlaps,
    safe_upgrades,
    solve,
    to_intervals,
)


def index(**versions: list[str]) -> StaticIndex:
    return StaticIndex({f"pkg:pypi/{name}": found for name, found in versions.items()})


# --- the conflict explanation -------------------------------------------


def test_an_unsatisfiable_solve_names_both_requirements_and_both_windows():
    solution = solve(
        [
            Requirement("pkg:pypi/urllib3", "<2", requested_by="pkg:pypi/requests"),
            Requirement("pkg:pypi/urllib3", ">=2", requested_by="pkg:pypi/boto3"),
        ],
        index(urllib3=["1.26.18", "2.0.7", "2.2.1"]),
    )

    assert not solution.satisfiable
    conflict = solution.conflicts[0]
    assert conflict.pair == ("pkg:pypi/requests", "pkg:pypi/boto3")
    assert "<2" in conflict.explain()
    assert ">=2" in conflict.explain()
    assert "2.2.1" in conflict.explain(), "the available versions are named too"


def test_an_unsatisfiable_solution_cannot_be_built_without_a_conflict():
    """The type refuses the shrug. 'It does not work' is not a usable answer."""
    with pytest.raises(ReasoningError, match="must name a conflict"):
        Solution(satisfiable=False)


def test_an_unknown_package_is_a_named_conflict_not_a_silent_assumption():
    solution = solve(
        [Requirement("pkg:pypi/ghost", ">=1", requested_by="root")],
        StaticIndex(),
    )

    assert not solution.satisfiable
    assert "no versions of it are known" in solution.explain()
    assert "not declared" in solution.conflicts[0].reason


def test_the_conflicting_pair_is_preferred_over_an_arbitrary_single_requirement():
    """Naming one individually-satisfiable requirement sends the reader wrong."""
    solution = solve(
        [
            Requirement("pkg:pypi/six", ">=1.16", requested_by="pkg:pypi/a"),
            Requirement("pkg:pypi/six", "<1.15", requested_by="pkg:pypi/b"),
        ],
        index(six=["1.14.0", "1.16.0"]),
    )

    conflict = solution.conflicts[0]
    assert conflict.right is not None
    assert set(conflict.pair) == {"pkg:pypi/a", "pkg:pypi/b"}


# --- solving -------------------------------------------------------------


def test_a_satisfiable_solve_picks_the_newest_version_that_works():
    solution = solve(
        [Requirement("pkg:pypi/flask", ">=2.0", requested_by="root")],
        index(flask=["2.0.0", "2.3.3", "3.0.0"]),
    )

    assert solution.satisfiable
    assert solution.version_of("pkg:pypi/flask") == "3.0.0"


def test_candidates_are_ordered_by_the_version_algebra_not_by_string():
    """`1.10.0` sorts before `1.9.0` lexically; a solver reading it that way
    picks the wrong 'latest' every time."""
    found = StaticIndex({"pkg:pypi/x": ["1.9.0", "1.10.0", "1.2.0"]})
    assert found.versions_of("pkg:pypi/x")[0] == "1.10.0"


def test_transitive_requirements_are_pulled_in_from_the_index():
    found = StaticIndex()
    found.add(
        "pkg:pypi/requests", "2.31.0",
        Requirement("pkg:pypi/urllib3", ">=1.21.1,<3", requested_by="pkg:pypi/requests"),
    )
    found.add("pkg:pypi/urllib3", "1.26.18")
    found.add("pkg:pypi/urllib3", "2.2.1")

    solution = solve([Requirement("pkg:pypi/requests", ">=2.0")], found)

    assert solution.satisfiable
    assert solution.version_of("pkg:pypi/urllib3") == "2.2.1"


def test_depth_records_how_far_a_package_sits_from_the_root():
    found = StaticIndex()
    found.add(
        "pkg:pypi/requests", "2.31.0",
        Requirement("pkg:pypi/urllib3", ">=1", requested_by="pkg:pypi/requests"),
    )
    found.add("pkg:pypi/urllib3", "2.2.1")

    solution = solve([Requirement("pkg:pypi/requests", ">=2.0")], found)
    depths = {a.coordinate: a.depth for a in solution.assignments}

    assert depths["pkg:pypi/requests"] == 0
    assert depths["pkg:pypi/urllib3"] == 1


def test_backtracking_finds_the_older_version_whose_dependencies_work():
    """The newest candidate is tried first and must be abandoned."""
    found = StaticIndex()
    found.add(
        "pkg:pypi/app", "2.0.0",
        Requirement("pkg:pypi/lib", ">=5", requested_by="pkg:pypi/app"),
    )
    found.add(
        "pkg:pypi/app", "1.0.0",
        Requirement("pkg:pypi/lib", ">=1,<2", requested_by="pkg:pypi/app"),
    )
    found.add("pkg:pypi/lib", "1.5.0")

    solution = solve([Requirement("pkg:pypi/app", ">=1")], found)

    assert solution.satisfiable
    assert solution.version_of("pkg:pypi/app") == "1.0.0"
    assert solution.attempts > 1, "it had to back out of the 2.0.0 branch"


def test_an_unreadable_range_admits_nothing_rather_than_everything():
    requirement = Requirement("pkg:pypi/x", "whatever-latest")
    assert not requirement.readable
    assert not requirement.allows("1.0.0")

    solution = solve([requirement], index(x=["1.0.0"]))
    assert solution.satisfiable, "it is set aside, not silently widened"
    assert solution.unreadable and solution.unreadable[0].range == "whatever-latest"


def test_giving_up_is_reported_as_giving_up_and_not_as_unsatisfiable():
    found = StaticIndex()
    for i in range(40):
        found.add("pkg:pypi/a", f"1.{i}.0",
                  Requirement("pkg:pypi/b", ">=99", requested_by="pkg:pypi/a"))
    found.add("pkg:pypi/b", "1.0.0")

    solution = BacktrackingSolver(max_steps=3).solve(
        [Requirement("pkg:pypi/a", ">=1")], found,
    )

    assert not solution.satisfiable
    assert solution.exhausted
    assert "not an unsatisfiability proof" in solution.explain()


def test_an_optional_requirement_never_blocks_a_solve():
    solution = solve(
        [
            Requirement("pkg:pypi/flask", ">=2.0"),
            Requirement("pkg:pypi/absent", ">=9", optional=True),
        ],
        index(flask=["3.0.0"]),
    )
    assert solution.satisfiable


def test_the_solve_renders_as_reasoning_steps_carrying_their_evidence_ids():
    requirement = Requirement(
        "pkg:pypi/flask", ">=2.0", derived_from=("evidence-1",),
    )
    solver = BacktrackingSolver()
    solution = solver.solve([requirement], index(flask=["3.0.0"]))

    steps = solver.steps(solution)
    assert steps and "flask resolves to 3.0.0" in steps[0].claim
    assert "evidence-1" in steps[0].inputs


# --- interval arithmetic -------------------------------------------------


def test_ranges_sharing_only_a_boundary_overlap_only_when_both_admit_it():
    assert overlaps("<=1.9", ">=1.9")
    assert not overlaps("<1.9", ">=1.9")
    assert not overlaps("<=1.9", ">1.9")


def test_a_caret_range_and_an_explicit_window_are_compared_correctly():
    assert overlaps("^1.2.0", ">=1.5,<2.0")
    assert not overlaps("^1.2.0", ">=2.0")


def test_an_unreadable_range_is_reported_as_possibly_compatible_not_disjoint():
    """Declaring a conflict we could not compute sends somebody hunting for a
    problem that may not exist."""
    verdict = overlaps("not-a-range", ">=1.0")
    assert verdict.overlaps
    assert not verdict.certain


def test_a_wildcard_range_overlaps_everything():
    assert overlaps("*", ">=99.0")


def test_an_interval_renders_its_boundaries_so_a_human_can_read_the_conflict():
    interval = to_intervals(">=1.0,<2.0")[0]
    assert str(interval) == "[1.0, 2.0)"


# --- change classification ----------------------------------------------


def test_a_major_bump_may_break_but_a_patch_does_not():
    assert classify("1.2.3", "2.0.0") is Change.MAJOR
    assert classify("1.2.3", "1.3.0") is Change.MINOR
    assert classify("1.2.3", "1.2.4") is Change.PATCH
    assert Change.MAJOR.may_break
    assert not Change.PATCH.may_break


def test_a_minor_bump_below_one_point_zero_counts_as_major():
    """Semver rule 4: pre-1.0 is where breaking changes live."""
    assert classify("0.1.0", "0.2.0") is Change.MAJOR
    assert classify("0.1.0", "0.1.1") is Change.PATCH


def test_a_version_we_cannot_parse_is_unknown_and_therefore_risky():
    assert classify("1.0.0", "latest") is Change.UNKNOWN
    assert Change.UNKNOWN.may_break


def test_upgrades_are_offered_with_who_each_one_breaks_named():
    steps = safe_upgrades(
        "pkg:pypi/urllib3", "1.26.18", ["1.26.19", "2.2.1"],
        [Requirement("pkg:pypi/urllib3", "<2", requested_by="pkg:pypi/requests")],
    )

    assert [step.target for step in steps] == ["1.26.19", "2.2.1"], "safest first"
    assert steps[0].safe
    assert not steps[1].safe
    assert steps[1].breaks == ("pkg:pypi/requests",)


def test_a_downgrade_is_never_offered_as_an_upgrade():
    steps = safe_upgrades("pkg:pypi/x", "2.0.0", ["1.0.0", "2.0.0", "2.1.0"], [])
    assert [step.target for step in steps] == ["2.1.0"]
