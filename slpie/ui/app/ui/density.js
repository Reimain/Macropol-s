/* The two registers, and the theme, as a stored choice.
 *
 * `bench` is dense and tabular — the register for somebody working a list all
 * day, and the one the interface opens in. `reading` is calm: more leading,
 * fewer rows, a capped measure, for reading an answer rather than scanning a
 * table. Both are the same layout with a different token set, selected by
 * `data-density` on the root element.
 *
 * A deep link may override for one visit (`?density=reading`), which is what
 * makes a link shareable to somebody who reads differently.
 */

import { emit } from "../core/bus.js";
import { h } from "../core/dom.js";

export const REGISTERS = ["bench", "reading"];
export const THEMES = ["dark", "light"];

const KEY_DENSITY = "slpie.density";
const KEY_THEME = "slpie.theme";

function store(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch (error) {
    // Private browsing, or storage disabled. The choice still applies to this
    // page; it simply will not survive a reload. That is a smaller failure than
    // refusing to switch at all.
  }
}

function stored(key) {
  try {
    return window.localStorage.getItem(key) || "";
  } catch (error) {
    return "";
  }
}

export function density() {
  return document.documentElement.dataset.density || "bench";
}

export function theme() {
  return document.documentElement.dataset.theme || "dark";
}

export function setDensity(value) {
  if (!REGISTERS.includes(value)) return;
  document.documentElement.dataset.density = value;
  store(KEY_DENSITY, value);
  emit("density", value);
}

export function setTheme(value) {
  if (!THEMES.includes(value)) return;
  document.documentElement.dataset.theme = value;
  store(KEY_THEME, value);
  emit("theme", value);
}

/**
 * Apply the stored choices. Called by `boot.js` before the app loads.
 *
 * `index.html` hardcodes the defaults on the root element, so the common case
 * paints correctly on the first frame. A reader whose choice differs from the
 * default sees one frame of the default, and that is accepted rather than
 * solved: PWA-first plus no build step plus `script-src 'self'` leaves no legal
 * inline bootstrap, and injecting a `<style>` to avoid a single frame would
 * trade a flash for a hole in the content security policy.
 */
export function apply(search = window.location.search) {
  const asked = new URLSearchParams(search);
  const chosen = asked.get("density") || stored(KEY_DENSITY);
  const wanted = asked.get("theme") || stored(KEY_THEME);

  if (REGISTERS.includes(chosen)) document.documentElement.dataset.density = chosen;
  if (THEMES.includes(wanted)) document.documentElement.dataset.theme = wanted;
}

/** The control, for the header. Two buttons, each saying what it switches to. */
export function control() {
  const densityButton = h("button", {
    type: "button",
    class: "chip usable",
    title: "switch reading register",
    onclick: () => {
      setDensity(density() === "bench" ? "reading" : "bench");
      label();
    },
  });
  const themeButton = h("button", {
    type: "button",
    class: "chip usable",
    title: "switch theme",
    onclick: () => {
      setTheme(theme() === "dark" ? "light" : "dark");
      label();
    },
  });

  function label() {
    densityButton.textContent = density() === "bench" ? "dense" : "calm";
    themeButton.textContent = theme() === "dark" ? "dark" : "light";
  }
  label();

  return h("div", { class: "row", role: "group", "aria-label": "appearance" },
    densityButton, themeButton);
}
