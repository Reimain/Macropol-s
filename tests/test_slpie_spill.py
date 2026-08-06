"""The spill tier — bounded memory, isolated sessions, and no silent loss.

Four claims, and each one is a way this goes wrong in production rather than a
property that is merely nice to have:

**Memory is bounded and the bound is flat.** A scan of four hundred thousand
records must not cost four hundred thousand records' worth of RAM. Asserted by
measuring, not by inspecting the code.

**Spilling changes cost, never truth.** The same composition run under a generous
ceiling and under a tight one produces the same digest. This is the claim that
would be worthless if it were only *usually* true — and it was not true at first:
`Flow.items` type-checked for `tuple`/`list`, so a spilled sequence arrived
downstream as a one-element flow and every answer quietly changed.

**Sessions cannot reach each other.** Identical content in two sessions produces
different block ids, and neither can read or sweep the other's blocks.

**It is lossless or it refuses.** An out-of-memory kill is loud; a lossy spill
produces a complete-looking answer built on records that lost a field three
stages back. `Observation.to_dict()` is exactly that trap and is not used.
"""

from __future__ import annotations

import gc
import json
import threading
import tracemalloc
from pathlib import Path

import pytest

from slpie.compose import Composition, Context, registry
from slpie.domain.evidence import Evidence, EvidenceKind, SourceLocation
from slpie.errors import SpillError
from slpie.plugins.protocol import Observation
from slpie.spill import (
    LENGTH,
    BlockRef,
    Budget,
    FileStore,
    SpilledSequence,
    SpillError,
    SpillSession,
    Unspillable,
    block_id,
    decode,
    encode,
    is_block_id,
    new_session_key,
    require_block_id,
    require_session,
    reset_shared_budget,
    shared_budget,
    spillable,
    sweep_stale,
    write_block,
)


@pytest.fixture()
def store(tmp_path):
    return FileStore(tmp_path / "spill")


def evidence(i: int = 1) -> Evidence:
    return Evidence(
        kind=EvidenceKind.LOCKFILE_PIN,
        location=SourceLocation(f"file:///r/w{i % 7}/package-lock.json", line=i),
        extractor="npm", extractor_version="2",
        excerpt=f'"version": "1.0.{i}"',
        content_digest=f"{i:032x}",
        labels={"hash_alg": "sha512", "source": "lockfile"},
    )


def observation(i: int) -> Observation:
    return Observation(
        kind="declares", subject=f"pkg:npm/p{i}@1.0.{i}",
        object=f"pkg:npm/dep{i}@2.0.0", qualifier="dev",
        evidence=evidence(i), properties={"range": "^1.0.0", "ecosystem": "npm"},
    )


# --- lossless, or refused ---------------------------------------------------


def test_an_observation_round_trips_exactly():
    """Every field, not the ones somebody remembered to check."""
    original = observation(42)

    assert decode(encode(original)) == original


def test_the_fields_the_plugin_wire_format_drops_survive_a_spill():
    """`Observation.to_dict()` loses these, which is why the codec avoids it.

    `labels` carries the hash algorithm an SBOM checksum needs. Spilling through
    the plugin format would have produced SBOMs missing exactly the hashes the
    tree supplied, on precisely the large scans that spill.
    """
    original = observation(7)
    lossy = Observation.from_dict(original.to_dict(), plugin_id="npm")
    assert lossy.evidence.labels == {}, "the plugin format is lossy, as designed"
    assert lossy.evidence.content_digest == ""

    restored = decode(encode(original))
    assert restored.evidence.labels == {"hash_alg": "sha512", "source": "lockfile"}
    assert restored.evidence.content_digest == f"{7:032x}"


@pytest.mark.parametrize(
    "value", [None, True, 3, 2.5, "text", [1, 2], {"a": 1}, {"nested": {"b": [1]}}],
)
def test_json_native_values_round_trip(value):
    assert decode(encode(value)) == value


def test_a_value_with_no_lossless_encoding_is_refused_by_name():
    class Custom:
        pass

    with pytest.raises(Unspillable, match="Custom"):
        encode(Custom())
    assert not spillable(Custom())
    assert spillable(observation(1))


def test_a_corrupt_block_raises_rather_than_returning_what_is_left():
    """A short answer that looks complete is the failure this tier prevents."""
    with pytest.raises(SpillError, match="not JSON"):
        decode("{not json")


def test_an_unknown_record_tag_is_not_guessed_at():
    with pytest.raises(SpillError, match="unknown type tag"):
        decode(json.dumps({"t": "martian", "v": 1}))


# --- the id ------------------------------------------------------------------


def test_a_block_id_is_always_the_same_fixed_length():
    key = new_session_key()
    for size in (0, 1, 1000, 100_000):
        identifier = block_id(b"x" * size, key=key)
        assert len(identifier) == LENGTH
        assert is_block_id(identifier)


def test_the_same_content_gives_the_same_id_within_a_session():
    """Content-addressed, so a repeated block is written once."""
    key = new_session_key()

    assert block_id(b"payload", key=key) == block_id(b"payload", key=key)
    assert block_id(b"payload", key=key) != block_id(b"other", key=key)


def test_the_same_content_gives_different_ids_in_different_sessions():
    """The isolation property, at its root.

    One session cannot derive another's ids from content they both hold, so it
    cannot fish for another tenant's blocks by guessing.
    """
    content = b"identical bytes in both sessions"

    assert block_id(content, key=new_session_key()) != block_id(
        content, key=new_session_key()
    )


@pytest.mark.parametrize(
    "bad", ["", "abc", "../../etc/passwd", "A" * LENGTH, "g" * LENGTH,
            "a" * (LENGTH - 1), "a" * (LENGTH + 1), "../" + "a" * (LENGTH - 3)],
)
def test_anything_that_is_not_an_id_is_refused_rather_than_normalised(bad):
    """Paths are built from these; `root/session/../../etc` is not a block."""
    assert not is_block_id(bad)
    with pytest.raises(SpillError, match="not a block id"):
        require_block_id(bad)


def test_an_oversized_session_key_is_refused_at_the_boundary():
    with pytest.raises(SpillError, match="at most"):
        block_id(b"x", key=b"k" * 65)


@pytest.mark.parametrize("bad", ["", "..", "a/b", "x" * 65, "-leading", "with space"])
def test_a_session_name_that_could_escape_its_directory_is_refused(bad):
    with pytest.raises(SpillError, match="not a usable session name"):
        require_session(bad)


# --- the store ---------------------------------------------------------------


def test_a_block_is_written_once_and_read_back_whole(store):
    key = new_session_key()
    content = b"line one\nline two\n"
    identifier = block_id(content, key=key)

    assert store.put("alpha", identifier, content) == len(content)
    assert store.put("alpha", identifier, content) == 0, "content-addressed"
    assert store.get("alpha", identifier) == content


def test_a_partial_write_never_becomes_a_readable_block(store, monkeypatch):
    """Atomic rename, not write-in-place: a reader sees all of a block or none."""
    import os

    key = new_session_key()
    content = b"x" * 4096
    identifier = block_id(content, key=key)

    def explode(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", explode)
    with pytest.raises(OSError):
        store.put("alpha", identifier, content)
    monkeypatch.undo()

    assert store.blocks("alpha") == (), "no half-written block is visible"
    assert not list((store.root / "alpha").glob(".staging-*")), "and none is left behind"


def test_a_missing_block_raises_rather_than_reading_as_empty(store):
    identifier = block_id(b"never stored", key=new_session_key())

    with pytest.raises(SpillError, match="unreadable"):
        store.get("alpha", identifier)


def test_sweeping_one_session_never_touches_another(store):
    key = new_session_key()
    mine = block_id(b"mine", key=key)
    yours = block_id(b"yours", key=key)
    store.put("alpha", mine, b"mine")
    store.put("beta", yours, b"yours")

    store.sweep("alpha")

    assert store.blocks("alpha") == ()
    assert store.blocks("beta") == (yours,)


def test_the_store_reports_what_it_is_holding(store):
    key = new_session_key()
    store.put("alpha", block_id(b"a", key=key), b"a")
    store.put("beta", block_id(b"bb", key=key), b"bb")
    report = store.report()

    assert report.sessions == 2 and report.blocks == 2 and report.bytes == 3
    assert "block(s)" in str(report)
    assert report.to_dict()["blocks"] == 2


def test_sweeping_a_session_that_was_never_used_is_not_an_error(store):
    assert store.sweep("neverused") == 0


# --- the budget --------------------------------------------------------------


def test_a_budget_refuses_what_will_not_fit():
    budget = Budget(1000)

    assert budget.admit("a", 100)
    assert not budget.admit("a", 10_000)
    assert budget.report().refused == 1


def test_a_reserve_is_kept_so_that_spilling_is_always_possible():
    """A budget run to exactly zero cannot spill; spilling needs a buffer."""
    budget = Budget(1000)

    assert not budget.admit("a", 1000), "the last byte is reserved"
    assert budget.admit("a", 800)


def test_releasing_gives_the_memory_back():
    budget = Budget(10_000)
    budget.admit("a", 5_000)
    budget.release("a", 5_000)

    assert budget.used == 0
    assert budget.admit("a", 5_000)


def test_releasing_without_a_size_releases_everything_that_holder_has():
    budget = Budget(10_000)
    budget.admit("a", 1_000)
    budget.admit("a", 2_000)
    budget.admit("b", 3_000)

    assert budget.release("a") == 3_000
    assert budget.used == 3_000, "b keeps its own"


def test_a_holder_cannot_release_more_than_it_holds():
    budget = Budget(10_000)
    budget.admit("a", 100)

    assert budget.release("a", 9_999) == 100
    assert budget.used == 0


def test_a_ceiling_of_zero_is_refused_rather_than_silently_disabling_the_tier():
    with pytest.raises(SpillError, match="must be positive"):
        Budget(0)


def test_a_negative_admission_is_refused():
    with pytest.raises(SpillError, match="negative"):
        Budget(100).admit("a", -1)


def test_the_estimate_is_measured_rather_than_assumed():
    """A record carrying a long excerpt costs more, and the estimate knows it."""
    small = [{"a": 1} for _ in range(10)]
    large = [{"a": "x" * 500} for _ in range(10)]

    assert Budget.estimate(large) > Budget.estimate(small) * 5
    assert Budget.estimate([]) == 0


def test_an_unspillable_record_still_counts_against_the_estimate():
    class Custom:
        pass

    assert Budget.estimate([Custom()]) > 0


def test_the_budget_reports_pressure_and_serialises():
    budget = Budget(1000)
    budget.admit("a", 500)
    report = budget.report()

    assert 0 < report.pressure <= 1
    assert report.free == 500
    assert report.to_dict()["used"] == 500
    assert "MB" in str(report)
    assert not Budget(1000).under_pressure


# --- the sequence ------------------------------------------------------------


@pytest.fixture()
def spilled(store):
    """Twelve hundred observations on disk, in several blocks."""
    return SpilledSequence.of(
        (observation(i) for i in range(1200)),
        store=store, session="alpha", key=new_session_key(), chunk=250,
    )


def test_a_spilled_sequence_is_exactly_what_went_in(spilled):
    assert len(spilled) == 1200
    assert list(spilled) == [observation(i) for i in range(1200)]


def test_it_can_be_iterated_more_than_once(spilled):
    """A verb may walk its input twice; a generator would yield nothing again.

    `_dependencies` in the SBOM emitter does exactly this, and would have
    produced a document with no dependencies and no error.
    """
    assert list(spilled) == list(spilled)


def test_length_is_exact_without_reading_the_blocks(spilled):
    """`if not flow.empty` is everywhere; it must not cost a full pass."""
    assert len(spilled) == 1200
    assert spilled.resident == 0, "nothing was loaded to answer len()"


def test_indexing_reaches_any_record(spilled):
    assert spilled[0] == observation(0)
    assert spilled[700] == observation(700)
    assert spilled[-1] == observation(1199)


def test_slicing_returns_the_records_asked_for(spilled):
    assert spilled[10:13] == [observation(i) for i in (10, 11, 12)]
    assert spilled[:3] == [observation(i) for i in range(3)]


def test_an_index_outside_the_sequence_says_so(spilled):
    with pytest.raises(IndexError, match="out of range"):
        spilled[1200]
    with pytest.raises(IndexError):
        spilled[-1201]


def test_only_a_window_is_ever_resident(spilled):
    """The bound, at the level of one sequence."""
    spilled[600]

    assert 0 < spilled.resident <= 250, "one block, not twelve hundred records"


def test_the_window_can_be_released_without_losing_the_blocks(spilled):
    spilled[600]
    spilled.release()

    assert spilled.resident == 0
    assert len(list(spilled)) == 1200, "the records are still there"


def test_an_empty_sequence_is_a_valid_sequence(store):
    empty = SpilledSequence.of(
        iter(()), store=store, session="alpha", key=new_session_key(),
    )

    assert len(empty) == 0 and list(empty) == []
    assert empty.bytes == 0


def test_a_block_reference_describes_itself(store):
    reference = write_block(
        store, "alpha", [observation(1)], key=new_session_key(),
    )

    assert isinstance(reference, BlockRef)
    assert reference.count == 1 and reference.bytes > 0
    assert reference.to_dict()["session"] == "alpha"
    assert "record" in str(reference)


def test_a_sequence_serialises_for_a_status_view(spilled):
    body = spilled.to_dict()

    assert body["spilled"] is True
    assert body["records"] == 1200 and body["blocks"] > 1


# --- the session -------------------------------------------------------------


def test_a_small_input_on_an_idle_worker_never_touches_the_disk(store):
    """The fast path must stay the common one, or this tier is a tax."""
    with SpillSession(store=store, budget=Budget(64 * 1024 * 1024)) as session:
        kept = session.keep(observation(i) for i in range(500))

        assert isinstance(kept, tuple)
        assert session.report().blocks == 0
        assert store.blocks(session.name) == ()


def test_the_same_input_under_pressure_spills_and_stays_correct(store):
    with SpillSession(store=store, budget=Budget(8 * 1024), chunk=100) as session:
        kept = session.keep(observation(i) for i in range(500))

        assert isinstance(kept, SpilledSequence)
        assert list(kept) == [observation(i) for i in range(500)]


def test_closing_a_session_reclaims_its_disk(store):
    session = SpillSession(store=store, budget=Budget(8 * 1024), chunk=100)
    session.keep(observation(i) for i in range(500))
    assert store.blocks(session.name)

    session.close()

    assert store.blocks(session.name) == ()
    assert session.closed


def test_closing_twice_is_not_an_error(store):
    session = SpillSession(store=store, budget=Budget(1024))
    session.close()

    assert session.close() == 0


def test_a_closed_session_refuses_to_be_reused(store):
    session = SpillSession(store=store, budget=Budget(1024))
    session.close()

    with pytest.raises(SpillError, match="closed"):
        session.keep([observation(1)])


def test_a_session_releases_its_budget_when_it_closes(store):
    budget = Budget(64 * 1024 * 1024)
    session = SpillSession(store=store, budget=budget)
    session.keep(observation(i) for i in range(200))
    assert budget.used > 0

    session.close()

    assert budget.used == 0


def test_a_value_that_cannot_be_spilled_stays_in_memory_and_is_reported(store):
    """Bounded-but-wrong is not an option; unbounded-and-said-so is."""
    class Custom:
        pass

    with SpillSession(store=store, budget=Budget(8 * 1024), chunk=10) as session:
        kept = session.keep([Custom() for _ in range(200)])

        assert isinstance(kept, tuple), "kept in memory"
        assert len(kept) == 200, "and nothing was dropped on the way"
        assert session.refusals, "and the caller is told it is not bounded"


def test_hold_over_a_small_input_is_an_ordinary_tuple(store):
    """The fast path goes through `hold` too, and has no window to release."""
    with SpillSession(store=store, budget=Budget(64 * 1024 * 1024)) as session:
        with session.hold(observation(i) for i in range(50)) as kept:
            assert isinstance(kept, tuple)
            assert len(kept) == 50


def test_hold_releases_the_window_on_the_way_out(store):
    with SpillSession(store=store, budget=Budget(8 * 1024), chunk=100) as session:
        with session.hold(observation(i) for i in range(400)) as kept:
            kept[10]
            assert kept.resident > 0
        assert kept.resident == 0


def test_a_session_report_serialises(store):
    with SpillSession(store=store, budget=Budget(8 * 1024), chunk=50) as session:
        session.keep(observation(i) for i in range(200))
        report = session.report()

        assert report.spilled_records == 200
        assert report.to_dict()["blocks"] > 0
        assert "record(s) spilled" in str(report)


# --- concurrency -------------------------------------------------------------


def test_many_concurrent_sessions_never_see_each_others_records(store):
    """The headline concurrency claim, under a ceiling that forces every spill."""
    budget = Budget(32 * 1024)
    users, count = 12, 800
    results: dict[int, list[str]] = {}
    failures: list[BaseException] = []

    def work(user: int) -> None:
        try:
            with SpillSession(store=store, budget=budget, chunk=64) as session:
                kept = session.keep(
                    Observation(
                        kind="declares", subject=f"pkg:npm/u{user}-p{i}@1.0.0",
                        evidence=evidence(i), properties={},
                    )
                    for i in range(count)
                )
                results[user] = [item.subject for item in kept]
        except BaseException as error:  # noqa: BLE001 - reported, not swallowed
            failures.append(error)

    threads = [threading.Thread(target=work, args=(user,)) for user in range(users)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not failures, failures[:1]
    assert len(results) == users
    for user, subjects in results.items():
        assert subjects == [f"pkg:npm/u{user}-p{i}@1.0.0" for i in range(count)], (
            f"session {user} read another session's records"
        )


def test_concurrent_sessions_return_the_budget_they_borrowed(store):
    budget = Budget(4 * 1024 * 1024)

    def work() -> None:
        with SpillSession(store=store, budget=budget, chunk=64) as session:
            session.keep(observation(i) for i in range(300))

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert budget.used == 0, "a leaked claim would starve the next request"


def test_every_session_cleans_up_after_itself(store):
    def work() -> None:
        with SpillSession(store=store, budget=Budget(8 * 1024), chunk=32) as session:
            session.keep(observation(i) for i in range(200))

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert store.sessions() == ()


def test_two_sessions_reading_one_sequence_do_not_corrupt_each_others_window(store):
    """A window is shared state; without a lock two readers see a half-load."""
    sequence = SpilledSequence.of(
        (observation(i) for i in range(2000)),
        store=store, session="alpha", key=new_session_key(), chunk=100,
    )
    seen: list[bool] = []

    def read(start: int) -> None:
        seen.append(all(
            sequence[index] == observation(index)
            for index in range(start, start + 400)
        ))

    threads = [threading.Thread(target=read, args=(n,)) for n in (0, 500, 1000, 1500)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert all(seen) and len(seen) == 4


# --- housekeeping ------------------------------------------------------------


def test_a_directory_left_by_a_dead_process_is_reclaimed(store, tmp_path):
    """A hard kill cannot run cleanup, so something else has to."""
    import os
    import time

    key = new_session_key()
    store.put("orphan", block_id(b"left behind", key=key), b"left behind")
    stale = store.root / "orphan"
    old = time.time() - 99_999
    os.utime(stale, (old, old))

    assert sweep_stale(store, older_than=3600) > 0
    assert "orphan" not in store.sessions()


def test_an_active_session_is_never_swept_from_under_itself(store):
    key = new_session_key()
    store.put("busy", block_id(b"in use", key=key), b"in use")

    sweep_stale(store, older_than=3600)

    assert "busy" in store.sessions()


def test_the_shared_budget_is_one_budget_for_the_whole_process():
    """Per-session ceilings are not ceilings: twenty sessions is twenty times it."""
    first = reset_shared_budget(4096)

    assert shared_budget() is first
    assert shared_budget() is shared_budget()


def test_the_ceiling_can_be_set_by_the_environment(monkeypatch):
    """A container limit or a systemd unit has no manifest to read."""
    monkeypatch.setenv("SLPIE_MEMORY_CEILING", str(7 * 1024 * 1024))
    assert reset_shared_budget().ceiling == 7 * 1024 * 1024

    monkeypatch.setenv("SLPIE_MEMORY_CEILING", "not a number")
    assert reset_shared_budget().ceiling > 0, "a bad value falls back, never crashes"

    monkeypatch.setenv("SLPIE_MEMORY_CEILING", "-5")
    assert reset_shared_budget().ceiling > 0


# --- the paths that only run when something goes wrong ----------------------


def test_free_reports_what_is_actually_available():
    """`free` subtracts the reserve, so it never promises the unspillable last byte."""
    budget = Budget(1000)
    budget.admit("a", 400)

    assert budget.free == 1000 - 400 - budget.reserve


def test_a_partial_release_leaves_the_rest_held():
    budget = Budget(10_000)
    budget.admit("a", 1_000)

    assert budget.release("a", 400) == 400
    assert budget.used == 600
    assert budget.release("a") == 600


def test_a_json_shaped_value_that_will_not_serialise_is_refused():
    """`isinstance(value, dict)` is not the same as `json.dumps` succeeding."""
    with pytest.raises(Unspillable, match="dict"):
        encode({"a": {1, 2}})


def test_encoding_many_values_is_lazy():
    """A million records must not become a million strings held at once."""
    from slpie.spill.codec import encode_all

    lines = encode_all(observation(i) for i in range(5))

    assert not isinstance(lines, list)
    assert [decode(line) for line in lines] == [observation(i) for i in range(5)]


def test_memory_already_claimed_is_returned_when_a_later_batch_spills(store):
    """The refusal arrives mid-stream, and the earlier claim must not leak.

    A budget generous enough for the first chunk and not the tenth is the
    ordinary case under load, and a claim left behind there would starve the
    next request by exactly the amount that was already going to disk anyway.
    """
    budget = Budget(150 * 1024)
    with SpillSession(store=store, budget=budget, chunk=50) as session:
        kept = session.keep(observation(i) for i in range(4000))

        assert isinstance(kept, SpilledSequence), "it spilled"
        assert budget.used == 0, "and gave back what it had claimed on the way"
        assert list(kept) == [observation(i) for i in range(4000)]


def test_opening_a_block_that_is_not_there_says_so(store):
    with pytest.raises(SpillError, match="cannot be opened"):
        store.open("alpha", block_id(b"absent", key=new_session_key()))


def test_dropping_one_block_leaves_the_rest(store):
    key = new_session_key()
    kept = block_id(b"kept", key=key)
    gone = block_id(b"gone", key=key)
    store.put("alpha", kept, b"kept")
    store.put("alpha", gone, b"gone")

    assert store.drop("alpha", gone) is True
    assert store.drop("alpha", gone) is False, "dropping twice is not an error"
    assert store.blocks("alpha") == (kept,)


def test_a_blank_line_in_a_block_is_skipped_rather_than_decoded(store):
    """Trailing newlines are ordinary; decoding one would raise on empty input."""
    key = new_session_key()
    content = encode(observation(1)).encode("utf-8") + b"\n\n"
    identifier = block_id(content, key=key)
    store.put("alpha", identifier, content)
    sequence = SpilledSequence(
        store, [BlockRef("alpha", identifier, count=1, bytes=len(content))],
    )

    assert list(sequence) == [observation(1)]


def test_sweeping_a_store_with_no_root_is_a_no_op():
    """An adapter that keeps blocks elsewhere has nothing on a local disk."""
    class Remote:
        def sessions(self):
            return ("a",)

    assert sweep_stale(Remote()) == 0


def test_a_directory_that_vanishes_mid_sweep_is_not_an_error(store):
    """Two workers sweeping at once is the intended outcome, not a failure.

    Modelled by a store that lists a session whose directory is already gone —
    exactly what the loser of the race sees between listing and stat.
    """
    class Racing(FileStore):
        def sessions(self):
            return ("alreadygone",)

    racing = Racing(store.root)

    assert sweep_stale(racing, older_than=0) == 0


def test_the_shared_budget_is_created_on_first_use():
    import slpie.spill.session as module

    monkey = module._SHARED
    try:
        module._SHARED = None
        assert shared_budget(4096).ceiling == 4096
    finally:
        module._SHARED = monkey


# --- the property that matters most -----------------------------------------


@pytest.fixture()
def big_tree(tmp_path):
    """Two hundred workspaces — enough observations to force a spill."""
    for i in range(200):
        workspace = tmp_path / f"pkg{i}"
        workspace.mkdir()
        (workspace / "package-lock.json").write_text(json.dumps({
            "name": f"pkg{i}", "lockfileVersion": 3,
            "packages": {
                f"node_modules/dep{j}": {"version": f"1.{j}.0"} for j in range(30)
            },
        }), encoding="utf-8")
    return tmp_path


def test_spilling_changes_the_cost_and_never_the_answer(big_tree):
    """The claim this whole tier would be worthless without.

    It was not true when the tier was first wired in: `Flow.items` type-checked
    for `tuple`/`list`, so a spilled sequence arrived downstream wrapped in a
    one-element tuple. `link` resolved one object instead of twelve thousand and
    produced a different, entirely plausible answer with no error anywhere.
    """
    verbs = registry()
    pipeline = f"discover {big_tree} | link | findings"
    answers = []

    for ceiling in (256 * 1024 * 1024, 200 * 1024):
        reset_shared_budget(ceiling)
        with Context(root=str(big_tree)) as context:
            flow = Composition.read(pipeline, verbs=verbs).run(context).flow
            spilled = context.spill.report().spilled_records if context.spill else 0
            answers.append((flow.size, flow.digest, spilled))

    generous, tight = answers
    assert generous[2] == 0, "a generous ceiling does not spill"
    assert tight[2] > 0, "a tight one does"
    assert generous[:2] == tight[:2], "and the answer is identical either way"


def test_memory_stays_flat_as_the_record_count_grows(store):
    """Measured, not asserted from reading the code.

    The unbounded path is linear at roughly 500 bytes a record; a flat peak
    across a fourfold increase is the property, and a regression here would
    show up as a slope.
    """
    peaks = []
    for count in (20_000, 80_000):
        gc.collect()
        with SpillSession(store=store, budget=Budget(256 * 1024), chunk=2048) as session:
            kept = session.keep(observation(i) for i in range(count))
            tracemalloc.start()
            total = sum(1 for _ in kept)
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            assert total == count
            peaks.append(peak)
        gc.collect()

    assert peaks[1] < peaks[0] * 2, (
        f"a fourfold increase in records cost {peaks[1] / max(peaks[0], 1):.1f}x "
        f"the memory; the scan is not streaming"
    )


def test_a_spilled_flow_is_still_a_sequence_to_every_verb(big_tree):
    """`Flow.items` asks the protocol, not `isinstance(value, tuple)`."""
    reset_shared_budget(200 * 1024)
    verbs = registry()

    with Context(root=str(big_tree)) as context:
        flow = Composition.read(
            f"discover {big_tree}", verbs=verbs,
        ).run(context).flow

        assert flow.facts["spilled"] is True
        assert flow.size > 1000, "not wrapped into a one-element flow"
        assert len(flow.items) == flow.size
        assert not flow.empty


def test_a_composition_sweeps_its_blocks_when_the_context_closes(big_tree):
    reset_shared_budget(200 * 1024)
    verbs = registry()
    context = Context(root=str(big_tree))

    Composition.read(f"discover {big_tree}", verbs=verbs).run(context)
    session = context.spill
    assert session.store.blocks(session.name)

    context.close()

    assert session.store.blocks(session.name) == ()


@pytest.fixture(autouse=True)
def _restore_default_ceiling():
    """Every test leaves the process budget as it found it."""
    yield
    reset_shared_budget()
