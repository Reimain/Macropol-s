"""Reading the environment: every format, and the guards around the risky ones."""

from __future__ import annotations

from pathlib import Path

import pytest

from gratimos.errors import TransformError
from gratimos.meta import TypeTag
from gratimos.probes import (
    AccessDenied,
    AccessPolicy,
    ExecPolicy,
    Target,
    default_registry,
    image_size,
    read_script,
    run_script,
    stream_query,
)


def test_scan_reads_every_format_in_the_environment(environment: Path):
    discovery = default_registry().scan(environment)
    probes = discovery.by_probe()
    assert set(probes) >= {"csv", "json", "sqlite", "xlsx", "image", "shell", "text"}
    assert discovery.rows > 0
    assert not discovery.skipped


def test_csv_types_are_inferred_from_content(environment: Path):
    capture = default_registry().capture(environment / "orders.csv")
    shape = capture.payloads[0].shape
    assert shape.field("total").tag is TypeTag.FLOAT
    assert shape.field("placed").tag is TypeTag.DATE


def test_json_envelope_splits_into_entities(environment: Path):
    capture = default_registry().capture(environment / "customers.json")
    assert capture.payloads[0].name == "customers_customers"
    assert capture.payloads[0].rows == 3


def test_jsonl_is_read_line_by_line(environment: Path):
    capture = default_registry().capture(environment / "events.jsonl")
    assert capture.payloads[0].rows == 3


def test_xlsx_is_parsed_without_third_party_libraries(environment: Path):
    capture = default_registry().capture(environment / "quarterly.xlsx")
    rows = capture.payloads[0].records()
    assert [r["customer"] for r in rows] == ["Ada", "Alan", "Grace"]
    assert rows[0]["total"] == 99.5


def test_sqlite_is_opened_read_only(environment: Path):
    before = (environment / "billing.db").stat().st_mtime
    capture = default_registry().capture(environment / "billing.db")
    assert capture.payloads[0].name == "billing_invoices"
    assert capture.payloads[0].rows == 3
    assert (environment / "billing.db").stat().st_mtime == before


def test_sqlite_stream_query_yields_bounded_chunks(environment: Path):
    import sqlite3

    connection = sqlite3.connect(environment / "billing.db")
    chunks = list(stream_query(connection, "SELECT * FROM invoices", chunk_size=2, name="inv"))
    connection.close()
    assert [c.rows for c in chunks] == [2, 1]


def test_png_dimensions_come_from_the_header(environment: Path):
    record = default_registry().capture(environment / "logo.png").payloads[0].records()[0]
    assert (record["width"], record["height"]) == (640, 480)
    assert record["kind"] == "image"
    assert image_size((environment / "logo.png").read_bytes()) == (640, 480)


def test_shell_scripts_are_read_not_executed(environment: Path):
    capture = default_registry().capture(environment / "sync.sh")
    facts = capture.payloads[0].records()[0]
    assert facts["interpreter"] == "bash"
    assert facts["functions"] == ["sync_orders"]
    assert facts["variables"]["REGION"] == "eu-west-1"
    assert "curl" in facts["notable_commands"]
    assert any("notable commands" in w for w in capture.warnings)


def test_script_execution_is_refused_without_an_explicit_grant(environment: Path):
    with pytest.raises(TransformError, match="not permitted"):
        run_script(environment / "sync.sh", policy=ExecPolicy())


def test_script_execution_runs_under_an_explicit_grant(tmp_path: Path):
    script = tmp_path / "hello.sh"
    script.write_text("#!/bin/sh\necho '{\"ok\": true}'\n")
    script.chmod(0o755)
    result = run_script(script, policy=ExecPolicy(allow_exec=True, allow_paths=(str(script.resolve()),)))
    assert result.ok
    assert "ok" in result.stdout


def test_access_policy_refuses_private_addresses():
    policy = AccessPolicy()
    for url in ("http://localhost:8080/x", "http://127.0.0.1/x", "http://[::1]/x"):
        with pytest.raises(AccessDenied):
            policy.check(url)


def test_access_policy_refuses_unlisted_hosts_and_schemes():
    policy = AccessPolicy(allow_hosts=frozenset({"api.example.com"}), allow_private=True)
    with pytest.raises(AccessDenied, match="allow list"):
        policy.check("https://evil.example.net/x")
    with pytest.raises(AccessDenied, match="scheme"):
        policy.check("file:///etc/passwd")


def test_private_addresses_are_reachable_when_explicitly_allowed():
    AccessPolicy(allow_private=True).check("http://127.0.0.1:9/x")


def test_contested_targets_are_reported(tmp_path: Path):
    # A .txt full of CSV: the text probe wins on suffix, csv on structure.
    path = tmp_path / "ambiguous.txt"
    path.write_text("a,b,c\n1,2,3\n4,5,6\n")
    match = default_registry().match(Target.of(path))
    assert match is not None
    assert match.probe.name == "csv"
    assert match.runner_up == "text"


def test_read_script_never_evaluates_substitutions():
    facts = read_script('X=$(rm -rf /)\nY="safe"\n')
    assert "X" not in facts.variables  # command substitution is not recorded
    assert facts.variables["Y"] == "safe"
