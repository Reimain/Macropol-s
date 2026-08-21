/* Pointing the platform at a folder — the first thing anybody does, and the
 * thing the interface had no control for at all.
 *
 * ── What actually happens, and why it is shown ───────────────────────────
 *
 * "Open a folder" is not a button that does a hidden thing. It is a
 * composition, and the console shows it before running it:
 *
 *     discover --path <folder> | link | findings
 *     │                          │      └ rank what the rules raise about it
 *     │                          └ join lockfile pins to manifest ranges,
 *     │                            resolving each package to one identity
 *     └ walk the tree, and record an observation per artifact read —
 *       each carrying the file and line it came from
 *
 * That is the §24 thesis at the one moment it matters most: the reader sees the
 * reasoning before paying for it, each stage says why it is there, and nothing
 * is a black box labelled "Scan". It is also literally true — the same string is
 * what gets posted to `/api/run`, so the explanation cannot drift from the
 * behaviour.
 *
 * ── The browser will not tell us the path, and pretending otherwise is worse
 *    than saying so ─────────────────────────────────────────────────────────
 *
 * A folder dropped onto a web page yields its *name* and the relative paths
 * inside it. It does **not** yield an absolute path: browsers withhold it
 * deliberately, and no flag turns that off. Meanwhile the crawler runs
 * server-side and walks the *server's* filesystem, so the name alone is not
 * enough to open anything.
 *
 * So the text field is authoritative and the drop zone assists it: dropping
 * fills in the folder's name and says plainly that the rest has to be typed,
 * rather than appearing to work and then reading the wrong directory. A control
 * that looks like it accepts a drop and silently does nothing with it is the
 * worse failure, because the reader concludes the platform is broken instead of
 * concluding they need to type a path.
 */

import { h } from "../core/dom.js";
import { run } from "../data/queries.js";
import { cite } from "../core/format.js";

/** The composition an open actually runs. One string, shown and executed. */
export function pipelineFor(path, { severity = "" } = {}) {
  const quoted = /\s/.test(path) ? JSON.stringify(path) : path;
  const last = severity ? `findings --severity ${severity}` : "findings";
  return `discover --path ${quoted} | link | ${last}`;
}

/** What each stage is for, in the reader's terms rather than the type graph's. */
const WHY = {
  discover: "walk the tree and record an observation per artifact read, each "
    + "carrying the file and line it came from",
  link: "join lockfile pins to manifest ranges, resolving each package to one "
    + "identity",
  findings: "rank what the rules raise against what was resolved",
};

function explain(path) {
  return h("div", { class: "plan" },
    h("code", { class: "mono" }, pipelineFor(path || "…")),
    h("ol", { class: "stages" },
      ["discover", "link", "findings"].map((name) =>
        h("li", {},
          h("span", { class: "mono" }, name),
          h("span", { class: "muted" }, WHY[name])))));
}

/**
 * The opener.
 *
 * `onOpened(result)` is called with the run's result so the caller can redraw
 * from it. Nothing is cached here: the store owns state, and a control that
 * kept its own copy of the answer would be a second read model.
 */
export function opener({ onOpened = () => {} } = {}) {
  const field = h("input", {
    type: "text",
    id: "environment-path",
    "aria-label": "path to the folder to read",
    placeholder: "/path/to/your/repository",
    autocomplete: "off",
    spellcheck: false,
    class: "mono",
  });

  const note = h("p", { class: "muted drop-note" });
  const outcome = h("div", {});
  const preview = h("div", {});
  let busy = false;

  const draw = () => {
    preview.replaceChildren(explain(field.value.trim()));
  };
  field.addEventListener("input", draw);
  draw();

  const go = h("button", {
    type: "button", class: "go",
    onclick: async () => {
      const path = field.value.trim();
      if (!path || busy) return;
      busy = true;
      go.disabled = true;
      go.textContent = "Reading…";
      outcome.replaceChildren(h("p", { class: "muted" },
        "Walking the tree. Observations arrive on the live feed as they are "
        + "recorded."));

      const result = await run(pipelineFor(path));

      busy = false;
      go.disabled = false;
      go.textContent = "Read this folder";
      outcome.replaceChildren(report(result));
      onOpened(result);
    },
  }, "Read this folder");

  const zone = h("div", {
    class: "dropzone",
    tabindex: "0",
    role: "group",
    "aria-label": "drop a folder to fill in its name",
    ondragover: (event) => {
      event.preventDefault();
      zone.classList.add("over");
    },
    ondragleave: () => zone.classList.remove("over"),
    ondrop: (event) => {
      event.preventDefault();
      zone.classList.remove("over");
      const name = droppedName(event);
      if (!name) {
        note.textContent = "That did not look like a folder.";
        return;
      }
      // The name is all the browser will give up. Say so, and put it where the
      // reader can complete it rather than silently doing nothing with it.
      field.value = field.value.trim().replace(/\/+$/, "") + "/" + name;
      field.focus();
      draw();
      note.textContent = `Dropped “${name}”. Browsers do not hand a web page a `
        + `folder's full path, so add the directories above it — the platform `
        + `reads this path on the machine running the server.`;
    },
  },
  h("div", { class: "what" }, "Drop a folder here, or type its path"),
  h("div", { class: "row" }, field, go),
  note);

  return h("div", { class: "opener stack" }, zone, preview, outcome);
}

/** The dropped folder's name, or "" if what arrived was not a folder.
 *
 *  `webkitGetAsEntry` is the only interface that distinguishes a directory from
 *  a file at drop time, and it is supported everywhere despite the prefix. The
 *  `items` list is live and is emptied when the handler yields, which is why it
 *  is read synchronously here rather than after an await. */
function droppedName(event) {
  const items = event.dataTransfer && event.dataTransfer.items;
  if (items && items.length) {
    for (const item of items) {
      const entry = item.webkitGetAsEntry && item.webkitGetAsEntry();
      if (entry && entry.isDirectory) return entry.name;
    }
  }
  const files = (event.dataTransfer && event.dataTransfer.files) || [];
  // A folder dragged in a browser without the entry API arrives as its files,
  // each carrying a relative path whose first segment is the folder.
  for (const file of files) {
    const relative = file.webkitRelativePath || "";
    if (relative.includes("/")) return relative.split("/")[0];
  }
  return "";
}

/** What the run produced, per stage — so "what happens next" is answered by the
 *  screen rather than by the reader guessing. */
function report(result) {
  if (result.error && !result.partial) {
    return h("div", { class: "fault" },
      h("h3", {}, result.error.heading || "Could not read that folder"),
      h("p", {}, result.error.message || ""));
  }

  const body = result.body || {};
  const flow = body.flow || body;
  const gaps = flow.gaps || [];
  const stages = flow.stages || [];

  return h("div", { class: "stack" },
    h("div", { class: "row" },
      h("span", { class: `status ${body.ok === false ? "bad" : "ok"}` },
        body.ok === false ? "stopped part-way" : "read"),
      stages.length
        ? h("span", { class: "mono muted" }, stages.join(" | "))
        : null,
      h("span", { class: "muted" }, flow.kind || "")),

    body.ok === false && body.error
      ? h("p", { class: "muted" }, body.error)
      : null,

    // The gaps come first, not last. A folder the platform could only partly
    // read produces an answer that looks complete, and the gap is the only
    // thing standing between the reader and trusting it.
    gaps.length
      ? h("div", { class: "stack" },
        h("h3", {}, `${gaps.length} gap${gaps.length === 1 ? "" : "s"} limit this`),
        gaps.map((gap) => h("div", { class: "gap" },
          h("div", { class: "what" }, gap.kind || "gap"),
          h("div", { class: "fix" }, gap.detail || gap.reason || ""))))
      : h("p", { class: "muted" }, "Nothing limits this reading."),

    (flow.reasoning && (flow.reasoning.steps || []).length)
      ? h("div", { class: "stack" },
        h("h3", {}, "How it got there"),
        h("ol", { class: "reasoning" },
          flow.reasoning.steps.map((step) => h("li", {},
            h("div", {}, step.claim || step.detail || ""),
            (step.evidence || []).length
              ? h("div", { class: "cite mono" },
                (step.evidence || []).map((piece) => cite(piece.location)).join("  "))
              : null))))
      : null);
}
