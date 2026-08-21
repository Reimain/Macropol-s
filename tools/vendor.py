"""Vendoring, made reproducible and recorded.

A third-party file that appears in a repository with no provenance is a liability
whatever its licence: nobody can say where it came from, whether it was edited on
the way in, or what it would take to move to the next version. So vendoring here
is a *declaration* (`tools/vendored.json`) plus a tool that either fetches what
the declaration names or verifies what is already committed against it.

Two modes, and the second is the one CI runs::

    python -m tools.vendor            # fetch, write, record the digests
    python -m tools.vendor --check    # verify the committed bytes, no network

`--check` is what stops a vendored file drifting. A digest that moved is either a
corrupted checkout or somebody editing a third party's source in place, and both
have to be loud — the second especially, because an edited vendored file is a
fork nobody declared and it silently blocks the next upgrade.

**The fetch reuses `gratimos/crawl` rather than reaching for `urllib`.** That
package already does polite fetching over the official registry APIs, with
robots handling, rate limiting, retries and a cache, and it is the door this
repository has already decided outward traffic goes through. A second HTTP path
in `tools/` would be a second set of manners to keep in step.

This is tooling, not the kernel: it is never imported by `slpie/` and never runs
at request time. Invariant 4 is about what a clean `pip install -e .` pulls in,
and this pulls in nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
DECLARATION = ROOT / "tools" / "vendored.json"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load() -> dict[str, Any]:
    return json.loads(DECLARATION.read_text(encoding="utf-8"))


def save(document: Mapping[str, Any]) -> None:
    DECLARATION.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )


# -- checking, which needs no network -----------------------------------------


def check(document: Mapping[str, Any]) -> list[str]:
    """Every declared file, present and byte-identical to what was recorded."""
    problems: list[str] = []
    for package in document["packages"]:
        destination = ROOT / package["destination"]
        for entry in package["files"]:
            target = destination / entry["to"]
            if not entry["sha256"]:
                problems.append(
                    f"{package['name']}/{entry['to']}: no digest recorded — run "
                    f"`python -m tools.vendor` to fetch and record it"
                )
                continue
            if not target.is_file():
                problems.append(f"{target.relative_to(ROOT)} is declared and missing")
                continue
            found = digest(target.read_bytes())
            if found != entry["sha256"]:
                problems.append(
                    f"{target.relative_to(ROOT)} does not match its recorded digest\n"
                    f"    declared {entry['sha256']}\n"
                    f"    found    {found}\n"
                    f"    a vendored file was edited in place, which is a fork "
                    f"nobody declared"
                )
    return problems


# -- fetching ------------------------------------------------------------------


def tarball(package: Mapping[str, Any]) -> bytes:
    """The published archive for one package version, through the polite door."""
    from gratimos.crawl.fetch import Fetcher
    from gratimos.crawl.policy import CrawlPolicy

    name = package["name"]
    registry = package["registry"].rstrip("/")
    # The registry's own tarball path. Scoped packages put the unscoped name in
    # the filename, which is npm's convention rather than something to derive.
    stem = name.split("/")[-1]
    url = f"{registry}/{name}/-/{stem}-{package['version']}.tgz"

    fetcher = Fetcher(CrawlPolicy(
        user_agent=(
            "slpie-vendor (+https://github.com/Reimain/Macropol-s) "
            "one-off vendoring fetch"
        ),
        # A published bundle is larger than a page. The default is sized for
        # crawling documents, and raising it here rather than globally keeps the
        # crawler's own manners unchanged.
        max_bytes=32 * 1024 * 1024,
        timeout=120.0,
    ))
    response = fetcher.get(url)
    if not response.ok:
        raise SystemExit(f"{url} answered {response.status}")
    return response.body


def extract(archive: bytes, wanted: list[str]) -> dict[str, bytes]:
    """Pull exactly the named members out. Never `extractall`.

    A tar archive can name `../` and absolute paths, and `extractall` will
    happily write them. Reading named members by hand is both the safe form and
    the honest one: what lands in the tree is what the declaration listed, and
    nothing else can arrive because the archive asked nicely.
    """
    found: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for name in wanted:
            try:
                member = tar.getmember(name)
            except KeyError:
                raise SystemExit(f"the archive does not contain {name!r}")
            if not member.isfile():
                raise SystemExit(f"{name!r} is not a regular file")
            handle = tar.extractfile(member)
            if handle is None:
                raise SystemExit(f"{name!r} could not be read")
            found[name] = handle.read()
    return found


def fetch(document: dict[str, Any]) -> list[str]:
    written: list[str] = []
    for package in document["packages"]:
        destination = ROOT / package["destination"]
        destination.mkdir(parents=True, exist_ok=True)

        archive = tarball(package)
        members = extract(archive, [entry["from"] for entry in package["files"]])

        for entry in package["files"]:
            body = members[entry["from"]]
            target = destination / entry["to"]
            target.write_bytes(body)
            entry["sha256"] = digest(body)
            entry["bytes"] = len(body)
            written.append(
                f"{target.relative_to(ROOT)}  {len(body):,} bytes  {entry['sha256'][:16]}…"
            )
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.vendor",
        description="Fetch or verify the third-party files declared in tools/vendored.json.",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="verify the committed files against their recorded digests; no network",
    )
    arguments = parser.parse_args(argv)
    document = load()

    if arguments.check:
        problems = check(document)
        for problem in problems:
            print(problem, file=sys.stderr)
        if problems:
            print(f"\n{len(problems)} vendored file(s) do not match the declaration.",
                  file=sys.stderr)
            return 1
        total = sum(len(package["files"]) for package in document["packages"])
        print(f"{total} vendored file(s) match tools/vendored.json")
        return 0

    for line in fetch(document):
        print(f"  {line}")
    save(document)
    print(f"recorded in {DECLARATION.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
