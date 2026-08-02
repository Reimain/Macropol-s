"""Shared fixtures.

What lives here is what more than one test file needs. Until §29 that was two
fixtures out of seventy, and neither was used by any of the twenty-four
slpie-facing files — so eight of them each carried their own copy of the same
npm tree and the same `registry()` call. The construction now lives in
`_trees.py` and the fixtures in this file; a test that needs a differently
shaped tree still writes one, but it starts from a builder rather than from
another paste of `lockfileVersion: 3`.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from _trees import conflicted_tree, minimal_tree, unhealthy_tree  # noqa: E402
from fixtures import build_environment  # noqa: E402


@pytest.fixture
def environment(tmp_path: Path) -> Path:
    """A synthetic environment containing every format the probes cover."""
    return build_environment(tmp_path / "env")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path / "workspace"


# --- the verb registry ----------------------------------------------------


@pytest.fixture(scope="session")
def verbs():
    """This build's verbs.

    Session-scoped because `slpie.compose.registry()` is a process-wide
    singleton — the eight copies of this fixture that used to exist returned the
    identical object whatever scope they declared, so the narrower scopes bought
    nothing. A test that registers its own verb calls `compose.registry.reset()`
    and is responsible for restoring it.
    """
    from slpie.compose import registry

    return registry()


# --- trees to reason over -------------------------------------------------


@pytest.fixture()
def repository(tmp_path: Path) -> Path:
    """A manifest and its lockfile, disagreeing about lodash.

    The default tree: enough for `discover | link | findings` to have something
    real to do, and small enough that a test asserting on the whole result stays
    readable.
    """
    return minimal_tree(tmp_path)


@pytest.fixture()
def conflicted_repository(tmp_path: Path) -> Path:
    """Two lockfiles pinning different lodash versions, plus a `*` range."""
    return conflicted_tree(tmp_path)


@pytest.fixture()
def unhealthy_repository(tmp_path: Path) -> Path:
    """A typosquat, a copyleft licence under MIT, and a secret in a config."""
    return unhealthy_tree(tmp_path)


# --- running things -------------------------------------------------------


@pytest.fixture()
def run(verbs):
    """Run a pipeline against a root. The shape most integration tests want.

    Returns the `Result`, not the `Flow`, because a test that only ever looked at
    `.flow` would pass on a composition that failed at stage two — `Result.ok`
    is the thing worth asserting first.
    """
    from slpie.compose import Composition, Context

    def go(pipeline: str, root: Path | str = ".", **options):
        return Composition.read(pipeline, verbs=verbs).run(
            Context(root=str(root), **options),
        )

    return go


@pytest.fixture()
def cli():
    """A CLI harness with its streams captured.

    In-process rather than a subprocess: `Cli` takes its streams as arguments
    precisely so the exit code and the output can be asserted without paying for
    an interpreter start per case.
    """
    from slpie.cli import Cli

    def go(argv, stdin: str = ""):
        out, err = io.StringIO(), io.StringIO()
        code = Cli(
            stdout=out, stderr=err, stdin=io.StringIO(stdin), isatty=False,
        ).main(list(argv))
        return code, out.getvalue(), err.getvalue()

    return go
