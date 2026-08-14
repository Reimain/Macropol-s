/* The Compose view — composition, made visible.
 *
 * Everything here is built from `/api/verbs` and `/api/manual`, which are
 * projections of the one verb registry. So a verb added on the server appears in
 * this palette with no change to this file — the same property that makes the CLI
 * and the manual impossible to drift, applied to the screen.
 *
 * The type check runs *locally*, from the graph the contract ships. That is the
 * point of typing the pipe: you find out `findings | attach` is impossible while
 * you are still building it, not after a round trip. The server validates again
 * before running anything, because a client is never the authority on that.
 */

const compose = {
  verbs: {},        // name -> declaration
  graph: {},        // name -> successors
  manual: null,
  pipeline: [],     // [{ verb, args }]
  running: false,
};

/* --- loading the registry ----------------------------------------------- */

async function loadCompose() {
  if (!Object.keys(compose.verbs).length) {
    const body = await api.get("verbs");
    body.verbs.forEach((verb) => { compose.verbs[verb.name] = verb; });
    compose.graph = body.graph || {};
    compose.groups = body.groups || {};
    try {
      compose.manual = await api.get("manual");
    } catch (error) {
      compose.manual = null;   // the recipes are a nicety, not a requirement
    }
    renderRecipes();
  }
  renderPalette();
  renderPipeline();
}

/* --- the local type check ------------------------------------------------ */

/** What is flowing after N stages. `same` passes the kind through. */
function kindAfter(stages) {
  let current = "nothing";
  for (const stage of stages) {
    const verb = compose.verbs[stage.verb];
    if (!verb) return current;
    current = verb.produces === "same" ? current : verb.produces;
  }
  return current;
}

/** The first type error, or null. Mirrors the server's own rule exactly. */
function checkPipeline(stages) {
  let current = "nothing";
  for (let index = 0; index < stages.length; index += 1) {
    const verb = compose.verbs[stages[index].verb];
    if (!verb) return `stage ${index + 1}: unknown verb`;
    if (verb.consumes !== "any" && verb.consumes !== current) {
      if (current === "nothing") {
        return `stage ${index + 1} \`${verb.name}\` needs ` +
          `${verb.consumes.toUpperCase()} piped into it, but it starts the ` +
          `pipeline — a source verb has to come first`;
      }
      return `stage ${index + 1} \`${verb.name}\` consumes ` +
        `${verb.consumes.toUpperCase()}, but it was given ` +
        `${current.toUpperCase()}`;
    }
    current = verb.produces === "same" ? current : verb.produces;
  }
  return null;
}

/** Whether appending this verb would still type-check. Drives the palette. */
function canFollow(name) {
  const verb = compose.verbs[name];
  if (!verb) return false;
  const current = kindAfter(compose.pipeline);
  return verb.consumes === "any" || verb.consumes === current;
}

function pipelineText() {
  return compose.pipeline.map((stage) => {
    const parts = [stage.verb];
    Object.entries(stage.args || {}).forEach(([name, value]) => {
      if (value === true) parts.push(`--${name}`);
      else if (value !== "" && value != null) parts.push(`--${name} ${value}`);
    });
    return parts.join(" ");
  }).join(" | ");
}

/* --- rendering ----------------------------------------------------------- */

function renderPalette() {
  const host = el("compose-palette");
  if (!host) return;

  const current = kindAfter(compose.pipeline);
  const groups = Object.entries(compose.groups || {});

  host.innerHTML = groups.map(([group, names]) => {
    const chips = names.map((name) => {
      const verb = compose.verbs[name];
      const usable = canFollow(name);
      const classes = ["chip", usable ? "usable" : "blocked"];
      if (verb.mutates) classes.push("danger");
      const why = usable
        ? `${verb.summary} — ${verb.consumes === "any" ? "accepts anything"
            : verb.consumes === "nothing" ? "starts a pipeline"
            : `takes ${verb.consumes.toUpperCase()}`}`
        : `needs ${verb.consumes.toUpperCase()}, but ${current.toUpperCase()} is flowing`;
      return `<button class="${classes.join(" ")}" data-verb="${escape(name)}"
        ${usable ? "" : "disabled"} title="${escape(why)}">${escape(name)}</button>`;
    }).join("");
    return `<div class="palette-group">
      <span class="palette-label">${escape(group)}</span>${chips}</div>`;
  }).join("");

  host.querySelectorAll("button[data-verb]").forEach((button) =>
    button.addEventListener("click", () => addStage(button.dataset.verb)));
}

function renderPipeline() {
  const host = el("compose-stages");
  if (!host) return;

  if (!compose.pipeline.length) {
    host.innerHTML = `<p class="empty">
      Pick a source verb to begin. Verbs that cannot follow what is flowing are
      disabled rather than hidden, so the shape of the type graph stays visible.
    </p>`;
    el("compose-signature").textContent = "";
    el("compose-text").value = "";
    el("compose-run").disabled = true;
    return;
  }

  let current = "nothing";
  host.innerHTML = compose.pipeline.map((stage, index) => {
    const verb = compose.verbs[stage.verb] || {};
    const before = current;
    current = verb.produces === "same" ? current : verb.produces;
    const params = (verb.params || []).map((param) => {
      const value = (stage.args || {})[param.name] ?? "";
      if (param.type === "bool") {
        return `<label class="param"><input type="checkbox" data-stage="${index}"
          data-param="${escape(param.name)}" ${value === true ? "checked" : ""}>
          ${escape(param.name)}</label>`;
      }
      if (param.choices && param.choices.length) {
        const options = ["", ...param.choices].map((choice) =>
          `<option ${choice === value ? "selected" : ""}>${escape(choice)}</option>`
        ).join("");
        return `<label class="param">${escape(param.name)}
          <select data-stage="${index}" data-param="${escape(param.name)}">
          ${options}</select></label>`;
      }
      return `<label class="param">${escape(param.name)}
        <input class="mono" data-stage="${index}" data-param="${escape(param.name)}"
          value="${escape(value === true ? "" : value)}"
          placeholder="${escape(param.help || param.type)}"></label>`;
    }).join("");

    return `<div class="stage">
      <div class="stage-head">
        <span class="stage-index">${index + 1}</span>
        <span class="mono stage-name">${escape(stage.verb)}</span>
        <span class="stage-flow mono">${escape(before.toUpperCase())} →
          ${escape(current.toUpperCase())}</span>
        ${verb.mutates ? '<span class="pill live">changes the environment</span>' : ""}
        <button class="drop" data-remove="${index}" title="remove this stage">×</button>
      </div>
      <div class="stage-summary muted">${escape(verb.summary || "")}</div>
      ${params ? `<div class="stage-params">${params}</div>` : ""}
    </div>`;
  }).join('<div class="pipe-arrow mono">|</div>');

  host.querySelectorAll("[data-remove]").forEach((button) =>
    button.addEventListener("click", () => {
      compose.pipeline.splice(Number(button.dataset.remove), 1);
      renderPalette();
      renderPipeline();
    }));

  host.querySelectorAll("[data-param]").forEach((input) =>
    input.addEventListener("change", () => {
      const stage = compose.pipeline[Number(input.dataset.stage)];
      stage.args = stage.args || {};
      stage.args[input.dataset.param] =
        input.type === "checkbox" ? input.checked : input.value;
      renderPipeline();
    }));

  const error = checkPipeline(compose.pipeline);
  const signature = el("compose-signature");
  const mutating = compose.pipeline.filter(
    (stage) => (compose.verbs[stage.verb] || {}).mutates);

  if (error) {
    signature.innerHTML = `<span class="bad">✕ ${escape(error)}</span>`;
    el("compose-run").disabled = true;
  } else {
    signature.innerHTML =
      `<span class="ok">✓ produces ${escape(kindAfter(compose.pipeline).toUpperCase())}</span>` +
      (mutating.length
        ? ` <span class="bad">— ${escape(mutating.map((s) => s.verb).join(", "))}
            will change the environment, so it needs confirmation</span>`
        : "");
    el("compose-run").disabled = compose.running;
  }
  el("compose-text").value = pipelineText();
  el("compose-confirm").hidden = mutating.length === 0;
}

function renderRecipes() {
  const host = el("compose-recipes");
  if (!host || !compose.manual) return;

  host.innerHTML = (compose.manual.recipes || []).map((recipe) =>
    `<div class="recipe">
      <button class="recipe-run mono" data-pipeline="${escape(recipe.pipeline)}"
        title="load this into the builder">${escape(recipe.pipeline)}</button>
      <div class="muted">${escape(recipe.intent)}</div>
      ${recipe.needs_environment
        ? '<span class="pill simulated">needs an environment</span>' : ""}
    </div>`).join("");

  host.querySelectorAll("[data-pipeline]").forEach((button) =>
    button.addEventListener("click", () => loadPipeline(button.dataset.pipeline)));
}

/* --- editing ------------------------------------------------------------- */

function addStage(name) {
  compose.pipeline.push({ verb: name, args: {} });
  renderPalette();
  renderPipeline();
}

/** Parse a pipeline back into stages, so a recipe or a plan is editable.
 *
 * Deliberately simple: it splits on `|` and reads `--flag value`. The server is
 * the authority on parsing, and this only has to be good enough to populate the
 * builder — anything it misreads is visible in the text box before running.
 */
function loadPipeline(text) {
  compose.pipeline = String(text || "").split("|").map((fragment) => {
    const tokens = fragment.trim().split(/\s+/).filter(Boolean);
    const verb = tokens.shift();
    const args = {};
    for (let index = 0; index < tokens.length; index += 1) {
      const token = tokens[index];
      if (!token.startsWith("--")) continue;
      const name = token.slice(2);
      const next = tokens[index + 1];
      if (next && !next.startsWith("--")) { args[name] = next; index += 1; }
      else args[name] = true;
    }
    return { verb, args };
  }).filter((stage) => stage.verb && compose.verbs[stage.verb]);

  show("compose");
  renderPalette();
  renderPipeline();
}

/* --- running ------------------------------------------------------------- */

async function runComposition() {
  const text = el("compose-text").value.trim();
  if (!text) return;

  compose.running = true;
  el("compose-run").disabled = true;
  el("compose-result").innerHTML = '<p class="muted">running…</p>';

  const { status, body } = await api.post("run", {
    pipeline: text,
    confirmed: el("compose-confirmed") ? el("compose-confirmed").checked : false,
  });

  compose.running = false;
  renderPipeline();
  renderResult(status, body);
}

function renderResult(status, body) {
  const host = el("compose-result");

  if (body.error) {
    host.innerHTML = `<div class="card bad-card">
      <strong>refused</strong><div>${escape(body.error)}</div></div>`;
    return;
  }

  const flow = body.flow || {};
  const gaps = flow.gaps || [];
  const steps = (flow.reasoning || {}).steps || [];

  host.innerHTML = `
    <div class="result-head">
      <span class="pill mono">${escape((flow.kind || "").toUpperCase())}
        × ${flow.size ?? 0}</span>
      <span class="pill mono" title="confidence, already discounted by the gaps">
        confidence ${flow.confidence ?? 0}</span>
      <span class="pill mono ${flow.grounded ? "ok-pill" : "bad-pill"}"
        title="whether every claim traces back to a file and a line">
        ${flow.grounded ? "grounded" : "not grounded"}</span>
      <span class="pill mono" title="two surfaces running this must agree">
        ${escape(short(flow.digest, 10))}</span>
      ${body.partial ? '<span class="pill live">partial</span>' : ""}
    </div>

    ${body.error || body.failed ? `<div class="bad">failed at
      <span class="mono">${escape(body.failed)}</span>:
      ${escape(body.error || "")}</div>` : ""}

    <div class="grid" style="margin-top:12px">
      <div class="card">
        <h2>Reasoning</h2>
        ${steps.length ? `<ol class="reasoning">${steps.map((step) =>
          `<li>${escape(step.claim)}
            ${(step.evidence || []).length
              ? `<div class="cites mono">${step.evidence.slice(0, 4).map((item) =>
                  escape((item.location || {}).uri
                    ? `${(item.location.uri || "").split("/").pop()}${
                        item.location.line ? `:${item.location.line}` : ""}`
                    : "")).join(" · ")}</div>`
              : '<div class="cites muted">cites nothing</div>'}
          </li>`).join("")}</ol>`
          : '<p class="empty">No reasoning recorded.</p>'}
      </div>
      <div class="card">
        <h2>Gaps limiting this answer</h2>
        ${gaps.length ? gaps.map((gap) =>
          `<div class="gap"><span class="mono">${escape(gap.kind)}</span>
            <div>${escape(gap.detail)}</div>
            ${gap.remediation
              ? `<div class="muted">→ ${escape(gap.remediation)}</div>` : ""}
          </div>`).join("")
          : '<p class="empty">None — nothing limited this answer.</p>'}
      </div>
    </div>

    <div class="card" style="margin-top:12px">
      <h2>Result</h2>
      <div class="scroll"><pre class="mono">${escape(
        JSON.stringify(flow.value, null, 2) || "")}</pre></div>
    </div>`;
}

/* --- the planner --------------------------------------------------------- */

async function planQuestion() {
  const question = el("compose-question").value.trim();
  if (!question) return;

  const { body } = await api.post("plan", { question });
  const host = el("compose-plan");

  if (!body.understood) {
    host.innerHTML = `<div class="card">
      <div>${escape(body.clarification)}</div>
      <div style="margin-top:8px">${(body.options || []).map((option) =>
        `<button class="chip usable mono" data-option="${escape(option)}"
          >${escape(option)}</button>`).join("")}</div>
    </div>`;
    host.querySelectorAll("[data-option]").forEach((button) =>
      button.addEventListener("click", () => loadPipeline(button.dataset.option)));
    return;
  }

  host.innerHTML = `<div class="card">
    <div class="mono" style="font-size:14px">${escape(body.pipeline)}</div>
    <ol class="reasoning">${(body.steps || []).map((step) =>
      `<li><span class="mono">${escape(step.verb)}</span> — ${escape(step.because)}</li>`
    ).join("")}</ol>
    ${body.needs_environment
      ? '<div class="muted">needs an environment to run</div>' : ""}
    <div class="row" style="margin-top:8px">
      <button class="go" id="compose-plan-load">Load into the builder</button>
    </div>
  </div>`;

  const load = el("compose-plan-load");
  if (load) load.addEventListener("click", () => loadPipeline(body.pipeline));
}

/* --- wiring -------------------------------------------------------------- */

function wireCompose() {
  const run = el("compose-run");
  if (run) run.addEventListener("click", runComposition);

  const clear = el("compose-clear");
  if (clear) clear.addEventListener("click", () => {
    compose.pipeline = [];
    el("compose-result").innerHTML = "";
    renderPalette();
    renderPipeline();
  });

  const text = el("compose-text");
  if (text) text.addEventListener("change", () => loadPipeline(text.value));

  const ask = el("compose-plan-go");
  if (ask) ask.addEventListener("click", planQuestion);

  const question = el("compose-question");
  if (question) question.addEventListener("keydown", (event) => {
    if (event.key === "Enter") planQuestion();
  });

  views.compose = loadCompose;
}

wireCompose();
