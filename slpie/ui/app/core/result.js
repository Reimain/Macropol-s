/* One map from an HTTP status to how it should read.
 *
 * The taxonomy the server sends is already right — `api.py` distinguishes a
 * refusal from a fault from a type error from a partial flow — and the whole
 * job here is not to flatten it back into "something went wrong".
 *
 * The distinction that matters most is refusal versus fault, and it is a design
 * decision rather than a colour choice: **a 403 is never red.** A refusal is the
 * platform working. Rendering it in the danger colour teaches people that
 * policy is a bug, and then they file tickets about their own permissions
 * instead of asking for the grant they need.
 */

export const REFUSAL = "refusal";
export const FAULT = "fault";
export const TYPE_ERROR = "type-error";
export const ABSENT = "absent";
export const THROTTLED = "throttled";
export const NO_ENVIRONMENT = "no-environment";
export const PARTIAL = "partial";

/** What kind of answer this is. `body` is the decoded JSON, `status` the code. */
export function classify(status, body) {
  const payload = body || {};

  if (status === 403 || payload.refused) return REFUSAL;
  if (status === 409 || payload.type === "NoEnvironment") return NO_ENVIRONMENT;
  if (status === 429) return THROTTLED;
  if (status === 404) return ABSENT;
  if (status === 400 && payload.type === "TypeMismatch") return TYPE_ERROR;
  if (status === 400 && payload.ok === false) return PARTIAL;
  if (status >= 500) return FAULT;
  if (status >= 400) return FAULT;
  return null;
}

/** The sentence, the class to render it with, and what to do about it. */
export function describe(status, body) {
  const kind = classify(status, body);
  const payload = body || {};
  const message = payload.error || payload.explanation || "";

  switch (kind) {
    case REFUSAL:
      return {
        kind,
        className: "refusal",
        heading: "Refused",
        message,
        // `Decision.explain()` already says what would allow it, so the
        // interface repeats the server's own sentence rather than inventing a
        // second, worse explanation of the same rule.
        obligation: payload.obligation || "",
      };
    case NO_ENVIRONMENT:
      return {
        kind, className: "empty", heading: "No environment open", message,
      };
    case THROTTLED:
      return {
        kind,
        className: "refusal",
        heading: "Rate limited",
        message,
        retryAfter: Number(payload.retry_after || 0),
        tier: payload.tier || "",
      };
    case TYPE_ERROR:
      // Rendered inline at the offending stage, never as a modal: the reader
      // needs to see which stage, and a modal hides exactly that.
      return { kind, className: "inline", heading: "", message };
    case ABSENT:
      return { kind, className: "empty", heading: "Not here", message };
    case PARTIAL:
      return { kind, className: "fault", heading: "Stopped part-way", message };
    case FAULT:
      return {
        kind,
        className: "fault",
        heading: "Platform fault",
        message: message || "the server failed without saying why",
        detail: payload.type || "",
      };
    default:
      return null;
  }
}

/** True when the answer is usable even though the request did not fully succeed.
 *
 *  `/api/run` deliberately attaches the partial flow to a 400, so blanking the
 *  panel throws away the half of the answer that did arrive. */
export function usable(status, body) {
  return status === 400 && body && typeof body === "object" && "flow" in body;
}
