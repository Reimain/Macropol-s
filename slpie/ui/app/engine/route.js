/* The route is a query result, never a generated tour.
 *
 * The mock this view came from picked a random walk of length 6–15, because it
 * had no data behind it. We have the real thing: `impact` is reverse
 * reachability in SQL (§12), and it returns distance *and* a propagated minimum
 * confidence per node. That is the rail. The flight is `impact --id <node>`
 * rendered as motion — an answer, in sequence — rather than an animation over a
 * graph.
 *
 * ── Speed is the evidence ────────────────────────────────────────────────
 *
 * This is the one part of the rejected prototypes that survived contact. The
 * traversal moves quickly across well-attested ground and drags where the
 * platform is guessing, so the *feel* of the ride is the confidence of the
 * answer and the reader notices the slow stretch before reading the number. A
 * hop reached only through a 0.4 dynamic load should look and feel different
 * while you are travelling it.
 *
 * ── Short paths do not fly ───────────────────────────────────────────────
 *
 * Real estates produce two-hop answers constantly, and a cinematic traverse
 * over two hops is silly. Below `FLOOR` hops the route reports `animates:
 * false` and the screen draws it statically. The animation has to earn its
 * place on each answer rather than being applied because the feature exists.
 *
 * Pure: an `impact` payload in, a rail out. No DOM, no timers, no canvas.
 */

/** Fewer hops than this and the route is a diagram, not a ride. */
export const FLOOR = 4;

/** Seconds per hop across fully-attested ground. */
export const FAST = 0.55;

/** Seconds per hop where the platform is guessing. */
export const SLOW = 1.9;

/** Below this a hop is inference rather than something that was read. */
export const INFERRED = 0.7;

/**
 * Turn an `ImpactResult` payload into a rail.
 *
 * `payload` is `ImpactResult.to_dict()`: `{root, impacted: [{node_id, distance,
 * confidence, display, kind, certain}], total, summary}`.
 *
 * Ordered by distance, then by confidence descending, then by id — a *total*
 * order, so the same answer produces the same ride every time. Without the id
 * the many nodes sharing a distance and a confidence would sort arbitrarily and
 * two runs of one query would travel two different routes.
 */
export function rail(payload, { floor = FLOOR } = {}) {
  const reached = (payload && payload.impacted) || [];
  const hops = [...reached]
    .sort((left, right) =>
      left.distance - right.distance
      || right.confidence - left.confidence
      || String(left.node_id).localeCompare(String(right.node_id)))
    .map((item, index) => ({
      index,
      id: item.node_id,
      display: item.display || item.node_id,
      kind: item.kind || "",
      distance: item.distance,
      confidence: item.confidence,
      inferred: item.confidence < INFERRED,
      // The confidence floor *so far*. This is the number that says how much of
      // the answer is inference at this point in the journey, and it can only
      // fall — a chain is exactly as strong as its weakest link, which is the
      // same rule L7 applies when it propagates a minimum rather than a product.
      floor: 0,
      seconds: 0,
    }));

  let carried = 1;
  for (const hop of hops) {
    carried = Math.min(carried, hop.confidence);
    hop.floor = carried;
    // Speed is the evidence: fast across what was read, slow across what was
    // inferred, interpolated so the change is felt rather than announced.
    hop.seconds = SLOW + (FAST - SLOW) * clamp(hop.confidence);
  }

  const weakest = hops.length ? hops[hops.length - 1].floor : 1;
  return {
    root: (payload && payload.root) || "",
    hops,
    length: hops.length,
    deepest: hops.reduce((far, hop) => Math.max(far, hop.distance), 0),
    /** The answer's confidence, which is its weakest hop and nothing else. */
    floor: weakest,
    inferred: hops.filter((hop) => hop.inferred).length,
    seconds: hops.reduce((total, hop) => total + hop.seconds, 0),
    /** Whether this is worth flying, or is a list wearing a 3D scene. */
    animates: hops.length >= floor,
    summary: summarise(hops, weakest, floor),
  };
}

function summarise(hops, floor, minimum) {
  if (!hops.length) return "nothing depends on this";
  if (hops.length < minimum) {
    return `${hops.length} hop(s) — too short to fly, drawn as a path`;
  }
  const inferred = hops.filter((hop) => hop.inferred).length;
  let text = `${hops.length} hops, bounded at ${floor.toFixed(2)}`;
  if (inferred) text += `; ${inferred} of them inferred rather than read`;
  return text;
}

function clamp(value) {
  return value <= 0 ? 0 : value >= 1 ? 1 : value;
}

/**
 * Where the camera should be at a given moment along the rail.
 *
 * Returns `{index, into, hop, next, done}` — which hop is being approached, and
 * how far into it, so a caller interpolates between two positions rather than
 * snapping. `into` is 0..1 within the current hop.
 *
 * Time is passed in rather than read from a clock, which is what lets the whole
 * ride be exercised without waiting for it — the same argument
 * `slpie/simulator/clock.py` makes for the bitemporal tests.
 */
export function at(route, seconds) {
  const hops = route.hops;
  if (!hops.length) return { index: -1, into: 0, hop: null, next: null, done: true };

  let remaining = Math.max(0, seconds);
  for (let index = 0; index < hops.length; index += 1) {
    const hop = hops[index];
    if (remaining < hop.seconds) {
      return {
        index,
        into: hop.seconds ? remaining / hop.seconds : 1,
        hop,
        next: hops[index + 1] || null,
        done: false,
      };
    }
    remaining -= hop.seconds;
  }
  const last = hops[hops.length - 1];
  return { index: hops.length - 1, into: 1, hop: last, next: null, done: true };
}

/**
 * The ticks a hop rail draws: one per hop, filled as it is passed.
 *
 * Red where a hop carries a critical, which is the one place this module reads
 * severity — and it reads it from what the caller supplies rather than deciding
 * it, because severity belongs to governance and not to a route.
 */
export function ticks(route, seconds, severities = {}) {
  const now = at(route, seconds);
  return route.hops.map((hop) => ({
    id: hop.id,
    passed: now.done || hop.index < now.index,
    current: !now.done && hop.index === now.index,
    inferred: hop.inferred,
    severity: severities[hop.id] || "",
  }));
}
