"""The whole thing: point it at an environment and check what it built."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from gratimos import Depth, Governor, govern
from gratimos.cli import main
from gratimos.orchestrator import SURVEY, Budget

TIDY = textwrap.dedent('''
    NAME = "tidy_orders"
    APPLIES_TO = "orders"
    def transform(records, context):
        out = []
        for row in records:
            row = dict(row)
            row["customer"] = str(row["customer"]).strip().title()
            row["is_large"] = float(row["total"]) > 100
            out.append(row)
        context["log"]("tidied", len(out))
        return out
''')


def test_depth_parses_from_every_form():
    assert Depth.parse("generate") is Depth.GENERATE
    assert Depth.parse("GOVERN") is Depth.GOVERN
    assert Depth.parse(2) is Depth.GENERATE
    assert Depth.parse("2") is Depth.GENERATE
    assert Depth.parse(99) is Depth.GOVERN
    with pytest.raises(ValueError, match="unknown depth"):
        Depth.parse("nonsense")


def test_depth_levels_contain_the_ones_below():
    assert Depth.GOVERN.allows(Depth.SENSE)
    assert not Depth.SENSE.allows(Depth.GENERATE)


def test_sense_depth_reads_without_writing_code(environment: Path, workspace: Path):
    with Governor(environment, workspace, depth=Depth.SENSE, budget=SURVEY) as governor:
        report = governor.run()
        assert report.cycles[0].discovered > 0
        assert governor.hub.entities()
        assert not report.cycles[0].generated
        assert not list(governor.workspace.generated.glob("*.py")) or \
            [p.name for p in governor.workspace.generated.glob("*.py")] == ["__init__.py"]


def test_generate_depth_writes_importable_modules(environment: Path, workspace: Path):
    with Governor(environment, workspace, depth=Depth.GENERATE) as governor:
        report = governor.run()
        assert "orders" in report.entities
        assert "orders" in report.cycles[0].generated

        module = governor.modules.load("orders")
        record = module.Orders.from_record({
            "order_id": "7", "customer": "Zed", "total": "5.5",
            "placed": "2026-03-01", "priority": "true",
        })
        assert record.order_id == 7
        assert record.to_record()["customer"] == "Zed"


def test_govern_depth_runs_transformations_and_regenerates(environment: Path, workspace: Path):
    with Governor(environment, workspace, depth=Depth.GOVERN) as governor:
        (governor.workspace.transformations / "tidy_orders.py").write_text(TIDY)
        report = governor.run()

        assert any("orders:tidy_orders" in c.transformed for c in report.cycles)
        # The transformation introduced a field, so the model regenerated to match.
        assert any("orders" in c.regenerated for c in report.cycles)
        assert "is_large" in governor.modules.load("orders").describe()["fields"]


def test_the_loop_converges_and_says_why(environment: Path, workspace: Path):
    with Governor(environment, workspace, depth=Depth.GOVERN) as governor:
        report = governor.run()
        assert report.stable
        assert "stable" in report.stopped_because
        assert not report.cycles[-1].productive
        assert not any(c.rolled_back for c in report.cycles)


def test_cycle_budget_stops_the_loop(environment: Path, workspace: Path):
    budget = Budget(max_cycles=1, stop_when_stable=False)
    with Governor(environment, workspace, depth=Depth.GENERATE, budget=budget) as governor:
        report = governor.run()
        assert len(report.cycles) == 1
        assert "cycle budget" in report.stopped_because


def test_migrations_are_applied_in_chain_order(environment: Path, workspace: Path):
    with Governor(environment, workspace, depth=Depth.GOVERN) as governor:
        governor.run()
        ledger = governor.ledger.report()
        assert ledger["pending"] == 0
        assert ledger["applied"] == ledger["revisions"]
        assert "orders" in ledger["heads"]


def test_a_second_run_over_unchanged_data_generates_nothing_new(environment: Path, workspace: Path):
    first = govern(environment, workspace=workspace, depth=Depth.GENERATE)
    assert first.entities

    with Governor(environment, workspace, depth=Depth.GENERATE) as governor:
        second = governor.run()
        # Shapes are re-derived from scratch, so entities reappear, but the
        # generated modules must not be rewritten with new revisions.
        assert not any(c.regenerated for c in second.cycles)


def test_everything_is_recorded_on_the_trace(environment: Path, workspace: Path):
    with Governor(environment, workspace, depth=Depth.GOVERN) as governor:
        governor.run()
        summary_path = governor.workspace.trace
        governor.journal.flush()
        assert summary_path.exists()

        from gratimos.trace import Journal

        kinds = Journal(summary_path).summary()["by_kind"]
        assert {"capture", "shape", "route", "policy", "codegen", "migration"} <= set(kinds)


def test_report_covers_every_subsystem(environment: Path, workspace: Path):
    with Governor(environment, workspace, depth=Depth.GENERATE) as governor:
        governor.run()
        report = governor.report()
        assert set(report) >= {
            "root", "workspace", "depth", "flow", "hub", "catalog",
            "modules", "transforms", "migrations", "policy", "agents",
        }
        assert report["catalog"]["orders"]["field_types"]["placed"] == "date"


def test_rejected_transformations_are_reported_not_run(environment: Path, workspace: Path):
    with Governor(environment, workspace, depth=Depth.GOVERN) as governor:
        (governor.workspace.transformations / "hostile.py").write_text(
            "import socket\ndef transform(records, context):\n    return records\n"
        )
        report = governor.run()
        warnings = [w for c in report.cycles for w in c.warnings]
        assert any("hostile" in w and "rejected" in w for w in warnings)


def test_an_empty_environment_is_not_an_error(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    report = govern(empty, workspace=tmp_path / "ws", depth=Depth.GENERATE)
    assert report.entities == []
    assert not any(c.rolled_back for c in report.cycles)


# --- CLI -----------------------------------------------------------------


def test_cli_scan_lists_entities(environment: Path, workspace: Path, capsys):
    assert main(["scan", str(environment), "-w", str(workspace)]) == 0
    out = capsys.readouterr().out
    assert "orders" in out and "placed:date" in out


def test_cli_govern_reports_and_exits_clean(environment: Path, workspace: Path, capsys):
    assert main(["govern", str(environment), "-w", str(workspace), "-d", "govern"]) == 0
    assert "generated :" in capsys.readouterr().out


def test_cli_emits_json_on_request(environment: Path, workspace: Path, capsys):
    assert main(["govern", str(environment), "-w", str(workspace), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["depth"] == "generate"
    assert "orders" in payload["entities"]


def test_cli_shapes_prints_one_entity(environment: Path, workspace: Path, capsys):
    assert main(["shapes", str(environment), "orders", "-w", str(workspace)]) == 0
    shape = json.loads(capsys.readouterr().out)
    assert shape["name"] == "orders"
    assert {f["ident"] for f in shape["fields"]} >= {"order_id", "total"}


def test_cli_shapes_rejects_an_unknown_entity(environment: Path, workspace: Path, capsys):
    assert main(["shapes", str(environment), "nope", "-w", str(workspace)]) == 2
    assert "unknown entity" in capsys.readouterr().err


def test_cli_transforms_scaffolds_and_lists(workspace: Path, capsys):
    assert main(["transforms", "new", str(workspace), "clean_orders", "--applies-to", "orders*"]) == 0
    assert main(["transforms", "list", str(workspace)]) == 0
    assert "clean_orders" in capsys.readouterr().out


def test_cli_transforms_exits_nonzero_when_something_is_rejected(workspace: Path):
    root = workspace / "transformations"
    root.mkdir(parents=True)
    (root / "bad.py").write_text("import socket\ndef transform(r, c):\n    return r\n")
    assert main(["transforms", "list", str(workspace)]) == 1


def test_cli_trace_summarizes(environment: Path, workspace: Path, capsys):
    main(["govern", str(environment), "-w", str(workspace)])
    assert main(["trace", str(workspace), "-s"]) == 0
    assert "by_kind" in capsys.readouterr().out


def test_cli_connectors_lists_availability(capsys):
    assert main(["connectors"]) == 0
    out = capsys.readouterr().out
    assert "file" in out and "available" in out


def test_cli_reports_errors_without_a_traceback(capsys):
    assert main(["scan", "/nonexistent/path/xyz"]) == 0  # nothing to read is not an error
    assert main(["trace", "/nonexistent/workspace"]) == 2
