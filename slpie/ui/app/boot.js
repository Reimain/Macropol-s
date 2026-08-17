/* The first script the page runs.
 *
 * It exists because `script-src 'self'` forbids an inline bootstrap and there
 * is no build step to inline one for us. Its only job is to apply the stored
 * appearance before the app module loads, so the common case paints correctly
 * on the first frame.
 *
 * `index.html` hardcodes the defaults on `<html>`, so a reader who has never
 * chosen sees no flash at all. A reader whose stored choice differs from the
 * default sees exactly one frame of the default. That is accepted rather than
 * solved: the alternatives are a build step, or injecting a `<style>` element,
 * and trading a content-security-policy hole for a single frame is a bad deal.
 */

import { apply } from "./ui/density.js";

apply();
