"""The world the demo runs against — real files, not mocks.

The same twenty-nine discoverers, the same linkers, the same reasoning layers and
the same verbs run against this as against a customer's repository. That is why a
green demo is evidence about the real code path rather than a rehearsal of one, and
it is the rule the whole test suite already follows (§19).

The manifest and the lockfile **genuinely disagree**. That disagreement is the
finding the demo exists to surface: the build is not what the manifest says it is,
and no single file could have revealed it. Choosing a world where everything is
fine would demonstrate nothing except that nothing was wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

#: Every file, and why it is here. Each one is claimed by a different discoverer,
#: so the demo exercises the registry rather than one parser.
FILES: dict[str, str] = {
    # npm: the manifest asks for ^3.0.0 …
    "package.json": json.dumps({
        "name": "storefront",
        "version": "2.1.0",
        "dependencies": {"lodash": "^3.0.0", "express": "^4.18.0"},
    }, indent=2) + "\n",
    # … and the lockfile pins 4.17.21. This is the contradiction.
    "package-lock.json": json.dumps({
        "name": "storefront",
        "lockfileVersion": 3,
        "packages": {
            "node_modules/lodash": {"version": "4.17.21"},
            "node_modules/express": {"version": "4.18.2"},
        },
    }, indent=2) + "\n",
    # pip: a second ecosystem, so identity merging has something to merge.
    "requirements.txt": "flask==3.0.0\nPyYAML==6.0.1\n",
    # A container, so the infrastructure discoverers contribute.
    "Dockerfile": "FROM python:3.11-slim\nRUN pip install flask\n",
    # Python source, so `import yaml` can be joined to the PyYAML distribution —
    # a link no single file states.
    "app/main.py": (
        "import yaml\n"
        "import flask\n"
        "\n"
        "\n"
        "def load(path: str) -> dict:\n"
        "    with open(path) as handle:\n"
        "        return yaml.safe_load(handle)\n"
    ),
}

#: What the demo should find, so a beat can assert rather than narrate.
EXPECTATIONS = {
    "contradiction": ("4.17.21", "^3.0.0"),
    "import_join": ("yaml", "pyyaml"),
    "ecosystems": ("npm", "pypi"),
}


def materialise(root: Path) -> Path:
    """Write the world. Returns the root, so callers can chain."""
    for name, content in FILES.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def describe() -> tuple[str, ...]:
    """The world, as lines a surface can show before anything runs."""
    return (
        "package.json asks for lodash ^3.0.0.",
        "package-lock.json pins 4.17.21.",
        "Nobody's build is what they think it is — and no single file says so.",
    )
