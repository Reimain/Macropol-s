/* The condition model — what the interface is doing, as a machine.
 *
 * ── Why this exists before any scene does ────────────────────────────────
 *
 * Three prototypes were built for this view and all three were rejected on
 * sight, for the same reason each time: **each one tuned a scene before the
 * interaction existed.** They opened into a rendered field of scattered points,
 * which communicates one thing — *there is a lot of data* — and that is a
 * texture rather than a fact, and one everybody has already seen.
 *
 * So the interaction is a state machine and the machine comes first. A machine
 * can be asserted; "it feels right when you drag the slider" cannot. It is also
 * what stops the interface growing a seventh behaviour by accident, which is
 * the failure mode of every camera controller written without one.
 *
 * ── The load-bearing condition is the one that draws nothing ─────────────
 *
 * `CHOOSING` renders **no scene at all**. That is not an empty state to be
 * filled in later — it is the mechanism that enforces the rule the prototypes
 * broke. The graph does not appear until a selection has earned it, because a
 * graph before a question is a picture of an estate nobody asked about.
 *
 * ── Manual input always wins ─────────────────────────────────────────────
 *
 * Any pan, drag or scroll during `TRAVERSE` moves to `HELD` rather than
 * fighting the animation. A camera that wrestles the reader for the wheel is
 * the specific way this class of interface becomes unusable, and the rule that
 * prevents it is that the reader's input is never silently overridden.
 *
 * Pure: no DOM, no canvas, no timers. It answers questions about transitions
 * and holds no scene, so it is checkable with nothing running.
 */

export const CHOOSING = "choosing";
export const AIMING = "aiming";
export const APPROACH = "approach";
export const TRAVERSE = "traverse";
export const HELD = "held";
export const ARRIVED = "arrived";

export const CONDITIONS = [CHOOSING, AIMING, APPROACH, TRAVERSE, HELD, ARRIVED];

/** Which stage of choose-aim-ride each condition belongs to. */
export const STAGE = {
  [CHOOSING]: "choose",
  [AIMING]: "aim",
  [APPROACH]: "ride",
  [TRAVERSE]: "ride",
  [HELD]: "ride",
  [ARRIVED]: "ride",
};

/** What each condition means, in the words the panel shows. */
export const MEANS = {
  [CHOOSING]: "nothing selected — choose what to look at",
  [AIMING]: "a scope is selected; pick where to go",
  [APPROACH]: "the route is resolved and held at its first hop",
  [TRAVERSE]: "travelling the route",
  [HELD]: "stopped, and holding position",
  [ARRIVED]: "the last hop, and what the path is worth",
};

/**
 * The transitions, as data.
 *
 * A table rather than a switch, for the reason every other table in this
 * codebase is one: the legal moves are then *queryable*, the illegal ones are
 * refusals with a reason, and a test can walk every pair rather than trusting
 * that somebody wrote every branch.
 */
export const TRANSITIONS = {
  [CHOOSING]: { select: AIMING },
  [AIMING]: { clear: CHOOSING, aim: APPROACH },
  [APPROACH]: { clear: CHOOSING, aim: APPROACH, go: TRAVERSE, arrive: ARRIVED },
  [TRAVERSE]: { hold: HELD, touch: HELD, arrive: ARRIVED, clear: CHOOSING },
  [HELD]: { go: TRAVERSE, aim: APPROACH, clear: CHOOSING, arrive: ARRIVED },
  [ARRIVED]: { aim: APPROACH, clear: CHOOSING, go: TRAVERSE },
};

/** Conditions in which nothing spatial is drawn. */
export const BLANK = new Set([CHOOSING]);

/** Conditions in which the camera is under the interface's control. */
export const DRIVEN = new Set([TRAVERSE]);

export function draws(condition) {
  return !BLANK.has(condition);
}

export function driven(condition) {
  return DRIVEN.has(condition);
}

/**
 * One move. Returns the next condition, or the same one with a stated reason.
 *
 * Never throws. An interface that crashes on an unexpected event is worse than
 * one that declines it, and every refusal here is something a panel can show.
 */
export function next(condition, event) {
  const table = TRANSITIONS[condition];
  if (!table) {
    return {
      condition, moved: false, legal: false,
      reason: `unknown condition ${condition}`,
    };
  }
  const to = table[event];
  if (!to) {
    return {
      condition,
      moved: false,
      legal: false,
      reason: `${event} does nothing while ${condition} — ${MEANS[condition]}`,
    };
  }
  // `legal` and `moved` are separate on purpose. Re-aiming while already at
  // `APPROACH` is a declared transition onto itself: the reader asked for
  // something the machine allows and there was nothing to change. Reporting
  // that as a refusal would put a reason on screen for an action that worked,
  // which teaches people to ignore the reasons.
  return { condition: to, moved: to !== condition, legal: true, reason: "" };
}

/**
 * A small holder, so a screen does not keep the condition in a loose variable.
 *
 * `touch()` is the reader taking the controls, and it is deliberately separate
 * from `hold()`: they land in the same place but they mean different things,
 * and the panel says which happened.
 */
export function machine(start = CHOOSING, { onChange = () => {} } = {}) {
  let current = CONDITIONS.includes(start) ? start : CHOOSING;
  let last = "";

  const move = (event) => {
    const answer = next(current, event);
    last = answer.reason;
    if (answer.moved) {
      const from = current;
      current = answer.condition;
      onChange(current, from, event);
    }
    return answer;
  };

  return {
    get condition() { return current; },
    get reason() { return last; },
    get stage() { return STAGE[current]; },
    get draws() { return draws(current); },
    get driven() { return driven(current); },
    send: move,
    /** The reader touched the controls. Always wins, never overridden. */
    touch: () => move("touch"),
    can: (event) => Boolean(TRANSITIONS[current] && TRANSITIONS[current][event]),
  };
}
