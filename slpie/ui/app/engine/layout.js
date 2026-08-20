/* Deterministic placement, in three dimensions.
 *
 * The rule that already governs `ui/graph.js` does not change, it gains an
 * axis. **No force simulation** — a physics layout settles somewhere different
 * on every run, so the same graph is a different picture each time you open it:
 * you cannot point at it in a review, cannot compare two screenshots, and
 * cannot tell "the architecture changed" from "the simulation landed
 * elsewhere". Same graph in, same coordinates out, asserted by test, which is
 * the snapshot digest's property applied to the drawing rather than the data.
 *
 * Today's two-dimensional layout has three inputs: kind groups the columns,
 * degree orders the rows inside one, and a tall kind wraps into a second
 * column. Each becomes an axis:
 *
 *   kind    becomes Z   one lane per kind, in today's population order
 *   degree  becomes X   position along the lane, busiest first
 *   wrap    becomes Y   a lane taller than `LANE` stacks rather than spilling
 *
 * ── Regions ──────────────────────────────────────────────────────────────
 *
 * A region is a **declared** security boundary from the manifest, never one
 * inferred from structure — the platform marking its own homework is the thing
 * the whole evidence model exists to avoid. Regions group lanes along Z with a
 * gap between them, which does two jobs at once: it makes a boundary visible as
 * a place rather than as a legend entry, and it makes region adjacency a
 * Z-neighbour relation, which is exactly what the colouring in the next step
 * needs.
 *
 * Everything outside every declared boundary shares one region, placed last. It
 * is not "no region" — it is the rest of the estate, and it is usually most of
 * it.
 */

/** How many nodes a lane holds before it stacks in Y. */
export const LANE = 11;

export const STEP_X = 26;
export const STEP_Y = 34;
export const LANE_DEPTH = 40;

/** Between one region's lanes and the next. Wide enough to read as a gap. */
export const REGION_GAP = 90;

/** Where everything outside every declared boundary lives. */
export const UNBOUNDED = "estate";

/**
 * Place a graph.
 *
 * `regionOf(node)` names the declared boundary a node sits in, or "" for none.
 * It is injected rather than looked up here for the same reason the surveyor
 * takes a predicate: the manifest owns the membership rule, and a second copy
 * of it in the renderer is a copy that will disagree.
 */
export function place(nodes, edges, { regionOf = () => "", lane = LANE } = {}) {
  const degree = new Map();
  for (const edge of edges || []) {
    degree.set(edge.src, (degree.get(edge.src) || 0) + 1);
    degree.set(edge.dst, (degree.get(edge.dst) || 0) + 1);
  }

  // One group per (region, kind). A Map preserves insertion order, and
  // insertion order here is the order the nodes arrived in, which is not stable
  // enough to place by — so every ordering below is re-derived explicitly.
  const groups = new Map();
  const regions = new Map();
  for (const node of nodes || []) {
    const region = regionOf(node) || UNBOUNDED;
    const key = `${region} ${node.kind}`;
    if (!groups.has(key)) groups.set(key, { region, kind: node.kind, members: [] });
    groups.get(key).members.push(node);
    regions.set(region, (regions.get(region) || 0) + 1);
  }

  // Declared regions first, by population then by name; the estate always last,
  // because it is the backdrop the boundaries sit against rather than a peer.
  const order = [...regions.entries()]
    .sort((left, right) => {
      if (left[0] === UNBOUNDED) return 1;
      if (right[0] === UNBOUNDED) return -1;
      return right[1] - left[1] || left[0].localeCompare(right[0]);
    })
    .map((entry) => entry[0]);
  const rank = new Map(order.map((name, index) => [name, index]));

  const lanes = [...groups.values()].sort((left, right) =>
    rank.get(left.region) - rank.get(right.region)
    || right.members.length - left.members.length
    || left.kind.localeCompare(right.kind));

  const placed = new Map();
  let depth = 0;
  let previous = null;

  for (const strip of lanes) {
    if (previous !== null && strip.region !== previous) depth += REGION_GAP;
    previous = strip.region;
    strip.z = depth;
    depth += LANE_DEPTH;

    // Busiest first, then name, then id — a *total* order. Without the id the
    // many nodes that share a name (`index`, `orders`) would sort arbitrarily
    // and the same-graph-same-picture guarantee would quietly not hold.
    strip.members.sort((left, right) =>
      (degree.get(right.id) || 0) - (degree.get(left.id) || 0)
      || String(left.name || "").localeCompare(String(right.name || ""))
      || String(left.id).localeCompare(String(right.id)));

    strip.members.forEach((node, index) => {
      placed.set(node.id, {
        node,
        id: node.id,
        kind: node.kind,
        region: strip.region,
        degree: degree.get(node.id) || 0,
        x: (index % lane) * STEP_X,
        y: Math.floor(index / lane) * STEP_Y,
        z: strip.z,
      });
    });
  }

  return {
    placed,
    lanes,
    edges: edges || [],
    regions: order.map((name) => ({
      name,
      count: regions.get(name),
      declared: name !== UNBOUNDED,
      lanes: lanes.filter((strip) => strip.region === name).map((strip) => strip.kind),
    })),
    extent: extentOf(placed),
  };
}

/** The bounding box, so a camera can frame it without measuring twice. */
export function extentOf(placed) {
  const points = [...placed.values()];
  if (!points.length) {
    return { min: { x: 0, y: 0, z: 0 }, max: { x: 0, y: 0, z: 0 } };
  }
  const axis = (name, pick) => pick(...points.map((point) => point[name]));
  return {
    min: { x: axis("x", Math.min), y: axis("y", Math.min), z: axis("z", Math.min) },
    max: { x: axis("x", Math.max), y: axis("y", Math.max), z: axis("z", Math.max) },
  };
}

/**
 * Which declared regions touch, by an edge crossing between them.
 *
 * Emitted here because placement is where the crossings are already in hand,
 * and it is the input the region colouring takes: two regions joined by an edge
 * must not share a hue, and nothing else about them matters to that decision.
 */
export function adjacency(placed, edges) {
  const touching = new Map();
  for (const edge of edges || []) {
    const from = placed.get(edge.src);
    const to = placed.get(edge.dst);
    if (!from || !to || from.region === to.region) continue;
    for (const pair of [[from.region, to.region], [to.region, from.region]]) {
      if (!touching.has(pair[0])) touching.set(pair[0], new Set());
      touching.get(pair[0]).add(pair[1]);
    }
  }
  return touching;
}
