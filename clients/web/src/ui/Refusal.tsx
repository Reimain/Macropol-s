/* A refusal, rendered as policy — never as a fault.
 *
 * The distinction has to be legible at a glance, and the reason is behavioural
 * rather than aesthetic: a 403 painted in the danger colour teaches readers
 * that policy is a bug, and then they file tickets about the platform working
 * correctly. So a refusal wears the accent, a fault wears the danger colour,
 * and this component is the only place in the shell that draws either.
 *
 * It renders three things the gateway already computed and most consoles throw
 * away: the sentence in the platform's own words, **which step refused** — the
 * §26 rule that a refusal names the rule that stopped it — and **what would
 * allow the call**. Without the third, a refused reader has to ask an operator;
 * with it, they can act.
 */

import type { Loaded } from "../api";

export function Refusal({ answer, subject }: { answer: Loaded<unknown>; subject: string }) {
  if (!answer.error) return null;

  // A fault is the platform's problem and says so. Anything the gateway did not
  // mark `refused` is one — including a network failure, which is not a
  // decision anybody made.
  if (!answer.denied) {
    return (
      <p className="fault">
        {subject} could not be read — {answer.error}. This is a platform fault,
        not your input.
      </p>
    );
  }

  const { message, stage, obligation, retryAfter } = answer.denied;
  return (
    <div className="refusal">
      <p>{message}</p>
      {stage && <p className="small muted">Refused at the {stage} step.</p>}
      {obligation && <p className="small">What would allow it: {obligation}.</p>}
      {retryAfter && <p className="small">Try again in {retryAfter}s.</p>}
    </div>
  );
}
