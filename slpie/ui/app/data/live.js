/* The live feed: one connection, fanned out, and lossless across a drop.
 *
 * `EventSource` reconnects on its own and sends back the last `id:` it saw as
 * `Last-Event-ID`, which the server replays from. That is the whole resume
 * mechanism and it costs nothing — but only because the server now emits an
 * `id:` per frame. Before that, every reconnect silently lost whatever happened
 * while the socket was down, and the view diverged from the platform with
 * nothing on screen to say so.
 *
 * Two things are tracked here that the browser will not track for us:
 *
 *  - the last sequence seen, so a deliberate resume (a phone waking up, where
 *    the page closed the socket itself) can ask for `?since=`;
 *  - the `dropped` event, which the server sends when a resume point predates
 *    what it can still replay. That is the cue to refetch everything rather
 *    than to patch, and patching from a partial replay is how a screen ends up
 *    confidently wrong.
 */

import { emit } from "../core/bus.js";

const STREAM = "/api/stream";
const BACKOFF = [500, 1000, 2000, 4000, 8000];
/* Mobile browsers kill an open socket in the background anyway. Closing it
 * ourselves and resuming with `?since=` turns an unpredictable loss into a
 * known one. */
const HIDDEN_GRACE = 60000;

let source = null;
let attempt = 0;
let lastSequence = 0;
let hiddenAt = 0;
let state = "offline";

export function status() {
  return { state, lastSequence };
}

function announce(next) {
  if (state === next) return;
  state = next;
  emit("connection", { state, lastSequence });
}

export function connect() {
  disconnect();

  const url = lastSequence ? `${STREAM}?since=${lastSequence}` : STREAM;
  announce(source ? "reconnecting" : "connecting");
  source = new EventSource(url);

  source.addEventListener("open", () => {
    attempt = 0;
    announce("live");
  });

  source.addEventListener("dropped", (raw) => {
    // Told, not silently short-changed. Everything held is suspect, so the
    // honest response is a full refetch rather than applying the tail of a
    // replay to a state that is missing its head.
    emit("dropped", { missed: Number(raw.data || 0) });
  });

  source.addEventListener("message", (raw) => {
    if (raw.lastEventId) lastSequence = Number(raw.lastEventId) || lastSequence;
    let payload = null;
    try {
      payload = JSON.parse(raw.data);
    } catch (error) {
      return;   // a malformed frame is not worth tearing the connection down
    }
    emit("event", payload);
    if (payload.kind) emit(payload.kind, payload);
  });

  source.addEventListener("error", () => {
    // `EventSource` retries on its own using the server's `retry:`, but only
    // while the connection is merely dropped. A closed one needs reopening,
    // and the backoff stops a dead server being hammered by every open tab.
    if (source && source.readyState === EventSource.CLOSED) {
      announce("reconnecting");
      const wait = BACKOFF[Math.min(attempt, BACKOFF.length - 1)];
      attempt += 1;
      // Jitter, so a hundred tabs do not all return in the same millisecond.
      setTimeout(connect, wait + Math.random() * 250);
    } else {
      announce("reconnecting");
    }
  });
}

export function disconnect() {
  if (source) {
    source.close();
    source = null;
  }
  announce("offline");
}

/** Close while hidden, resume on return. The mobile half of PWA-first. */
export function watchVisibility() {
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      hiddenAt = Date.now();
      return;
    }
    if (hiddenAt && Date.now() - hiddenAt > HIDDEN_GRACE) {
      connect();       // resumes from `lastSequence`
    }
    hiddenAt = 0;
  });
}
