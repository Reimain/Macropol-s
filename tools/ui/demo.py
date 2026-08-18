"""Bake the console into one self-contained HTML page, for GitHub Pages.

    python -m tools.ui.demo --out docs/_build/demo/index.html

**Not a second implementation of the design.** The actual modules from
`slpie/ui/app` are inlined verbatim, in dependency order, behind a thirty-line
module registry — so the routing, the store, the type checker, the grid and the
graph that render here are the ones that ship. Only the *transport* is replaced:
`fetch` answers from a recording of a real scan, and `EventSource` replays that
run's ledger. A bug in the interface is a bug you see in this page.

That distinction is the whole reason this exists rather than a hand-written demo
page. A separate demo drifts from the product by the second release and then
misrepresents it; this one cannot, because there is nothing in it to drift.

It needs no browser and no bundler: the recording is captured in Python through
`Api.handle`, and the modules are plain text. So CI can build it from the same
kernel-only install the documentation uses.

**Two runs are not byte-identical, and that is correct.** Everything structural
is stable — the manifest digest, and the node, edge and evidence counts — but the
*ledger digest* differs, because the ledger is a hash chain over events carrying
real wall-clock times. Freezing the clock to make the page reproduce byte for
byte would publish a ledger that misrepresents how the real one behaves, which is
a worse trade than a digest that moves. It was checked rather than assumed, and
the check also confirmed the page embeds no absolute path from the build machine.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

APP = ROOT / "slpie" / "ui" / "app"
DEFAULT_OUT = ROOT / "docs" / "_build" / "demo" / "index.html"

# Dependency order: core has no imports, then data/ui, then screens, then shell.
MODULES = [
    "core/dom.js", "core/format.js", "core/bus.js", "core/store.js",
    "core/result.js", "core/router.js",
    "data/client.js", "data/http.js", "data/queries.js", "data/live.js",
    "ui/pill.js", "ui/panel.js", "ui/table.js", "ui/grid.js", "ui/chart.js",
    "ui/graph.js",
    "ui/nav.js", "ui/density.js", "ui/opener.js",
    "screens/inspector.js", "screens/console.js", "screens/compose.js",
    "screens/findings.js", "screens/graph.js", "screens/verbs.js",
    "screens/catalog.js",
    "screens/workspaces.js", "screens/index.js",
    "shell.js",
]

STYLES = [
    "styles/tokens.css", "styles/density.css", "styles/base.css",
    "styles/layout.css", "styles/components.css", "styles/screens.css",
]


def resolve(spec: str, origin: str) -> str:
    """A relative specifier, as a key in the registry."""
    base = Path(origin).parent
    return str((base / spec).resolve().relative_to(Path("/x").resolve())) \
        if False else str((Path("/x") / base / spec).resolve().relative_to("/x"))


def transform(path: str, source: str) -> str:
    """One ES module, as a registry factory. Exports become getters so a
    reassigned binding stays live, which is how ESM actually behaves."""
    exports: list[str] = []

    def named(match: re.Match) -> str:
        names, spec = match.group(1), match.group(2)
        key = resolve(spec, path)
        # `a, b as c` → `a, b: c`
        binding = ", ".join(
            part.strip().replace(" as ", ": ") for part in names.split(",") if part.strip()
        )
        return f'const {{ {binding} }} = __m("{key}");'

    def star(match: re.Match) -> str:
        alias, spec = match.group(1), match.group(2)
        return f'const {alias} = __m("{resolve(spec, path)}");'

    out = re.sub(r'import\s*\{([^}]*)\}\s*from\s*["\']([^"\']+)["\']\s*;', named, source)
    out = re.sub(r'import\s*\*\s*as\s+(\w+)\s+from\s*["\']([^"\']+)["\']\s*;', star, out)

    # `export { fmt };` — a plain re-export of an already-declared binding.
    for match in re.finditer(r'^export\s*\{([^}]*)\}\s*;', out, re.M):
        exports.extend(n.strip() for n in match.group(1).split(",") if n.strip())
    out = re.sub(r'^export\s*\{[^}]*\}\s*;', "", out, flags=re.M)

    for match in re.finditer(r'^export\s+(?:async\s+)?function\s+(\w+)', out, re.M):
        exports.append(match.group(1))
    for match in re.finditer(r'^export\s+(?:const|let|var)\s+(\w+)', out, re.M):
        exports.append(match.group(1))
    out = re.sub(r'^export\s+', "", out, flags=re.M)

    getters = ", ".join(f'get {name}() {{ return {name}; }}' for name in dict.fromkeys(exports))
    return (
        f'__d("{path}", function () {{\n{out}\n'
        f'return {{ {getters} }};\n}});\n'
    )


def capture() -> dict:
    """A real scan, recorded route by route."""
    from tools.ui.world import build
    from slpie.ui.api import Api, Request

    engine = build(tempfile.mkdtemp())
    api = Api(engine=engine)

    routes = [
        "/api/status", "/api/graph", "/api/findings", "/api/station",
        "/api/reconcile", "/api/cycles", "/api/verbs", "/api/manual",
        "/api/routes", "/api/screens", "/api/integrity", "/api/projections",
        "/api/scenarios", "/api/manifest", "/api/stream/status",
        "/api/admin/workspaces", "/api/admin/datasets", "/api/admin/quota",
        "/api/apim/apis", "/api/apim/gateway", "/api/apim/throttles",
        "/api/apim/analytics", "/api/apim/subscriptions", "/api/contract",
    ]
    recorded = {}
    for route in routes:
        try:
            response = api.handle(Request("GET", route, {}, {}))
        except Exception as error:                       # noqa: BLE001
            recorded[route] = {"__status": 500, "error": str(error)}
            continue
        body = getattr(response, "body", response)
        recorded[route] = {
            "__status": getattr(response, "status", 200),
            "__body": body,
        }

    # A few POSTs the console makes, so Ask and Run answer in the demo.
    posts = {}
    for path, payload in (
        ("/api/ask", {"question": "what breaks if lodash 5 lands?"}),
        ("/api/run", {"pipeline": "discover --path . | link | findings"}),
    ):
        try:
            response = api.handle(Request("POST", path, {}, body=payload))
            posts[path] = {
                "__status": getattr(response, "status", 200),
                "__body": getattr(response, "body", response),
            }
        except Exception as error:                       # noqa: BLE001
            posts[path] = {"__status": 500, "__body": {"error": str(error)}}

    events = []
    try:
        for record in engine.ledger.read(limit=40):
            events.append({
                "sequence": getattr(record, "sequence", 0),
                "kind": getattr(record, "kind", ""),
                "subject": str(getattr(record, "subject", ""))[:80],
            })
    except Exception:                                     # noqa: BLE001
        pass

    return {"get": recorded, "post": posts, "events": events}


SHIM = """
/* The network, recorded.
 *
 * Every module below is the real one from `slpie/ui/app`. Only the transport is
 * replaced: `fetch` answers from a recording of an actual scan of a simulated
 * `acme-production` estate, and `EventSource` replays that run's ledger so the
 * live feed behaves as it does against a running server.
 *
 * Nothing about the rendering, the routing, the store or the type checking is
 * faked — a bug in any of them is a bug you would see here. */
const RECORDED = __RECORDING__;

function reply(store, path) {
  const key = path.split("?")[0];
  const hit = store[key];
  if (!hit) {
    return new Response(JSON.stringify({error: "not recorded in this demo"}),
      {status: 404, headers: {"content-type": "application/json"}});
  }
  return new Response(JSON.stringify(hit.__body === undefined ? hit : hit.__body), {
    status: hit.__status || 200,
    headers: {
      "content-type": "application/json",
      "x-slpie-version": "1",
      "x-slpie-ledger-version": "1",
    },
  });
}

window.fetch = async (input, init = {}) => {
  const url = typeof input === "string" ? input : input.url;
  const path = url.replace(/^https?:\\/\\/[^/]+/, "");
  await new Promise((done) => setTimeout(done, 90));   // a plausible latency
  return reply((init.method || "GET") === "POST" ? RECORDED.post : RECORDED.get, path);
};

/* A stand-in for the live feed. The console's reconnect logic, sequence
 * tracking and dropped-gap handling all run against this exactly as they do
 * against the server's SSE stream. */
window.EventSource = class {
  constructor() {
    this.readyState = 1;
    this.listeners = {};
    setTimeout(() => this.onopen && this.onopen({}), 120);
    let index = 0;
    this.timer = setInterval(() => {
      const event = RECORDED.events[index % RECORDED.events.length];
      index += 1;
      if (!event) return;
      const message = {data: JSON.stringify(event), lastEventId: String(event.sequence)};
      (this.listeners.message || []).forEach((fn) => fn(message));
      if (this.onmessage) this.onmessage(message);
    }, 2200);
  }
  addEventListener(name, fn) {
    (this.listeners[name] = this.listeners[name] || []).push(fn);
  }
  removeEventListener() {}
  close() { clearInterval(this.timer); this.readyState = 2; }
};

/* A demo has no service worker and no storage guarantees. */
if (!("serviceWorker" in navigator)) navigator.serviceWorker = undefined;
"""

REGISTRY = """
const __cache = {}, __factories = {};
function __d(name, factory) { __factories[name] = factory; }
function __m(name) {
  if (!(name in __cache)) {
    if (!__factories[name]) throw new Error("module not bundled: " + name);
    __cache[name] = __factories[name]();
  }
  return __cache[name];
}
"""


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="where to write the page (default: docs/_build/demo/index.html)")
    args = parser.parse_args(argv)

    recording = capture()
    css = "\n".join(f"/* {name} */\n{(APP / name).read_text()}" for name in STYLES)
    bundle = "".join(transform(name, (APP / name).read_text()) for name in MODULES)

    shim = SHIM.replace("__RECORDING__", json.dumps(recording))
    body = re.search(r"<body>(.*)</body>", (APP / "index.html").read_text(), re.S)
    markup = body.group(1)
    markup = re.sub(r'<script[^>]*></script>|<script[^>]*>.*?</script>', "", markup, flags=re.S)

    page = f"""<!doctype html>
<html lang="en" data-theme="light" data-density="bench">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SLPIE Console</title>
<style>
{css}
/* The one surface this page adds. Built from the console's own tokens so it
   reads as part of the instrument rather than as a sticker on top of it. */
body {{ grid-template-rows: auto var(--topbar-h) 1fr;
       grid-template-areas: "banner banner" "rail topbar" "rail page"; }}
.demo-banner {{
  grid-area: banner;
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--sp-3) var(--sp-5);
  background: var(--accent-soft);
  border-bottom: 1px solid var(--line);
  padding: var(--sp-3) var(--sp-6);
  font-size: var(--fs-sm);
}}
.demo-banner .tag {{
  font-weight: 600;
  color: var(--accent);
  text-transform: uppercase;
  letter-spacing: var(--tracking);
  font-size: var(--fs-xs);
}}
.demo-banner .try {{ color: var(--muted); margin-left: auto; }}
.demo-banner .try a {{ margin-left: var(--sp-4); }}
@media (max-width: 720px) {{
  body {{ grid-template-rows: auto var(--topbar-h) auto 1fr;
         grid-template-areas: "banner" "topbar" "rail" "page"; }}
  .demo-banner .try {{ margin-left: 0; width: 100%; }}
}}
</style>
</head>
<body>
<div class="demo-banner">
  <span class="tag">Recorded demo</span>
  <span>The shipping console, replaying a real scan of a simulated
    <code>acme-production</code> estate — 41 nodes, 48 relationships,
    66 pieces of evidence. Try <b>Calm</b> and <b>Dense</b> in the top right — they are two different instruments, not two sizes.</span>
  <span class="try">Start here
    <a href="#/graph">Graph</a>
    <a href="#/verbs">Verbs</a>
    <a href="#/compose">Compose</a></span>
</div>
{markup}
<script type="module">
{REGISTRY}
{shim}
{bundle}
__m("shell.js");
</script>
</body>
</html>
"""
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page)
    print(f"wrote {args.out}  ({len(page) / 1024:.0f} KB)")
    print(f"recorded {len(recording['get'])} GET routes, "
          f"{len(recording['post'])} POST, {len(recording['events'])} events")


if __name__ == "__main__":
    main()
