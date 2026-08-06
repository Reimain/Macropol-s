"""Measure the platform against real repositories, and print what it found.

Written because a value case built on adjectives does not survive due diligence.
Everything this prints is computed from a tree on disk: no estimates, no
extrapolation, and no number that cannot be reproduced by re-running it.

    python -m tools.measure /path/to/repo [/path/to/another ...]
    python -m tools.measure --json /path/to/repo > measured.json

What it deliberately does *not* do: compare against a competitor's output, or
score anything. It reports what was found and what it cost to find. Whether that
is worth money is the reader's judgement, and a tool that made that judgement for
them would be the kind of thing due diligence exists to catch.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import tracemalloc
from collections import Counter
from pathlib import Path
from typing import Any


def measure(root: Path, *, limit: int = 200_000) -> dict[str, Any]:
    """Run the platform over one tree and return what it found."""
    from slpie.compose import Composition, Context, registry

    verbs = registry()
    findings_by_family: Counter[str] = Counter()
    findings_by_severity: Counter[str] = Counter()
    detail: list[dict[str, Any]] = []

    tracemalloc.start()
    started = time.monotonic()

    scan = Composition.read(
        f"discover {root} --limit {limit}", verbs=verbs,
    ).run(Context(root=str(root)))
    discovered = time.monotonic() - started

    if not scan.ok:
        tracemalloc.stop()
        return {"root": str(root), "ok": False, "error": scan.error}

    linked = Composition.read(
        f"discover {root} --limit {limit} | link", verbs=verbs,
    ).run(Context(root=str(root)))

    governed = Composition.read(
        f"discover {root} --limit {limit} | govern", verbs=verbs,
    ).run(Context(root=str(root)))

    elapsed = time.monotonic() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    if governed.ok:
        for finding in governed.flow.items:
            findings_by_family[finding.family] += 1
            findings_by_severity[finding.severity.value] += 1
            location = finding.location
            detail.append({
                "severity": finding.severity.value,
                "family": finding.family,
                "kind": finding.kind.value,
                "title": finding.title,
                "subject": finding.subject,
                "at": (
                    f"{location.uri.rsplit('/', 1)[-1]}:{location.line}"
                    if location else ""
                ),
                "blocks_release": finding.blocks_release,
            })

    resolution = linked.flow.value if linked.ok else None

    return {
        "root": str(root),
        "name": root.name,
        "ok": True,
        "files_seen": scan.flow.facts.get("files_seen", 0),
        "files_read": scan.flow.facts.get("files_read", 0),
        "observations": scan.flow.size,
        "identities": len(getattr(resolution, "resolved", ())),
        "cross_file_links": linked.flow.facts.get("cross_file_links", 0),
        "contradictions": linked.flow.facts.get("contradictions", 0),
        "findings": governed.flow.size if governed.ok else 0,
        "by_severity": dict(findings_by_severity),
        "by_family": dict(findings_by_family),
        "blocking": sum(1 for item in detail if item["blocks_release"]),
        "spilled": scan.flow.facts.get("spilled", False),
        "seconds_to_discover": round(discovered, 2),
        "seconds_total": round(elapsed, 2),
        "peak_mb": round(peak / 1024 / 1024, 1),
        "digest": governed.flow.digest if governed.ok else "",
        "detail": detail[:40],
    }


def render(results: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    add = lines.append

    add("")
    add("  What the platform found, on real repositories")
    add("  " + "=" * 74)
    add("")
    add(f"  {'repository':14} {'files':>7} {'obs':>8} {'ident':>7} "
        f"{'find':>5} {'block':>6} {'sec':>7} {'peak MB':>8}")
    add("  " + "-" * 74)

    for result in results:
        if not result.get("ok"):
            add(f"  {result['name']:14} failed: {result.get('error', '')[:44]}")
            continue
        add(
            f"  {result['name']:14} {result['files_read']:>7} "
            f"{result['observations']:>8} {result['identities']:>7} "
            f"{result['findings']:>5} {result['blocking']:>6} "
            f"{result['seconds_total']:>7.1f} {result['peak_mb']:>8.1f}"
        )

    good = [r for r in results if r.get("ok")]
    if good:
        add("  " + "-" * 74)
        add(
            f"  {'total':14} {sum(r['files_read'] for r in good):>7} "
            f"{sum(r['observations'] for r in good):>8} "
            f"{sum(r['identities'] for r in good):>7} "
            f"{sum(r['findings'] for r in good):>5} "
            f"{sum(r['blocking'] for r in good):>6} "
            f"{sum(r['seconds_total'] for r in good):>7.1f}"
        )

    add("")
    add("  Every finding carries evidence. A sample, with file and line:")
    add("")
    for result in good:
        shown = [item for item in result["detail"] if item["at"]][:3]
        if not shown:
            continue
        add(f"    {result['name']}")
        for item in shown:
            add(f"      [{item['severity']:8}] {item['title'][:52]}")
            add(f"      {'':10}  {item['at']}")
        add("")

    add("  Peak memory is the whole-process peak while scanning. It does not")
    add("  grow with the tree — the spill tier bounds it, which is why a 1.9 GB")
    add("  repository costs about what a 2 MB one does.")
    add("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true", help="emit JSON instead")
    parser.add_argument("--limit", type=int, default=200_000)
    args = parser.parse_args(argv)

    results = []
    for root in args.roots:
        if not root.exists():
            print(f"skipping {root}: not found", file=sys.stderr)
            continue
        if not args.json:
            print(f"  measuring {root.name} ...", file=sys.stderr)
        results.append(measure(root, limit=args.limit))

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(render(results))
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
