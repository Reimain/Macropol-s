/* Inference, staged — the reasoning arriving in order rather than in a table.
 *
 * Every hop of an `impact` result already carries why it is there: the evidence
 * kind, the file and line, the confidence that follows, and the gap that limits
 * it. Nothing here needs inventing. What it needs is **staging**, so arriving at
 * a hop reveals the reasoning for *that* hop rather than dumping the whole
 * path's provenance at the end.
 *
 * That is the difference between a reference document and learning something. A
 * table of provenance is the former, and nobody reads a reference document to
 * find out how a system thinks. Watching the evidence model applied, one hop at
 * a time, is how a reviewer learns it — which is what "understand and learn
 * faster" actually asks for.
 *
 *     approaching  payments-api
 *       ← static import, services/payments/client.py:41       0.90
 *       ← lockfile pin, package-lock.json                     1.00
 *       = holding speed. two independent observations agree.
 *
 *     approaching  vault-sdk
 *       ← dynamic load, services/vault/loader.py:88           0.40
 *       = slowing. this hop is inferred, not read.
 *          the answer is now bounded at 0.40.
 *
 * ── The rule that keeps it honest ────────────────────────────────────────
 *
 * **Every line resolves to something that was computed.** A line with no
 * evidence behind it is not written, and `lines()` marks each one with where it
 * came from so a test can walk them. The mock this view descends from displayed
 * "nodes in frustum" as `NODE_COUNT * (1 - zoom * 0.88)` — a formula presented
 * as a measurement — and a surface whose whole claim is that it distinguishes a
 * known thing from a guessed one cannot display a number nobody computed.
 *
 * Pure: evidence in, sentences out.
 */

/** How an evidence kind reads in a sentence, and what it implies about speed. */
const READS = {
  lockfile_pin: "lockfile pin",
  runtime_trace: "runtime trace",
  manifest_declared: "manifest declaration",
  declared: "environment manifest",
  static_import: "static import",
  iac_declaration: "infrastructure declaration",
  build_config: "build config",
  container_manifest: "container manifest",
  generated_code: "generated code",
  annotation: "annotation",
  config_reference: "config reference",
  di_registration: "dependency injection",
  reflection: "reflection",
  dynamic_load: "dynamic load",
  name_heuristic: "name heuristic",
};

/** Kinds that are inference rather than something that was read directly. */
const INFERRED = new Set(["reflection", "dynamic_load", "name_heuristic"]);

export function reads(kind) {
  return READS[kind] || String(kind || "unrecorded").replace(/_/g, " ");
}

export function isInference(kind) {
  return INFERRED.has(kind);
}

/**
 * Where one piece of evidence points, rendered the way the rest of the product
 * renders it — a path and a line, never a bare identifier.
 */
export function cite(evidence) {
  const uri = (evidence && (evidence.uri || (evidence.location || {}).uri)) || "";
  const line = (evidence && (evidence.line || (evidence.location || {}).line)) || 0;
  const trimmed = uri.replace(/^file:\/\//, "");
  if (!trimmed) return "";
  return line ? `${trimmed}:${line}` : trimmed;
}

/**
 * The narration for one hop.
 *
 * `evidence` is whatever the platform recorded for this subject. An empty list
 * is not a failure and is not padded with a reassuring sentence: it produces a
 * line saying the platform has nothing on the record for this hop, which is a
 * true statement and an actionable one.
 */
export function hop(item, evidence = []) {
  const lines = [];

  lines.push({
    role: "approaching",
    text: item.display || item.id,
    from: "route",
  });

  for (const found of evidence) {
    const where = cite(found);
    lines.push({
      role: "evidence",
      text: reads(found.kind) + (where ? `, ${where}` : ""),
      confidence: found.confidence,
      inference: isInference(found.kind),
      // What this line rests on, so a test can require that every rendered
      // sentence traces to a record rather than to prose.
      from: found.id || where || found.kind,
    });
  }

  lines.push(verdict(item, evidence));
  return lines;
}

function verdict(item, evidence) {
  const independent = new Set(
    evidence.map((found) => `${found.kind}${cite(found)}`),
  ).size;

  if (!evidence.length) {
    return {
      role: "verdict",
      text: "nothing is recorded for this hop — it is reached, and unexplained",
      from: "route",
      slowing: true,
    };
  }
  if (item.inferred) {
    return {
      role: "verdict",
      text: `slowing. this hop is inferred, not read. `
        + `the answer is now bounded at ${item.floor.toFixed(2)}.`,
      from: "route",
      slowing: true,
    };
  }
  if (independent > 1) {
    return {
      role: "verdict",
      text: `holding speed. ${independent} independent observations agree.`,
      from: "route",
      slowing: false,
    };
  }
  return {
    role: "verdict",
    text: `holding speed. one observation, at ${item.confidence.toFixed(2)}.`,
    from: "route",
    slowing: false,
  };
}

/**
 * The whole narration up to a moment, so the panel reads as a transcript.
 *
 * Only what has been *reached* — the point is that the reasoning arrives in
 * order. Rendering the rest greyed out ahead of time would turn it back into
 * the table this exists to replace.
 */
export function upto(route, index, evidenceFor = () => []) {
  const lines = [];
  for (const item of route.hops) {
    if (item.index > index) break;
    lines.push(...hop(item, evidenceFor(item.id) || []));
  }
  return lines;
}

/**
 * The counted telemetry the panel shows.
 *
 * Hops travelled and remaining, and the confidence floor so far. **Not speed in
 * km/h**: there is no distance and no time in a dependency graph, and a
 * plausible-looking number for either would be decoration wearing an
 * instrument's clothes. `drawn` comes from the renderer's own tally, never from
 * a formula over the node count.
 */
export function readout(route, index, drawn = null) {
  const travelled = Math.min(index + 1, route.length);
  return {
    travelled,
    remaining: Math.max(0, route.length - travelled),
    floor: route.hops[Math.max(0, Math.min(index, route.length - 1))]?.floor ?? route.floor,
    inferred: route.hops.filter((item) => item.index <= index && item.inferred).length,
    marks: drawn ? drawn.marks : null,
    represents: drawn ? drawn.represented : null,
  };
}
