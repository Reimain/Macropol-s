"""Discovery: npm, Python, JS/TS source, git — against real materialised files."""

from __future__ import annotations

import json
import subprocess
import textwrap

import pytest

from slpie.discovery import Scanner, Source, materialise, register_builtins
from slpie.discovery.code.javascript import discover_javascript_source
from slpie.discovery.code.python_ast import discover_python_source
from slpie.discovery.ecosystems.npm import (
    discover_package_json,
    discover_package_lock,
    discover_yarn_lock,
    npm_purl,
)
from slpie.discovery.ecosystems.python import (
    discover_poetry_lock,
    discover_pyproject,
    discover_requirements,
)
from slpie.discovery.scm import git
from slpie.domain import EvidenceKind, NodeKind
from slpie.engine import Engine
from slpie.plugins import Registry

MANIFEST = """apiVersion: slpie/v1
environment: acme
target: simulated
codebase:
  - root: ./services/payments
    language: npm
  - root: ./services/api
    language: python
"""


def source(text: str, *, uri: str, element: str = "app") -> Source:
    return Source(uri=uri, text=textwrap.dedent(text).lstrip("\n"), element=element)


def targets(result):
    return {o.object for o in result.observations if o.object}


def evidence_kinds(result):
    return {o.evidence.kind for o in result.observations if o.evidence}


# --- npm -----------------------------------------------------------------


def test_a_scoped_package_becomes_a_namespaced_purl():
    """`@types/node` is namespace `@types`, name `node` — not one name with a
    slash in it, which would collide with every other scope."""
    assert npm_purl("@types/node").to_string() == "pkg:npm/%40types/node"
    assert npm_purl("lodash", "4.17.21").to_string() == "pkg:npm/lodash@4.17.21"


def test_a_package_json_reports_ranges_not_versions():
    result = discover_package_json(source("""
        {
          "name": "payments",
          "version": "1.0.0",
          "license": "Apache-2.0",
          "dependencies": {"lodash": "^4.17.20"},
          "devDependencies": {"jest": "^29.0.0"}
        }
    """, uri="./p/package.json"))

    ranges = {o.object: o.properties.get("range") for o in result.observations if o.object}
    assert ranges["pkg:npm/lodash"] == "^4.17.20"
    # A manifest states intent, so it never claims certainty.
    assert evidence_kinds(result) == {EvidenceKind.MANIFEST_DECLARED}


def test_dependency_sections_are_distinguished_by_qualifier():
    result = discover_package_json(source("""
        {"name": "p", "dependencies": {"a": "1"}, "devDependencies": {"b": "1"},
         "peerDependencies": {"c": "1"}, "optionalDependencies": {"d": "1"}}
    """, uri="./p/package.json"))

    qualifiers = {o.object.split("/")[-1]: o.qualifier for o in result.observations if o.object}
    assert qualifiers == {"a": "", "b": "dev", "c": "peer", "d": "optional"}


def test_a_lockfile_v3_yields_the_only_evidence_that_reaches_certainty():
    result = discover_package_lock(source("""
        {
          "name": "payments", "version": "1.0.0", "lockfileVersion": 3,
          "packages": {
            "": {"name": "payments", "version": "1.0.0"},
            "node_modules/lodash": {"version": "4.17.20", "license": "MIT",
                                    "integrity": "sha512-abc"}
          }
        }
    """, uri="./p/package-lock.json"))

    assert "pkg:npm/lodash@4.17.20" in targets(result)
    assert evidence_kinds(result) == {EvidenceKind.LOCKFILE_PIN}


def test_a_nested_v1_lockfile_is_walked_to_the_bottom():
    result = discover_package_lock(source("""
        {
          "name": "p", "lockfileVersion": 1,
          "dependencies": {
            "a": {"version": "1.0.0",
                  "dependencies": {"b": {"version": "2.0.0"}}}
          }
        }
    """, uri="./p/package-lock.json"))

    assert {"pkg:npm/a@1.0.0", "pkg:npm/b@2.0.0"} <= targets(result)


def test_a_lockfile_that_is_not_json_reports_rather_than_raises():
    result = discover_package_lock(source("{ truncated", uri="./p/package-lock.json"))

    assert result.observations == ()
    assert "not valid JSON" in result.errors[0]


def test_yarn_v1_and_berry_lockfiles_both_yield_pins():
    v1 = discover_yarn_lock(source('''
        # yarn lockfile v1

        lodash@^4.17.20:
          version "4.17.21"
          resolved "https://registry.yarnpkg.com/lodash/-/lodash-4.17.21.tgz"
    ''', uri="./p/yarn.lock"))
    assert "pkg:npm/lodash@4.17.21" in targets(v1)

    berry = discover_yarn_lock(source('''
        "lodash@npm:^4.17.20":
          version: 4.17.21
          resolution: "lodash@npm:4.17.21"
    ''', uri="./p/yarn.lock"))
    assert "pkg:npm/lodash@4.17.21" in targets(berry)


# --- python packaging ----------------------------------------------------


def test_pep621_dependencies_are_read_with_stdlib_tomllib():
    result = discover_pyproject(source("""
        [project]
        name = "api"
        version = "1.0.0"
        requires-python = ">=3.11"
        dependencies = ["requests>=2.31", "httpx[http2]~=0.27"]

        [project.optional-dependencies]
        dev = ["pytest>=8.0"]
    """, uri="./api/pyproject.toml"))

    found = {o.object: o.properties.get("range") for o in result.observations if o.object}
    assert found["pkg:pypi/requests"] == ">=2.31"
    assert found["pkg:pypi/httpx"] == "~=0.27"
    assert "pkg:pypi/pytest" in found
    assert found["pkg:generic/python"] == ">=3.11"


def test_poetry_dependency_tables_are_read_too():
    result = discover_pyproject(source("""
        [tool.poetry]
        name = "api"
        version = "1.0.0"

        [tool.poetry.dependencies]
        python = "^3.11"
        requests = "^2.31"
        boto3 = {version = "^1.34", optional = true}

        [tool.poetry.group.dev.dependencies]
        pytest = "^8.0"
    """, uri="./api/pyproject.toml"))

    by_target = {o.object: o for o in result.observations if o.object}
    assert by_target["pkg:pypi/requests"].properties["range"] == "^2.31"
    assert by_target["pkg:pypi/boto3"].qualifier == "optional"
    assert by_target["pkg:pypi/pytest"].qualifier == "dev"


def test_pypi_names_normalise_so_one_distribution_is_one_node():
    result = discover_requirements(source("""
        Typing_Extensions==4.9.0
        typing-extensions>=4.0
    """, uri="./api/requirements.txt"))

    assert {o.object for o in result.observations if o.object} == {
        "pkg:pypi/typing-extensions", "pkg:pypi/typing-extensions@4.9.0",
    }


def test_a_pinned_requirement_is_still_a_declaration_not_a_lockfile_pin():
    """Nothing has resolved a requirements file against an index, so `==` is
    intent — certainty is reserved for an actual lockfile."""
    result = discover_requirements(source("requests==2.31.0\n", uri="./api/requirements.txt"))

    assert evidence_kinds(result) == {EvidenceKind.MANIFEST_DECLARED}


def test_requirement_directives_and_comments_are_skipped():
    result = discover_requirements(source("""
        # a comment
        -r other.txt
        --index-url https://example.invalid/simple
        -e .
        git+https://github.com/x/y.git
        requests>=2.31 ; python_version < "3.13"
    """, uri="./api/requirements.txt"))

    assert {o.object for o in result.observations if o.object} == {"pkg:pypi/requests"}
    marker = result.observations[0].properties["marker"]
    assert 'python_version < "3.13"' in marker


def test_a_poetry_lock_is_a_real_pin():
    result = discover_poetry_lock(source("""
        [[package]]
        name = "requests"
        version = "2.31.0"

        [[package]]
        name = "urllib3"
        version = "2.1.0"
    """, uri="./api/poetry.lock"))

    assert {"pkg:pypi/requests@2.31.0", "pkg:pypi/urllib3@2.1.0"} <= targets(result)
    assert evidence_kinds(result) == {EvidenceKind.LOCKFILE_PIN}


# --- python source -------------------------------------------------------


def test_python_imports_are_parsed_not_matched():
    result = discover_python_source(source('''
        """A docstring that says: import notreal"""
        import requests
        from httpx import AsyncClient
        from . import sibling
        import os, json
        SAMPLE = "import alsonotreal"
    ''', uri="./api/main.py", element="api"))

    # A regex would find `notreal` and `alsonotreal`; the parser does not.
    assert targets(result) == {"pkg:pypi/requests", "pkg:pypi/httpx"}


def test_the_standard_library_is_not_a_dependency():
    """Reporting `import os` as a dependency would bury every real third-party
    edge under a hundred of them."""
    result = discover_python_source(source(
        "import os, sys, json, typing\nimport requests\n",
        uri="./api/main.py", element="api",
    ))
    assert targets(result) == {"pkg:pypi/requests"}


def test_a_dotted_import_resolves_to_its_distribution():
    result = discover_python_source(source(
        "import concurrent.futures\nfrom google.cloud import storage\n",
        uri="./api/main.py", element="api",
    ))
    assert targets(result) == {"pkg:pypi/google"}


def test_a_dynamic_import_of_a_literal_is_weaker_than_a_static_one():
    result = discover_python_source(source("""
        import importlib
        import requests
        plugin = importlib.import_module("httpx")
    """, uri="./api/main.py", element="api"))

    by_target = {o.object: o.evidence.kind for o in result.observations if o.object}
    assert by_target["pkg:pypi/requests"] is EvidenceKind.STATIC_IMPORT
    assert by_target["pkg:pypi/httpx"] is EvidenceKind.DYNAMIC_LOAD


def test_a_computed_import_names_no_target_but_is_still_recorded():
    """Naming a package we cannot identify would be a guess with a file:line
    attached. Recording that the graph is incomplete here is not."""
    result = discover_python_source(source("""
        import importlib
        def load(name):
            return importlib.import_module(name)
    """, uri="./api/loader.py", element="api"))

    assert targets(result) == set()
    assert any(o.properties.get("dynamic_import_site") for o in result.observations)


def test_a_file_that_does_not_parse_is_reported_not_fatal():
    """Real repositories contain templates and Python 2 leftovers."""
    result = discover_python_source(source(
        "def broken(:\n    pass\n", uri="./api/broken.py", element="api",
    ))

    assert result.observations == ()
    assert "could not parse" in result.errors[0]


def test_import_line_numbers_point_at_the_import():
    result = discover_python_source(source("""
        # a comment
        import os

        import requests
    """, uri="./api/main.py", element="api"))

    requests = next(o for o in result.observations if o.object == "pkg:pypi/requests")
    assert requests.evidence.location.line == 4
    assert "import requests" in requests.evidence.excerpt


# --- javascript source ---------------------------------------------------


JS_SAMPLE = """
const lodash = require('lodash');
import express from "express";
import { a } from './local';
import fs from "node:fs";
// import fake from "commented"
/* import block
   from "blocked" */
const s = "import notreal from 'quoted'";
const mod = await import('semver');
const dyn = await import(name);
import type { T } from "@types/node";
export { x } from "redux";
"""


def test_javascript_imports_survive_masking_but_quoted_code_does_not():
    result = discover_javascript_source(source(JS_SAMPLE, uri="./p/src/index.js", element="p"))

    found = targets(result)
    assert "pkg:npm/lodash" in found and "pkg:npm/express" in found
    assert "pkg:npm/redux" in found and "pkg:npm/%40types/node" in found
    # A code sample in a string is not a dependency.
    assert not any("notreal" in t or "blocked" in t or "commented" in t for t in found)
    # Nor is a relative path or a node builtin.
    assert not any("local" in t or t == "pkg:npm/fs" for t in found)


def test_every_javascript_line_number_points_at_its_own_source_line():
    text = textwrap.dedent(JS_SAMPLE).lstrip("\n")
    result = discover_javascript_source(source(JS_SAMPLE, uri="./p/src/index.js", element="p"))
    lines = text.splitlines()

    for observation in result.observations:
        if not observation.object:
            continue
        line = observation.evidence.location.line
        name = observation.object.rsplit("/", 1)[-1]
        assert name in lines[line - 1], f"{name} reported on line {line}: {lines[line - 1]!r}"


def test_a_lazy_import_is_weaker_than_a_static_one():
    result = discover_javascript_source(source(JS_SAMPLE, uri="./p/src/index.js", element="p"))
    by_target = {o.object: o.evidence.kind for o in result.observations if o.object}

    assert by_target["pkg:npm/lodash"] is EvidenceKind.STATIC_IMPORT
    assert by_target["pkg:npm/semver"] is EvidenceKind.DYNAMIC_LOAD


def test_a_deep_import_still_names_its_package():
    result = discover_javascript_source(source(
        "import merge from 'lodash/fp/merge';\n", uri="./p/a.js", element="p",
    ))
    assert targets(result) == {"pkg:npm/lodash"}


def test_typescript_is_recognised_as_its_own_language():
    result = discover_javascript_source(source(
        "import x from 'zod';\n", uri="./p/a.ts", element="p",
    ))
    assert result.observations[0].properties["language"] == "typescript"


def test_a_module_urn_does_not_repeat_its_element_name():
    result = discover_javascript_source(source(
        "import x from 'zod';\n", uri="./services/payments/src/index.js", element="payments",
    ))
    assert result.observations[0].subject == "urn:slpie:module:payments/src/index"


# --- git -----------------------------------------------------------------


needs_git = pytest.mark.skipif(not git.available(), reason="git is not installed")


@needs_git
def test_a_repository_reports_its_own_identity_and_history(tmp_path):
    engine = Engine.from_text(MANIFEST)
    engine.declare()
    world = engine.simulate(root=tmp_path / "world")

    result = git.discover_repository(world.path_for("payments"), element="payments")

    assert result.observations
    properties = result.observations[0].properties
    assert properties["scm"] == "git"
    assert len(properties["head"]) == 40
    assert properties["commit_count"] >= 1
    assert properties["contributors"] == ["simulator@slpie.local"]
    engine.close()


@needs_git
def test_a_directory_that_is_not_a_repository_says_so(tmp_path):
    result = git.discover_repository(tmp_path, element="nothing")

    assert result.observations == ()
    assert "not a git repository" in result.errors[0]


@needs_git
def test_when_a_path_last_changed_is_answerable(tmp_path):
    engine = Engine.from_text(MANIFEST)
    engine.declare()
    world = engine.simulate(root=tmp_path / "world")

    changed = git.last_changed(world.path_for("payments"), ["package.json"])
    assert changed["package.json"] > 0
    engine.close()


# --- materialising observations -----------------------------------------


def test_one_package_seen_three_ways_is_one_node_with_three_evidences():
    """Emitting three nodes would fragment the graph and throw away exactly the
    corroboration that makes the claim strong."""
    manifest = discover_package_json(source(
        '{"name": "p", "dependencies": {"lodash": "^4.17.20"}}', uri="./p/package.json",
    ))
    code = discover_javascript_source(source(
        "const _ = require('lodash');\n", uri="./p/index.js", element="p",
    ))

    nodes, edges, errors = materialise([*manifest.observations, *code.observations])

    lodash = [n for n in nodes if n.name == "lodash"]
    assert len(lodash) == 1
    assert len(lodash[0].evidence) == 2
    assert lodash[0].confidence > 0.95
    assert errors == []


def test_an_unparseable_identity_is_reported_rather_than_dropped_silently():
    from slpie.discovery.base import evidence_at
    from slpie.plugins import Observation

    bad = Observation(
        kind="depends_on", subject="not a purl at all", object="pkg:npm/x",
        evidence=evidence_at("file:///x", kind=EvidenceKind.STATIC_IMPORT, extractor="t"),
    )
    nodes, edges, errors = materialise([bad])

    assert errors
    assert edges == ()


def test_an_unknown_observation_kind_is_reported():
    from slpie.discovery.base import evidence_at
    from slpie.plugins import Observation

    unknown = Observation(
        kind="teleports_to", subject="pkg:npm/a", object="pkg:npm/b",
        evidence=evidence_at("file:///x", kind=EvidenceKind.STATIC_IMPORT, extractor="t"),
    )
    _, edges, errors = materialise([unknown])

    assert edges == ()
    assert "unknown observation kind" in errors[0]


# --- the scan end to end -------------------------------------------------


def test_every_builtin_registers_through_the_public_plugin_path():
    registry = register_builtins(Registry())

    assert len(registry) == 9
    assert all(not r.manifest.external for r in registry)
    # And each declares the evidence kinds it is allowed to claim.
    assert all(r.manifest.evidence_kinds for r in registry)


def test_lockfiles_are_read_before_source_analysis():
    """The packaging layer names things; source analysis corroborates."""
    registry = register_builtins(Registry())
    claims = [r.id for r in registry.for_uri("package-lock.json")]

    assert claims == ["slpie.npm.lockfile"]
    assert registry.get("slpie.npm.lockfile").manifest.priority < (
        registry.get("slpie.javascript").manifest.priority
    )


def test_a_scan_builds_the_graph_from_materialised_files(tmp_path):
    engine = Engine.from_text(MANIFEST)
    engine.declare()
    engine.simulate(root=tmp_path / "world")
    engine.attach(wanted=("file-read", "directory-list", "lockfile-read", "static-analysis"))

    report = engine.scan()

    assert report["files_read"] >= 4
    assert report["nodes"] > 10
    assert report["edges"] > 10
    assert report["clean"], report["errors"]
    engine.close()


def test_a_pinned_package_reaches_certainty_and_a_declared_one_does_not(tmp_path):
    engine = Engine.from_text(MANIFEST)
    engine.declare()
    engine.simulate(root=tmp_path / "world")
    engine.attach(wanted=("file-read", "directory-list", "lockfile-read"))
    engine.scan()

    pinned = [n for n in engine.graph.nodes() if n.name == "lodash" and n.version]
    declared = [n for n in engine.graph.nodes() if n.name == "lodash" and not n.version]

    assert pinned and pinned[0].confidence == 1.0
    assert declared and declared[0].confidence < 1.0
    engine.close()


def test_every_discovered_node_can_show_the_line_it_came_from(tmp_path):
    engine = Engine.from_text(MANIFEST)
    engine.declare()
    engine.simulate(root=tmp_path / "world")
    engine.attach(wanted=("file-read", "directory-list", "lockfile-read", "static-analysis"))
    engine.scan()

    for node in engine.graph.nodes():
        assert node.evidence
        assert node.evidence[0].location.uri
    engine.close()


def test_a_second_scan_of_an_unchanged_world_adds_nothing(tmp_path):
    engine = Engine.from_text(MANIFEST)
    engine.declare()
    engine.simulate(root=tmp_path / "world")
    engine.attach(wanted=("file-read", "directory-list", "lockfile-read", "static-analysis"))

    engine.scan()
    baseline = engine.seal(label="first")
    engine.scan()
    again = engine.seal(label="second")

    assert again.root_digest == baseline.root_digest
    engine.close()


def test_a_changed_lockfile_changes_the_graph(tmp_path):
    engine = Engine.from_text(MANIFEST)
    engine.declare()
    world = engine.simulate(root=tmp_path / "world")
    engine.attach(wanted=("file-read", "directory-list", "lockfile-read"))
    engine.scan()
    before = engine.seal(label="before")

    engine.fire("cve", package="lodash", version="4.17.19")
    engine.scan()
    after = engine.seal(label="after")

    assert after.root_digest != before.root_digest
    assert any(
        n.version == "4.17.19" for n in engine.graph.nodes() if n.name == "lodash"
    )
    engine.close()


def test_vendored_directories_are_never_read(tmp_path):
    engine = Engine.from_text(MANIFEST)
    engine.declare()
    world = engine.simulate(root=tmp_path / "world")
    world.rewrite("payments", "node_modules/junk/index.js", "require('should-not-appear');\n")
    engine.attach(wanted=("file-read", "directory-list"))

    engine.scan()

    assert not any(
        "should-not-appear" in n.identity.to_string() for n in engine.graph.nodes()
    )
    engine.close()


def test_a_scan_of_an_unavailable_element_is_skipped_not_fatal(tmp_path):
    engine = Engine.from_text(MANIFEST)
    engine.declare()
    engine.simulate(root=tmp_path / "world")
    engine.attach(wanted=("file-read",))
    engine.fire("service-dies", element="payments")
    engine.resolver = engine.world.bind()

    report = engine.scan()

    assert "payments" not in report["elements"]
    assert "api" in report["elements"]
    engine.close()
