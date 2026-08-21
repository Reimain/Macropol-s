"""Framework route tables as discoverers — the corroboration §1 has been missing.

The environment manifest declares `network:` elements: a name, a URL, a kind,
maybe an OpenAPI contract. Every one of them arrives as `DECLARED` evidence at
0.92 — authoritative about *intent* and about nothing else. Until something
independent observes the same endpoint, the platform has one source saying it
exists and knows it.

These discoverers are that second source. A FastAPI `@app.get("/orders")` or a
Django `path("orders/", …)` is the **code that serves the thing the manifest
promised**, read from the repository, cited to a file and a line. That turns a
declaration into a corroborated fact by the ordinary route: two independent
observations, noisy-OR, confidence rises because it was checked rather than
because somebody asserted it harder.

It also produces the deltas §1 exists for. A route in the code that no manifest
declares is `UNDECLARED_ELEMENT` — a shadow endpoint. A declared endpoint no
route serves is `DECLARED_NOT_FOUND`. Neither is findable with one source.

**Static, never imported.** These read the source with `ast` rather than
importing the application to inspect its router. Importing a customer's web
app executes their module-level code — connecting to their database, reading
their secrets, starting their background threads — inside a scan. Every
discoverer in this platform reads files, and there is no exception worth making
for this one.
"""

from __future__ import annotations

from .routes import discover_routes, routes_in

__all__ = ["discover_routes", "routes_in"]
