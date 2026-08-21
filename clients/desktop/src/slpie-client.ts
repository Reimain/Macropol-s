// Generated from the SLPIE verb registry. Do not edit.
// Regenerate with: slpie contract --typescript
// contract 1.0.0

export type Kind =
  | "nothing"
  | "any"
  | "same"
  | "manifest"
  | "elements"
  | "observations"
  | "resolution"
  | "enrichments"
  | "nodes"
  | "edges"
  | "graph"
  | "impact"
  | "requirements"
  | "solution"
  | "findings"
  | "gaps"
  | "guidance"
  | "judgements"
  | "plan"
  | "report"
  | "text"
  ;

export interface Gap {
  kind: string;
  subject: string;
  detail: string;
  remediation?: string;
  confidence_impact?: number;
}

export interface Flow<T = unknown> {
  kind: Kind;
  size: number;
  stages: string[];
  /** The path's confidence, already discounted by the gaps. */
  confidence: number;
  /** Whether every claim traces back to a file and a line. */
  grounded: boolean;
  /** Two surfaces running the same composition must produce the same digest. */
  digest: string;
  gaps: Gap[];
  reasoning: { steps: unknown[]; sources: string[] };
  value: T;
  facts: Record<string, unknown>;
}

/** What each verb consumes and produces, so a client can type-check a
  * composition before sending it. Mirrors the server's type graph. */
export const VERB_TYPES = {
  "accept": { consumes: "any", produces: "same", mutates: false },
  "agent-tools": { consumes: "nothing", produces: "report", mutates: false },
  "ask": { consumes: "enrichments", produces: "guidance", mutates: false },
  "attach": { consumes: "nothing", produces: "elements", mutates: false },
  "audit": { consumes: "nothing", produces: "judgements", mutates: false },
  "c4": { consumes: "observations", produces: "report", mutates: false },
  "capture": { consumes: "nothing", produces: "observations", mutates: false },
  "chain": { consumes: "nothing", produces: "report", mutates: false },
  "changed": { consumes: "nothing", produces: "report", mutates: false },
  "constraints": { consumes: "resolution", produces: "solution", mutates: false },
  "context": { consumes: "nothing", produces: "report", mutates: false },
  "count": { consumes: "any", produces: "same", mutates: false },
  "declare": { consumes: "nothing", produces: "elements", mutates: false },
  "discover": { consumes: "nothing", produces: "observations", mutates: false },
  "dismiss": { consumes: "any", produces: "same", mutates: false },
  "enterprise": { consumes: "observations", produces: "report", mutates: false },
  "explain": { consumes: "any", produces: "same", mutates: false },
  "filter": { consumes: "any", produces: "same", mutates: false },
  "findings": { consumes: "any", produces: "findings", mutates: false },
  "fire": { consumes: "nothing", produces: "report", mutates: true },
  "gaps": { consumes: "nothing", produces: "gaps", mutates: false },
  "govern": { consumes: "observations", produces: "findings", mutates: false },
  "graph": { consumes: "nothing", produces: "nodes", mutates: false },
  "head": { consumes: "any", produces: "same", mutates: false },
  "history": { consumes: "nothing", produces: "report", mutates: false },
  "impact": { consumes: "nodes", produces: "impact", mutates: false },
  "interest": { consumes: "nodes", produces: "report", mutates: false },
  "json": { consumes: "any", produces: "same", mutates: false },
  "lexicon": { consumes: "nothing", produces: "report", mutates: false },
  "link": { consumes: "observations", produces: "resolution", mutates: false },
  "options": { consumes: "enrichments", produces: "report", mutates: false },
  "quarantine": { consumes: "any", produces: "report", mutates: false },
  "radius": { consumes: "enrichments", produces: "impact", mutates: false },
  "reason": { consumes: "observations", produces: "enrichments", mutates: false },
  "reconcile": { consumes: "nothing", produces: "findings", mutates: false },
  "risk": { consumes: "findings", produces: "report", mutates: false },
  "rivals": { consumes: "nothing", produces: "report", mutates: false },
  "routine": { consumes: "nothing", produces: "report", mutates: false },
  "rules": { consumes: "nothing", produces: "report", mutates: false },
  "sbom": { consumes: "observations", produces: "report", mutates: false },
  "scan": { consumes: "nothing", produces: "observations", mutates: false },
  "search": { consumes: "nothing", produces: "nodes", mutates: false },
  "simulate": { consumes: "nothing", produces: "elements", mutates: true },
  "sort": { consumes: "any", produces: "same", mutates: false },
  "status": { consumes: "nothing", produces: "report", mutates: false },
  "suggest": { consumes: "any", produces: "same", mutates: false },
  "target": { consumes: "nothing", produces: "report", mutates: true },
  "tool": { consumes: "any", produces: "text", mutates: false },
  "tools": { consumes: "nothing", produces: "report", mutates: false },
  "unique": { consumes: "any", produces: "same", mutates: false },
  "verdicts": { consumes: "judgements", produces: "same", mutates: false },
} as const satisfies Record<string, {
  consumes: Kind; produces: Kind; mutates: boolean;
}>;

export type VerbName = keyof typeof VERB_TYPES;

/** Reject an impossible composition without a round trip. */
export function validate(pipeline: VerbName[]): string | null {
  let current: Kind = "nothing";
  for (let i = 0; i < pipeline.length; i++) {
    const verb = VERB_TYPES[pipeline[i]];
    if (verb.consumes !== "any" && verb.consumes !== current) {
      return `stage ${i + 1} \`${pipeline[i]}\` consumes ${verb.consumes.toUpperCase()}, but it was given ${current.toUpperCase()}`;
    }
    current = verb.produces === "same" ? current : verb.produces;
  }
  return null;
}

export function producedKind(pipeline: VerbName[]): Kind {
  let current: Kind = "nothing";
  for (const name of pipeline) {
    const verb = VERB_TYPES[name];
    current = verb.produces === "same" ? current : verb.produces;
  }
  return current;
}

export interface ClientOptions {
  baseUrl?: string;
  fetch?: typeof fetch;
}

export class SlpieClient {
  private readonly baseUrl: string;
  private readonly doFetch: typeof fetch;

  constructor(options: ClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.doFetch = options.fetch ?? fetch;
  }

  /** Run a whole composition server-side. The primary entry point. */
  async run(pipeline: string, confirmed = false): Promise<Flow> {
    return this.post(`/api/run`, { pipeline, confirmed });
  }

  /** Check a composition without running any of it. */
  async check(pipeline: string): Promise<{ ok: boolean; explanation: string }> {
    return this.post(`/api/compose/validate`, { pipeline });
  }

  /** Ask the planner to write a composition for a question. */
  async plan(question: string): Promise<unknown> {
    return this.post(`/api/plan`, { question });
  }

  async verbs(): Promise<unknown> {
    return this.get(`/api/verbs`);
  }

  async manual(): Promise<unknown> {
    return this.get(`/api/manual`);
  }

  /** record that a suggested path was worth taking */
  async accept(params: { key: string; about?: string; upstream?: Flow }): Promise<Flow> {
    return this.post(`/api/v/accept`, params);
  }

  /** the capabilities an agent can call, as JSON schemas */
  async agentTools(params: { name?: string; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/agent-tools`, params);
  }

  /** the answer, its reasoning, its gaps, and what to ask next */
  async ask(params: { question?: string; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/ask`, params);
  }

  /** register every declared element and negotiate capabilities */
  async attach(params: { capabilities?: string[]; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/attach`, params);
  }

  /** judge a tree against its stated architecture */
  async audit(params: { path?: string; checks?: "auto" | "self"; rule?: string; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/audit`, params);
  }

  /** C4 views of the system, as Mermaid */
  async c4(params: { level?: "context" | "container" | "component" | "code"; container?: string; component?: string; out?: string; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/c4`, params);
  }

  /** identify files by content and run them past the firewall */
  async capture(params: { path?: string; depth?: "probe" | "structure" | "model" | "semantic"; limit?: number; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/capture`, params);
  }

  /** the firewall's rules, in the order they are evaluated */
  async chain(params: { upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/chain`, params);
  }

  /** what has moved since the last scan, and what it would cost */
  async changed(params: { path?: string; baseline?: string; commit?: boolean; strict?: boolean; lenient?: boolean; limit?: number; "max-bytes"?: number; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/changed`, params);
  }

  /** solve the version constraints, naming any conflict */
  async constraints(params: { ecosystem?: string; max_steps?: number; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/constraints`, params);
  }

  /** the product's own map: what exists, and what connects it */
  async context(params: { query?: string; depth?: number; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/context`, params);
  }

  /** count what is flowing, without changing it */
  async count(params: { upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/count`, params);
  }

  /** build the skeleton graph from the manifest, before reading a file */
  async declare(params: { upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/declare`, params);
  }

  /** read a tree and record what every discoverer finds */
  async discover(params: { path?: string; limit?: number; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/discover`, params);
  }

  /** record that a suggested path was not useful here */
  async dismiss(params: { key: string; reason?: string; about?: string; upstream?: Flow }): Promise<Flow> {
    return this.post(`/api/v/dismiss`, params);
  }

  /** TOGAF views and the deployment topology */
  async enterprise(params: { view?: "application" | "data" | "technology" | "standards" | "topology"; write?: boolean; out?: string; policy?: "raise" | "mark" | "local" | "generated"; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/enterprise`, params);
  }

  /** render the reasoning, the sources and the gaps behind this */
  async explain(params: { remediation?: boolean; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/explain`, params);
  }

  /** keep only what matches */
  async filter(params: { field?: string; contains?: string; equals?: string; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/filter`, params);
  }

  /** rank everything wrong that is known so far */
  async findings(params: { severity?: "critical" | "high" | "medium" | "low" | "info"; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/findings`, params);
  }

  /** fire a scripted condition at the simulated world */
  async fire(params: { scenario: "boundary-breach" | "capability-refused" | "contract-broken" | "cve" | "declaration-drift" | "duplicate-versions" | "license-change" | "major-bump" | "partial-scan" | "service-dies" | "shadow-dependency" | "unmaintained"; package?: string; version?: string; upstream?: Flow; confirmed?: boolean }): Promise<Flow> {
    return this.post(`/api/v/fire`, params);
  }

  /** everything the platform currently cannot see */
  async gaps(params: { upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/gaps`, params);
  }

  /** run every governance rule over what was scanned */
  async govern(params: { path?: string; severity?: "critical" | "high" | "medium" | "low" | "info"; family?: string; advisories?: string; popular?: string; distribution?: "internal_only" | "network_service" | "distributed_binary" | "distributed_source" | "embedded"; linkage?: "dynamic" | "static" | "separate_process" | "unmodified"; project_license?: string; no_sources?: boolean; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/govern`, params);
  }

  /** read nodes from the graph */
  async graph(params: { limit?: number; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/graph`, params);
  }

  /** keep the first N */
  async head(params: { count?: number; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/head`, params);
  }

  /** recent commits, from git rather than from a reimplementation */
  async history(params: { count?: number; path?: string; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/history`, params);
  }

  /** what depends on this, and how confidently */
  async impact(params: { id?: string; depth?: number; min_confidence?: number; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/impact`, params);
  }

  /** what a selection makes worth drawing, and what it hides */
  async interest(params: { id?: string; horizon?: number; budget?: number; threshold?: number; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/interest`, params);
  }

  /** serialise the flow, provenance included */
  async json(params: { limit?: number; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/json`, params);
  }

  /** the words this context uses for the platform's nouns */
  async lexicon(params: { profile?: string; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/lexicon`, params);
  }

  /** merge observations onto identities and join across files */
  async link(params: { resolve_only?: boolean; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/link`, params);
  }

  /** every upgrade available, with the cost of each */
  async options(params: { kind?: "safe_upgrade" | "upgrade_option" | "duplicate_versions" | "unconstrained_range"; safe?: boolean; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/options`, params);
  }

  /** what was held rather than admitted, and why */
  async quarantine(params: { upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/quarantine`, params);
  }

  /** what depends on what, without needing a database */
  async radius(params: { package?: string; min_size?: number; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/radius`, params);
  }

  /** run the reasoning layers over what was observed */
  async reason(params: { element?: string; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/reason`, params);
  }

  /** compare what was declared against what was observed */
  async reconcile(params: { upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/reconcile`, params);
  }

  /** findings aggregated into a ranked risk register */
  async risk(params: { limit?: number; markdown?: boolean; out?: string; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/risk`, params);
  }

  /** what else is on the market, and where nobody serves the buyer */
  async rivals(params: { gaps?: boolean; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/rivals`, params);
  }

  /** claim what you did as a short key, or list what you claimed */
  async routine(params: { action?: "list" | "claim" | "forget"; name?: string; pipeline?: string; key?: string; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/routine`, params);
  }

  /** what this build checks, and each rule's fingerprint */
  async rules(params: { tag?: string; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/rules`, params);
  }

  /** a bill of materials, in CycloneDX or SPDX */
  async sbom(params: { format?: "cyclonedx" | "spdx"; out?: string; subject?: string; subject_version?: string; timestamp?: number; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/sbom`, params);
  }

  /** read every attached element and record what is found */
  async scan(params: { upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/scan`, params);
  }

  /** find nodes by name */
  async search(params: { query: string; limit?: number; upstream?: Flow }): Promise<Flow> {
    return this.post(`/api/v/search`, params);
  }

  /** materialise the declared world as real files on disk */
  async simulate(params: { at?: string; upstream?: Flow; confirmed?: boolean } = {}): Promise<Flow> {
    return this.post(`/api/v/simulate`, params);
  }

  /** order by a field */
  async sort(params: { field?: string; desc?: boolean; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/sort`, params);
  }

  /** the environment's current state */
  async status(params: { upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/status`, params);
  }

  /** what to look at next, and why */
  async suggest(params: { about?: string; kind?: string; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/suggest`, params);
  }

  /** bind the environment to simulated or live */
  async target(params: { to: "simulated" | "live"; upstream?: Flow; confirmed?: boolean }): Promise<Flow> {
    return this.post(`/api/v/target`, params);
  }

  /** pipe the flow through a registered external tool */
  async tool(params: { name: string; args?: string; upstream?: Flow }): Promise<Flow> {
    return this.post(`/api/v/tool`, params);
  }

  /** which external tools are installed, and how reproducible each is */
  async tools(params: { upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/tools`, params);
  }

  /** drop duplicates */
  async unique(params: { field?: string; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/unique`, params);
  }

  /** keep only judgements with one verdict */
  async verdicts(params: { only?: "upheld" | "violated" | "indeterminate" | "inapplicable"; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/verdicts`, params);
  }

  private async get(path: string): Promise<any> {
    const response = await this.doFetch(`${this.baseUrl}${path}`);
    return this.unwrap(response);
  }

  private async post(path: string, body: unknown): Promise<any> {
    const response = await this.doFetch(`${this.baseUrl}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body ?? {}),
    });
    return this.unwrap(response);
  }

  private async unwrap(response: Response): Promise<any> {
    const body = await response.json();
    if (!response.ok) {
      // A refusal is an answer with a reason, not a generic failure.
      throw Object.assign(
        new Error(body?.error ?? `HTTP ${response.status}`),
        { status: response.status, refused: body?.refused === true },
      );
    }
    return body;
  }
}
