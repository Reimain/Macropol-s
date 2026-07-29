"""Cargo, Go modules, GraphQL, gRPC and MQTT — the rest of Discovery II.

The tests that carry weight are the ones about *identity*, because a discoverer
that invents an identity is worse than one that finds nothing: a Cargo path
dependency named `helper` would match the real crates.io `helper` crate's
advisories, and a Go `exclude` recorded as a dependency puts a package the
module explicitly refuses into every blast radius and SBOM.

MQTT's two wildcards get their own section. `+` matches one level and `#`
matches the rest; flattening them into "a wildcard" makes a narrow subscriber
appear to receive everything, which is wrong in the direction that hides
problems.
"""

from __future__ import annotations

import pytest

from slpie.discovery.base import Source
from slpie.discovery.ecosystems import cargo, gomod
from slpie.discovery.interfaces import graphql, grpc, mqtt
from slpie.discovery.registry import register_builtins
from slpie.plugins.registry import Registry

ELEMENT = "urn:slpie:codebase:payments"


def read(module, text: str, *, name: str, index: int = 0):
    return module.DISCOVERERS[index][1](
        Source(uri=f"file:///repo/{name}", text=text, digest="", element=ELEMENT)
    )


def objects(found) -> set[str]:
    return {o.object for o in found.observations if o.object}


def kinds(found) -> set[str]:
    return {o.kind for o in found.observations}


def at(found, line_of_text: str, source: str) -> bool:
    """Whether some observation cites the line that actually holds this text."""
    lines = source.splitlines()
    return any(
        0 < o.evidence.location.line <= len(lines)
        and line_of_text in lines[o.evidence.location.line - 1]
        for o in found.observations
    )


# --- Cargo ----------------------------------------------------------------

CARGO_TOML = """[package]
name = "payments"
version = "0.3.1"
license = "MIT"

[dependencies]
serde = { version = "1.0", features = ["derive"] }
tokio = "1.35"
helper = { path = "../helper" }
remote = { git = "https://example.com/remote.git" }

[dev-dependencies]
criterion = "0.5"
"""


def test_a_crate_declares_itself_with_its_version_and_licence():
    found = read(cargo, CARGO_TOML, name="Cargo.toml")
    declared = [o for o in found.observations if o.kind == "declares"]
    assert declared
    assert declared[0].subject == "pkg:cargo/payments@0.3.1"
    assert declared[0].properties.get("license") == "MIT"


def test_both_shorthand_and_table_dependency_forms_are_read():
    found = read(cargo, CARGO_TOML, name="Cargo.toml")
    assert "pkg:cargo/serde" in objects(found)      # table form
    assert "pkg:cargo/tokio" in objects(found)      # shorthand


@pytest.mark.parametrize("crate", ["helper", "remote"])
def test_a_path_or_git_dependency_gets_no_crates_io_identity(crate):
    """Minting `pkg:cargo/helper` invents a package that exists for somebody
    else — it would match the real crate's advisories and merge with it."""
    found = read(cargo, CARGO_TOML, name="Cargo.toml")
    assert f"pkg:cargo/{crate}" not in objects(found)
    assert any(crate in o and o.startswith("urn:slpie:module:") for o in objects(found))


def test_dev_dependencies_are_kept_because_ci_runs_them():
    found = read(cargo, CARGO_TOML, name="Cargo.toml")
    assert "pkg:cargo/criterion" in objects(found)


CARGO_LOCK = """version = 3

[[package]]
name = "payments"
version = "0.3.1"
dependencies = ["serde"]

[[package]]
name = "serde"
version = "1.0.195"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "deadbeef"
"""


def test_the_lockfile_pins_exact_versions():
    found = read(cargo, CARGO_LOCK, name="Cargo.lock", index=1)
    assert "pkg:cargo/serde@1.0.195" in objects(found) | {
        o.subject for o in found.observations
    }


def test_lockfile_evidence_outranks_the_manifest():
    from slpie.domain.evidence import EvidenceKind

    assert EvidenceKind.LOCKFILE_PIN in cargo.DISCOVERERS[1][4]
    assert EvidenceKind.MANIFEST_DECLARED in cargo.DISCOVERERS[0][4]


def test_malformed_toml_is_reported_rather_than_raised():
    found = read(cargo, "[package\nname =", name="Cargo.toml")
    assert found.errors
    assert not found.observations


# --- Go modules -----------------------------------------------------------

GO_MOD = """module github.com/acme/payments

go 1.22

require (
\tgithub.com/gin-gonic/gin v1.9.1
\tgithub.com/stretchr/testify v1.8.4 // indirect
)

require github.com/google/uuid v1.5.0

replace github.com/acme/internal => ../internal

exclude github.com/bad/pkg v0.1.0
"""


def test_a_module_declares_its_own_path():
    found = read(gomod, GO_MOD, name="go.mod")
    assert "pkg:golang/github.com/acme/payments" in {
        o.subject for o in found.observations
    }


def test_requirements_are_read_from_both_a_block_and_a_single_line():
    found = read(gomod, GO_MOD, name="go.mod")
    assert "pkg:golang/github.com/gin-gonic/gin" in objects(found)   # block
    assert "pkg:golang/github.com/google/uuid" in objects(found)     # single line


def test_an_indirect_requirement_is_marked_as_such():
    """Direct and indirect need different remediation advice."""
    found = read(gomod, GO_MOD, name="go.mod")
    testify = [o for o in found.observations if "testify" in (o.object or "")]
    assert testify
    assert testify[0].qualifier == "indirect"


def test_an_exclude_is_not_a_dependency():
    """It states a version the module refuses. An edge would put it in every
    blast radius and SBOM as though it were used."""
    found = read(gomod, GO_MOD, name="go.mod")
    excluded = [o for o in found.observations if "bad/pkg" in (o.object or "")]
    assert excluded
    assert excluded[0].kind == "references"
    assert excluded[0].properties.get("excluded") is True
    assert all(o.kind != "depends_on" for o in excluded)


def test_a_replaced_requirement_says_it_is_replaced():
    """A replaced requirement is not what gets built."""
    found = read(gomod, GO_MOD, name="go.mod")
    assert any(
        o.properties.get("replaces") or o.qualifier == "replace"
        for o in found.observations
    )


def test_the_go_sum_pins_what_the_module_resolved_to():
    found = read(gomod, "github.com/gin-gonic/gin v1.9.1 h1:abc=\n",
                 name="go.sum", index=1)
    assert any("gin@v1.9.1" in o for o in objects(found) | {
        o.subject for o in found.observations
    })


# --- GraphQL --------------------------------------------------------------

SDL = '''"""The public API. This description contains a { brace }."""
type Query {
  orders(first: Int): [Order!]!
  health: String
}

type Order implements Node & Timestamped {
  id: ID!
  customer: Customer
}

interface Node {
  id: ID!
}
# a trailing comment
'''


def test_a_description_containing_a_brace_does_not_truncate_the_schema():
    """Left in place it closes the first type early and everything after is lost."""
    found = read(graphql, SDL, name="schema.graphql")
    declared = {o.subject for o in found.observations if o.kind == "declares"}
    assert any("Order" in s for s in declared)
    assert any("Node" in s for s in declared)


def test_line_numbers_survive_comment_stripping():
    """Evidence citing a line the reader cannot find is worse than none."""
    found = read(graphql, SDL, name="schema.graphql")
    assert at(found, "type Query {", SDL)
    assert at(found, "orders(first: Int)", SDL)


def test_each_root_field_is_its_own_api_surface():
    """Recording only `Query` makes every breaking change look identical."""
    found = read(graphql, SDL, name="schema.graphql")
    apis = {o.subject for o in found.observations if "api:graphql" in o.subject}
    assert any("Query.orders" in api for api in apis)
    assert any("Query.health" in api for api in apis)


def test_a_wrapped_type_reference_is_unwrapped():
    """`[Order!]!` refers to `Order`, not to four different names."""
    found = read(graphql, SDL, name="schema.graphql")
    assert any(o.endswith("/Order") for o in objects(found))
    assert not any("!" in o or "[" in o for o in objects(found))


def test_builtin_scalars_do_not_become_nodes():
    found = read(graphql, SDL, name="schema.graphql")
    assert not any(o.endswith(("/String", "/ID", "/Int")) for o in objects(found))


def test_implemented_interfaces_become_edges():
    found = read(graphql, SDL, name="schema.graphql")
    implemented = {
        o.object for o in found.observations
        if o.properties.get("relation") == "implements"
    }
    assert any("Node" in o for o in implemented)
    assert any("Timestamped" in o for o in implemented)


def test_a_document_with_no_type_definitions_says_so():
    found = read(graphql, "query GetOrders { orders { id } }\n", name="q.graphql")
    assert found.errors


# --- gRPC -----------------------------------------------------------------

PROTO = """syntax = "proto3";

package payments.v1;

import "common/money.proto";

// A comment containing a { brace }
service Payments {
  rpc CreateOrder (OrderRequest) returns (OrderReply);
  rpc WatchOrders (stream WatchRequest) returns (stream OrderEvent);
}

message OrderRequest {
  string id = 1;
}
"""


def test_a_comment_containing_a_brace_does_not_close_the_service():
    found = read(grpc, PROTO, name="payments.proto")
    methods = {o.subject for o in found.observations if "api:grpc" in o.subject}
    assert any("CreateOrder" in m for m in methods)
    assert any("WatchOrders" in m for m in methods)


def test_a_method_is_named_by_the_wire_path_grpc_itself_uses():
    """So a runtime trace and this static reading name the same thing."""
    found = read(grpc, PROTO, name="payments.proto")
    assert any(
        "payments.v1.Payments/CreateOrder" in o.subject for o in found.observations
    )


def test_the_package_qualifies_every_message():
    """Two files can both define `Order`; unqualified they collapse into one."""
    found = read(grpc, PROTO, name="payments.proto")
    assert any("payments.v1.OrderRequest" in o.subject for o in found.observations)


def test_streaming_is_recorded_on_both_sides_separately():
    """A unary call and a streaming one fail differently."""
    found = read(grpc, PROTO, name="payments.proto")
    watch = [o for o in found.observations if "WatchOrders" in o.subject
             and o.kind == "declares"]
    create = [o for o in found.observations if "CreateOrder" in o.subject
              and o.kind == "declares"]
    assert watch[0].properties["client_streaming"] is True
    assert watch[0].properties["server_streaming"] is True
    assert create[0].properties["streaming"] is False


def test_an_import_becomes_an_edge_between_contracts():
    found = read(grpc, PROTO, name="payments.proto")
    assert any("common/money.proto" in (o.object or "") for o in found.observations)


def test_the_request_and_reply_are_referenced_with_their_roles():
    found = read(grpc, PROTO, name="payments.proto")
    roles = {o.properties.get("role") for o in found.observations}
    assert {"request", "reply"} <= roles


def test_proto_line_numbers_are_right():
    found = read(grpc, PROTO, name="payments.proto")
    assert at(found, "service Payments {", PROTO)
    assert at(found, "rpc CreateOrder", PROTO)


# --- MQTT -----------------------------------------------------------------


@pytest.mark.parametrize("pattern,topic,expected", [
    ("fleet/+/temperature", "fleet/berlin/temperature", True),
    ("fleet/+/temperature", "fleet/berlin/van-7/temperature", False),
    ("fleet/#", "fleet/berlin/van-7/temperature", True),
    # `#` includes the *parent* level: MQTT 4.7.1.2 states that `sport/#` also
    # matches the singular `sport`. Surprising, and worth pinning down.
    ("fleet/#", "fleet", True),
    ("#", "anything/at/all", True),
    ("fleet/+", "fleet/berlin", True),
    ("fleet/+", "fleet/berlin/van-7", False),
    ("fleet/berlin", "fleet/berlin", True),
    ("fleet/berlin", "fleet/munich", False),
])
def test_the_two_wildcards_are_not_interchangeable(pattern, topic, expected):
    """`+` is one level and `#` is the rest. Flattening them makes a narrow
    subscriber look like it receives everything."""
    assert mqtt.matches(pattern, topic) is expected


@pytest.mark.parametrize("bad", [
    "fleet/#/temperature",      # `#` must be last
    "fleet/de#/x",              # `#` must be a whole level
    "fleet/van+/x",             # `+` must be a whole level
    "",
])
def test_an_illegal_filter_is_rejected_rather_than_recorded(bad):
    """A broker would refuse it, so recording it describes an estate that
    cannot exist."""
    legal, reason = mqtt.valid(bad)
    assert not legal
    assert reason


MQTT_CONFIG = """mqtt:
  broker: mqtt://fleet.acme.com
  client_id: telemetry
  publishes:
    - fleet/berlin/van-7/temperature
  subscribes:
    - fleet/+/commands
    - fleet/#
  device_classes:
    - temp-v2
    - gps-v1
"""


def test_topics_become_nodes_with_their_direction():
    found = read(mqtt, MQTT_CONFIG, name="mqtt.yaml")
    assert "publishes" in kinds(found)
    assert "subscribes" in kinds(found)


def test_a_filter_is_marked_as_a_pattern_rather_than_a_topic():
    """`fleet/+/commands` names no single thing on the wire."""
    found = read(mqtt, MQTT_CONFIG, name="mqtt.yaml")
    filters = [
        o for o in found.observations
        if o.kind == "declares" and o.properties.get("is_filter")
    ]
    assert filters
    concrete = [
        o for o in found.observations
        if o.kind == "declares" and o.properties.get("is_filter") is False
    ]
    assert any("van-7" in o.subject for o in concrete)


def test_device_classes_are_recorded():
    found = read(mqtt, MQTT_CONFIG, name="mqtt.yaml")
    assert any("temp-v2" in o.subject for o in found.observations)


def test_an_illegal_topic_in_a_config_is_reported_not_recorded():
    found = read(mqtt, "mqtt:\n  broker: x\n  subscribes:\n    - fleet/#/bad\n",
                 name="mqtt.yaml")
    assert found.errors
    assert not any("bad" in (o.subject or "") for o in found.observations)


def test_a_yaml_file_that_is_not_mqtt_is_declined_quietly():
    """It claims broad filenames, so declining matters more than reporting."""
    found = read(mqtt, "name: something-else\nversion: 1\n", name="mqtt.yaml")
    assert not found.observations
    assert not found.errors


# --- registration ---------------------------------------------------------


def test_all_five_are_wired_into_the_scanner():
    ids = {plugin.id for plugin in register_builtins(Registry())}
    for expected in ("slpie.cargo.manifest", "slpie.cargo.lockfile",
                     "slpie.gomod.manifest", "slpie.gomod.lockfile",
                     "slpie.graphql", "slpie.grpc", "slpie.mqtt"):
        assert expected in ids


def test_phase_eight_is_complete():
    """Every ecosystem, infrastructure format and interface contract planned."""
    ids = {plugin.id for plugin in register_builtins(Registry())}
    families = {identifier.split(".")[1] for identifier in ids}
    assert {
        "npm", "yarn", "python", "maven", "gradle", "nuget", "cargo", "gomod",
        "dockerfile", "compose", "kubernetes", "helm", "terraform",
        "openapi", "asyncapi", "graphql", "grpc", "mqtt",
    } <= families


def test_contracts_are_read_after_the_packaging_that_names_things():
    registry = register_builtins(Registry())
    assert (
        registry.get("slpie.cargo.manifest").manifest.priority
        < registry.get("slpie.graphql").manifest.priority
        < registry.get("slpie.javascript").manifest.priority
    )
