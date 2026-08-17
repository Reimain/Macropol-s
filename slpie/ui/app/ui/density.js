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
export const THEMES = ["light", "dark"];

/* The defaults, and the single place they are named in JavaScript. `index.html`
 * hardcodes the same pair on the root element so the first frame paints
 * correctly; these are what the accessors fall back to when the attribute is
 * missing, and the two must agree. They did not: this said "dark" while the
 * markup said light, so the first click on the theme control computed the
 * switch from the wrong current value and appeared to do nothing. */
const DEFAULT_DENSITY = "bench";
const DEFAULT_THEME = "light";

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
  return document.documentElement.dataset.density || DEFAULT_DENSITY;
}

export function theme() {
  return document.documentElement.dataset.theme || DEFAULT_THEME;
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

/**
 * The control, for the top bar. Two buttons, each **naming what it switches
 * to** rather than what is currently on.
 *
 * That distinction is the whole of it, and it was the other way round: the
 * theme button read "light" while the page was already light, which is a
 * control that appears to have been pressed and ignored. A button is a verb. A
 * label showing current state belongs on a pill, and this is not one.
 */
export function control() {
  const densityButton = h("button", {
    type: "button",
    class: "chip usable",
    onclick: () => {
      setDensity(density() === "bench" ? "reading" : "bench");
      label();
    },
  });
  const themeButton = h("button", {
    type: "button",
    class: "chip usable",
    onclick: () => {
      setTheme(theme() === "dark" ? "light" : "dark");
      label();
    },
  });

  function label() {
    const calm = density() === "reading";
    densityButton.textContent = calm ? "Dense" : "Calm";
    densityButton.title = calm
      ? "switch to the dense register, for scanning a list"
      : "switch to the calm register, for reading an answer";

    const dark = theme() === "dark";
    themeButton.textContent = dark ? "Light" : "Dark";
    themeButton.title = `switch to the ${dark ? "light" : "dark"} theme`;
  }
  label();

  return h("div", { class: "row", role: "group", "aria-label": "appearance" },
    densityButton, themeButton);
}
