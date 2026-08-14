"""Command line interface.

Deliberately thin: every command is a few lines over the same public API a
library caller would use. If something is only reachable from the CLI, that is
a bug in the library.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .orchestrator import DEEP, STANDARD, SURVEY, Budget, Depth, Governor

_BUDGETS = {"survey": SURVEY, "standard": STANDARD, "deep": DEEP}


def _emit(value: Any, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2, default=str))
    elif isinstance(value, str):
        print(value)
    else:
        print(json.dumps(value, indent=2, default=str))


def _governor(args: argparse.Namespace) -> Governor:
    return Governor(
        args.root,
        args.workspace,
        depth=Depth.parse(args.depth),
        budget=_BUDGETS.get(args.budget, STANDARD),
    )


# --- commands ------------------------------------------------------------


def cmd_govern(args: argparse.Namespace) -> int:
    """Run the kernel over an environment."""
    with _governor(args) as governor:
        report = governor.run(cycles=args.cycles)
        _emit(report.to_dict() if args.json else report.summary(), as_json=args.json)
        return 0 if not any(c.rolled_back for c in report.cycles) else 1


def cmd_scan(args: argparse.Namespace) -> int:
    """Discover and shape, changing nothing."""
    args.depth = "sense"
    with _governor(args) as governor:
        report = governor.run(cycles=1)
        if args.json:
            _emit({"run": report.to_dict(), "catalog": governor.hub.meta.catalog()}, as_json=True)
            return 0
        catalog = governor.hub.meta.catalog()
        if not catalog:
            print(f"nothing readable under {args.root}")
            return 0
        width = max(len(name) for name in catalog)
        print(f"{'entity'.ljust(width)}  rows  fields  shape")
        for name, info in catalog.items():
            fields = ", ".join(f"{k}:{v}" for k, v in list(info["field_types"].items())[:5])
            more = "…" if len(info["field_types"]) > 5 else ""
            print(f"{name.ljust(width)}  {info['rows_observed']:>4}  {info['fields']:>6}  {fields}{more}")
        return 0


def cmd_shapes(args: argparse.Namespace) -> int:
    """Print the full inferred shape of one entity, or all of them."""
    args.depth = "sense"
    with _governor(args) as governor:
        governor.run(cycles=1)
        if args.entity:
            shape = governor.hub.shape(args.entity)
            if shape is None:
                print(f"unknown entity {args.entity!r}; known: {governor.hub.entities()}", file=sys.stderr)
                return 2
            _emit(shape.to_dict(), as_json=True)
            return 0
        _emit({e: governor.hub.shape(e).to_dict() for e in governor.hub.entities()}, as_json=True)
        return 0


def cmd_trace(args: argparse.Namespace) -> int:
    """Summarize or tail a run's journal."""
    from .trace import Journal

    path = Path(args.workspace) / "trace.jsonl"
    if not path.exists():
        print(f"no trace at {path}", file=sys.stderr)
        return 2
    journal = Journal(path, autoflush=False)
    if args.summary:
        _emit(journal.summary(), as_json=True)
        return 0
    for record in journal.tail(args.count):
        event = record.event
        if args.json:
            _emit(event.to_dict(), as_json=True)
        else:
            print(f"{event.at}  {event}")
    return 0


def cmd_transforms(args: argparse.Namespace) -> int:
    """Scaffold, list, or statically check transformations."""
    from .transforms import TransformRegistry, scaffold

    root = Path(args.workspace) / "transformations"
    if args.action == "new":
        path = scaffold(root, args.name, applies_to=args.applies_to)
        print(f"wrote {path}")
        return 0

    registry = TransformRegistry(root)
    found = registry.discover()
    if args.json:
        _emit([t.to_dict() for t in found], as_json=True)
        return 0
    if not found:
        print(f"no transformations in {root}")
        return 0
    for transformation in found:
        mark = "ok " if transformation.approved else "REJECTED"
        print(f"{mark}  {transformation.name:<24} applies_to={transformation.applies_to!r}")
        for reason in transformation.inspection.reasons:
            print(f"          {reason}")
    return 0 if not registry.rejected() else 1


def cmd_connectors(_: argparse.Namespace) -> int:
    """Show which storage backends are reachable in this environment."""
    from .storage import REGISTRY

    for spec in REGISTRY.catalog():
        state = "available" if spec["available"] else f"needs {', '.join(spec['missing'])}"
        print(f"{spec['scheme']:<6} {spec['tier']:<5} {state:<32} {spec['description']}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Expose this kernel as an A2A agent over HTTP."""
    import time

    from .a2a import AgentServer, AgentSkill, build_card, serve_http

    with _governor(args) as governor:
        governor.run(cycles=1)

        def executor(context: Any) -> None:
            question = context.text.lower()
            catalog = governor.hub.meta.catalog()
            if "shape" in question or "schema" in question or "catalog" in question:
                context.artifact("catalog", catalog)
                context.complete(f"{len(catalog)} entities: {', '.join(catalog) or 'none'}")
                return
            context.artifact("report", governor.report())
            context.complete("returned the full kernel report as an artifact")

        card = build_card(
            f"gratimos:{governor.root.name}",
            description=f"Gratimos kernel governing {governor.root}",
            url=f"http://{args.host}:{args.port}",
            skills=[
                AgentSkill(id="catalog", name="Describe the context",
                           description="List entities and their inferred shapes.",
                           tags=("shapes", "catalog")),
                AgentSkill(id="report", name="Full report",
                           description="Return hub, codegen, migration, and policy state.",
                           tags=("report",)),
            ],
        )
        httpd = serve_http(AgentServer(card, executor, flow=governor.flow), args.host, args.port)
        url = httpd.gratimos_url  # type: ignore[attr-defined]
        print(f"serving A2A on {url}  (card at {url}/.well-known/agent-card.json)")
        print("ctrl-c to stop")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            httpd.shutdown()
            print("\nstopped")
        return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Run and dump the complete kernel state."""
    with _governor(args) as governor:
        governor.run(cycles=args.cycles)
        _emit(governor.report(), as_json=True)
        return 0


# --- parser --------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gratimos",
        description="A self-building agent kernel: read an environment, shape it, generate against it.",
    )
    parser.add_argument("--version", action="version", version=f"gratimos {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def with_common(sub: argparse.ArgumentParser, *, depth: str = "generate") -> argparse.ArgumentParser:
        sub.add_argument("root", help="directory to govern")
        sub.add_argument("-w", "--workspace", default=None,
                         help="where to write generated code and traces (default: <root>/.gratimos)")
        sub.add_argument("-d", "--depth", default=depth,
                         help="sense | model | generate | transform | govern")
        sub.add_argument("-b", "--budget", default="standard", choices=sorted(_BUDGETS),
                         help="survey | standard | deep")
        sub.add_argument("-c", "--cycles", type=int, default=0, help="override the cycle budget")
        sub.add_argument("--json", action="store_true", help="emit JSON")
        return sub

    govern = with_common(subparsers.add_parser("govern", help="run the kernel over an environment"))
    govern.set_defaults(func=cmd_govern)

    scan = subparsers.add_parser("scan", help="discover and shape without changing anything")
    scan.add_argument("root")
    scan.add_argument("-w", "--workspace", default=None)
    scan.add_argument("-b", "--budget", default="survey", choices=sorted(_BUDGETS))
    scan.add_argument("--json", action="store_true")
    scan.set_defaults(func=cmd_scan, cycles=1)

    shapes = subparsers.add_parser("shapes", help="print inferred shapes")
    shapes.add_argument("root")
    shapes.add_argument("entity", nargs="?", default="")
    shapes.add_argument("-w", "--workspace", default=None)
    shapes.add_argument("-b", "--budget", default="survey", choices=sorted(_BUDGETS))
    shapes.set_defaults(func=cmd_shapes, cycles=1, json=True)

    trace = subparsers.add_parser("trace", help="inspect a run's journal")
    trace.add_argument("workspace")
    trace.add_argument("-n", "--count", type=int, default=20)
    trace.add_argument("-s", "--summary", action="store_true")
    trace.add_argument("--json", action="store_true")
    trace.set_defaults(func=cmd_trace)

    transforms = subparsers.add_parser("transforms", help="manage transformations")
    transforms.add_argument("action", choices=("list", "new"))
    transforms.add_argument("workspace")
    transforms.add_argument("name", nargs="?", default="new_transform")
    transforms.add_argument("--applies-to", default="*")
    transforms.add_argument("--json", action="store_true")
    transforms.set_defaults(func=cmd_transforms)

    connectors = subparsers.add_parser("connectors", help="show storage backend availability")
    connectors.set_defaults(func=cmd_connectors)

    serve = with_common(subparsers.add_parser("serve", help="expose the kernel as an A2A agent"))
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=0)
    serve.set_defaults(func=cmd_serve)

    report = with_common(subparsers.add_parser("report", help="dump full kernel state as JSON"))
    report.set_defaults(func=cmd_report)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - the CLI reports, it does not traceback
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
