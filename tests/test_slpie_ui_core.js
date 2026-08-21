/* Behavioural tests for the parts of `core/` that need no DOM.
 *
 * Run by `tests/test_slpie_ui_core.py` under node, which is available here as a
 * parser and a runtime but is not a dependency of anything: the test skips when
 * node is absent, exactly as a dispatch capability does when its binary is
 * missing (§27).
 *
 * The structural tests prove the modules load and obey the ring rule. They
 * cannot prove the rules inside them, and one of those rules — that a late
 * answer never overwrites a newer one — is the single thing standing between a
 * live console and a screen that goes backwards while somebody reads it.
 */

import assert from "node:assert/strict";

import { begin, cell, commit, LOADING, READY, reset, subscribe }
  from "../slpie/ui/app/core/store.js";
import { classify, describe, REFUSAL, FAULT, THROTTLED, TYPE_ERROR }
  from "../slpie/ui/app/core/result.js";
import { cite, confidence, short } from "../slpie/ui/app/core/format.js";
import { at, rail } from "../slpie/ui/app/engine/route.js";

let ran = 0;

function test(name, body) {
  ran += 1;
  try {
    body();
  } catch (error) {
    console.error(`FAIL ${name}\n  ${error.message}`);
    process.exitCode = 1;
    return;
  }
  console.log(`ok   ${name}`);
}

/* --- the store ---------------------------------------------------------- */

test("a newer answer replaces an older one", () => {
  reset();
  commit("graph", { status: READY, value: "old", version: 4 });
  commit("graph", { status: READY, value: "new", version: 7 });
  assert.equal(cell("graph").value, "new");
});

test("a late answer never overwrites a newer one", () => {
  // The failure this exists to prevent: a refetch triggered by event N lands
  // after the one triggered by event N+1, and the screen shows the older graph
  // with nothing on it to say so.
  reset();
  commit("graph", { status: READY, value: "new", version: 7 });
  commit("graph", { status: READY, value: "stale", version: 4 });
  assert.equal(cell("graph").value, "new");
  assert.equal(cell("graph").version, 7);
});

test("two projections at the same version are both current", () => {
  reset();
  commit("graph", { status: READY, value: "first", version: 5 });
  commit("graph", { status: READY, value: "second", version: 5 });
  assert.equal(cell("graph").value, "second");
});

test("an unversioned answer is accepted", () => {
  // Not every route is backed by a QueryResult. Refusing an answer with no
  // version would strand every hand-written read route permanently empty.
  reset();
  commit("routes", { status: READY, value: "listing" });
  assert.equal(cell("routes").value, "listing");
});

test("loading keeps the value it already had", () => {
  // A panel that empties on every event is unreadable while anything is
  // happening, which is precisely when somebody is looking at it.
  reset();
  commit("findings", { status: READY, value: [1, 2, 3], version: 2 });
  begin("findings");
  assert.equal(cell("findings").status, LOADING);
  assert.deepEqual(cell("findings").value, [1, 2, 3]);
});

test("a broken subscriber does not stop the others being told", () => {
  // One dead listener is not an outage. The same argument the event bus makes
  // on the server, for the same reason: a screen that threw during a redraw
  // would otherwise silently freeze every other screen watching the same cell.
  reset();
  const seen = [];
  const stopThrowing = subscribe("graph", () => {
    throw new Error("this subscriber is broken");
  });
  const stopWatching = subscribe("graph", (state) => seen.push(state.value));

  const noise = console.error;
  console.error = () => {};
  try {
    commit("graph", { status: READY, value: "delivered", version: 1 });
  } finally {
    console.error = noise;
  }

  assert.deepEqual(seen, ["delivered"]);
  stopThrowing();
  stopWatching();
});

test("unsubscribing stops delivery", () => {
  reset();
  const seen = [];
  const stop = subscribe("graph", (state) => seen.push(state.value));
  commit("graph", { status: READY, value: "one", version: 1 });
  stop();
  commit("graph", { status: READY, value: "two", version: 2 });
  assert.deepEqual(seen, ["one"]);
});

/* --- the result taxonomy ------------------------------------------------ */

test("a refusal is a refusal, and it is never a fault", () => {
  assert.equal(classify(403, { error: "no", refused: true }), REFUSAL);
  assert.equal(describe(403, { error: "no" }).className, "refusal");
  // The whole point: rendering policy in the danger colour teaches people that
  // policy is a bug, and then they file tickets about their own permissions.
  assert.notEqual(describe(403, { error: "no" }).className, "fault");
});

test("a server fault is a fault", () => {
  assert.equal(classify(500, { error: "boom" }), FAULT);
  assert.equal(describe(500, { error: "boom" }).className, "fault");
});

test("a type mismatch renders inline, not as a modal", () => {
  const shape = { error: "findings produces FINDINGS", type: "TypeMismatch" };
  assert.equal(classify(400, shape), TYPE_ERROR);
  assert.equal(describe(400, shape).className, "inline");
});

test("a rate limit carries what to do about it", () => {
  const shape = { error: "slow down", retry_after: 30, tier: "gold" };
  assert.equal(classify(429, shape), THROTTLED);
  assert.equal(describe(429, shape).retryAfter, 30);
  assert.equal(describe(429, shape).tier, "gold");
});

test("a success is not classified at all", () => {
  assert.equal(classify(200, { nodes: [] }), null);
  assert.equal(describe(200, { nodes: [] }), null);
});

/* --- formatting --------------------------------------------------------- */

test("a long identifier keeps both ends", () => {
  // Node ids share a long prefix, so truncating from the right alone makes
  // every row in a table look identical.
  const long = "urn:slpie:package:npm/lodash@4.17.21-with-a-long-tail";
  const cut = short(long, 12);
  assert.ok(cut.startsWith("urn:slpie:pa"));
  assert.ok(cut.endsWith("ng-tail".slice(-6)));
});

test("a short identifier is left alone", () => {
  assert.equal(short("lodash", 12), "lodash");
});

test("confidence keeps two places", () => {
  // 0.90 and 0.95 are different claims; rounding both to 1 erases the
  // distinction the evidence ladder exists to make.
  assert.equal(confidence(0.9), "0.90");
  assert.equal(confidence(0.95), "0.95");
});

test("a citation renders as file:line", () => {
  assert.equal(cite({ uri: "file:///r/app.py", line: 7 }), "/r/app.py:7");
  assert.equal(cite({ uri: "file:///r/app.py" }), "/r/app.py");
  assert.equal(cite(null), "");
});

/* --- the ride, which two shells now drive -------------------------------- */

/* One `impact` payload, deliberately out of order, so the ordering is being
 * tested rather than the input's arrangement. Both shells call `rail` on
 * exactly this shape — it is `ImpactResult.to_dict()` — so what this pins is
 * the sequence the stdlib console narrates and the built one flies. */
const PAYLOAD = {
  root: "urn:slpie:package:npm/lodash",
  impacted: [
    { node_id: "d", distance: 2, confidence: 0.90, display: "billing", kind: "service" },
    { node_id: "b", distance: 1, confidence: 0.40, display: "vault-sdk", kind: "package" },
    { node_id: "a", distance: 1, confidence: 0.90, display: "payments", kind: "service" },
    { node_id: "c", distance: 2, confidence: 0.90, display: "audit", kind: "service" },
  ],
};

test("the rail is ordered by distance, then confidence, then id", () => {
  // A total order, so one query is one ride every time. Without the id the two
  // hops sharing a distance and a confidence would sort arbitrarily and two
  // runs of one question would travel two different routes.
  const route = rail(PAYLOAD);
  assert.deepEqual(route.hops.map((hop) => hop.id), ["a", "b", "c", "d"]);
});

test("the floor falls to the weakest hop and never recovers", () => {
  // A chain is exactly as strong as its weakest link — the same rule L7
  // applies when it propagates a minimum rather than a product. A floor that
  // climbed back after a 0.40 hop would be claiming the inference was undone.
  const route = rail(PAYLOAD);
  assert.deepEqual(route.hops.map((hop) => hop.floor), [0.9, 0.4, 0.4, 0.4]);
  assert.equal(route.floor, 0.4);
});

test("scrubbing lands on the same hop the ride would be on", () => {
  // The scrubber and the played ride are one mechanism: both do nothing but
  // choose a moment, and `at()` turns a moment into a hop. If a shell computed
  // the index itself, dragging the timeline would disagree with playing it —
  // by a rounding error per frame, which is the kind of drift nobody reports
  // and everybody distrusts.
  const route = rail(PAYLOAD);
  let elapsed = 0;
  route.hops.forEach((hop, index) => {
    assert.equal(at(route, elapsed + hop.seconds / 2).index, index);
    elapsed += hop.seconds;
  });
  assert.equal(at(route, elapsed + 1).done, true);
});

test("a short answer is not flown", () => {
  // Below the flight floor the animation has not earned its place: a two-hop
  // answer travelled cinematically is a list wearing a 3D scene.
  const short_route = rail({ root: "r", impacted: PAYLOAD.impacted.slice(0, 2) });
  assert.equal(short_route.animates, false);
  assert.ok(short_route.summary.includes("too short to fly"));
  assert.equal(rail(PAYLOAD).animates, true);
});

console.log(`\n${ran} checks`);
