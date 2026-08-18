/* The service worker — what makes this installable and usable offline.
 *
 * The caching rule is the interesting part, and it is deliberately asymmetric:
 *
 *   assets   cache-first    the shell never changes between requests, so serving
 *                           it from cache is both faster and what lets the app
 *                           open with no network at all.
 *   /api/*   network-first  an answer about an environment is only worth having
 *                           if it is current. A cached graph served as though it
 *                           were fresh is exactly the "stale replica answering as
 *                           if fresh" failure the platform refuses elsewhere.
 *
 * When the network fails and a cached API response exists, it IS served — but
 * tagged `x-slpie-stale`, so the page can mark the answer as offline rather than
 * present it as live. The honesty rule does not weaken because the client is a
 * laptop on a plane.
 */

/* Bumped when the shell's file set changes. An `activate` handler deletes every
 * cache that is not this one, so the bump is what evicts a stale shell — a
 * version left alone across a restructure serves yesterday's modules from
 * cache and the page dies on an import that no longer exists. */
const VERSION = "slpie-v4";

/* Every file the app needs to open with the network unplugged. Listed rather
 * than discovered, because discovery needs a request and the whole point is to
 * work without one.
 *
 * `tests/test_slpie_ui_assets.py` checks this list in *both* directions. The
 * forward check — everything listed is served — has always been here. The
 * reverse check is the one that matters after a restructure: a new module that
 * nobody added to this list installs fine, works online, and breaks offline,
 * which is the failure nobody reproduces until a plane. */
const SHELL = [
  "/", "/index.html", "/manifest.webmanifest", "/icon.svg",
  "/styles.css",
  "/styles/tokens.css", "/styles/density.css", "/styles/base.css",
  "/styles/layout.css", "/styles/components.css", "/styles/screens.css",
  "/boot.js", "/shell.js",
  "/core/dom.js", "/core/store.js", "/core/bus.js", "/core/router.js",
  "/core/result.js", "/core/format.js",
  "/data/client.js", "/data/http.js", "/data/live.js", "/data/queries.js",
  "/ui/panel.js", "/ui/table.js", "/ui/pill.js", "/ui/nav.js", "/ui/density.js",
  "/ui/opener.js", "/ui/chart.js", "/ui/graph.js", "/ui/grid.js",
  "/screens/index.js", "/screens/console.js", "/screens/compose.js",
  "/screens/findings.js", "/screens/graph.js", "/screens/verbs.js",
  "/screens/catalog.js",
  "/screens/workspaces.js",
  "/screens/inspector.js",
];

/* The live feed's path, which must never be cached. Named once here and
 * asserted against the client's `EventSource(...)` call by the suite. */
const STREAM = "/api/stream";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(VERSION)
      // `addAll` rejects if any single entry fails, which would leave no cache at
      // all. Adding individually means a missing asset costs that asset only.
      .then((cache) => Promise.allSettled(SHELL.map((path) => cache.add(path))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(
        names.filter((name) => name !== VERSION).map((name) => caches.delete(name)),
      ))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // The event stream must never be cached: it is an open connection, not a
  // document, and a cached SSE response would replay history as though it were
  // happening now.
  //
  // This guard named `/events` for three phases — a path the server has never
  // served. The real endpoint is `/api/stream`, which therefore fell straight
  // through to `networkFirst` below and had `cache.put` applied to an infinite
  // response. The test that was meant to catch this searched `sw.js` for the
  // string "/events" and so passed on the dead path, which is why nothing said
  // anything. `tests/test_slpie_ui.py` now parses the URL out of the client's
  // own `EventSource(...)` call instead, so the two cannot drift again.
  if (url.pathname === STREAM) return;

  if (url.pathname.startsWith("/api/")) {
    event.respondWith(networkFirst(request));
    return;
  }
  event.respondWith(cacheFirst(request));
});

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(VERSION);
      cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    const shell = await caches.match("/index.html");
    if (shell) return shell;
    throw error;
  }
}

function cacheable(response) {
  // A path check is the first line and a content-type check is the second. A
  // route added later that streams — and there will be one — is caught here
  // without anybody remembering to add it to a list.
  const type = response.headers.get("content-type") || "";
  return response.ok && !type.includes("text/event-stream");
}

async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (cacheable(response)) {
      const cache = await caches.open(VERSION);
      cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    const cached = await caches.match(request);
    if (!cached) throw error;

    // Served, but never as though it were fresh.
    const headers = new Headers(cached.headers);
    headers.set("x-slpie-stale", "1");
    return new Response(await cached.blob(), {
      status: cached.status,
      statusText: "stale (offline)",
      headers,
    });
  }
}
