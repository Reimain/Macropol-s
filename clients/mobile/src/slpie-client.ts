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
  "attach": { consumes: "nothing", produces: "elements", mutates: false },
  "constraints": { consumes: "resolution", produces: "solution", mutates: false },
  "count": { consumes: "any", produces: "same", mutates: false },
  "declare": { consumes: "nothing", produces: "elements", mutates: false },
  "discover": { consumes: "nothing", produces: "observations", mutates: false },
  "explain": { consumes: "any", produces: "same", mutates: false },
  "filter": { consumes: "any", produces: "same", mutates: false },
  "findings": { consumes: "any", produces: "findings", mutates: false },
  "gaps": { consumes: "nothing", produces: "gaps", mutates: false },
  "graph": { consumes: "nothing", produces: "nodes", mutates: false },
  "head": { consumes: "any", produces: "same", mutates: false },
  "history": { consumes: "nothing", produces: "report", mutates: false },
  "impact": { consumes: "nodes", produces: "impact", mutates: false },
  "json": { consumes: "any", produces: "same", mutates: false },
  "link": { consumes: "observations", produces: "resolution", mutates: false },
  "reason": { consumes: "observations", produces: "enrichments", mutates: false },
  "reconcile": { consumes: "nothing", produces: "findings", mutates: false },
  "scan": { consumes: "nothing", produces: "observations", mutates: false },
  "search": { consumes: "nothing", produces: "nodes", mutates: false },
  "sort": { consumes: "any", produces: "same", mutates: false },
  "status": { consumes: "nothing", produces: "report", mutates: false },
  "target": { consumes: "nothing", produces: "report", mutates: true },
  "tool": { consumes: "any", produces: "text", mutates: false },
  "tools": { consumes: "nothing", produces: "report", mutates: false },
  "unique": { consumes: "any", produces: "same", mutates: false },
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

  /** register every declared element and negotiate capabilities */
  async attach(params: { capabilities?: string[]; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/attach`, params);
  }

  /** solve the version constraints, naming any conflict */
  async constraints(params: { ecosystem?: string; max_steps?: number; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/constraints`, params);
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

  /** everything the platform currently cannot see */
  async gaps(params: { upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/gaps`, params);
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

  /** serialise the flow, provenance included */
  async json(params: { limit?: number; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/json`, params);
  }

  /** merge observations onto identities and join across files */
  async link(params: { resolve_only?: boolean; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/link`, params);
  }

  /** run the reasoning layers over what was observed */
  async reason(params: { element?: string; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/reason`, params);
  }

  /** compare what was declared against what was observed */
  async reconcile(params: { upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/reconcile`, params);
  }

  /** read every attached element and record what is found */
  async scan(params: { upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/scan`, params);
  }

  /** find nodes by name */
  async search(params: { query: string; limit?: number; upstream?: Flow }): Promise<Flow> {
    return this.post(`/api/v/search`, params);
  }

  /** order by a field */
  async sort(params: { field?: string; desc?: boolean; upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/sort`, params);
  }

  /** the environment's current state */
  async status(params: { upstream?: Flow } = {}): Promise<Flow> {
    return this.post(`/api/v/status`, params);
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
