/* The words this reader uses, and the one function that applies them.
 *
 * `t("finding", {plural: true, title: true})` → "Findings", or "Risks" for a
 * tenant whose profile says so. Every user-visible noun the interface renders
 * from data goes through here, so adapting terminology is a lookup rather than
 * a fork.
 *
 * Two rules carried over from the kernel, because a split vocabulary is worse
 * than one wrong word:
 *
 * **A missing term renders as its key.** A render is not the place to fail. The
 * profile is where a bad key raises, because that is authored and checkable —
 * `slpie/context/lexicon.py` refuses it there.
 *
 * **A control's word is never renamed.** Severities, gap kinds, verdicts and
 * target states are protected server-side, so anything arriving here is already
 * either a name a context may choose or a word it may not. This module does not
 * re-check that; it could not, and duplicating the rule would give it somewhere
 * to be different.
 *
 * **`core/` imports nothing.** The default vocabulary is baked into
 * `data/client.js`, and this module cannot reach it — the browser follows the
 * kernel's ring rule one level down, and reaching upward for a constant is how
 * a tier stops being reusable. So `shell.js`, the composition root that may
 * import everything, seeds it at boot with `seed(LEXICON)`, and `load()`
 * refreshes it from `GET /api/lexicon` once the context is known.
 *
 * Before it is seeded every lookup returns its key. That is the same fallback
 * a missing term gets, so an unseeded module is ugly rather than broken.
 */

import { emit } from "./bus.js";

let words = {};
let profile = "default";

/** The word for a key. Falls back to the key, never throws. */
export function t(key, { plural = false, title = false } = {}) {
  const term = words[key];
  const word = term ? (plural ? term.plural : term.word) : key;
  return title ? word.charAt(0).toUpperCase() + word.slice(1) : word;
}

/** A term's one-sentence gloss, for a tooltip. Empty when nobody wrote one. */
export function gloss(key) {
  return (words[key] && words[key].gloss) || "";
}

/**
 * Resolve a label that may be a term reference.
 *
 * `"{finding.plural|title}"` → "Findings". A literal passes through untouched,
 * which is what lets a block carry either without the manifest declaring which
 * — most labels are literals and forcing every one through a term key would
 * mean inventing terms for "Window" and "Burst".
 */
export function label(text) {
  if (!text || !text.includes("{")) return text || "";
  return text.replace(/\{([^}]+)\}/g, (whole, body) => {
    const [reference, ...flags] = body.split("|");
    const [key, form] = reference.split(".");
    if (!words[key]) return whole;
    return t(key, {
      plural: form === "plural",
      title: flags.includes("title"),
    });
  });
}

export function current() {
  return profile;
}

/**
 * Install the baked default. Called once by `shell.js` before the first draw.
 *
 * Takes the compact shape `data/client.js` carries — `{key: {word, plural,
 * gloss}}` — rather than the API's envelope, because the two arrive from
 * different places and converting at the boundary is cheaper than teaching this
 * module both.
 */
export function seed(baked) {
  if (!baked) return;
  words = { ...baked };
}

/** Swap in a context's words. Announced, so drawn screens redraw. */
export function apply(body) {
  if (!body || !body.terms) return;
  words = {};
  for (const [key, term] of Object.entries(body.terms)) {
    words[key] = {
      word: term.word,
      plural: term.plural || term.word,
      gloss: term.gloss || "",
    };
  }
  profile = body.name || "default";
  emit("lexicon", profile);
}

/**
 * Fetch the lexicon for the current context.
 *
 * Failure is silent on purpose: the baked default is a complete, correct
 * vocabulary, and a console that refuses to render because it could not confirm
 * its own labels would be trading a cosmetic problem for an outage.
 */
export async function load(fetcher = fetch) {
  try {
    const response = await fetcher("/api/lexicon");
    if (!response.ok) return;
    apply(await response.json());
  } catch (error) {
    /* keep the baked words */
  }
}
