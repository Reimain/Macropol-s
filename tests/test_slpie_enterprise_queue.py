"""Celery as a `TaskRunner`, and the honest limit of that seam.

Two properties matter and neither is about Celery. The protocol says results
come back **in submission order**, because discovery merges by subject and a
runner that returned them as they finished would make the graph depend on which
worker was quickest — and the snapshot digest §12 promises is a function of the
inputs would stop being one.

And a unit that cannot leave the process is **counted, not hidden**. An adapter
that quietly ran everything locally would be an `InlineRunner` wearing Celery's
name, and an operator adding workers would never learn why nothing changed.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip(
    "celery", reason="the queue adapter needs `pip install -e '.[enterprise]'`",
)

from slpie.core.tasks import InlineRunner  # noqa: E402
from slpie_enterprise.queue import CeleryRunner, application  # noqa: E402


@pytest.fixture
def runner():
    return CeleryRunner(application(eager=True))


def _units():
    """Units that finish out of order on purpose."""
    def slow():
        time.sleep(0.03)
        return "slow"

    def quick():
        return "quick"

    def broken():
        raise ValueError("deliberate")

    return [("slow", slow), ("quick", quick), ("broken", broken)]


def test_results_come_back_in_submission_order(runner):
    """Completion order is the convenient one to read and the wrong one."""
    answers = runner.run(_units())
    assert [answer.unit for answer in answers] == ["slow", "quick", "broken"]


def test_it_answers_exactly_as_the_inline_runner_does(runner):
    """Same protocol, same answers. A runner that changed the values as well as
    the placement would not be an implementation of the seam, it would be a
    second execution model."""
    units = _units()
    here = InlineRunner().run(units)
    there = runner.run(units)

    assert [r.unit for r in here] == [r.unit for r in there]
    assert [r.value for r in here] == [r.value for r in there]
    assert [r.ok for r in here] == [r.ok for r in there]


def test_a_failed_unit_is_a_result_rather_than_a_raise(runner):
    """A scan where one plugin died is a scan with a gap, and the other
    ninety-nine results are still worth having."""
    answers = runner.run(_units())
    failed = [answer for answer in answers if not answer.ok]
    assert len(failed) == 1
    assert "ValueError: deliberate" in failed[0].error
    assert [a.value for a in answers if a.ok] == ["slow", "quick"]


def test_nothing_to_do_is_not_an_error(runner):
    assert runner.run([]) == ()


def test_a_plain_callable_is_run_here_and_counted(runner):
    """The honest limit, and the whole reason `gaps()` exists.

    A closure cannot be serialised, and the serializers that come close turn
    any queue an attacker can write to into remote code execution. So the unit
    runs correctly, in this process, and the runner says how many did.
    """
    runner.run(_units())

    assert runner.local == 3
    assert runner.distributed == 0
    gaps = runner.gaps()
    assert len(gaps) == 1
    assert "closure cannot cross a process boundary" in gaps[0]
    assert "3 unit(s)" in gaps[0]


def test_a_runner_that_distributed_everything_reports_nothing(runner):
    """A runner reporting no gaps and a runner that distributed nothing must
    not look the same from outside."""
    assert CeleryRunner(application(eager=True)).gaps() == ()
    assert runner.to_dict()["gaps"] == []
    runner.run(_units())
    assert runner.to_dict()["gaps"], "the gap disappeared from the report"


def test_a_registered_task_is_recognised_as_dispatchable(runner):
    """The other branch, so the local path is not the only one that exists."""
    from slpie_enterprise.queue.celery_runner import dispatchable

    app = runner.app

    @app.task(name="tests.unit")
    def work():
        return "distributed"

    assert dispatchable(work) is True
    assert dispatchable(lambda: 1) is False

    answers = runner.run([("remote", work)])
    assert answers[0].value == "distributed"
    assert runner.distributed == 1
    assert runner.local == 0
    assert runner.gaps() == ()


def test_the_serializer_is_json_and_never_pickle(runner):
    """`pickle` would let this accept a closure and would make any queue an
    attacker can write to a remote-code-execution surface."""
    assert runner.app.conf.task_serializer == "json"
    assert runner.app.conf.accept_content == ["json"]
    assert "pickle" not in runner.app.conf.accept_content
