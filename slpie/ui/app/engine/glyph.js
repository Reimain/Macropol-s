/* Shape carries kind, so colour never has to.
 *
 * ── Why shape and not another hue ────────────────────────────────────────
 *
 * There are twenty-nine node kinds and ten region hues, and the hues are
 * already spent on regions. Colouring by kind as well would put two meanings on
 * one channel, which is the reliable way to make both unreadable — and it would
 * fail outright for the reader who cannot separate them, the greyscale print,
 * and the screenshot pasted into a ticket, which is where most of these end up.
 *
 * So: **hue is region, saturation is severity, shape is kind.** Three channels,
 * three meanings, no overlap. It is the same rule `ui/graph.js` already follows
 * with its stroke patterns, extended by one axis rather than replaced.
 *
 * ── Six shapes for twenty-nine kinds ─────────────────────────────────────
 *
 * Twenty-nine distinguishable silhouettes do not exist at ten pixels. What does
 * exist is the grouping the domain already declares: `slpie/domain/node.py`
 * lays its kinds out in six commented families, and those families are the
 * distinction a reader actually navigates by — "is that a service or a data
 * thing" long before "is that a table or an entity". So shape says family, and
 * the label says the exact kind. Six silhouettes are told apart at a glance;
 * twenty-nine are told apart by nobody.
 *
 * ── Above a threshold, and honestly below it ─────────────────────────────
 *
 * A glyph smaller than a few pixels is a dot with extra vertices: it costs the
 * path and delivers no distinction. Below `FLOOR` the renderer draws a dot,
 * and that is not a degradation to apologise for — it is the correct mark for
 * something too far away to identify, and it matches what aggregation says
 * about the same distance.
 *
 * Pure, like `camera.js` and `palette.js`: this returns geometry, never touches
 * a rendering context, and is therefore checkable without a browser.
 */

/** Radius in device-independent pixels below which a glyph is drawn as a dot. */
export const FLOOR = 3.4;

/** The six families, in the order `slpie/domain/node.py` declares them. */
export const FAMILIES = ["code", "runtime", "data", "delivery", "organisation", "unknown"];

/** kind -> family. Read off the domain enum's own grouping, not invented here. */
export const FAMILY_OF = {
  repository: "code", workspace: "code", package: "code",
  module: "code", component: "code",

  service: "runtime", api: "runtime", event: "runtime", queue: "runtime",
  runtime_process: "runtime", web_app: "runtime", device_class: "runtime",

  database: "data", table: "data", entity: "data", schema: "data",
  dataset: "data", ai_model: "data",

  environment: "delivery", pipeline: "delivery", deployment: "delivery",
  cloud_resource: "delivery", configuration: "delivery",
  feature_flag: "delivery", secret: "delivery",

  team: "organisation", domain: "organisation", policy: "organisation",
  license: "organisation", external_provider: "organisation",
};

/* One silhouette per family, chosen so the outlines differ in *count of
 * corners* rather than in proportion: a square and a wide rectangle are the
 * same shape at ten pixels, whereas a triangle and a hexagon are not. */
export const SHAPE_OF = {
  code: "square",          // 4 corners — a thing on disk, with edges
  runtime: "triangle",     // 3 — something pointed at a direction of travel
  data: "cylinder",        // a drum, the only shape everybody already reads as a store
  delivery: "diamond",     // 4, rotated — a decision or a placement
  organisation: "hexagon", // 6 — a group, the shape of a cell in every org chart
  unknown: "circle",       // 0 — we do not know what this is, and say so
};

export function familyOf(kind) {
  return FAMILY_OF[kind] || "unknown";
}

export function shapeOf(kind) {
  return SHAPE_OF[familyOf(kind)];
}

/**
 * The outline of one glyph, centred on the origin.
 *
 * Returns `{shape, points}`. `points` is empty for a shape the renderer draws
 * with an arc — a circle has no useful polygonal approximation at these sizes,
 * and forcing one would trade a correct curve for sixteen line segments.
 *
 * `radius` is the circumradius, so every family occupies the same visual weight
 * and a triangle does not read as smaller than a hexagon of the same nominal
 * size — which it does if you scale by side length instead.
 */
export function outline(kind, radius) {
  const shape = typeof kind === "string" && SHAPE_OF[kind] ? SHAPE_OF[kind] : shapeOf(kind);
  if (radius < FLOOR) return { shape: "dot", points: [] };

  switch (shape) {
    case "square":
      return { shape, points: corners(4, radius, Math.PI / 4) };
    case "triangle":
      return { shape, points: corners(3, radius, -Math.PI / 2) };
    case "diamond":
      return { shape, points: corners(4, radius, -Math.PI / 2) };
    case "hexagon":
      return { shape, points: corners(6, radius, -Math.PI / 2) };
    case "cylinder":
      // A drum read end-on: a rectangle with the top edge lifted, which is
      // enough to say "store" at twelve pixels and cheap enough at twenty
      // thousand.
      return {
        shape,
        points: [
          { x: -radius, y: -radius * 0.62 }, { x: radius, y: -radius * 0.62 },
          { x: radius, y: radius * 0.62 }, { x: -radius, y: radius * 0.62 },
        ],
      };
    default:
      return { shape: "circle", points: [] };
  }
}

function corners(count, radius, start) {
  const points = [];
  for (let index = 0; index < count; index += 1) {
    const angle = start + (index * 2 * Math.PI) / count;
    points.push({ x: radius * Math.cos(angle), y: radius * Math.sin(angle) });
  }
  return points;
}

/**
 * The severity token for a node, or "" when nothing was raised against it.
 *
 * The only place saturation is spent. Named as a token so the renderer resolves
 * it once against the same palette every other screen uses — a reader who
 * learnt "critical is that red" on the Findings screen must not relearn it
 * here, and the way to guarantee that is to use the identical custom property
 * rather than a colour that matches today.
 */
export function severityToken(severity) {
  switch (severity) {
    case "critical": return "--crit";
    case "high": return "--bad";
    case "medium": return "--warn";
    case "low": return "--ok";
    default: return "";
  }
}
