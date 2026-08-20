/* Aggregation — what you did *not* ask about, drawn honestly.
 *
 * ── This is not primarily a performance trick ────────────────────────────
 *
 * It reads like one, because the numbers are dramatic: the prototype this
 * replaces went from 27fps drawing twenty thousand dots to 60fps drawing seven
 * hundred and eighty marks. But the frame rate is a *consequence*. The reason
 * to aggregate is that a mark per node is a lie about what the reader can see —
 * twenty thousand overlapping dots communicate "there is a lot of data", which
 * is a texture rather than a fact, and it is a texture everybody has already
 * seen a thousand times.
 *
 * Google Maps draws cities at country zoom, not buildings. Not because
 * buildings are slow: because at that distance a building is not the unit of
 * anything. Same here. A lane whose whole population lands inside eighteen
 * pixels is one thing at that distance, and drawing it as one thing that
 * *carries its count* tells the reader more than drawing forty overlapping
 * circles that carry nothing.
 *
 * So the contract is: **nothing is dropped, and every mark says how many nodes
 * are inside it.** Focus plus context, which is the same promise `Elision`
 * makes on the Python side of the degree-of-interest field.
 *
 * ── Two passes, and the second is the one the prototype needed ───────────
 *
 * 1. **Semantic.** A lane — one kind inside one region — whose projected extent
 *    is smaller than `FLOOR` collapses to a single mark at its centroid. This
 *    is the Maps rule, and it is driven by screen-space extent so descending
 *    reveals members rather than magnifying a blob.
 *
 * 2. **Deconfliction.** After that, marks can still land on top of each other:
 *    two small lanes at the same distance, or a near lane seen edge-on. The
 *    prototype had no answer for this and they simply piled up, which looks
 *    exactly like one dark mark and is not one. So a second pass buckets what
 *    survived into a grid of `CELL` pixels and merges anything sharing a cell
 *    into a coarser tier.
 *
 * Bucketing is **single-pass**. The prototype rescanned all n once per band,
 * which was 1.2 million iterations a frame and the actual reason it was slow.
 * One pass, one map, O(n).
 *
 * ── An aggregate never claims what it does not have ──────────────────────
 *
 * A cluster spanning two regions has no region, and is drawn neutral rather
 * than borrowing one of them. Same for kind. Severity is the only attribute
 * that propagates upward, and it propagates as the **worst** contained — a
 * cluster containing one critical is a critical cluster, because the whole
 * point of reserving saturation for severity is that a finding is never hidden
 * by the thing that swallowed it.
 *
 * Pure, like the rest of the tier: screen coordinates in, marks out, no
 * rendering context anywhere.
 */

/** Screen-space extent below which a lane becomes one mark. */
export const FLOOR = 18;

/** Grid cell for deconfliction. Two marks closer than this are one mark. */
export const CELL = 14;

/** Severity, worst first, so "the worst contained" is a table lookup. */
const RANK = { critical: 4, high: 3, medium: 2, low: 1, info: 0 };

function worse(left, right) {
  if (!left) return right;
  if (!right) return left;
  return (RANK[right] || 0) > (RANK[left] || 0) ? right : left;
}

/** Unanimous or nothing: an aggregate must not borrow one member's identity. */
function agreed(values) {
  const first = values[0];
  return values.every((value) => value === first) ? first : "";
}

function mark(members, tier) {
  let x = 0;
  let y = 0;
  let depth = Infinity;
  let radius = 0;
  let severity = "";
  let count = 0;

  for (const member of members) {
    x += member.x;
    y += member.y;
    // The nearest member's depth, because that is what decides whether this
    // mark is in front of another one. Averaging would let a cluster with one
    // distant member sink behind something it visibly overlaps.
    if (member.depth < depth) depth = member.depth;
    if (member.radius > radius) radius = member.radius;
    severity = worse(severity, member.severity);
    count += member.count || 1;
  }

  return {
    tier,
    x: x / members.length,
    y: y / members.length,
    depth,
    // A cluster is drawn a little larger than its largest member, so "several
    // things here" reads as weight rather than as one thing that moved.
    radius: tier === "node" ? radius : radius * (1 + Math.min(0.6, members.length / 20)),
    count,
    severity,
    region: agreed(members.map((member) => member.region)),
    kind: agreed(members.map((member) => member.kind)),
    members: members.length,
  };
}

/**
 * Reduce projected points to the marks that should actually be drawn.
 *
 * `points` are already in screen space: `{id, x, y, depth, radius, region,
 * kind, severity}`. Returns:
 *
 *   marks       what to draw, each carrying `count` and `tier`
 *   assignment  Map(node id -> index into `marks`), so edges know where their
 *               endpoints went and an edge inside one mark is not drawn at all
 *   represented how many nodes the marks stand for — counted, not modelled
 *   tiers       how many marks of each tier, for the console to report
 */
export function aggregate(points, { floor = FLOOR, cell = CELL } = {}) {
  const all = [...points];
  if (!all.length) {
    return {
      marks: [], assignment: new Map(), represented: 0,
      tiers: { node: 0, lane: 0, cluster: 0 },
    };
  }

  // -- pass one: a lane smaller than `floor` on screen is one thing ---------
  const lanes = new Map();
  for (const point of all) {
    const key = `${point.region}|${point.kind}`;
    if (!lanes.has(key)) lanes.set(key, []);
    lanes.get(key).push(point);
  }

  const survivors = [];
  const inside = new Map();          // survivor index -> the ids it stands for
  for (const key of [...lanes.keys()].sort()) {
    const members = lanes.get(key);
    const width = extent(members, "x");
    const height = extent(members, "y");

    if (members.length > 1 && Math.max(width, height) < floor) {
      inside.set(survivors.length, members.map((member) => member.id));
      survivors.push(mark(members, "lane"));
    } else {
      for (const member of members) {
        inside.set(survivors.length, [member.id]);
        survivors.push({ ...member, tier: "node", count: 1, members: 1 });
      }
    }
  }

  // -- pass two: whatever still overlaps becomes one coarser mark -----------
  //
  // One pass over the survivors, one bucket map. The prototype rescanned every
  // point once per band and paid 1.2 million iterations a frame for it.
  const buckets = new Map();
  survivors.forEach((survivor, index) => {
    const key = `${Math.floor(survivor.x / cell)}:${Math.floor(survivor.y / cell)}`;
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(index);
  });

  const marks = [];
  const assignment = new Map();
  // Sorted so two runs over one scene produce one picture, exactly as the
  // layout and the colouring do.
  for (const key of [...buckets.keys()].sort()) {
    const indices = buckets.get(key);
    const members = indices.map((index) => survivors[index]);
    const position = marks.length;

    marks.push(members.length === 1 ? members[0] : mark(members, "cluster"));
    for (const index of indices) {
      for (const id of inside.get(index)) assignment.set(id, position);
    }
  }

  const tiers = { node: 0, lane: 0, cluster: 0 };
  for (const item of marks) tiers[item.tier] = (tiers[item.tier] || 0) + 1;

  return { marks, assignment, represented: all.length, tiers };
}

function extent(members, axis) {
  let low = Infinity;
  let high = -Infinity;
  for (const member of members) {
    if (member[axis] < low) low = member[axis];
    if (member[axis] > high) high = member[axis];
  }
  return high - low;
}

/**
 * The edges worth drawing once the marks are decided.
 *
 * An edge whose endpoints landed in the same mark is *internal* and is not
 * drawn: it would be a line from a mark to itself, and at twenty thousand nodes
 * most edges are internal, so this is also where the edge pass stops being the
 * expensive half.
 *
 * Parallel edges between the same pair of marks collapse to one, with a count,
 * because forty overlapping lines and one line look identical and only the
 * second is honest about it.
 */
export function bundle(edges, assignment) {
  const bundled = new Map();
  let internal = 0;

  for (const edge of edges || []) {
    const from = assignment.get(edge.src);
    const to = assignment.get(edge.dst);
    if (from === undefined || to === undefined) continue;
    if (from === to) {
      internal += 1;
      continue;
    }
    // Normalised, so the pair is the identity: an edge and its reverse are one
    // line on screen, and storing whichever orientation happened to arrive
    // first would make the record disagree with the key that found it.
    const [low, high] = from < to ? [from, to] : [to, from];
    const held = bundled.get(`${low}:${high}`);
    if (held) held.count += 1;
    else bundled.set(`${low}:${high}`, { from: low, to: high, count: 1 });
  }

  return { edges: [...bundled.values()], internal };
}
