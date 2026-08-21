/* Which screens this shell actually implements, keyed by the kernel's own key.
 *
 * The key is not a local invention: it is `Screen.key` from
 * `slpie/ui/contract.py`, the same string `GET /api/shells` reports under
 * `cannot`. That is what lets `App.tsx` ask the platform which screens the
 * air-gapped console declines and then look each one up here, rather than
 * carrying a hand-written list of what this shell is for — a list that would be
 * right on the day it was written and wrong by the next capability.
 *
 * **One file per key, and the filename is the key.** A Python test walks this
 * directory and fails if a file here names a screen the stdlib console can
 * already draw. That is the rule keeping this a companion rather than a second
 * console: a screen both shells can render is two implementations of one thing,
 * and they drift.
 */

import type { ComponentType } from "react";
import { Flight } from "./flight";

export const SCREENS: Record<string, ComponentType> = {
  flight: Flight,
};
