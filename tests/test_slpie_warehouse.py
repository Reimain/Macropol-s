"""The BI layer — facts, dimensions, measures, and the templates that read them.

Before this existed, every number this platform derived left through a picture.
A TOGAF view rendered as Mermaid, a risk register as Markdown — correct, and
*terminal*: anything wanting to ask a question the report did not anticipate had
to parse a diagram of the answer.

So the assertions here are about the properties that make a warehouse worth
having rather than about row counts:

* a **value that was not recorded stays absent**, because zero is a measurement;
* the **same graph builds the same tables**, so an export can be diffed;
* **provenance survives** — a count of relationships is a different number
  depending on whether they were read or inferred, and the fact table says which;
* a **measure that cannot be summed says so**, which is the single flag standing
  between a chart and a meaningless total.
"""

from __future__ import annotations

import sqlite3

import pytest

from slpie.warehouse import export, measures
from slpie.warehouse.build import READ_KINDS, build
from slpie.warehouse.model import Column, Dimension, Fact, Star, Table, ddl, insert
from slpie.warehouse.schema import STARS, names, star


@pytest.fixture()
def graph(repository):
    """A real graph over a real tree, through the platform's own scan."""
    from slpie.compose import Composition, Context
    from slpie.compose.registry import registry
    from slpie.governance.view import view_of

    result = Composition.read(f"discover {repository}", verbs=registry()).run(
        Context(root=str(repository)))
    with view_of(result.flow.items) as opened:
        yield opened


@pytest.fixture()
def warehouse(graph):
    # A fixed timestamp: a build that stamped the clock would differ on every
    # run and none of the determinism assertions below would mean anything.
    return build(graph, now=1_700_000_000)


# --- the schema ---------------------------------------------------------------


def test_every_star_resolves_its_own_dimensions():
    """A fact pointing at a dimension its star does not carry is a broken join."""
    for item in STARS:
        assert not item.unresolved(), f"{item.name} points at a missing dimension"


def test_every_column_is_documented():
    """A warehouse whose columns are undocumented is one where every analyst
    asks the same question of the same person."""
    for item in STARS:
        for table in item.tables:
            for column in table.columns:
                # A key that joins a documented dimension explains itself.
                assert column.doc or column.dimension, (
                    f"{table.name}.{column.name} has no documentation"
                )


def test_every_fact_states_its_grain():
    """A table whose grain nobody wrote down is one where somebody eventually
    sums a column that must not be summed."""
    for item in STARS:
        assert item.fact.grain, f"{item.fact.name} does not say what one row is"


def test_the_two_dialects_are_declared_rather_than_branched():
    from slpie.warehouse.model import TYPES

    for name, (sqlite_type, postgres_type) in TYPES.items():
        assert sqlite_type and postgres_type, name
    # SQLite has no boolean; storing 0/1 in INTEGER is what it does anyway, and
    # a `BOOLEAN` column there silently becomes NUMERIC with different
    # comparison semantics.
    assert TYPES["boolean"] == ("INTEGER", "BOOLEAN")


def test_the_placeholder_follows_the_dialect():
    """A Postgres export emitting SQLite's `?` fails at the driver, not here."""
    table = Table(name="t", doc="", columns=(Column("a"), Column("b")))
    assert insert(table, dialect="sqlite").endswith("(?, ?)")
    assert insert(table, dialect="postgres").endswith("(%s, %s)")


# --- the build ----------------------------------------------------------------


def test_a_build_fills_every_published_star(warehouse):
    assert {item.name for item in warehouse.stars} == set(names())
    assert warehouse.table("fact_element").rows
    assert warehouse.table("dim_node").rows


def test_two_builds_of_one_graph_are_identical(graph):
    """So an export can be diffed, and a real change is visible as one."""
    first = build(graph, now=1_700_000_000)
    second = build(graph, now=1_700_000_000)
    for table in first.tables:
        assert table.rows == second.table(table.name).rows, table.name


#: Dimensions written out by hand in the schema. Their order is a decision —
#: `dim_severity` is in rank order because that is the order that means
#: something — so the ETL's sort rule does not apply to them.
DECLARED = {"dim_severity"}


def test_rows_the_build_produced_are_ordered_by_key(warehouse):
    for table in warehouse.tables:
        if not table.keys or not table.rows or table.name in DECLARED:
            continue
        keyed = [tuple(row[key] for key in table.keys) for row in table.rows]
        assert keyed == sorted(keyed), f"{table.name} is not in key order"


def test_a_value_that_was_not_recorded_stays_absent(warehouse):
    """Zero is a measurement. Absent is not, and filling one in as the other is
    how a warehouse starts lying quietly."""
    table = warehouse.table("fact_element")
    sparse = Table(name="t", doc="", columns=(Column("a"), Column("missing")))
    assert sparse.values({"a": 1}) == (1, None)
    assert table.rows, "nothing was built, so this asserts nothing"


def test_the_evidence_dimension_draws_the_line_between_read_and_inferred(warehouse):
    """The single fact that makes `inferred_share` mean anything."""
    rows = {row["evidence_kind"]: row for row in warehouse.table("dim_evidence").rows}

    assert rows["lockfile_pin"]["read"] is True
    assert rows["static_import"]["read"] is True
    assert rows["name_heuristic"]["read"] is False
    assert rows["dynamic_load"]["read"] is False
    # And the base confidences are §10's, not a second copy of them.
    assert rows["lockfile_pin"]["base_confidence"] == 1.0


def test_the_severity_dimension_carries_its_own_order():
    """A chart sorting alphabetically puts critical between medium and high."""
    from slpie.warehouse.schema import DIM_SEVERITY

    ranked = sorted(DIM_SEVERITY.rows, key=lambda row: row["rank"])
    assert [row["severity"] for row in ranked] == [
        "info", "low", "medium", "high", "critical"]
    assert all(row["blocks_release"] for row in ranked[3:])
    assert not any(row["blocks_release"] for row in ranked[:3])


def test_an_edge_carries_the_strongest_evidence_it_rests_on(graph):
    """An edge corroborated by a lockfile *and* a name heuristic is a lockfile
    edge — that is what its confidence rests on."""
    built = build(graph, now=1)
    rows = built.table("fact_edge").rows
    if not rows:
        pytest.skip("this tree produced no relationships")

    for row in rows:
        if not row["evidence_kind"]:
            continue
        from slpie.domain.evidence import EvidenceKind

        assert row["read"] == (EvidenceKind(row["evidence_kind"]) in READ_KINDS)


def test_a_build_with_no_manifest_says_declared_is_false_because_nobody_asked(warehouse):
    """Not the same as nothing being declared, and the measures read false."""
    assert any("no environment manifest" in gap for gap in warehouse.gaps)


def test_an_empty_findings_star_is_reported_rather_than_read_as_clean(warehouse):
    """An empty findings table and a clean estate look identical from here."""
    assert any("no findings were supplied" in gap for gap in warehouse.gaps)


def test_a_suppressed_finding_is_absent_rather_than_flagged(graph):
    """A suppression is a decision with a reason on the record. Counting it
    would make that decision invisible in every total."""
    from slpie.domain.evidence import Evidence, EvidenceKind, SourceLocation
    from slpie.domain.finding import Finding, FindingKind
    from slpie.domain.lifecycle import Severity

    # Evidence is not optional anywhere in this platform, and a finding is no
    # exception — which is invariant 1 reaching even a test fixture.
    cited = Evidence(
        kind=EvidenceKind.LOCKFILE_PIN,
        location=SourceLocation(uri="file:///x/package-lock.json", line=12),
        extractor="test",
    )
    # Two *different* findings, because a Finding's id is content-addressed:
    # suppressing one does not change its content, so `open_one.suppress(...)`
    # has the same id and asserting on ids alone would prove nothing.
    open_one = Finding(kind=FindingKind.VULNERABLE_DEPENDENCY, severity=Severity.HIGH,
                       subject="visible", title="t", detail="d", evidence=(cited,))
    hidden = Finding(kind=FindingKind.VULNERABLE_DEPENDENCY, severity=Severity.HIGH,
                     subject="waived", title="t", detail="d", evidence=(cited,),
                     ).suppress("accepted for this release")

    built = build(graph, findings=(open_one, hidden), now=1)
    subjects = [row["subject"] for row in built.table("fact_finding").rows]
    assert "visible" in subjects
    assert "waived" not in subjects
    # And the measure agrees, because it counts the rows rather than filtering
    # again — one definition of "how many findings", as the module promises.
    assert measures.measure("findings").of(built.table("fact_finding").rows) == 1


# --- measures -----------------------------------------------------------------


def test_every_measure_names_a_fact_that_exists():
    facts = {item.fact.name for item in STARS}
    for item in measures.MEASURES:
        assert item.fact in facts, f"{item.name} reads {item.fact}, which is not a table"


def test_every_measure_explains_itself():
    """'How many findings' has three plausible answers. A measure without a
    definition is two dashboards disagreeing in a meeting."""
    for item in measures.MEASURES:
        assert len(item.doc) > 40, item.name


def test_a_measure_that_cannot_be_summed_says_so():
    """The single flag between a chart and a meaningless total."""
    assert measures.measure("findings").additive
    # The same subject appears under several severities; adding the
    # per-severity figures double-counts it.
    assert not measures.measure("subjects_affected").additive
    assert not measures.measure("mean_confidence").additive


def test_the_inferred_share_is_the_read_flag_and_nothing_else():
    rows = [{"read": True}, {"read": True}, {"read": False}, {"read": False}]
    assert measures.measure("inferred_share").of(rows) == 0.5


def test_the_weakest_link_bounds_a_slice():
    rows = [{"confidence": 0.9}, {"confidence": 0.4}, {"confidence": 1.0}]
    assert measures.measure("weakest_link").of(rows) == 0.4


def test_a_measure_over_nothing_is_zero_rather_than_an_error():
    for item in measures.MEASURES:
        assert item.of(()) == 0.0, item.name


def test_summarise_carries_the_additive_flag_to_the_caller():
    """A caller building a total row cannot know which columns it may add
    without being told, and the flag is the whole reason it exists."""
    summary = measures.summarise("fact_edge", [{"confidence": 0.5, "read": True}])
    assert summary["relationships"]["additive"] is True
    assert summary["mean_confidence"]["additive"] is False


# --- export -------------------------------------------------------------------


def test_csv_carries_the_header_from_the_schema(warehouse):
    table = warehouse.table("dim_severity")
    text = export.to_csv(table)
    assert text.splitlines()[0] == ",".join(table.header())


def test_csv_writes_a_boolean_as_one_or_zero(warehouse):
    """What every SQL target stores and what a spreadsheet sums."""
    text = export.to_csv(warehouse.table("dim_severity"))
    assert ",1" in text and ",0" in text
    assert "True" not in text and "False" not in text


def test_json_ships_the_schema_beside_the_data(warehouse):
    """A consumer reading an extract cold needs to know what the columns mean.

    That is the difference between a file and a dataset.
    """
    import json

    payload = json.loads(export.to_json(warehouse.table("fact_element")))
    assert payload["grain"]
    assert payload["columns"] and all(item["name"] for item in payload["columns"])
    assert len(payload["rows"]) == len(warehouse.table("fact_element").rows)


def test_sql_escapes_a_quote_rather_than_ending_the_statement():
    table = Table(name="t", doc="", columns=(Column("name"),),
                  rows=({"name": "o'brien"},))
    assert "'o''brien'" in export.to_sql(table)


def test_sql_writes_null_for_absent_rather_than_an_empty_string():
    table = Table(name="t", doc="", columns=(Column("a"), Column("b")),
                  rows=({"a": 1},))
    assert "VALUES (1, NULL)" in export.to_sql(table)


def test_loading_materialises_into_a_real_database(warehouse):
    connection = sqlite3.connect(":memory:")
    written = export.load(connection, warehouse.tables)

    assert written["dim_severity"] == 5
    rows = connection.execute(
        "SELECT severity FROM dim_severity ORDER BY rank DESC LIMIT 1").fetchall()
    assert rows == [("critical",)]


def test_loading_twice_does_not_double_the_rows(warehouse):
    """Rebuilt, never migrated: a stale row is worse than a missing one because
    it looks current."""
    connection = sqlite3.connect(":memory:")
    export.load(connection, warehouse.tables)
    export.load(connection, warehouse.tables)

    count = connection.execute("SELECT count(*) FROM dim_severity").fetchone()[0]
    assert count == 5


def test_the_loader_is_parameterised(warehouse):
    """These rows carry node names and file paths — user-controlled strings.

    The loader is the one place a warehouse could grow an injection, so it is
    asserted rather than reviewed.
    """
    connection = sqlite3.connect(":memory:")
    nasty = Table(
        name="t", doc="", columns=(Column("name"),),
        rows=({"name": "x'); DROP TABLE dim_severity; --"},),
    )
    export.load(connection, [nasty])
    assert connection.execute("SELECT name FROM t").fetchone()[0].startswith("x');")


def test_an_unknown_format_is_refused_with_the_list(warehouse, tmp_path):
    with pytest.raises(KeyError) as raised:
        export.write(warehouse.tables, tmp_path, fmt="parquet")
    assert "csv" in str(raised.value)


def test_the_data_dictionary_comes_from_the_schema(warehouse):
    text = export.dictionary(warehouse.tables)
    for table in warehouse.tables:
        assert f"`{table.name}`" in text
        for column in table.columns:
            assert f"`{column.name}`" in text
    assert "Generated by" in text


# --- templates: choosing a screen for a demand --------------------------------


from slpie.present.template import (  # noqa: E402 - grouped with what it tests
    CONTEXTS,
    DOMAINS,
    FLOOR,
    UTILITIES,
    Demand,
    classify,
    score,
    select,
)
from slpie.present.templates import TEMPLATES, keys, template  # noqa: E402


def test_every_template_declares_all_three_axes():
    """A single 'type' would collapse axes that vary independently: `monitor`
    in a console about security and `report` in a document about security share
    a subject and almost nothing else."""
    for item in TEMPLATES:
        assert item.utility in UTILITIES, item.key
        assert item.context in CONTEXTS, item.key
        assert item.domain in DOMAINS, item.key
        assert all(also in DOMAINS for also in item.also), item.key


def test_every_panel_names_a_component_the_browser_has():
    """A template naming a component this build lacks renders a hole.

    Read out of the browser's own dictionary rather than out of either Python
    constant, so the chain `panel -> present.COMPONENTS -> contract.COMPONENTS
    -> dictionary.js` is pinned at the end that actually draws.
    """
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "slpie" / "ui" / "app"
              / "components" / "dictionary.js").read_text(encoding="utf-8")
    # Split on the *declaration*, not on the name: the module docstring says
    # "COMPONENTS" three lines in, and splitting there swept the cell formats
    # into the set — which let a panel name `pill` and pass.
    _, _, body = source.partition("export const COMPONENTS")
    assert body, "the component dictionary no longer declares COMPONENTS"
    body = body.split("};", 1)[0]
    available = set(re.findall(r"^\s{2}(\w+):", body, re.M))
    assert available, "the component dictionary no longer parses"

    for item in TEMPLATES:
        for panel in item.panels:
            assert panel.component in available, (
                f"{item.key} names component {panel.component!r}, which the "
                f"browser does not have"
            )


def test_the_panel_vocabulary_is_the_browsers():
    """`present` restates the addressable set to dodge an import cycle.

    A restatement that nobody checks is how two constants come to disagree, so
    this is the check: every component a panel may name is one the contract
    declares addressable.
    """
    from slpie.present.template import COMPONENTS
    from slpie.ui.contract import COMPONENTS as ADDRESSABLE

    assert COMPONENTS <= ADDRESSABLE, sorted(COMPONENTS - ADDRESSABLE)


def test_a_panel_naming_an_unknown_component_is_refused():
    """At construction, not at render — a template is imported at start-up."""
    from slpie.present.template import Panel

    with pytest.raises(ValueError) as raised:
        Panel(component="diagram", title="The estate")
    assert "diagram" in str(raised.value)
    assert "grid" in str(raised.value)


def test_every_panel_names_a_measure_that_is_defined():
    """A number nobody defined is a number nobody can explain."""
    known = {item.name for item in measures.MEASURES}
    for item in TEMPLATES:
        for panel in item.panels:
            for name in panel.measures:
                assert name in known, f"{item.key} reads undefined measure {name!r}"


def test_every_panel_reads_a_star_that_exists():
    published = set(names())
    for item in TEMPLATES:
        for panel in item.panels:
            # A panel with no star reads something outside the warehouse — the
            # queue board, for instance — and says so by naming its source.
            if not panel.star:
                assert panel.options.get("source"), (
                    f"{item.key}: a panel with no star must say where it reads from"
                )
                continue
            assert panel.star in published, f"{item.key} reads unknown star {panel.star!r}"


def test_a_measure_is_only_placed_on_the_star_that_defines_it():
    """`findings` on the relationships star would compute over the wrong rows
    and produce a plausible number that means nothing."""
    for item in TEMPLATES:
        for panel in item.panels:
            for name in panel.measures:
                found = measures.measure(name)
                star_for = star(panel.star)
                assert star_for and found.fact == star_for.fact.name, (
                    f"{item.key}: {name} reads {found.fact}, "
                    f"but the panel is on {panel.star}"
                )


def test_selection_prefers_the_subject_over_everything_else():
    """A security board shown to somebody asking about cost is wrong in a way a
    slightly mis-sized layout is not."""
    chosen = select(Demand(domain="security", context="document"), TEMPLATES)
    assert chosen.template.domain == "security"


def test_an_unstated_axis_is_neutral_rather_than_a_mismatch():
    """A caller who knows only the domain should not be punished for not
    inventing a context."""
    stated = select(Demand(domain="security", utility="monitor", context="dashboard"),
                    TEMPLATES)
    partial = select(Demand(domain="security"), TEMPLATES)
    assert partial.template is stated.template
    assert partial.score < stated.score


def test_a_tie_is_reported_rather_than_resolved_silently():
    """An arbitrary winner presented as a decision is the kind of thing nobody
    notices until it is wrong."""
    chosen = select(Demand(), TEMPLATES)
    assert chosen.tied_with, "nothing stated, so everything should tie"
    assert "tied with" in chosen.reason
    assert not chosen.confident


def test_a_weak_best_match_declares_itself_weak():
    chosen = select(Demand(), TEMPLATES)
    assert chosen.score < FLOOR
    assert "closest template rather than the right one" in chosen.reason


def test_no_template_claims_to_serve_every_domain():
    """A universal second-best ties with the specialist on every subject and
    wins on name order — which the engine correctly reported as meaningless."""
    for item in TEMPLATES:
        assert len(item.also) < len(DOMAINS) - 1, (
            f"{item.key} claims almost every domain, so it will tie everywhere"
        )


def test_the_classifier_declines_rather_than_guessing():
    """'It could not tell' and 'it is about dependencies' are different answers."""
    assert classify("what CVEs affect payments") == "security"
    assert classify("which packages need upgrading") == "dependencies"
    assert classify("") == ""
    assert classify("tell me about the thing") == ""


def test_a_tie_between_domains_is_no_answer():
    """'a cve in a package' is genuinely about both, and picking one hides the
    most interesting thing about the question."""
    assert classify("a cve in a package") == ""


def test_a_stated_domain_is_never_overridden_by_the_classifier():
    chosen = select(
        Demand(domain="operations", about="cve vulnerability secret"), TEMPLATES)
    assert chosen.template.domain == "operations" or "operations" in chosen.template.also


def test_selection_over_no_templates_says_so_rather_than_failing():
    chosen = select(Demand(domain="security"), ())
    assert chosen.template is None
    assert "no templates" in chosen.reason


def test_a_named_template_resolves_and_an_unknown_one_does_not():
    assert template("security-board") is not None
    assert template("nonesuch") is None
    assert set(keys()) == {item.key for item in TEMPLATES}


# --- the split: models state shapes, the presentation tier draws them ----------


def test_no_model_imports_the_presentation_tier_at_module_scope():
    """The dependency points one way: `present` may know about views, and a
    view must not need `present` to exist in order to be constructed.

    Checked with `ast` at module scope only — `to_diagram` imports the type it
    returns from inside the function, which is what keeps import time clean.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "slpie" / "enterprise"
    offenders = []
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:                     # module scope only
            if isinstance(node, ast.ImportFrom) and "present" in (node.module or ""):
                offenders.append(f"{path.name}:{node.lineno}")
            if isinstance(node, ast.Import):
                offenders.extend(
                    f"{path.name}:{node.lineno}" for alias in node.names
                    if "present" in alias.name
                )
    assert not offenders, f"a model imports its own renderer: {offenders}"


def test_a_view_states_its_orientation_as_intent_not_as_syntax():
    """It used to carry the literal string `graph TD`, which put a Mermaid
    keyword inside every view and left no room for a second renderer."""
    from slpie.enterprise.view import View
    from slpie.present.diagram import ORIENTATIONS

    view = View(name="x", doc="d", orientation="top-down")
    assert view.orientation in ORIENTATIONS
    assert "graph" not in view.orientation


def test_one_renderer_decides_escaping_for_every_diagram():
    """Escaping was decided three times in three files that could not see one
    another, and a fix to one was a fix to one."""
    from slpie.present import mermaid
    from slpie.present.diagram import Diagram, Mark

    drawn = mermaid(Diagram(name="x", marks=(Mark(id="a", label='pay"ments [prod]'),)))
    assert '"' not in drawn.split("[", 1)[1].rsplit("]", 1)[0].replace('"', "", 2)
    assert "pay'ments (prod)" in drawn


def test_an_empty_diagram_says_so_rather_than_rendering_nothing():
    """An empty string is indistinguishable from a failure."""
    from slpie.present import mermaid
    from slpie.present.diagram import Diagram

    assert "nothing to draw" in mermaid(Diagram(name="x"))


def test_a_diagram_with_marks_and_no_links_still_draws_the_boxes():
    """Boxes with no arrows is a true statement about an architecture with no
    recorded relationships."""
    from slpie.present import mermaid
    from slpie.present.diagram import Diagram, Mark

    drawn = mermaid(Diagram(name="x", marks=(Mark(id="a", label="A"),)))
    assert "nothing to draw" not in drawn
    assert 'a["A"]' in drawn


# --- grouping, which is what a dimension is for -------------------------------


def test_a_breakdown_and_a_total_are_the_same_definition():
    """The bar chart and the headline must not disagree about what a row is.

    Both go through one `Measure`, so this is a property rather than a
    coincidence: the parts sum to the whole for an additive measure, always.
    """
    from slpie.warehouse import query
    from slpie.warehouse.measures import measure

    counted = measure("findings")
    filled = star("findings").fact.with_rows((
        {"finding_id": "a", "subject": "x", "severity": "high"},
        {"finding_id": "b", "subject": "y", "severity": "high"},
        {"finding_id": "c", "subject": "z", "severity": "low"},
    ))
    subject = Star(name="findings", doc="", fact=filled,
                   dimensions=star("findings").dimensions)

    parts = query.breakdown(subject, "severity", counted)
    assert {row["label"]: row["value"] for row in parts} == {"high": 2.0, "low": 1.0}
    assert sum(row["value"] for row in parts) == counted.of(filled.rows)


def test_a_breakdown_carries_the_dimensions_own_order():
    """Sorted alphabetically, `critical` lands third and looks like a data bug."""
    from slpie.warehouse import query
    from slpie.warehouse.measures import measure

    filled = star("findings").fact.with_rows((
        {"finding_id": "a", "subject": "x", "severity": "low"},
        {"finding_id": "b", "subject": "y", "severity": "low"},
        {"finding_id": "c", "subject": "z", "severity": "critical"},
    ))
    subject = Star(name="findings", doc="", fact=filled,
                   dimensions=star("findings").dimensions)

    parts = query.breakdown(subject, "severity", measure("findings"))
    ranked = sorted(parts, key=lambda row: row["rank"], reverse=True)
    assert ranked[0]["label"] == "critical"
    # And by value, `low` wins — which is exactly why the rank has to travel.
    assert parts[0]["label"] == "low"


def test_grouping_by_a_field_with_no_dimension_still_answers():
    """`kind` has no table, and that is not an error."""
    from slpie.warehouse import query
    from slpie.warehouse.measures import measure

    filled = star("elements").fact.with_rows((
        {"node_id": "a", "kind": "package"},
        {"node_id": "b", "kind": "service"},
    ))
    subject = Star(name="elements", doc="", fact=filled,
                   dimensions=star("elements").dimensions)

    parts = query.breakdown(subject, "kind", measure("elements"))
    assert {row["label"] for row in parts} == {"package", "service"}
    assert all("rank" not in row for row in parts)


def test_an_absent_value_groups_as_unknown_rather_than_blank():
    """A blank label and an unrecorded one look identical on a chart."""
    from slpie.warehouse import query
    from slpie.warehouse.measures import measure

    filled = star("elements").fact.with_rows((
        {"node_id": "a", "kind": "package"},
        {"node_id": "b"},
    ))
    subject = Star(name="elements", doc="", fact=filled,
                   dimensions=star("elements").dimensions)

    labels = {row["label"] for row in query.breakdown(subject, "kind",
                                                     measure("elements"))}
    assert labels == {"package", "unknown"}


def test_rows_resolve_their_dimension_to_something_readable():
    """A grid showing a subject as a digest helps nobody, and losing the digest
    would break the link out of the row."""
    from slpie.warehouse import query

    filled = star("findings").fact.with_rows((
        {"finding_id": "a", "subject": "node-1", "severity": "high"},
    ))
    nodes = star("findings").dimension("dim_node").with_rows((
        {"node_id": "node-1", "name": "payments-api"},
    ))
    subject = Star(name="findings", doc="", fact=filled,
                   dimensions=(nodes, star("findings").dimension("dim_severity")))

    row = query.rows(subject)[0]
    assert row["subject"] == "node-1"
    assert row["subject_name"] == "payments-api"


def test_worst_first_sorts_by_seriousness_not_by_spelling():
    """`critical` before `low` — alphabetically that is the wrong way round."""
    from slpie.warehouse import query

    filled = star("findings").fact.with_rows((
        {"finding_id": "a", "subject": "x", "severity": "low"},
        {"finding_id": "b", "subject": "y", "severity": "critical"},
    ))
    subject = Star(name="findings", doc="", fact=filled,
                   dimensions=star("findings").dimensions)

    ordered = [row["severity"] for row in query.rows(subject, sort="severity")]
    assert ordered == ["critical", "low"]


def test_a_sort_the_star_cannot_do_leaves_the_rows_alone():
    """Unsorted and complete beats refused: the panel still renders."""
    from slpie.warehouse import query

    filled = star("elements").fact.with_rows((
        {"node_id": "a"}, {"node_id": "b"},
    ))
    subject = Star(name="elements", doc="", fact=filled,
                   dimensions=star("elements").dimensions)

    assert [row["node_id"] for row in
            query.rows(subject, sort="nonexistent")] == ["a", "b"]


def test_an_unmeasured_row_sorts_last_in_both_directions():
    """Absent is not small. A missing confidence must not lead a worst-first
    table, which would put the least-known row at the top of the most-urgent
    list."""
    from slpie.warehouse import query

    filled = star("relationships").fact.with_rows((
        {"edge_id": "a", "confidence": 0.4},
        {"edge_id": "b"},
        {"edge_id": "c", "confidence": 0.9},
    ))
    subject = Star(name="relationships", doc="", fact=filled,
                   dimensions=star("relationships").dimensions)

    for descending in (True, False):
        ordered = [row["edge_id"] for row in
                   query.rows(subject, sort="confidence", descending=descending)]
        assert ordered[-1] == "b", descending


def test_the_column_spec_is_the_shape_the_browser_takes():
    """Emitted as plain dictionaries to dodge an import cycle, so the shape is
    pinned against the contract's own column rather than assumed."""
    from slpie.ui.contract import FORMATS, Column
    from slpie.warehouse import query

    shape = set(Column(key="x").to_dict())
    for spec in query.columns(star("findings")):
        assert set(spec) == shape, spec
        assert spec["format"] in FORMATS, spec


# --- the dashboard verb, which is where all of it arrives ---------------------


def _dashboard(repository, **arguments):
    from slpie.compose import Composition, Context
    from slpie.compose.registry import registry

    flags = "".join(f" --{key} {value}" for key, value in arguments.items())
    return Composition.read(f"discover {repository} | dashboard{flags}",
                            verbs=registry()).run(Context(root=str(repository)))


def test_a_dashboard_arrives_drawable(repository):
    """Every panel carries its own rows, so the browser needs no second request
    and a saved flow renders offline."""
    result = _dashboard(repository, template="architecture-map")
    panels = result.flow.value["panels"]

    assert result.flow.value["template"]["key"] == "architecture-map"
    assert panels, "the template lost its panels"
    for panel in panels:
        assert "data" in panel and "columns" in panel
    grids = [panel for panel in panels if panel["component"] == "grid"]
    assert grids and grids[0]["columns"], "a grid with no columns draws nothing"


def test_a_breakdown_panel_carries_label_and_value(repository):
    """The shape the browser's bar list takes, so it renders unadapted."""
    result = _dashboard(repository, template="architecture-map")
    bars = [panel for panel in result.flow.value["panels"]
            if panel["component"] == "bars"]

    assert bars, "the architecture map lost its breakdowns"
    for row in bars[0]["data"]:
        assert set(row) >= {"label", "value", "rows"}


def test_a_panel_reading_a_source_this_ring_lacks_says_so(repository):
    """The queue lives in the enterprise ring. An air-gapped console draws the
    panel empty and states the gap rather than hiding it, which would look like
    an estate with no jobs."""
    result = _dashboard(repository, template="operations")
    empty = [panel for panel in result.flow.value["panels"]
             if panel["options"].get("source") == "queue"]

    assert empty and all(panel["data"] == [] for panel in empty)
    assert any("queue" in gap.detail for gap in result.flow.gaps)


def test_an_unnamed_demand_still_chooses_and_says_why(repository):
    """Selection is the point of the template engine — a menu the reader has to
    understand first is the thing it replaces."""
    result = _dashboard(repository, domain="security", utility="monitor")
    selection = result.flow.value["selection"]

    assert selection["template"] == "security-board"
    assert selection["reason"]


# --- the typed pipe, checked where it was lying --------------------------------


MANIFEST = """
apiVersion: slpie/v1
environment: acme
target: simulated
codebase:
  - root: ./services/payments
    team: payments
"""


@pytest.fixture()
def opened(tmp_path):
    """An engine with a simulated world attached — what `scan` reads."""
    from slpie.engine import Engine

    engine = Engine.from_text(MANIFEST)
    engine.declare()
    engine.simulate(root=str(tmp_path / "world"))
    engine.attach()
    yield engine
    engine.close()


def test_scan_carries_observations_rather_than_a_count(opened):
    """`ScanReport.to_dict()["observations"]` is a number, and the verb was
    putting it on a flow that declares OBSERVATIONS.

    The composition type-checked and then died at the second stage on
    `'int' object has no attribute 'evidence'`, which is worse than an untyped
    pipe: the check passed and the promise did not hold.
    """
    from slpie.compose import Composition, Context
    from slpie.compose.registry import registry

    result = Composition.read("scan", verbs=registry()).run(Context(engine=opened))

    assert result.ok, result.error
    assert not isinstance(result.flow.value, int), (
        "scan is carrying a count on a flow that promises observations"
    )
    assert result.flow.items, "scan produced no observations to pass on"


@pytest.mark.parametrize("downstream", ["warehouse", "govern", "link", "dashboard"])
def test_everything_that_consumes_observations_can_follow_scan(opened, downstream):
    """The claim a typed pipe makes, asserted across every consumer rather than
    spot-checked on the one that happened to be under construction."""
    from slpie.compose import Composition, Context
    from slpie.compose.registry import registry

    result = Composition.read(f"scan | {downstream}",
                              verbs=registry()).run(Context(engine=opened))

    assert result.ok, f"scan | {downstream} failed: {result.error}"


def test_a_fact_serialises_with_its_grain():
    """`slots=True` rebuilds the class, so a zero-argument `super()` inside a
    dataclass method closes over the wrong one and raises.

    The grain is the first thing anyone reading a fact table needs, so an
    export that could not serialise one was the whole point of the table lost
    at the last step.
    """
    for item in STARS:
        payload = item.fact.to_dict()
        assert payload["grain"], f"{item.fact.name} shipped without its grain"
        assert payload["columns"], f"{item.fact.name} shipped without its columns"


def test_a_grid_shows_the_name_and_keeps_the_id():
    """A subject column reading `f92e259c841f97b1…` is a table nobody can use.

    Both travel: the id is what the link needs and the name is what the reader
    needs, so the name leads and the id stays for the dense register.
    """
    from slpie.warehouse import query

    specs = {spec["key"]: spec for spec in query.columns(star("findings"))}

    assert "subject_name" in specs, "the subject arrives as a digest only"
    assert specs["subject_name"]["link"] == "#/node/:subject"
    assert specs["subject_name"]["density"] == ""
    assert specs["subject"]["density"] == "dense"
    assert specs["subject"]["format"] == "mono"
