/* The camera along the route, and the clearance rule that broke a prototype.
 *
 * ── The defect this module exists to make impossible ─────────────────────
 *
 * The third prototype's panels were right and its 3D was broken in one
 * specific way: the eye sat at y=52 while the solids were 26–143 units tall
 * standing at y=0, so it rendered from *inside* the buildings. The horizon
 * never painted and every surface washed to one colour. Nothing failed; it just
 * looked wrong, and looking wrong is not a thing a test catches unless
 * something states the rule.
 *
 * So the rule is stated and checked **every frame**: the eye clears the tallest
 * solid within the corridor it is passing through. Not "usually" and not "at
 * the start" — a route that climbs into a dense region would sink the camera
 * mid-flight, which is exactly the case that produced the bug.
 *
 * ── The road is a surface, not a line ────────────────────────────────────
 *
 * A path drawn as a line is a diagram of somewhere else; a path drawn as a
 * surface is somewhere you are. `edges()` returns the two rails of that surface
 * so a renderer can fill between them, and the width narrows as the confidence
 * floor drops — the road visibly thins where the platform is guessing, and the
 * reader slows down without being told to.
 *
 * Pure: positions in, positions out. No canvas, no DOM, no clock.
 */

import { at } from "./route.js";
import { look, vector } from "./camera.js";

/** How far above the tallest thing in the corridor the eye must sit. */
export const CLEARANCE = 18;

/** How far ahead the camera looks. Short enough to feel like driving. */
export const LOOKAHEAD = 1.4;

/** Half-width of the road where every hop is fully attested. */
export const LANE = 14;

/** Half-width where the path is entirely inference. Narrower on purpose. */
export const NARROW = 5;

/** How far either side of the road counts as "the corridor". */
export const CORRIDOR = 90;

function lerp(from, to, share) {
  return from + (to - from) * share;
}

function between(a, b, share) {
  return vector(lerp(a.x, b.x, share), lerp(a.y, b.y, share), lerp(a.z, b.z, share));
}

/**
 * Where each hop sits in the world.
 *
 * Taken from the placement rather than invented, so the ride travels the same
 * space the survey shows. A hop whose node was not placed is dropped with the
 * count reported, never silently skipped — a route that quietly lost a hop
 * would travel a shorter path than the answer it claims to be.
 */
export function path(route, placed) {
  const points = [];
  let missing = 0;
  for (const hop of route.hops) {
    const point = placed.get(hop.id);
    if (!point) {
      missing += 1;
      continue;
    }
    points.push({ ...hop, x: point.x, y: point.y, z: point.z });
  }
  return { points, missing };
}

/**
 * The tallest solid near a point, so the camera can clear it.
 *
 * Height follows degree — a busy node is a taller building — which is the same
 * statement the survey makes with mark size, made in the third dimension.
 */
export function ceiling(placed, near, { corridor = CORRIDOR, height = byDegree } = {}) {
  let tallest = 0;
  for (const point of placed.values()) {
    const dx = point.x - near.x;
    const dz = point.z - near.z;
    if (dx * dx + dz * dz > corridor * corridor) continue;
    const top = point.y + height(point);
    if (top > tallest) tallest = top;
  }
  return tallest;
}

export function byDegree(point) {
  // Logarithmic, for the reason the interest weighting is: the difference
  // between one link and ten is enormous and between two hundred and two
  // hundred and ten is nothing anybody acts on.
  return 12 + 26 * Math.min(1, Math.log2(1 + (point.degree || 0)) / Math.log2(65));
}

/**
 * The camera at one moment of the ride.
 *
 * Returns the camera plus the numbers the panel shows, so a caller never has to
 * recompute what the ride already knows: which hop, how far in, the confidence
 * floor so far, and whether the clearance rule had to lift the eye.
 *
 * The `@param` line is not decoration. A shell that type-checks this module
 * infers its signature from the JSDoc, and a default of `null` alone infers as
 * *only* null — so the placement a caller must pass for the clearance rule to
 * work would be rejected by its own type. Stating the type is what keeps the
 * inferred contract the same as the real one.
 *
 * @param {any} route
 * @param {any} laid
 * @param {number} seconds
 * @param {{width?: number, height?: number, clearance?: number,
 *          lookahead?: number, placed?: Map<string, any> | null}} [options]
 */
export function ride(route, laid, seconds, {
  width = 1, height = 1, clearance = CLEARANCE, lookahead = LOOKAHEAD, placed = null,
} = {}) {
  const points = laid.points;
  if (points.length < 2) {
    return null;
  }

  const now = at(route, seconds);
  const index = Math.min(Math.max(0, now.index), points.length - 2);
  const here = points[index];
  const ahead = points[index + 1];
  const share = now.done ? 1 : now.into;

  const ground = between(here, ahead, share);
  const target = between(
    here, points[Math.min(index + 2, points.length - 1)],
    Math.min(1, share + lookahead / 2),
  );

  // The clearance rule, every frame. `raised` is reported rather than hidden,
  // because a camera that had to climb is a fact about the terrain and the
  // panel can say so.
  const floor = placed ? ceiling(placed, ground) : 0;
  const wanted = ground.y + clearance;
  const eye = vector(ground.x, Math.max(wanted, floor + clearance), ground.z);

  return {
    camera: look(eye, vector(target.x, eye.y * 0.35 + target.y * 0.65, target.z), {
      width, height, fov: Math.PI / 2.4, near: 1,
    }),
    index,
    into: share,
    hop: now.hop,
    next: now.next,
    done: now.done,
    floor: now.hop ? now.hop.floor : route.floor,
    raised: eye.y > wanted,
    clearedBy: eye.y - floor,
  };
}

/**
 * The two rails of the road surface, and how wide it is at each hop.
 *
 * Width carries the confidence floor: the road thins as the answer becomes
 * inference. That is evidence in the geometry, which is the graph screen's
 * existing signature — stroke says how an edge was learned — carried into the
 * third dimension.
 */
export function edges(laid, { lane = LANE, narrow = NARROW } = {}) {
  const points = laid.points;
  const left = [];
  const right = [];

  // The heading a segment arrived on, carried forward. A step that is purely
  // vertical — the lane wrapped, so x and z are unchanged and only y moved —
  // has no direction in the XZ plane, and computing one from it collapsed the
  // road to zero width at that hop. A vertical segment still has a heading:
  // the one it came in on.
  let heading = { dx: 1, dz: 0 };

  for (let index = 0; index < points.length; index += 1) {
    const here = points[index];
    const other = points[index + 1] || points[index - 1] || here;
    let dx = other.x - here.x;
    let dz = other.z - here.z;
    const size = Math.hypot(dx, dz);
    if (size < 1e-6) {
      ({ dx, dz } = heading);
    } else {
      dx /= size;
      dz /= size;
      heading = { dx, dz };
    }

    const half = narrow + (lane - narrow) * clamp(here.floor);
    left.push(vector(here.x - dz * half, here.y, here.z + dx * half));
    right.push(vector(here.x + dz * half, here.y, here.z - dx * half));
  }
  return { left, right };
}

function clamp(value) {
  return value <= 0 ? 0 : value >= 1 ? 1 : value;
}

/**
 * The reduced-motion answer: the same hops, stepped rather than flown.
 *
 * Not a lesser path. `prefers-reduced-motion` is honoured everywhere in this
 * product, and a fallback that lost the ordering or the confidence would be a
 * *different answer* rather than the same one presented calmly. So this returns
 * the identical sequence with a position per hop, and a caller steps it.
 */
export function stepped(route, laid) {
  return laid.points.map((point, index) => ({
    index,
    id: point.id,
    display: point.display,
    distance: point.distance,
    confidence: point.confidence,
    floor: point.floor,
    inferred: point.inferred,
    at: vector(point.x, point.y, point.z),
    of: laid.points.length,
    last: index === laid.points.length - 1,
  }));
}
