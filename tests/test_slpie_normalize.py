"""Normalization — the convergence everything downstream rests on.

One property carries this package: **two discoverers reading the same package
in two dialects must produce the same node id.** A Maven POM and a Gradle build
script, a PyPI classifier and a `pyproject.toml`, a lockfile pin and a manifest
range. Where they diverge the graph holds two nodes for one thing, and every
blast radius, SBOM and advisory match built on it is wrong in a way nothing
surfaces.

The other theme is refusal. `com.acme:payments:jar` and `"BSD"` are both
genuinely ambiguous, and a module that guesses produces an answer somebody
relies on. Those tests assert that nothing is returned.
"""

from __future__ import annotations

import pytest

from slpie.domain.identity import InvalidPurl, Purl
from slpie.normalize import coordinates, licenses, purl, versions
from slpie.normalize.coordinates import CoordinateError

# --- the convergence property ---------------------------------------------


@pytest.mark.parametrize("ecosystem,left,right", [
    ("pypi", "PyYAML", "pyyaml"),
    ("pypi", "zope.interface", "zope-interface"),
    ("pypi", "Flask_SQLAlchemy", "flask-sqlalchemy"),
    ("nuget", "Newtonsoft.Json", "newtonsoft.json"),
    ("npm", "lodash", "lodash"),
])
def test_two_spellings_of_one_package_land_on_one_node(ecosystem, left, right):
    """The single property the linking layer exists to provide."""
    first = purl.canonical_node_id(f"pkg:{ecosystem}/{left}")
    second = purl.canonical_node_id(f"pkg:{ecosystem}/{right}")
    assert first == second


def test_a_pom_and_a_gradle_script_converge_on_one_node():
    """The two JVM dialects a real repository contains at once."""
    from_pom = coordinates.to_purl("maven", "com.acme:payments", version="1.0.0")
    from_gradle = coordinates.to_purl("maven", "com.acme:payments:1.0.0")
    assert from_pom == from_gradle
    assert purl.node_id(from_pom) == purl.node_id(from_gradle)


def test_different_packages_do_not_converge():
    assert (
        purl.canonical_node_id("pkg:pypi/requests")
        != purl.canonical_node_id("pkg:pypi/requests-oauthlib")
    )


def test_the_same_name_in_two_ecosystems_is_two_packages():
    """`pkg:npm/lodash` and `pkg:pypi/lodash` are unrelated."""
    assert (
        purl.canonical_node_id("pkg:npm/lodash")
        != purl.canonical_node_id("pkg:pypi/lodash")
    )


def test_a_version_does_not_change_which_package_it_is():
    """A manifest range and a lockfile pin must reach one node."""
    assert coordinates.same_package("pkg:pypi/PyYAML@6.0", "pkg:pypi/pyyaml@5.4.1")


def test_go_module_paths_keep_their_slashes():
    parsed = purl.canonical_purl("pkg:golang/github.com/acme/payments")
    assert "github.com/acme" in parsed.to_string()


# --- purl round trips ------------------------------------------------------


@pytest.mark.parametrize("text", [
    "pkg:npm/%40scope/thing@1.2.3",
    "pkg:golang/github.com/x/y@v1.2.3",
    "pkg:maven/com.acme/payments@1.0",
    "pkg:pypi/zope-interface@5.0",
    "pkg:cargo/serde@1.0.195",
    "pkg:nuget/newtonsoft.json@13.0.3",
])
def test_a_purl_survives_rendering_and_reparsing(text):
    """Percent-encoding a namespace is only safe if it reverses."""
    assert purl.round_trips(Purl.parse(text))


def test_a_scoped_npm_name_keeps_its_scope_through_encoding():
    parsed = Purl.parse("pkg:npm/%40scope/thing@1.2.3")
    assert parsed.namespace == "@scope"
    assert purl.round_trips(parsed)


# --- native coordinates ↔ purl ---------------------------------------------


@pytest.mark.parametrize("ecosystem,coordinate,expected", [
    ("maven", "com.acme:payments:1.0.0", "pkg:maven/com.acme/payments@1.0.0"),
    ("maven", "com.acme:payments:jar:1.0.0", "pkg:maven/com.acme/payments@1.0.0"),
    ("golang", "github.com/x/y v1.2.3", "pkg:golang/github.com/x/y@1.2.3"),
    ("pypi", "PyYAML==6.0", "pkg:pypi/pyyaml@6.0"),
    ("npm", "@scope/thing@1.2.3", "pkg:npm/%40scope/thing@1.2.3"),
    ("npm", "lodash@4.17.21", "pkg:npm/lodash@4.17.21"),
    ("cargo", "serde 1.0.195", "pkg:cargo/serde@1.0.195"),
    ("nuget", "Newtonsoft.Json 13.0.3", "pkg:nuget/newtonsoft.json@13.0.3"),
])
def test_a_native_coordinate_becomes_the_right_purl(ecosystem, coordinate, expected):
    assert coordinates.to_purl(ecosystem, coordinate).to_string() == expected


def test_a_scoped_npm_version_splits_on_the_last_at_not_the_first():
    """Splitting on the first `@` loses the scope entirely."""
    parsed = coordinates.to_purl("npm", "@scope/thing@1.2.3")
    assert parsed.namespace == "@scope"
    assert parsed.version == "1.2.3"


def test_an_explicit_version_overrides_one_embedded_in_the_coordinate():
    """A `<version>` element is more trustworthy than re-parsing a string."""
    parsed = coordinates.to_purl("maven", "com.acme:payments:1.0.0", version="2.0.0")
    assert parsed.version == "2.0.0"


def test_a_maven_packaging_type_is_not_mistaken_for_a_version():
    """`group:artifact:jar` has no version; reading `jar` as one is worse than
    reading nothing."""
    assert coordinates.to_purl("maven", "com.acme:payments:jar").version == ""


def test_a_maven_coordinate_with_too_few_segments_is_refused():
    with pytest.raises(CoordinateError, match="at least group:artifact"):
        coordinates.to_purl("maven", "payments")


def test_a_coordinate_with_three_whitespace_parts_is_refused_not_guessed():
    with pytest.raises(CoordinateError, match="whitespace-separated parts"):
        coordinates.to_purl("cargo", "serde 1.0.195 extra")


def test_an_empty_coordinate_names_nothing():
    with pytest.raises(CoordinateError, match="names nothing"):
        coordinates.to_purl("cargo", "   ")


# --- back to what the tooling accepts --------------------------------------


@pytest.mark.parametrize("ecosystem,coordinate", [
    ("maven", "com.acme:payments:1.0.0"),
    ("golang", "github.com/x/y v1.2.3"),
    ("pypi", "PyYAML==6.0"),
    ("npm", "@scope/thing@1.2.3"),
    ("cargo", "serde 1.0.195"),
    ("nuget", "Newtonsoft.Json 13.0.3"),
])
def test_a_coordinate_survives_both_directions(ecosystem, coordinate):
    """An identity good one way and lossy the other is a formatter."""
    assert coordinates.round_trips(ecosystem, coordinate)


def test_a_go_version_keeps_its_v_prefix_going_back():
    """`go.mod` will not accept the bare form, however well it compares."""
    parsed = coordinates.to_purl("golang", "github.com/x/y v1.2.3")
    assert coordinates.to_native(parsed) == "github.com/x/y v1.2.3"


def test_a_report_renders_what_the_developer_can_paste():
    """Telling a Python developer `pkg:pypi/pyyaml@6.0` leaves them to translate."""
    assert coordinates.to_native("pkg:pypi/pyyaml@6.0") == "pyyaml==6.0"
    assert coordinates.to_native("pkg:maven/com.acme/payments@1.0") == \
        "com.acme:payments:1.0"


def test_describe_gives_both_spellings_and_the_coordinate():
    described = coordinates.describe("pkg:npm/%40scope/thing@1.2.3")
    assert described["native"] == "@scope/thing@1.2.3"
    assert described["purl"].startswith("pkg:npm/")
    assert described["version"] == "1.2.3"


def test_comparing_something_that_is_not_a_purl_is_false_not_an_error():
    assert not coordinates.same_package("not a purl", "pkg:pypi/x")


# --- versions --------------------------------------------------------------


@pytest.mark.parametrize("ecosystem,raw,expected", [
    ("golang", "v1.2.3", "1.2.3"),
    ("golang", "1.2.3", "1.2.3"),
])
def test_a_go_version_normalises_for_comparison(ecosystem, raw, expected):
    assert versions.normalize_version(raw, ecosystem=ecosystem) == expected


def test_a_prerelease_is_recognised():
    assert versions.is_prerelease("1.0.0-alpha.1")
    assert not versions.is_prerelease("1.0.0")


def test_versions_compare_across_their_own_notation():
    assert versions.compare("1.0.0", "1.0.1") < 0
    assert versions.compare("2.0.0", "1.9.9") > 0
    assert versions.compare("1.0.0", "1.0.0") == 0


def test_the_highest_of_a_set_is_found():
    assert versions.highest(["1.0.0", "1.10.0", "1.9.0"]) == "1.10.0"


# --- licences: the refusals are the point ----------------------------------


@pytest.mark.parametrize("value,expected", [
    ("MIT", "MIT"),
    ("Apache-2.0", "Apache-2.0"),
    ("MIT OR Apache-2.0", "MIT OR Apache-2.0"),
    ({"type": "ISC"}, "ISC"),
    ("License :: OSI Approved :: MIT License", "MIT"),
    ("Apache 2", "Apache-2.0"),
    ("the MIT License", "MIT"),
])
def test_a_recognisable_licence_becomes_spdx(value, expected):
    assert licenses.normalize_license(value).expression == expected


def test_npms_legacy_array_is_read_as_a_choice_not_a_conjunction():
    """`AND` would compound obligations the publisher never imposed."""
    reading = licenses.normalize_license([{"type": "MIT"}, {"type": "Apache-2.0"}])
    assert reading.expression == "MIT OR Apache-2.0"


@pytest.mark.parametrize("value", ["BSD", "GPL", "LGPL", "bsd license", "Artistic"])
def test_an_ambiguous_licence_is_refused_and_says_what_it_is_ambiguous_between(value):
    """`BSD` covers three licences with different obligations. Picking one
    produces a compliance answer somebody relies on."""
    reading = licenses.normalize_license(value)
    assert reading.expression == ""
    assert not reading.confident
    assert len(reading.candidates) >= 2
    assert "ambiguous" in reading.reason


def test_the_ambiguity_check_runs_before_the_spdx_pass():
    """`normalize("BSD")` happens to parse, so checking SPDX first would let the
    exact string this module exists to refuse straight through."""
    assert licenses.normalize_license("BSD").expression == ""
    assert licenses.normalize_license("BSD-3-Clause").expression == "BSD-3-Clause"


@pytest.mark.parametrize("value", ["NOASSERTION", "", "UNKNOWN", "see LICENSE", "n/a"])
def test_a_value_meaning_unknown_never_becomes_a_licence(value):
    reading = licenses.normalize_license(value)
    assert reading.expression == ""
    assert "unknown" in reading.reason.lower()


def test_something_unrecognisable_is_refused_with_a_reason_a_human_can_act_on():
    reading = licenses.normalize_license("Acme Internal Licence v3")
    assert reading.expression == ""
    assert "not a recognised SPDX identifier" in reading.reason


def test_a_refusal_is_falsey_so_callers_cannot_use_it_by_accident():
    assert not licenses.normalize_license("BSD")
    assert licenses.normalize_license("MIT")


def test_pypi_classifiers_resolve_to_one_expression():
    reading = licenses.from_classifiers([
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
    ])
    assert reading.expression == "MIT"


def test_several_licence_classifiers_are_read_as_a_choice():
    reading = licenses.from_classifiers([
        "License :: OSI Approved :: MIT License",
        "License :: OSI Approved :: Apache Software License",
    ])
    assert " OR " in reading.expression


def test_a_normalised_licence_parses_back_through_the_domain():
    """The whole point is producing something the obligation analysis can use."""
    reading = licenses.normalize_license("Apache 2")
    assert reading.parsed is not None
    assert not reading.parsed.unknown_terms


def test_a_licence_reading_serialises_with_its_reason():
    payload = licenses.normalize_license("BSD").to_dict()
    assert payload["expression"] == ""
    assert payload["candidates"]
    assert payload["reason"]
