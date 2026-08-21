/* The Flight workbench — the first screen a block manifest cannot express.
 *
 * Not "the 3D one". The stdlib console draws 3D perfectly well through the same
 * renderer seam, and it says so. What it cannot do is the workbench *around*
 * the view: panes the reader drags to size, a scrubbable axis over the answer,
 * and a route you re-aim without leaving the screen. Those are direct
 * manipulation and arranged layout, and that is where a declarative block
 * manifest stops and code starts.
 *
 * Nothing about the *answer* is written here. The scene modules come from ring
 * 0 verbatim — placement, colouring, the condition model, the rail, the camera,
 * the narration — so the two shells cannot disagree about where a node sits,
 * what a hue means, or which hop comes next. What this file adds is React and a
 * timeline.
 *
 * The data is real too: `GET /api/graph`, `GET /api/findings` and
 * `GET /api/impact` through the generated client. An earlier version built nine
 * hundred synthetic nodes when it had nothing to draw; it is gone rather than
 * hidden behind a flag, for the reason `api.ts` gives.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CHOOSING, MEANS, machine } from "@scene/condition.js";
import { at, rail, ticks } from "@scene/route.js";
import { path, ride, stepped } from "@scene/ride.js";
import { readout, upto } from "@scene/narrate.js";
import { client } from "../api";
import { useApi } from "../useApi";
import { Refusal } from "../ui/Refusal";
import { useScene, type Edge, type Node } from "../scene/useScene";

type GraphPayload = {
  nodes?: Array<Record<string, any>>;
  edges?: Array<Record<string, any>>;
  counts?: Record<string, number>;
  by_kind?: Record<string, number>;
};

type FindingsPayload = { findings?: Array<Record<string, any>> };

/** How much of the estate the first frame is allowed to ask for. */
const LIMIT = 400;

export function Flight() {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [split, setSplit] = useState(0.72);
  const [picked, setPicked] = useState("");
  const [seconds, setSeconds] = useState(0);

  /* The condition model, held rather than re-derived.
   *
   * It is the same machine ring 0's graph screen drives, and it is what stops
   * a scene appearing before a selection exists — §32's rule, and the reason
   * three prototypes were rejected. `tick` exists because the machine is
   * mutable and React is not: a transition has to tell the component something
   * changed, and a counter is the smallest honest way to say it.
   */
  const [, bump] = useState(0);
  const flight = useRef(machine(CHOOSING, { onChange: () => bump((n) => n + 1) })).current;

  const graph = useApi<GraphPayload>(() => client.readGraph({ limit: LIMIT }), []);
  // Severity is not a property of a node — it is what governance raised
  // *against* one. So it is joined here rather than read off the graph payload,
  // and a node with nothing against it carries "", which is what the scene
  // tier means by "spend no saturation on this".
  const findings = useApi<FindingsPayload>(() => client.readFindings(), []);
  const impact = useApi<any>(
    () => (picked ? client.readImpact({ id: picked }) : Promise.resolve(null)),
    [picked],
  );

  const severities = useMemo(() => worst(findings.value?.findings || []), [findings.value]);

  const { nodes, edges } = useMemo(() => {
    const rows = graph.value?.nodes || [];
    const nodes: Node[] = rows.map((row) => ({
      id: String(row.id),
      name: String(row.display || row.name || row.id),
      kind: String(row.kind || ""),
      severity: severities.get(String(row.id)) || "",
    }));
    const present = new Set(nodes.map((node) => node.id));
    // The graph route caps nodes and edges independently, so an edge can name a
    // node the other half of the answer did not include. Dropping those keeps
    // the scene consistent with itself; keeping them would put a line on screen
    // running to a mark that is not there.
    const edges: Edge[] = (graph.value?.edges || [])
      .filter((row) => present.has(String(row.src)) && present.has(String(row.dst)))
      .map((row) => ({ src: String(row.src), dst: String(row.dst) }));
    return { nodes, edges };
  }, [graph.value, severities]);

  const route = useMemo(() => (impact.value ? rail(impact.value) : null), [impact.value]);

  /* A selection is what allows a scene at all; a resolved route is what allows
   * a ride. Both are transitions rather than derived booleans, so the panel can
   * say *why* it is showing what it is showing. */
  useEffect(() => {
    if (!picked) { flight.send("clear"); return; }
    flight.send("select");
  }, [picked, flight]);

  useEffect(() => {
    setSeconds(0);
    if (!route) return;
    flight.send("aim");
    // A short answer is a list wearing a 3D scene. Below the flight floor the
    // rail says `animates: false` and the ride is never offered — the animation
    // earns its place on each answer rather than being applied because the
    // feature exists.
    if (!route.animates) flight.send("arrive");
  }, [route, flight]);

  const driving = flight.driven && Boolean(route) && Boolean(route?.animates);

  // The clock. Time is state rather than something the renderer reads, which is
  // what lets the scrubber and the ride be the same mechanism: both do nothing
  // but set `seconds`.
  useEffect(() => {
    if (!driving || !route) return;
    let frame = 0;
    let last = performance.now();
    const step = (now: number) => {
      const delta = (now - last) / 1000;
      last = now;
      setSeconds((held) => {
        const moved = held + delta;
        if (moved >= route.seconds) {
          flight.send("arrive");
          return route.seconds;
        }
        return moved;
      });
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [driving, route, flight]);

  /* The camera. Handed to the scene as a function of the moment, so the
   * renderer never learns that a ride exists — it is given a camera and paints
   * from it, exactly as it does for the survey. */
  const view = useCallback(
    (_extent: any, surface: { width: number; height: number }, placed: Map<string, any>) => {
      if (!route || !route.animates || !flight.draws) return null;
      const track = path(route, placed);
      if (track.points.length < 2) return null;
      const moment = ride(route, track, seconds, { ...surface, placed });
      return moment ? moment.camera : null;
    },
    [route, seconds, flight.condition],
  );

  const scene = useScene(canvas, nodes, edges, { view });

  // The reduced path needs the same placement the ride travels, so it is taken
  // from the scene rather than laid out a second time — a second layout would
  // be a second answer, which is exactly what the reduced path must not be.
  const laid = useMemo(
    () => (route && scene.placed ? path(route, scene.placed) : null),
    [route, scene.placed],
  );

  const now = route ? at(route, seconds).index : -1;
  const numbers = route ? readout(route, now, scene.drawn) : null;
  const lines = route ? upto(route, now, () => []) : [];
  const rung = route ? ticks(route, seconds, Object.fromEntries(severities)) : [];
  const steps = route && laid && !route.animates ? stepped(route, laid) : [];

  return (
    <div className="workbench" style={{ gridTemplateColumns: `${split * 100}% 1fr` }}>
      <div className="stage">
        {/* Nothing spatial before a selection. This is the rule three
          * prototypes broke by opening into a field of scattered points, and a
          * condition that renders nothing is the mechanism that enforces it. */}
        {flight.draws ? <canvas ref={canvas} /> : (
          <p className="muted pad">{meaning(flight.condition)}</p>
        )}
        <div
          className="handle"
          role="separator"
          aria-label="Resize the stage"
          aria-orientation="vertical"
          onPointerDown={(event) => {
            const start = event.clientX;
            const from = split;
            const width = event.currentTarget.parentElement?.clientWidth || 1;
            const move = (moved: PointerEvent) =>
              setSplit(Math.min(0.9, Math.max(0.35,
                from + (moved.clientX - start) / width)));
            const stop = () => {
              window.removeEventListener("pointermove", move);
              window.removeEventListener("pointerup", stop);
            };
            window.addEventListener("pointermove", move);
            window.addEventListener("pointerup", stop);
          }}
        />
      </div>

      <aside className="panel">
        <h2>Flight</h2>

        {/* Four states, and each says a different thing. A console that
          * collapsed them would tell a reader with a new environment that the
          * platform is broken. */}
        {graph.loading && <p className="muted">Reading the estate…</p>}
        <Refusal answer={graph} subject="The estate" />
        {graph.empty && !graph.error && (
          <p className="muted">
            The graph is empty. Nothing has been discovered into it yet — run
            <code> slpie 'discover . | link'</code> against an environment, then
            reload.
          </p>
        )}

        {nodes.length > 0 && (
          <>
            <label className="field">
              <span>Aim at</span>
              <select value={picked} onChange={(event) => setPicked(event.target.value)}>
                <option value="">— nothing selected —</option>
                {[...nodes].sort(byInterest).slice(0, 200).map((node) => (
                  <option key={node.id} value={node.id}>
                    {node.severity ? `[${node.severity}] ` : ""}{node.name}
                  </option>
                ))}
              </select>
            </label>

            <p className="muted small">
              <strong>{flight.condition}</strong> — {meaning(flight.condition)}
            </p>

            {picked && impact.loading && <p className="muted">Reading the blast radius…</p>}
            {picked && <Refusal answer={impact} subject="The blast radius" />}

            {route && (
              <>
                <p className={route.floor < 0.5 ? "refusal" : "muted mono"}>
                  {route.summary}
                </p>

                {/* The hop rail: a tick per hop, filled as it is passed, and
                  * the one place saturation is spent. Six hops is a distance
                  * you can see rather than a number you read. */}
                <ol className="rail" aria-label="Hops along this route">
                  {rung.map((tick: any) => (
                    <li
                      key={tick.id}
                      className={[
                        "tick",
                        tick.passed ? "passed" : "",
                        tick.current ? "current" : "",
                        tick.inferred ? "inferred" : "",
                        tick.severity ? `sev-${tick.severity}` : "",
                      ].filter(Boolean).join(" ")}
                    />
                  ))}
                </ol>

                {route.animates ? (
                  <div className="transport">
                    <button
                      type="button"
                      onClick={() => flight.send(flight.driven ? "hold" : "go")}
                    >
                      {flight.driven ? "Hold" : "Fly it"}
                    </button>
                    <input
                      type="range"
                      min={0}
                      max={route.seconds}
                      step={0.01}
                      value={seconds}
                      aria-label="Scrub the route"
                      onChange={(event) => {
                        // Manual input always wins and is never silently
                        // overridden. A camera that wrestles the reader for the
                        // wheel is how this class of interface becomes unusable.
                        flight.touch();
                        setSeconds(Number(event.target.value));
                      }}
                    />
                  </div>
                ) : (
                  <ol className="stepwise">
                    {steps.map((step: any) => (
                      <li key={step.id} className={step.inferred ? "inferred" : ""}>
                        {step.display} — {step.confidence.toFixed(2)}
                      </li>
                    ))}
                  </ol>
                )}

                {numbers && (
                  <p className="muted mono small">
                    {numbers.travelled} of {route.length} hops, {numbers.remaining} to
                    go, bounded at {numbers.floor.toFixed(2)}
                    {numbers.inferred ? ` — ${numbers.inferred} inferred` : ""}
                  </p>
                )}

                {/* The reasoning arriving in order. Every line traces to an
                  * evidence record; nothing here is a number nobody computed. */}
                <ol className="narration">
                  {lines.map((line: any, index: number) => (
                    <li
                      key={`${line.role}-${index}`}
                      className={`narrate ${line.role}${line.slowing ? " slowing" : ""}`}
                    >
                      {line.text}
                    </li>
                  ))}
                </ol>
              </>
            )}

            <p className="muted">
              Drawing with <strong>{scene.engine}</strong> — {scene.label}.
            </p>
            {scene.fallback && <p className="refusal">{scene.reason}</p>}
            {scene.shortfall && <p className="refusal">{scene.shortfall}</p>}

            <dl>
              <dt>Nodes read</dt>
              <dd>
                {nodes.length}
                {nodes.length >= LIMIT ? ` of ${graph.value?.counts?.nodes ?? "?"}` : ""}
              </dd>
              <dt>Edges between them</dt>
              <dd>{edges.length}</dd>
              <dt>Marks drawn</dt>
              <dd>{scene.drawn?.marks ?? "—"}</dd>
              <dt>Standing for</dt>
              <dd>{scene.drawn?.represented ?? "—"} nodes</dd>
              <dt>Carrying a finding</dt>
              <dd>{scene.drawn?.severe ?? "—"}</dd>
              <dt>Edges between marks</dt>
              <dd>{scene.drawn?.edges ?? "—"}</dd>
            </dl>

            <p className="muted small">
              Every number here is the renderer's own tally of what it put on the
              surface, never a formula over the node count.
            </p>
          </>
        )}

        {findings.error && (
          <p className="muted small">
            Severity is not shown — findings could not be read ({findings.error}).
            The scene is drawn without it rather than with a guess.
          </p>
        )}
      </aside>
    </div>
  );
}

/** What the current condition means, in ring 0's own words. */
function meaning(condition: string): string {
  return (MEANS as Record<string, string>)[condition] || "";
}

const RANK: Record<string, number> = {
  critical: 4, high: 3, medium: 2, low: 1, info: 0,
};

/** The worst severity standing against each subject, by node id. */
function worst(findings: Array<Record<string, any>>): Map<string, string> {
  const held = new Map<string, string>();
  for (const finding of findings) {
    if (finding?.suppressed) continue;
    const subject = String(finding?.subject || "");
    const severity = String(finding?.severity || "");
    if (!subject || !(severity in RANK)) continue;
    const standing = held.get(subject);
    if (!standing || RANK[severity] > RANK[standing]) held.set(subject, severity);
  }
  return held;
}

/** Findings first, then alphabetical — the order a reviewer aims in. */
function byInterest(left: Node, right: Node): number {
  return (RANK[right.severity || ""] ?? -1) - (RANK[left.severity || ""] ?? -1)
    || String(left.name).localeCompare(String(right.name));
}
