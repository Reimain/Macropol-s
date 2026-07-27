/* The interface. Plain modules, no framework, no build step.
 *
 * Views subscribe to the SSE feed rather than polling: the same event that
 * changed the graph refreshes the view that shows it. That is only possible
 * because the platform is event-sourced all the way down — the UI gets a live
 * feed for free rather than as a feature somebody had to build.
 */

const api = {
  async get(path, params = {}) {
    const query = new URLSearchParams(params).toString();
    const response = await fetch(`/api/${path}${query ? `?${query}` : ""}`);
    return response.json();
  },
  async post(path, body = {}) {
    const response = await fetch(`/api/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return { status: response.status, body: await response.json() };
  },
};

const el = (id) => document.getElementById(id);
const escape = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const short = (value, length = 12) => {
  const text = String(value ?? "");
  return text.length > length ? `${text.slice(0, length)}…` : text;
};

/* --- views -------------------------------------------------------------- */

const views = {};
let current = "console";

function show(name) {
  current = name;
  document.querySelectorAll(".view").forEach((section) =>
    section.classList.toggle("active", section.id === `view-${name}`));
  document.querySelectorAll("nav button").forEach((button) =>
    button.classList.toggle("active", button.dataset.view === name));
  refresh(name);
}

async function refresh(name) {
  if (views[name]) await views[name]();
}

document.querySelectorAll("nav button").forEach((button) =>
  button.addEventListener("click", () => show(button.dataset.view)));

/* --- status ------------------------------------------------------------- */

async function loadStatus() {
  const status = await api.get("status");
  el("environment").textContent = status.environment || "unconfigured";

  const target = el("target");
  target.textContent = status.target || "—";
  target.className = `pill ${status.target === "live" ? "live" : "simulated"}`;

  el("target-note").textContent = status.simulated
    ? "A simulated world is materialised on disk. Binding is against those files."
    : "";
  return status;
}

/* --- console ------------------------------------------------------------ */

views.console = async () => {
  const status = await loadStatus();
  const guidance = await api.get("status");   // gaps are rendered from /api/ask
  renderGaps(await currentGaps());
};

async function currentGaps() {
  const response = await api.post("ask", { question: "" });
  return response.body.gaps || [];
}

function renderGaps(gaps) {
  const host = el("console-gaps");
  if (!gaps.length) {
    host.innerHTML = '<p class="empty">Nothing is limiting the platform\'s answers.</p>';
    return;
  }
  host.innerHTML = gaps.map((gap) => `
    <div class="gap">
      <div class="what">${escape(gap.detail)}</div>
      ${gap.remediation ? `<div class="fix">→ ${escape(gap.remediation)}</div>` : ""}
    </div>`).join("");
}

async function ask(question) {
  if (!question.trim()) return;
  const conversation = el("conversation");
  if (conversation.querySelector(".empty")) conversation.innerHTML = "";

  const response = await api.post("ask", { question });
  const guidance = response.body;

  const steps = (guidance.reasoning?.steps || []).map((step) => `
    <div class="step">${escape(step.claim)}
      ${(step.evidence || []).map((e) =>
        `<span class="cite">${escape(e.reference)}</span>`).join(" ")}
    </div>`).join("");

  conversation.insertAdjacentHTML("beforeend", `
    <div class="turn">
      <div class="q">${escape(question)}</div>
      <div class="a">${escape(guidance.summary || guidance.answer || "no answer yet")}</div>
      ${steps}
      ${guidance.confidence != null
        ? `<div class="muted" style="font-size:12px;margin-top:4px">
             confidence ${Number(guidance.confidence).toFixed(2)}
             ${guidance.complete ? "" : " · limited by gaps"}</div>`
        : ""}
    </div>`);
  conversation.scrollTop = conversation.scrollHeight;

  const suggestions = guidance.next_questions || [];
  el("suggestions").innerHTML = suggestions.map((q) =>
    `<button class="chip" data-q="${escape(q.text)}">${escape(q.text)}</button>`).join("");
  el("suggestions").querySelectorAll(".chip").forEach((chip) =>
    chip.addEventListener("click", () => {
      el("question").value = chip.dataset.q;
      ask(chip.dataset.q);
    }));

  renderGaps(guidance.gaps || []);
}

el("ask").addEventListener("click", () => ask(el("question").value));
el("question").addEventListener("keydown", (event) => {
  if (event.key === "Enter") ask(el("question").value);
});

/* --- graph -------------------------------------------------------------- */

views.graph = async () => {
  const graph = await api.get("graph", { limit: 200 });
  const counts = graph.counts || {};

  el("graph-metrics").innerHTML = `
    ${metric("Nodes", counts.nodes)}
    ${metric("Edges", counts.edges)}
    ${metric("Evidence", counts.evidence)}
    ${metric("Retired", (counts.retired_nodes || 0) + (counts.retired_edges || 0))}
    <div class="card">
      <h2>Edge confidence</h2>
      ${Object.entries(graph.confidence || {}).map(([band, count]) =>
        `<div class="row"><span>${escape(band)}</span><span class="spacer"></span>
         <span class="mono">${count}</span></div>`).join("") || '<p class="empty">—</p>'}
    </div>`;

  renderNodes(graph.nodes || []);
};

function renderNodes(nodes) {
  el("graph-nodes").innerHTML = `
    <thead><tr><th>Kind</th><th>Name</th><th>Version</th>
      <th class="right">Confidence</th><th>Validation</th><th>Lifecycle</th></tr></thead>
    <tbody>${nodes.map((node) => `
      <tr>
        <td class="mono">${escape(node.kind)}</td>
        <td>${escape(node.name || node.display)}</td>
        <td class="mono">${escape(node.version || "")}</td>
        <td class="right mono">${Number(node.confidence).toFixed(2)}</td>
        <td>${pill(node.validation)}</td>
        <td class="mono muted">${escape(node.lifecycle)}</td>
      </tr>`).join("")}</tbody>`;
  if (!nodes.length) {
    el("graph-nodes").innerHTML = '<tbody><tr><td class="empty">No nodes yet.</td></tr></tbody>';
  }
}

el("graph-search").addEventListener("input", async (event) => {
  const term = event.target.value.trim();
  const data = term
    ? await api.get("search", { q: term })
    : await api.get("graph", { limit: 200 });
  renderNodes(data.results || data.nodes || []);
});

/* --- station ------------------------------------------------------------ */

views.station = async () => {
  const station = await api.get("station");
  const elements = station.elements || [];

  el("station-table").innerHTML = `
    <thead><tr><th>Element</th><th>Kind</th><th>State</th>
      <th>Granted</th><th>Refused</th><th>Handovers</th></tr></thead>
    <tbody>${elements.map((element) => `
      <tr>
        <td>${escape(element.element)}</td>
        <td class="mono muted">${escape(element.kind)}</td>
        <td>${element.attached
              ? (element.stale ? '<span class="pill warn">stale</span>'
                               : '<span class="pill ok">attached</span>')
              : '<span class="pill bad">detached</span>'}</td>
        <td class="mono">${(element.capabilities || []).map(escape).join(", ") || "—"}</td>
        <td class="mono muted">${(element.refused || []).map((c) => escape(c.name)).join(", ") || "—"}</td>
        <td class="right mono">${(element.handovers || []).length}</td>
      </tr>`).join("")}</tbody>`;

  if (!elements.length) {
    el("station-table").innerHTML =
      '<tbody><tr><td class="empty">Nothing attached yet.</td></tr></tbody>';
  }

  const refusals = station.refusals || [];
  el("station-refusals").innerHTML = refusals.length
    ? refusals.map((r) => `
        <div class="gap">
          <div class="what">${escape(r.element)} refused
            <span class="mono">${escape(r.capability)}</span></div>
          <div class="fix">${escape(r.reason)}</div>
        </div>`).join("")
    : '<p class="empty">Every capability asked for was granted.</p>';
};

/* --- reconciliation ----------------------------------------------------- */

views.reconciliation = async () => {
  const result = await api.get("reconcile");

  el("reconcile-metrics").innerHTML = `
    ${metric("Coverage", `${Math.round((result.coverage || 0) * 100)}%`)}
    ${metric("Corroborated", (result.corroborated || []).length)}
    ${metric("Not found", (result.declared_not_found || []).length)}
    ${metric("Undeclared", (result.undeclared || []).length)}`;

  list("declared-not-found", result.declared_not_found,
       (name) => `<div class="gap"><div class="what">${escape(name)}</div>
         <div class="fix">declared, but discovery never met it</div></div>`,
       "Everything declared was found.");

  list("undeclared", result.undeclared,
       (id) => `<div class="gap"><div class="what mono">${escape(short(id, 24))}</div>
         <div class="fix">observed, but the manifest does not declare it</div></div>`,
       "Nothing undeclared was observed.");

  list("contradictions", result.contradictions,
       (c) => `<div class="gap"><div class="what">${escape(c.attribute)}:
         declared <span class="mono">${escape(c.declared)}</span>,
         observed <span class="mono">${escape(c.observed)}</span></div>
         <div class="fix">the documentation is wrong, not merely stale</div></div>`,
       "Declaration and reality agree.");

  list("violations", result.boundary_violations,
       (v) => `<div class="gap" style="border-left-color:var(--crit)">
         <div class="what">${escape(v.inside)} → ${escape(v.outside)}</div>
         <div class="fix">leaves the ${escape(v.boundary)} boundary undeclared</div></div>`,
       "No boundary is crossed undeclared.");
};

/* --- findings ----------------------------------------------------------- */

views.findings = async () => {
  const data = await api.get("findings");
  const findings = data.findings || [];

  el("findings-list").innerHTML = findings.length
    ? findings.map((finding) => `
        <div class="card finding ${escape(finding.severity)}" style="margin-bottom:10px">
          <div class="row">
            <strong>${escape(finding.title)}</strong>
            <span class="spacer"></span>
            <span class="pill ${finding.severity === "critical" || finding.severity === "high"
              ? "bad" : "warn"}">${escape(finding.severity)}</span>
          </div>
          <div class="muted" style="font-size:13px;margin-top:6px">${escape(finding.detail || "")}</div>
          ${finding.remediation
            ? `<div class="fix" style="margin-top:8px">→ ${escape(finding.remediation.summary)}</div>`
            : ""}
          ${finding.location
            ? `<div class="cite mono" style="margin-top:6px">${escape(finding.location.uri)}</div>`
            : ""}
        </div>`).join("")
    : '<p class="empty">No open findings.</p>';
};

/* --- simulator ---------------------------------------------------------- */

views.simulator = async () => {
  await loadStatus();

  const scenarios = (await api.get("scenarios")).scenarios || [];
  el("scenario-chips").innerHTML = scenarios.map((name) =>
    `<button class="chip" data-scenario="${escape(name)}">${escape(name)}</button>`).join("");
  el("scenario-chips").querySelectorAll(".chip").forEach((chip) =>
    chip.addEventListener("click", () => fireScenario(chip.dataset.scenario)));

  const integrity = await api.get("integrity");
  el("integrity").innerHTML = integrity.intact
    ? `<div class="row"><span class="pill ok">chain intact</span>
       <span class="spacer"></span>
       <span class="mono muted">v${integrity.version} · ${escape(short(integrity.digest, 16))}</span></div>`
    : `<div class="row"><span class="pill bad">chain broken</span>
       <span class="spacer"></span>
       <span class="mono">${escape(integrity.detail || "")}</span></div>`;

  const projections = await api.get("projections");
  el("projections").innerHTML = `
    <thead><tr><th>Projection</th><th class="right">Sequence</th>
      <th class="right">Applied</th></tr></thead>
    <tbody>${Object.entries(projections).map(([name, state]) => `
      <tr><td class="mono">${escape(name)}</td>
        <td class="right mono">${state.last_sequence}</td>
        <td class="right mono">${state.applied}</td></tr>`).join("")}</tbody>`;
};

async function fireScenario(name) {
  const result = el("scenario-result");
  result.innerHTML = `<span class="muted">firing ${escape(name)}…</span>`;
  const response = await api.post("scenario", { scenario: name });

  if (response.status >= 400) {
    result.innerHTML = `<div class="gap"><div class="what">${escape(response.body.error)}</div></div>`;
    return;
  }
  const outcome = response.body;
  result.innerHTML = `
    <div class="gap" style="border-left-color:var(--accent)">
      <div class="what">${escape(outcome.detail || name)}</div>
      <div class="fix">
        expects: ${(outcome.expect_findings || []).map(escape).join(", ") || "—"}
        ${(outcome.expect_gaps || []).length
          ? ` · gaps: ${outcome.expect_gaps.map(escape).join(", ")}` : ""}
      </div>
    </div>`;
}

el("target-simulated").addEventListener("click", () => setTarget("simulated", true));
el("target-live").addEventListener("click", () => {
  // The gate is enforced at the write side; this only asks the human first.
  const confirmed = window.confirm(
    "Binding to the live environment reaches your real infrastructure.\n\n" +
    "SLPIE stays read-only unless writes are separately granted.\n\nContinue?");
  if (confirmed) setTarget("live", true);
});

async function setTarget(target, confirmed) {
  const response = await api.post("target", { target, confirmed, reason: "changed from the UI" });
  if (response.status >= 400) {
    el("target-note").textContent = response.body.error;
    return;
  }
  await loadStatus();
}

/* --- enterprise --------------------------------------------------------- */

views.enterprise = async () => {
  const data = await api.get("cycles");
  const cycles = data.cycles || [];
  el("cycles").innerHTML = cycles.length
    ? `<h2 style="margin-top:20px">Circular dependencies</h2>` + cycles.map((cycle) =>
        `<div class="gap"><div class="what mono">${escape(cycle.render)}</div>
         <div class="fix">${cycle.length} components in the cycle</div></div>`).join("")
    : '<p class="empty" style="margin-top:16px">No circular dependencies.</p>';
};

/* --- helpers ------------------------------------------------------------ */

function metric(label, value) {
  return `<div class="card"><h2>${escape(label)}</h2>
    <div class="metric">${escape(value ?? 0)}</div></div>`;
}

function pill(value) {
  const tone = { confirmed: "ok", corroborated: "ok", contradicted: "bad" }[value] || "";
  return `<span class="pill ${tone}">${escape(value)}</span>`;
}

function list(id, items, render, empty) {
  const host = el(id);
  host.innerHTML = (items && items.length)
    ? items.map(render).join("")
    : `<p class="empty">${escape(empty)}</p>`;
}

/* --- the live feed ------------------------------------------------------ */

function connect() {
  const source = new EventSource("/api/stream");
  const indicator = el("connection");

  source.onopen = () => {
    indicator.textContent = "live";
    indicator.className = "pill ok";
  };
  source.onerror = () => {
    indicator.textContent = "reconnecting";
    indicator.className = "pill warn";
  };
  source.addEventListener("dropped", (event) => {
    // Never let the view diverge silently from the platform's state.
    append(`<div class="event"><span class="seq">!</span>
      <span class="kind">feed-behind</span>
      <span class="subject">${escape(event.data)} events dropped</span></div>`);
  });
  source.onmessage = (message) => {
    const event = JSON.parse(message.data);
    append(`<div class="event ${event.operational ? "operational" : ""}">
      <span class="seq">${event.sequence}</span>
      <span class="kind">${escape(event.kind)}</span>
      <span class="subject">${escape(short(event.subject, 48))}</span></div>`);

    // The view that shows what just changed refreshes itself.
    if (event.mutates_graph && current === "graph") refresh("graph");
    if (event.kind.startsWith("finding") && current === "findings") refresh("findings");
    if (event.kind.startsWith("capability") || event.kind.startsWith("element")) {
      if (current === "station") refresh("station");
    }
    if (event.kind === "target_changed") loadStatus();
  };
}

function append(html) {
  const feed = el("feed");
  if (feed.querySelector(".empty")) feed.innerHTML = "";
  feed.insertAdjacentHTML("beforeend", html);
  while (feed.children.length > 200) feed.removeChild(feed.firstChild);
  feed.scrollTop = feed.scrollHeight;
}

/* --- boot --------------------------------------------------------------- */

loadStatus().then(() => show("console"));
connect();
