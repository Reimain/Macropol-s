/* The renderer seam, and the declared boundary for third parties.
 *
 * The navigation concept this tier serves arrived as a Three.js mock loading
 * `three.min.js` from a CDN. Ring 0 cannot do that: CSP is `script-src 'self'`,
 * a walk over every shipped file refuses an external origin, and there is no
 * build step. The wrong conclusion is "therefore no third-party renderer,
 * ever". The right one is the move this codebase already makes everywhere else
 * — `ObjectStore` with a filesystem default and S3 in ring 1, `TaskRunner` with
 * `InlineRunner` and Celery in ring 1, a plugin registry whose built-ins
 * register through the third-party path.
 *
 * So: a protocol, a native default, and third parties as declared plugins.
 *
 * ── What an engine is ────────────────────────────────────────────────────
 *
 *   name      what is drawing, for the console to report
 *   native    whether it needs anything not in this repository
 *   mount(canvas, scene)   take a surface and a scene; return nothing
 *   draw(camera)           paint one frame from a camera
 *   dispose()              release whatever was held
 *
 * ── `native` is machine-readable, not a comment ──────────────────────────
 *
 * "Marked as not air-gapped native" has to be a fact a program can check, or it
 * is marketing. Every engine states it, the console says which engine is
 * drawing and whether it is native, and a test asserts the default is native
 * while every vendored engine declares itself otherwise.
 *
 * The promise underneath is kept by a check rather than by intent: **the app
 * boots, renders, and passes the whole browser tier with `engine/vendor/`
 * deleted.** An engine that made itself necessary would fail that on the commit
 * that did it. It is the `gateway=None` argument (§30 step 6) applied to
 * rendering.
 *
 * ── Loading is dynamic, because a static import of an absent module is fatal ──
 *
 * `import "./vendor/x.js"` at the top of a module that ships without `vendor/`
 * takes the whole screen down. `resolve()` reaches for an engine at runtime and
 * falls back to the native one **with a stated reason** — the treatment §27
 * gives a missing binary and §3 gives a refused capability. A missing engine is
 * a capability gap, never a blank canvas.
 *
 * Nothing is vendored yet, and that is deliberate. The tier ships with the
 * protocol and the native renderer so the seam is proven by its own use rather
 * than asserted, which is invariant 6's argument applied to rendering. A third
 * party drops in afterwards for a measured reason — a frame rate the native
 * path could not reach — rather than speculatively. Vendoring 600KB before
 * knowing whether it is needed is how a dependency arrives without anyone
 * deciding to take it.
 */

import { canvas2d } from "./canvas2d.js";

/** The engines that ship. Vendored ones are added here by their wrapper. */
const ENGINES = new Map([[canvas2d.name, canvas2d]]);

/** The one that always works, with nothing else present. */
export const DEFAULT = canvas2d.name;

export function engines() {
  return [...ENGINES.values()];
}

export function register(engine) {
  const problem = invalid(engine);
  if (problem) throw new Error(`refusing to register an engine: ${problem}`);
  ENGINES.set(engine.name, engine);
  return engine;
}

/**
 * What an engine must be before it is allowed to draw anything.
 *
 * Checked at registration rather than at first frame: an engine that fails
 * halfway through a paint leaves a half-drawn canvas and a console message, and
 * the reader cannot tell that from a graph that genuinely looks like that.
 */
export function invalid(engine) {
  if (!engine || typeof engine !== "object") return "not an object";
  if (!engine.name) return "no name";
  if (typeof engine.native !== "boolean") {
    return `${engine.name} does not declare whether it is native`;
  }
  for (const method of ["mount", "draw", "dispose"]) {
    if (typeof engine[method] !== "function") {
      return `${engine.name} has no ${method}()`;
    }
  }
  return "";
}

/**
 * Resolve an engine by name, falling back to the native one and saying why.
 *
 * Returns `{engine, fallback, reason}`. `fallback` is true when the request
 * could not be met, and `reason` is the sentence a console shows — never an
 * empty canvas and never a silent substitution, which would look identical to
 * the real thing and be a different answer.
 */
export async function resolve(name = DEFAULT, { load = importer } = {}) {
  if (!name || name === DEFAULT) {
    return { engine: canvas2d, fallback: false, reason: "" };
  }
  if (ENGINES.has(name)) {
    return { engine: ENGINES.get(name), fallback: false, reason: "" };
  }

  try {
    const module = await load(name);
    const engine = module && (module.engine || module.default);
    const problem = invalid(engine);
    if (problem) {
      return fell(`${name} is present but unusable: ${problem}`);
    }
    // An engine may decline the machine it landed on — no WebGL, no GPU, a
    // locked-down browser. Asked before it is chosen rather than at first
    // frame, so the reader gets a stated fallback instead of a black canvas
    // and a console insisting it is drawing. The same treatment §27 gives a
    // missing binary and §3 gives a refused capability.
    const declined = typeof engine.available === "function" ? engine.available() : "";
    if (declined) {
      return fell(
        `${name} cannot run here — ${declined}. Drawing with ${DEFAULT}, `
        + `which needs nothing outside this repository.`,
      );
    }
    ENGINES.set(engine.name, engine);
    return { engine, fallback: false, reason: "" };
  } catch (error) {
    return fell(
      `${name} is not installed in this build — ${message(error)}. `
      + `Drawing with ${DEFAULT}, which needs nothing outside this repository.`,
    );
  }
}

function fell(reason) {
  return { engine: canvas2d, fallback: true, reason };
}

function message(error) {
  return String((error && error.message) || error || "no reason given");
}

/* Kept in one place so a test can substitute it without a network, and so the
 * only dynamic specifier in the tree is legible rather than buried in a call. */
function importer(name) {
  return import(`./vendor/${name}.js`);
}

/** What the console reports: which engine, and whether it is ours alone. */
export function describe(engine) {
  return {
    name: engine.name,
    native: engine.native,
    label: engine.native ? "native" : "not air-gapped native",
  };
}
