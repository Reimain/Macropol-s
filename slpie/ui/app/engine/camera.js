/* Projection, and nothing else.
 *
 * Split out from the renderer on purpose: **a camera bug looks exactly like a
 * layout bug.** A node in the wrong place could be a bad coordinate or a bad
 * matrix, and with the two in one module the only way to tell is to squint at a
 * canvas. Here the maths touches no DOM and no rendering context, so it is
 * exercised against known points with no browser in the way, and a failure names
 * which half is wrong.
 *
 * Nothing here needs a scene graph, materials or lighting. It needs points
 * projected from three dimensions to two: a basis, a perspective divide, and a
 * depth to sort by. That is one small module rather than a dependency, which is
 * the measured version of the argument in `contract.js` — the case for a
 * third-party engine has to be made against a workload, and this is the
 * workload it would have to beat.
 *
 * The convention, stated once so every reader of the numbers agrees:
 *
 *   - right-handed, with **+Y up** and the camera looking along its own forward
 *   - screen space has **+Y down**, because that is what a canvas context uses,
 *     so the projection flips it exactly once and no caller has to remember
 *   - `depth` is distance along forward, in world units, so it sorts painters'
 *     order directly and is the number atmospheric fade reads
 */

const EPSILON = 1e-9;

export function vector(x = 0, y = 0, z = 0) {
  return { x, y, z };
}

function subtract(left, right) {
  return vector(left.x - right.x, left.y - right.y, left.z - right.z);
}

function cross(left, right) {
  return vector(
    left.y * right.z - left.z * right.y,
    left.z * right.x - left.x * right.z,
    left.x * right.y - left.y * right.x,
  );
}

function dot(left, right) {
  return left.x * right.x + left.y * right.y + left.z * right.z;
}

export function length(point) {
  return Math.sqrt(dot(point, point));
}

function normalise(point) {
  const size = length(point);
  return size < EPSILON ? vector(0, 0, 1) : vector(point.x / size, point.y / size, point.z / size);
}

/**
 * A camera, resolved once so `project` stays arithmetic.
 *
 * The basis and the focal length are computed here rather than per point:
 * recomputing them inside the loop is how a projection that is fine at two
 * hundred nodes becomes the frame budget at twenty thousand, and it is a
 * mistake that hides because both versions are correct.
 *
 * `fov` is the **vertical** field of view in radians, so the horizontal one
 * follows from the aspect rather than being a second thing to keep in step.
 */
export function look(
  eye,
  target,
  { up = vector(0, 1, 0), fov = Math.PI / 3, near = 0.1, width = 1, height = 1 } = {},
) {
  const forward = normalise(subtract(target, eye));
  // `forward x up`, not `up x forward`. Both are "the right vector" in some
  // convention, and picking the wrong one mirrors the whole scene horizontally
  // — a bug that renders perfectly and is only visible if you happen to know
  // which node should be on the left.
  //
  // This is the right-handed convention every graphics API uses, and it has the
  // consequence people find counter-intuitive: **looking along +Z, world +X is
  // on your left**, because you have turned to face the opposite way from the
  // one the axes were drawn for. An earlier revision of this file "fixed" that
  // surprise by swapping the operands, which agreed with the intuition and
  // disagreed with every other renderer on earth. It was caught by projecting
  // the same point through this module and through a vendored engine and
  // finding them 252 pixels apart — which is the argument for having two
  // renderers rather than one.
  let right = cross(forward, up);
  if (length(right) < EPSILON) {
    // Looking straight along `up`: the cross product degenerates and every
    // point would land on the centre. Tilt the reference rather than returning
    // a camera that silently draws nothing.
    right = cross(forward, vector(up.z, up.x, up.y));
  }
  right = normalise(right);

  return Object.freeze({
    eye, target, forward, right,
    up: normalise(cross(right, forward)),
    fov, near, width, height,
    focal: (height / 2) / Math.tan(fov / 2),
  });
}

/**
 * One point, in screen coordinates.
 *
 * `visible` is false for anything at or behind the near plane. Callers must
 * honour it: a point behind the eye still yields finite numbers — mirrored
 * through the origin — and drawing them puts the scene *behind* you on screen,
 * which is the classic way a hand-rolled projection looks haunted rather than
 * broken.
 */
export function project(point, camera) {
  const offset = subtract(point, camera.eye);
  const depth = dot(offset, camera.forward);
  if (depth <= camera.near) {
    return { x: 0, y: 0, depth, scale: 0, visible: false };
  }
  const scale = camera.focal / depth;
  return {
    x: camera.width / 2 + dot(offset, camera.right) * scale,
    y: camera.height / 2 - dot(offset, camera.up) * scale,
    depth,
    scale,
    visible: true,
  };
}

/**
 * How much of the far distance has faded into the ground.
 *
 * Depth is carried by **contrast, not hue**: distant marks lose contrast toward
 * the surface colour, which is a value change and therefore composes with the
 * confidence ramp instead of competing with it. The mock this tier is built
 * from used glow for depth, and that is the one part of its look that has to go
 * — hue in this product means confidence and severity, and a renderer that
 * spends it on distance makes both unreadable.
 */
export function haze(depth, { near = 0, far = 1 } = {}) {
  if (far <= near) return 0;
  const share = (depth - near) / (far - near);
  return share <= 0 ? 0 : share >= 1 ? 1 : share;
}

/**
 * A camera that holds an extent in frame, from a given direction.
 *
 * Deterministic in the same way the layout is: the same extent and the same
 * direction produce the same camera, so two builds of one graph are two
 * identical pictures rather than two similar ones.
 */
export function frame(extent, { fov = Math.PI / 3, width = 1, height = 1, pitch = 0.6, yaw = 0 } = {}) {
  const centre = vector(
    (extent.min.x + extent.max.x) / 2,
    (extent.min.y + extent.max.y) / 2,
    (extent.min.z + extent.max.z) / 2,
  );
  const span = Math.max(
    extent.max.x - extent.min.x,
    extent.max.y - extent.min.y,
    extent.max.z - extent.min.z,
    1,
  );
  // Far enough back that the whole span subtends less than the field of view,
  // with a margin so the outermost marks are not clipped by their own radius.
  const distance = (span / 2) / Math.tan(fov / 2) * 1.4;
  const eye = vector(
    centre.x + distance * Math.cos(pitch) * Math.sin(yaw),
    centre.y + distance * Math.sin(pitch),
    centre.z + distance * Math.cos(pitch) * Math.cos(yaw),
  );
  return look(eye, centre, { fov, width, height, near: Math.max(0.1, span / 1000) });
}
