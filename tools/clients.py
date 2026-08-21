"""Regenerate the committed clients, and fail when they have drifted.

    python -m tools.clients            # rewrite them
    python -m tools.clients --check    # exit 1 if any is stale

`clients/` held a TypeScript client covering 25 of 48 verbs and an OpenAPI
document with 52 of 77 paths. Nothing failed, because **no test read those
files**. They were generated once, committed, and then the registry moved on
without them for three phases.

Regenerating them is not the fix — they would be stale again by the next verb.
The fix is `--check`, wired into the suite, so drift is a red test on the commit
that causes it rather than a discovery somebody makes later. This is deliberately
the same shape as `tools/notebooks/build.py`, for the same reason.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIENTS = ROOT / "clients"

#: One generated client, three shells. They are byte-identical on purpose: the
#: web, desktop and mobile clients differ in their shell, never in how they talk
#: to the platform, and a per-shell client would be three things to keep current.
SHELLS = ("web", "desktop", "mobile")


def targets() -> dict[Path, str]:
    """Every committed artifact, mapped to what the generator says it should be."""
    from slpie.compose import registry
    from slpie.ui.api import Api
    from slpie.ui.contract import javascript, openapi, typescript
    from slpie.ui.server import APP_ROOT

    verbs = registry()
    routes = Api(engine=None).routes

    wanted = {
        CLIENTS / "openapi.json":
            json.dumps(openapi(verbs=verbs, routes=routes), indent=2) + "\n",
        # The interface's own client. Committed rather than served from a route
        # because the service worker precaches the shell, and a module generated
        # at request time cannot boot with the network unplugged.
        APP_ROOT / "data" / "client.js": javascript(verbs=verbs, routes=routes),
    }
    client = typescript(verbs=verbs, routes=routes)
    for shell in SHELLS:
        wanted[CLIENTS / shell / "src" / "slpie-client.ts"] = client
    return wanted


def build(*, check: bool = False) -> int:
    stale: list[str] = []
    wanted = targets()

    for path in sorted(wanted):
        body = wanted[path]
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        relative = path.relative_to(ROOT)
        if current == body:
            if not check:
                print(f"  {str(relative):40} unchanged")
            continue
        if check:
            stale.append(str(relative))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        print(f"  {str(relative):40} rewritten ({len(body):,} bytes)")

    if check and stale:
        print(
            "these committed clients no longer match the registry — "
            "run `python -m tools.clients`:",
            file=sys.stderr,
        )
        for name in stale:
            print(f"  {name}", file=sys.stderr)
        return 1
    if check:
        print(f"{len(wanted)} committed clients are current with the registry")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="do not write; exit 1 if any committed client has drifted",
    )
    return build(check=parser.parse_args(argv).check)


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
