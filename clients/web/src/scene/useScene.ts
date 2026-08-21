/* The bridge, and the whole point of this package.
 *
 * Every module imported here comes from `@scene`, which is an alias for
 * `slpie/ui/app/engine/` — **the same files the stdlib console draws with**.
 * Not a port, not a copy kept in step by discipline. If a region colouring
 * changes, both shells change; if somebody duplicates one of these modules into
 * this package, the Python suite fails, because the copy is the drift.
 *
 * What this file adds is React, and only React: a hook that owns a canvas, a
 * renderer and an animation frame. The decisions — where a node goes, what hue
 * a region wears, which marks survive at this distance — are all upstream, in
 * ring 0, where the air-gapped console makes them too.
 */

/* The scene tier is plain ESM and ships no `.d.ts` — a declaration file would
 * be a build artifact in a directory that deliberately has no build step. It
 * does not need one: `allowJs` lets TypeScript infer these signatures from the
 * source and the JSDoc on it, so this package is type-checked against the
 * *actual* ring-0 code rather than against a hand-written description of it
 * that could quietly go stale. A first attempt here suppressed each import with
 * `@ts-expect-error`; every one of them turned out to be unnecessary, which is
 * the inference working. */
import { DEFAULT, describe, resolve } from "@scene/contract.js";
import { frame } from "@scene/camera.js";
import { adjacency, place } from "@scene/layout.js";
import { colour } from "@scene/palette.js";

import { useEffect, useRef, useState } from "react";

export type Node = {
  id: string;
  name?: string;
  kind: string;
  severity?: string;
};

export type Edge = { src: string; dst: string };

export type Tally = {
  marks: number;
  edges: number;
  represented: number;
  severe: number;
  tiers: { node: number; lane: number; cluster: number };
};

export type SceneState = {
  engine: string;
  native: boolean;
  label: string;
  fallback: boolean;
  reason: string;
  drawn: Tally | null;
  shortfall: string;
};

/**
 * Build the scene once per graph, and draw it every frame.
 *
 * The engine is resolved through ring 0's own seam, with the dynamic import
 * supplied here rather than left to the bundler: `contract.js` reaches for
 * `./vendor/${name}.js` at runtime, which Vite cannot analyse statically, and
 * `resolve()` already takes a loader for exactly this reason. So the seam works
 * unchanged in a bundled build and in a build-free one — which is the property
 * a seam is supposed to have.
 */
export function useScene(
  canvas: React.RefObject<HTMLCanvasElement | null>,
  nodes: Node[],
  edges: Edge[],
  wanted: string = "three",
) {
  const [state, setState] = useState<SceneState>({
    engine: DEFAULT, native: true, label: "native",
    fallback: false, reason: "", drawn: null, shortfall: "",
  });
  const running = useRef(0);

  useEffect(() => {
    let live = true;
    let drawing: any = null;

    (async () => {
      const surface = canvas.current;
      if (!surface || !nodes.length) return;

      const scene = place(nodes, edges, { regionOf: region });
      for (const [id, point] of scene.placed) {
        const found = nodes.find((node) => node.id === id);
        point.severity = found?.severity ?? "";
      }
      const colouring = colour(
        scene.regions.map((region: { name: string }) => region.name),
        adjacency(scene.placed, edges),
      );
      scene.colouring = colouring;

      const chosen = await resolve(wanted, {
        load: () => import("@scene/vendor/three.js"),
      });
      if (!live) return;

      drawing = Object.create(chosen.engine);
      drawing.mount(surface, scene);

      const view = () => frame(scene.extent, {
        width: surface.clientWidth || 900,
        height: surface.clientHeight || 600,
      });

      const tick = () => {
        if (!live) return;
        const drawn = drawing.draw(view());
        setState((held) => ({ ...held, drawn }));
        running.current = requestAnimationFrame(tick);
      };

      setState({
        engine: chosen.engine.name,
        native: chosen.engine.native,
        label: describe(chosen.engine).label,
        fallback: chosen.fallback,
        reason: chosen.reason,
        drawn: null,
        shortfall: colouring.shortfall,
      });
      tick();
    })();

    return () => {
      live = false;
      cancelAnimationFrame(running.current);
      if (drawing) drawing.dispose();
    };
  }, [canvas, nodes, edges, wanted]);

  return state;
}

/* Declared boundaries come from the manifest over the API. Until this screen is
 * wired to `GET /api/manifest` every node is estate, which is the honest
 * default: a region nobody declared is not a region. */
function region(_node: Node): string {
  return "";
}
