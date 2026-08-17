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
