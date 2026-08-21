"""HTTP routes, read out of source with `ast`.

Four frameworks, one walk. They differ in spelling and not in shape: every one
of them names a method, a path, and a handler, and every one of them does it in
a call expression the AST can see without running anything.

    FastAPI / Flask   @app.get("/orders")            decorator, method in the name
    FastAPI router    @router.post("/orders/{id}")   same, on a router
    Django            path("orders/", views.list)    call, method not stated
    Starlette         Route("/orders", endpoint)     call, methods= keyword

**Read, never imported.** Importing a customer's application to inspect its
router executes their module-level code — database connections, secret reads,
background threads — inside a scan. That is not a scanner, it is a deployment.
`ast.parse` sees the decorators and the calls without any of it.

The honest cost of that choice, stated rather than discovered later: a route
registered in a loop, or behind a `settings.FEATURE` flag, or by an
`include_router` this walk did not follow, is **not found**. That is a gap, and
`discover_routes` reports the count rather than implying the list is complete.
A dynamic-import discoverer would find those and would also run the code, and
between "incomplete and says so" and "complete and dangerous" the first is the
only defensible one.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

from slpie.discovery.base import Source, declares, evidence_at, result
from slpie.domain.evidence import EvidenceKind
from slpie.domain.identity import Urn
from slpie.plugins.protocol import DiscoveryResult

EXTRACTOR = "slpie_enterprise.frameworks"

#: Decorator names that carry their method. `websocket` is deliberately absent:
#: it is not an HTTP method and recording it as one would put a socket in the
#: graph wearing an endpoint's clothes.
METHODS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")

#: Call expressions that register a route without naming a method.
REGISTRARS = ("path", "re_path", "url", "Route", "add_api_route", "add_url_rule")


@dataclass(frozen=True, slots=True)
class Route:
    """One route, as the source states it."""

    method: str
    path: str
    handler: str
    line: int
    framework: str

    @property
    def urn(self) -> Urn:
        return Urn.create("api", f"{self.method}/{self.path}")

    def __str__(self) -> str:
        return f"{self.method} {self.path}"


def routes_in(text: str, *, filename: str = "<source>") -> tuple[tuple[Route, ...], tuple[str, ...]]:
    """Every route this file registers, and what could not be read.

    Errors are returned rather than raised. One unparseable file in a repository
    of four hundred must not end the scan, and a file that failed to parse is a
    *gap* — reported, counted against coverage — rather than a silence.
    """
    try:
        tree = ast.parse(text, filename=filename)
    except SyntaxError as error:
        return (), (f"{filename}: {error.msg} at line {error.lineno}",)

    found: list[Route] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.extend(_decorated(node))
        elif isinstance(node, ast.Call):
            found.extend(_registered(node))

    # Total order, so two scans of one repository produce one graph. Without
    # the line number the many routes sharing a method and a path — a real
    # thing when a router is mounted twice — would sort arbitrarily.
    found.sort(key=lambda route: (route.path, route.method, route.line))
    return tuple(found), ()


def _decorated(node: ast.FunctionDef | ast.AsyncFunctionDef) -> Iterator[Route]:
    """`@app.get("/orders")` and `@router.post(...)` — FastAPI, Flask, Starlette."""
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        attribute = decorator.func
        if not isinstance(attribute, ast.Attribute):
            continue
        method = attribute.attr.lower()
        if method not in METHODS:
            continue
        path = _first_string(decorator)
        if path is None:
            continue
        yield Route(
            method=method.upper(), path=path, handler=node.name,
            line=decorator.lineno,
            framework=_framework(attribute),
        )

        # Flask spells the method in a keyword rather than in the decorator
        # name: `@app.route("/x", methods=["POST"])`. Handled below.

    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        attribute = decorator.func
        if not isinstance(attribute, ast.Attribute) or attribute.attr != "route":
            continue
        path = _first_string(decorator)
        if path is None:
            continue
        for method in _methods_keyword(decorator) or ("GET",):
            yield Route(
                method=method, path=path, handler=node.name,
                line=decorator.lineno, framework="flask",
            )


def _registered(node: ast.Call) -> Iterator[Route]:
    """`path("orders/", view)` and `Route("/orders", endpoint, methods=[...])`."""
    name = _called(node)
    if name not in REGISTRARS:
        return
    path = _first_string(node)
    if path is None:
        return

    handler = ""
    if len(node.args) > 1:
        handler = _name_of(node.args[1])

    methods = _methods_keyword(node)
    if not methods:
        # Django's `path()` states no method: the view decides, and a scanner
        # that guessed `GET` would be asserting something the source does not
        # say. `ANY` is the honest record, and reconciliation can still match
        # it against a declared endpoint.
        methods = ("ANY",)

    framework = "django" if name in ("path", "re_path", "url") else "starlette"
    for method in methods:
        yield Route(
            method=method, path=path, handler=handler,
            line=node.lineno, framework=framework,
        )


def _framework(attribute: ast.Attribute) -> str:
    root = attribute.value
    name = root.id if isinstance(root, ast.Name) else ""
    return "fastapi" if name in ("app", "router", "api") else "unknown"


def _called(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _first_string(node: ast.Call) -> str | None:
    for argument in node.args:
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            return argument.value
    return None


def _methods_keyword(node: ast.Call) -> tuple[str, ...]:
    for keyword in node.keywords:
        if keyword.arg != "methods":
            continue
        if isinstance(keyword.value, (ast.List, ast.Tuple)):
            return tuple(
                item.value.upper()
                for item in keyword.value.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            )
    return ()


def _name_of(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name_of(node.value)}.{node.attr}".lstrip(".")
    return ""


def discover_routes(source: Source) -> DiscoveryResult:
    """The discoverer, in the shape every other one has.

    Evidence is `ANNOTATION` at 0.75 rather than `STATIC_IMPORT` at 0.90. A
    decorator is a *declaration in code* — strong, and weaker than an import
    the interpreter must resolve, because a decorated function can be
    registered on a router nobody mounts. §10's ladder already has the right
    rung for this and inventing a new one would break every comparison.
    """
    text = source.text
    found, errors = routes_in(text, filename=source.name)

    observations = [
        declares(
            str(route.urn),
            evidence_at(
                source.uri,
                kind=EvidenceKind.ANNOTATION,
                extractor=EXTRACTOR,
                line=route.line,
                excerpt=_line(text, route.line),
            ),
            method=route.method,
            path=route.path,
            handler=route.handler,
            framework=route.framework,
        )
        for route in found
    ]
    return result(observations, errors=errors)


def _line(text: str, number: int) -> str:
    lines = text.splitlines()
    return lines[number - 1].strip() if 0 < number <= len(lines) else ""


def scan(root: str | Path) -> tuple[tuple[Route, ...], tuple[str, ...]]:
    """Every route under a tree, with what could not be read.

    A convenience for the CLI and the tests. `discover_routes` is the seam the
    registry uses; this is the same walk with the paths resolved.
    """
    base = Path(root)
    found: list[Route] = []
    errors: list[str] = []
    for path in sorted(base.rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            errors.append(f"{path}: {error}")
            continue
        routes, problems = routes_in(text, filename=str(path))
        found.extend(routes)
        errors.extend(problems)
    return tuple(found), tuple(errors)
