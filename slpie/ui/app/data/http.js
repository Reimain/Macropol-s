/* Every request leaves through here. No screen calls `fetch` directly.
 *
 * That is a structural rule rather than a convention, and the reason is the
 * same one the server has for putting the gateway in `Api.handle` rather than
 * in each route: a cross-cutting concern applied in each caller is a concern
 * applied in most callers. The trace id, the credential, the version headers
 * and the backoff are all things exactly one screen would forget.
 *
 * The chain, in order:
 *
 *   trace      a per-request id, echoed by the server onto the ledger entry, so
 *              an error in the browser links to the fact that caused it
 *   credential the API key, when one is held
 *   dispatch   fetch
 *   version    lift X-Slpie-Version / -Ledger-Version / -Stale into the cell
 *   classify   status → refusal | fault | type error | partial, once
 *
 * Reads and writes are separate calls. Only a command or an event invalidates a
 * cell, which is CQRS applied to the browser: a read cannot change what anybody
 * else is looking at, so a screen can refetch freely.
 */

import { begin, commit, ERROR, READY, REFUSED } from "../core/store.js";
import { classify, describe, REFUSAL, usable } from "../core/result.js";

let credential = "";
let counter = 0;

export function authenticate(key) {
  credential = key || "";
}

function trace() {
  counter += 1;
  return `ui-${Date.now().toString(36)}-${counter}`;
}

function headers(extra) {
  const sent = { "X-Slpie-Trace": trace(), ...extra };
  if (credential) sent.Authorization = `Bearer ${credential}`;
  return sent;
}

/** The version fields the server stamps, lifted off the response. */
function position(response) {
  return {
    version: Number(response.headers.get("X-Slpie-Version") || 0),
    ledger: Number(response.headers.get("X-Slpie-Ledger-Version") || 0),
    projection: response.headers.get("X-Slpie-Projection") || "",
    // Set by the service worker when it serves a cached answer offline. The
    // honesty rule does not weaken because the client is a laptop on a plane.
    stale: response.headers.get("x-slpie-stale") === "1",
    // Whether a device may hold this answer. Decided by the contract and
    // stamped by the API, so the browser's device tier, the service worker and
    // any edge cache read one statement rather than three.
    keep: response.headers.get("X-Slpie-Cacheable") === "1",
  };
}

async function send(path, options) {
  const response = await fetch(path, options);
  let body = null;
  try {
    body = await response.json();
  } catch (error) {
    body = null;   // an empty body is not a parse failure worth surfacing
  }
  return { response, body };
}

/**
 * A read. Lands in the store under `key`, ordered by version.
 *
 * Returns the cell rather than the raw body, so a caller cannot accidentally
 * use an answer the store rejected as stale.
 */
export async function query(key, path, { signal } = {}) {
  begin(key);
  try {
    const { response, body } = await send(path, {
      headers: headers({ Accept: "application/json" }),
      signal,
    });
    const kind = classify(response.status, body);
    if (kind) {
      return commit(key, {
        ...position(response),
        status: kind === REFUSAL ? REFUSED : ERROR,
        error: describe(response.status, body),
        fetchedAt: Date.now(),
      });
    }
    return commit(key, {
      ...position(response),
      status: READY,
      value: body,
      error: null,
      partial: false,
      fetchedAt: Date.now(),
    });
  } catch (error) {
    // A network failure is a fault the page can render, not an unhandled
    // rejection in a console nobody has open.
    return commit(key, {
      status: ERROR,
      error: {
        kind: "fault",
        className: "fault",
        heading: "Cannot reach the platform",
        message: String(error && error.message ? error.message : error),
      },
      fetchedAt: Date.now(),
    });
  }
}

/**
 * A write. Returns `{ok, status, body, error}` and touches no cell of its own —
 * the caller decides what a command invalidated, because only the caller knows.
 */
export async function command(path, payload = {}) {
  try {
    const { response, body } = await send(path, {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
    });
    const error = describe(response.status, body);
    return {
      ok: response.ok,
      status: response.status,
      body,
      error,
      // `/api/run` attaches the partial flow to a 400 on purpose. Reporting
      // only the failure would throw away the half of the answer that arrived.
      partial: usable(response.status, body),
      ...position(response),
    };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      body: null,
      error: {
        kind: "fault",
        className: "fault",
        heading: "Cannot reach the platform",
        message: String(error && error.message ? error.message : error),
      },
    };
  }
}
