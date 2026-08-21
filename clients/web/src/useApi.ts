/* `useApi` — one read, four states, and no fabricated fifth.
 *
 * React's own story for "fetch something" is three hooks and a race condition,
 * and every screen that writes it by hand writes it slightly differently. So it
 * is written once, here, and every screen in this shell reads through it.
 *
 * Two properties are worth stating because they are the ones hand-written
 * versions lose:
 *
 *   * **A late answer to a stale question never lands.** The effect captures a
 *     `live` flag and drops the response if the deps changed while it was in
 *     flight. Without it, switching selection twice quickly leaves whichever
 *     request the network happened to finish last on screen — which is the
 *     browser telling the reader a confident answer to a question they have
 *     already moved on from.
 *   * **Empty is not an error, and neither is a refusal.** `Loaded` keeps them
 *     apart (see `api.ts`), so a new environment reads as new rather than as
 *     broken, and a 403 renders as policy rather than as a fault.
 */

import { useCallback, useEffect, useState } from "react";
import { idle, load, type Loaded } from "./api";

export type Reloadable<T> = Loaded<T> & { reload: () => void };

/**
 * Run `read` on mount and whenever `deps` change.
 *
 * `read` is not itself a dependency: an inline arrow is a new function every
 * render, so depending on it would refetch forever. The caller states what the
 * read actually depends on, which is the honest answer and the only one that
 * terminates.
 */
export function useApi<T>(read: () => Promise<T>, deps: unknown[] = []): Reloadable<T> {
  const [state, setState] = useState<Loaded<T>>(idle<T>());
  const [attempt, again] = useState(0);

  const reload = useCallback(() => again((count) => count + 1), []);

  useEffect(() => {
    let live = true;
    setState(idle<T>());
    load(read).then((settled) => {
      if (live) setState(settled);
    });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, attempt]);

  return { ...state, reload };
}
