# The console

The interface is part of the kernel, not a client of it. It ships inside
`slpie/ui/app`, it is served by a standard-library HTTP server, and it has **no
build step, no bundler and no third-party package** — invariant 4 applies to the
interface exactly as it does to everything else, which is why this console runs
inside an air-gapped network where a React build would not.

```bash
slpie ui                 # open it
slpie ui --port 9000     # somewhere else
slpie ui --once          # start, print the URL, return — for scripts
```

<p><strong><a href="../demo/index.html">Try it in the browser →</a></strong> — the
shipping modules, running against a recording of a real scan. Only the transport
is replaced.</p>

```{note}
That link is a raw anchor rather than Markdown on purpose. The demo page is
generated *after* Sphinx runs — the publish job writes it to `/demo/`,
a sibling of this reference rather than a page inside it — so a Markdown link resolves against the document tree, finds no
such document, and silently renders as an inert `<span class="xref myst">`
instead of a link. An anchor is passed through untouched.
```

---

## What it is for

Most operations surfaces render everything as equally true. This one must not:
the platform's whole claim is that it knows the difference between something it
**read**, something it **inferred**, and something it **never looked at** — so a
reviewer has to be able to tell those apart at a glance, all day, without
reading a number.

That single requirement is what decides nearly every choice below.

---

## Two registers, and they are two instruments

Users work in one of two registers, switched from the top bar and remembered
between visits. This is a **token axis**, not two layouts — one codebase, one
column spec, one keyboard model — but the difference is structural rather than
metric, because "the same interface at two sizes" is a zoom control and not a
register.

### Dense

The register for somebody working a list all day, and the one it opens in. It is
built like the rich desktop clients those people already live in: ruled columns,
zebra rows, a sticky header you sort by clicking, a selected row, arrow-key
navigation, and a status bar saying what is in view.

![The dense register: a rich-client data grid, sorted by group with a row
selected and a status bar reading "48 rows, sorted by Group, row 4 of
48"](_static/ui/verbs-dense.png)

Numbers are right-aligned on tabular figures, so a wrong order of magnitude is
visible down a column without reading a single value.

### Calm

The same screen, the same data, the same column spec — with the vertical rules
and the stripes gone, text wrapping instead of truncating, and room around it.
A document rather than an instrument.

![The same screen in the calm register: no rules, no stripes, wrapped text and
more space](_static/ui/verbs-calm.png)

**Why the vertical rules exist in one and not the other.** An ordinary table is
*scanned*, and without vertical rules the eye reads rows — which is what
scanning wants, so `base.css` drops them. A grid is *worked*, and the task is
comparing one field down a column, where the rule is what keeps the eye inside
it. Both are right; the register is what decides which applies.

---

## The graph

The screen the platform should be judged on, and the one no rival can currently
draw.

Every competitor renders a topology — a flow map, a lineage diagram — in which
**every edge is equally true**. The picture asserts that the system knows how
these things connect, and says nothing about how it found out. Here the
*stroke* carries the evidence:

| Stroke | Means | Typical evidence |
|---|---|---|
| solid, heavy | surveyed | a lockfile pin, a runtime trace |
| solid, hairline | recorded | a manifest, a static import |
| dashed | inferred | a configuration reference, a DI registration |
| dotted | guessed | reflection, a name heuristic — capped at 0.60 |

![The graph screen: a hero figure reading "Read directly 100%", the certainty
split as a stacked bar, and the estate drawn as a node-link diagram grouped by
kind](_static/ui/graph.png)

It leads with the number none of the alternatives can compute: **how much of what
you are being told was read directly**, rather than joined, inferred or guessed.
Every dependency tool answers "how many dependencies do you have"; none of them
answers "and how much of that do you actually know."

Selecting a node dims everything it does not touch rather than hiding it — the
shape of what is *not* connected is information too — and offers the
compositions worth running next as real, copyable links.

![A node selected: the rest of the estate dimmed, and a panel resolving express
to pkg:npm/express at 0.99, corroborated, with eight
connections](_static/ui/graph-selected.png)

**The layout is deterministic — there is no force simulation.** A physics layout
settles somewhere different on every run, so the same graph is a different
picture each time: you cannot point at it in a review, cannot compare two
screenshots, and cannot tell "the architecture changed" from "the simulation
landed elsewhere". Nodes are grouped into columns by kind, ordered within a
column by degree, and wrapped at eleven. Same graph in, same picture out — the
snapshot digest's property, applied to the drawing of it.

---

## Colour carries meaning, and only one meaning each

Two scales, kept rigorously apart:

**Certainty** takes an ordinal blue ramp, dark for known and pale for inferred,
with a neutral off-ramp for *unknown* — because "nothing was read here" is not
the low end of a confidence scale, it is absent from the scale.

**Severity** takes the reserved status palette, and only ever for a finding.

The separation is the point. *Confidence is not goodness.* An edge learned from
a name heuristic scores 0.25; painted red beside a green lockfile pin it reads as
"something is wrong here", and nothing is wrong — it is a dependency the platform
is less sure of. A reviewer chasing every amber edge is chasing the platform's
own uncertainty.

The ramp is validated rather than eyeballed, in both modes, and the dark steps
are **selected for the dark ground** rather than flipped:

```
validate_palette.js "#0b4f9e,#1f6fc4,#5192d6,#86b0e2" --ordinal --mode light
  → ALL CHECKS PASS  (monotone L · adjacent ΔL ≥ 0.06 · light end 2.25:1)
```

The green/amber/red alternative that was tried first measured **deutan ΔE 1.4** —
one colour to a red-green colourblind reader — which is the measured reason this
console does not use one.

![The dark theme, whose ramp is selected for its own ground rather than inverted
from the light one](_static/ui/graph-dark.png)

Colour is never the only channel: every severity, verdict, certainty band and
target state also carries a glyph and a word, so the palette survives a
colour-blind reviewer and a screenshot pasted into a ticket — which is where most
of these end up.

---

## Every screen is a composition

The console has no private capabilities. Everything it does is a verb from the
one registry (§24), which is why the verb palette can be generated rather than
maintained, and why a verb that cannot follow what is currently flowing is
**disabled rather than hidden** — hiding it would hide the type graph, and the
shape of what can follow what is the thing worth learning.

![The compose screen: the verb palette grouped by family, with unusable verbs
dimmed rather than removed](_static/ui/compose.png)

The same principle governs the way in. Pointing the platform at a folder is not
a button that does a hidden thing — it is a composition, shown before it runs,
and the string displayed is the string that executes:

```
discover --path <folder> | link | findings
```

![The console with nothing open: a path field and drop zone, and beneath it the
composition it will run with each stage explained](_static/ui/console.png)

```{note}
A browser will not hand a web page a dropped folder's absolute path — it gives
the name and the relative paths inside, and no flag changes that. The crawler
runs server-side and walks the *server's* filesystem, so the text field is
authoritative and the drop zone fills in what the browser will admit to. A
control that looked like it accepted a drop and silently read the wrong
directory would be the worse failure.
```

---

## Screens are shipped as data

A framework stays generalistic. It ships `Table` and `List` and `Item`, and
every product built on it ends up wearing the framework's vocabulary. This
console does the opposite, and it costs a dictionary rather than a framework.

**A screen is a list of blocks, and a block names a component.**
`slpie/ui/contract.py` emits the manifest — path, title, what it reads, which
events invalidate it, and now which components to draw and with which columns —
and `app/ui/components.js` is the dictionary those names index into. Each entry
is an ordinary piece of CSS, HTML and JavaScript; what makes it addressable is
that it is reachable by key.

```json
{
  "component": "grid",
  "source": "GET /api/apim/throttles",
  "select": "tiers",
  "columns": [
    {"key": "name",     "label": "Tier"},
    {"key": "requests", "label": "Requests", "align": "right", "format": "count"}
  ]
}
```

Two rules keep it honest. The addressable set is held in Python *and* in the
browser and a test asserts they are equal in both directions — a name with no
implementation would render a blank area, and an implementation with no name
would be unreachable code. And a cell renderer is a function, so it cannot
travel as data: `format` names the behaviour instead, exactly as `component`
names the component one level up.

Where nobody could declare the shape — an arbitrary route's body is not
knowable from Python — the block asks for `auto`, and the browser looks at what
actually arrived. Rows become a table, fields become metrics, and anything else
says it could not be laid out rather than pretending. Declaring columns for
every inspector by hand would be a list that drifts the first time a payload
changes; reading the rows that arrived cannot drift, because there is nothing
to keep in step.

**Authored beats composed beats dumped.** A screen with a hand-built module is
drawn by that module and ignores its blocks entirely. Composing is what the
other screens do instead of printing a payload.

---

## The same platform, in your words

The kernel knows what a thing *is*. What you call it is a separate fact, and
keeping the two apart is what lets one console read as a platform-engineering
tool to one team and a compliance tool to another.

```yaml
# .slpie/lexicon/platform-engineering.yaml
terms:
  node:    service
  finding: risk
  station: { word: fleet, gloss: The estate this console is attached to. }
```

The default vocabulary is derived from the code — the modules under
`slpie/domain/`, whose package docstring already calls itself *"the vocabulary
every other layer is written in"*, plus the package names — so the platform
cannot offer a word its own code does not use, and every term carries the module
it came from.

**A profile may rename the product. It may never rename a control.** Every
severity, gap kind, verdict and target state is protected, and the protected set
is derived from the enums themselves rather than listed — so a severity added
next year is protected the day it is added. A tenant renaming *refused* to
*pending* is how a control becomes invisible, and it would be invisible to us
reading their ledger too.

---

## Your device holds the screens; the server holds the truth

The ledger is authoritative in one place. The graph is a read model that
replicates freely. A replica caches up to a ledger sequence and, past its
freshness budget, reports how far behind it is rather than answering as though
it were fresh — and §23 says in as many words that this does not weaken because
the replica is a laptop.

So the browser is the smallest replica in that model. Answers the server marks
keepable are held in IndexedDB and restored on the next visit, and **a restored
answer says how old it is**: *"The world has moved on since this answer (ledger
812, answered at 407)."*

This is tractable here and awkward elsewhere for one reason. Client state
libraries persist arbitrary state and then face cache invalidation with TTLs and
refetch heuristics, because the server gave them nothing to order by. Every
answer here carries its version and the ledger's, so **invalidation is ordering,
not guessing** — and hydration goes through the same version check a network
answer does, which means a cell from disk is older by construction and can only
fill a gap, never win a race.

Three rules are not negotiable:

- **A different principal wipes the device, it does not filter it.** A filtered
  view of another tenant's cells is still their bytes on a shared machine.
- **A refused quota degrades, never crashes.** A device declining to store is a
  refused capability: fall back to memory, keep answering, say what it cost.
- **Only what the contract marks keepable is kept.** A 409 "no environment
  open" held past the moment one opens is a console insisting the platform is
  empty.

The dividend is on the server: if the device holds the screens, the API tier
holds no per-session state at all, which is what makes a horizontally scaled
tier viable without sticky routing and a session store in front of it.

---

## Navigation is a map, not a table of contents

The rail lists **destinations**. A screen that is a *view of* something —
Node and Impact and Cycles are things you look at about a graph — declares a
parent in the generated manifest and appears as a tab on that parent's page,
never as a rail row beside it. Thirty-six screens reduce to thirteen
destinations.

Every list row is a real `<a href="#/…">`, so middle-click and copy-link work —
the place most hand-rolled consoles fail. Routing is hash-based deliberately: a
cached `index.html` then serves every route offline with no service-worker
cooperation.

---

## What it refuses to do

- **`innerHTML` appears in zero files.** The developer portal renders
  operator-authored API descriptions, and `script-src 'self'` does nothing about
  a string that becomes markup. There is no safe subset to remember, so there is
  no exception to make.
- **No component declares a raw size.** Every dimension is a token; a test greps
  for `\d+px` outside the token files. That single check is what keeps the
  density axis real rather than decorative.
- **Nothing reaches an external origin.** No CDN, no webfont, no analytics —
  asserted by walking every shipped file.
- **A hidden screen is a convenience, never a control.** Navigation is built
  from what the platform said is permitted, and a screen absent from the menu
  still returns 403 to a direct request.

---

## Regenerating what is on this page

```bash
make ui-screenshots      # the images above, from a real scan through a browser
make ui-demo             # the interactive page linked at the top
```

The screenshots are generated rather than pasted — an image dragged into a docs
folder is correct on the day it is taken and silently wrong afterwards, and
unlike prose nobody rereads an image to check. They are nevertheless committed,
because the documentation builds from a kernel-only install with no browser in
it.
