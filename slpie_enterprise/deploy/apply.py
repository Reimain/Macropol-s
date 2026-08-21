"""Render, write, dispatch — and report exactly what happened.

The shape is the same for all six platforms because the difference between them
is one argv. What is *not* uniform is what a failure means, and that is the
reason each command carries its own `Step` rather than being run in a loop over
a list of strings: `terraform init` failing is a setup problem an operator fixes
in a minute, and `terraform apply` failing halfway is infrastructure in a state
nobody described. Reporting both as "command 2 failed" would flatten the one
distinction that matters at three in the morning.

── Stopping at the first failure is the whole design ────────────────────

`helm upgrade` after a failed `kubectl apply` compounds a broken state. So the
sequence stops, reports which step failed and what it printed, and leaves the
rest unrun *and named* — an operator needs to know what did not happen as much
as what did.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from slpie.deploy import emitters
from slpie.deploy._apply import Applied
from slpie.deploy._render import DEFAULT_OUTPUT, write
from slpie.deploy.manifest import Deployment
from slpie.dispatch.tool import Determinism, Tool

#: The binaries this adapter can dispatch to. Declared as `Tool`s so they are
#: probed, version-recorded and reported through the same machinery `git` and
#: `rg` already use — including the part where absence is a gap rather than a
#: traceback.
#:
#: `VOLATILE` for every one of them, and it is not pedantry: their output
#: depends on the state of a cluster at a moment, so nothing built on them can
#: claim reproducibility. §27 requires that to be declared rather than assumed,
#: and `slpie audit --digest` reads it.
TOOLS: tuple[Tool, ...] = (
    Tool(
        name="docker", argv=("docker",),
        summary="compose, for a single host",
        determinism=Determinism.VOLATILE,
        version_argv=("docker", "--version"),
        fallback="render the compose file and run it yourself",
        timeout=900.0,
    ),
    Tool(
        name="kubectl", argv=("kubectl",),
        summary="apply raw manifests to a cluster",
        determinism=Determinism.VOLATILE,
        version_argv=("kubectl", "version", "--client"),
        fallback="render the manifests and apply them yourself",
        timeout=900.0,
    ),
    Tool(
        name="helm", argv=("helm",),
        summary="install or upgrade the chart",
        determinism=Determinism.VOLATILE,
        version_argv=("helm", "version", "--short"),
        fallback="render the chart and install it yourself",
        timeout=900.0,
    ),
    Tool(
        name="terraform", argv=("terraform",),
        summary="provision the infrastructure under the workloads",
        determinism=Determinism.VOLATILE,
        version_argv=("terraform", "version"),
        fallback="render the HCL and apply it yourself",
        timeout=1800.0,
    ),
)


@dataclass(frozen=True, slots=True)
class Step:
    """One command, and what it means if it fails."""

    tool: str
    arguments: tuple[str, ...]
    #: Said to the operator when this step fails. Per step, because the
    #: consequences genuinely differ.
    on_failure: str


#: What each emitter's output is applied with. `pipelines` is absent on purpose:
#: its artifact is committed and run by a CI system, and an adapter that pushed
#: a commit would be doing something the operator did not ask for.
SEQUENCES: dict[str, tuple[Step, ...]] = {
    "compose": (
        Step("docker", ("compose", "up", "-d"),
             "nothing was started, or only some services were; "
             "`docker compose ps` says which"),
    ),
    "kubernetes": (
        Step("kubectl", ("apply", "-f", "."),
             "some objects may have been created before the failure; "
             "`kubectl get all` says which"),
    ),
    "helm": (
        Step("helm", ("upgrade", "--install", "slpie", ".", "--wait"),
             "helm rolls back a failed upgrade of an existing release, but a "
             "failed *first* install leaves the release in a failed state; "
             "`helm status slpie` says which happened"),
    ),
    "terraform": (
        Step("terraform", ("init", "-input=false"),
             "nothing was provisioned; this is a setup failure, not an "
             "infrastructure one"),
        Step("terraform", ("apply", "-input=false", "-auto-approve"),
             "**infrastructure may be partially created.** Run "
             "`terraform plan` before doing anything else"),
    ),
}


@dataclass
class DeploymentApplier:
    """The ring-1 applier. Installed on an engine; called by `deploy-apply`.

    Holds a `ToolRegistry` rather than reaching for one, so a test can install a
    dispatcher that records instead of executing — which is how this module is
    exercised without a cluster, and the only honest way to test it here.
    """

    registry: Any = None
    #: Where rendered files are written before being applied. The same default
    #: `deploy render --write` uses: an operator who ran `render` and then
    #: `apply` should be applying the files they just read.
    output: str = DEFAULT_OUTPUT
    root: str = "."

    def __post_init__(self) -> None:
        if self.registry is None:
            from slpie.dispatch.registry import ToolRegistry

            self.registry = ToolRegistry()
        for tool in TOOLS:
            self.registry.add(tool)

    def _installed(self, name: str) -> bool:
        """Whether the binary is here, asked *through the registry*.

        Not `shutil.which` directly, and the reason is a test that was quietly
        machine-dependent: on a host without terraform the applier short-circuited
        before dispatching, so the assertions about command *order* passed or
        failed according to what happened to be installed. Routing the question
        through the same seam that runs the command makes both observable
        together, which is what a seam is for.
        """
        report = getattr(self.registry, "available", None)
        if report is None:  # pragma: no cover - every registry has one
            return bool(shutil.which(name))
        return bool(report().get(name, False))

    def __call__(self, declared: Deployment, *, emitter: str = "") -> Applied:
        chosen = emitter or emitters.default_for(declared)
        sequence = SEQUENCES.get(chosen)
        if sequence is None:
            return Applied(
                environment=declared.environment,
                gaps=(
                    f"the {chosen} emitter has no apply command: its artifact is "
                    f"committed and run by something else. Render it and commit it.",
                ),
            )

        destination = Path(self.root) / self.output
        files = emitters.render(declared, emitter=chosen)
        write(files, destination)

        steps: list[str] = []
        for index, step in enumerate(sequence):
            if not self._installed(step.tool):
                # A missing binary is a capability gap naming the tool, and the
                # rendered files are still on disk — so the operator has
                # everything they need to do it by hand. §27's treatment, and
                # §3's, applied to a deploy.
                return Applied(
                    environment=declared.environment, by=chosen, steps=tuple(steps),
                    gaps=(
                        f"{step.tool} is not installed, so nothing was applied. "
                        f"The rendered files are in {destination} — apply them "
                        f"yourself, or install {step.tool}.",
                    ),
                )

            outcome = self.registry.run(
                step.tool, step.arguments, cwd=str(destination),
            )
            steps.append(f"{step.tool} {' '.join(step.arguments)} → exit {outcome.code}")
            if not outcome.ok:
                remaining = [item.tool for item in sequence[index + 1:]]
                gaps = [
                    f"`{step.tool} {' '.join(step.arguments)}` failed "
                    f"(exit {outcome.code}). {step.on_failure}.",
                ]
                if outcome.stderr.strip():
                    gaps.append(f"it said: {outcome.stderr.strip().splitlines()[-1]}")
                if remaining:
                    gaps.append(
                        f"not run, because the sequence stops at the first "
                        f"failure: {', '.join(remaining)}"
                    )
                return Applied(
                    environment=declared.environment, by=chosen,
                    steps=tuple(steps), gaps=tuple(gaps),
                )

        return Applied(
            environment=declared.environment, applied=True, by=chosen,
            steps=tuple(steps),
            gaps=tuple(emitters.gaps(declared, emitter=chosen)),
        )
