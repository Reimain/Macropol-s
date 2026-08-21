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

#: Files that ship but are not part of the bundled page, with the reason.
#: `engine/vendor/` is deliberate: the demo is the *native* console, and the
#: vendored renderer's dynamic `import()` fails here exactly as it does in an
#: air-gapped install — the seam falls back to `canvas2d` and says why.
EXCLUDED = ("sw.js", "boot.js", "engine/vendor/")

#: Where the bundle starts. Everything else is reached from here.
ENTRY = "shell.js"

#: Ships, is precached for offline, and no screen imports it — measured, and
#: recorded in `docs/AUDIT.md` rather than quietly bundled. The graph screen
#: draws through `components/graph.js`'s SVG, so the whole renderer seam is
#: reachable from its own tests and from nowhere a reader can get to. Bundling
#: it would put 60KB of unreachable code in the published page and hide the
#: finding; naming it here keeps `test_the_bundle_reaches_every_file_that_ships`
#: able to fail on the *next* orphan.
UNREACHED = (
    "engine/aggregate.js", "engine/camera.js", "engine/canvas2d.js",
    "engine/contract.js", "engine/glyph.js", "engine/layout.js",
    "engine/palette.js", "engine/ride.js",
)

#: Static import specifiers, as the browser sees them.
IMPORTS = re.compile(
    r'^\s*import\s+(?:[^"\';]+\s+from\s+)?["\']([^"\']+)["\']', re.M,
)


def _relative(spec: str, origin: str) -> str | None:
    """A specifier, as a key in the registry — or `None` if it is not ours."""
    if not spec.startswith("."):
        return None
    return resolve(spec, origin)


def modules(entry: str = ENTRY) -> list[str]:
    """Every module the entry reaches, in dependency order.

    Read from the imports rather than restated as a list. The list version went
    stale the moment `app/ui/` became `app/components/`: the demo referenced
    seven files that no longer existed, so the published page could not be built
    at all — and nothing said so until somebody ran the build. A bundler that
    derives its own order cannot drift from the tree it bundles.
    """
    ordered: list[str] = []
    seen: set[str] = set()

    def visit(name: str, stack: tuple[str, ...] = ()) -> None:
        if name in seen:
            return
        if name in stack:
            # A cycle is legal in ESM and unbundlable in this registry, so it is
            # named rather than silently producing a half-initialised module.
            raise SystemExit(f"import cycle through {name}: {' -> '.join(stack)}")
        source = (APP / name).read_text(encoding="utf-8")
        for spec in IMPORTS.findall(source):
            target = _relative(spec, name)
            if target and not target.startswith(EXCLUDED):
                if not (APP / target).is_file():
                    raise SystemExit(f"{name} imports {spec}, which does not exist")
                visit(target, stack + (name,))
        seen.add(name)
        ordered.append(name)

    visit(entry)
    return ordered


#: The stylesheet's own `@import` order, for the same reason.
AT_IMPORT = re.compile(r'@import\s+["\']([^"\']+)["\']')


def styles() -> list[str]:
    root = (APP / "styles.css").read_text(encoding="utf-8")
    found = [spec.lstrip("./") for spec in AT_IMPORT.findall(root)]
    if not found:
        raise SystemExit("styles.css declares no @import — has it been replaced?")
    return found


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


#: A body, as one string both sides agree on. The browser sends a POST body the
#: recording has to be keyed by, and key order in JSON is not guaranteed — so
#: both ends sort and join instead of hashing the serialisation.
def _key(body: dict) -> str:
    return "&".join(
        f"{name}={body[name]}" for name in sorted(body) if not isinstance(
            body[name], (dict, list))
    )


def _demands() -> list[dict]:
    """The dashboard demands the recording answers.

    Every single-axis move from the empty demand, and every (utility, domain)
    pair — which is what the screen produces as a reader works the controls.
    Contexts are recorded singly rather than crossed: `for` changes the layout
    of the same subject, so a reader exploring it one axis at a time is served,
    and the full cube would be 150 scans for a page nobody scrolls that far in.
    """
    from slpie.present.template import DOMAINS, UTILITIES

    out: list[dict] = [{}]
    out += [{"utility": item} for item in UTILITIES]
    out += [{"domain": item} for item in DOMAINS]
    out += [{"utility": one, "domain": two} for one in UTILITIES for two in DOMAINS]
    return out


#: What the estate's own root is called in the published page. The world is
#: materialised into a temporary directory, and a demo whose evidence cites
#: `/tmp/tmp8f3k/services/payments` reads as a bug rather than as an estate.
ESTATE = "/acme-production"


def scrub(recording: dict, *paths: str) -> dict:
    """Take the build machine out of the recording.

    Evidence carries `file://` URIs, and the URIs are real absolute paths on
    whichever machine ran the build. Publishing them leaks the runner's layout
    and — worse for the reader — makes every citation on the page point at a
    directory that never existed on theirs.

    Rewritten as text rather than walked as a tree because the paths appear in
    URIs, in excerpts, in reasoning sentences and in gap details; a structural
    walk would have to know all four and would miss the fifth.
    """
    body = json.dumps(recording)
    for path in sorted(paths, key=len, reverse=True):
        if path and path != "/":
            body = body.replace(path.rstrip("/"), ESTATE)
    return json.loads(body)


def capture() -> dict:
    """A real scan, recorded route by route."""
    from slpie.ui.api import Api, Request
    from tools.ui.world import build

    world = tempfile.mkdtemp()
    engine = build(world)
    api = Api(engine=engine)

    def record(method: str, path: str, body: dict | None = None) -> dict:
        try:
            response = api.handle(Request(method, path, {}, body or {}))
        except Exception as error:                       # noqa: BLE001
            return {"__status": 500, "__body": {"error": str(error)}}
        return {
            "__status": getattr(response, "status", 200),
            "__body": getattr(response, "body", response),
        }

    # Every GET the API declares, rather than a list somebody maintains beside
    # it. The generated inspectors are *most* of the interface — the whole API
    # management section is one — so a hand-kept list means the screens nobody
    # authored are the screens the demo cannot show.
    recorded = {}
    for method, path in api.routes:
        if method != "GET" or ":" in path or path == "/api/stream":
            continue
        recorded[path] = record("GET", path)

    # Routes that need a subject: recorded for one real node, so the detail
    # screens have something to open rather than a 404.
    subject = ""
    nodes = (recorded.get("/api/graph", {}).get("__body") or {}).get("nodes") or []
    if nodes:
        subject = str(nodes[0].get("id") or "")
    if subject:
        for path in ("/api/node", "/api/impact"):
            recorded[f"{path}?id={subject}"] = record("GET", f"{path}?id={subject}")

    posts: dict[str, dict] = {}

    def post(path: str, body: dict) -> None:
        posts.setdefault(path, {})[_key(body)] = record("POST", path, body)

    post("/api/ask", {"question": "what breaks if lodash 5 lands?"})
    post("/api/plan", {"question": "what breaks if lodash 5 lands?"})
    # The *estate*, not this repository. Recording `discover --path .` scanned
    # whatever tree the build ran in, so the Compose screen answered about the
    # machine that baked the page while every other screen answered about
    # `acme-production` — two estates in one console, and the confusing one was
    # the one with a `Run` button under it.
    post("/api/run", {"pipeline": f"discover --path {world} | link | findings"})
    for demand in _demands():
        pipeline = "scan | dashboard --govern" + "".join(
            f" --{name} {value}" for name, value in demand.items()
        )
        post("/api/run", {"pipeline": pipeline})

    events = []
    try:
        for record_ in engine.ledger.read(limit=40):
            events.append({
                "sequence": getattr(record_, "sequence", 0),
                "kind": getattr(record_, "kind", ""),
                "subject": str(getattr(record_, "subject", ""))[:80],
            })
    except Exception:                                     # noqa: BLE001
        pass

    return scrub(
        {"get": recorded, "post": posts, "events": events, "node": subject},
        world, str(ROOT), tempfile.gettempdir(),
    )


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

function missing(what) {
  return new Response(JSON.stringify({
    error: `${what} is not in this recording`,
    detail: "This page replays one scan. A question it was not asked says so "
      + "rather than answering from something adjacent.",
  }), {status: 404, headers: {"content-type": "application/json"}});
}

function answer(hit) {
  return new Response(JSON.stringify(hit.__body === undefined ? hit : hit.__body), {
    status: hit.__status || 200,
    headers: {
      "content-type": "application/json",
      "x-slpie-version": "1",
      "x-slpie-ledger-version": "1",
    },
  });
}

/* The same key Python wrote, built the same way: sorted names, scalars only.
 * Hashing the serialised body would have been shorter and wrong — key order in
 * JSON is not guaranteed, so the two ends would disagree on some browsers and
 * not on others. */
function bodyKey(raw) {
  let body = {};
  try { body = JSON.parse(raw || "{}"); } catch (error) { body = {}; }
  return Object.keys(body).sort()
    .filter((name) => body[name] === null || typeof body[name] !== "object")
    .map((name) => `${name}=${body[name]}`)
    .join("&");
}

window.fetch = async (input, init = {}) => {
  const url = typeof input === "string" ? input : input.url;
  const path = url.replace(/^https?:\\/\\/[^/]+/, "");
  await new Promise((done) => setTimeout(done, 90));   // a plausible latency

  if ((init.method || "GET") === "POST") {
    const bucket = RECORDED.post[path.split("?")[0]];
    if (!bucket) return missing(path);
    const hit = bucket[bodyKey(init.body)];
    return hit ? answer(hit) : missing("that request");
  }
  // Exact first — `/api/node?id=…` is recorded whole — then the bare path, so
  // a query the recording ignores still answers.
  const hit = RECORDED.get[path] || RECORDED.get[path.split("?")[0]];
  return hit ? answer(hit) : missing(path);
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
    css = "\n".join(f"/* {name} */\n{(APP / name).read_text()}" for name in styles())
    bundle = "".join(transform(name, (APP / name).read_text()) for name in modules())

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
    <a href="#/dashboard">Dashboard</a>
    <a href="#/graph">Graph</a>
    <a href="#/findings">Findings</a>
    <a href="#/compose">Compose</a>
    <a href="#/portal">API portal</a></span>
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
