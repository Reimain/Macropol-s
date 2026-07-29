"""Merging identities, and joining what spans two files.

The load-bearing case is the first one: two discoverers reading two different
files in two different dialects must produce **one** node. The rest of the graph
is built on that being true, and a UI showing two similar-looking nodes is the
symptom nobody diagnoses correctly.

The mirror case matters just as much. `requests` and `requests-oauthlib` look
alike and are not the same package; a resolver that merged them would produce a
graph nothing downstream could un-merge.
"""

from __future__ import annotations

import pytest

from slpie.domain.evidence import Evidence, EvidenceKind, SourceLocation
from slpie.linking import (
    CorroborationLinker,
    ImportToPackageLinker,
    LinkerSet,
    Resolver,
    merges_with,
)
from slpie.linking.resolver import ResolutionError
from slpie.plugins.protocol import Observation


def evidence(uri: str, *, line: int = 1, kind: EvidenceKind = EvidenceKind.MANIFEST_DECLARED,
             excerpt: str = "") -> Evidence:
    return Evidence(
        kind=kind, location=SourceLocation(uri, line=line),
        extractor="test", excerpt=excerpt or f"line {line} of {uri}",
    )


def declares(subject: str, uri: str, **properties) -> Observation:
    kind = properties.pop("evidence_kind", EvidenceKind.MANIFEST_DECLARED)
    line = properties.pop("line", 1)
    return Observation(
        kind="declares", subject=subject,
        evidence=evidence(uri, line=line, kind=kind), properties=properties,
    )


# --- merging on identity -------------------------------------------------


def test_two_dialects_of_the_same_package_resolve_to_one_node():
    resolution = Resolver().resolve([
        declares("pkg:pypi/Typing_Extensions@4.9.0", "file:///repo/setup.py"),
        declares("pkg:pypi/typing-extensions@4.9.0", "file:///repo/requirements.txt"),
    ])

    assert len(resolution.resolved) == 1
    entry = resolution.resolved[0]
    assert entry.identity == "pkg:pypi/typing-extensions@4.9.0"
    assert len(entry.observations) == 2
    assert resolution.merged == 1


def test_a_scoped_npm_package_survives_percent_encoding_on_both_sides():
    resolution = Resolver().resolve([
        declares("pkg:npm/%40types/node@20.1.0", "file:///repo/package.json"),
        declares("pkg:npm/@types/node@20.1.0", "file:///repo/package-lock.json"),
    ])

    assert len(resolution.resolved) == 1
    assert resolution.resolved[0].node_id


def test_similar_names_are_never_merged():
    resolution = Resolver().resolve([
        declares("pkg:pypi/requests@2.31.0", "file:///repo/requirements.txt"),
        declares("pkg:pypi/requests-oauthlib@1.3.1", "file:///repo/requirements.txt"),
    ])

    assert len(resolution.resolved) == 2
    assert not merges_with("pkg:pypi/requests", "pkg:pypi/requests-oauthlib")


def test_merging_is_by_identity_not_by_edit_distance():
    assert merges_with("pkg:pypi/Typing_Extensions", "pkg:pypi/typing-extensions")
    assert not merges_with("pkg:npm/node-fetch", "pkg:npm/node_fetch")
    assert not merges_with("urn:slpie:service:a", "urn:slpie:service:b")


def test_corroboration_counts_files_not_observations():
    """One discoverer reporting a package twice from one file is one source."""
    one_file = Resolver().resolve([
        declares("pkg:pypi/flask@3.0.0", "file:///repo/requirements.txt", line=1),
        declares("pkg:pypi/flask@3.0.0", "file:///repo/requirements.txt", line=9),
    ])
    assert not one_file.resolved[0].corroborated

    two_files = Resolver().resolve([
        declares("pkg:pypi/flask@3.0.0", "file:///repo/requirements.txt"),
        declares("pkg:pypi/flask@3.0.0", "file:///repo/pyproject.toml"),
    ])
    assert two_files.resolved[0].corroborated


def test_two_lockfiles_pinning_different_versions_is_a_contradiction():
    resolution = Resolver().resolve([
        declares(
            "pkg:npm/lodash@4.17.21", "file:///repo/a/package-lock.json",
            evidence_kind=EvidenceKind.LOCKFILE_PIN,
        ),
        declares(
            "pkg:npm/lodash@4.17.15", "file:///repo/b/package-lock.json",
            evidence_kind=EvidenceKind.LOCKFILE_PIN,
        ),
    ])

    contradicted = resolution.contradictions()
    assert len(contradicted) == 1
    assert set(contradicted[0].versions) == {"4.17.21", "4.17.15"}


def test_a_range_and_a_pin_share_a_node_without_collapsing_into_each_other():
    resolution = Resolver().resolve([
        declares("pkg:npm/lodash", "file:///repo/package.json", range="^4.17.0"),
        declares(
            "pkg:npm/lodash@4.17.21", "file:///repo/package-lock.json",
            evidence_kind=EvidenceKind.LOCKFILE_PIN,
        ),
    ])

    assert len(resolution.resolved) == 1
    entry = resolution.resolved[0]
    assert entry.pinned == "4.17.21"
    assert "^4.17.0" in entry.ranges
    assert not entry.contradicted


def test_an_identity_that_is_neither_purl_nor_urn_is_reported_unresolved():
    resolution = Resolver().resolve([declares("lodash", "file:///repo/package.json")])

    assert not resolution.resolved
    assert len(resolution.unresolved) == 1
    assert "neither a purl nor an SLPIE urn" in resolution.unresolved[0][1]


def test_an_observation_with_no_evidence_never_reaches_the_graph():
    resolution = Resolver().resolve([
        Observation(kind="declares", subject="pkg:pypi/flask@3.0.0", evidence=None),
    ])

    assert not resolution.resolved
    assert resolution.unresolved[0][1] == "no evidence was cited"


def test_canonicalise_refuses_a_malformed_purl_by_name():
    with pytest.raises(ResolutionError) as caught:
        Resolver().canonicalise("pkg:")
    assert "pkg:" in str(caught.value)


def test_a_relationship_becomes_an_edge_between_two_resolved_nodes():
    resolution = Resolver().resolve([
        Observation(
            kind="depends_on", subject="urn:slpie:service:payments/api",
            object="pkg:pypi/flask@3.0.0",
            evidence=evidence("file:///repo/pyproject.toml", line=12),
        ),
    ])

    assert len(resolution.links) == 1
    link = resolution.links[0]
    assert link.kind == "depends_on"
    assert {entry.node_id for entry in resolution.resolved} == {link.source, link.target}


# --- linkers -------------------------------------------------------------


def test_a_pin_outside_its_declared_range_is_reported_as_a_contradiction():
    resolution = Resolver().resolve([
        declares("pkg:npm/lodash", "file:///repo/package.json", range="^3.0.0"),
        declares(
            "pkg:npm/lodash@4.17.21", "file:///repo/package-lock.json",
            evidence_kind=EvidenceKind.LOCKFILE_PIN,
        ),
    ])

    joined = CorroborationLinker().link(resolution)
    assert len(joined) == 1
    assert joined[0].contradicts
    assert "4.17.21" in joined[0].contradiction
    assert "^3.0.0" in joined[0].contradiction


def test_a_pin_inside_its_declared_range_corroborates_it():
    resolution = Resolver().resolve([
        declares("pkg:npm/lodash", "file:///repo/package.json", range="^4.17.0"),
        declares(
            "pkg:npm/lodash@4.17.21", "file:///repo/package-lock.json",
            evidence_kind=EvidenceKind.LOCKFILE_PIN,
        ),
    ])

    joined = CorroborationLinker().link(resolution)
    assert joined and not joined[0].contradicts
    assert joined[0].kind == "corroborates"


def test_the_import_table_knows_that_yaml_is_pyyaml():
    linker = ImportToPackageLinker()
    assert linker.distribution_for("yaml") == "pyyaml"
    assert linker.distribution_for("cv2") == "opencv-python"
    assert linker.distribution_for("sklearn") == "scikit-learn"


def test_a_stdlib_import_is_never_attributed_to_a_package():
    """Several PyPI packages share a stdlib module's name and are not it."""
    linker = ImportToPackageLinker()
    assert linker.distribution_for("json") == ""
    assert linker.distribution_for("sqlite3") == ""


def test_an_import_whose_name_is_the_distribution_name_passes_through():
    assert ImportToPackageLinker().distribution_for("flask") == "flask"
    assert ImportToPackageLinker().distribution_for("flask.helpers") == "flask"


def test_an_import_links_to_the_distribution_that_provides_it():
    resolution = Resolver().resolve([
        declares("pkg:pypi/pyyaml@6.0.1", "file:///repo/requirements.txt"),
        Observation(
            kind="imports", subject="urn:slpie:module:app/main",
            object="pkg:pypi/pyyaml",
            evidence=evidence(
                "file:///repo/app/main.py", line=3,
                kind=EvidenceKind.STATIC_IMPORT, excerpt="import yaml",
            ),
            properties={"module": "yaml"},
        ),
    ])

    joined = ImportToPackageLinker().link(resolution)
    assert len(joined) == 1
    assert joined[0].kind == "provided_by"
    assert "not the import name" in joined[0].enrichment.rationale


def test_a_linker_that_raises_abstains_without_costing_the_others():
    class Broken:
        name = "broken"

        def link(self, resolution):
            raise RuntimeError("the world is not as I expected")

    resolution = Resolver().resolve([
        declares("pkg:npm/lodash", "file:///repo/package.json", range="^4.17.0"),
        declares(
            "pkg:npm/lodash@4.17.21", "file:///repo/package-lock.json",
            evidence_kind=EvidenceKind.LOCKFILE_PIN,
        ),
    ])

    linkers = LinkerSet()
    linkers.add(Broken())
    joined = linkers.run(resolution)

    assert joined, "the working linkers still produced their joins"
    assert len(linkers.errors) == 1
    assert "broken abstained" in linkers.errors[0]


def test_every_link_a_linker_makes_cites_something():
    resolution = Resolver().resolve([
        declares("pkg:npm/lodash", "file:///repo/package.json", range="^4.17.0"),
        declares(
            "pkg:npm/lodash@4.17.21", "file:///repo/package-lock.json",
            evidence_kind=EvidenceKind.LOCKFILE_PIN,
        ),
    ])

    for entry in LinkerSet().run(resolution):
        assert entry.enrichment.grounded, f"{entry.linker} asserted without citing"
