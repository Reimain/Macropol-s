/* The read model, client side.
 *
 * One store holding many cells, and a cell is the browser's mirror of a
 * `QueryResult` plus the parts of HTTP that matter: the status, the version the
 * answer was computed at, and whether it came from a cache while offline.
 *
 * The version is why this exists at all. A screen that refetches on every event
 * receives responses out of order — the fetch triggered by event N can land
 * after the one triggered by event N+1 — and with no version it paints the
 * older answer and the screen goes backwards. `commit` drops a response whose
 * version is behind what is already held, which is a rule the server made
 * possible by sending `X-Slpie-Version` and the client would otherwise have no
 * way to apply.
 *
 * Subscriptions are synchronous. Components never await; they render from a
 * snapshot, and the snapshot is whatever is true at the moment they are called.
 */

const cells = new Map();
const watchers = new Map();

export const IDLE = "idle";
export const LOADING = "loading";
export const READY = "ready";
export const ERROR = "error";
export const REFUSED = "refused";

function blank() {
  return {
    status: IDLE,
    value: null,
    version: 0,      // X-Slpie-Version — which projection state answered
    ledger: 0,       // X-Slpie-Ledger-Version — where the world was
    projection: "",
    stale: false,    // served from cache while offline
    partial: false,  // a flow that failed mid-pipeline but carries a value
    error: null,
    fetchedAt: 0,
  };
}

export function cell(key) {
  return cells.get(key) || blank();
}

/**
 * Install a new state for `key`, unless it is older than what is held.
 *
 * Equal versions are accepted: two projections at the same version are both
 * current, and refusing the second would strand a screen on a stale render of
 * an identical answer.
 */
export function commit(key, next) {
  const held = cells.get(key);
  if (held && next.version && held.version && next.version < held.version) {
    return held;   // an older answer arrived late; keep what we have
  }
  const merged = { ...blank(), ...held, ...next };
  cells.set(key, merged);
  notify(key, merged);
  return merged;
}

export function begin(key) {
  const held = cells.get(key) || blank();
  // Loading keeps the previous value, so a refresh does not blank the panel.
  // A screen that empties on every event is unreadable while anything is
  // happening, which is precisely when somebody is looking at it.
  return commit(key, { ...held, status: LOADING, error: null });
}

export function invalidate(keys) {
  for (const key of keys) {
    const held = cells.get(key);
    if (held) notify(key, { ...held, fetchedAt: 0 });
  }
}

export function forget(key) {
  cells.delete(key);
}

export function subscribe(key, watcher) {
  if (!watchers.has(key)) watchers.set(key, new Set());
  watchers.get(key).add(watcher);
  return () => watchers.get(key)?.delete(watcher);
}

function notify(key, state) {
  for (const watcher of watchers.get(key) || []) {
    try {
      watcher(state);
    } catch (error) {
      // A broken subscriber must not stop the others from being told. This is
      // the same argument the event bus makes on the server: one dead listener
      // is not an outage.
      console.error("subscriber failed for", key, error);
    }
  }
}

/** For tests and for a hard refetch after the feed reports a gap. */
export function reset() {
  cells.clear();
}
