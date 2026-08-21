"""Phase 12 — the artifacts a release ships, and the one Gratimos import.

Four themes:

**The bridge works, and hand edits survive it.** `slpie/artifacts/codegen.py` is
the single module invariant 8 permits to import Gratimos. Architecture-as-code
that overwrote an architect's reasoning on every run would be worse than a
diagram, so a `# gratimos:keep` annotation survives unconditionally and a genuine
conflict *raises* rather than silently picking a winner.

**Nothing invents a fact.** A licence appears only where a node carries one, a
hash only where evidence supplied one, a classification only where the graph
derived one. Views are projections, not a second store.

**Nothing reads the clock.** An SBOM whose bytes change on every run cannot be
diffed, attested or checked into a release. The timestamp is an argument and the
serial number is derived from content, so identical graphs produce identical
documents.

**An empty view is reported, not dropped.** A data architecture with no entities
usually means nothing scanned the warehouse — a fact about coverage. Hiding the
view would turn a visible gap into an absence nobody notices.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pytest

from slpie.artifacts.c4 import C4Level, c4_views, code_view, component_view
from slpie.artifacts.codegen import (
    KEEP_MARKER,
    ArchitectureCodegen,
    ArchitectureConflict,
    EmittedView,
    MergePolicy,
    emit_views,
    shape_for,
)
from slpie.artifacts.sbom import (
    SbomOptions,
    cyclonedx_document,
    spdx_document,
    write_sbom,
)
from slpie.compose import Composition, Context, Kind, VerbError, registry
from slpie.domain.edge import Edge, EdgeKind
from slpie.domain.evidence import Evidence, EvidenceKind, SourceLocation
from slpie.domain.finding import Finding, FindingKind, Remediation
from slpie.domain.identity import Purl, Urn
from slpie.domain.lifecycle import Severity
from slpie.domain.node import Node, NodeKind
from slpie.enterprise import (
    Risk,
    View,
    application_view,
    data_view,
    environments,
    heat_map,
    identifier,
    register,
    relations_between,
    report,
    risk_view,
    standards_view,
    technology_view,
    togaf_views,
    topology_view,
    undeployed,
    unique,
)
from slpie.enterprise.topology import UNPLACED, place_of
from slpie.errors import ArtifactError
from slpie.governance.view import view_from_resolution, view_of
from slpie.graph.sqlite_graph import SqliteGraph
from slpie.plugins.protocol import Observation
from slpie.present import c4 as present_c4

from _trees import EXAMPLE_AWS_KEY, write_npm


def evidence(uri: str = "file:///r/package-lock.json", line: int = 1,
             kind: EvidenceKind = EvidenceKind.LOCKFILE_PIN,
             **labels: str) -> Evidence:
    return Evidence(
        kind=kind, location=SourceLocation(uri, line=line),
        extractor="test", excerpt=f"{uri}:{line}", labels=labels,
    )


def node(identity: str, kind: NodeKind = NodeKind.PACKAGE, **properties: Any) -> Node:
    parsed = Purl.parse(identity) if identity.startswith("pkg:") else Urn.parse(identity)
    return Node(kind=kind, identity=parsed, evidence=(evidence(),), properties=properties)


@pytest.fixture()
def graph():
    """A small estate: two packages, a service, a database, a deployment."""
    built = SqliteGraph(None)
    lodash = node("pkg:npm/lodash@4.17.21", license="MIT",
                  hashes={"sha512": "abc123"})
    left = node("pkg:npm/left-pad@1.3.0", license="MIT OR Apache-2.0")
    app = node("pkg:npm/app@1.0.0", license="NoSuchLicence-9.9")
    api = node("urn:slpie:service:payments/api", NodeKind.SERVICE,
               team="billing", domain="payments", environment="prod", zone="eu-west-1")
    store = node("urn:slpie:table:warehouse/public.orders", NodeKind.TABLE,
                 classification="pii")
    # Declared and never placed. The interesting element on a topology: it is
    # in the architecture and nothing says where it runs.
    orphan = node("urn:slpie:service:reports/api", NodeKind.SERVICE, team="data")
    deployment = node("urn:slpie:deployment:prod/eu-west-1/payments",
                      NodeKind.DEPLOYMENT, runtime="python", runtime_version="3.11",
                      environment="prod", zone="eu-west-1", replicas="3")

    for item in (lodash, left, app, api, store, deployment, orphan):
        built.assert_node(item)
    built.assert_edge(Edge(
        kind=EdgeKind.DEPENDS_ON, src=app.id, dst=lodash.id, evidence=(evidence(),),
    ))
    built.assert_edge(Edge(
        kind=EdgeKind.DEPENDS_ON, src=app.id, dst=left.id, evidence=(evidence(),),
    ))
    built.assert_edge(Edge(
        kind=EdgeKind.READS, src=api.id, dst=store.id, evidence=(evidence(),),
    ))
    built.assert_edge(Edge(
        kind=EdgeKind.DEPLOYS_TO, src=api.id, dst=deployment.id,
        evidence=(evidence(),),
    ))
    built.nodes_by_identity = {  # convenience for tests that need one back
        "app": app, "lodash": lodash, "api": api, "store": store,
        "deployment": deployment, "left": left, "orphan": orphan,
    }
    yield built
    built.close()


# --- the bridge: the one Gratimos import, executed --------------------------


@dataclass(frozen=True)
class Simple:
    """A minimal ArchitectureView, so bridge tests do not depend on TOGAF."""

    rows_: tuple = ()
    name: str = "sample"
    doc: str = "a sample view"

    def rows(self):
        return self.rows_

    def to_dict(self):
        return {"name": self.name, "rows": [dict(row) for row in self.rows_]}

    def to_diagram(self):
        """The protocol asks for a shape, not a picture.

        This fake states one directly rather than building it from `rows_`,
        because what it is standing in for is *any* view — and a fake that
        derived its diagram would be testing the derivation rather than the
        bridge that consumes it.
        """
        from slpie.present.diagram import Diagram, Link, Mark

        return Diagram(
            name=self.name, orientation="top-down",
            marks=(Mark(id="A"), Mark(id="B")), links=(Link(source="A", target="B"),),
        )


ONE = ({"id": "PAYMENTS", "label": "Payments", "kind": "service", "team": "billing"},)
TWO = (*ONE, {"id": "ORDERS", "label": "Orders", "kind": "service"})



def _render(view):
    """Render a view exactly as the product does — through `slpie.present`.

    Rendering left the models: a view states its shape and the presentation
    tier draws it. These tests reach it the same way rather than through a
    method that no longer exists, which is the point of the split.
    """
    from slpie.present import c4, mermaid

    if hasattr(view, "level") and hasattr(view, "relationships"):
        return c4.mermaid(view)
    return mermaid(view.to_diagram())


def test_the_bridge_generates_importable_python(tmp_path):
    """The single permitted Gratimos import, executed for real."""
    codegen = ArchitectureCodegen(tmp_path)
    emitted = codegen.emit(Simple(ONE))

    assert emitted.module_path.is_file()
    assert emitted.mermaid_path.read_text(encoding="utf-8").startswith("graph TD")
    assert json.loads(emitted.json_path.read_text(encoding="utf-8"))["name"] == "sample"

    module = codegen.load("sample")
    assert hasattr(module, "SHAPE_DIGEST")
    assert [field.name for field in module.SHAPE.fields] == ["PAYMENTS"]
    assert "payments" in {
        field.name for field in dataclasses.fields(module.Sample)
    }, "the element became a real dataclass field a caller can name"


def test_a_hand_edit_marked_keep_survives_regeneration(tmp_path):
    """The headline claim of architecture-as-code."""
    codegen = ArchitectureCodegen(tmp_path)
    first = codegen.emit(Simple(ONE))

    text = first.module_path.read_text(encoding="utf-8")
    first.module_path.write_text(
        text + f'\n\n{KEEP_MARKER}\nWHY = "reporting sits outside the boundary"\n',
        encoding="utf-8",
    )

    second = codegen.emit(Simple(TWO))
    after = second.module_path.read_text(encoding="utf-8")

    assert "WHY" in after, "an architect's reasoning was destroyed by regeneration"
    assert "ORDERS" in after, "and the new element still arrived"
    assert "WHY" in second.kept


def test_a_genuine_conflict_raises_rather_than_choosing_a_winner(tmp_path):
    """Either choice loses somebody's work with no record of it."""
    codegen = ArchitectureCodegen(tmp_path)
    first = codegen.emit(Simple(ONE))

    # Edit a *generated* symbol by hand, without pinning it.
    text = first.module_path.read_text(encoding="utf-8")
    first.module_path.write_text(
        text.replace('SHAPE_REVISION = 1', 'SHAPE_REVISION = 99'), encoding="utf-8",
    )

    with pytest.raises(ArchitectureConflict) as raised:
        codegen.emit(Simple(TWO))

    assert raised.value.symbol
    assert KEEP_MARKER in str(raised.value), "the message says how to pin it"
    assert raised.value.view == "sample"


def test_a_policy_can_prefer_the_local_definition_instead_of_raising(tmp_path):
    codegen = ArchitectureCodegen(tmp_path, policy=MergePolicy.PREFER_LOCAL)
    first = codegen.emit(Simple(ONE))
    text = first.module_path.read_text(encoding="utf-8")
    first.module_path.write_text(
        text.replace("SHAPE_REVISION = 1", "SHAPE_REVISION = 99"), encoding="utf-8",
    )

    second = codegen.emit(Simple(TWO))
    assert second.revision >= first.revision


def test_a_policy_may_be_overridden_per_emission(tmp_path):
    codegen = ArchitectureCodegen(tmp_path, policy=MergePolicy.RAISE)
    first = codegen.emit(Simple(ONE))
    text = first.module_path.read_text(encoding="utf-8")
    first.module_path.write_text(
        text.replace("SHAPE_REVISION = 1", "SHAPE_REVISION = 99"), encoding="utf-8",
    )

    second = codegen.emit(Simple(TWO), policy=MergePolicy.PREFER_GENERATED)
    assert second.rewritten


def test_regenerating_an_unmoved_view_does_not_rewrite_the_module(tmp_path):
    """The shape digest is what makes regeneration cheap."""
    codegen = ArchitectureCodegen(tmp_path)
    first = codegen.emit(Simple(ONE))
    second = codegen.emit(Simple(ONE))

    assert second.shape_digest == first.shape_digest
    assert not second.rewritten


def test_a_view_row_without_an_id_is_refused():
    with pytest.raises(ArtifactError, match="no 'id'"):
        shape_for(Simple(({"label": "nameless"},)))


def test_an_empty_view_cannot_be_generated():
    """There is nothing to generate, and a zero-field dataclass says nothing."""
    with pytest.raises(ArtifactError, match="no elements"):
        shape_for(Simple(()))


def test_the_shape_carries_one_field_per_element():
    shape = shape_for(Simple(TWO))

    assert shape.rows == 2
    assert {field.name for field in shape.fields} == {"PAYMENTS", "ORDERS"}
    assert shape.kind == "architecture-view"


def test_emitting_several_views_reports_each(tmp_path):
    emitted = emit_views(tmp_path, [Simple(ONE), Simple(TWO, name="second")])

    assert [item.name for item in emitted] == ["sample", "second"]
    assert all(isinstance(item, EmittedView) for item in emitted)


def test_the_codegen_reports_its_own_state(tmp_path):
    codegen = ArchitectureCodegen(tmp_path)
    codegen.emit(Simple(ONE))
    codegen.emit(Simple(TWO))

    assert codegen.views() == ("sample",)
    assert codegen.revisions("sample") == (1, 2)
    body = codegen.report()
    assert body["root"] == str(tmp_path.resolve())
    assert body["policy"] == "raise"


def test_a_generated_file_broken_by_hand_is_reported_against_its_view(tmp_path):
    """The merge base has to parse; when it cannot, say which view and why.

    Realistic rather than theoretical: somebody edits the generated module,
    leaves a syntax error, and the next regeneration cannot three-way merge.
    Letting Gratimos's own error escape would name a file and not the view.
    """
    codegen = ArchitectureCodegen(tmp_path)
    first = codegen.emit(Simple(ONE))
    first.module_path.write_text("def broken(:\n", encoding="utf-8")

    with pytest.raises(ArtifactError, match="sample: cannot parse"):
        codegen.emit(Simple(TWO))


def test_loading_a_view_that_was_never_generated_is_refused(tmp_path):
    with pytest.raises(ArtifactError, match="cannot load"):
        ArchitectureCodegen(tmp_path).load("nothing")


def test_a_fresh_instance_can_load_what_an_earlier_run_generated(tmp_path):
    """The generated module is the artifact; the history is only for merges.

    Generation history lives in memory, so a new process pointed at an existing
    `architecture/` has none — and refusing there would be refusing to open a
    file sitting in front of it. Architecture-as-code that only the generating
    process can import is not architecture-as-code.
    """
    ArchitectureCodegen(tmp_path).emit(Simple(ONE))

    module = ArchitectureCodegen(tmp_path).load("sample")

    assert [field.name for field in module.SHAPE.fields] == ["PAYMENTS"]
    assert dataclasses.fields(module.Sample), "a real dataclass, freshly imported"


def test_a_generated_file_that_does_not_import_says_so_rather_than_vanishing(
    tmp_path,
):
    """Distinguish 'never generated' from 'generated and broken'."""
    emitted = ArchitectureCodegen(tmp_path).emit(Simple(ONE))
    emitted.module_path.write_text("raise RuntimeError('boom')\n", encoding="utf-8")

    with pytest.raises(ArtifactError, match="on disk but does not import"):
        ArchitectureCodegen(tmp_path).load("sample")


def test_an_emitted_view_serialises_for_a_report(tmp_path):
    emitted = ArchitectureCodegen(tmp_path).emit(Simple(ONE))
    body = emitted.to_dict()

    assert body["name"] == "sample"
    assert body["elements"] == 1
    assert body["rewritten"] is True
    assert isinstance(body["decisions"], list)


# --- SBOM -------------------------------------------------------------------


def test_a_cyclonedx_document_passes_purls_through_untranslated(graph):
    document = cyclonedx_document(graph)

    refs = {component["bom-ref"] for component in document.document["components"]}
    assert "pkg:npm/lodash@4.17.21" in refs, "identity is passed through, not mapped"
    assert document.document["specVersion"] == "1.5"
    assert document.components >= 3


def test_the_same_graph_produces_the_same_document_twice(graph):
    """An SBOM that changed on every run cannot be diffed or attested."""
    first = cyclonedx_document(graph).to_json()
    second = cyclonedx_document(graph).to_json()

    assert first == second
    assert json.loads(first)["serialNumber"].startswith("urn:uuid:")


def test_a_timestamp_is_supplied_and_never_read_from_the_clock(graph):
    without = cyclonedx_document(graph)
    with_stamp = cyclonedx_document(
        graph, options=SbomOptions(timestamp=1_700_000_000),
    )

    assert "timestamp" not in without.document["metadata"]
    assert with_stamp.document["metadata"]["timestamp"] == "2023-11-14T22:13:20Z"


def test_a_licence_choice_is_kept_as_an_expression(graph):
    """Flattening `MIT OR Apache-2.0` discards the part a reviewer needs."""
    document = cyclonedx_document(graph)
    by_ref = {c["bom-ref"]: c for c in document.document["components"]}

    assert by_ref["pkg:npm/left-pad@1.3.0"]["licenses"] == [
        {"expression": "MIT OR Apache-2.0"}
    ]
    assert by_ref["pkg:npm/lodash@4.17.21"]["licenses"] == [
        {"license": {"id": "MIT"}}
    ]


def test_an_unrecognised_licence_travels_as_a_name_not_an_id(graph):
    document = cyclonedx_document(graph)
    by_ref = {c["bom-ref"]: c for c in document.document["components"]}
    licences = by_ref["pkg:npm/app@1.0.0"].get("licenses", [])

    assert licences, "the declaration is still reported"
    assert "id" not in licences[0].get("license", {}), (
        "an unknown identifier must not be asserted as SPDX"
    )


def test_a_hash_appears_only_where_something_supplied_one(graph):
    document = cyclonedx_document(graph)
    by_ref = {c["bom-ref"]: c for c in document.document["components"]}

    assert by_ref["pkg:npm/lodash@4.17.21"]["hashes"] == [
        {"alg": "SHA-512", "content": "abc123"}
    ]
    assert "hashes" not in by_ref["pkg:npm/left-pad@1.3.0"]


def test_an_unrecognised_hash_algorithm_is_skipped():
    """CycloneDX names its algorithms; anything else cannot be written as one."""
    built = SqliteGraph(None)
    built.assert_node(node("pkg:npm/x@1.0.0",
                           hashes={"crc32": "aaaa", "sha256": "bbbb", "sha1": ""}))
    component = cyclonedx_document(built).document["components"][0]
    built.close()

    assert component["hashes"] == [{"alg": "SHA-256", "content": "bbbb"}]


def test_a_dependency_on_something_outside_the_document_is_omitted():
    """A dangling ref makes a consuming tool error or drop the edge silently."""
    built = SqliteGraph(None)
    app = node("pkg:npm/app@1.0.0")
    team = node("urn:slpie:team:payments", NodeKind.TEAM)   # not a component
    built.assert_node(app)
    built.assert_node(team)
    built.assert_edge(Edge(kind=EdgeKind.DEPENDS_ON, src=app.id, dst=team.id,
                           evidence=(evidence(),)))
    document = cyclonedx_document(built)
    built.close()

    assert document.document["dependencies"] == [
        {"ref": "pkg:npm/app@1.0.0", "dependsOn": []}
    ]


def test_a_hash_from_evidence_is_honoured_when_the_algorithm_is_stated():
    built = SqliteGraph(None)
    item = Node(
        kind=NodeKind.PACKAGE, identity=Purl.parse("pkg:npm/x@1.0.0"),
        evidence=(Evidence(
            kind=EvidenceKind.LOCKFILE_PIN,
            location=SourceLocation("file:///r/lock.json", line=1),
            extractor="test", excerpt="x", content_digest="deadbeef",
            labels={"hash_alg": "sha256"},
        ),),
    )
    built.assert_node(item)
    document = cyclonedx_document(built)
    built.close()

    assert document.document["components"][0]["hashes"] == [
        {"alg": "SHA-256", "content": "deadbeef"}
    ]


def test_a_digest_with_no_stated_algorithm_is_skipped():
    """Writing a digest under a guessed algorithm fails verification obscurely."""
    built = SqliteGraph(None)
    built.assert_node(Node(
        kind=NodeKind.PACKAGE, identity=Purl.parse("pkg:npm/x@1.0.0"),
        evidence=(Evidence(
            kind=EvidenceKind.LOCKFILE_PIN,
            location=SourceLocation("file:///r/lock.json", line=1),
            extractor="test", excerpt="x", content_digest="deadbeef",
        ),),
    ))
    document = cyclonedx_document(built)
    built.close()

    assert "hashes" not in document.document["components"][0]


def test_every_dependency_reference_resolves_inside_the_document(graph):
    """A dangling ref makes a consuming tool error or silently drop the edge."""
    document = cyclonedx_document(graph)
    refs = {c["bom-ref"] for c in document.document["components"]}
    refs |= {
        d["ref"] for d in document.document["dependencies"]
        if d["ref"] not in refs
    }

    for entry in document.document["dependencies"]:
        for target in entry["dependsOn"]:
            assert target in refs, f"{target} dangles"


def test_naming_a_subject_adds_it_to_the_dependency_graph(graph):
    document = cyclonedx_document(
        graph, options=SbomOptions(subject="shop", subject_version="2.0"),
    )
    metadata = document.document["metadata"]

    assert metadata["component"]["name"] == "shop"
    assert metadata["component"]["version"] == "2.0"
    assert document.document["dependencies"][0]["ref"] == metadata["component"]["bom-ref"]


def test_a_scoped_package_carries_its_namespace_as_a_group():
    built = SqliteGraph(None)
    built.assert_node(node("pkg:npm/%40acme/widgets@1.0.0",
                           description="the widget library"))
    component = cyclonedx_document(built).document["components"][0]
    built.close()

    assert component["group"] == "@acme"
    assert component["version"] == "1.0.0"
    assert component["description"] == "the widget library"


def test_a_non_package_element_carries_no_group_it_does_not_have():
    """CycloneDX has no slot for a bespoke scheme; the urn travels as the ref."""
    built = SqliteGraph(None)
    built.assert_node(node("urn:slpie:service:payments/api", NodeKind.SERVICE))
    component = cyclonedx_document(built).document["components"][0]
    built.close()

    assert component["bom-ref"] == "urn:slpie:service:payments/api"
    assert component["group"] == "payments"
    assert "purl" not in component


def test_a_urn_with_no_namespace_omits_the_group_rather_than_writing_an_empty_one():
    """An empty `group` is a field a consumer reads and learns nothing from."""
    built = SqliteGraph(None)
    built.assert_node(node("urn:slpie:domain:billing", NodeKind.COMPONENT))
    component = cyclonedx_document(built).document["components"][0]
    built.close()

    assert "group" not in component


def test_an_empty_graph_has_no_sbom_to_emit():
    built = SqliteGraph(None)
    with pytest.raises(ArtifactError, match="no component nodes"):
        cyclonedx_document(built)
    with pytest.raises(ArtifactError, match="no component nodes"):
        spdx_document(built)
    built.close()


def test_spdx_distinguishes_a_declaration_from_a_conclusion(graph):
    """SLPIE observes declarations; it does not run a licence scanner."""
    document = spdx_document(graph)
    packages = {p["name"]: p for p in document.document["packages"]}

    assert packages["lodash"]["licenseDeclared"] == "MIT"
    assert packages["lodash"]["licenseConcluded"] == "NOASSERTION"


def test_spdx_carries_the_purl_as_an_external_reference(graph):
    document = spdx_document(graph)
    packages = {p["name"]: p for p in document.document["packages"]}

    assert packages["lodash"]["externalRefs"][0] == {
        "referenceCategory": "PACKAGE-MANAGER",
        "referenceType": "purl",
        "referenceLocator": "pkg:npm/lodash@4.17.21",
    }


def test_spdx_describes_every_package_it_lists(graph):
    document = spdx_document(graph)
    described = {
        r["relatedSpdxElement"] for r in document.document["relationships"]
        if r["relationshipType"] == "DESCRIBES"
    }

    assert described == {p["SPDXID"] for p in document.document["packages"]}


def test_a_cyclic_graph_still_produces_resolvable_roots():
    built = SqliteGraph(None)
    one = node("pkg:npm/one@1.0.0")
    two = node("pkg:npm/two@1.0.0")
    built.assert_node(one)
    built.assert_node(two)
    built.assert_edge(Edge(kind=EdgeKind.DEPENDS_ON, src=one.id, dst=two.id,
                           evidence=(evidence(),)))
    built.assert_edge(Edge(kind=EdgeKind.DEPENDS_ON, src=two.id, dst=one.id,
                           evidence=(evidence(),)))

    document = cyclonedx_document(built, options=SbomOptions(subject="cyclic"))
    built.close()

    roots = document.document["dependencies"][0]["dependsOn"]
    assert roots, "a cycle leaves no root; every component is then direct"


def test_a_document_is_written_as_canonical_json(tmp_path, graph):
    document = cyclonedx_document(graph)
    path = write_sbom(document, tmp_path / "nested" / "sbom.json")

    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8"))["bomFormat"] == "CycloneDX"
    assert document.to_dict()["components"] == document.components


# --- C4 ---------------------------------------------------------------------


def test_c4_builds_the_levels_the_graph_supports(graph):
    views = c4_views(graph)

    assert [view.level for view in views] == [C4Level.CONTEXT, C4Level.CONTAINER]
    assert all(_render(view).startswith("%%") or "flowchart" in _render(view)
               for view in views)


def test_c3_and_c4_are_built_only_when_a_subject_is_named(graph):
    container = graph.nodes_by_identity["api"].id
    views = c4_views(graph, container=container, component=container)

    assert [view.level for view in views] == [
        C4Level.CONTEXT, C4Level.CONTAINER, C4Level.COMPONENT, C4Level.CODE,
    ]


def test_a_component_view_without_a_container_is_refused(graph):
    with pytest.raises(ArtifactError, match="node id of a container"):
        component_view(graph, "")
    with pytest.raises(ArtifactError, match="node id of a component"):
        code_view(graph, "")


def test_a_c4_view_serialises_with_its_level(graph):
    body = c4_views(graph)[0].to_dict()

    assert body["level"] == "context"
    assert "elements" in body and "relationships" in body


@pytest.fixture()
def drawable():
    """A graph C1 can actually draw: a domain, a team, and an external provider."""
    built = SqliteGraph(None)
    domain = node("urn:slpie:domain:billing", NodeKind.DOMAIN)
    team = node("urn:slpie:team:payments", NodeKind.TEAM)
    stripe = node("urn:slpie:provider:stripe", NodeKind.EXTERNAL_PROVIDER)
    for item in (domain, team, stripe):
        built.assert_node(item)
    built.assert_edge(Edge(
        kind=EdgeKind.OWNS, src=team.id, dst=domain.id, evidence=(evidence(),),
    ))
    built.assert_edge(Edge(
        kind=EdgeKind.CALLS, src=domain.id, dst=stripe.id, evidence=(evidence(),),
        qualifier="webhook",
    ))
    # Asserted twice with the same shape: the renderer must draw one arrow.
    built.assert_edge(Edge(
        kind=EdgeKind.CALLS, src=domain.id, dst=stripe.id, evidence=(evidence(
            "file:///r/other.json", 9,
        ),), qualifier="webhook",
    ))
    built.nodes_by_identity = {"domain": domain, "team": team, "stripe": stripe}
    yield built
    built.close()


def test_an_external_provider_is_drawn_inside_its_own_subgraph(drawable):
    """C1's whole point is the line between the system and everything else."""
    mermaid = _render(c4_views(drawable)[0])

    assert 'subgraph external["External"]' in mermaid
    assert mermaid.count("    end") == 1


def test_a_relationship_is_drawn_once_however_often_it_was_observed(drawable):
    view = c4_views(drawable)[0]
    mermaid = _render(view)

    assert mermaid.count("-->") == len(view.relationships)
    assert len([r for r in view.relationships if "webhook" in r.label]) == 1


def test_a_relationship_label_carries_its_qualifier(drawable):
    view = c4_views(drawable)[0]
    labelled = [r for r in view.relationships if r.qualifier]

    assert labelled and "webhook" in str(labelled[0])
    assert labelled[0].to_dict()["qualifier"] == "webhook"


def test_a_c4_element_serialises_everything_a_client_renders(drawable):
    element = c4_views(drawable)[0].elements[0].to_dict()

    assert {"alias", "label", "kind", "node_id"} <= set(element)


def test_c4_rows_are_shaped_for_the_code_generator(drawable):
    """A C4 view is emittable too, which means it must satisfy the protocol."""
    rows = c4_views(drawable)[0].rows()

    assert rows and all(row["id"] for row in rows)
    assert any(row["external"] for row in rows)


# --- enterprise views -------------------------------------------------------


def test_the_application_view_selects_what_serves_requests(graph):
    view = application_view(graph)
    labels = {row["label"] for row in view.elements}

    assert "payments/api" in labels
    assert not any(row["kind"] == "package" for row in view.elements)


def test_a_view_reads_what_the_graph_derived_and_adds_nothing(graph):
    row = next(
        row for row in application_view(graph).elements
        if row["label"] == "payments/api"
    )

    assert row["team"] == "billing"
    assert row["confidence"] == round(
        graph.nodes_by_identity["api"].confidence, 4
    )
    assert row["lifecycle"] == graph.nodes_by_identity["api"].lifecycle.value


def test_the_data_view_carries_the_classification(graph):
    row = data_view(graph).elements[0]

    assert row["classification"] == "pii"
    assert row["kind"] == "table"


def test_the_technology_view_selects_what_runs(graph):
    view = technology_view(graph)

    assert [row["kind"] for row in view.elements] == ["deployment"]
    assert view.elements[0]["runtime"] == "python"


def test_the_standards_catalogue_aggregates_rather_than_lists(graph):
    """The question is the estate's spread, which a list of nodes cannot answer."""
    view = standards_view(graph)

    assert len(view.elements) == 1
    assert view.elements[0]["runtime"] == "python"
    assert view.elements[0]["version"] == "3.11"
    assert view.elements[0]["instances"] == 1
    assert "payments" in view.elements[0]["examples"]


def test_a_versioned_element_carries_its_version(graph):
    """A deployment or API often has one; the row must not lose it."""
    built = SqliteGraph(None)
    built.assert_node(node("urn:slpie:deviceclass:fleet/temp-v2",
                           NodeKind.DEVICE_CLASS, runtime="firmware"))
    built.assert_node(node("pkg:npm/x@1.0.0", NodeKind.COMPONENT))
    rows = application_view(built).elements
    built.close()

    assert rows[0]["version"] == "1.0.0"


def test_an_empty_view_is_kept_rather_than_dropped():
    """A data architecture with no entities is a fact about coverage."""
    built = SqliteGraph(None)
    views = togaf_views(built)
    built.close()

    assert len(views) == 4, "every view is present"
    assert all(view.empty for view in views)


def test_a_view_renders_relations_that_are_closed_over_it(graph):
    """An arrow to a box not on the diagram reads as a missing element."""
    view = data_view(graph)
    mermaid = _render(view)

    for row in view.elements:
        assert row["id"] in mermaid
    for source, _kind, target in view.relations:
        assert source in mermaid and target in mermaid


def test_relations_omit_edges_that_leave_the_view(graph):
    """`api reads store` spans two views, so neither draws it alone."""
    nodes = [graph.nodes_by_identity["api"]]
    found = relations_between(graph, nodes, kinds=(EdgeKind.READS,))

    assert found == (), "the target is not in this selection"


def test_relations_can_be_bounded(graph):
    nodes = list(graph.nodes(live=True))
    assert len(relations_between(graph, nodes, limit=1)) <= 1


def test_a_view_serialises_and_measures_itself(graph):
    view = application_view(graph)
    body = view.to_dict()

    assert body["counts"]["elements"] == len(view) == len(view.elements)
    assert body["name"] == "application"
    assert "element" in str(view)
    assert not view.empty


def test_a_view_with_no_relations_still_renders_its_boxes():
    view = View(name="x", doc="d", elements=({"id": "A", "label": "A", "kind": "k"},))

    mermaid = _render(view)
    assert "A[" in mermaid
    assert "-->" not in mermaid


def test_mermaid_labels_cannot_end_the_label_early():
    view = View(
        name="x", doc="d",
        elements=({"id": "A", "label": 'a "quoted" [thing]', "kind": ""},),
    )
    mermaid = _render(view)

    assert '"quoted"' not in mermaid
    assert "[thing]" not in mermaid


# --- identifiers ------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("payments-api", "PAYMENTS_API"),
        ("@scope/pkg", "SCOPE_PKG"),
        ("pkg:npm/lodash@4.17.21", "PKG_NPM_LODASH_4_17_21"),
        ("3rd-party", "N_3RD_PARTY"),
        ("", "ELEMENT"),
        ("...", "ELEMENT"),
        ("a  b", "A_B"),
    ],
)
def test_an_identifier_is_always_valid_python(text, expected):
    """A view that raised on an awkward name would fail on the trees worth describing."""
    assert identifier(text) == expected
    assert identifier(text).isidentifier()


def test_colliding_identifiers_are_suffixed_rather_than_dropped():
    """`my-lib` and `my_lib` are different packages; losing one loses an element."""
    rows = unique([
        {"id": "MY_LIB", "label": "my-lib"},
        {"id": "MY_LIB", "label": "my_lib"},
        {"id": "OTHER", "label": "other"},
    ])

    assert [row["id"] for row in rows] == ["MY_LIB", "MY_LIB_2", "OTHER"]
    assert len({row["id"] for row in rows}) == 3


def test_a_row_with_no_id_still_gets_one():
    assert unique([{"label": "x"}])[0]["id"] == "ELEMENT"


# --- topology ---------------------------------------------------------------


def test_the_topology_places_what_it_can(graph):
    view = topology_view(graph)
    placed = {row["label"]: (row["environment"], row["zone"]) for row in view.elements}

    assert placed["prod/eu-west-1/payments"] == ("prod", "eu-west-1")
    assert placed["payments/api"] == ("prod", "eu-west-1")


def test_something_with_no_stated_place_is_shown_as_unknown(graph):
    """Hiding it would make the topology look tidier than the estate is."""
    view = topology_view(graph)
    unknown = [row for row in view.elements if row["environment"] == UNPLACED]

    assert [row["label"] for row in unknown] == ["reports/api"]
    assert unknown[0]["zone"] == UNPLACED


def test_environments_group_by_place(graph):
    grouped = environments(graph)

    assert "prod" in grouped
    assert "eu-west-1" in grouped["prod"]
    assert UNPLACED in grouped


def test_place_of_prefers_an_explicit_environment():
    assert place_of(node("urn:slpie:service:a/b", NodeKind.SERVICE,
                         environment="prod", cluster="c1"))[0] == "prod"
    assert place_of(node("urn:slpie:service:a/b", NodeKind.SERVICE,
                         cluster="c1"))[0] == "c1"
    assert place_of(node("urn:slpie:service:a/b", NodeKind.SERVICE))[0] == UNPLACED


def test_undeployed_reports_the_delta_between_existing_and_running(graph):
    """The reason topology is a view rather than a filter on the application one."""
    assert [item.display for item in undeployed(graph)] == ["reports/api"]


def test_a_deployed_service_is_not_reported_as_undeployed(graph):
    assert graph.nodes_by_identity["api"].id not in {
        item.id for item in undeployed(graph)
    }


# --- risk -------------------------------------------------------------------


def finding(subject: str, severity: Severity,
            kind: FindingKind = FindingKind.VULNERABLE_DEPENDENCY) -> Finding:
    return Finding(
        kind=kind, severity=severity, subject=subject,
        title=f"{subject} has a problem", detail="detail",
        evidence=(evidence(),),
        remediation=Remediation(summary="fix it"),
    )


def test_a_register_aggregates_findings_onto_subjects():
    risks = register([
        finding("pkg:npm/a", Severity.HIGH),
        finding("pkg:npm/a", Severity.LOW),
        finding("pkg:npm/b", Severity.CRITICAL),
    ])

    assert len(risks) == 2
    assert risks[0].subject == "pkg:npm/b", "critical outranks high"
    by_subject = {risk.subject: risk for risk in risks}
    assert by_subject["pkg:npm/a"].severity is Severity.HIGH
    assert len(by_subject["pkg:npm/a"].findings) == 2


def test_reach_breaks_ties_between_equal_severities():
    """A critical in a leaf is a smaller problem than one in a hub."""
    leaf = Risk("leaf", "leaf", (finding("leaf", Severity.HIGH),), dependents=0)
    hub = Risk("hub", "hub", (finding("hub", Severity.HIGH),), dependents=40)

    assert hub.rank > leaf.rank
    assert hub.exposure > leaf.exposure


def test_severity_dominates_at_equal_reach():
    critical = Risk("c", "c", (finding("c", Severity.CRITICAL),), dependents=4)
    high = Risk("h", "h", (finding("h", Severity.HIGH),), dependents=4)

    assert critical.rank > high.rank


def test_enough_reach_can_outrank_a_higher_severity_and_that_is_deliberate():
    """The one judgement the register makes, asserted rather than left implicit.

    A high-severity issue in a package forty modules touch genuinely is more
    urgent than a critical in a leaf nobody imports, and a register that could
    never express that would just be the findings list re-sorted. The crossover
    is what makes it a *register*; a release gate that must not have it should
    rank on `severity` directly, which is why that field is kept separate.
    """
    isolated = Risk("c", "c", (finding("c", Severity.CRITICAL),), dependents=0)
    hub = Risk("h", "h", (finding("h", Severity.HIGH),), dependents=40)

    assert hub.rank > isolated.rank
    assert isolated.severity.rank > hub.severity.rank, "severity is still visible"


def test_exposure_is_compressed_so_popularity_cannot_swamp_severity():
    """Uncompressed, a 400-dependent hub would sort purely by popularity."""
    small = Risk("a", "a", (finding("a", Severity.LOW),), dependents=4)
    huge = Risk("b", "b", (finding("b", Severity.LOW),), dependents=400)

    assert huge.exposure < 4 * small.exposure


def test_a_risk_reports_what_blocks_a_release():
    risk = Risk("s", "s", (
        finding("s", Severity.CRITICAL), finding("s", Severity.LOW),
    ))

    assert risk.blocking == 1
    assert risk.risk_class.value == "critical"
    assert risk.to_dict()["kinds"] == ["vulnerable_dependency"]
    assert "finding(s)" in str(risk)


def test_a_risk_with_no_findings_is_the_lowest_severity():
    assert Risk("s", "s").severity is Severity.INFO


def test_exposure_is_measured_from_the_graph_when_one_is_given(graph):
    lodash = graph.nodes_by_identity["lodash"]
    risks = register([finding(lodash.id, Severity.HIGH)], graph=graph)

    assert risks[0].dependents >= 1, "app depends on lodash"


def test_a_store_that_cannot_traverse_leaves_exposure_unmeasured():
    """Unmeasured must fall back to severity, not sort to the bottom."""
    class NoTraversal:
        pass

    risks = register([finding("s", Severity.HIGH)], graph=NoTraversal())
    assert risks[0].dependents == 0
    assert risks[0].exposure == 1.0


def test_a_traversal_that_raises_is_treated_as_unmeasured():
    class Broken:
        def blast_radius(self, node_id, **options):
            raise RuntimeError("no")

    assert register([finding("s", Severity.HIGH)], graph=Broken())[0].dependents == 0


def test_a_node_digest_is_shortened_rather_than_printed_in_full():
    risks = register([finding("a" * 40, Severity.LOW)])

    assert risks[0].label.startswith("node:")
    assert len(risks[0].label) < 20


def test_a_purl_subject_is_kept_verbatim():
    assert register([finding("pkg:npm/x@1.0", Severity.LOW)])[0].label == "pkg:npm/x@1.0"


def test_a_supplied_label_wins():
    risks = register([finding("abc", Severity.LOW)], labels={"abc": "Payments API"})
    assert risks[0].label == "Payments API"


def test_the_heat_map_renders_both_dimensions():
    grid = heat_map(register([
        finding("a", Severity.CRITICAL), finding("b", Severity.LOW),
    ]))

    assert "critical" in grid and "isolated" in grid
    assert "·" in grid, "empty cells are marked, not blank"


def test_the_markdown_report_ranks_and_says_what_it_omitted():
    risks = register([finding(f"pkg:npm/p{i}", Severity.HIGH) for i in range(25)])
    body = report(risks, limit=5)

    assert body.startswith("# Risk register")
    assert "Heat map" in body
    assert "20 further subject(s) not shown" in body


def test_a_short_report_omits_nothing_and_says_nothing_about_omission():
    body = report(register([finding("a", Severity.LOW)]), limit=20)
    assert "not shown" not in body


def test_the_register_renders_as_an_emittable_view(graph):
    view = risk_view([finding("pkg:npm/x@1.0", Severity.HIGH)], graph=graph)

    assert view.name == "risk"
    assert view.elements[0]["severity"] == "high"
    assert view.elements[0]["id"].isidentifier()


# --- the offline view -------------------------------------------------------


def test_the_offline_view_is_closed_after_use():
    with view_of([]) as built:
        assert built.nodes(live=True) == ()
    with pytest.raises(Exception):
        built.nodes(live=True)


def test_observations_can_be_recovered_from_a_resolution():
    from slpie.linking.resolver import Resolver

    observations = [
        Observation(kind="declares", subject="pkg:npm/x@1.0.0", evidence=evidence()),
    ]
    resolution = Resolver().resolve(observations)

    assert len(view_from_resolution(resolution)) == 1


# --- the verbs --------------------------------------------------------------


@pytest.fixture()
def repository(tmp_path: Path) -> Path:
    """A tree with something genuinely wrong in it.

    Deliberately not clean: a risk register over a tree with no findings
    exercises none of the ranking, and a test that asserted the header line
    while the body was empty would pass without the verb ever having worked.
    """
    write_npm(
        tmp_path, name="shop", license="MIT",
        declared={"lodash": "^4.0.0", "loose": "*"},
        resolved={
            "lodash": {"version": "4.17.21", "license": "MIT"},
            "loose": {"version": "0.1.0"},
        },
    )
    # `lodash`, spelled correctly, and MIT throughout — unlike `unhealthy_tree`.
    # The risk register here should rank the unconstrained range and the secret,
    # not a typosquat or a licence conflict this tree does not have.
    (tmp_path / "settings.py").write_text(EXAMPLE_AWS_KEY, encoding="utf-8")
    return tmp_path


def run(pipeline: str, root: Path, verbs):
    return Composition.read(pipeline, verbs=verbs).run(Context(root=str(root)))


def test_the_sbom_verb_emits_over_a_scan_with_no_database(repository, verbs):
    result = run(f"discover {repository} | sbom", repository, verbs)

    assert result.ok
    assert result.flow.kind is Kind.REPORT
    assert json.loads(result.flow.facts["sbom"])["bomFormat"] == "CycloneDX"


def test_the_sbom_verb_speaks_spdx_too(repository, verbs):
    result = run(f"discover {repository} | sbom --format spdx", repository, verbs)

    assert json.loads(result.flow.facts["sbom"])["spdxVersion"] == "SPDX-2.3"


def test_an_unknown_sbom_format_is_refused_before_anything_runs(repository, verbs):
    """A declared choice is checked at validation time, not at execution time."""
    from slpie.compose.pipeline import CompositionError

    with pytest.raises(CompositionError, match="cyclonedx"):
        run(f"discover {repository} | sbom --format xml", repository, verbs)


def test_an_undeclared_format_reaching_the_verb_is_still_refused():
    """The verb does not rely on the registry having caught it first."""
    from slpie.compose.flow import Flow
    from slpie.compose.verb import Context as VerbContext

    verb = next(v for v in registry() if v.name == "sbom")
    with pytest.raises(VerbError, match="cyclonedx or spdx"):
        verb.run(Flow(kind=Kind.OBSERVATIONS, value=()), {"format": "xml"},
                 VerbContext())


def test_the_sbom_verb_writes_where_it_is_told(repository, verbs, tmp_path):
    target = tmp_path / "out" / "sbom.json"
    result = run(
        f"discover {repository} | sbom --out {target}", repository, verbs,
    )

    assert target.is_file()
    assert result.flow.facts["sbom_written"] == str(target)


def test_an_sbom_over_a_tree_with_no_components_is_refused(tmp_path, verbs):
    (tmp_path / "notes.md").write_text("nothing here", encoding="utf-8")
    result = run(f"discover {tmp_path} | sbom", tmp_path, verbs)

    assert not result.ok
    assert "no component nodes" in str(result.error)


def test_the_c4_verb_renders_mermaid(repository, verbs):
    result = run(f"discover {repository} | c4", repository, verbs)

    assert result.ok
    assert "flowchart" in result.flow.facts["c4"]
    assert result.flow.facts["c4_levels"] == ["context", "container"]


def test_the_c4_verb_can_select_one_level(repository, verbs):
    result = run(f"discover {repository} | c4 --level container", repository, verbs)

    assert result.flow.facts["c4_levels"] == ["container"]


def test_an_unknown_c4_level_is_refused(repository, verbs):
    from slpie.compose.pipeline import CompositionError

    with pytest.raises(CompositionError, match="context"):
        run(f"discover {repository} | c4 --level nonsense", repository, verbs)


def test_a_level_this_graph_cannot_build_is_named_by_the_verb(repository, verbs):
    """`code` is a real level and needs a subject; asking without one says so."""
    result = run(f"discover {repository} | c4 --level code", repository, verbs)

    assert not result.ok
    assert "is not a level this graph supports" in str(result.error)


def test_the_c4_verb_writes_a_file_per_level(repository, verbs, tmp_path):
    target = tmp_path / "c4"
    result = run(f"discover {repository} | c4 --out {target}", repository, verbs)

    assert (target / "context.mmd").is_file()
    assert len(result.flow.facts["c4_written"]) == 2


def test_the_enterprise_verb_reports_every_view(repository, verbs):
    result = run(f"discover {repository} | enterprise", repository, verbs)

    assert result.ok
    assert set(result.flow.facts["views"]) == {
        "application", "data", "technology", "standards", "topology",
    }


def test_the_enterprise_verb_says_when_a_view_selected_nothing(repository, verbs):
    result = run(f"discover {repository} | enterprise", repository, verbs)

    assert "was this scanned?" in result.flow.facts["enterprise"]


def test_the_enterprise_verb_can_select_one_view(repository, verbs):
    result = run(
        f"discover {repository} | enterprise --view topology", repository, verbs,
    )

    assert result.flow.facts["views"] == ["topology"]


def test_an_unknown_enterprise_view_is_refused(repository, verbs):
    from slpie.compose.pipeline import CompositionError

    with pytest.raises(CompositionError, match="application"):
        run(f"discover {repository} | enterprise --view nope", repository, verbs)


def test_an_undeclared_view_reaching_the_verb_is_still_refused():
    from slpie.compose.flow import Flow
    from slpie.compose.verb import Context as VerbContext

    verb = next(v for v in registry() if v.name == "enterprise")
    with pytest.raises(VerbError, match="is not a view"):
        verb.run(Flow(kind=Kind.OBSERVATIONS, value=()), {"view": "nope"},
                 VerbContext())


def test_the_enterprise_verb_generates_code_through_the_bridge(
    verbs, tmp_path,
):
    """The end-to-end claim: a scan becomes importable architecture."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "compose.yaml").write_text(
        "services:\n"
        "  payments:\n"
        "    image: acme/payments:1.2.3\n"
        "  orders:\n"
        "    image: acme/orders:2.0.0\n",
        encoding="utf-8",
    )
    out = tmp_path / "architecture"
    result = run(
        f"discover {root} | enterprise --write --out {out}", root, verbs,
    )

    assert result.ok, result.error
    generated = result.flow.facts["generated"]
    assert generated, "at least one view had elements and was generated"
    assert any(Path(item["module"]).is_file() for item in generated)


def test_generating_an_empty_view_is_skipped_rather_than_raising(
    repository, verbs, tmp_path,
):
    """`shape_for` refuses an empty view, and rightly; the verb must not crash."""
    result = run(
        f"discover {repository} | enterprise --write --out {tmp_path / 'arch'}",
        repository, verbs,
    )

    assert result.ok
    assert result.flow.facts["generated"] == []


def test_the_risk_verb_aggregates_what_govern_found(repository, verbs):
    result = run(f"discover {repository} | govern | risk", repository, verbs)

    assert result.ok
    assert result.flow.kind is Kind.REPORT
    assert result.flow.facts["risk_subjects"] > 0, (
        "a register over a tree with nothing wrong exercises none of the ranking"
    )
    assert result.flow.size == result.flow.facts["risk_subjects"]
    assert "critical" in result.flow.facts["risk"]


def test_the_risk_verb_renders_markdown_on_request(repository, verbs):
    result = run(
        f"discover {repository} | govern | risk --markdown", repository, verbs,
    )

    assert result.flow.facts["risk"].startswith("# Risk register")


def test_the_risk_verb_writes_a_report(repository, verbs, tmp_path):
    target = tmp_path / "reports" / "risk.md"
    run(f"discover {repository} | govern | risk --out {target}", repository, verbs)

    assert target.is_file()
    assert target.read_text(encoding="utf-8").startswith("# Risk register")


def test_the_risk_verb_limits_what_it_shows(repository, verbs):
    result = run(
        f"discover {repository} | govern | risk --limit 1", repository, verbs,
    )

    assert result.ok
    assert result.flow.facts["risk"].count("finding(s),") == 1
    assert result.flow.facts["risk_subjects"] > 1, (
        "the limit narrows what is shown, not what was aggregated"
    )


def test_an_overwritten_hand_edit_stops_the_verb_and_names_the_symbol(
    verbs, tmp_path,
):
    """The verb must surface the conflict, not let a half-merged file land."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "compose.yaml").write_text(
        "services:\n  payments:\n    image: acme/payments:1.0.0\n", encoding="utf-8",
    )
    out = tmp_path / "arch"
    first = run(f"discover {root} | enterprise --write --out {out}", root, verbs)
    assert first.ok and first.flow.facts["generated"]

    # Edit a generated symbol by hand, without pinning it.
    module = Path(first.flow.facts["generated"][0]["module"])
    module.write_text(
        module.read_text(encoding="utf-8").replace(
            "SHAPE_REVISION = 1", "SHAPE_REVISION = 99",
        ),
        encoding="utf-8",
    )
    (root / "compose.yaml").write_text(
        "services:\n  payments:\n    image: acme/payments:1.0.0\n"
        "  orders:\n    image: acme/orders:2.0.0\n", encoding="utf-8",
    )
    second = run(f"discover {root} | enterprise --write --out {out}", root, verbs)

    assert not second.ok
    assert "would overwrite the local definition" in str(second.error)
    assert "Nothing was written for this view" in str(second.error)


def test_a_conflict_during_generation_stops_that_view_and_says_so(
    verbs, tmp_path,
):
    """A half-merged architecture module is worse than a refused regeneration."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "compose.yaml").write_text(
        "services:\n  payments:\n    image: acme/payments:1.0.0\n", encoding="utf-8",
    )
    out = tmp_path / "arch"
    first = run(f"discover {root} | enterprise --write --out {out}", root, verbs)
    assert first.ok and first.flow.facts["generated"]

    # Break a generated module the way a hand edit would.
    module = Path(first.flow.facts["generated"][0]["module"])
    module.write_text("def broken(:\n", encoding="utf-8")

    (root / "compose.yaml").write_text(
        "services:\n  payments:\n    image: acme/payments:1.0.0\n"
        "  orders:\n    image: acme/orders:2.0.0\n", encoding="utf-8",
    )
    second = run(f"discover {root} | enterprise --write --out {out}", root, verbs)

    assert not second.ok
    assert "cannot parse" in str(second.error)


def test_artifact_verbs_appear_in_every_projection(verbs):
    """A verb registered without being wired is a test failure, not a drift."""
    for name in ("sbom", "c4", "enterprise", "risk"):
        verb = verbs.require(name)
        assert verb.summary and verb.examples
        for example in verb.examples:
            assert Composition.read(example, verbs=verbs).validate().ok
