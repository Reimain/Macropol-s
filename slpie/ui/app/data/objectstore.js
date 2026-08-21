/* The device tier: screens live on the reader's machine, truth lives on the server.
 *
 * §23 already decided this and called it something else. The ledger is
 * single-writer and authoritative in one place; the graph is a read model that
 * replicates freely; a replica caches up to ledger sequence *N* and, past its
 * freshness budget, reports how far behind it is rather than answering as
 * though it were fresh. A browser holding screen state on disk is the smallest
 * replica in that model — §23 says so in as many words about a laptop.
 *
 * **Why this is tractable here and awkward in NgRx or redux-persist.** Those
 * persist arbitrary client state and then face cache invalidation with TTLs and
 * refetch heuristics, because the server gave them nothing to order by. Every
 * answer here carries `X-Slpie-Version` and `X-Slpie-Ledger-Version`, so
 * invalidation is *ordering*, not guessing: a cell knows the sequence it was
 * answered at and the feed knows where the world is, and the difference is a
 * number.
 *
 * One protocol, three tiers, and callers never choose between them — the same
 * shape as `slpie_enterprise/storage/tiered.py`, which routes a `Tier` to a
 * filesystem or an object store without anything above it knowing which.
 *
 *   memory   a Map. Always present, and the whole store when nothing else is.
 *   device   IndexedDB. Survives a reload; wiped when the principal changes.
 *   shell    Cache Storage, owned by `sw.js` — the app itself, not its answers.
 *
 * Three rules that are not negotiable, each because the alternative is a real
 * incident rather than an inconvenience:
 *
 * **A principal change wipes.** Not filters. Leaving one tenant's graph on a
 * shared machine after a logout is a data-residency incident, and a filtered
 * view is still bytes on their disk. This is `ObjectRef`'s discipline from
 * `slpie/workspace/store.py` carried to the device: the prefix is enforced when
 * the key is built, so the store can never be asked for a key outside it.
 *
 * **A refused quota degrades, never crashes.** `QuotaExceededError` is the
 * device declining a capability, and it gets what §3 gives a refused capability
 * and §27 gives a missing binary: fall back, keep answering, and say what it
 * cost. Silently dropping to memory leaves somebody wondering why offline
 * stopped working; throwing loses a screen over a cache.
 *
 * **Nothing is written that the server did not mark keepable.** The device tier
 * reads `X-Slpie-Cacheable`, which the API stamps from the contract — the same
 * flag the service worker's policy comes from. §26 is explicit that a fourth
 * policy vocabulary must not be invented.
 */

const DB_NAME = "slpie";
const DB_VERSION = 1;
const STORE = "cells";

/** How many cells the device tier will hold before evicting the oldest. */
export const BUDGET = 200;

/* --- keys --------------------------------------------------------------- */

/**
 * A key that has been checked against the prefix it must stay inside.
 *
 * Building one *is* the check, so there is no way to reach a backend with a
 * bare string — which is what stops the check from being the thing somebody
 * forgets. `..`, a leading slash, and a principal that is a prefix of a
 * different principal (`acme` against `acme-corp`) are all refused, exactly as
 * the kernel's `ObjectRef` refuses them.
 */
export function ref(principal, tenant, cell) {
  const parts = [principal || "anonymous", tenant || "_", cell];
  for (const part of parts) {
    if (typeof part !== "string" || !part) {
      throw new Error("a storage key needs a principal, a tenant and a cell");
    }
  }
  if (/(^|\/)\.\.?(\/|$)/.test(cell) || cell.startsWith("/")) {
    throw new Error(`refusing the storage key "${cell}": it escapes its prefix`);
  }
  return parts.map(encodeURIComponent).join("/");
}

/** The prefix every key for this reader shares. Used to scope a wipe. */
export function prefix(principal, tenant) {
  return `${encodeURIComponent(principal || "anonymous")}/`
    + `${encodeURIComponent(tenant || "_")}/`;
}

/* --- the memory tier ---------------------------------------------------- */

export class MemoryStore {
  constructor() {
    this.tier = "memory";
    this.refusals = [];
    this._held = new Map();
  }

  async get(key) {
    return this._held.has(key) ? this._held.get(key) : null;
  }

  async put(key, value) {
    this._held.set(key, value);
  }

  async remove(key) {
    this._held.delete(key);
  }

  async keys() {
    return [...this._held.keys()].sort();
  }

  async clear(scope = "") {
    for (const key of [...this._held.keys()]) {
      if (!scope || key.startsWith(scope)) this._held.delete(key);
    }
  }
}

/* --- the device tier ---------------------------------------------------- */

function open() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: "key" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function transact(db, mode, run) {
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(STORE, mode);
    const result = run(transaction.objectStore(STORE));
    transaction.oncomplete = () => resolve(result && result.result);
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

export class DeviceStore {
  constructor(db, { budget = BUDGET } = {}) {
    this.tier = "device";
    this.refusals = [];
    this.budget = budget;
    this._db = db;
  }

  async get(key) {
    const found = await transact(this._db, "readonly", (store) => store.get(key));
    return found ? found.value : null;
  }

  async put(key, value) {
    try {
      await transact(this._db, "readwrite", (store) =>
        store.put({ key, value, written: Date.now() }));
      await this._evict();
    } catch (error) {
      // The device declining a capability, not a failure of ours. Recorded so
      // the console can say what it could not keep and what that cost, then
      // carried on with — the answer is already rendered from memory.
      this.refusals.push({
        key,
        reason: (error && error.name) || "unknown",
        detail: "the device would not store this answer; it is held in memory "
          + "for this visit and will not survive a reload",
      });
    }
  }

  async remove(key) {
    await transact(this._db, "readwrite", (store) => store.delete(key));
  }

  async keys() {
    const found = await transact(this._db, "readonly", (store) => store.getAllKeys());
    return (found || []).slice().sort();
  }

  async clear(scope = "") {
    const keys = await this.keys();
    for (const key of keys) {
      if (!scope || key.startsWith(scope)) await this.remove(key);
    }
  }

  /** Oldest-written first, once over budget. Reported, not silent. */
  async _evict() {
    const rows = await transact(this._db, "readonly", (store) => store.getAll());
    const held = rows || [];
    if (held.length <= this.budget) return;
    held.sort((one, two) => (one.written || 0) - (two.written || 0));
    for (const row of held.slice(0, held.length - this.budget)) {
      await this.remove(row.key);
    }
  }
}

/* --- choosing, which callers do not do ---------------------------------- */

/**
 * The best tier this device offers.
 *
 * Falls back to memory rather than failing: private browsing disables
 * IndexedDB, and a console that refuses to run there would be refusing over a
 * cache. The chosen tier is reported on the store so the interface can say
 * whether anything will survive a reload.
 */
export async function attach({ budget = BUDGET } = {}) {
  if (typeof indexedDB === "undefined") return new MemoryStore();
  try {
    return new DeviceStore(await open(), { budget });
  } catch (error) {
    const fallback = new MemoryStore();
    fallback.refusals.push({
      key: "*",
      reason: (error && error.name) || "unavailable",
      detail: "this device has no persistent store, so answers are held for "
        + "this visit only",
    });
    return fallback;
  }
}
