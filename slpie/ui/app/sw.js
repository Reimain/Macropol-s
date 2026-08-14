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

const VERSION = "slpie-v1";
const SHELL = [
  "/", "/index.html", "/styles.css", "/app.js", "/compose.js",
  "/manifest.webmanifest", "/icon.svg",
];

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
  if (url.pathname === "/events") return;

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

async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
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
