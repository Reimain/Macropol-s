"""Applying — dispatch, refusal, and the failure that must stop the sequence.

Nothing here runs `terraform`. That is not a shortcut: applying infrastructure
in a test would need a cloud account, and a green tick for something that was
never applied is worse than an honest gap — the position phase 17 took on Tauri
and §25 takes on `INDETERMINATE`.

What *is* testable is everything around it, and it is the part that goes wrong:
which command runs, in what order, where, what happens when one fails, and what
the operator is told. A recording dispatcher makes all of that observable
without a cluster, which is the same seam Gratimos's guarded executor takes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from slpie.deploy.manifest import loads
from slpie.dispatch.tool import Outcome
from slpie_enterprise.deploy import TOOLS, DeploymentApplier

# No marker and no skip: this adapter imports nothing outside the standard
# library and `slpie`. Celery and FastAPI need their extras; dispatching a
# binary does not, and pretending otherwise would hide the tier from the
# default suite for no reason.

MANIFEST = """
apiVersion: slpie/v1
kind: Deployment
environment: acme
target: apply
topology:
  api: { replicas: 2, cpu: 1, memory: 1Gi }
  workers: { min: 1, max: 4 }
persistence:
  graph: { engine: postgres, size: 10Gi }
platform: compose
"""


@dataclass
class Recording:
    """A registry that records instead of executing.

    Deliberately not a mock library — the suite forbids one, and the reason
    shows here: this object is fifteen lines and it is *exactly* the seam the
    real registry exposes, so a change to that seam breaks this at compile time
    rather than leaving a mock agreeing with a signature nobody has any more.
    """

    calls: list[tuple[str, tuple[str, ...], str]] = field(default_factory=list)
    fail_at: int = -1
    names: tuple[str, ...] = ()
    #: Binaries this machine is pretending not to have. Injected rather than
    #: read from the host, so the assertions about command order hold on a
    #: laptop with terraform and on CI without it.
    absent: frozenset[str] = frozenset()

    def add(self, tool) -> None:
        self.names = (*self.names, tool.name)

    def available(self) -> dict[str, bool]:
        return {name: name not in self.absent for name in self.names}

    def run(self, name, arguments=(), **options) -> Outcome:
        self.calls.append((name, tuple(arguments), options.get("cwd", "")))
        failed = len(self.calls) - 1 == self.fail_at
        return Outcome(
            tool=name, argv=(name, *arguments),
            code=1 if failed else 0,
            stderr="Error: the cluster said no\n" if failed else "",
        )


@pytest.fixture()
def declared():
    return loads(MANIFEST)


def test_the_tools_are_declared_as_volatile():
    """Nothing built on a cluster's current state can claim reproducibility.

    §27 requires the determinism class to be declared rather than assumed, and
    `slpie audit --digest` reads it. A deploy tool marked deterministic would
    make a flight-recorder digest claim something it cannot.
    """
    assert {tool.name for tool in TOOLS} == {"docker", "kubectl", "helm", "terraform"}
    for tool in TOOLS:
        assert tool.determinism.value == "volatile", tool.name
        assert tool.fallback, f"{tool.name} does not say what to do without it"


def test_applying_renders_writes_and_dispatches(declared, tmp_path):
    recording = Recording()
    applier = DeploymentApplier(registry=recording, root=str(tmp_path))

    outcome = applier(declared, emitter="compose")

    assert outcome.applied
    assert outcome.by == "compose"
    assert recording.calls == [("docker", ("compose", "up", "-d"), str(tmp_path / "deploy"))]
    # The files an apply ran against are left on disk, deliberately: an operator
    # debugging a failed deploy needs to read what was actually applied.
    assert (tmp_path / "deploy" / "docker-compose.yaml").is_file()


def test_it_applies_what_it_just_rendered(declared, tmp_path):
    """The bytes on disk are the emitter's, not a second rendering."""
    from slpie.deploy import emitters

    applier = DeploymentApplier(registry=Recording(), root=str(tmp_path))
    applier(declared, emitter="compose")

    written = (tmp_path / "deploy" / "docker-compose.yaml").read_text(encoding="utf-8")
    assert written == emitters.render(declared, emitter="compose")["docker-compose.yaml"]


def test_terraform_runs_init_before_apply(declared, tmp_path):
    recording = Recording()
    DeploymentApplier(registry=recording, root=str(tmp_path))(declared, emitter="terraform")

    assert [call[1][0] for call in recording.calls] == ["init", "apply"]
    # Non-interactive, both of them. A deploy that blocked on a prompt in CI
    # would hang until the job timed out and report nothing useful.
    assert all("-input=false" in call[1] for call in recording.calls)


def test_a_failure_stops_the_sequence_and_names_what_did_not_run(declared, tmp_path):
    """`terraform apply` after a failed `init` compounds a broken state."""
    recording = Recording(fail_at=0)
    outcome = DeploymentApplier(registry=recording, root=str(tmp_path))(
        declared, emitter="terraform")

    assert not outcome.applied
    assert len(recording.calls) == 1, "it kept going after a failure"
    assert any("not run" in gap and "terraform" in gap for gap in outcome.gaps)


def test_a_failure_says_what_it_means_rather_than_only_that_it_failed(declared, tmp_path):
    """`init` failing and `apply` failing are not the same afternoon."""
    setup = DeploymentApplier(registry=Recording(fail_at=0), root=str(tmp_path))(
        declared, emitter="terraform")
    halfway = DeploymentApplier(registry=Recording(fail_at=1), root=str(tmp_path))(
        declared, emitter="terraform")

    assert "nothing was provisioned" in " ".join(setup.gaps)
    assert "partially created" in " ".join(halfway.gaps)


def test_the_tool_s_own_words_reach_the_operator(declared, tmp_path):
    outcome = DeploymentApplier(registry=Recording(fail_at=0), root=str(tmp_path))(
        declared, emitter="compose")
    assert any("the cluster said no" in gap for gap in outcome.gaps)


def test_an_emitter_with_no_apply_command_says_so(declared, tmp_path):
    """`pipelines` produces a file a CI system runs.

    An adapter that pushed a commit to make it run would be doing something the
    operator did not ask for.
    """
    outcome = DeploymentApplier(registry=Recording(), root=str(tmp_path))(
        declared, emitter="pipelines")
    assert not outcome.applied
    assert "committed and run by something else" in " ".join(outcome.gaps)


def test_a_missing_binary_leaves_the_rendered_files_and_says_where(declared, tmp_path):
    """The treatment §27 gives a missing binary, applied to a deploy.

    Not a crash, and not a silent success — and crucially the render still
    happened, so the operator has exactly what an apply would have run.
    """
    recording = Recording(absent=frozenset({"docker"}))
    outcome = DeploymentApplier(registry=recording, root=str(tmp_path))(
        declared, emitter="compose")

    assert not outcome.applied
    assert not recording.calls, "it dispatched a binary that is not installed"
    assert "docker is not installed" in " ".join(outcome.gaps)
    assert str(tmp_path / "deploy") in " ".join(outcome.gaps)
    assert (tmp_path / "deploy" / "docker-compose.yaml").is_file()


def test_a_successful_apply_still_reports_the_emitter_s_limits(declared, tmp_path):
    """Applied is not the same as complete.

    Compose has no autoscaler, so the declared worker range was rendered at its
    floor — and an operator who just deployed it should be told now rather than
    from a queue that never drains.
    """
    outcome = DeploymentApplier(registry=Recording(), root=str(tmp_path))(
        declared, emitter="compose")
    assert outcome.applied
    assert any("autoscaler" in gap for gap in outcome.gaps)


def test_the_applier_satisfies_the_ring_zero_seam(declared, tmp_path):
    """It is what `deploy-apply` looks for, and returns what that verb renders.

    Asserted through the ring-0 entry point rather than by calling the adapter
    directly, because the seam is the thing being claimed.
    """
    from slpie.deploy._apply import apply_through

    class Engine:
        deployment_applier = DeploymentApplier(registry=Recording(), root=str(tmp_path))

    class Ctx:
        engine = Engine()
        confirmed = True

    outcome = apply_through(declared, Ctx(), emitter="compose")
    assert outcome.applied
    assert outcome.to_dict()["summary"].startswith("applied acme")
