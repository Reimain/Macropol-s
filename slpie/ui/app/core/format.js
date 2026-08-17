/* Rendering values as text. No DOM, no fetch — the leaf of the tier graph. */

/** A long identifier, shortened without losing which one it is.
 *
 *  Keeping both ends matters: node ids share a long prefix, so truncating from
 *  the right alone makes every row look identical. */
export function short(value, length = 12) {
  const text = String(value ?? "");
  if (text.length <= length * 2 + 1) return text;
  return `${text.slice(0, length)}…${text.slice(-6)}`;
}

export function count(value) {
  return Number(value ?? 0).toLocaleString("en");
}

export function percent(value) {
  return `${Math.round(Number(value ?? 0) * 100)}%`;
}

/** Confidence, to two places. Never rounded to a whole number: 0.90 and 0.95
 *  are different claims and displaying both as "1" erases the distinction the
 *  evidence ladder exists to make. */
export function confidence(value) {
  return Number(value ?? 0).toFixed(2);
}

export function ago(seconds) {
  const delta = Math.max(0, Math.round(Number(seconds ?? 0)));
  if (delta < 60) return `${delta}s ago`;
  if (delta < 3600) return `${Math.round(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.round(delta / 3600)}h ago`;
  return `${Math.round(delta / 86400)}d ago`;
}

/** `file:line`, the citation shape every piece of evidence terminates in. */
export function cite(location) {
  if (!location) return "";
  const uri = String(location.uri || "").replace(/^file:\/\//, "");
  return location.line ? `${uri}:${location.line}` : uri;
}

export function title(value) {
  return String(value ?? "")
    .replace(/[-_]/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

/* --- certainty ----------------------------------------------------------
 *
 * The confidence ladder (§10) has fourteen evidence kinds and a continuous
 * score. A reader scanning a table cannot compare fourteen numbers, and does
 * not need to: what they need is *how this was arrived at*, which is four
 * answers, not fourteen.
 *
 * The bands are cut at the ladder's own joints rather than at round numbers.
 * 0.85 is where build config and container manifests sit — things written down
 * by a person or a tool that was there. 0.60 is the cap the platform already
 * applies to evidence drawn only from reflection, dynamic loading and name
 * heuristics, so it is the line between recorded and inferred whether this
 * file draws it or not. 0.40 is dynamic load: a guess with a reason.
 */
export const CERTAINTY = ["surveyed", "recorded", "inferred", "guessed", "unknown"];

export function certainty(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    // Not "zero confidence" — no confidence *was computed*, which is a
    // different claim and gets a different mark.
    return "unknown";
  }
  const score = Number(value);
  if (score >= 0.95) return "surveyed";
  if (score >= 0.85) return "recorded";
  if (score >= 0.60) return "inferred";
  if (score > 0) return "guessed";
  return "unknown";
}

/** What the mark under a claim means, in one phrase. For the key. */
export const CERTAINTY_MEANS = {
  surveyed: "pinned or traced — read directly",
  recorded: "declared in a manifest or an import",
  inferred: "joined from configuration or registration",
  guessed: "reflection or a name; the platform caps this at 0.60",
  unknown: "no confidence was computed",
};
