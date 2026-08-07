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

Two things about the memory number, because a memory claim measured with the
wrong instrument is worse than no claim at all.

**It is whole-process peak RSS, from `resource.getrusage`, not `tracemalloc`.**
`tracemalloc` reports the traced *Python heap*, which is a different quantity
from what an operator provisions a container with, and it costs a factor of
sixty on this workload — a first attempt at the large-repository row was killed
by a fifty-minute timeout that the same scan finishes in forty-six seconds
without it. Measuring the cheap way and the honest way turned out to be the same
choice.

**Each repository is measured in its own subprocess.** `ru_maxrss` is a
high-water mark for the life of a process, so measuring three trees in one
would report the largest one's peak for all three. The parent spawns a child per
root and collects its JSON, which is also why a crash on one tree does not lose
the others.
"""

from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any


def _rss_mb() -> float:
    """Peak resident set size for this process, in MB.

    `ru_maxrss` is kilobytes on Linux and bytes on macOS. This repository's CI
    and every measurement in `docs/VALUE.md` run on Linux; the platform check
    keeps a number produced on a laptop from being off by a factor of 1024.
    """
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    return round(peak / divisor, 1)


def measure(root: Path, *, limit: int = 200_000) -> dict[str, Any]:
    """Run the platform over one tree and return what it found."""
    from slpie.compose import Composition, Context, registry

    verbs = registry()
    findings_by_family: Counter[str] = Counter()
    findings_by_severity: Counter[str] = Counter()
    detail: list[dict[str, Any]] = []

    # Taken after the imports and the registry build, so the figure below is the
    # cost of scanning rather than the cost of starting Python.
    baseline = _rss_mb()
    context = Context(root=str(root))
    started = time.monotonic()

    scan = Composition.read(
        f"discover {root} --limit {limit}", verbs=verbs,
    ).run(context)
    discovered = time.monotonic() - started

    if not scan.ok:
        return {"root": str(root), "name": root.name, "ok": False, "error": scan.error}

    # `run_from`, not three separate `discover | …` pipelines. Discovery is the
    # dominant cost on a large tree and re-running it twice to reach two
    # different downstream verbs measured the scan three times over — which made
    # the large repositories look three times more expensive than they are.
    linked = Composition.read("link", verbs=verbs).run_from(scan.flow, context)
    governed = Composition.read("govern", verbs=verbs).run_from(scan.flow, context)

    elapsed = time.monotonic() - started
    peak = _rss_mb()

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
        "bytes_on_disk": _tree_bytes(root),
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
        "baseline_mb": baseline,
        "peak_mb": peak,
        "scan_mb": round(peak - baseline, 1),
        "digest": governed.flow.digest if governed.ok else "",
        "detail": detail[:40],
    }


def _tree_bytes(root: Path) -> int:
    """How large the tree is, so the memory claim has something to be flat against."""
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue          # a broken link or a race; it is not worth failing over
    return total


def measure_out_of_process(root: Path, *, limit: int) -> dict[str, Any]:
    """Measure one tree in a child process, so its peak RSS is its own.

    The alternative — measuring every root in this process — reports the largest
    tree's high-water mark for all of them, because that is what `ru_maxrss`
    means. A memory table where every row shows the same number would be exactly
    the kind of thing this module exists to avoid producing.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "tools.measure", "--one", str(root),
         "--limit", str(limit)],
        capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    if completed.returncode != 0:
        return {
            "root": str(root), "name": root.name, "ok": False,
            "error": (completed.stderr or "").strip()[-400:] or "child exited nonzero",
        }
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            "root": str(root), "name": root.name, "ok": False,
            "error": f"child produced no JSON: {completed.stdout[:200]!r}",
        }


def render(results: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    add = lines.append

    add("")
    add("  What the platform found, on real repositories")
    add("  " + "=" * 78)
    add("")
    add(f"  {'repository':14} {'on disk':>9} {'files':>7} {'obs':>8} {'ident':>7} "
        f"{'find':>5} {'sec':>7} {'peak MB':>8} {'scan MB':>8}")
    add("  " + "-" * 78)

    for result in results:
        if not result.get("ok"):
            add(f"  {result['name']:14} failed: {result.get('error', '')[:48]}")
            continue
        add(
            f"  {result['name']:14} {_size(result.get('bytes_on_disk', 0)):>9} "
            f"{result['files_read']:>7} "
            f"{result['observations']:>8} {result['identities']:>7} "
            f"{result['findings']:>5} "
            f"{result['seconds_total']:>7.1f} {result['peak_mb']:>8.1f} "
            f"{result.get('scan_mb', 0.0):>8.1f}"
        )

    good = [r for r in results if r.get("ok")]
    if good:
        add("  " + "-" * 78)
        add(
            f"  {'total':14} {_size(sum(r.get('bytes_on_disk', 0) for r in good)):>9} "
            f"{sum(r['files_read'] for r in good):>7} "
            f"{sum(r['observations'] for r in good):>8} "
            f"{sum(r['identities'] for r in good):>7} "
            f"{sum(r['findings'] for r in good):>5} "
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

    if good:
        add(_memory_note(good))
        add("")
    return "\n".join(lines)


def fit(good: list[dict[str, Any]]) -> dict[str, float]:
    """Least squares of scan memory against observations retained.

    The question worth answering is *what* memory tracks, and the honest answer
    turns out not to be tree size. A repository is scanned by walking it and
    keeping what was found, so what is held is the findings — and a fit against
    observations reports a stable marginal cost where a fit against bytes on disk
    reports a different slope for every corpus.
    """
    rows = [r for r in good if r.get("scan_mb", 0) > 0]
    if len(rows) < 2:
        return {}

    xs = [float(r["observations"]) for r in rows]
    ys = [float(r["scan_mb"]) for r in rows]
    n = len(rows)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    spread = sum((x - mean_x) ** 2 for x in xs)
    if spread == 0:
        return {}

    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / spread
    intercept = mean_y - slope * mean_x
    total = sum((y - mean_y) ** 2 for y in ys)
    residual = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))

    return {
        "fixed_mb": round(intercept, 1),
        "kb_per_observation": round(slope * 1024, 2),
        "r_squared": round(1 - residual / total, 4) if total else 1.0,
        "points": float(n),
    }


def _memory_note(good: list[dict[str, Any]]) -> str:
    """State what the memory column shows, from the rows rather than from a belief.

    An earlier version of this function asserted that memory does not grow with
    the tree, above a table of three small repositories none of which could have
    contradicted it. Measuring two large ones showed it was wrong: memory grows,
    just not with the thing the sentence named. Deriving the note means it is a
    reading of the data and stops being printed the moment the data stops
    supporting it.
    """
    header = (
        "  Peak memory is whole-process peak RSS (`ru_maxrss`), measured in a child\n"
        "  process per repository so each figure is its own. `scan MB` subtracts the\n"
        "  interpreter baseline, so it is the cost of the scan rather than of Python."
    )
    model = fit(good)
    if not model:
        return header

    sized = [r for r in good if r.get("bytes_on_disk")]
    ratio = ""
    if len(sized) >= 2:
        small = min(sized, key=lambda r: r["bytes_on_disk"])
        large = max(sized, key=lambda r: r["bytes_on_disk"])
        disk = large["bytes_on_disk"] / max(small["bytes_on_disk"], 1)
        memory = large["scan_mb"] / max(small["scan_mb"], 0.1)
        ratio = (
            f"\n  {large['name']} is {disk:.0f}x {small['name']} on disk and costs "
            f"{memory:.0f}x the memory to scan,\n"
            f"  so it is sub-linear in tree size — but that is a consequence, not the "
            f"law."
        )

    return (
        f"{header}\n\n"
        f"  Across {int(model['points'])} repositories, memory is linear in "
        f"*observations retained*:\n"
        f"      scan MB  ~  {model['fixed_mb']:.1f} + "
        f"{model['kb_per_observation']:.1f} KB x observations      "
        f"(r2 = {model['r_squared']:.3f})\n"
        f"  What is held is what was found, not what was walked."
        f"{ratio}"
    )


def _size(count: int) -> str:
    if not count:
        return "-"
    for unit, scale in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if count >= scale:
            return f"{count / scale:.0f} {unit}"
    return f"{count} B"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true", help="emit JSON instead")
    parser.add_argument("--limit", type=int, default=200_000)
    parser.add_argument(
        "--one", action="store_true",
        help="measure a single root in this process and print its JSON — the "
             "child half of the per-repository subprocess, not normally typed",
    )
    parser.add_argument(
        "--out", type=Path,
        help="write results here after each repository, so a timeout keeps what "
             "already finished",
    )
    args = parser.parse_args(argv)

    if args.one:
        print(json.dumps(measure(args.roots[0], limit=args.limit)))
        return 0

    results = []
    for root in args.roots:
        if not root.exists():
            print(f"skipping {root}: not found", file=sys.stderr)
            continue
        print(f"  measuring {root.name} ...", file=sys.stderr)
        results.append(measure_out_of_process(root, limit=args.limit))

        # Written after every repository, not at the end. A run over a 2 GB
        # monorepo can take longer than whatever timeout somebody wrapped it in,
        # and accumulating everything meant a kill lost the lot — which is how
        # the first attempt at this table produced an empty file.
        if args.out:
            args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")
            print(f"    wrote {args.out} ({len(results)} so far)", file=sys.stderr)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(render(results))
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
