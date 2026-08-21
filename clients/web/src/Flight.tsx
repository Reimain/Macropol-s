/* The Flight workbench — the first screen a block manifest cannot express.
 *
 * Not "the 3D one". The stdlib console draws 3D perfectly well through the same
 * renderer seam, and it says so. What it cannot do is the workbench *around*
 * the view: panes the reader drags to size, a scrubbable axis over the ledger,
 * and a route you re-aim by dragging. Those are direct manipulation and
 * arranged layout, and that is where a declarative block manifest stops and
 * code starts.
 *
 * The scene itself is not written here. It comes from ring 0 verbatim, so the
 * two shells cannot disagree about where a node is or what a region's hue
 * means.
 */

import { useMemo, useRef, useState } from "react";
import { useScene, type Edge, type Node } from "./scene/useScene";

const KINDS = ["package", "service", "table", "deployment", "team"];

export function Flight() {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [split, setSplit] = useState(0.72);

  // Stand-in until this is wired to `GET /api/graph`. Kept obviously synthetic
  // rather than dressed up as real data — a demo that looks like an answer is
  // the thing this product exists to stop.
  const { nodes, edges } = useMemo(() => {
    const nodes: Node[] = Array.from({ length: 900 }, (_, index) => ({
      id: `n${index}`,
      name: `node-${index}`,
      kind: KINDS[index % KINDS.length],
      severity: index % 90 === 0 ? "critical" : "",
    }));
    const edges: Edge[] = Array.from({ length: 1400 }, (_, index) => ({
      src: `n${index % 900}`,
      dst: `n${(index * 7 + 13) % 900}`,
    }));
    return { nodes, edges };
  }, []);

  const scene = useScene(canvas, nodes, edges);

  return (
    <div className="workbench" style={{ gridTemplateColumns: `${split * 100}% 1fr` }}>
      <div className="stage">
        <canvas ref={canvas} />
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
        <p className="muted">
          Drawing with <strong>{scene.engine}</strong> — {scene.label}.
        </p>
        {scene.fallback && <p className="refusal">{scene.reason}</p>}
        {scene.shortfall && <p className="refusal">{scene.shortfall}</p>}

        <dl>
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
      </aside>
    </div>
  );
}
