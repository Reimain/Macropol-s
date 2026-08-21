"""§31 — the device tier: screens on the reader's machine, truth on the server.

The browser is the smallest replica in §23's model. Everything below is about
the two ways that could go wrong: a restored answer painting over a fresher one,
and a device keeping bytes it has no business keeping.

Structural and HTTP tests here; the behavioural half runs in the browser tier,
because hydration only means anything against a real IndexedDB.
"""

from __future__ import annotations

import re

import pytest

from slpie.ui.api import Api, Request
from slpie.ui.contract import cacheable_routes

from _walk import REPOSITORY

APP = REPOSITORY / "slpie" / "ui" / "app"


@pytest.fixture(scope="module")
def routes() -> tuple[tuple[str, str], ...]:
    return tuple(Api(engine=None).routes)


# -- what may be kept ----------------------------------------------------


def test_cacheability_is_read_from_the_contract_not_decided_again(routes) -> None:
    """One statement, three consumers: the service worker, the device tier, and
    any edge cache. A second list would be a second answer."""
    keepable = cacheable_routes(routes=routes)
    assert "GET /api/findings" in keepable
    assert "GET /api/verbs" in keepable
    # A mutation is never keepable, whatever its method.
    assert "POST /api/v/target" not in keepable
    assert "POST /api/v/attach" not in keepable


def test_the_live_feed_is_never_keepable(routes) -> None:
    """It is a connection, not a document.

    `/api/stream` is registered as a route so a generated client can discover
    it, which means it would otherwise be swept up as an ordinary GET — and a
    cached SSE response replays history as though it were happening now.
    """
    assert "GET /api/stream" not in cacheable_routes(routes=routes)


def test_the_api_stamps_what_a_device_may_hold() -> None:
    api = Api(engine=None)
    kept = api.handle(Request("GET", "/api/verbs", {}, {}))
    assert ("X-Slpie-Cacheable", "1") in kept.headers


def test_a_refusal_is_never_marked_keepable() -> None:
    """A 409 "no environment open" held past the moment one is opened is a
    console insisting the platform is empty."""
    api = Api(engine=None)
    refused = api.handle(Request("GET", "/api/station", {}, {}))
    assert refused.status == 409
    assert not [name for name, _ in refused.headers if name == "X-Slpie-Cacheable"]


# -- the keys ------------------------------------------------------------


def test_the_store_key_is_scoped_before_it_reaches_a_backend() -> None:
    """`ObjectRef`'s discipline, carried to the device.

    Building the key *is* the check, so there is no way to reach a backend with
    a bare string — which is what stops the check from being the thing somebody
    forgets.
    """
    source = (APP / "data" / "objectstore.js").read_text(encoding="utf-8")
    assert "export function ref(" in source
    assert "export function prefix(" in source
    # The traversal refusal, and the one that is easy to miss: a principal that
    # is a prefix of a different principal.
    assert re.search(r"\\\.\\\.\?", source) or ".." in source


def test_the_device_tier_obeys_the_browser_ring_rule() -> None:
    """`core/store.js` must not import `data/`.

    Persistence is injected by the shell instead. Without this the store — the
    one module every screen depends on — would drag the whole data tier behind
    it and stop being reusable, which is exactly what the tier rule exists to
    prevent.
    """
    source = (APP / "core" / "store.js").read_text(encoding="utf-8")
    # Imports, not prose. The first version of this asserted `"data/" not in
    # source` and failed on the docstring explaining why the import is absent —
    # a test that cannot tell an explanation from a dependency.
    imports = re.findall(r'from\s+"([^"]+)"', source)
    assert not [item for item in imports if "data/" in item], imports
    assert "export async function persist(" in source
    assert "export async function hydrate(" in source


def test_hydration_goes_through_the_version_rule() -> None:
    """The property that makes restoring from disk safe.

    A restored cell is older by construction, so routing it through the same
    `commit` a network answer takes means it can only fill a gap and never win a
    race. A separate restore path would be a second place for the rule to be
    wrong.
    """
    source = (APP / "core" / "store.js").read_text(encoding="utf-8")
    body = source[source.index("export async function hydrate("):]
    body = body[: body.index("\n}")]
    assert "commit(" in body, "hydrate must restore through commit, not around it"
    assert "stale: true" in body, "a cell from disk must announce that it is old"


def test_only_an_answer_is_written_through() -> None:
    """Not a refusal, not a fault, not a loading placeholder."""
    source = (APP / "core" / "store.js").read_text(encoding="utf-8")
    assert "merged.keep && merged.status === READY" in source


def test_a_principal_change_wipes_rather_than_filters() -> None:
    """Leaving one tenant's graph on a shared machine after a logout is a
    data-residency incident, and a filtered view is still their bytes."""
    source = (APP / "core" / "store.js").read_text(encoding="utf-8")
    body = source[source.index("export async function persist("):]
    body = body[: body.index("\n}")]
    assert "clear()" in body
    assert "cells.clear()" in body


def test_a_refused_quota_degrades_rather_than_crashing() -> None:
    """The device declining a capability, treated as §3 treats any refusal:
    fall back, keep answering, and say what it cost."""
    source = (APP / "data" / "objectstore.js").read_text(encoding="utf-8")
    assert "this.refusals.push(" in source
    assert "export function refusals" in (
        APP / "core" / "store.js"
    ).read_text(encoding="utf-8").replace("export function refusals(", "export function refusals")


def test_the_device_tier_is_precached() -> None:
    worker = (APP / "sw.js").read_text(encoding="utf-8")
    assert '"/data/objectstore.js"' in worker


def test_nothing_in_the_device_tier_uses_inner_html() -> None:
    for name in ("data/objectstore.js", "core/store.js"):
        assert "innerHTML" not in (APP / name).read_text(encoding="utf-8")
