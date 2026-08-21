/* Three.js behind the seam — the same answer, drawn with WebGL.
 *
 * ── What this is, and what it is not ─────────────────────────────────────
 *
 * The concept this serves arrived as a Vite + React + react-three-fiber
 * application. React was dropped on the way in, and that is a decision worth
 * stating rather than leaving as an omission: **react-three-fiber is a React
 * binding, and this console is not React.** Its value is declarative scene
 * composition inside a component tree; with no component tree it is three peer
 * dependencies and a build step bought for nothing. Every substantive thing in
 * that bundle — instanced meshes, a swept path tube, depth fog, a camera rig
 * that blends between two roles — is plain Three, and plain Three is a pair of
 * ESM files that need no bundler.
 *
 * So this is a *wrapper*, and it is deliberately thin: it implements the same
 * `Renderer` contract `canvas2d` implements, over the same scene modules
 * (`layout`, `palette`, `glyph`, `aggregate`). Nothing about what is drawn
 * lives here. What lives here is how.
 *
 * ── Different renderers must not be different answers ────────────────────
 *
 * This is the rule the whole seam exists to protect, and it is §24's seventh
 * acceptance applied to rendering: one composition, several surfaces, one
 * answer. So this engine does **not** get to draw more than the native one. It
 * aggregates through the identical `aggregate()` call, so a lane too small on
 * screen is one mark carrying its count here exactly as it is on canvas.
 * WebGL could happily push twenty thousand instances, and drawing them would
 * make this engine show a *different picture of the same query* — which is a
 * worse failure than being slow.
 *
 * What WebGL genuinely buys, and the reason it is worth 751KB:
 *
 *   - marks become **solids** rather than filled outlines, so the flight view
 *     has something to fly past;
 *   - depth is real fog rather than an sRGB blend per mark;
 *   - the route is a **swept tube** along the traversal, which is the thing a
 *     2D canvas cannot express at all;
 *   - the camera moves at a frame rate a per-mark path fill cannot hold.
 *
 * ── It declares that it is not native, and it can decline ────────────────
 *
 * `native: false`, which the console reports as *not air-gapped native*. And
 * `available()` answers a reason when WebGL is absent — a headless box, a
 * locked-down browser, a machine with no GPU — so `resolve()` falls back to
 * canvas2d and *says why*. A missing capability is reported, never a blank
 * canvas: the same treatment §27 gives a missing binary.
 */

import * as THREE from "./three.module.min.js";
import { aggregate, bundle } from "../aggregate.js";
import { project } from "../camera.js";
import { familyOf } from "../glyph.js";
import { ESTATE, HUES, tokenFor } from "../palette.js";
import { severityToken } from "../glyph.js";

/** One low-poly solid per glyph family. Corner count carries the family, as on
 *  canvas — a reader who learnt the silhouette in 2D keeps it in 3D. */
function solids() {
  return {
    code: new THREE.BoxGeometry(1, 1, 1),
    runtime: new THREE.ConeGeometry(0.7, 1.4, 3),
    data: new THREE.CylinderGeometry(0.6, 0.6, 1.2, 12),
    delivery: new THREE.OctahedronGeometry(0.75),
    organisation: new THREE.CylinderGeometry(0.7, 0.7, 0.9, 6),
    unknown: new THREE.SphereGeometry(0.6, 8, 6),
  };
}

function resolveTokens(canvas) {
  const style = getComputedStyle(canvas);
  const read = (name, fallback) => (style.getPropertyValue(name) || "").trim() || fallback;
  const palette = {
    surface: read("--flight-surface", "#0b1419"),
    ink: read("--flight-ink", "#dbe6ec"),
    line: read("--line", "#30363d"),
    hue: {},
  };
  for (const name of [...HUES, ESTATE, "--ok", "--warn", "--bad", "--crit"]) {
    palette.hue[name] = read(name, palette.ink);
  }
  return palette;
}

export const engine = {
  name: "three",
  native: false,

  /**
   * Whether this engine can run here at all.
   *
   * Checked before it is chosen rather than at first frame, so a machine
   * without WebGL gets a stated fallback instead of a canvas that stays black
   * while the console insists it is drawing.
   */
  available(document = globalThis.document) {
    if (!document) return "there is no document to draw into";
    try {
      const probe = document.createElement("canvas");
      const context = probe.getContext("webgl2") || probe.getContext("webgl");
      if (!context) return "this browser has no WebGL context";
      return "";
    } catch (error) {
      return `WebGL could not be started: ${(error && error.message) || error}`;
    }
  },

  mount(canvas, scene) {
    this.canvas = canvas;
    this.scene = scene;
    this.palette = resolveTokens(canvas);

    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    this.renderer.setPixelRatio(
      (canvas.ownerDocument.defaultView || {}).devicePixelRatio || 1,
    );

    this.world = new THREE.Scene();
    this.world.background = new THREE.Color(this.palette.surface);
    // Depth as contrast toward the ground, exactly as the native renderer does
    // it. Fog is the same statement made by the hardware: a value change that
    // composes with the confidence ramp rather than competing with it.
    this.world.fog = new THREE.Fog(this.palette.surface, 1, 1000);

    this.view = new THREE.PerspectiveCamera(60, 1, 0.1, 4000);

    // One instanced mesh per family, allocated for the whole graph. Marks never
    // outnumber nodes, so this is the ceiling and it is allocated once.
    const capacity = Math.max(1, scene.placed.size);
    this.geometry = solids();
    this.meshes = {};
    for (const [family, shape] of Object.entries(this.geometry)) {
      const mesh = new THREE.InstancedMesh(
        shape,
        new THREE.MeshLambertMaterial({ vertexColors: false }),
        capacity,
      );
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      mesh.count = 0;
      mesh.frustumCulled = false;
      this.meshes[family] = mesh;
      this.world.add(mesh);
    }

    this.lines = new THREE.LineSegments(
      new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({ color: new THREE.Color(this.palette.line) }),
    );
    this.lines.frustumCulled = false;
    this.world.add(this.lines);

    this.world.add(new THREE.AmbientLight(0xffffff, 0.55));
    const key = new THREE.DirectionalLight(0xffffff, 0.9);
    key.position.set(0.4, 1, 0.6);
    this.world.add(key);

    this.route = null;
    this.spare = new THREE.Object3D();
    this.tint = new THREE.Color();
    this.drawn = {
      marks: 0, edges: 0, clipped: 0, dots: 0, severe: 0,
      represented: 0, internal: 0, tiers: { node: 0, lane: 0, cluster: 0 },
    };
    return this;
  },

  draw(camera) {
    if (!this.renderer) return this.drawn;

    this.renderer.setSize(camera.width, camera.height, false);
    this.view.fov = (camera.fov * 180) / Math.PI;
    this.view.aspect = camera.width / Math.max(1, camera.height);
    this.view.near = camera.near;
    this.view.position.set(camera.eye.x, camera.eye.y, camera.eye.z);
    this.view.up.set(0, 1, 0);
    this.view.lookAt(camera.target.x, camera.target.y, camera.target.z);
    this.view.updateProjectionMatrix();

    // The identical aggregation the native renderer runs, over the identical
    // projection. This engine draws the same marks; it does not get to show a
    // different picture of the same query because it can afford more triangles.
    const projected = [];
    const tally = {
      marks: 0, edges: 0, clipped: 0, dots: 0, severe: 0,
      represented: 0, internal: 0, tiers: { node: 0, lane: 0, cluster: 0 },
    };
    let nearest = Infinity;
    let furthest = 0;

    for (const point of this.scene.placed.values()) {
      const at = project(point, camera);
      if (!at.visible) {
        tally.clipped += 1;
        continue;
      }
      projected.push({
        id: point.id, x: at.x, y: at.y, depth: at.depth,
        radius: Math.max(1, at.scale * 2.2),
        region: point.region, kind: point.kind, severity: point.severity || "",
        world: { x: point.x, y: point.y, z: point.z },
      });
      if (at.depth < nearest) nearest = at.depth;
      if (at.depth > furthest) furthest = at.depth;
    }

    const field = aggregate(projected);
    const drawn = bundle(this.scene.edges || [], field.assignment);
    tally.represented = field.represented;
    tally.internal = drawn.internal;
    tally.tiers = field.tiers;

    this.world.fog.near = Math.max(1, nearest);
    this.world.fog.far = Math.max(nearest + 1, furthest);

    const assigned = this.scene.colouring ? this.scene.colouring.assigned : new Map();
    const counts = {};
    for (const family of Object.keys(this.meshes)) counts[family] = 0;

    for (const item of field.marks) {
      // `world` is the centroid `aggregate` carried through, so a cluster sits
      // where its members are rather than where the screen happened to put it.
      const at = item.world;
      if (!at) continue;

      const family = item.kind ? familyOf(item.kind) : "unknown";
      const mesh = this.meshes[family];
      const index = counts[family];

      // Size in world units from the count it stands for, so a mark holding
      // forty nodes is visibly heavier than one holding one — the same
      // statement the native renderer makes with radius.
      const size = 6 * (1 + Math.min(1.4, Math.log2(1 + item.count) / 4));
      this.spare.position.set(at.x, at.y, at.z);
      this.spare.scale.set(size, size, size);
      this.spare.rotation.set(0, 0, 0);
      this.spare.updateMatrix();
      mesh.setMatrixAt(index, this.spare.matrix);

      const severity = severityToken(item.severity);
      const token = severity || tokenFor(item.region, assigned);
      this.tint.set(this.palette.hue[token] || this.palette.ink);
      mesh.setColorAt(index, this.tint);

      counts[family] = index + 1;
      tally.marks += 1;
      if (severity) tally.severe += 1;
    }

    for (const [family, mesh] of Object.entries(this.meshes)) {
      mesh.count = counts[family];
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    }

    const segments = [];
    for (const edge of drawn.edges) {
      const from = field.marks[edge.from];
      const to = field.marks[edge.to];
      if (!from.world || !to.world) continue;
      segments.push(
        from.world.x, from.world.y, from.world.z,
        to.world.x, to.world.y, to.world.z,
      );
      tally.edges += 1;
    }
    this.lines.geometry.setAttribute(
      "position", new THREE.Float32BufferAttribute(segments, 3),
    );
    this.lines.geometry.attributes.position.needsUpdate = true;

    this.sweep();
    this.renderer.render(this.world, this.view);
    this.drawn = tally;
    return tally;
  },

  /**
   * The route, swept as a tube along the traversal.
   *
   * This is the one thing a 2D canvas cannot express, and it is the reason the
   * flight view wants an engine at all: a path drawn as a *surface* is
   * somewhere you are, where a path drawn as a line is a diagram of somewhere
   * else. The points are the `impact` result's own order — the route is a query
   * result rendered as motion, never a generated tour.
   */
  sweep() {
    const path = this.scene.route || [];
    const signature = path.length ? path.map((point) => point.id || "").join("|") : "";
    if (signature === this.swept) return;
    this.swept = signature;

    if (this.route) {
      this.world.remove(this.route);
      this.route.geometry.dispose();
      this.route.material.dispose();
      this.route = null;
    }
    if (path.length < 2) return;

    const curve = new THREE.CatmullRomCurve3(
      path.map((point) => new THREE.Vector3(point.x, point.y, point.z)),
    );
    this.route = new THREE.Mesh(
      new THREE.TubeGeometry(curve, Math.max(16, path.length * 8), 2.2, 8, false),
      new THREE.MeshBasicMaterial({
        color: new THREE.Color(this.palette.hue["--flight-hue-5"] || this.palette.ink),
        transparent: true,
        opacity: 0.55,
      }),
    );
    this.route.frustumCulled = false;
    this.world.add(this.route);
  },

  dispose() {
    if (this.route) {
      this.route.geometry.dispose();
      this.route.material.dispose();
    }
    for (const mesh of Object.values(this.meshes || {})) {
      mesh.geometry.dispose();
      mesh.material.dispose();
    }
    if (this.lines) {
      this.lines.geometry.dispose();
      this.lines.material.dispose();
    }
    if (this.renderer) this.renderer.dispose();
    this.renderer = null;
    this.world = null;
    this.scene = null;
    this.meshes = {};
    return this.drawn;
  },
};

export default engine;
