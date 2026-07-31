"""Explicable decisions, and the reversible ledger they act on."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fixtures import ORDERS

from gratimos.contextflow import ContextFlow
from gratimos.errors import MigrationError, PolicyError
from gratimos.meta import Wrapped, infer_shape
from gratimos.migrations import AlembicAdapter, MigrationLedger, OpKind, operations_for
from gratimos.policy import (
    AUTO_APPLY,
    BLOCK,
    ESCALATE,
    GENERATE,
    HOLD,
    STAGE_LOCAL,
    STAGE_REVIEW,
    WAIT,
    PolicyEngine,
    Policymakers,
    Situation,
)


def _shape(rows=ORDERS, name="orders"):
    wrapped, _ = Wrapped.of(rows, name=name).cast()
    return wrapped.shape


# --- policy engine -------------------------------------------------------


def test_verdicts_carry_the_rule_that_produced_them():
    makers = Policymakers()
    payload = Wrapped.of(ORDERS, name="orders")

    roomy = makers.storage(payload, pressure=0.1, free_bytes=10**9)
    assert roomy.action == HOLD

    tight = makers.storage(payload, pressure=0.95, free_bytes=10)
    assert tight.action == STAGE_LOCAL
    assert tight.rule == "storage.under-pressure"
    assert "high-water" in tight.reason


def test_codegen_waits_until_the_evidence_justifies_it():
    makers = Policymakers()
    thin = infer_shape([{"a": 1}], name="t")
    assert makers.codegen("t", thin, revision=1, changes=[]).action == WAIT

    assert makers.codegen("orders", _shape(), revision=1, changes=[]).action == GENERATE

    current = _shape()
    assert makers.codegen(
        "orders", current, revision=2, changes=[], generated_digest=current.digest
    ).action == WAIT


def test_breaking_changes_escalate_rather_than_regenerate():
    makers = Policymakers()
    before = _shape()
    after = infer_shape([{"order_id": 1, "customer": "A"}] * 3, name="orders")
    verdict = makers.codegen("orders", after, revision=3, changes=before.diff(after))
    assert verdict.action == ESCALATE
    assert "break existing readers" in verdict.reason


def test_migration_verdicts_track_reversibility_and_loss():
    makers = Policymakers()
    before = _shape()
    additive = before.diff(before.merge(infer_shape([{"channel": "web"}], name="orders")))
    assert makers.migration("orders", additive, rows=3).action == AUTO_APPLY

    lossy = before.diff(infer_shape([{"order_id": 1}], name="orders"))
    assert makers.migration("orders", lossy, rows=3).action == STAGE_REVIEW
    assert makers.migration("orders", lossy, rows=3, has_downgrade=False).action == BLOCK


def test_creating_an_entity_is_always_safe_to_apply():
    assert Policymakers().migration("orders", [], rows=3, creating=True).action == AUTO_APPLY


def test_a_higher_priority_rule_overrides_the_default_set():
    makers = Policymakers()
    makers.engine.rule(
        "local.never-generate", "codegen",
        when=lambda s: True, then=WAIT, reason="this deployment reviews all codegen",
        priority=1,
    )
    verdict = makers.codegen("orders", _shape(), revision=1, changes=[])
    assert verdict.rule == "local.never-generate"


def test_a_kind_with_no_rule_and_no_default_raises():
    engine = PolicyEngine()
    with pytest.raises(PolicyError, match="no rule and no default"):
        engine.decide(Situation(kind="unknown", subject="x"))


def test_a_broken_rule_abstains_instead_of_crashing():
    engine = PolicyEngine()
    engine.rule("bad", "k", when=lambda s: 1 / 0, then="never", priority=1)
    engine.rule("good", "k", when=lambda s: True, then="fine", priority=2)
    assert engine.decide(Situation(kind="k")).action == "fine"


def test_decisions_are_recorded_on_the_flow():
    flow = ContextFlow("kernel")
    makers = Policymakers(flow)
    makers.codegen("orders", _shape(), revision=1, changes=[])
    event = flow.events(kind=flow.events()[0].kind)[0]
    assert event.payload["action"] == GENERATE
    assert event.payload["rule"] == "codegen.first-generation"


# --- ledger --------------------------------------------------------------


def test_operations_are_derived_from_shape_changes():
    before = _shape()
    after = before.merge(infer_shape([{"channel": "web"}], name="orders"))
    operations = operations_for("orders", before.diff(after))
    assert [op.kind for op in operations] == [OpKind.ADD_FIELD]
    assert operations[0].inverse().kind is OpKind.DROP_FIELD


def test_create_then_evolve_forms_a_chain():
    ledger = MigrationLedger(ContextFlow("kernel"))
    created = ledger.create_entity(_shape())
    assert created.down_revision == ""
    assert created.reversible

    after = _shape().merge(infer_shape([{"channel": "web"}], name="orders"))
    evolved = ledger.propose("orders", _shape().diff(after), shape=after)
    assert evolved.down_revision == created.revision
    assert ledger.head("orders") == evolved.revision


def test_revision_ids_are_content_addressed():
    a, b = MigrationLedger(), MigrationLedger()
    assert a.create_entity(_shape()).revision == b.create_entity(_shape()).revision


def test_out_of_order_application_is_refused():
    ledger = MigrationLedger()
    ledger.create_entity(_shape())
    after = _shape().merge(infer_shape([{"channel": "web"}], name="orders"))
    second = ledger.propose("orders", _shape().diff(after), shape=after)

    with pytest.raises(MigrationError, match="out of order"):
        ledger.apply(second.revision)


def test_reverting_an_additive_change_records_the_inverse():
    ledger = MigrationLedger()
    created = ledger.create_entity(_shape())
    ledger.apply(created.revision)
    after = _shape().merge(infer_shape([{"channel": "web"}], name="orders"))
    added = ledger.propose("orders", _shape().diff(after), shape=after)
    ledger.apply(added.revision)

    reverted = ledger.revert(added.revision)
    assert reverted.operations[0].kind is OpKind.DROP_FIELD
    assert reverted.operations[0].field_name == "channel"


def test_reverting_a_lossy_change_is_refused_without_force():
    ledger = MigrationLedger()
    created = ledger.create_entity(_shape())
    ledger.apply(created.revision)
    narrowed = infer_shape([{"order_id": 1}], name="orders")
    dropped = ledger.propose("orders", _shape().diff(narrowed), shape=narrowed)
    ledger.apply(dropped.revision)

    assert dropped.lossy and not dropped.reversible
    with pytest.raises(MigrationError, match="not reversible"):
        ledger.revert(dropped.revision)
    assert ledger.revert(dropped.revision, force=True).operations


def test_ledger_persists_and_reloads(tmp_path: Path):
    ledger = MigrationLedger()
    created = ledger.create_entity(_shape())
    ledger.apply(created.revision)
    ledger.save(tmp_path / "ledger.json")

    reloaded = MigrationLedger()
    assert reloaded.load(tmp_path / "ledger.json") == 1
    assert reloaded.head("orders") == created.revision
    assert reloaded.pending() == []


# --- alembic rendering ---------------------------------------------------


def test_rendered_migration_is_valid_python():
    ledger = MigrationLedger()
    after = _shape().merge(infer_shape([{"channel": "web"}], name="orders"))
    ledger.create_entity(_shape())
    revision = ledger.propose("orders", _shape().diff(after), shape=after)

    rendered = AlembicAdapter(table_prefix="gr_").render(revision)
    ast.parse(rendered.source)
    assert "op.add_column" in rendered.source
    assert "op.drop_column('gr_orders', 'channel')" in rendered.source


def test_irreversible_migrations_refuse_to_downgrade_silently():
    ledger = MigrationLedger()
    ledger.create_entity(_shape())
    narrowed = infer_shape([{"order_id": 1}], name="orders")
    revision = ledger.propose("orders", _shape().diff(narrowed), shape=narrowed)

    rendered = AlembicAdapter().render(revision)
    ast.parse(rendered.source)
    assert not rendered.reversible
    assert "NotImplementedError" in rendered.source
    assert "cannot be recovered" in rendered.source


def test_rendered_migrations_write_to_disk(tmp_path: Path):
    ledger = MigrationLedger()
    ledger.create_entity(_shape())
    paths = AlembicAdapter().write_all(ledger, tmp_path)
    assert paths and paths[0].exists()
    ast.parse(paths[0].read_text())
