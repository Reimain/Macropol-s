"""Framework route tables as a second source of evidence.

The manifest declares `network:` elements and every one arrives as `DECLARED`
at 0.92 — authoritative about intent and nothing else. A route in the code is
the independent observation that turns a declaration into a corroborated fact,
and its absence is what makes `DECLARED_NOT_FOUND` findable at all.
"""

from __future__ import annotations

import pytest

from slpie_enterprise.frameworks.routes import routes_in, scan

SOURCE = '''
from fastapi import APIRouter
router = APIRouter()

@router.get("/orders")
async def list_orders(): ...

@app.post("/orders/{id}/pay")
def pay(id: str): ...

@app.route("/legacy", methods=["PUT", "DELETE"])
def legacy(): ...

urlpatterns = [
    path("admin/", admin.site.urls),
    Route("/health", healthcheck, methods=["GET"]),
]
'''


def test_four_frameworks_one_walk():
    found, errors = routes_in(SOURCE, filename="app.py")
    assert not errors

    seen = {f"{route.method} {route.path}" for route in found}
    assert "GET /orders" in seen                    # FastAPI decorator
    assert "POST /orders/{id}/pay" in seen          # FastAPI, path parameter
    assert "PUT /legacy" in seen and "DELETE /legacy" in seen   # Flask keyword
    assert "GET /health" in seen                    # Starlette Route(...)
    assert "ANY admin/" in seen                     # Django path(...)


def test_django_is_recorded_as_any_rather_than_guessed():
    """`path()` states no method — the view decides. A scanner that guessed
    `GET` would assert something the source does not say."""
    found, _errors = routes_in(SOURCE)
    django = [route for route in found if route.framework == "django"]
    assert django and all(route.method == "ANY" for route in django)


def test_a_route_carries_the_line_it_was_read_from():
    """Evidence is only checkable if it points somewhere."""
    found, _errors = routes_in(SOURCE)
    for route in found:
        assert route.line > 0
        assert SOURCE.splitlines()[route.line - 1].strip()


def test_the_order_is_total_so_two_scans_give_one_graph():
    first, _ = routes_in(SOURCE)
    second, _ = routes_in(SOURCE)
    assert [str(route) for route in first] == [str(route) for route in second]


def test_an_unparseable_file_is_a_reported_gap_rather_than_a_crash():
    """One bad file in a repository of four hundred must not end the scan."""
    found, errors = routes_in("def broken(:\n", filename="bad.py")
    assert found == ()
    assert len(errors) == 1
    assert "bad.py" in errors[0]


def test_nothing_is_imported_to_find_a_route():
    """Importing a customer's application executes their module-level code —
    database connections, secret reads, background threads — inside a scan.

    Asserted by giving the walk a module whose import would raise, and which is
    never imported.
    """
    hostile = '''
raise SystemExit("this module must never be imported by a scanner")

@app.get("/never-run")
def handler(): ...
'''
    found, errors = routes_in(hostile, filename="hostile.py")
    assert not errors
    assert [str(route) for route in found] == ["GET /never-run"]


def test_the_discoverer_cites_an_annotation_rather_than_an_import():
    """A decorator is a declaration in code — strong, and weaker than an import
    the interpreter must resolve, because a decorated function can live on a
    router nobody mounts. §10's ladder already has the right rung."""
    from slpie.discovery.base import Source
    from slpie.domain.evidence import EvidenceKind
    from slpie_enterprise.frameworks.routes import discover_routes

    answer = discover_routes(Source(uri="file:///r/app.py", text=SOURCE))
    assert answer.observations
    for observation in answer.observations:
        assert observation.evidence.kind is EvidenceKind.ANNOTATION
        assert observation.evidence.location.line > 0
        assert observation.properties["method"]
        assert observation.properties["path"]


def test_a_tree_scan_reports_what_it_could_not_read(tmp_path):
    (tmp_path / "good.py").write_text(SOURCE, encoding="utf-8")
    (tmp_path / "bad.py").write_text("def broken(:\n", encoding="utf-8")

    found, errors = scan(tmp_path)
    assert found, "the good file produced nothing"
    assert len(errors) == 1 and "bad.py" in errors[0]
