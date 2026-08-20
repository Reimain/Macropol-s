/* Colouring, as the graph-colouring problem it actually is.
 *
 * ── Regions, not nodes ───────────────────────────────────────────────────
 *
 * The instinct is to colour nodes. That produces a confetti field: twenty
 * thousand marks in ten hues says nothing, because a hue only carries meaning
 * when the thing wearing it is a *place* you can name. So the colouring runs
 * over **regions** — declared security boundaries from the manifest — and the
 * constraint is the classic one: two regions that touch must not share a hue,
 * or the boundary between them stops being visible at exactly the moment it
 * matters.
 *
 * Welsh–Powell, because it is the right size of algorithm for this. Order the
 * regions by how many others they touch, then sweep the list once per colour
 * assigning that colour wherever it is legal. It is not optimal — graph
 * colouring is NP-hard and an exact answer is not worth a millisecond here —
 * but it is deterministic, it is about twenty lines, and its bound (one more
 * than the maximum degree) is comfortably inside a ten-hue palette for any
 * estate anybody has declared boundaries for.
 *
 * ── The overflow is counted, never wrapped ───────────────────────────────
 *
 * An earlier version of this took `index % HUES.length`. With seven regions
 * against a six-hue palette that put one hue on two *adjacent* regions and said
 * nothing — the picture was wrong and looked fine, which is the worst failure
 * mode a visualisation has. The modulo is gone. If the palette runs out, the
 * regions that could not be coloured are returned in `overflow` and the caller
 * reports them, exactly as `slpie context` reports an indeterminate verdict
 * rather than guessing one.
 *
 * ── Saturation is reserved for severity ──────────────────────────────────
 *
 * Region hues are muted, near 0.26 saturation; every status colour sits above
 * 0.43, and a test compares the two. That reservation is what makes the one
 * vivid thing on a flight canvas always a finding. Colour is never the only
 * channel either: shape carries kind (`glyph.js`), the panel carries the word,
 * and a colour-blind reader loses nothing that was only ever in the hue.
 *
 * Colours are named as **tokens**, not resolved here. The theme axis lives in
 * CSS and the renderer resolves each token once at mount, so this module stays
 * pure and testable with no DOM — the same split `camera.js` makes for the same
 * reason.
 */

/** The hue tokens, in assignment order. Golden-angle spaced; see `tokens.css`. */
export const HUES = [
  "--flight-hue-1", "--flight-hue-2", "--flight-hue-3", "--flight-hue-4",
  "--flight-hue-5", "--flight-hue-6", "--flight-hue-7", "--flight-hue-8",
  "--flight-hue-9", "--flight-hue-10",
];

/** Everything outside every declared boundary. Deliberately not a hue. */
export const ESTATE = "--flight-estate";

/** What an uncoloured region falls back to, and what `overflow` explains. */
export const UNCOLOURED = ESTATE;

/**
 * Welsh–Powell over region adjacency.
 *
 * `regions` are names; `adjacency` is the `Map(name -> Set(name))` that
 * `layout.adjacency()` produces. Returns:
 *
 *   assigned   Map(region -> token)
 *   overflow   regions the palette could not reach, in order
 *   used       how many hues the answer actually needed
 *
 * The estate is excluded from the problem rather than given a hue. It is the
 * backdrop the declared boundaries are read against, it touches almost
 * everything, and colouring it would both waste a hue and make the unremarkable
 * majority compete with the thing somebody took the trouble to declare.
 */
export function colour(regions, adjacency, { hues = HUES } = {}) {
  const subjects = [...new Set(regions)].filter((name) => name && name !== "estate");

  // Busiest first, ties broken by name so the assignment is total and two runs
  // over one graph produce one picture.
  const touching = (name) => (adjacency.get(name) || new Set()).size;
  subjects.sort((left, right) => touching(right) - touching(left) || left.localeCompare(right));

  const assigned = new Map();
  for (const hue of hues) {
    for (const name of subjects) {
      if (assigned.has(name)) continue;
      const neighbours = adjacency.get(name) || new Set();
      let clash = false;
      for (const neighbour of neighbours) {
        if (assigned.get(neighbour) === hue) { clash = true; break; }
      }
      if (!clash) assigned.set(name, hue);
    }
  }

  const overflow = subjects.filter((name) => !assigned.has(name));
  return {
    assigned,
    overflow,
    used: new Set(assigned.values()).size,
    /** The sentence the console shows when the palette ran out. */
    shortfall: overflow.length
      ? `${overflow.length} region(s) share the neutral because ${hues.length} `
        + `hues were not enough: ${overflow.join(", ")}`
      : "",
  };
}

/** The token for one region, including the two cases that are not hues. */
export function tokenFor(region, assigned) {
  if (!region || region === "estate") return ESTATE;
  return assigned.get(region) || UNCOLOURED;
}

/**
 * Whether two adjacent regions ended up sharing a hue.
 *
 * Exported because it is the property, not an implementation detail: a
 * colouring is correct exactly when this is empty, and the test asserts on it
 * rather than on the algorithm's internals.
 */
export function clashes(assigned, adjacency) {
  const found = [];
  for (const [name, neighbours] of adjacency) {
    for (const neighbour of neighbours) {
      if (name >= neighbour) continue;               // report each pair once
      const left = assigned.get(name);
      if (left && left === assigned.get(neighbour)) {
        found.push(`${name} and ${neighbour} both ${left}`);
      }
    }
  }
  return found.sort();
}
