/* The service layer: one named function per thing a screen wants.
 *
 * Screens never build a URL. They ask for `findings("high")` and the path, the
 * cell key and the invalidation set are decided here — which is what makes
 * "which screen reads which route" a fact the manifest can state rather than
 * something you learn by grepping for string concatenation.
 *
 * Cell keys are the paths themselves. Two screens asking the same question share
 * an answer and its version, which is the point of holding one store rather than
 * one cache per screen.
 */

import { command, query } from "./http.js";
import { VERBS } from "./client.js";

function url(path, params = {}) {
  const search = new URLSearchParams(
    Object.entries(params).filter(([, value]) => value !== "" && value != null),
  ).toString();
  return search ? `${path}?${search}` : path;
}

/* --- reads -------------------------------------------------------------- */

export const status = () => query("status", "/api/status");
export const manifest = () => query("manifest", "/api/manifest");
export const station = () => query("station", "/api/station");
export const reconcile = () => query("reconcile", "/api/reconcile");
export const cycles = () => query("cycles", "/api/cycles");
export const integrity = () => query("integrity", "/api/integrity");
export const projections = () => query("projections", "/api/projections");
export const scenarios = () => query("scenarios", "/api/scenarios");
export const verbs = () => query("verbs", "/api/verbs");
export const manual = () => query("manual", "/api/manual");
export const routes = () => query("routes", "/api/routes");
export const screens = () => query("screens", "/api/screens");
export const streamStatus = () => query("stream-status", "/api/stream/status");

export const workspaces = () => query("admin-workspaces", "/api/admin/workspaces");
export const quota = (tenant = "") =>
  query(`quota:${tenant}`, url("/api/admin/quota", { tenant }));
export const datasets = (tenant = "", realm = "") =>
  query(`datasets:${tenant}/${realm}`, url("/api/admin/datasets", { tenant, realm }));

export const findings = (severity = "") =>
  query(`findings:${severity}`, url("/api/findings", { severity }));

export const graph = (limit = 200) =>
  query("graph", url("/api/graph", { limit }));

export const node = (id) => query(`node:${id}`, url("/api/node", { id }));

export const search = (q, limit = 20) =>
  query(`search:${q}`, url("/api/search", { q, limit }));

export const impact = (id, depth = 10, minConfidence = 0) =>
  query(`impact:${id}`, url("/api/impact", {
    id, depth, min_confidence: minConfidence,
  }));

export const history = (subject = "", limit = 100) =>
  query(`history:${subject}`, url("/api/history", { subject, limit }));

export const causation = (event) =>
  query(`causation:${event}`, url("/api/causation", { event }));

/* --- writes ------------------------------------------------------------- */

export const ask = (question) => command("/api/ask", { question });
export const plan = (question) => command("/api/plan", { question });
export const validate = (pipeline) => command("/api/compose/validate", { pipeline });
export const run = (pipeline, options = {}) =>
  command("/api/run", { pipeline, ...options });

export const fire = (scenario, params = {}) =>
  command("/api/scenario", { scenario, ...params });

export const setTarget = (target, confirmed = false) =>
  command("/api/target", { target, confirmed });

export const seal = (label) => command("/api/snapshot", { label });

/** One verb, directly. `upstream` continues a flow rather than starting one. */
export function verb(name, params = {}, upstream = null) {
  if (!VERBS[name]) {
    return Promise.resolve({
      ok: false,
      status: 404,
      error: {
        kind: "fault",
        className: "fault",
        heading: "Unknown verb",
        message: `this build has no verb \`${name}\``,
      },
    });
  }
  return command(`/api/v/${name}`, upstream ? { ...params, upstream } : params);
}
