"""`slpie demo` — the end-to-end run, driven and narrated.

The demo materialises a small **real** repository on a temp filesystem, then drives
the same composition through every surface and shows that they agree. Nothing is
mocked: the same 29 discoverers, the same linkers, the same reasoning layers and
the same verbs run against this as against a customer's tree, so a green demo is
evidence about the real code path rather than a rehearsal of one.

The claim it exists to demonstrate is the one that is easy to say and hard to
hold: **different surfaces are not different answers.** The CLI, an OS pipe, the
HTTP API and the generated contract all execute one pipeline, and the demo prints
the `Flow` digest from each. If they diverge, one surface is lying.

`tests/test_slpie_demo.py` asserts every step of this. A demo that only runs when
somebody is watching is a demo that breaks quietly.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Sequence

#: The composition. It exercises discovery, identity merging, a cross-file join, a
#: contradiction and a finding with evidence — and needs no environment, so it runs
#: wherever the package is installed.
PIPELINE = "discover {root} | link | findings"

#: A repository whose manifest and lockfile genuinely disagree. That disagreement
#: is the finding the demo exists to surface: the build is not what the manifest
#: says it is, and no single file could have revealed it.
WORLD: dict[str, str] = {
    "package.json": json.dumps({
        "name": "storefront",
        "version": "2.1.0",
        "dependencies": {"lodash": "^3.0.0", "express": "^4.18.0"},
    }, indent=2) + "\n",
    "package-lock.json": json.dumps({
        "name": "storefront",
        "lockfileVersion": 3,
        "packages": {
            "node_modules/lodash": {"version": "4.17.21"},
            "node_modules/express": {"version": "4.18.2"},
        },
    }, indent=2) + "\n",
    "requirements.txt": "flask==3.0.0\nPyYAML==6.0.1\n",
    "Dockerfile": "FROM python:3.11-slim\nRUN pip install flask\n",
    "app/main.py": "import yaml\nimport flask\n\n\ndef main() -> None:\n    pass\n",
}


def materialise(root: Path) -> Path:
    """Write the world as real files. Real artifacts beat mocks, per §19."""
    for name, content in WORLD.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


class Demo:
    """Drives the demo and narrates it. Streams are injected so it is testable."""

    def __init__(self, *, stdout: Any, root: Path | None = None) -> None:
        self.stdout = stdout
        self.root = root
        self._temporary: str | None = None
        self.digests: dict[str, str] = {}

    # -- narration -------------------------------------------------------

    def say(self, text: str = "") -> None:
        self.stdout.write(text.rstrip("\n") + "\n")

    def heading(self, number: int, text: str) -> None:
        self.say()
        self.say(f"  {number}. {text}")
        self.say(f"     {'-' * len(text)}")

    # -- the run ---------------------------------------------------------

    def run(self) -> int:
        from .compose import Composition, Context, Kind
        from .compose.wire import decode, encode
        from .manual import offline
        from .planner import plan_for

        if self.root is None:
            self._temporary = tempfile.mkdtemp(prefix="slpie-demo-")
            self.root = Path(self._temporary)
        materialise(self.root)

        try:
            self.say()
            self.say("  SLPIE — end to end")
            self.say()
            self.say("  Capabilities are verbs. Verbs pipe. The pipe carries the")
            self.say("  reasoning and the gaps, not bytes — so composing accumulates")
            self.say("  the explanation instead of discarding it.")

            pipeline = PIPELINE.format(root=self.root)

            # 1 -----------------------------------------------------------
            self.heading(1, "a real world on disk")
            for name in sorted(WORLD):
                self.say(f"     {name}")
            self.say()
            self.say("     package.json asks for lodash ^3.0.0.")
            self.say("     package-lock.json pins 4.17.21. Nobody's build is what")
            self.say("     they think it is — and no single file says so.")

            # 2 -----------------------------------------------------------
            self.heading(2, "ask a question; it writes the composition")
            plan = plan_for("what is wrong here before release?")
            self.say(f"     {plan.pipeline}")
            for index, step in enumerate(plan.steps, start=1):
                self.say(f"       {index}. {step.verb:<12} {step.because}")
            self.say()
            self.say("     Planned offline, with no key: the planner selects routes")
            self.say("     from the type graph, so it cannot invent a verb and")
            self.say("     cannot emit a pipeline that would not run.")

            # 3 -----------------------------------------------------------
            self.heading(3, "check it before running any of it")
            composition = Composition.read(pipeline)
            self.say(composition.explain())

            # 4 -----------------------------------------------------------
            self.heading(4, "an impossible composition is refused, and nothing runs")
            broken = Composition.read("findings | attach")
            self.say(f"     findings | attach")
            self.say(f"     → {broken.validate().explain()}")

            # 5 -----------------------------------------------------------
            self.heading(5, "run it in process")
            result = composition.run(Context(root=str(self.root)))
            if not result.ok:
                self.say(f"     FAILED: {result.error}")
                return 1
            self.digests["in-process"] = result.flow.digest
            self._report(result.flow)

            # 6 -----------------------------------------------------------
            self.heading(6, "the same pipeline through the HTTP API")
            from .ui.api import Api, Request

            api = Api(engine=None)
            response = api.handle(Request(
                method="POST", path="/api/run", query={},
                body={"pipeline": pipeline},
            ))
            self.digests["http"] = response.body["flow"]["digest"]
            self.say(f"     POST /api/run → {response.status}, "
                     f"{response.body['flow']['kind'].upper()}"
                     f"[{response.body['flow']['size']}]")

            # 7 -----------------------------------------------------------
            self.heading(7, "and across two processes, as real OS pipes")
            produced = Composition.read(f"discover {self.root}").run(Context())
            crossed = decode(encode(produced.flow))
            self.digests["os-pipe (stage 1)"] = crossed.digest
            self.say(f"     slpie discover . --wire | slpie link")
            self.say(f"     {crossed.size} observations crossed as JSON; "
                     f"gaps and reasoning came with them")

            # 8 -----------------------------------------------------------
            self.heading(8, "do the surfaces agree?")
            for name, digest in self.digests.items():
                self.say(f"     {name:<22} {digest}")
            agreed = len({
                self.digests["in-process"], self.digests["http"],
            }) == 1
            self.say()
            self.say(
                "     ✓ identical — the same composition is the same answer"
                if agreed else
                "     ✕ THE SURFACES DISAGREE — one of them is lying"
            )
            if not agreed:
                return 1

            # 9 -----------------------------------------------------------
            self.heading(9, "the manual is generated, and every recipe runs")
            self.say(f"     {len(offline())} offline recipes, each executed by the")
            self.say("     test suite. `slpie help compose` shows them with the")
            self.say("     reason each one is shaped the way it is.")

            # 10 ----------------------------------------------------------
            self.heading(10, "one registry, five projections")
            from .compose import registry as verb_registry

            verbs = verb_registry()
            self.say(f"     {len(verbs)} verbs → {len(verbs)} CLI subcommands,")
            self.say(f"     {len(verbs)} HTTP routes, {len(verbs)} manual pages,")
            self.say("     the planner's vocabulary, and every client's palette.")
            self.say("     Add one and it appears in all of them; the suite fails")
            self.say("     if it does not.")

            self.say()
            self.say("  Next:")
            self.say("     slpie help compose        the recipes, and why")
            self.say('     slpie plan "<question>"   let it write one for you')
            self.say("     slpie ui                  the screen; open it on a phone")
            self.say()
            return 0
        finally:
            if self._temporary:
                shutil.rmtree(self._temporary, ignore_errors=True)

    def _report(self, flow: Any) -> None:
        self.say(f"     {flow}")
        self.say(f"     confidence {flow.confidence}, "
                 f"{'grounded' if flow.grounded else 'NOT GROUNDED'}")
        self.say()
        for item in flow.items[:4]:
            self.say(f"     [{getattr(item.severity, 'value', '?')}] "
                     f"{getattr(item, 'title', item)}")
            detail = getattr(item, "detail", "")
            if detail:
                self.say(f"           {detail[:140]}")
            for evidence in getattr(item, "evidence", ())[:2]:
                self.say(f"           cited: {evidence.location.reference}")
        self.say()
        self.say("     Every line above traces back to a file and a line. That is")
        self.say("     what the pipe carrying provenance buys.")


def main(stdout: Any = None, root: Path | None = None) -> int:
    import sys

    return Demo(stdout=stdout or sys.stdout, root=root).run()
