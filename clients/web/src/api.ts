/* The one place this shell talks to the platform.
 *
 * Everything goes through the generated client, so a route that changed shape
 * is a TypeScript compile error here rather than a runtime 404 somebody reports
 * from production. That is the whole reason `slpie contract --typescript`
 * exists, and it only pays off if nothing calls `fetch` directly — so nothing
 * does, and a test asserts it.
 *
 * ── There is no synthetic fallback, deliberately ─────────────────────────
 *
 * An earlier version of the Flight screen built nine hundred fake nodes when it
 * had nothing to draw. It looked convincing, which is precisely the problem: a
 * screen that fabricates data when the API is down shows something *plausible*
 * at the moment a reader most needs to know the truth. This platform's entire
 * claim is that it distinguishes what it observed from what it inferred, and a
 * demo generator inside the client is that claim being broken by the product
 * that makes it.
 *
 * So an empty answer stays empty, an error stays an error, and both say so.
 */

import { SlpieClient } from "./slpie-client";

export const client = new SlpieClient({ baseUrl: baseUrl(), token });

/* The credential, read per request rather than captured once.
 *
 * The gateway does the rest — `Api.handle` identifies the caller from this one
 * header and every refusal downstream of it names what would allow the call.
 * There is deliberately no login form here and no session in this shell: §16's
 * rule is that the live guard is not reimplemented for a second transport, and
 * the same reasoning covers identity. A shell that minted its own session would
 * be a second identity path with its own bugs.
 *
 * It comes from `sessionStorage` rather than `localStorage`: a token that
 * outlives the tab on a shared machine is a credential nobody remembers
 * leaving behind. Both accessors throw in a browser configured to block site
 * data, so both are guarded — an unreadable store means *no credential*, which
 * the gateway will refuse legibly, and never a crash on the way to the first
 * paint. */
export const CREDENTIAL = "slpie.token";

function token(): string | null {
  try {
    return window.sessionStorage.getItem(CREDENTIAL);
  } catch {
    return null;
  }
}

/** Hand the shell a credential for this tab, or clear it with an empty string. */
export function authenticate(value: string): void {
  try {
    if (value) window.sessionStorage.setItem(CREDENTIAL, value);
    else window.sessionStorage.removeItem(CREDENTIAL);
  } catch {
    // A browser that will not store it is not a failure worth raising: the
    // request simply goes out unauthenticated and the gateway says so.
  }
}

function baseUrl(): string {
  // Same origin in production — the built bundle is served by the platform. In
  // development Vite serves the bundle and the platform is elsewhere, so the
  // origin is configurable and defaults to where `slpie ui` listens.
  const configured = import.meta.env?.VITE_SLPIE_URL;
  if (configured) return String(configured);
  return import.meta.env?.DEV ? "http://127.0.0.1:8765" : "";
}

export type Loaded<T> = {
  value: T | null;
  loading: boolean;
  /** The refusal or the fault, in the words the platform used. */
  error: string;
  /** Whether the platform answered and had nothing — not the same as an error. */
  empty: boolean;
  /** The refusal in full, when the platform refused rather than failed. A 403
    * rendered as a fault teaches people that policy is a bug, so the two are
    * kept apart all the way to the card. */
  denied: Refusal | null;
};

export const idle = <T,>(): Loaded<T> => ({
  value: null, loading: true, error: "", empty: false, denied: null,
});

/**
 * Run one read and shape the outcome the way every screen needs it.
 *
 * Four states, not two. `loading`, an answer, an *empty* answer and a failure
 * are four different things a reader acts on differently, and collapsing empty
 * into failure is how a console ends up claiming an environment is broken when
 * it is merely new.
 */
export async function load<T>(read: () => Promise<T>): Promise<Loaded<T>> {
  try {
    const value = await read();
    return {
      value,
      loading: false,
      error: "",
      empty: value === null || value === undefined || isEmpty(value),
      denied: null,
    };
  } catch (error) {
    const denied = refusal(error);
    return {
      value: null,
      loading: false,
      denied: denied.refused ? denied : null,
      // A refusal is an answer with a reason. The generated client already
      // attaches `refused` and the platform's own sentence; passing it through
      // unedited is what lets the refusal card say what would allow the call.
      error: denied.message,
      empty: false,
    };
  }
}

function isEmpty(value: unknown): boolean {
  if (Array.isArray(value)) return value.length === 0;
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    if (Array.isArray(record.nodes)) return record.nodes.length === 0;
    return Object.keys(record).length === 0;
  }
  return false;
}

export function message(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error ?? "the platform did not say why");
}

export function refused(error: unknown): boolean {
  return Boolean(error && typeof error === "object" && (error as any).refused === true);
}

/** A refusal, in the shape a card renders: the sentence and the way out. */
export type Refusal = {
  refused: boolean;
  message: string;
  /** Which gateway step stopped it — the §26 rule, applied to admission. */
  stage: string;
  /** What would allow the call, when the platform knows. */
  obligation: string;
  retryAfter: string;
};

export function refusal(error: unknown): Refusal {
  const held = (error || {}) as Record<string, any>;
  return {
    refused: refused(error),
    message: message(error),
    stage: String(held.stage || ""),
    obligation: String(held.obligation || ""),
    retryAfter: String(held.retryAfter || ""),
  };
}
