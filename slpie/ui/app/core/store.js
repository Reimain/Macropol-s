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
 *
 * **Persistence rides the version rule rather than working around it** (§31).
 * A backend can be attached with `persist()`, and then a cell the server marked
 * keepable is written through and read back on the next visit. Hydration is an
 * ordinary `commit()` — so a cell restored from disk is older by construction
 * and *cannot* overwrite something fresher. The rule that already existed is
 * what makes hydration safe, and there is no second path for it to be wrong in.
 *
 * The backend is injected, not imported: `core/` imports nothing, and reaching
 * into `data/` for a store would break the tier rule the whole browser tree
 * follows. `shell.js` wires them together, as it does for the lexicon.
 */

const cells = new Map();
const watchers = new Map();

/* The device tier, when one is attached. Absent, everything below behaves
 * exactly as it did — which is what lets the whole existing suite stand as the
 * proof that persistence is inert until asked for. */
let backend = null;
let scope = "";
let keyFor = (key) => key;

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
    stale: false,    // served from cache, or restored from this device
    partial: false,  // a flow that failed mid-pipeline but carries a value
    error: null,
    fetchedAt: 0,
    keep: false,     // X-Slpie-Cacheable — whether a device may hold this
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
  // Only what the server marked keepable, and only when it is an answer. A
  // refusal or a 409 "no environment open" held past the moment one is opened
  // is a console insisting the platform is empty.
  if (backend && merged.keep && merged.status === READY && merged.value !== null) {
    write(key, merged);
  }
  return merged;
}

function write(key, state) {
  Promise.resolve(backend.put(keyFor(key), {
    value: state.value,
    version: state.version,
    ledger: state.ledger,
    projection: state.projection,
    fetchedAt: state.fetchedAt,
  })).catch(() => {
    /* the backend records its own refusals; the answer is already on screen */
  });
}

/**
 * Attach a device tier, and say who it belongs to.
 *
 * **A different principal wipes rather than filters.** Leaving one tenant's
 * graph on a shared machine after a logout is a data-residency incident, and a
 * filtered view is still their bytes on somebody's disk.
 */
export async function persist(store, { key = (k) => k, owner = "" } = {}) {
  backend = store;
  keyFor = key;
  if (owner && scope && owner !== scope) {
    await store.clear();
    cells.clear();
  }
  scope = owner;
  return store;
}

/**
 * Read the device tier back into the store.
 *
 * Every restored cell goes through `commit`, so the version rule applies to it
 * exactly as it applies to a network answer — and a restored cell is older than
 * anything fetched since, so it can only fill a gap and never win a race.
 *
 * `stale` is set on every one of them. A screen painted from disk while the
 * server is four hundred sequences ahead must say so: that is `STALE_REPLICA`
 * (§23) with the client being a laptop, and `panel.js` already renders it.
 */
export async function hydrate(keys = []) {
  if (!backend) return 0;
  let restored = 0;
  for (const key of keys) {
    let held = null;
    try {
      held = await backend.get(keyFor(key));
    } catch (error) {
      continue;                 // an unreadable device is not a broken console
    }
    if (!held || held.value === null || held.value === undefined) continue;
    commit(key, { ...held, status: READY, stale: true, keep: true });
    restored += 1;
  }
  return restored;
}

/** What the device tier would not keep, for the console to report honestly. */
export function refusals() {
  return backend ? backend.refusals || [] : [];
}

/** Which tier is answering: `memory` for this visit only, `device` to survive. */
export function tier() {
  return backend ? backend.tier : "none";
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
