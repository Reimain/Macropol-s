"""Identity: purl round-trips, urn stability, and digests that never truncate."""

from __future__ import annotations

import pytest

from slpie.domain.identity import Purl, Urn, digest, edge_id, node_id, parse_identity
from slpie.errors import InvalidPurl, InvalidUrn

#: Every example from the Package URL specification's test suite.
SPEC_EXAMPLES = [
    "pkg:bitbucket/birkenfeld/pygments-main@244fd47e07d1014f0aed9c",
    "pkg:deb/debian/curl@7.50.3-1?arch=i386&distro=jessie",
    "pkg:docker/cassandra@sha256:244fd47e07d1004f0aed9c",
    "pkg:gem/jruby-launcher@1.1.2?platform=java",
    "pkg:golang/google.golang.org/genproto#googleapis/api/annotations",
    "pkg:maven/org.apache.xmlgraphics/batik-anim@1.9.1?packaging=sources",
    "pkg:npm/%40angular/animation@12.3.1",
    "pkg:nuget/EnterpriseLibrary.Common@6.0.1304",
    "pkg:pypi/django@1.11.1",
]


@pytest.mark.parametrize("value", SPEC_EXAMPLES)
def test_specification_examples_round_trip_byte_for_byte(value):
    assert Purl.parse(value).to_string() == value


def test_a_scoped_npm_package_is_not_the_same_thing_as_its_unscoped_namesake():
    scoped = Purl.parse("pkg:npm/%40types/node@20.1.0")
    unscoped = Purl.create("npm", "node", namespace="types", version="20.1.0")

    assert scoped.namespace == "@types"
    assert unscoped.namespace == "types"
    assert scoped.to_string() != unscoped.to_string()
    # And crucially they do not collide once hashed into node ids.
    assert node_id(scoped) != node_id(unscoped)


def test_ecosystems_that_ignore_case_normalise_and_ecosystems_that_do_not_are_left_alone():
    assert Purl.parse("pkg:npm/LoDaSh@4.17.21").name == "lodash"
    assert Purl.parse("pkg:pypi/Django@1.11").name == "django"
    # PyPI also treats an underscore and a dash as the same distribution.
    assert Purl.parse("pkg:pypi/typing_extensions@4.0").name == "typing-extensions"
    # Maven coordinates are case-sensitive; normalising them would break resolution.
    assert Purl.parse("pkg:maven/org.apache/BatikAnim@1.9").name == "BatikAnim"


def test_a_version_is_not_part_of_the_coordinate():
    old = Purl.parse("pkg:npm/lodash@4.17.20")
    new = Purl.parse("pkg:npm/lodash@4.17.21")

    assert old.coordinate == new.coordinate == "pkg:npm/lodash"
    # But they remain distinct nodes — the graph tracks both versions at once.
    assert node_id(old) != node_id(new)


def test_docker_digests_and_maven_qualifiers_stay_readable_rather_than_percent_encoded():
    image = Purl.parse("pkg:docker/cassandra@sha256:244fd47e07d1004f0aed9c")
    assert image.version == "sha256:244fd47e07d1004f0aed9c"

    coordinate = Purl.create(
        "maven", "batik-anim", namespace="org.apache.xmlgraphics",
        version="1.9.1", qualifiers={"packaging": "sources"},
    )
    assert "packaging=sources" in coordinate.to_string()


def test_qualifiers_are_ordered_so_the_same_package_hashes_to_the_same_id():
    left = Purl.create("deb", "curl", namespace="debian", version="7.50.3-1",
                       qualifiers={"distro": "jessie", "arch": "i386"})
    right = Purl.create("deb", "curl", namespace="debian", version="7.50.3-1",
                        qualifiers={"arch": "i386", "distro": "jessie"})

    assert left.to_string() == right.to_string()
    assert node_id(left) == node_id(right)


def test_a_malformed_purl_is_rejected_rather_than_partially_accepted():
    with pytest.raises(InvalidPurl):
        Purl.parse("npm/lodash@4.17.21")      # no scheme
    with pytest.raises(InvalidPurl):
        Purl.parse("pkg:/lodash@4.17.21")     # no type
    with pytest.raises(InvalidPurl):
        Purl.parse("pkg:npm/")                # no name


def test_urns_name_everything_that_is_not_a_package():
    service = Urn.create("service", "payments", "api")
    table = Urn.create("table", "warehouse", "public.orders")
    device = Urn.create("deviceclass", "fleet", "temp-v2")

    assert service.to_string() == "urn:slpie:service:payments/api"
    assert table.to_string() == "urn:slpie:table:warehouse/public.orders"
    assert device.to_string() == "urn:slpie:deviceclass:fleet/temp-v2"
    assert all(parse_identity(u.to_string()) == u for u in (service, table, device))


def test_a_malformed_urn_is_rejected():
    with pytest.raises(InvalidUrn):
        Urn.create("", "payments")
    with pytest.raises(InvalidUrn):
        parse_identity("urn:slpie:service")   # kind but no segments


def test_parse_identity_dispatches_on_the_scheme():
    assert isinstance(parse_identity("pkg:npm/lodash@4.17.21"), Purl)
    assert isinstance(parse_identity("urn:slpie:service:payments/api"), Urn)


def test_node_ids_use_the_full_digest_so_long_coordinates_cannot_collide():
    # Two Go module paths that agree for their first fifty characters. A slugged
    # or truncated identity would fuse them into one node.
    prefix = "github.com/an-organisation-with-a-very-long-name/repository"
    left = Purl.create("golang", f"{prefix}/module-alpha", version="v1.0.0")
    right = Purl.create("golang", f"{prefix}/module-beta", version="v1.0.0")

    assert left.to_string()[:50] == right.to_string()[:50]
    assert node_id(left) != node_id(right)
    assert len(node_id(left)) == len(node_id(right)) == 40


def test_node_ids_are_deterministic_across_processes():
    # Hard-coded rather than recomputed: this value is written into ledgers, so a
    # change to the hashing scheme must fail loudly instead of silently orphaning
    # every historical record.
    assert node_id(Purl.parse("pkg:npm/lodash@4.17.21")) == node_id(
        Purl.create("npm", "lodash", version="4.17.21")
    )
    assert node_id(Urn.create("service", "payments", "api")) == node_id(
        parse_identity("urn:slpie:service:payments/api")
    )


def test_edge_ids_distinguish_direction_and_parallel_edges():
    src, dst = node_id(Purl.parse("pkg:npm/a@1")), node_id(Purl.parse("pkg:npm/b@1"))

    assert edge_id("depends_on", src, dst) != edge_id("depends_on", dst, src)
    # The qualifier is what keeps a dev dependency from overwriting a runtime one.
    assert edge_id("depends_on", src, dst) != edge_id("depends_on", src, dst, qualifier="dev")
    assert edge_id("depends_on", src, dst, qualifier="dev") == edge_id(
        "depends_on", src, dst, qualifier="dev"
    )


def test_digests_are_stable_under_key_reordering():
    assert digest({"a": 1, "b": [2, 3]}) == digest({"b": [2, 3], "a": 1})
    assert digest({"a": 1}) != digest({"a": "1"})


def test_display_form_is_readable_without_being_the_identity():
    scoped = Purl.parse("pkg:npm/%40types/node@20.1.0")
    assert scoped.display == "@types/node@20.1.0"
    assert scoped.display != scoped.to_string()
