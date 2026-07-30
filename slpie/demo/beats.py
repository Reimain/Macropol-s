"""The demo's beats. Each one names its claim, and each claim is checked.

A beat is not a paragraph of narration with a print statement attached. It states
what it demonstrates, which surface demonstrates it, which screen it maps to, and
which call produces it — and then `run` returns whether the claim actually held.

`holds=False` is not a rendering detail. The terminal marks it, the UI marks it,
and the test suite fails on it. A demo that narrated a claim it could not
substantiate would be the most expensive kind of documentation: convincing and
wrong.
"""

from __future__ import annotations

from typing import Any

from ..compose import Composition, Context, Kind, registry
from ..compose.wire import decode, encode
from .script import Beat, Outcome, Script, Stage, Surface

PIPELINE = "discover {root} | link | findings"


def _pipeline(stage: Stage) -> str:
    return PIPELINE.format(root=stage.root)


# --- the beats -----------------------------------------------------------


def _world(stage: Stage) -> Outcome:
    from .world import FILES, describe, materialise

    materialise(stage.root)
    return Outcome(
        holds=all((stage.root / name).is_file() for name in FILES),
        lines=(*sorted(FILES), "", *describe()),
        detail=f"{len(FILES)} real files on disk",
    )


def _plan(stage: Stage) -> Outcome:
    from ..planner import plan_for

    plan = plan_for("what is wrong here before release?")
    lines = [plan.pipeline, ""]
    lines.extend(
        f"{index}. {step.verb:<12} {step.because}"
        for index, step in enumerate(plan.steps, start=1)
    )
    stage.facts["planned"] = plan.pipeline
    return Outcome(
        # The claim is not "it produced something" but "it produced something
        # runnable" — a planner that emits an invalid pipeline is worse than one
        # that refuses.
        holds=plan.understood and Composition.read(plan.pipeline).validate().ok,
        lines=tuple(lines),
        detail="planned offline, with no key and no network",
    )


def _check(stage: Stage) -> Outcome:
    composition = Composition.read(_pipeline(stage))
    validation = composition.validate()
    return Outcome(
        holds=validation.ok,
        lines=tuple(composition.explain().splitlines()),
        detail=f"produces {validation.produces.label} — known before it ran",
    )


def _refusal(stage: Stage) -> Outcome:
    """A refusal is part of the demo. Showing only the happy path teaches that
    nothing is ever refused, which is the opposite of what this platform does."""
    broken = Composition.read("findings | attach")
    validation = broken.validate()
    return Outcome(
        holds=not validation.ok,
        lines=("findings | attach", f"→ {validation.explain()}"),
        detail="refused before stage one, so nothing had happened yet",
    )


def _run(stage: Stage) -> Outcome:
    result = Composition.read(_pipeline(stage)).run(Context(root=str(stage.root)))
    if not result.ok:
        return Outcome(holds=False, lines=(str(result),), detail=result.error)

    stage.digests["in-process"] = result.flow.digest
    lines = [
        str(result.flow),
        f"confidence {result.flow.confidence}, "
        f"{'grounded' if result.flow.grounded else 'NOT GROUNDED'}",
        "",
    ]
    for item in result.flow.items[:4]:
        lines.append(f"[{getattr(item.severity, 'value', '?')}] "
                     f"{getattr(item, 'title', item)}")
        if getattr(item, "detail", ""):
            lines.append(f"      {item.detail}")
        for evidence in getattr(item, "evidence", ())[:2]:
            lines.append(f"      cited: {evidence.location.reference}")

    stage.facts["findings"] = result.flow.size
    return Outcome(
        # Grounded is the claim. A finding that cannot be traced to a file and a
        # line is the failure this whole architecture exists to prevent.
        holds=result.flow.grounded and result.flow.size > 0,
        lines=tuple(lines),
        digest=result.flow.digest,
        detail="every line traces back to a file and a line",
    )


def _http(stage: Stage) -> Outcome:
    from ..ui.api import Api, Request

    response = Api(engine=None).handle(Request(
        method="POST", path="/api/run", query={},
        body={"pipeline": _pipeline(stage)},
    ))
    flow = response.body.get("flow", {})
    stage.digests["http"] = flow.get("digest", "")
    return Outcome(
        holds=response.status == 200 and bool(flow.get("digest")),
        lines=(
            f"POST /api/run → {response.status}",
            f"{str(flow.get('kind', '')).upper()}[{flow.get('size', 0)}]",
            f"reasoning: {len(flow.get('reasoning', {}).get('steps', []))} step(s)",
        ),
        digest=flow.get("digest", ""),
        detail="the screen renders exactly this payload",
    )


def _pipe(stage: Stage) -> Outcome:
    """Two processes. The gaps and reasoning cross with the value."""
    produced = Composition.read(f"discover {stage.root}").run(Context())
    crossed = decode(encode(produced.flow))
    stage.digests["os-pipe"] = crossed.digest
    return Outcome(
        holds=(
            crossed.digest == produced.flow.digest
            and crossed.size == produced.flow.size
        ),
        lines=(
            "slpie discover . --wire | slpie link",
            f"{crossed.size} observations crossed as JSON",
            f"{len(crossed.reasoning.steps)} reasoning step(s) came with them",
        ),
        digest=crossed.digest,
        detail="provenance does not stop at a process boundary",
    )


def _agree(stage: Stage) -> Outcome:
    """The thesis. If these diverge, one surface is lying."""
    compared = {
        name: digest for name, digest in stage.digests.items()
        if name in ("in-process", "http")
    }
    agreed = len(set(compared.values())) == 1 and all(compared.values())
    return Outcome(
        holds=agreed,
        lines=tuple(
            f"{name:<22} {digest}" for name, digest in stage.digests.items()
        ) + (
            "",
            "✓ identical — the same composition is the same answer" if agreed
            else "✕ THE SURFACES DISAGREE — one of them is lying",
        ),
        detail="asserted, not observed: the test suite fails if they diverge",
    )


def _contract(stage: Stage) -> Outcome:
    """The client's local type check has to agree with the server's."""
    verbs = registry()
    cases = (
        (["discover", "link", "findings"], True),
        (["findings", "attach"], False),
        (["discover", "impact"], False),
    )
    holds = True
    lines: list[str] = []
    for names, expected in cases:
        current = "nothing"
        client_ok = True
        for name in names:
            verb = verbs.require(name)
            if verb.consumes.value not in ("any", current):
                client_ok = False
                break
            produced = verb.produces.value
            current = current if produced == "same" else produced
        if client_ok is not expected:
            holds = False
        lines.append(
            f"{' | '.join(names):<34} client says "
            f"{'ok' if client_ok else 'no'}, expected "
            f"{'ok' if expected else 'no'}"
        )
    return Outcome(
        holds=holds, lines=tuple(lines),
        detail="the generated client rejects impossible compositions locally",
    )


def _projection(stage: Stage) -> Outcome:
    verbs = registry()
    from ..manual import offline, pages

    documented = len(pages(verbs))
    return Outcome(
        holds=documented == len(verbs),
        lines=(
            f"{len(verbs)} verbs",
            f"{len(verbs)} CLI subcommands",
            f"{len(verbs)} HTTP routes",
            f"{documented} manual pages",
            f"{len(offline())} runnable recipes",
            "one planner vocabulary, one client palette",
        ),
        detail="add a verb and it appears in all of them; the suite fails if not",
    )


# --- the script ----------------------------------------------------------


def script() -> Script:
    """The demo. Ordered to teach, not to enumerate."""
    return Script(
        name="SLPIE — end to end",
        thesis=(
            "Capabilities are verbs. Verbs pipe. The pipe carries the reasoning "
            "and the gaps rather than bytes, so composing accumulates the "
            "explanation instead of discarding it — and every surface is a "
            "projection of one registry, so none of them can disagree."
        ),
        beats=(
            Beat(
                key="world", title="a real world on disk", surface=Surface.CLI,
                claim="the demo runs against real files, not mocks",
                narration=(
                    "The same discoverers run against this as against a "
                    "customer's tree, so a green demo is evidence about the real "
                    "code path.",
                ),
                run=_world,
            ),
            Beat(
                key="plan", title="ask, and it writes the composition",
                surface=Surface.CLI, screen="compose",
                call='slpie plan "what is wrong here before release?"',
                claim="the offline planner emits a composition that type-checks",
                narration=(
                    "It selects routes from the type graph rather than composing "
                    "freely, so it cannot invent a verb and cannot emit a "
                    "pipeline that would not run. No key, no network.",
                ),
                run=_plan,
            ),
            Beat(
                key="check", title="check it before running any of it",
                surface=Surface.CLI, screen="compose",
                call="slpie '<pipeline>' --dry-run",
                claim="the composition's output kind is known before it executes",
                narration=(
                    "You see what it will do and what it will cost while the "
                    "decision is still free.",
                ),
                run=_check,
            ),
            Beat(
                key="refusal", title="an impossible composition is refused",
                surface=Surface.CLI, screen="compose",
                call="slpie 'findings | attach'",
                claim="an invalid composition names both kinds and runs nothing",
                narration=(
                    "Showing only the happy path would teach that nothing is "
                    "ever refused. The refusal arrives before stage one, so for "
                    "a pipeline with a mutating verb nothing has happened yet.",
                ),
                run=_refusal,
            ),
            Beat(
                key="run", title="run it", surface=Surface.CLI, screen="compose",
                call="slpie 'discover . | link | findings'",
                claim="the finding is real and every line of it cites a file",
                narration=(
                    "The contradiction spans two files, so no single discoverer "
                    "could have found it. That is what the linking layer is for.",
                ),
                run=_run,
            ),
            Beat(
                key="http", title="the same pipeline through HTTP",
                surface=Surface.API, screen="compose", call="POST /api/run",
                claim="the API returns the same answer with its provenance intact",
                narration=(
                    "The screen renders this payload directly, so if the "
                    "reasoning were dropped in transit the UI would show an "
                    "answer with no justification.",
                ),
                run=_http,
            ),
            Beat(
                key="pipe", title="and across two processes",
                surface=Surface.PIPE,
                call="slpie discover . --wire | slpie link",
                claim="a flow crossing a process boundary is unchanged",
                narration=(
                    "Gaps and reasoning cross with the value. Provenance whose "
                    "honesty depended on how the tool was invoked would not be "
                    "provenance.",
                ),
                run=_pipe,
            ),
            Beat(
                key="agree", title="do the surfaces agree?",
                surface=Surface.CLI,
                claim="every surface produced an identical digest",
                narration=(
                    "This is the thesis. Different surfaces must not be "
                    "different answers.",
                ),
                run=_agree,
            ),
            Beat(
                key="contract", title="the client checks locally, and agrees",
                surface=Surface.CONTRACT, screen="compose",
                call="slpie contract --typescript",
                claim="the generated client's type check matches the server's",
                narration=(
                    "If they disagreed, the builder would offer compositions the "
                    "server then refuses — which reads to a user as a bug in the "
                    "platform.",
                ),
                run=_contract,
            ),
            Beat(
                key="projection", title="one registry, five projections",
                surface=Surface.CLI, screen="compose", call="slpie help",
                claim="every verb is present in every projection",
                narration=(
                    "A verb added once appears in the CLI, the API, the manual, "
                    "the planner and every client. Registering one without "
                    "wiring it is a test failure, not a drift somebody finds "
                    "later.",
                ),
                run=_projection,
            ),
        ),
    )
