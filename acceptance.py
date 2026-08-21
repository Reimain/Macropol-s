#!/usr/bin/env python3
"""The end-to-end acceptance run: drive the whole platform, then prove four things.

    python acceptance.py                # the full run
    python acceptance.py --baseline     # re-record the timings after a change
    python acceptance.py --fail-on high # exit 3 if the real tree has findings at
                                        # or above that severity

This is not the demo. `slpie demo` is an eleven-beat teaching run that narrates
what the platform does; this drives it the way a customer would — write a
manifest, materialise a world, attach, scan, reconcile, reason, ask, govern, emit
artifacts, audit, capture, rescan incrementally, hand an agent its tools — and
then reports whether four claims hold:

1. **Verb coverage.** Which of the registry's verbs the run actually executed,
   and a non-zero exit if any was never invoked. Adding a verb without covering
   it breaks this run, which is the only thing that reliably stops a capability
   from arriving with no exercise behind it.
2. **Scenario expectations.** Every scenario fires, and each one's declared
   `expect_findings` / `expect_gaps` is checked. Those expectations are already
   data on the `Outcome`, so this is an assertion rather than a narration.
3. **Correctness on a real tree.** Facts about this repository that are true
   independently of the platform, checked against what the platform says.
4. **Cost.** Wall time and peak RSS per stage. If a baseline has been recorded
   *on this machine* it is compared against, past a stated tolerance.

The baseline is deliberately **not committed**. A timing recorded on a laptop and
compared against a CI runner measures the runner, not the change, and a check
that fails on the machine it happens to be running on teaches people to pass
`--baseline` reflexively — which is the same as having no check at all. So CI
reports the numbers and asserts the other three claims; `make acceptance-baseline`
records a baseline for whoever wants to watch their own machine.

Exit codes follow the CLI's convention: `0` all held, `1` a claim failed, `2`
usage, `3` findings at or above `--fail-on`.

**What is not here, stated rather than implied.** The Kaggle-style corpus of
vendored third-party artifacts is not built, so claim 3 runs against this
repository rather than against a corpus with a recorded `expect` block. That is a
weaker claim than the plan's — it proves the platform is right about a tree
nobody curated for it, but not that it is right about a package whose true
dependency count somebody wrote down in advance. The difference is named here so
nobody reads the green result as the stronger thing.

Stdlib only, no pytest, no network.
"""

from __future__ import annotations

import argparse
import json
import resource
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
BASELINE = ROOT / "acceptance-baseline.json"

#: How much slower than the recorded baseline a stage may be before it fails.
#: Generous, because a CI runner under load is genuinely slower than a laptop and
#: a threshold that fires on noise teaches people to re-run rather than to read.
TOLERANCE = 2.5

MANIFEST = """apiVersion: slpie/v1
environment: acceptance
target: simulated
security:
  concerns: [pci-dss, gdpr]
  boundaries:
    - name: cardholder-data
      contains: [payments]
codebase:
  - root: ./services/payments
    language: npm
    team: payments
  - root: ./services/api
    language: python
    team: platform
  - root: ./services/gateway
    language: go
data:
  - folder: ./warehouse/orders
    kind: schema
network:
  - name: payments-api
    url: https://api.acme.test/v1
    kind: rest
  - name: order-events
    uri: kafka://broker/orders
    kind: event-stream
web:
  - name: storefront
    root: ./apps/storefront
    framework: next
iot:
  - name: fleet
    broker: mqtt://fleet.acme.test
    device_classes: [temp-v2]
providers:
  - name: stripe
"""


# --- what a run records -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class Claim:
    """One thing the run asserts, and whether it held."""

    name: str
    held: bool
    detail: str = ""
    lines: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "held": self.held, "detail": self.detail}


@dataclass(slots=True)
class Timing:
    stage: str
    seconds: float
    peak_mb: float


@dataclass(slots=True)
class Run:
    """Everything one acceptance run accumulated."""

    executed: set[str] = field(default_factory=set)
    timings: list[Timing] = field(default_factory=list)
    findings: list[Any] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        self.failures.append(message)


def _rss_mb() -> float:
    """Peak RSS for this process. Kilobytes on Linux, bytes on macOS."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)


class Driver:
    """Runs compositions and records which verbs were reached.

    Coverage is read from the *parsed* composition rather than from a list
    maintained here, so a stage that was typed but never executed cannot be
    counted, and a verb reached through a pipeline nobody thought about still is.
    """

    def __init__(self, run: Run, verbs: Any) -> None:
        self.run = run
        self.verbs = verbs

    def __call__(
        self, pipeline: str, *, expect: bool = True, **context: Any,
    ) -> Any:
        from slpie.compose import Composition, Context

        composition = Composition.read(pipeline, verbs=self.verbs)
        try:
            result = composition.run(Context(**context))
        except Exception as error:  # noqa: BLE001 - a refusal is a valid outcome
            if expect:
                self.run.note(f"{pipeline!r} was refused: {error}")
            return None

        if result.ok:
            # Only on success. A pipeline that failed at stage two did not
            # exercise stage three, and counting it would report coverage the
            # run does not have.
            self.run.executed.update(stage.verb for stage in composition.stages)
        elif expect:
            self.run.note(f"{pipeline!r} failed: {result.error}")
        return result


def timed(run: Run, stage: str) -> "_Timer":
    return _Timer(run, stage)


class _Timer:
    def __init__(self, run: Run, stage: str) -> None:
        self.run, self.stage = run, stage

    def __enter__(self) -> "_Timer":
        self.started = time.monotonic()
        print(f"  {self.stage} ...", flush=True)
        return self

    def __exit__(self, *_: Any) -> None:
        self.run.timings.append(Timing(
            self.stage, round(time.monotonic() - self.started, 2), _rss_mb(),
        ))


# --- the run ----------------------------------------------------------------


def drive_simulated(run: Run, verbs: Any, workspace: Path) -> None:
    """Everything that needs a declared environment and a materialised world."""
    from slpie.engine import Engine
    from slpie.environment import loads

    manifest_path = workspace / "slpie.environment.yaml"
    manifest_path.write_text(MANIFEST, encoding="utf-8")
    engine = Engine.create(loads(MANIFEST, source_uri=manifest_path.as_uri()))
    drive = Driver(run, verbs)
    where = {"root": str(workspace), "engine": engine, "confirmed": True}

    with timed(run, "declare and materialise"):
        drive("declare", **where)
        world = drive("simulate", **where)
        run.facts["artifacts"] = world.flow.facts["artifacts"] if world else 0

    with timed(run, "attach and scan"):
        drive("attach", **where)
        scanned = drive("scan", **where)
        run.facts["files_read"] = (
            scanned.flow.facts.get("files_read", 0) if scanned else 0
        )

    with timed(run, "reconcile and inspect"):
        drive("reconcile", **where)
        drive("status", **where)
        drive("gaps | explain", **where)
        drive("graph --limit 50 | count", **where)
        found = drive("search payments", **where)
        if found and found.flow.size:
            drive("search payments | impact --depth 3", **where)
            # What a reader would actually be shown, ranked. `interest` is the
            # degree-of-interest survey the graph screen renders from, and it
            # takes NODES rather than an id — so it belongs on the end of a
            # search, which is how the screen reaches it too.
            drive("search payments | interest --horizon 3 --budget 40", **where)
        else:
            run.note(
                "`search payments` found nothing, so `impact` and `interest` "
                "were not reached"
            )
        # Already simulated, so this is a no-op flip rather than a live binding.
        # The dangerous direction is exercised by the refusal test in the suite,
        # not by an acceptance run that would have to mean it.
        drive("target --to simulated", **where)

    drive_scenarios(run, drive, where)


def drive_scenarios(run: Run, drive: Driver, where: dict[str, Any]) -> None:
    """Fire every scenario, and check what each says the platform ought to find.

    The check is a **cross-reference**: every name in `expect_findings` must be a
    real `FindingKind` and every name in `expect_gaps` a real `GapKind`. A
    scenario predicting `foo_bar` is predicting something no rule can raise, so
    it can never fail — and a prediction that cannot fail is worth nothing.

    What is deliberately *not* checked: that scanning after the fire actually
    produces the predicted finding. That is the stronger claim and it is not made
    here. Two of the twelve change no file at all — `unmaintained` advances the
    clock, `declaration-drift` injects a fault — so asserting an effect on disk
    would fail them for being correct.
    """
    from slpie.domain.finding import FindingKind, GapKind
    from slpie.simulator.scenarios import available

    findings = {kind.value for kind in FindingKind}
    gaps = {kind.value for kind in GapKind}
    unmet: list[str] = []

    with timed(run, f"fire {len(available())} scenarios"):
        for name in available():
            result = drive(f"fire {name}", **where)
            if result is None or not result.ok:
                unmet.append(f"{name}: did not fire")
                continue
            facts = result.flow.facts
            predicted = set(facts["expect_findings"]) | set(facts["expect_gaps"])
            if not predicted:
                unmet.append(f"{name}: predicts nothing, so nothing can be checked")
                continue
            unknown = sorted(predicted - findings - gaps)
            if unknown:
                unmet.append(
                    f"{name}: predicts {', '.join(unknown)}, which is neither a "
                    f"FindingKind nor a GapKind, so it can never be raised"
                )

    run.facts["scenarios"] = len(available())
    run.facts["scenarios_unmet"] = unmet


def drive_real_tree(run: Run, verbs: Any, workspace: Path) -> None:
    """The same platform against a tree nobody curated for it: this repository."""
    drive = Driver(run, verbs)
    here = {"root": str(ROOT)}
    tree = str(ROOT)
    # The guidance verbs write an acceptance ledger under `<root>/.slpie/`, and
    # §28 retires a suggestion after three dismissals. An acceptance run that
    # dismissed into the checkout would therefore make its *own* next run fail —
    # which is exactly what happened. Pointing their root at the throwaway
    # workspace keeps the run from having a memory, and keeps it out of the
    # user's tree, which it had no business writing to either.
    aside = {"root": str(workspace)}

    with timed(run, "discover and link"):
        drive(f"discover {tree} | link | count", **here)
        drive(f"discover {tree} | link | constraints", **here)

    with timed(run, "govern"):
        governed = drive(f"discover {tree} | govern", **here)
        if governed:
            run.findings.extend(governed.flow.items)
        drive(f"discover {tree} | govern | risk", **here)
        drive(f"discover {tree} | link | findings | sort --field severity --desc "
              f"| head --count 5 | explain", **here)
        drive("rules", **here)

    with timed(run, "reason"):
        drive(f"discover {tree} | reason | ask --question "
              f"\"what should I fix first?\"", **here)
        drive(f"discover {tree} | reason | options", **here)
        drive(f"discover {tree} | reason | radius", **here)

    with timed(run, "artifacts"):
        sbom = drive(f"discover {tree} | sbom --format cyclonedx", **here)
        # The flow carries a one-line summary; the document itself is a fact.
        run.facts["sbom"] = sbom.flow.facts.get("sbom") if sbom else None
        run.facts["sbom_components"] = (
            sbom.flow.facts.get("sbom_components", 0) if sbom else 0
        )
        drive(f"discover {tree} | c4", **here)
        drive(f"discover {tree} | enterprise", **here)

    with timed(run, "capture"):
        drive(f"capture {tree} | quarantine", **here)
        drive("chain", **here)

    with timed(run, "audit"):
        judged = drive("audit", **here)
        run.facts["audit"] = judged.flow.value if judged else None
        drive("audit | verdicts --only indeterminate", **here)

    with timed(run, "incremental, agent, guidance"):
        drive("changed", **here)
        drive("agent-tools", **here)
        drive("routine", **aside)
        suggested = drive(f"discover {tree} | link | findings | suggest", **aside)
        # The key comes from what `suggest` just offered, not from a literal.
        # A hardcoded key is refused the moment the ranking changes, which makes
        # the acceptance run fail for a reason that is not a defect.
        key = _first_suggestion(suggested)
        if key:
            drive(f"discover {tree} | link | findings | accept --key {key}", **aside)
            drive(f"discover {tree} | link | findings | dismiss --key {key}", **aside)
        else:
            run.note("`suggest` offered nothing, so `accept` and `dismiss` "
                     "could not be reached")

    with timed(run, "positioning and dispatch"):
        drive("rivals", **here)
        drive("rivals --gaps", **here)
        drive("tools", **here)
        drive("history --count 5", **here)
        drive(f"discover {tree} | head --count 3 | json | tool --name jq "
              f"--args .", **here)

    with timed(run, "shaping"):
        drive(f"discover {tree} | filter --field kind --equals depends_on "
              f"| unique --field subject | count", **here)

    with timed(run, "warehouse and dashboards"):
        # The BI half, driven the way a reader reaches it: build the stars from
        # a scan, export them, load them into the store, and read one through a
        # template. `--govern` on the dashboard because half the templates read
        # the findings star and the rules are what fills it.
        drive(f"discover {tree} | warehouse", **here)
        drive(f"discover {tree} | warehouse-export --format csv "
              f"--out {workspace / 'warehouse'}", **here)
        # `warehouse-load` mutates — it drops and rebuilds the tables — so it
        # goes through the same guard `deploy-apply` does, with confirmation
        # rather than around it.
        drive(f"discover {tree} | warehouse-load "
              f"--database {workspace / 'warehouse.db'}",
              **{**here, "confirmed": True})
        drive(f"discover {tree} | dashboard --govern --domain security "
              f"--utility monitor", **here)

    with timed(run, "the product's own map"):
        # The index describes this repository rather than the environment under
        # test, which is why it needs no manifest and no scan. Exercised here
        # for the same reason everything else is: a capability the run does not
        # touch is a capability nobody would notice breaking.
        mapped = drive("context", **here)
        run.facts["facets"] = mapped.flow.facts.get("counts", {}) if mapped else {}
        drive("context --query verb:findings", **here)
        drive("lexicon", **here)
        drive("lexicon | count", **here)

    # The throwaway workspace, not the checkout: a deployment manifest and a
    # rendered `./deploy/` tree have no business appearing in somebody's repo
    # because they ran the acceptance script.
    drive_deployment(run, drive, workspace)


def drive_deployment(run: Run, drive: Driver, root: Path) -> None:
    """The platform deploying itself, up to the point where it would touch something.

    A deployment manifest is written, planned, rendered through every emitter and
    turned into an install document — and then the run **stops**, because the next
    step changes infrastructure and this script is not the place to find out
    whether the confirmation works. That it refuses is asserted in
    `tests/test_slpie_deploy.py`, through the real guard.

    Rendering every emitter rather than one is the point: an emitter nobody runs
    is an emitter that has stopped working, and the acceptance run exists to
    notice exactly that.
    """
    from slpie.deploy import emitters

    here = {"root": str(root)}
    (root / "slpie.deployment.yaml").write_text(DEPLOYMENT, encoding="utf-8")

    with timed(run, "deploy itself, up to the gate"):
        planned = drive("deploy-plan", **here)
        run.facts["deploy_changes"] = (
            planned.flow.facts.get("changes", 0) if planned else 0
        )
        drive("deploy-status", **here)
        for emitter in emitters.names():
            drive(f"deploy-render --emitter {emitter}", **here)
        drive("deploy-render --emitter compose --write", **here)
        drive("deploy-manual --emitter compose", **here)

        # `deploy-apply`, reached rather than merely refused — and it cannot
        # apply anything, for a reason that is structural rather than lucky:
        #
        #   * no engine is passed, so `apply_through` finds no adapter and
        #     returns a capability gap before touching a command;
        #   * ring 0 does not shell out at all, so there is nothing for it to
        #     reach even if it wanted to.
        #
        # The refusal path is asserted in `tests/test_slpie_deploy.py` through
        # the real guard, which is where a *gate* belongs. What this run is for
        # is coverage: a verb nobody executes is a verb nobody would notice
        # breaking, and that includes the dangerous one.
        applying = root / "applying"
        applying.mkdir(exist_ok=True)
        (applying / "slpie.deployment.yaml").write_text(
            DEPLOYMENT.replace("target: plan", "target: apply"), encoding="utf-8")
        outcome = drive("deploy-apply", root=str(applying), confirmed=True)
        if outcome and outcome.flow.value.get("applied"):
            run.note("deploy-apply reported success with no adapter installed")


#: What this platform declares about itself. Compose, because it is the one a
#: single machine can actually stand up — which is what §18's acceptance needs.
DEPLOYMENT = """
apiVersion: slpie/v1
kind: Deployment
environment: slpie-acceptance
target: plan

topology:
  api: { replicas: 1, cpu: 1, memory: 1Gi, ingress: slpie.local }
  workers: { min: 1, max: 4, cpu: 2, memory: 2Gi, queues: [scan, reason] }

persistence:
  graph: { engine: postgres, size: 10Gi }

platform: compose
cloud: onprem
"""


# --- the claims -------------------------------------------------------------


def claim_verb_coverage(run: Run, verbs: Any) -> Claim:
    registered = {verb.name for verb in verbs}
    missed = sorted(registered - run.executed)
    return Claim(
        "every registered verb was executed",
        held=not missed,
        detail=(
            f"{len(run.executed)} of {len(registered)}"
            + (f"; never reached: {', '.join(missed)}" if missed else "")
        ),
        lines=tuple(missed),
    )


def claim_scenarios(run: Run) -> Claim:
    unmet = run.facts.get("scenarios_unmet", [])
    total = run.facts.get("scenarios", 0)
    return Claim(
        "every scenario fired and predicted something checkable",
        held=not unmet and total > 0,
        detail=f"{total - len(unmet)} of {total}",
        lines=tuple(unmet),
    )


def claim_real_tree(run: Run) -> Claim:
    """Facts about this repository that are true whatever the platform says.

    Each one is checkable by hand in under a minute, which is the property that
    makes it worth asserting. A check whose expected value came from the same
    code that produced the actual value would only prove the code is consistent
    with itself.
    """
    problems: list[str] = []

    # The kernel declares no runtime dependencies. `pyproject.toml` says
    # `dependencies = []`, and that is the constraint the whole architecture
    # rests on, so the SBOM must not invent one.
    body = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if "dependencies = []" not in body:
        problems.append(
            "pyproject.toml no longer declares zero runtime dependencies, so "
            "this check is measuring something else than it was written for"
        )

    # The SBOM travels as the serialised document, which is the point: what is
    # checked here is the bytes a customer would hand to their scanner, not an
    # object that renders to them.
    document = _as_json(run.facts.get("sbom"))
    components = run.facts.get("sbom_components", 0)
    if document is None:
        problems.append("the SBOM did not parse as JSON at all")
    elif document.get("bomFormat") != "CycloneDX":
        problems.append(
            f"the SBOM claims bomFormat={document.get('bomFormat')!r}, not CycloneDX"
        )
    elif len(document.get("components", ())) != components:
        problems.append(
            f"the SBOM reports {components} component(s) as a fact but the "
            f"document carries {len(document.get('components', ()))}"
        )
    if not components:
        problems.append("the SBOM has no components at all, on a tree with lockfiles")

    # Exactly one module bridges to Gratimos, which the audit judges and
    # `tests/test_slpie_boundaries.py` independently asserts. If the two ever
    # disagree the judge is broken, and that is worth catching outside pytest.
    judged = run.facts.get("audit")
    verdicts = _verdicts(judged)
    # The rule names are read from the audit itself rather than guessed at, and
    # both directions are required: a run that only found one of them would pass
    # while half the invariant went unjudged.
    wanted = {"slpie\u2192gratimos", "gratimos\u2192slpie", "kernel-purity"}
    seen = {str(v.get("rule", "")) for v in verdicts}
    missing = sorted(wanted - seen)
    if missing:
        problems.append(
            f"the audit reported no verdict for {', '.join(missing)}; it judged "
            f"{', '.join(sorted(seen)) or 'nothing'}"
        )
    violated = [
        str(v.get("subject", "")) for v in verdicts
        if str(v.get("rule", "")) in wanted and v.get("verdict") == "violated"
    ]
    if violated:
        problems.append(
            "a ring or bridge invariant is violated: " + ", ".join(violated)
        )

    if not run.facts.get("files_read"):
        problems.append("the simulated world was materialised but never read")

    return Claim(
        "what the platform says about a real tree matches what is true of it",
        held=not problems,
        detail=(
            f"{components} SBOM component(s), "
            f"{len(verdicts)} audit verdict(s), "
            f"{len(run.findings)} finding(s)"
        ),
        lines=tuple(problems),
    )


def _first_suggestion(result: Any) -> str:
    """A key `suggest` actually offered, so `accept` has something real to take.

    Read from `suggestion_keys` rather than from the flow's items: `suggest` is a
    passthrough, so what flows on is the findings it was handed, and the
    suggestions travel as facts beside them.
    """
    if result is None or not getattr(result, "ok", False):
        return ""
    offered = result.flow.facts.get("suggestion_keys") or ()
    return str(offered[0]) if offered else ""


def _as_json(body: Any) -> dict[str, Any] | None:
    if isinstance(body, dict):
        return body
    if isinstance(body, str):
        try:
            found = json.loads(body)
        except json.JSONDecodeError:
            return None
        return found if isinstance(found, dict) else None
    return None


def _verdicts(report: Any) -> list[dict[str, Any]]:
    """The audit's verdicts as plain dicts, whatever shape it handed back."""
    if report is None:
        return []
    if hasattr(report, "judgements"):
        report = report.judgements
    found: list[dict[str, Any]] = []
    for item in (report if isinstance(report, (list, tuple)) else ()):
        if isinstance(item, dict):
            found.append(item)
        elif hasattr(item, "to_dict"):
            found.append(item.to_dict())
        else:
            found.append({
                "rule": getattr(item, "rule", ""),
                "verdict": getattr(getattr(item, "verdict", None), "value", ""),
                "subject": getattr(item, "subject", ""),
            })
    return found


def claim_cost(run: Run, *, record: bool, enforce: bool) -> Claim:
    """Wall time per stage against the recorded baseline.

    Reported always, **enforced only when asked**. Two runs of this script on one
    unloaded machine, minutes apart, differed by more than the 2.5x tolerance on
    the `govern` stage — so a check that failed on that would fail for reasons
    that have nothing to do with the code, and the first thing anybody would
    learn is to re-run it. A flaky gate is worse than no gate: it trains people
    to ignore a red result.

    `--check-cost` turns it into a real gate for somebody who wants one on a
    quiet machine, and `make acceptance-baseline` records what to compare to.
    """
    measured = {timing.stage: timing.seconds for timing in run.timings}
    peak = max((timing.peak_mb for timing in run.timings), default=0.0)

    if record:
        BASELINE.write_text(
            json.dumps({"stages": measured, "peak_mb": peak}, indent=2) + "\n",
            encoding="utf-8",
        )
        return Claim(
            "cost is within tolerance of the baseline", held=True,
            detail=f"baseline rewritten: {len(measured)} stage(s), peak {peak} MB",
        )

    if not BASELINE.exists():
        return Claim(
            "cost is within tolerance of the baseline", held=True,
            detail=(
                f"{len(measured)} stage(s), peak {peak} MB; no baseline on this "
                f"machine — `make acceptance-baseline` records one"
            ),
        )

    recorded = json.loads(BASELINE.read_text(encoding="utf-8"))
    slow: list[str] = []
    del enforce  # read below, after the comparison, so the detail is the same
    for stage, seconds in measured.items():
        was = recorded.get("stages", {}).get(stage)
        if was is None:
            continue                       # a new stage has nothing to regress against
        # An absolute floor as well as a ratio: a stage that went from 0.02s to
        # 0.06s is 3x slower and nobody cares, and failing on it would train
        # people to pass --baseline reflexively.
        if seconds > max(was * TOLERANCE, was + 1.0):
            slow.append(f"{stage}: {seconds:.2f}s against {was:.2f}s recorded")

    return Claim(
        "cost is within tolerance of the baseline",
        held=not slow or not _ENFORCE_COST[0],
        detail=(
            f"{len(measured)} stage(s), peak {peak} MB, tolerance {TOLERANCE}x"
            + ("" if _ENFORCE_COST[0] else "; reported only — pass --check-cost "
               "to make this a gate")
            + (f"; {len(slow)} stage(s) over" if slow else "")
        ),
        lines=tuple(slow),
    )


#: Set once from the command line. A module-level cell rather than a parameter
#: threaded through four call sites, because the alternative was passing a flag
#: into a function that has no other reason to know about the command line.
_ENFORCE_COST = [False]


# --- reporting --------------------------------------------------------------


def report(run: Run, claims: list[Claim]) -> str:
    lines = ["", "  Acceptance", "  " + "=" * 68, ""]

    lines.append(f"  {'stage':34} {'seconds':>9} {'peak MB':>9}")
    lines.append("  " + "-" * 68)
    for timing in run.timings:
        lines.append(f"  {timing.stage:34} {timing.seconds:>9.2f} "
                     f"{timing.peak_mb:>9.1f}")
    lines.append("  " + "-" * 68)
    lines.append(f"  {'total':34} "
                 f"{sum(t.seconds for t in run.timings):>9.2f}")
    lines.append("")

    for claim in claims:
        mark = "held" if claim.held else "FAILED"
        lines.append(f"  [{mark:6}] {claim.name}")
        if claim.detail:
            lines.append(f"            {claim.detail}")
        for note in claim.lines[:12]:
            lines.append(f"              - {note}")
        if len(claim.lines) > 12:
            lines.append(f"              ... and {len(claim.lines) - 12} more")
    lines.append("")

    if run.failures:
        lines.append(f"  {len(run.failures)} pipeline(s) did not run:")
        for note in run.failures[:12]:
            lines.append(f"    - {note}")
        lines.append("")

    by_severity: dict[str, int] = {}
    for finding in run.findings:
        key = getattr(getattr(finding, "severity", None), "value", "?")
        by_severity[key] = by_severity.get(key, 0) + 1
    if by_severity:
        lines.append(f"  findings on this repository: "
                     + ", ".join(f"{count} {name}"
                                 for name, count in sorted(by_severity.items())))
        lines.append("")

    return "\n".join(lines)


def _at_or_above(run: Run, severity: str) -> int:
    order = ("info", "low", "medium", "high", "critical")
    if severity not in order:
        return 0
    floor = order.index(severity)
    return sum(
        1 for finding in run.findings
        if getattr(getattr(finding, "severity", None), "value", "") in order
        and order.index(finding.severity.value) >= floor
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline", action="store_true",
        help="re-record the stage timings instead of checking against them",
    )
    parser.add_argument(
        "--fail-on", default="", metavar="SEVERITY",
        help="exit 3 if this repository has findings at or above this severity",
    )
    parser.add_argument(
        "--check-cost", action="store_true",
        help="fail if a stage is slower than the recorded baseline. Off by "
             "default: the comparison is only meaningful on a quiet machine "
             "against a baseline recorded on that same machine",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead")
    args = parser.parse_args(argv)

    _ENFORCE_COST[0] = args.check_cost

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from slpie.compose import registry

    verbs = registry()
    run = Run()
    workspace = Path(tempfile.mkdtemp(prefix="slpie-acceptance-"))

    print(f"  driving {len(list(verbs))} verbs; workspace {workspace}", flush=True)
    try:
        drive_simulated(run, verbs, workspace)
        drive_real_tree(run, verbs, workspace)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    claims = [
        claim_verb_coverage(run, verbs),
        claim_scenarios(run),
        claim_real_tree(run),
        claim_cost(run, record=args.baseline, enforce=args.check_cost),
    ]

    if args.json:
        print(json.dumps({
            "claims": [claim.to_dict() for claim in claims],
            "timings": [
                {"stage": t.stage, "seconds": t.seconds, "peak_mb": t.peak_mb}
                for t in run.timings
            ],
            "executed": sorted(run.executed),
            "failures": run.failures,
        }, indent=2))
    else:
        print(report(run, claims))

    if not all(claim.held for claim in claims):
        return 1
    if args.fail_on and _at_or_above(run, args.fail_on):
        return 3
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
