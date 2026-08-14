"""Plugins: one registry, in-process and out, and what happens when they misbehave.

Every external-plugin test launches a real subprocess. A mocked pipe would prove
the protocol parser works and nothing about whether a foreign process can
actually be driven, killed, or trusted — which is the entire question.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from slpie.core import CommandBus
from slpie.domain import Evidence, EvidenceKind, GapKind, SourceLocation
from slpie.errors import PluginError, PluginProtocolError, PluginQuarantined
from slpie.ledger import CountingProjection, InMemoryLedger, ProjectionSet
from slpie.plugins import (
    DiscoveryResult,
    ExtensionPoint,
    Limits,
    Message,
    Observation,
    PluginManifest,
    Registry,
    Runtime,
)

LOCK = Evidence(
    kind=EvidenceKind.LOCKFILE_PIN,
    location=SourceLocation("file:///r/package-lock.json", line=12),
    extractor="builtin.npm",
)


def observation(evidence=LOCK, **overrides):
    defaults = dict(
        kind="depends_on", subject="pkg:npm/app@1.0.0",
        object="pkg:npm/lodash@4.17.20", evidence=evidence,
    )
    return Observation(**{**defaults, **overrides})


def npm_handler(uri, **_context):
    return DiscoveryResult(observations=(observation(),))


# --- external plugin fixtures -------------------------------------------

WORKING_PLUGIN = """
import json
import sys

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    message = json.loads(line)
    if message["method"] == "describe":
        result = {"id": "acme.go-analyzer", "kinds": ["discoverer"]}
    else:
        uri = message["params"]["uri"]
        result = {"observations": [{
            "kind": "imports",
            "subject": "urn:slpie:module:x/main",
            "object": "pkg:golang/github.com/y/z",
            "evidence": {
                "kind": "static_import",
                "location": {"uri": uri, "line": 7},
                "excerpt": "import github.com/y/z",
            },
        }]}
    print(json.dumps({"id": message["id"], "result": result}), flush=True)
"""

MISBEHAVING = {
    "crash": "import sys\nsys.exit(1)\n",
    "garbage": 'import sys\nfor line in sys.stdin:\n    print("not json", flush=True)\n',
    "hang": "import sys, time\nfor line in sys.stdin:\n    time.sleep(60)\n",
    "wrong-id": (
        "import sys, json\n"
        "for line in sys.stdin:\n"
        "    json.loads(line)\n"
        '    print(json.dumps({"id": 999, "result": {}}), flush=True)\n'
    ),
    "no-evidence": (
        "import sys, json\n"
        "for line in sys.stdin:\n"
        "    m = json.loads(line)\n"
        '    print(json.dumps({"id": m["id"], "result": {"observations": ['
        '{"kind": "imports", "subject": "a", "object": "b"}]}}), flush=True)\n'
    ),
    "wrong-evidence-kind": (
        "import sys, json\n"
        "for line in sys.stdin:\n"
        "    m = json.loads(line)\n"
        '    print(json.dumps({"id": m["id"], "result": {"observations": ['
        '{"kind": "imports", "subject": "a", "object": "b", "evidence": '
        '{"kind": "lockfile_pin", "location": {"uri": "x"}}}]}}), flush=True)\n'
    ),
    "reports-error": (
        "import sys, json\n"
        "for line in sys.stdin:\n"
        "    m = json.loads(line)\n"
        '    print(json.dumps({"id": m["id"], "error": "cannot parse that file"}), flush=True)\n'
    ),
}


def write_plugin(root: Path, plugin_id: str, source: str, **manifest_extra) -> Path:
    directory = root / plugin_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "plugin.py").write_text(textwrap.dedent(source), encoding="utf-8")
    manifest = {
        "id": plugin_id,
        "version": "1.0.0",
        "kind": "discoverer",
        "runtime": "external",
        "entrypoint": [sys.executable, "./plugin.py"],
        "handles": ["**/*.go", "go.mod"],
        "produces": ["imports"],
        "evidence_kinds": ["static_import"],
        **manifest_extra,
    }
    (directory / "slpie-plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


@pytest.fixture
def registry():
    built = Registry()
    yield built
    built.shutdown()


@pytest.fixture
def go_plugin(tmp_path, registry):
    directory = write_plugin(tmp_path, "acme.go-analyzer", WORKING_PLUGIN)
    registry.load(directory)
    return registry.get("acme.go-analyzer")


# --- manifests -----------------------------------------------------------


def test_a_manifest_declares_what_a_plugin_is(tmp_path):
    directory = write_plugin(tmp_path, "acme.go-analyzer", WORKING_PLUGIN)
    manifest = PluginManifest.load(directory)

    assert manifest.id == "acme.go-analyzer"
    assert manifest.kind is ExtensionPoint.DISCOVERER
    assert manifest.runtime is Runtime.EXTERNAL
    assert manifest.external


def test_a_manifest_without_an_id_or_entrypoint_is_refused():
    with pytest.raises(PluginError, match="must declare an id"):
        PluginManifest(id="", entrypoint=("x",))
    with pytest.raises(PluginError, match="no entrypoint"):
        PluginManifest(id="x")


def test_an_unknown_evidence_kind_in_a_manifest_is_refused():
    with pytest.raises(PluginError, match="unknown evidence kind"):
        PluginManifest.from_dict({
            "id": "x", "entrypoint": ["x"], "evidence_kinds": ["telepathy"],
        })


def test_a_manifest_that_is_not_json_names_the_file(tmp_path):
    directory = tmp_path / "broken"
    directory.mkdir()
    (directory / "slpie-plugin.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(PluginError, match="not valid JSON"):
        PluginManifest.load(directory)


def test_a_plugin_claims_files_by_pattern(tmp_path):
    manifest = PluginManifest.load(write_plugin(tmp_path, "p", WORKING_PLUGIN))

    assert manifest.matches("file:///x/main.go")
    assert manifest.matches("file:///x/go.mod")
    assert not manifest.matches("file:///x/package.json")


def test_declared_evidence_kinds_are_a_constraint_not_documentation(tmp_path):
    manifest = PluginManifest.load(write_plugin(tmp_path, "p", WORKING_PLUGIN))

    assert manifest.may_claim(EvidenceKind.STATIC_IMPORT)
    # Confidence is derived from evidence kind, so choosing your own kind would
    # be choosing your own confidence.
    assert not manifest.may_claim(EvidenceKind.LOCKFILE_PIN)


# --- the registry --------------------------------------------------------


def test_a_builtin_registers_through_the_same_path_a_third_party_uses(registry):
    registry.register_builtin(
        "builtin.npm", npm_handler,
        handles=["package-lock.json"], evidence_kinds=[EvidenceKind.LOCKFILE_PIN],
    )
    result = registry.discover("file:///r/package-lock.json")

    assert len(result.observations) == 1
    assert result.clean


def test_registering_the_same_plugin_twice_is_refused(registry):
    registry.register_builtin("p", npm_handler, handles=["*.json"])
    with pytest.raises(PluginError, match="already registered"):
        registry.register_builtin("p", npm_handler, handles=["*.json"])


def test_an_in_process_plugin_with_no_handler_is_refused(registry):
    with pytest.raises(PluginError, match="no handler"):
        registry.register(PluginManifest(id="p", entrypoint=("p",)))


def test_an_external_plugin_given_a_handler_is_refused(registry):
    with pytest.raises(PluginError, match="external runtime"):
        registry.register(
            PluginManifest(id="p", entrypoint=("p",), runtime=Runtime.EXTERNAL),
            npm_handler,
        )


def test_only_plugins_that_claim_a_file_are_run(registry):
    registry.register_builtin(
        "npm", npm_handler, handles=["package-lock.json"],
        evidence_kinds=[EvidenceKind.LOCKFILE_PIN],
    )
    assert len(registry.discover("file:///r/package-lock.json").observations) == 1
    assert len(registry.discover("file:///r/README.md").observations) == 0


def test_several_plugins_may_claim_one_file(registry):
    """A package.json is read for dependencies and for workspace structure.
    Both are evidence about the same source."""
    def structure(uri, **_context):
        evidence = Evidence(
            kind=EvidenceKind.MANIFEST_DECLARED,
            location=SourceLocation(uri, line=2), extractor="workspace",
        )
        return DiscoveryResult(observations=(observation(evidence=evidence, kind="owns"),))

    registry.register_builtin(
        "npm", npm_handler, handles=["package.json"],
        evidence_kinds=[EvidenceKind.LOCKFILE_PIN],
    )
    registry.register_builtin(
        "workspace", structure, handles=["package.json"],
        evidence_kinds=[EvidenceKind.MANIFEST_DECLARED],
    )

    result = registry.discover("file:///r/package.json")
    assert {o.kind for o in result.observations} == {"depends_on", "owns"}


def test_priority_decides_the_order_plugins_run_in(registry):
    registry.register_builtin("late", npm_handler, handles=["*.json"], priority=200)
    registry.register_builtin("early", npm_handler, handles=["*.json"], priority=10)

    assert [r.id for r in registry.for_uri("x.json")] == ["early", "late"]


def test_an_in_process_plugin_is_held_to_the_evidence_rules_too(registry):
    """A built-in that could claim any evidence kind while a third party could
    not would make the constraint about trust rather than correctness."""
    def liar(uri, **_context):
        evidence = Evidence(
            kind=EvidenceKind.LOCKFILE_PIN, location=SourceLocation(uri), extractor="liar",
        )
        return DiscoveryResult(observations=(observation(evidence=evidence),))

    registry.register_builtin(
        "liar", liar, handles=["*.txt"], evidence_kinds=[EvidenceKind.NAME_HEURISTIC],
    )
    result = registry.discover("file:///r/x.txt")

    assert result.observations == ()
    assert "undeclared evidence kind" in result.errors[0]


def test_a_plugin_returning_the_wrong_type_is_reported(registry):
    registry.register_builtin("bad", lambda uri, **kw: {"nope": True}, handles=["*.txt"])
    result = registry.discover("file:///r/x.txt")

    assert "expected a DiscoveryResult" in result.errors[0]


def test_one_failing_plugin_does_not_cost_the_observations_of_the_others(registry):
    def explode(uri, **_context):
        raise RuntimeError("this analyzer is broken")

    registry.register_builtin("broken", explode, handles=["*.json"])
    registry.register_builtin(
        "npm", npm_handler, handles=["*.json"],
        evidence_kinds=[EvidenceKind.LOCKFILE_PIN],
    )

    result = registry.discover("file:///r/package.json")
    assert len(result.observations) == 1
    assert any("broken" in error for error in result.errors)


def test_registration_is_recorded_on_the_ledger():
    ledger = InMemoryLedger()
    commands = CommandBus(ledger)
    registry = Registry(commands=commands)
    registry.register_builtin("npm", npm_handler, handles=["*.json"])

    kinds = {record.kind.value for record in ledger.read()}
    assert "plugin_registered" in kinds


def test_unregistering_removes_a_plugin(registry):
    registry.register_builtin("npm", npm_handler, handles=["*.json"])
    assert "npm" in registry

    assert registry.unregister("npm")
    assert "npm" not in registry
    assert not registry.unregister("never-registered")


# --- loading from disk ---------------------------------------------------


def test_plugins_load_from_a_directory(tmp_path, registry):
    write_plugin(tmp_path, "one", WORKING_PLUGIN)
    write_plugin(tmp_path, "two", WORKING_PLUGIN, id="two")

    loaded = registry.discover_plugins(tmp_path)
    assert len(loaded) == 2


def test_one_broken_plugin_directory_does_not_stop_the_others_loading(tmp_path, registry):
    write_plugin(tmp_path, "good", WORKING_PLUGIN, id="good")
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "slpie-plugin.json").write_text("{not json", encoding="utf-8")

    loaded = registry.discover_plugins(tmp_path)

    assert [r.id for r in loaded] == ["good"]
    assert any(gap.kind is GapKind.PLUGIN_UNAVAILABLE for gap in registry.gaps())


def test_loading_from_a_directory_that_does_not_exist_is_empty_not_an_error(registry):
    assert registry.discover_plugins("/nowhere/at/all") == ()


# --- the out-of-process protocol ----------------------------------------


@pytest.mark.slow
def test_an_external_plugin_runs_in_a_real_subprocess_and_answers(go_plugin, registry):
    result = registry.discover("file:///x/main.go")

    assert len(result.observations) == 1
    assert str(result.observations[0]) == (
        "urn:slpie:module:x/main -imports-> pkg:golang/github.com/y/z"
    )
    assert go_plugin.external.alive


def test_evidence_from_an_external_plugin_is_attributed_to_it(go_plugin, registry):
    result = registry.discover("file:///x/main.go")
    evidence = result.observations[0].evidence

    assert evidence.kind is EvidenceKind.STATIC_IMPORT
    assert evidence.extractor == "acme.go-analyzer"
    assert evidence.extractor_version == "1.0.0"
    assert evidence.location.reference == "main.go:7"


def test_describe_verifies_the_plugin_is_who_its_manifest_says(go_plugin):
    assert go_plugin.external.describe()["id"] == "acme.go-analyzer"


def test_a_plugin_describing_itself_as_something_else_is_withdrawn(tmp_path, registry):
    source = WORKING_PLUGIN.replace("acme.go-analyzer", "someone-else")
    write_plugin(tmp_path, "impostor", source, id="impostor")
    registry.load(tmp_path / "impostor")

    with pytest.raises(PluginQuarantined, match="describes itself"):
        registry.get("impostor").external.describe()


def test_a_message_round_trips_through_the_wire_format():
    request = Message(method="discover", id=7, params={"uri": "file:///x"})
    decoded = Message.decode(request.encode())

    assert decoded.method == "discover"
    assert decoded.id == 7
    assert decoded.params["uri"] == "file:///x"


def test_a_line_that_is_not_json_is_a_protocol_error():
    with pytest.raises(PluginProtocolError, match="not JSON"):
        Message.decode("hello")
    with pytest.raises(PluginProtocolError, match="expected a JSON object"):
        Message.decode("[1, 2]")


# --- misbehaving plugins -------------------------------------------------


@pytest.mark.parametrize("name,expected", [
    ("crash", "closed its output"),
    ("hang", "did not answer"),
    ("wrong-id", "replied to id"),
])
def test_a_plugin_that_breaks_the_protocol_is_withdrawn(tmp_path, name, expected):
    write_plugin(tmp_path, name, MISBEHAVING[name], id=name)
    registry = Registry(limits=Limits(wall_seconds=1.5))
    registry.load(tmp_path / name)

    result = registry.discover("file:///x/main.go")

    assert result.observations == ()
    assert any(expected in error for error in result.errors)
    assert registry.get(name).quarantined
    assert any(gap.kind is GapKind.PLUGIN_QUARANTINED for gap in registry.gaps())
    registry.shutdown()


def test_a_withdrawn_plugin_is_never_consulted_again(tmp_path):
    """Retrying a broken analyzer on every file turns one bad plugin into a
    scan that never finishes."""
    write_plugin(tmp_path, "crash", MISBEHAVING["crash"], id="crash")
    registry = Registry(limits=Limits(wall_seconds=1.5))
    registry.load(tmp_path / "crash")

    registry.discover("file:///x/a.go")
    assert registry.for_uri("file:///x/b.go") == ()
    assert registry.discover("file:///x/b.go").errors == ()
    registry.shutdown()


def test_a_plugin_sending_rubbish_is_tolerated_briefly_then_withdrawn(tmp_path):
    write_plugin(tmp_path, "garbage", MISBEHAVING["garbage"], id="garbage")
    registry = Registry(limits=Limits(wall_seconds=2.0))
    registry.load(tmp_path / "garbage")

    for index in range(3):
        registry.discover(f"file:///x/{index}.go")

    assert registry.get("garbage").quarantined
    registry.shutdown()


def test_an_observation_without_evidence_is_dropped_at_the_boundary(tmp_path, registry):
    """The graph's central invariant, enforced where an external process could
    otherwise put a hole in it."""
    write_plugin(tmp_path, "no-evidence", MISBEHAVING["no-evidence"], id="no-evidence")
    registry.load(tmp_path / "no-evidence")

    result = registry.discover("file:///x/main.go")

    assert result.observations == ()
    assert "carries no evidence" in result.errors[0]
    # Dropped, not quarantined: the plugin works, this observation was wrong.
    assert not registry.get("no-evidence").quarantined


def test_a_plugin_cannot_claim_an_evidence_kind_it_did_not_declare(tmp_path, registry):
    write_plugin(
        tmp_path, "wrong-evidence-kind", MISBEHAVING["wrong-evidence-kind"],
        id="wrong-evidence-kind",
    )
    registry.load(tmp_path / "wrong-evidence-kind")

    result = registry.discover("file:///x/main.go")

    assert result.observations == ()
    assert "did not declare" in result.errors[0]


def test_a_plugin_reporting_its_own_error_is_not_withdrawn(tmp_path, registry):
    write_plugin(tmp_path, "reports-error", MISBEHAVING["reports-error"], id="reports-error")
    registry.load(tmp_path / "reports-error")

    result = registry.discover("file:///x/main.go")

    assert "cannot parse that file" in result.errors[0]
    assert not registry.get("reports-error").quarantined


def test_a_quarantine_reaches_the_ledger(tmp_path):
    ledger = InMemoryLedger()
    commands = CommandBus(ledger)
    write_plugin(tmp_path, "crash", MISBEHAVING["crash"], id="crash")
    registry = Registry(commands=commands, limits=Limits(wall_seconds=1.5))
    registry.load(tmp_path / "crash")

    registry.discover("file:///x/main.go")

    kinds = {record.kind.value for record in ledger.read()}
    assert "plugin_quarantined" in kinds
    registry.shutdown()


# --- the sandbox ---------------------------------------------------------


def test_limits_are_enforceable_on_this_platform():
    limits = Limits()
    assert limits.available
    assert limits.preexec() is not None


def test_the_child_environment_is_narrowed():
    """A plugin inheriting the parent's whole environment would pick up
    credentials that have nothing to do with parsing a lockfile."""
    environment = Limits().environment({
        "PATH": "/usr/bin",
        "HOME": "/home/x",
        "AWS_SECRET_ACCESS_KEY": "should-not-be-inherited",
        "DATABASE_URL": "postgres://secret@host/db",
    })

    assert environment["PATH"] == "/usr/bin"
    assert "AWS_SECRET_ACCESS_KEY" not in environment
    assert "DATABASE_URL" not in environment
    assert environment["SLPIE_PLUGIN"] == "1"


def test_limits_are_reported_so_degraded_isolation_is_visible():
    payload = Limits(cpu_seconds=5).to_dict()

    assert payload["cpu_seconds"] == 5
    assert "enforced" in payload


def test_a_plugin_that_exceeds_its_cpu_limit_is_killed(tmp_path):
    """The limits are containment: a runaway regex must not take the platform
    down with it."""
    write_plugin(
        tmp_path, "spinner",
        "import sys\nfor line in sys.stdin:\n    while True:\n        pass\n",
        id="spinner",
    )
    registry = Registry(limits=Limits(cpu_seconds=1, wall_seconds=4.0))
    registry.load(tmp_path / "spinner")

    result = registry.discover("file:///x/main.go")

    assert result.observations == ()
    assert registry.get("spinner").quarantined
    registry.shutdown()


# --- status --------------------------------------------------------------


def test_the_registry_reports_every_plugin_and_gap(go_plugin, registry):
    registry.register_builtin("npm", npm_handler, handles=["*.json"])
    registry.discover("file:///x/main.go")

    status = registry.status()
    assert status["count"] == 2
    identifiers = {plugin["id"] for plugin in status["plugins"]}
    assert identifiers == {"acme.go-analyzer", "npm"}
    assert any(plugin["observations"] == 1 for plugin in status["plugins"])
