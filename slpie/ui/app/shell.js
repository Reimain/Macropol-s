/* The composition root: the framework, wired to the page.
 *
 * Deliberately small, and deliberately not in charge yet. It mounts the
 * appearance control and nothing else, because the pre-§30 views still own the
 * screens and still hold the one `EventSource` the page has.
 *
 * That last point is the reason this file does not call `data/live.js`. Opening
 * a second connection alongside the existing one would double the server's
 * client count, double the replay on every reconnect, and make the connection
 * indicator show whichever of the two answered last. The feed moves here when
 * the console screen is authored on the framework, and not before — a rewrite
 * that replaced everything at once would trade a working interface for a large
 * diff with no way to tell which half broke.
 */

import { el, fill } from "./core/dom.js";
import { control } from "./ui/density.js";

const slot = el("appearance");
if (slot) fill(slot, control());
