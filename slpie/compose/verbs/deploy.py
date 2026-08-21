"""Deployment as verbs — plan, render, manual, status, and the gated apply.

Five verbs, and the interesting one is the fifth. Four of them read a manifest
and produce text: they touch nothing, need no credentials, and work air-gapped.
`apply` changes infrastructure, and it is refused unless confirmed **by
`slpie/binding/guard.py`** — the same object that refuses an unconfirmed live
binding, reached the same way `target` reaches it.

That is not tidiness. §16 declines to reimplement the live guard for FastAPI
because a second implementation of a gate is a second set of bugs and only one
of them gets patched; the same argument applies to a second confirmation for a
second dangerous action. `deploy apply` and `target --to live` are the same class
of thing and go through the same code.

── Why these consume NOTHING ────────────────────────────────────────────

Every verb here starts a pipeline rather than continuing one. A deployment
manifest is a declaration read from disk, not something an upstream stage
produces — and typing them as consumers of `MANIFEST` would make
`declare | deploy plan` type-check while feeding the *environment* manifest to
the deployment planner, which is exactly the confusion the schema's `kind:`
check exists to catch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ...domain.finding import Gap, GapKind
from ...domain.reasoning import ReasoningStep
from ..flow import Flow, Kind
from ..verb import Context, Param, Verb, VerbError

GROUP = "deploy"

#: Verb names are globally unique — the registry refuses a second `status`
#: because "a shadowed verb inherits confirmations it was never granted", which
#: is exactly the sentence you want to read before naming an *apply* loosely. So
#: these carry their group, as `agent-tools` already does. `slpie deploy plan`
#: still works: the CLI joins a two-token head when the joined name is a verb,
#: which is a general rule rather than a special case for this family.


def _gap(subject: str, detail: str, *, remediation: str = "") -> Gap:
    """A limit of this build, in the vocabulary the rest of the platform uses.

    `NOT_IMPLEMENTED` rather than a new kind. An emitter that cannot express an
    autoscaler and a build that cannot shell out to terraform are both "this
    platform does not do that here", which is exactly what the kind means — and
    inventing a `DEPLOY_LIMIT` would break every comparison the guidance layer
    makes across gap kinds.
    """
    return Gap(
        kind=GapKind.NOT_IMPLEMENTED, subject=subject, detail=detail,
        remediation=remediation, confidence_impact=0.0,
    )


def _step(claim: str, operation: str = "compare") -> ReasoningStep:
    """One line of explanation.

    No evidence attached, and that is honest rather than lazy: a plan rests on a
    manifest and a platform's reply, neither of which is an observation with a
    file and a line behind it. `grounded` therefore reads false, which is the
    right answer — a deployment plan is a *comparison*, not a finding.
    """
    return ReasoningStep(claim=claim, operation=operation, layer="deploy")


def _manifest(arguments: Mapping[str, Any], context: Context):
    """Read the deployment manifest, or say precisely what is missing.

    `--root` is honoured rather than the process working directory. That was a
    live defect in the environment loader (`docs/AUDIT.md`), and repeating it
    here would mean `slpie --root /somewhere deploy plan` silently planning
    against whatever happened to be next to the shell.
    """
    from ...deploy.manifest import DEFAULT_FILENAME, load
    from ...errors import ManifestError

    stated = str(arguments.get("manifest") or "")
    path = Path(stated) if stated else Path(context.root) / DEFAULT_FILENAME
    try:
        return load(path)
    except ManifestError as error:
        raise VerbError(
            f"{error}\n\nWrite one at {path}, or pass --manifest. "
            f"`slpie deploy manual` shows the shape."
        ) from None


def _running(arguments: Mapping[str, Any], context: Context):
    """What the platform says is deployed, and whether anybody asked it.

    Returns `(components, gaps)`. Nothing in ring 0 can query a live cluster —
    that is `slpie_enterprise/deploy/`'s job — so absent an adapter this
    reports *nothing observed* and says so. A plan that silently assumed an
    empty cluster would render every component as an addition and read as a
    first install when it was really a blind one.
    """
    observer = getattr(context.engine, "deployment_observer", None)
    if observer is None:
        return None, (
            "nothing was asked what is running: this build has no platform "
            "adapter, so the plan is against an assumed-empty estate. Install "
            "`slpie[enterprise]` for a live diff.",
        )
    return observer(), ()


def _plan(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """The diff between declared and running. Touches nothing."""
    from ...deploy.plan import plan as compute

    declared = _manifest(arguments, context)
    running, gaps = _running(arguments, context)
    answer = compute(declared, running, gaps=gaps)

    return flow.then(
        Kind.REPORT, answer.to_dict(), stage="deploy plan",
        steps=[_step(
            f"compared {len(declared.components)} declared component(s) against "
            f"{'what is running' if running is not None else 'an unobserved estate'}"
        )],
        facts={
            "changes": len(answer.changes),
            "destructive": len(answer.destructive),
            "empty": answer.empty,
        },
    )


def _render(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """Emit the artifacts. Reviewable before anything runs — which is the point."""
    from ...deploy import emitters
    from ...deploy._render import DEFAULT_OUTPUT, write

    declared = _manifest(arguments, context)
    wanted = str(arguments.get("emitter") or "") or emitters.default_for(declared)
    if wanted not in emitters.names():
        raise VerbError(
            f"no emitter named {wanted!r}; this build has "
            f"{', '.join(emitters.names())}"
        )

    files = emitters.render(declared, emitter=wanted)
    gaps = emitters.gaps(declared, emitter=wanted)

    written: tuple[str, ...] = ()
    if arguments.get("write"):
        destination = str(arguments.get("out") or DEFAULT_OUTPUT)
        written = write(files, Path(context.root) / destination)

    return flow.then(
        Kind.REPORT,
        {
            "emitter": wanted,
            "files": {name: text for name, text in sorted(files.items())},
            "written": list(written),
            "gaps": list(gaps),
        },
        stage="deploy render",
        steps=[_step(f"rendered {len(files)} file(s) through the {wanted} emitter",
                     "generate")],
        # Reported as gaps on the flow, not merely printed: a limit that
        # travels with the answer is invariant 5 holding through composition.
        gaps=[_gap(f"deploy render --emitter {wanted}", gap) for gap in gaps],
        facts={"emitter": wanted, "files": len(files), "written": len(written)},
    )


def _manual(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """The install document, generated so it cannot drift from what deploys."""
    from ...deploy import emitters
    from ...deploy.manual import install_manual

    declared = _manifest(arguments, context)
    wanted = str(arguments.get("emitter") or "") or emitters.default_for(declared)
    text = install_manual(declared, emitter=wanted)

    if target := str(arguments.get("out") or ""):
        path = Path(context.root) / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    return flow.then(
        Kind.TEXT, text, stage="deploy manual",
        steps=[_step("generated the install manual from the topology model",
                     "generate")],
        facts={"emitter": wanted, "bytes": len(text)},
    )


def _status(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """Declared against running, as a summary rather than a diff."""
    from ...deploy.plan import plan as compute

    declared = _manifest(arguments, context)
    running, gaps = _running(arguments, context)
    answer = compute(declared, running, gaps=gaps)

    return flow.then(
        Kind.REPORT,
        {
            "environment": declared.environment,
            "platform": declared.platform.value,
            "cloud": declared.cloud.value,
            "declared": [item.to_dict() for item in declared.components],
            "matching": list(answer.unchanged),
            "differing": len(answer.changes),
            "observed": running is not None,
            "summary": answer.summary(),
            "gaps": list(answer.gaps),
        },
        stage="deploy status",
        steps=[_step(f"read {declared.environment}'s declared topology")],
        gaps=[_gap("deploy status", gap) for gap in answer.gaps],
        facts={"environment": declared.environment, "differing": len(answer.changes)},
    )


def _apply(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """Change the infrastructure. The one dangerous verb here.

    Two refusals, in this order, and the order matters:

    1. **Unconfirmed** — refused by `binding/guard.py`, the same gate that
       refuses an unconfirmed live binding. Checked first, because a caller who
       has not confirmed should not learn what would have happened.
    2. **No applier** — ring 0 emits text and does not shell out to
       `terraform`. Absent the enterprise adapter this is a *capability gap*
       naming what is missing, exactly as §27 treats a missing binary — never a
       crash, and never a silent success.
    """
    from ...deploy._apply import apply_through, refuse_unconfirmed

    declared = _manifest(arguments, context)
    refuse_unconfirmed(declared, context)

    outcome = apply_through(declared, context, emitter=str(arguments.get("emitter") or ""))
    return flow.then(
        Kind.REPORT, outcome.to_dict(), stage="deploy apply",
        steps=[_step(outcome.summary, "apply")],
        gaps=[
            _gap(f"deploy apply {declared.environment}", gap,
                 remediation="install `slpie[enterprise]`, or render and apply "
                             "the artifacts yourself")
            for gap in outcome.gaps
        ],
        facts={"applied": outcome.applied, "environment": declared.environment},
    )


def verbs() -> tuple[Verb, ...]:
    from ...deploy import emitters
    from ...deploy._render import DEFAULT_OUTPUT

    choices = emitters.names()
    manifest_param = Param(
        "manifest", "str",
        "a deployment manifest elsewhere than ./slpie.deployment.yaml",
    )
    emitter_param = Param(
        "emitter", "str", "which platform to render for", choices=choices,
    )

    return (
        Verb(
            name="deploy-plan", group=GROUP, consumes=Kind.NOTHING, produces=Kind.REPORT,
            summary="the diff between the declared topology and the running one",
            detail=(
                "Touches nothing, and *reaches* nothing: a plan is computed "
                "from two models, so it costs a file read and can be produced "
                "for a cluster nobody has credentials to. Changes are typed — "
                "add, remove, scale, alter — because an apply that quietly "
                "deletes a component is the failure this model exists to make "
                "visible, and reporting it as `1 change` would be complete and "
                "useless."
            ),
            params=(manifest_param,),
            examples=("deploy-plan", "deploy-plan --manifest ./ops/prod.yaml"),
            run=_plan,
        ),
        Verb(
            name="deploy-render", group=GROUP, consumes=Kind.NOTHING, produces=Kind.REPORT,
            summary="emit the deployment artifacts, reviewable before anything runs",
            detail=(
                "Six emitters over one model — compose, kubernetes, systemd, "
                "helm, terraform and CI pipelines — so a fourth platform is a "
                "registration rather than a fork. Nothing reads the clock or "
                "the environment, so the same manifest renders byte-identically "
                "twice and a change is reviewable in a diff. What an emitter "
                "cannot express is reported as a gap rather than quietly left "
                "out of the file."
            ),
            params=(
                manifest_param, emitter_param,
                Param("write", "bool", "write the files instead of returning them"),
                Param("out", "str", "where to write them",
                      default=DEFAULT_OUTPUT),
            ),
            examples=(
                "deploy-render",
                "deploy-render --emitter terraform",
                "deploy-render --emitter compose --write",
            ),
            run=_render,
        ),
        Verb(
            name="deploy-manual", group=GROUP, consumes=Kind.NOTHING, produces=Kind.TEXT,
            summary="the install document, generated so it cannot drift",
            detail=(
                "A hand-written install document is wrong within two releases: "
                "it names a port the manifest no longer opens and a flag that "
                "was renamed. This one is wrong only if the model is — the "
                "prerequisites are the chosen emitter's, the ports are the "
                "manifest's, and the commands are the ones the CLI accepts."
            ),
            params=(
                manifest_param, emitter_param,
                Param("out", "str", "write it here, e.g. docs/INSTALL.md"),
            ),
            examples=("deploy-manual", "deploy-manual --out docs/INSTALL.md"),
            run=_manual,
        ),
        Verb(
            name="deploy-status", group=GROUP, consumes=Kind.NOTHING, produces=Kind.REPORT,
            summary="what is declared, and whether anything is running to match",
            detail=(
                "The summary form of `deploy plan`. Absent a platform adapter "
                "it reports that nothing was asked rather than assuming an "
                "empty estate — a blind plan and a first install produce the "
                "same diff and are not the same confidence."
            ),
            params=(manifest_param,),
            examples=("deploy-status",),
            run=_status,
        ),
        Verb(
            name="deploy-apply", group=GROUP, consumes=Kind.NOTHING, produces=Kind.REPORT,
            summary="make it so — gated by the same guard as `target --to live`",
            mutates=True,
            detail=(
                "Refused without confirmation by `binding/guard.py`, which is "
                "the object that refuses an unconfirmed live binding. There is "
                "no second gate: a second implementation of a confirmation is a "
                "second set of bugs, and only one of them gets patched. Ring 0 "
                "emits text and does not shell out, so absent the enterprise "
                "adapter this reports a capability gap naming what is missing "
                "rather than failing or, worse, appearing to succeed."
            ),
            params=(manifest_param, emitter_param),
            examples=("deploy-apply",),
            run=_apply,
        ),
    )
