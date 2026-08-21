"""Projecting the script — to a terminal now, to screens through the API.

The renderer holds no knowledge of what the demo demonstrates. It walks beats,
runs each one, and formats the outcome. Everything semantic lives in
`beats.py`, which is what lets the UI walk the identical script through its own
views without reimplementing the demo, and lets the test suite assert each beat's
claim without parsing terminal output.

`holds=False` propagates to the exit code. A demo that printed a failure and
returned success would be worse than one that did not run at all.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, Sequence

from .beats import script as default_script
from .script import Beat, Outcome, Script, Stage, Surface


class Demo:
    """Runs a script and renders it. Streams injected, so it is testable."""

    def __init__(
        self,
        *,
        stdout: Any,
        root: Path | None = None,
        script: Script | None = None,
    ) -> None:
        self.stdout = stdout
        self.root = root
        self.script = script or default_script()
        self.stage = Stage(root=root)
        self.outcomes: dict[str, Outcome] = {}
        self._temporary: str | None = None

    # -- running ---------------------------------------------------------

    def run(self) -> int:
        if self.stage.root is None:
            self._temporary = tempfile.mkdtemp(prefix="slpie-demo-")
            self.stage.root = Path(self._temporary)
        Path(self.stage.root).mkdir(parents=True, exist_ok=True)

        try:
            self._say()
            self._say(f"  {self.script.name}")
            self._say()
            for line in _wrap(self.script.thesis, 70):
                self._say(f"  {line}")

            failed = 0
            for index, beat in enumerate(self.script.beats, start=1):
                outcome = self._beat(index, beat)
                self.outcomes[beat.key] = outcome
                if not outcome.holds:
                    failed += 1

            self._footer(failed)
            return 1 if failed else 0
        finally:
            if self._temporary:
                shutil.rmtree(self._temporary, ignore_errors=True)

    def _beat(self, index: int, beat: Beat) -> Outcome:
        self._say()
        self._say(f"  {index}. {beat.title}")
        self._say(f"     {'-' * len(beat.title)}")

        if beat.call:
            self._say(f"     $ {beat.call}")
            self._say()

        outcome = beat.run(self.stage) if beat.run else Outcome()

        for line in outcome.lines:
            self._say(f"     {line}" if line else "")

        if beat.narration:
            self._say()
            for paragraph in beat.narration:
                for line in _wrap(paragraph, 66):
                    self._say(f"     {line}")

        self._say()
        mark = "✓" if outcome.holds else "✕"
        self._say(f"     {mark} {beat.claim}")
        if outcome.detail:
            self._say(f"       {outcome.detail}")
        return outcome

    def _footer(self, failed: int) -> None:
        self._say()
        if failed:
            self._say(f"  {failed} claim(s) did not hold. That is a real failure,")
            self._say("  not a rendering problem — the demo asserts what it shows.")
        else:
            self._say(f"  {len(self.script)} claims, all of them held.")
        self._say()
        self._say("  Next:")
        self._say("     slpie help compose        the recipes, and why")
        self._say('     slpie plan "<question>"   let it write one for you')
        self._say("     slpie ui                  the screen; open it on a phone")
        self._say()

    # -- output ----------------------------------------------------------

    def _say(self, text: str = "") -> None:
        self.stdout.write(text.rstrip("\n") + "\n")

    # -- the other projection --------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The script plus whatever has run, for the UI to walk."""
        body = self.script.to_dict()
        for beat in body["beats"]:
            outcome = self.outcomes.get(beat["key"])
            beat["outcome"] = outcome.to_dict() if outcome else None
        body["held"] = all(item.holds for item in self.outcomes.values())
        return body


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(" ".join(text.split()), width=width) or [""]


def render(*, stdout: Any, root: Path | None = None) -> int:
    return Demo(stdout=stdout, root=root).run()


def run(root: Path | None = None) -> tuple[int, dict[str, Any]]:
    """Run headlessly and return the structured result. What the API serves."""
    import io

    demo = Demo(stdout=io.StringIO(), root=root)
    code = demo.run()
    return code, demo.to_dict()


def main(stdout: Any = None, root: Path | None = None) -> int:
    import sys

    return Demo(stdout=stdout or sys.stdout, root=root).run()
