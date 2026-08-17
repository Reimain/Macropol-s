"""Run the browser core's own tests, under node.

The structural suite proves the modules load, resolve and obey the ring rule. It
cannot prove the rules *inside* them, and one of those rules is load-bearing
enough to be worth a runtime: **a late answer must never overwrite a newer one.**
Without it a console that refetches on every event paints yesterday's graph the
moment two responses cross in flight, and there is nothing on screen to say so.

Node is a developer tool here, not a dependency. The kernel installs and runs
with none of it; this skips when node is absent, the same treatment §27 gives a
missing dispatch binary — and the skip is loud rather than silent, so nobody
mistakes "not run" for "passed".
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SUITE = Path(__file__).resolve().parent / "test_slpie_ui_core.js"


def test_the_browser_core_passes_its_own_tests():
    node = shutil.which("node")
    if not node:  # pragma: no cover - depends on the machine
        pytest.skip("node is not installed; the browser core cannot be run here")

    assert SUITE.is_file(), "the JavaScript suite is missing"

    done = subprocess.run(
        [node, str(SUITE)],
        capture_output=True,
        text=True,
        cwd=SUITE.parent,
        timeout=60,
    )
    output = done.stdout + done.stderr

    assert done.returncode == 0, f"the browser core failed its own tests:\n{output}"
    # The guard against a suite that silently stops running anything: it prints
    # its own count, and a zero would otherwise look exactly like success.
    assert "checks" in output, f"the suite reported no count at all:\n{output}"
    ran = int(output.rsplit("\n", 2)[-2].split()[0])
    assert ran >= 15, f"only {ran} checks ran; the suite has shrunk"
