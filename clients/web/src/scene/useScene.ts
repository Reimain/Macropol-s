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
  /** The placement, so a caller can put a route through the same space. */
  placed: Map<string, any> | null;
};

/** What the caller draws from, when it wants a camera of its own. */
export type Surface = { width: number; height: number };
export type View = (extent: any, surface: Surface, placed: Map<string, any>) => any;

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
  { wanted = "three", view }: { wanted?: string; view?: View } = {},
) {
  const [state, setState] = useState<SceneState>({
    engine: DEFAULT, native: true, label: "native",
    fallback: false, reason: "", drawn: null, shortfall: "", placed: null,
  });
  const running = useRef(0);

  /* The caller's camera, held in a ref rather than in the dependency list.
   *
   * A ride camera is a function of the moment, so it is a new closure every
   * render — depending on it would tear down the renderer and rebuild the scene
   * sixty times a second. The frame loop reads the *current* one instead, which
   * is the standard answer and the correct one here: the scene is a function of
   * the graph, and the camera is a function of time, and only the first should
   * be able to remount anything. */
  const camera = useRef<View | undefined>(view);
  camera.current = view;

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

      const viewport = () => {
        const size = {
          width: surface.clientWidth || 900,
          height: surface.clientHeight || 600,
        };
        // The caller may decline for this frame — a route under the flight
        // floor, or a condition that is not driving — and the survey camera is
        // what it falls back to. Never a blank canvas.
        const asked = camera.current?.(scene.extent, size, scene.placed);
        return asked || frame(scene.extent, size);
      };

      const tick = () => {
        if (!live) return;
        const drawn = drawing.draw(viewport());
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
        placed: scene.placed,
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
