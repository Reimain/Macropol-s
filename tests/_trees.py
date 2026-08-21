"""Building the small npm trees the suite reasons over.

Eight test files each hand-rolled a `repository(tmp_path)` fixture writing a
`package.json` and a `package-lock.json` that disagree about a version. Four were
byte-identical; the literal ``AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"`` appeared
verbatim in three. So there were never eight trees — there were three, written
out eight times.

The builder is parameterised rather than uniform, deliberately. These trees are
not interchangeable fixtures: a test that asserts a typosquat is found needs
`lodahs`, and one that asserts a *clean* licence needs `lodash`. Forcing them to
share one shape would make each test's precondition invisible at its call site,
which is worse than the duplication. What is shared here is the *construction* —
lockfile v3 layout, the JSON shape, where a secret goes — so a change to how a
tree is written lands once.

`corpus/` (§29 stage 3) supplies the other half: these are hand-written trees
designed to contain a specific defect, and the corpus holds real third-party
manifests with no defect designed into them at all. Both are needed — the first
proves a rule fires, the second proves it does not fire on real data.
"""

from __future__ import annotations

import json
from pathlib import Path

#: A secret that is unmistakably fake. AWS publishes this exact key in its own
#: documentation as the canonical example, so a scanner finding it here is
#: finding a fixture, and nobody has to wonder whether a real credential leaked
#: into the test suite.
EXAMPLE_AWS_KEY = 'AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"\n'


def write_npm(
    root: Path,
    *,
    name: str = "demo",
    declared: dict[str, str],
    resolved: dict[str, str | dict[str, str]],
    license: str = "",
) -> Path:
    """One npm package, as a manifest and a lockfile that may disagree.

    `declared` is what `package.json` asks for; `resolved` is what the lockfile
    pinned. The disagreement between them is the point of most of these trees —
    a manifest saying `^3.0.0` over a lockfile saying `4.17.21` is the smallest
    tree that produces a real finding.

    An entry in `resolved` may be a bare version string or a dict, so a test that
    needs a licence on one package writes only that.
    """
    root.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, object] = {"name": name, "version": "1.0.0"}
    if license:
        manifest["license"] = license
    manifest["dependencies"] = declared
    (root / "package.json").write_text(
        json.dumps(manifest), encoding="utf-8",
    )

    packages = {
        f"node_modules/{package}": (
            entry if isinstance(entry, dict) else {"version": entry}
        )
        for package, entry in resolved.items()
    }
    (root / "package-lock.json").write_text(
        json.dumps({"name": name, "lockfileVersion": 3, "packages": packages}),
        encoding="utf-8",
    )
    return root


def minimal_tree(root: Path) -> Path:
    """A manifest and its lockfile, disagreeing about lodash. Nothing else.

    The smallest tree that gives `discover | link` something to join and
    `findings` something to rank. Used wherever the test is about the pipeline
    rather than about what the pipeline found.
    """
    return write_npm(
        root,
        declared={"lodash": "^3.0.0"},
        resolved={"lodash": "4.17.21"},
    )


def conflicted_tree(root: Path) -> Path:
    """Two lockfiles pinning different versions, and a range that is not one.

    `lodash: "*"` is unconstrained and `sub/package-lock.json` pins a second
    version of it, so constraint solving and duplicate detection both have
    something real to work on.
    """
    write_npm(
        root,
        declared={"lodash": "*", "left-pad": "^1.0.0"},
        resolved={"lodash": "4.17.21", "left-pad": "1.3.0"},
    )
    write_npm(
        root / "sub",
        name="sub",
        declared={},
        resolved={"lodash": "4.17.15"},
    )
    (root / "sub" / "package.json").unlink()
    return root


def unhealthy_tree(root: Path, *, secret_at: str = "settings.py") -> Path:
    """A typosquat, a copyleft licence under an MIT project, and a secret.

    Three defects from three different governance families in one tree, so a
    single scan exercises supply-chain, licence and secret rules at once. The
    typosquat is `lodahs` — a transposition of `lodash`, which is why the
    detector needs Damerau-Levenshtein rather than plain edit distance.
    """
    write_npm(
        root,
        name="shop",
        license="MIT",
        declared={"lodahs": "^4.0.0", "loose": "*"},
        resolved={
            "lodahs": {"version": "4.17.21", "license": "AGPL-3.0"},
            "loose": {"version": "0.1.0"},
        },
    )
    secret = root / secret_at
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text(EXAMPLE_AWS_KEY, encoding="utf-8")
    return root
