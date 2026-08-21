/* A pub/sub, twenty lines, no dependency.
 *
 * The live feed fans out through here: one `EventSource` for the whole app,
 * delivered to whichever screens are mounted. Per-screen sockets would open one
 * connection per view and each would need its own reconnect and its own resume
 * point, which is three copies of the hardest part of the feed.
 */

const listeners = new Map();

export function on(topic, listener) {
  if (!listeners.has(topic)) listeners.set(topic, new Set());
  listeners.get(topic).add(listener);
  return () => listeners.get(topic)?.delete(listener);
}

export function emit(topic, payload) {
  for (const listener of listeners.get(topic) || []) {
    try {
      listener(payload);
    } catch (error) {
      console.error("listener failed for", topic, error);
    }
  }
  // `*` sees everything, which is what the activity feed subscribes to.
  if (topic !== "*") {
    for (const listener of listeners.get("*") || []) {
      try {
        listener(payload, topic);
      } catch (error) {
        console.error("wildcard listener failed", error);
      }
    }
  }
}

export function clear() {
  listeners.clear();
}
