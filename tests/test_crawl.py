"""The crawler: politeness, caching, normalisation, and facts that cite a URL.

Every test here runs the *real* fetcher — the real rate limiter, the real robots
gate, the real retry loop, the real conditional-request cache — over a recorded
transport. Nothing is mocked out, so a green test is evidence about the code
path that runs against PyPI, not about a stand-in for it.
"""

from __future__ import annotations

import json
import time

import pytest

from gratimos.crawl import (
    Artifact,
    CrawlPolicy,
    Crawler,
    FetchError,
    Fetcher,
    GitHubSource,
    NpmSource,
    Origin,
    PolicyViolation,
    PyPISource,
    RateLimiter,
    RecordedTransport,
    Response,
    ResponseCache,
    RobotsGate,
    SourceError,
    SourceRegistry,
    official_policy,
)
from gratimos.ontology.need import Constraint, ConstraintKind, Need, need_from_text
from gratimos.reason import Engine, FactBase, Provenance, reuse_rules

ALLOW_ALL = "User-agent: *\nAllow: /\n"


# --- fixtures ------------------------------------------------------------


@pytest.fixture
def clock():
    """A hand-cranked clock, so rate limiting is exercised without waiting."""

    state = {"now": 1000.0}

    def read() -> float:
        return state["now"]

    def sleep(seconds: float) -> None:
        state["now"] += seconds

    read.advance = lambda seconds: state.__setitem__("now", state["now"] + seconds)
    read.sleep = sleep
    return read


@pytest.fixture
def transport():
    recorded = RecordedTransport()
    recorded.record("https://pypi.org/robots.txt", ALLOW_ALL)
    recorded.record("https://registry.npmjs.org/robots.txt", ALLOW_ALL)
    recorded.record("https://api.github.com/robots.txt", ALLOW_ALL)
    return recorded


@pytest.fixture
def fetcher(transport, clock):
    return Fetcher(
        official_policy(interval=1.0),
        transport=transport,
        clock=clock,
        sleep=clock.sleep,
    )


RETRY_JSON = {
    "info": {
        "name": "retry",
        "version": "0.9.2",
        "summary": "easy to use retry decorator with exponential backoff",
        "classifiers": [
            "License :: OSI Approved :: Apache Software License",
            "Topic :: Software Development :: Libraries",
        ],
        "requires_python": ">=3.7",
        "keywords": "retry backoff",
        "requires_dist": ["decorator>=3.4.2", "pytest; extra == 'dev'"],
        "project_urls": {"Source": "https://github.com/invl/retry"},
    },
    "urls": [{"upload_time_iso_8601": "2026-05-01T00:00:00Z", "yanked": False}],
}


# --- policy --------------------------------------------------------------


def test_a_crawler_must_identify_itself():
    with pytest.raises(PolicyViolation, match="identify itself"):
        CrawlPolicy(user_agent="   ")


def test_an_empty_allow_list_means_no_host_restriction_not_no_hosts():
    policy = CrawlPolicy()
    assert policy.permits_host("pypi.org")
    assert policy.permits_host("example.invalid")


def test_the_block_list_wins_over_the_allow_list():
    policy = CrawlPolicy(
        allowed_hosts=frozenset({"pypi.org"}), blocked_hosts=frozenset({"pypi.org"})
    )
    assert not policy.permits_host("pypi.org")


def test_the_official_policy_admits_registries_and_refuses_everything_else():
    policy = official_policy()
    assert policy.permits_host("registry.npmjs.org")
    assert not policy.permits_host("blog.example.com")


def test_a_retry_after_header_beats_our_own_arithmetic():
    policy = CrawlPolicy(interval=1.0)
    assert policy.backoff_for(1, retry_after=17.0) == 17.0
    assert policy.backoff_for(3) == 4.0


def test_a_hostile_retry_after_cannot_stall_the_crawl_indefinitely():
    policy = CrawlPolicy(interval=1.0)
    assert policy.backoff_for(1, retry_after=99_999.0) == 60.0


# --- transport -----------------------------------------------------------


def test_an_unrecorded_url_returns_404_rather_than_raising():
    response = RecordedTransport().get("https://pypi.org/nothing", {}, 5.0)
    assert response.status == 404
    assert not response.ok


def test_headers_are_read_case_insensitively():
    response = Response(url="u", status=200, headers={"ETag": "W/abc"})
    assert response.etag == "W/abc"
    assert response.header("etag") == "W/abc"


def test_a_response_that_is_not_json_names_the_url_it_came_from():
    response = Response(url="https://pypi.org/x", status=200, body=b"<html>oops</html>")
    with pytest.raises(FetchError, match="https://pypi.org/x did not return JSON"):
        response.json()


def test_the_recorded_transport_answers_conditional_requests_like_a_real_server():
    recorded = RecordedTransport()
    recorded.record("https://pypi.org/x", {"a": 1}, headers={"ETag": "v1"})
    fresh = recorded.get("https://pypi.org/x", {}, 5.0)
    assert fresh.status == 200
    revalidated = recorded.get("https://pypi.org/x", {"If-None-Match": "v1"}, 5.0)
    assert revalidated.status == 304


# --- rate limiting -------------------------------------------------------


def test_the_first_request_to_a_host_does_not_wait(clock):
    limiter = RateLimiter(1.0, clock=clock, sleep=clock.sleep)
    assert limiter.wait("pypi.org") == 0.0


def test_a_second_request_to_the_same_host_waits_the_interval(clock):
    limiter = RateLimiter(2.0, clock=clock, sleep=clock.sleep)
    limiter.wait("pypi.org")
    assert limiter.wait("pypi.org") == pytest.approx(2.0)


def test_hosts_are_rate_limited_independently(clock):
    limiter = RateLimiter(2.0, clock=clock, sleep=clock.sleep)
    limiter.wait("pypi.org")
    assert limiter.wait("registry.npmjs.org") == 0.0


def test_a_host_asking_for_a_longer_delay_gets_it(clock):
    limiter = RateLimiter(1.0, clock=clock, sleep=clock.sleep)
    limiter.wait("pypi.org")
    assert limiter.wait("pypi.org", minimum=10.0) == pytest.approx(10.0)


# --- robots --------------------------------------------------------------


def test_a_disallowed_path_is_refused_with_the_reason(transport):
    transport.record("https://pypi.org/robots.txt", "User-agent: *\nDisallow: /private\n")
    gate = RobotsGate(transport, official_policy())
    decision = gate.check("https://pypi.org/private/thing")
    assert not decision
    assert "disallows /private/thing" in decision.reason


def test_robots_is_fetched_once_per_origin_however_many_urls_are_checked(transport):
    gate = RobotsGate(transport, official_policy())
    for index in range(10):
        gate.check(f"https://pypi.org/pypi/{index}/json")
    assert gate.fetches == 1


def test_an_unreachable_robots_file_allows_but_says_so(transport):
    gate = RobotsGate(transport, official_policy())
    decision = gate.check("https://crates.io/api/v1/crates/serde")
    assert decision.allowed
    assert "could not be read" in decision.reason


def test_a_403_on_robots_itself_disallows_the_whole_origin(transport):
    transport.record("https://pypi.org/robots.txt", b"", status=403)
    gate = RobotsGate(transport, official_policy())
    assert not gate.check("https://pypi.org/pypi/retry/json")
    assert "https://pypi.org" in gate.status()["refused_origins"]


def test_a_crawl_delay_is_honoured(transport):
    transport.record(
        "https://pypi.org/robots.txt", "User-agent: *\nAllow: /\nCrawl-delay: 9\n"
    )
    gate = RobotsGate(transport, official_policy())
    assert gate.delay_for("https://pypi.org/pypi/retry/json") == 9.0


# --- cache ---------------------------------------------------------------


def test_a_fresh_entry_is_served_without_touching_the_transport(fetcher, transport):
    transport.record("https://pypi.org/pypi/retry/json", RETRY_JSON)
    fetcher.get("https://pypi.org/pypi/retry/json")
    before = transport.call_count
    again = fetcher.get("https://pypi.org/pypi/retry/json")
    assert again.from_cache
    assert transport.call_count == before


def test_errors_are_never_cached():
    cache = ResponseCache()
    cache.store(Response(url="https://pypi.org/x", status=500, body=b"down"))
    assert len(cache) == 0


def test_a_stale_entry_is_revalidated_rather_than_discarded(fetcher, transport):
    transport.record(
        "https://pypi.org/pypi/retry/json", RETRY_JSON, headers={"ETag": "v1"}
    )
    first = fetcher.get("https://pypi.org/pypi/retry/json")
    fetcher.cache.ttl = 0.0  # everything is now stale

    second = fetcher.get("https://pypi.org/pypi/retry/json")
    assert second.body == first.body
    assert fetcher.report.revalidated == 1
    assert second.from_cache


def test_a_durable_cache_survives_a_new_process(tmp_path):
    first = ResponseCache(tmp_path)
    first.store(Response(url="https://pypi.org/x", status=200, body=b'{"a": 1}'))

    second = ResponseCache(tmp_path)
    assert len(second) == 1
    assert second.fresh("https://pypi.org/x").json() == {"a": 1}


def test_a_corrupt_cache_file_is_dropped_rather_than_failing_to_start(tmp_path):
    (tmp_path / "deadbeef.json").write_text("{not json", encoding="utf-8")
    cache = ResponseCache(tmp_path)
    assert len(cache) == 0


def test_an_absence_is_remembered_briefly_so_name_guessing_stops_re_asking():
    cache = ResponseCache(negative_ttl=300.0)
    cache.note_absent("https://pypi.org/pypi/pyretry/json", now=100.0)
    assert cache.absent("https://pypi.org/pypi/pyretry/json", now=200.0)


def test_an_absence_expires_so_a_newly_published_package_becomes_visible():
    cache = ResponseCache(negative_ttl=300.0)
    cache.note_absent("https://pypi.org/pypi/pyretry/json", now=100.0)
    assert not cache.absent("https://pypi.org/pypi/pyretry/json", now=500.0)


def test_absences_are_not_persisted_so_a_new_process_believes_nothing_is_missing(
    tmp_path,
):
    first = ResponseCache(tmp_path)
    first.note_absent("https://pypi.org/pypi/pyretry/json", now=100.0)
    assert not ResponseCache(tmp_path).absent(
        "https://pypi.org/pypi/pyretry/json", now=101.0
    )


def test_a_remembered_absence_is_refused_without_a_network_request(clock):
    class CountingMisses:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, url, headers, timeout):
            if url.endswith("robots.txt"):
                return Response(url=url, status=200, body=ALLOW_ALL.encode())
            self.calls += 1
            return Response(url=url, status=404)

    counting = CountingMisses()
    fetcher = Fetcher(
        official_policy(interval=0.0),
        transport=counting,
        clock=clock,
        sleep=clock.sleep,
    )
    for _ in range(5):
        assert fetcher.try_get("https://pypi.org/pypi/nope/json") is None
    assert counting.calls == 1
    assert fetcher.report.known_absent == 4


def test_a_403_is_not_remembered_as_an_absence(clock):
    class Forbidden:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, url, headers, timeout):
            if url.endswith("robots.txt"):
                return Response(url=url, status=200, body=ALLOW_ALL.encode())
            self.calls += 1
            return Response(url=url, status=403)

    forbidden = Forbidden()
    fetcher = Fetcher(
        official_policy(interval=0.0),
        transport=forbidden,
        clock=clock,
        sleep=clock.sleep,
    )
    fetcher.try_get("https://pypi.org/pypi/private/json")
    fetcher.try_get("https://pypi.org/pypi/private/json")
    assert forbidden.calls == 2


def test_eviction_removes_the_oldest_entry_first():
    cache = ResponseCache(max_entries=2)
    for index, moment in enumerate([10.0, 20.0, 30.0]):
        cache.store(
            Response(url=f"https://pypi.org/{index}", status=200, body=b"{}"), now=moment
        )
    assert len(cache) == 2
    assert cache.get("https://pypi.org/0") is None


# --- fetcher -------------------------------------------------------------


def test_a_host_outside_the_policy_is_refused_before_any_request(fetcher, transport):
    before = transport.call_count
    with pytest.raises(PolicyViolation, match="not permitted"):
        fetcher.get("https://evil.example.com/payload")
    assert transport.call_count == before


def test_a_retryable_status_is_retried_and_then_succeeds(transport, clock):
    class Flaky:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, url, headers, timeout):
            if url.endswith("robots.txt"):
                return Response(url=url, status=200, body=ALLOW_ALL.encode())
            self.calls += 1
            if self.calls < 3:
                return Response(url=url, status=503, headers={"Retry-After": "2"})
            return Response(url=url, status=200, body=b'{"ok": true}')

    flaky = Flaky()
    fetcher = Fetcher(
        official_policy(interval=0.0), transport=flaky, clock=clock, sleep=clock.sleep
    )
    assert fetcher.get("https://pypi.org/x").json() == {"ok": True}
    assert fetcher.report.retries == 2


def test_retries_are_bounded_and_then_the_fetch_fails_loudly(clock):
    class AlwaysDown:
        def get(self, url, headers, timeout):
            if url.endswith("robots.txt"):
                return Response(url=url, status=200, body=ALLOW_ALL.encode())
            return Response(url=url, status=503)

    fetcher = Fetcher(
        official_policy(interval=0.0, retries=2),
        transport=AlwaysDown(),
        clock=clock,
        sleep=clock.sleep,
    )
    with pytest.raises(FetchError, match="503 after 2 retries"):
        fetcher.get("https://pypi.org/x")


def test_a_404_is_not_retried(clock):
    class Missing:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, url, headers, timeout):
            if url.endswith("robots.txt"):
                return Response(url=url, status=200, body=ALLOW_ALL.encode())
            self.calls += 1
            return Response(url=url, status=404)

    missing = Missing()
    fetcher = Fetcher(
        official_policy(interval=0.0), transport=missing, clock=clock, sleep=clock.sleep
    )
    with pytest.raises(FetchError):
        fetcher.get("https://pypi.org/x")
    assert missing.calls == 1


def test_try_get_returns_none_rather_than_aborting_a_crawl(fetcher):
    assert fetcher.try_get("https://pypi.org/absent") is None


def test_the_report_separates_network_requests_from_cache_hits(fetcher, transport):
    transport.record("https://pypi.org/pypi/retry/json", RETRY_JSON)
    fetcher.get("https://pypi.org/pypi/retry/json")
    fetcher.get("https://pypi.org/pypi/retry/json")
    assert fetcher.report.requests == 2
    assert fetcher.report.network_requests == 1


# --- PyPI ----------------------------------------------------------------


def test_pypi_normalises_a_distribution_into_an_artifact(fetcher, transport):
    transport.record("https://pypi.org/pypi/retry/json", RETRY_JSON)
    artifact = PyPISource(fetcher).describe("retry")
    assert artifact is not None
    assert artifact.purl == "pkg:pypi/retry@0.9.2"
    assert artifact.licence == "Apache-2.0"
    assert artifact.requires_runtime == ">=3.7"
    assert artifact.repository == "https://github.com/invl/retry"
    assert "backoff" in artifact.keywords


def test_a_missing_distribution_is_none_rather_than_an_error(fetcher):
    assert PyPISource(fetcher).describe("definitely-not-a-package") is None


def test_an_unmapped_licence_classifier_yields_no_licence_rather_than_a_guess(
    fetcher, transport
):
    payload = json.loads(json.dumps(RETRY_JSON))
    payload["info"]["classifiers"] = ["License :: Free For Home Use"]
    transport.record("https://pypi.org/pypi/retry/json", payload)
    assert PyPISource(fetcher).describe("retry").licence == ""


def test_a_licence_field_holding_the_whole_licence_text_is_not_treated_as_spdx(
    fetcher, transport
):
    payload = json.loads(json.dumps(RETRY_JSON))
    payload["info"]["classifiers"] = []
    payload["info"]["license"] = "Permission is hereby granted, free of charge, " * 20
    transport.record("https://pypi.org/pypi/retry/json", payload)
    assert PyPISource(fetcher).describe("retry").licence == ""


def test_pypi_name_guessing_is_inspectable(fetcher):
    names = PyPISource(fetcher).candidate_names(["retry"])
    assert names[0] == "retry"
    assert "python-retry" in names


def test_a_development_status_of_inactive_marks_the_package_deprecated(
    fetcher, transport
):
    payload = json.loads(json.dumps(RETRY_JSON))
    payload["info"]["classifiers"].append("Development Status :: 7 - Inactive")
    transport.record("https://pypi.org/pypi/retry/json", payload)
    assert PyPISource(fetcher).describe("retry").deprecated


# --- npm -----------------------------------------------------------------


def test_npm_search_normalises_results_and_keeps_the_registry_scores(
    fetcher, transport
):
    transport.record(
        "https://registry.npmjs.org/-/v1/search?text=retry&size=5",
        {
            "objects": [
                {
                    "package": {
                        "name": "p-retry",
                        "version": "6.2.0",
                        "description": "Retry a promise-returning function",
                        "keywords": ["Retry", "backoff"],
                        "license": "MIT",
                        "date": "2026-04-02T00:00:00.000Z",
                        "links": {"repository": "https://github.com/sindresorhus/p-retry"},
                    },
                    "score": {"final": 0.71, "detail": {"quality": 0.9}},
                }
            ]
        },
    )
    found = NpmSource(fetcher).search(["retry"], limit=5)
    assert len(found) == 1
    assert found[0].purl == "pkg:npm/p-retry@6.2.0"
    assert found[0].keywords == ("retry", "backoff")
    assert found[0].raw["search_score"] == 0.71


def test_a_legacy_npm_licence_object_is_read_as_an_expression(fetcher, transport):
    transport.record(
        "https://registry.npmjs.org/old",
        {
            "name": "old",
            "dist-tags": {"latest": "1.0.0"},
            "versions": {"1.0.0": {"license": {"type": "ISC"}, "description": "old"}},
            "time": {"1.0.0": "2019-01-01T00:00:00.000Z"},
        },
    )
    assert NpmSource(fetcher).describe("old").licence == "ISC"


def test_a_scoped_npm_package_keeps_its_scope_in_the_purl(fetcher, transport):
    transport.record(
        "https://registry.npmjs.org/@scope/thing",
        {
            "name": "@scope/thing",
            "dist-tags": {"latest": "2.0.0"},
            "versions": {
                "2.0.0": {
                    "description": "scoped",
                    "license": "MIT",
                    "engines": {"node": ">=20"},
                    "dependencies": {"a": "^1", "b": "^2"},
                }
            },
            "time": {"2.0.0": "2026-01-01T00:00:00.000Z"},
        },
    )
    artifact = NpmSource(fetcher).describe("@scope/thing")
    assert artifact.purl == "pkg:npm/%40scope/thing@2.0.0"
    assert artifact.requires_runtime == "node>=20"
    assert artifact.dependencies == ("a", "b")


# --- GitHub --------------------------------------------------------------


@pytest.mark.parametrize(
    "reference",
    [
        "https://github.com/invl/retry",
        "git+https://github.com/invl/retry.git",
        "git://github.com/invl/retry.git",
        "git@github.com:invl/retry.git",
        "https://github.com/invl/retry/tree/master",
        "invl/retry",
    ],
)
def test_every_shape_a_repository_link_takes_reduces_to_one_slug(reference):
    assert GitHubSource.slug(reference) == "invl/retry"


def test_something_that_is_not_a_repository_link_yields_no_slug():
    assert GitHubSource.slug("https://example.com/docs") == ""


def test_github_reports_archived_repositories_as_deprecated(fetcher, transport):
    transport.record(
        "https://api.github.com/repos/old/thing",
        {
            "full_name": "old/thing",
            "description": "abandoned",
            "stargazers_count": 12,
            "archived": True,
            "html_url": "https://github.com/old/thing",
            "pushed_at": "2018-01-01T00:00:00Z",
            "license": {"spdx_id": "MIT"},
        },
    )
    artifact = GitHubSource(fetcher, environ={}).describe("old/thing")
    assert artifact.deprecated
    assert artifact.stars == 12


def test_a_noassertion_spdx_id_is_read_as_no_licence(fetcher, transport):
    transport.record(
        "https://api.github.com/repos/x/y",
        {
            "full_name": "x/y",
            "license": {"spdx_id": "NOASSERTION"},
            "html_url": "https://github.com/x/y",
        },
    )
    assert GitHubSource(fetcher, environ={}).describe("x/y").licence == ""


def test_a_token_is_read_from_the_environment_when_not_passed(fetcher):
    source = GitHubSource(fetcher, environ={"GITHUB_TOKEN": "ghp_x"})
    assert source.authenticated
    assert GitHubSource(fetcher, environ={}).authenticated is False


def test_rate_limit_headers_are_recorded(fetcher, transport):
    transport.record(
        "https://api.github.com/repos/x/y",
        Response(
            url="https://api.github.com/repos/x/y",
            status=200,
            body=json.dumps({"full_name": "x/y", "html_url": "u"}).encode(),
            headers={"X-RateLimit-Remaining": "4987"},
        ),
    )
    source = GitHubSource(fetcher, environ={})
    source.describe("x/y")
    assert source.rate_limit_remaining == 4987


# --- artifacts -----------------------------------------------------------


def test_an_artifact_must_be_named_and_placed():
    with pytest.raises(SourceError, match="must be named"):
        Artifact(ecosystem="pypi", name="")
    with pytest.raises(SourceError, match="no ecosystem"):
        Artifact(ecosystem="", name="retry")


def test_merging_prefers_a_stated_value_over_an_absent_one_in_either_order():
    versioned = Artifact(ecosystem="pypi", name="retry", version="1.0")
    starred = Artifact(ecosystem="pypi", name="retry", stars=900)
    for merged in (versioned.merge(starred), starred.merge(versioned)):
        assert merged.version == "1.0"
        assert merged.stars == 900


def test_merging_two_different_artifacts_is_refused():
    with pytest.raises(SourceError, match="different artifacts"):
        Artifact(ecosystem="pypi", name="a").merge(Artifact(ecosystem="pypi", name="b"))


def test_an_unreported_count_is_not_the_same_as_zero():
    assert Artifact(ecosystem="pypi", name="quiet").stars is None
    assert Artifact(ecosystem="pypi", name="ignored", stars=0).stars == 0


# --- the crawl -----------------------------------------------------------


@pytest.fixture
def crawl_world(fetcher, transport):
    transport.record("https://pypi.org/pypi/retry/json", RETRY_JSON)
    transport.record(
        "https://api.github.com/repos/invl/retry",
        {
            "full_name": "invl/retry",
            "description": "retry decorator",
            "stargazers_count": 5000,
            "license": {"spdx_id": "Apache-2.0"},
            "topics": ["retry"],
            "html_url": "https://github.com/invl/retry",
            "pushed_at": "2026-06-01T00:00:00Z",
            "archived": False,
        },
    )
    engine = Engine(FactBase(), reuse_rules())
    registry = SourceRegistry(PyPISource(fetcher), GitHubSource(fetcher, environ={}))
    return Crawler(registry, engine=engine, fetcher=fetcher), engine


@pytest.fixture
def retry_need():
    return need_from_text(
        "I need to retry failed HTTP requests with exponential backoff",
        constraints=[Constraint(ConstraintKind.ECOSYSTEM, "pypi")],
    )


def test_search_terms_include_synonyms_because_registries_index_the_authors_words(
    crawl_world, retry_need
):
    crawler, _ = crawl_world
    terms = crawler.terms_for(retry_need)
    assert "retry-with-backoff" in terms
    assert "exponential backoff" in terms


def test_an_ecosystem_constraint_keeps_the_crawl_off_irrelevant_registries(
    fetcher, retry_need
):
    crawler = Crawler(SourceRegistry(PyPISource(fetcher), NpmSource(fetcher)))
    chosen = {source.name for source in crawler.sources_for(retry_need)}
    assert chosen == {"pypi"}


def test_a_repository_host_is_consulted_under_any_ecosystem(fetcher, retry_need):
    crawler = Crawler(
        SourceRegistry(PyPISource(fetcher), GitHubSource(fetcher, environ={}))
    )
    chosen = {source.name for source in crawler.sources_for(retry_need)}
    assert chosen == {"pypi", "github"}


def test_an_ecosystem_nobody_serves_becomes_a_named_gap(fetcher):
    need = need_from_text(
        "retry with backoff", constraints=[Constraint(ConstraintKind.ECOSYSTEM, "cargo")]
    )
    result = Crawler(SourceRegistry(PyPISource(fetcher))).discover(need)
    assert any("cargo" in gap for gap in result.gaps)


def test_a_crawl_finds_the_package_and_folds_in_its_repository_upkeep(
    crawl_world, retry_need
):
    crawler, _ = crawl_world
    result = crawler.discover(retry_need)
    assert result.found == 1
    artifact = result.artifacts[0]
    assert artifact.purl == "pkg:pypi/retry@0.9.2"
    assert artifact.stars == 5000  # from the forge, not the registry


def test_every_emitted_fact_cites_where_it_was_read(crawl_world, retry_need):
    crawler, _ = crawl_world
    result = crawler.discover(retry_need)
    package_facts = [f for f in result.facts if f.subject.startswith("pkg:")]
    assert package_facts
    assert all(f.provenance is Provenance.OBSERVED for f in package_facts)
    assert all(f.source.startswith("https://") for f in package_facts)


def test_the_crawler_never_claims_a_package_provides_what_the_need_asks(
    crawl_world, retry_need
):
    crawler, _ = crawl_world
    result = crawler.discover(retry_need)
    emitted = {fact.predicate for fact in result.facts}
    assert "named-like" in emitted
    assert "provides" not in emitted


def test_a_naming_hint_only_reaches_the_heuristic_confidence(crawl_world, retry_need):
    crawler, engine = crawl_world
    crawler.discover(retry_need)
    provides = [
        fact for fact in engine.facts
        if fact.predicate == "provides" and fact.subject.startswith("pkg:")
    ]
    assert provides
    assert max(f.confidence for f in provides) < 0.40


def test_only_the_lattice_the_need_touches_is_loaded(crawl_world, retry_need):
    crawler, _ = crawl_world
    subjects = {fact.subject for fact in crawler.lattice_facts(retry_need)}
    assert "retry-with-backoff" in subjects
    assert "sql-database" not in subjects


def test_the_need_itself_enters_the_fact_base_so_rules_can_join_against_it(
    crawl_world, retry_need
):
    crawler, _ = crawl_world
    facts = crawler.need_facts(retry_need)
    assert {f.subject for f in facts} == {retry_need.urn()}
    assert {f.predicate for f in facts} == {"requires"}


def test_a_source_that_cannot_be_reached_becomes_a_gap_not_a_crash(fetcher, retry_need):
    class Broken:
        name = "broken"
        ecosystem = "pypi"

        def search(self, terms, *, limit=20):
            raise FetchError("registry is down")

        def describe(self, name):
            return None

    result = Crawler(SourceRegistry(Broken())).discover(retry_need)
    assert result.found == 0
    assert any("registry is down" in gap for gap in result.gaps)


def test_a_package_with_no_licence_metadata_is_reported_as_a_gap(
    fetcher, transport, retry_need
):
    payload = json.loads(json.dumps(RETRY_JSON))
    payload["info"]["classifiers"] = []
    payload["info"]["license"] = ""
    transport.record("https://pypi.org/pypi/retry/json", payload)
    result = Crawler(SourceRegistry(PyPISource(fetcher))).discover(retry_need)
    assert any("publishes no licence" in gap for gap in result.gaps)


def test_a_second_identical_crawl_makes_no_further_network_requests(
    crawl_world, retry_need
):
    crawler, _ = crawl_world
    crawler.discover(retry_need)
    before = crawler.fetcher.report.network_requests
    crawler.discover(retry_need)
    assert crawler.fetcher.report.network_requests == before


def test_the_forward_chainer_reaches_conclusions_from_what_was_crawled(
    crawl_world, retry_need
):
    crawler, engine = crawl_world
    result = crawler.discover(retry_need)
    assert result.propagated > 0
    assert engine.holds("pkg:pypi/retry@0.9.2", "has-licence", "Apache-2.0")
    assert engine.holds("pkg:pypi/retry@0.9.2", "viable", "true")
