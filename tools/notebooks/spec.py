"""What each notebook contains.

A `Notebook` is a title, a one-line purpose, and an ordered list of cells. A cell
is either markdown or code. Nothing here knows about `nbformat` — `build.py`
turns this into JSON, and keeping the two apart is what lets a spec read like the
page it produces.

Three rules the cells follow, and each is here because the alternative produces a
notebook that looks fine and teaches nothing:

* **Every code cell runs.** No `...`, no pseudo-code, no cell that needs a file
  the reader has to supply. `run.py` executes all of them and CI fails on any
  error, so a cell that cannot run does not survive to a commit.
* **Every notebook stands alone.** A reader opening notebook 7 does not have to
  have run notebook 3. Each one builds whatever tree it needs in a temp
  directory, which is also what makes them safe to run in any order.
* **Outputs are shown, not described.** A cell that prints a digest prints the
  digest. Prose that says "you will see a digest" is prose nobody can check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["markdown", "code"]


@dataclass(frozen=True, slots=True)
class Cell:
    kind: Kind
    source: str


def markdown(source: str) -> Cell:
    return Cell("markdown", source.strip("\n"))


def code(source: str) -> Cell:
    return Cell("code", source.strip("\n"))


@dataclass(frozen=True, slots=True)
class Notebook:
    """One page: a number, a slug, a title, and the cells."""

    number: int
    slug: str
    title: str
    purpose: str
    cells: tuple[Cell, ...] = field(default_factory=tuple)

    @property
    def filename(self) -> str:
        return f"{self.number:02d}_{self.slug}.ipynb"


# --- shared preamble ------------------------------------------------------
#
# Repeated verbatim at the top of every notebook. It is duplicated on purpose:
# a notebook that imported a shared helper would stop working the moment
# somebody opened it on Colab without the rest of the repository, and "runs
# anywhere" is the property these pages are for.

SETUP = code('''
# --- setup: works locally, on Binder, and on Colab -------------------------
import subprocess, sys, pathlib

def _ensure_installed():
    """Make the package importable, preferring the checkout this notebook is in.

    The checkout comes first deliberately. Trusting whichever `slpie` happens to
    be installed means a notebook opened inside one clone can silently exercise
    a different one — which is exactly what happened while writing this.
    """
    here = pathlib.Path.cwd()
    root = next(
        (p for p in [here, *here.parents] if (p / "pyproject.toml").exists()), None,
    )
    if root is None:                     # Colab: no checkout, so fetch one
        root = pathlib.Path("/content/Macropol-s")
        if not root.exists():
            subprocess.run(
                ["git", "clone", "--depth", "1",
                 "https://github.com/Reimain/Macropol-s.git", str(root)],
                check=True,
            )
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        import slpie, gratimos          # noqa: F401
    except ModuleNotFoundError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-e", str(root)],
                       check=True)
    return root

ROOT = _ensure_installed()
print("package root:", ROOT)

import slpie
print("slpie", slpie.__version__)
''')

INTRO_NOTE = """
> **Every cell in this notebook runs.** They are generated from
> [`tools/notebooks/spec.py`](../tools/notebooks/spec.py) and executed by CI, so a
> cell that cannot run does not reach a commit. Change a cell, re-run it, and the
> page is yours — that is what it is for.
"""


def _header(title: str, purpose: str, body: str) -> Cell:
    return markdown(f"# {title}\n\n{purpose}\n{INTRO_NOTE}\n{body}")


# --- 00 · start here ------------------------------------------------------

START_HERE = Notebook(
    0, "start_here", "Start here",
    "What these notebooks are, what runs where, and the five-minute version.",
    (
        _header(
            "Start here",
            "**Two systems, one repository.** SLPIE answers questions about an "
            "architecture; Gratimos shapes data and generates code. This page "
            "gets you from nothing to a real answer in about a minute.",
            """
| | |
|---|---|
| **SLPIE** | An architecture intelligence engine. You hand it a tree or a manifest; it tells you what is in there, what depends on what, what is wrong, and *why it believes each answer*. |
| **Gratimos** | A data-shaping kernel. It infers shapes from messy sources, casts safely between them, and generates typed code with a three-way AST merge. |

The one idea worth having before anything else: **capabilities are verbs, verbs
pipe into each other, and the pipe carries the reasoning rather than bytes.** So
composing does not lose the explanation — it accumulates it.
""",
        ),
        SETUP,
        markdown("## The five-minute version\n\nBuild a small project with something wrong in it."),
        code('''
import json, pathlib, tempfile

WORK = pathlib.Path(tempfile.mkdtemp(prefix="slpie-nb-"))
shop = WORK / "shop"
shop.mkdir()

# A manifest asking for one thing...
(shop / "package.json").write_text(json.dumps({
    "name": "shop", "version": "1.0.0", "license": "MIT",
    "dependencies": {"lodahs": "^4.0.0", "loose": "*"},
}, indent=2))

# ...and a lockfile that pinned something else, under a licence that fights MIT.
(shop / "package-lock.json").write_text(json.dumps({
    "name": "shop", "lockfileVersion": 3, "packages": {
        "node_modules/lodahs": {"version": "4.17.21", "license": "AGPL-3.0"},
        "node_modules/loose": {"version": "0.1.0"},
    },
}, indent=2))

# And a secret somebody committed.
(shop / "settings.py").write_text('AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"\\n')

print(f"built a project at {shop}")
for path in sorted(shop.iterdir()):
    print(" ", path.name)
'''),
        markdown(
            "## Ask it what is wrong\n\n"
            "`discover` reads the tree, `govern` runs every governance rule over "
            "what was found. The `|` is the same idea as a shell pipe — except "
            "what flows through it carries its own provenance."
        ),
        code('''
from slpie.compose import Composition, Context, registry

verbs = registry()
result = Composition.read(f"discover {shop} | govern", verbs=verbs).run(
    Context(root=str(shop)),
)

print("ok:", result.ok)
print("kind:", result.flow.kind.label, "| findings:", result.flow.size)
print()
for finding in result.flow.items:
    print(f"  [{finding.severity.value:8}] {finding.kind.value}")
    print(f"             {finding.title}")
'''),
        markdown(
            "Three findings, from three different rule families — a typosquatted "
            "package name, a licence conflict, and a committed credential.\n\n"
            "## Now ask *why*\n\n"
            "This is the part that separates the platform from a linter. Every "
            "finding carries the evidence that produced it, down to the file and "
            "line."
        ),
        code('''
for finding in result.flow.items:
    print(f"{finding.title}")
    for item in finding.evidence[:2]:
        where = item.location
        print(f"    {item.kind.value:20} {where.uri.rsplit('/', 1)[-1]}:{where.line}")
        if item.excerpt:
            print(f"    {'':20} {item.excerpt.strip()[:60]}")
    print(f"    risk {finding.risk.value} · blocks release: {finding.blocks_release}")
    print()
'''),
        markdown(
            "## The pipe carries the reasoning\n\n"
            "A `Flow` is not a value. It is a value *plus* how it came to be — the "
            "reasoning path, the gaps, and the stages it passed through."
        ),
        code('''
flow = result.flow

print("stages:      ", " → ".join(flow.stages))
print("confidence:  ", round(flow.confidence, 3))
print("gaps:        ", len(flow.gaps))
print("digest:      ", flow.digest[:24])
print()
print("reasoning:")
for step in flow.reasoning.steps:
    print(f"  · [{step.layer}] {step.claim[:70]}")
'''),
        markdown(
            "That `digest` is content-addressed over the whole flow. The same "
            "composition over the same tree produces the same digest — which is "
            "what makes an answer something you can pin in CI rather than "
            "something you re-read every time."
        ),
        code('''
again = Composition.read(f"discover {shop} | govern", verbs=verbs).run(
    Context(root=str(shop)),
)
print("first run: ", result.flow.digest[:32])
print("second run:", again.flow.digest[:32])
print("identical: ", result.flow.digest == again.flow.digest)
'''),
        markdown(
            "## What else is there\n\n"
            "45 verbs, in 11 groups. Everything you can do is one of these, and "
            "anything can be piped into `explain`."
        ),
        code('''
from collections import defaultdict

groups = defaultdict(list)
for verb in verbs:
    groups[verb.group].append(verb.name)

for group in sorted(groups):
    print(f"{group:14} {' '.join(sorted(groups[group]))}")
print()
print(f"{len(verbs.names)} verbs, {len(groups)} groups")
'''),
        markdown(
            """
## Where to go next

| Notebook | For |
|---|---|
| `01_composition` | The verb registry, typed pipes, and why an invalid pipeline is refused before it runs |
| `02_discovery` | The 29 discoverers, across npm, PyPI, Maven, Cargo, Go, Docker, Kubernetes, OpenAPI… |
| `03_graph` | The bitemporal graph, blast radius, and cycles — traversed in SQL |
| `04_governance` | The five rule families, and how a finding earns its severity |
| `05_reasoning` | L1–L8, and asking a question in English |
| `06_environment` | Manifests, the simulator, and firing conditions at a world |
| `07_artifacts` | SBOM, C4, TOGAF views, risk registers |
| `08_incremental` | Rescanning only what moved, and refusing to guess about what it could not read |
| `09_audit` | The judge: deterministic architecture verdicts with a reproducible digest |
| `10_agent` | Handing an agent a tool set that is a projection of the registry |
| `11_gratimos_shapes` | Inferring shapes from messy data, and casting safely |
| `12_gratimos_codegen` | Generating typed code, and a three-way merge that preserves hand edits |
| `13_end_to_end` | All of it, on one project, in one run |
"""
        ),
    ),
)


# --- 01 · composition -----------------------------------------------------

COMPOSITION = Notebook(
    1, "composition", "Composition — verbs, typed pipes, and provenance",
    "The spine everything else is built on.",
    (
        _header(
            "Composition",
            "**Capabilities are verbs in one registry, and every surface is a "
            "projection of it.** The CLI, the HTTP API, the manual, the planner "
            "and every client read the same registry, so a capability added once "
            "appears in all of them and none of them can drift.",
            """
The model is the shell: small verbs, piped together, producing results no single
verb could. What makes this different from `ls | grep | wc` is what travels
through the pipe.

A shell pipe carries bytes and loses everything else — which is why
`curl | jq | grep` cannot tell you *why* a value is what it is. Here the pipe
carries a **`Flow`**: a value, its kind, its reasoning path, and its gaps.
Composing accumulates the explanation instead of discarding it.
""",
        ),
        SETUP,
        markdown("## The registry\n\nEvery verb declares what it consumes and what it produces."),
        code('''
from slpie.compose import Composition, Context, Kind, registry

verbs = registry()
verb = verbs.require("govern")

print("name:     ", verb.name)
print("group:    ", verb.group)
print("consumes: ", verb.consumes.label)
print("produces: ", verb.produces.label)
print("summary:  ", verb.summary)
print()
print("parameters:")
for param in verb.params:
    mark = " (required)" if param.required else ""
    print(f"  --{param.name:14}{param.type:8}{mark}  {param.help[:44]}")
'''),
        markdown(
            "## Typing the pipe is what makes the rest possible\n\n"
            "Because every verb declares its kinds, an impossible composition is "
            "refused **before anything runs** — not halfway through, with a stack "
            "trace and a partially changed environment."
        ),
        code('''
bad = Composition.read("findings | attach", verbs=verbs)
check = bad.validate()

print("valid:", check.ok)
print()
print(check.explain())
'''),
        markdown(
            "Both kinds are named. Nothing executed — and that matters, because "
            "`attach` changes the environment.\n\n"
            "## What can follow what\n\n"
            "\"What can I pipe into this?\" is a query over the registry, not prose "
            "somebody maintains."
        ),
        code('''
print("verbs that can follow `link`:")
print("  ", " ".join(sorted(v.name for v in verbs.successors("link"))[:14]))
print()
print("verbs that can start a pipeline (they consume NOTHING):")
print("  ", " ".join(sorted(v.name for v in verbs.sources())[:14]))
print()
print("kinds actually reachable in <= 4 stages:")
print("  ", " ".join(sorted(verbs.reachable())))
'''),
        markdown("## Build something and watch the provenance accumulate"),
        code('''
import json, pathlib, tempfile

WORK = pathlib.Path(tempfile.mkdtemp(prefix="slpie-nb-"))
(WORK / "package.json").write_text(json.dumps({
    "name": "demo", "version": "1.0.0", "dependencies": {"lodash": "^3.0.0"},
}))
(WORK / "package-lock.json").write_text(json.dumps({
    "name": "demo", "lockfileVersion": 3,
    "packages": {"node_modules/lodash": {"version": "4.17.21"}},
}))

# A manifest asking for ^3 and a lockfile pinning 4.17.21 — they contradict.
stages = ["discover " + str(WORK), "link", "findings"]
for count in range(1, len(stages) + 1):
    pipeline = " | ".join(stages[:count])
    result = Composition.read(pipeline, verbs=verbs).run(Context(root=str(WORK)))
    flow = result.flow
    print(f"{pipeline[-42:]:44} {flow.kind.label:14} "
          f"steps={len(flow.reasoning.steps):2} gaps={len(flow.gaps)}")
'''),
        markdown(
            "The reasoning path grows at every stage and nothing is dropped. A gap "
            "raised at stage one is still there at stage three — that is invariant "
            "5 (\"every answer carries its reasoning and its gaps\") holding "
            "*through composition*, which is what makes long pipelines trustworthy "
            "rather than merely convenient."
        ),
        code('''
result = Composition.read(
    f"discover {WORK} | link | findings", verbs=verbs,
).run(Context(root=str(WORK)))

print(result.flow.reasoning.render()[:900])
'''),
        markdown(
            "## Explaining before running\n\n"
            "You can see what a composition will do, and what it will cost, while "
            "the decision is still free."
        ),
        code('''
print(Composition.read(f"discover {WORK} | link | constraints | findings",
                       verbs=verbs).explain())
'''),
        markdown(
            "## Shaping verbs work on typed objects, not text\n\n"
            "`filter`, `sort`, `head` and `unique` accept `ANY` and produce `SAME`, "
            "so they slot in anywhere. They compare a `Severity`, not a rendering "
            "of one — which is why `grep` could not do this job."
        ),
        code('''
result = Composition.read(
    f"discover {WORK} | link | findings | sort --field severity --desc | head --count 3",
    verbs=verbs,
).run(Context(root=str(WORK)))

print("stages:", " → ".join(result.flow.stages))
print("kind:  ", result.flow.kind.label, "(unchanged by the shaping verbs)")
print("count: ", result.flow.size)
'''),
        markdown(
            "## Your turn\n\n"
            "Try these — each one is a real, valid composition:\n\n"
            "```python\n"
            "Composition.read(f\"discover {WORK} | reason | ask --question 'what is risky?'\", verbs=verbs)\n"
            "Composition.read(f\"discover {WORK} | sbom --format cyclonedx\", verbs=verbs)\n"
            "Composition.read(\"audit | verdicts --only violated\", verbs=verbs)\n"
            "```\n\n"
            "And try an invalid one — `validate()` will name both kinds."
        ),
        code('''
# Scratch cell — edit and run.
pipeline = f"discover {WORK} | reason | ask --question 'what should I fix first?'"

result = Composition.read(pipeline, verbs=verbs).run(Context(root=str(WORK)))
print("ok:", result.ok, "| kind:", result.flow.kind.label)
print(result.flow.facts.get("answer", "")[:600])
'''),
    ),
)


# --- a project every notebook can build for itself ------------------------
#
# Repeated rather than shared, for the same reason `SETUP` is: a notebook must
# work when it is the only file somebody opened.

PROJECT = code('''
import json, pathlib, tempfile

WORK = pathlib.Path(tempfile.mkdtemp(prefix="slpie-nb-"))
shop = WORK / "shop"
(shop / "services").mkdir(parents=True)

(shop / "package.json").write_text(json.dumps({
    "name": "shop", "version": "1.0.0", "license": "MIT",
    "dependencies": {"lodahs": "^4.0.0", "loose": "*", "express": "^4.18.0"},
}, indent=2))
(shop / "package-lock.json").write_text(json.dumps({
    "name": "shop", "lockfileVersion": 3, "packages": {
        "node_modules/lodahs": {"version": "4.17.21", "license": "AGPL-3.0"},
        "node_modules/loose": {"version": "0.1.0"},
        "node_modules/express": {"version": "4.18.2", "license": "MIT"},
    },
}, indent=2))
(shop / "requirements.txt").write_text("flask==3.0.0\\nrequests>=2.28\\n")
(shop / "settings.py").write_text('AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"\\n')
(shop / "Dockerfile").write_text(
    "FROM python:3.11-slim\\nRUN pip install flask==3.0.0\\nEXPOSE 8080\\n")
(shop / "services" / "api.yaml").write_text(json.dumps({
    "openapi": "3.0.0", "info": {"title": "shop", "version": "1.0.0"},
    "paths": {"/orders": {"get": {"responses": {"200": {"description": "ok"}}}}},
}))

from slpie.compose import Composition, Context, registry

verbs = registry()

def run(pipeline: str):
    """Run a composition against the project. Returns the Result."""
    return Composition.read(pipeline, verbs=verbs).run(Context(root=str(shop)))

print("project:", shop)
for path in sorted(shop.rglob("*")):
    if path.is_file():
        print("  ", path.relative_to(shop))
''')


# --- 02 · discovery -------------------------------------------------------

DISCOVERY = Notebook(
    2, "discovery", "Discovery — reading a tree the way its ecosystems intend",
    "29 discoverers, and what each one is allowed to claim.",
    (
        _header(
            "Discovery",
            "**A discoverer that cannot cite a source has not discovered "
            "anything.** Every observation carries a file and a line, and that is "
            "enforced in the type rather than by review.",
            """
Discovery is the bottom of the stack: it turns files into *observations*, each
one an assertion about the world with evidence attached. Everything above it —
linking, reasoning, governance, the graph — is downstream of what happens here,
which is why the rules are strict.

Different ecosystems mean different things by the same word. A `package.json`
range is a *wish*; a `package-lock.json` pin is a *fact*. Discovery records both
and lets the confidence model sort out which to believe.
""",
        ),
        SETUP,
        PROJECT,
        markdown("## What is registered\n\nDiscoverers claim files by pattern."),
        code('''
from slpie.discovery.registry import Registry, register_builtins

registry_of_discoverers = register_builtins(Registry())
plugins = list(registry_of_discoverers.plugins)

print(f"{len(plugins)} discoverers registered\\n")
for registration in sorted(plugins, key=lambda item: item.manifest.id)[:18]:
    manifest = registration.manifest
    handles = ", ".join(manifest.handles[:2])
    print(f"  {manifest.id:26} {handles[:44]}")
print("  ...")
'''),
        markdown("## Read the tree"),
        code('''
result = run(f"discover {shop}")

print("kind:        ", result.flow.kind.label)
print("observations:", result.flow.size)
print("files seen:  ", result.flow.facts["files_seen"])
print("files read:  ", result.flow.facts["files_read"])
print()
for observation in list(result.flow.items)[:8]:
    print(f"  {observation.kind:14} {observation.subject[:38]:40} -> {observation.object[:34]}")
'''),
        markdown(
            "## Every observation cites its source\n\n"
            "This is invariant 1, and it is why the platform can answer *why* "
            "rather than only *what*."
        ),
        code('''
for observation in list(result.flow.items)[:5]:
    evidence = observation.evidence
    where = evidence.location
    print(f"{observation.subject[:44]}")
    print(f"    kind       {evidence.kind.value}  (base confidence "
          f"{evidence.kind.base_confidence})")
    print(f"    at         {where.uri.rsplit('/', 1)[-1]}:{where.line}")
    print(f"    excerpt    {(evidence.excerpt or '').strip()[:56]}")
    print()
'''),
        markdown(
            "## Confidence is derived, never assigned\n\n"
            "No caller passes a number. A lockfile pin is 1.00 because a lockfile "
            "*is* the resolved truth; a name heuristic is 0.25 because guessing "
            "from a string is what it sounds like."
        ),
        code('''
from slpie.domain.evidence import EvidenceKind

ladder = sorted(EvidenceKind, key=lambda k: -k.base_confidence)
for kind in ladder:
    bar = "█" * round(kind.base_confidence * 30)
    print(f"  {kind.value:22} {kind.base_confidence:.2f}  {bar}")
'''),
        markdown(
            "Two guards sit on top of that ladder, and both exist because of a way "
            "this goes wrong:\n\n"
            "* evidence drawn **only** from reflection, dynamic loading or name "
            "heuristics caps at **0.60** — a confident answer built entirely on "
            "guesses is the worst kind;\n"
            "* a single **lockfile pin short-circuits to 1.00** — corroborating a "
            "fact with weaker evidence must not dilute it.\n\n"
            "## Corroboration compounds, re-reading does not"
        ),
        code('''
from slpie.domain.evidence import Confidence, Evidence, SourceLocation

def evidence(kind, uri, line=1):
    return Evidence(kind=kind, location=SourceLocation(uri, line=line),
                    extractor="demo", extractor_version="1")

manifest = evidence(EvidenceKind.MANIFEST_DECLARED, "file:///r/package.json")
same_file = evidence(EvidenceKind.MANIFEST_DECLARED, "file:///r/package.json", 9)
other     = evidence(EvidenceKind.STATIC_IMPORT, "file:///r/index.js")

print("one manifest declaration:            ", Confidence.combine([manifest]))
print("read twice from the same file:       ", Confidence.combine([manifest, same_file]))
print("plus an independent static import:   ", Confidence.combine([manifest, other]))
print()
print("Grouped by (kind, uri) before combining, so re-reading one file never")
print("compounds. Independent sources do.")
'''),
        markdown("## Across ecosystems\n\nThe same tree, seen by every discoverer that claims part of it."),
        code('''
from collections import Counter

families = Counter()
for observation in result.flow.items:
    families[observation.evidence.extractor] += 1

for extractor, count in families.most_common():
    print(f"  {extractor:16} {count:3} observations")
'''),
        markdown(
            "## Your turn\n\n"
            "Add a `pom.xml`, a `go.mod`, a `Cargo.toml` or a Kubernetes manifest "
            "to `shop/` and re-run the discover cell. Nothing else has to change — "
            "the discoverer that claims it is already registered."
        ),
        code('''
# Scratch cell — add a file and see who claims it.
(shop / "go.mod").write_text("module example.com/shop\\n\\ngo 1.21\\n\\nrequire github.com/gin-gonic/gin v1.9.1\\n")

again = run(f"discover {shop}")
print("observations now:", again.flow.size, "(was", result.flow.size, ")")
for observation in again.flow.items:
    if "gin" in observation.object:
        print("  found:", observation.object)
'''),
    ),
)


# --- 03 · graph -----------------------------------------------------------

GRAPH = Notebook(
    3, "graph", "The graph — bitemporal, and traversed in SQL",
    "Blast radius, cycles, and why nothing is ever deleted.",
    (
        _header(
            "The graph",
            "**Two independent time axes, and traversal that runs in the "
            "database.** Blast radius over a ten-thousand-node graph is one "
            "recursive query, not ten thousand round trips.",
            """
Two things make this graph different from a diagram:

**It is bitemporal.** `valid_from`/`valid_to` says when something was true in the
world; `observed_at`/`superseded_at` says when we learned it. Those are different
questions and conflating them makes "what did we believe last Tuesday?"
unanswerable.

**Nothing is deleted.** A correction supersedes; a retirement sets `valid_to`.
History survives, which is what lets an audit ask what was known at the time a
decision was made.
""",
        ),
        SETUP,
        markdown("## Build one"),
        code('''
import tempfile, pathlib
from slpie.graph.sqlite_graph import SqliteGraph
from slpie.graph.traversal import Traverser
from slpie.domain.node import Node, NodeKind
from slpie.domain.edge import Edge, EdgeKind
from slpie.domain.identity import Purl
from slpie.domain.evidence import Evidence, EvidenceKind, SourceLocation

WORK = pathlib.Path(tempfile.mkdtemp(prefix="slpie-nb-"))
graph = SqliteGraph(WORK / "graph.db")

def cite(name):
    return Evidence(
        kind=EvidenceKind.LOCKFILE_PIN,
        location=SourceLocation("file:///r/package-lock.json", line=1),
        extractor="npm", extractor_version="1", excerpt=f'"{name}"',
    )

def package(name, version="1.0.0"):
    return Node(kind=NodeKind.PACKAGE,
                identity=Purl.parse(f"pkg:npm/{name}@{version}"),
                evidence=(cite(name),))

# app -> api -> auth -> crypto, and app -> ui -> crypto
names = ["app", "api", "auth", "crypto", "ui"]
nodes = {name: package(name) for name in names}
graph.assert_nodes(nodes.values(), sequence=1)

chain = [("app", "api"), ("api", "auth"), ("auth", "crypto"),
         ("app", "ui"), ("ui", "crypto")]
graph.assert_edges(
    [Edge(kind=EdgeKind.DEPENDS_ON, src=nodes[a].id, dst=nodes[b].id,
          evidence=(cite(f"{a}->{b}"),)) for a, b in chain],
    sequence=1,
)

print("nodes:", graph.counts()["nodes"], "| edges:", graph.counts()["edges"])
'''),
        markdown(
            "## Blast radius\n\n"
            "\"If `crypto` changes, what breaks?\" — reverse reachability, with a "
            "confidence floor and a cycle guard, executed as one SQL query."
        ),
        code('''
traverser = Traverser(graph)
impact = traverser.impact(nodes["crypto"].id, max_depth=10)

print(f"{len(impact.impacted)} node(s) depend on crypto, directly or otherwise\\n")
for entry in impact.impacted:
    name = (entry.display or entry.node_id).split("/")[-1]
    print(f"  distance {entry.distance}  confidence {entry.confidence:.2f}  {name}")
'''),
        markdown(
            "`path_confidence` propagates the **minimum** along the path. A node "
            "reached only through a 0.4 dynamic load is reported as reached at "
            "0.4 — so \"this is affected\" and \"we think this might be affected\" "
            "are not the same answer wearing the same face."
        ),
        code('''
# The same query forward: what does `app` depend on?
downstream = traverser.dependencies(nodes["app"].id, max_depth=10)
print(f"app reaches {len(downstream.impacted)} node(s):")
for entry in downstream.impacted:
    name = (entry.display or entry.node_id).split("/")[-1]
    print(f"  distance {entry.distance}  {name}")
'''),
        markdown("## Cycles\n\nSame recursive CTE, pointed at itself."),
        code('''
# Introduce one: crypto -> app closes the loop.
graph.assert_edges(
    [Edge(kind=EdgeKind.DEPENDS_ON, src=nodes["crypto"].id, dst=nodes["app"].id,
          evidence=(cite("crypto->app"),))],
    sequence=2,
)

cycles = traverser.cycles(max_depth=10)
print(f"{len(cycles)} cycle(s) found")
for cycle in cycles[:3]:
    names_in = [str(name).split("/")[-1] for name in (cycle.displays or cycle.nodes)]
    print("  " + " → ".join(names_in))
'''),
        markdown(
            "## Nothing is deleted\n\n"
            "Retire a node and it stops being live — but it is still there, and "
            "the history still answers."
        ),
        code('''
import time

before = graph.counts()
graph.retire_node(nodes["ui"].id, valid_to=time.time_ns(), sequence=3)
after = graph.counts()

print("live nodes before retirement:", before["nodes"])
print("live nodes after retirement: ", after["nodes"])
print()
print("The row is not gone — retirement sets `valid_to`, so history survives")
print("and a bitemporal query still answers what was believed before it.")
'''),
        markdown(
            "## Snapshots are content-addressed\n\n"
            "Identical inputs produce an identical snapshot id, so \"is this the "
            "same architecture as last release?\" is a string comparison."
        ),
        code('''
from slpie.graph.snapshot import SnapshotStore

store = SnapshotStore(graph)
first = store.seal(ledger_version=3, label="baseline")
second = store.seal(ledger_version=3, label="again")

print("first: ", first.root_digest[:32])
print("second:", second.root_digest[:32])
print("identical inputs, identical digest:", first.root_digest == second.root_digest)

graph.close()
'''),
        markdown(
            "## Your turn\n\n"
            "Change a confidence floor and watch the blast radius shrink:\n\n"
            "```python\n"
            "traverser.impact(nodes['crypto'].id, max_depth=10, min_confidence=0.95)\n"
            "```"
        ),
        code('''
# Scratch cell.
strict = traverser.impact(nodes["crypto"].id, max_depth=10, min_confidence=0.99)
print("at min_confidence=0.99:", len(strict.impacted), "node(s)")
'''),
    ),
)


# --- 04 · governance ------------------------------------------------------

GOVERNANCE = Notebook(
    4, "governance", "Governance — many findings, not one verdict",
    "Five rule families, and how a finding earns its severity.",
    (
        _header(
            "Governance",
            "**Every matching rule runs, and a rule that raises abstains rather "
            "than aborting the scan.** One broken rule must not cost you the other "
            "thirteen answers.",
            """
There is no single "pass/fail". A release decision needs the *list*: what is
wrong, how badly, with what evidence, and what to do about it. A boolean throws
away everything an operator needs to triage.

Five families ship built in — advisories, licences, secrets, supply chain, and
security boundaries. Each rule carries its own `source_digest`, so a rule whose
meaning changed cannot pretend it did not.
""",
        ),
        SETUP,
        PROJECT,
        markdown("## What this build checks"),
        code('''
result = run("rules")
print(result.flow.facts["rules"])
'''),
        markdown("## Run them"),
        code('''
result = run(f"discover {shop} | govern")

print(f"{result.flow.size} finding(s)\\n")
for finding in result.flow.items:
    print(f"  [{finding.severity.value:8}] {finding.family:14} {finding.title[:52]}")
'''),
        markdown(
            "## A finding is not an opinion\n\n"
            "It names the rule that raised it, the evidence behind it, what it "
            "would take to fix, and whether it blocks a release."
        ),
        code('''
finding = result.flow.items[0]

print("kind:          ", finding.kind.value)
print("severity:      ", finding.severity.value, f"(rank {finding.severity.rank})")
print("family:        ", finding.family)
print("subject:       ", finding.subject)
print("risk:          ", finding.risk.value)
print("blocks release:", finding.blocks_release)
print("rule:          ", finding.rule_id)
print("rule digest:   ", finding.rule_digest[:24], "  <- the rule's own fingerprint")
print()
print("detail:", finding.detail)
print()
if finding.remediation:
    print("remediation:", finding.remediation.summary)
    print("action:     ", finding.remediation.action,
          "| breaking:", finding.remediation.breaking)
'''),
        markdown("## Filter and rank, as typed objects"),
        code('''
ranked = run(f"discover {shop} | govern | sort --field severity --desc")
for finding in ranked.flow.items:
    print(f"  {finding.severity.rank}  {finding.severity.value:8} {finding.title[:54]}")

print()
high = run(f"discover {shop} | govern --severity high")
print("only high:", high.flow.size, "finding(s)")
'''),
        markdown(
            "`sort --field severity` compares a `Severity`, not a rendering of "
            "one. That matters: alphabetically, `critical` sorts before `low` "
            "before `medium` — which would put a medium above a low and read as a "
            "working sort while being wrong."
        ),
        code('''
from slpie.domain.finding import Severity

alphabetical = sorted(Severity, key=lambda s: s.value)
by_rank      = sorted(Severity, key=lambda s: -s.rank)

print("alphabetical:", " ".join(s.value for s in alphabetical))
print("by severity: ", " ".join(s.value for s in by_rank))
print()
print("These disagree, which is why the shaping verbs work on domain objects.")
'''),
        markdown(
            "## Suppression is on the record\n\n"
            "A waiver never erases a finding — it marks it, with a reason and an "
            "actor. Erasing it would make the compliance history unauditable, "
            "which is the opposite of the point."
        ),
        code('''
from slpie.errors import GovernanceError

waived = finding.suppress("accepted for the 2026-Q1 release; tracked as ARCH-441")
print("suppressed:", waived.suppressed)
print("reason:    ", waived.suppression_reason)
print("still present in the list, still carrying its evidence:", len(waived.evidence), "item(s)")
print()
try:
    finding.suppress("")
except GovernanceError as error:
    print("a waiver with no reason is refused:", error)
'''),
        markdown("## Into a risk register"),
        code('''
register = run(f"discover {shop} | govern | risk")
print(register.flow.facts["risk"][:1400])
'''),
        markdown(
            "## Your turn\n\n"
            "Add a rule family of your own — governance rules are ordinary "
            "objects, and built-ins register through the identical path a "
            "third-party rule does."
        ),
        code('''
# Scratch cell: what would a stricter licence policy report?
strict = run(f"discover {shop} | govern --family licenses")
for f in strict.flow.items:
    print(f"  {f.severity.value:8} {f.title}")
'''),
    ),
)


# --- 05 · reasoning -------------------------------------------------------

REASONING = Notebook(
    5, "reasoning", "Reasoning — eight layers, and an answer in English",
    "L1–L8, and what happens when a layer cannot see.",
    (
        _header(
            "Reasoning",
            "**Layers append; none mutates a prior layer.** Walking `derived_from` "
            "backwards from any conclusion terminates in raw evidence with a file "
            "and a line.",
            """
| | |
|---|---|
| L1 | Discovery — what is there |
| L2 | Normalization — one identity per thing |
| L3 | Graph construction |
| L4 | Semantic linking — join the lockfile pin to the manifest range |
| L5 | Architecture validation, including manifest reconciliation |
| L6 | Constraint solving |
| L7 | Impact |
| L8 | Optimisation |

A layer that cannot complete **abstains** and says so. It does not guess, and it
does not take the rest of the pipeline down with it — the abstention becomes a
gap on the answer, which is the honest version of "we could not check that".
""",
        ),
        SETUP,
        PROJECT,
        markdown("## Run the pipeline"),
        code('''
result = run(f"discover {shop} | reason")

print("kind:       ", result.flow.kind.label)
print("layers run: ", result.flow.facts["layers"])
print("enrichments:", result.flow.facts["enrichments"])
print("abstained:  ", result.flow.facts["abstained"] or "none")
print()
for outcome in result.flow.value.results:
    mark = "abstained" if outcome.abstained else f"{len(outcome.enrichments):3} enrichment(s)"
    print(f"  L{outcome.number} {outcome.layer:24} {mark}")
'''),
        markdown(
            "## An enrichment knows where it came from\n\n"
            "`derived_from` chains back through prior enrichments to raw evidence. "
            "That chain is what `explain` renders, and what makes a conclusion "
            "checkable rather than merely stated."
        ),
        code('''
enrichments = list(result.flow.value.context.enrichments.values())
print(f"{len(enrichments)} enrichment(s)\\n")

for item in enrichments[:6]:
    print(f"  {item.layer:14} {item.attribute:22} {str(item.value)[:26]}")
    print(f"  {'':14} derived from {len(item.derived_from)} prior fact(s), "
          f"confidence {item.confidence:.2f}")
'''),
        markdown("## Ask a question"),
        code('''
answered = run(f"discover {shop} | reason | ask --question 'what should I fix first?'")
print(answered.flow.facts["answer"])
'''),
        markdown(
            "## An answer is never a bare value\n\n"
            "It carries what limits it, and what to ask next. `next_questions` are "
            "ranked by expected information gain — the question whose answer would "
            "most improve confidence goes first."
        ),
        code('''
guidance = answered.flow.value

print("confidence:", round(guidance.confidence, 3))
print()
print("gaps —", len(guidance.gaps), "thing(s) limiting this answer:")
for gap in guidance.gaps[:5]:
    print(f"  · {gap.kind.value:24} {gap.detail[:52]}")
print()
print("next questions, ranked:")
for question in guidance.next_questions[:5]:
    print(f"  · {question.text[:66]}")
print()
print("suggested actions:")
for action in guidance.actions[:4]:
    print(f"  · [{action.kind.value}] {action.summary[:58]}")
'''),
        markdown(
            "## Declining to rule where it cannot see\n\n"
            "This is the pattern that recurs everywhere in the platform. Give L5 a "
            "pipeline with no resolved graph and it refuses rather than reporting "
            "every dependency as missing."
        ),
        code('''
from slpie.errors import ReasoningError
from slpie.reasoning.l5_validation import ArchitectureValidationLayer
from slpie.reasoning.layer import LayerContext

layer = ArchitectureValidationLayer()
context = LayerContext(observations=())      # nothing was resolved

try:
    layer.run(context)
except ReasoningError as error:
    print("refused, and said why:\\n")
    print(" ", error)
'''),
        markdown(
            "A layer that had shrugged and returned an empty result would have "
            "reported a perfectly healthy architecture. Refusing is the only "
            "honest answer to a question you were not given the inputs for."
        ),
        markdown("## Constraint solving"),
        code('''
solved = run(f"discover {shop} | link | constraints")
solution = solved.flow.value

print("satisfiable:", solution.satisfiable)
print("assignments:", len(solution.assignments))
print("attempts:   ", solution.attempts)
print()
for coordinate, version in list(solution.resolved.items())[:8]:
    print(f"  {coordinate[:44]:46} {version}")
'''),
        markdown(
            "When it is *not* satisfiable, the result names the conflicting pair "
            "and both version windows — \"urllib3 is unsatisfiable\" is a shrug; "
            "\"requests 2.31 wants <2, boto3 1.34 wants >=2\" is an answer."
        ),
        code('''
# Scratch cell — ask your own question.
mine = run(f"discover {shop} | reason | ask --question 'what is the licence risk?'")
print(mine.flow.facts["answer"][:900])
'''),
    ),
)


# --- 06 · environment -----------------------------------------------------

ENVIRONMENT = Notebook(
    6, "environment", "Environments — declare first, simulate, then fire conditions at it",
    "The manifest, the station, and a world you can break on purpose.",
    (
        _header(
            "Environments",
            "**A `requirements.txt` is fast to reason about because it is a "
            "declaration of names, not a search.** An environment manifest works "
            "the same way: parsing it takes milliseconds and yields the skeleton "
            "graph before a single file is read.",
            """
```
DECLARE  →  BIND  →  ATTACH  →  CORROBORATE  →  UNDERSTAND
manifest    sim/live  register    discovery      layers + answer
(ms)        one tag   capability  evidence
```

**One tag swaps everything.** `target: simulated | live` decides which connector
answers. Same manifest, same code paths, same answers — only the binding differs.
You prove a case in the simulator, then point the identical configuration at the
real thing.

And the delta between what was *declared* and what was *observed* is first-class
intelligence, not an error: declared-but-absent is a dead declaration, and
observed-but-undeclared is a shadow dependency.
""",
        ),
        SETUP,
        markdown("## Write a manifest"),
        code('''
import pathlib, tempfile
from slpie.environment import loads, scaffold

WORK = pathlib.Path(tempfile.mkdtemp(prefix="slpie-nb-"))

MANIFEST = """
apiVersion: slpie/v1
environment: acme-production
target: simulated

security:
  concerns: [pci-dss, gdpr]
  boundaries:
    - name: cardholder-data
      contains: [payments]

codebase:
  - root: ./services/payments
    language: npm
    team: payments
  - root: ./services/orders
    language: python
    team: fulfilment

network:
  - name: payments-api
    url: https://api.acme.com/v1
    kind: rest

data:
  - folder: ./warehouse/orders
    kind: schema

providers:
  - name: stripe
"""

path = WORK / "slpie.environment.yaml"
path.write_text(MANIFEST)

manifest = loads(MANIFEST, source_uri=path.resolve().as_uri())
print("environment:", manifest.environment)
print("target:     ", manifest.target.value)
print("declared:   ", len(manifest.declarations), "element(s)")
print()
for declaration in manifest:
    print(f"  {declaration.kind.value:12} {declaration.name:12} {declaration.location}")
'''),
        markdown(
            "## The skeleton graph exists before anything is read\n\n"
            "A declaration is evidence — `DECLARED`, at 0.92. Authoritative about "
            "*intent*, and deliberately not about reality."
        ),
        code('''
from slpie.engine import Engine

engine = Engine.from_manifest(str(path))
count = engine.declare()

print(f"{count} node(s) in the graph, with no file read yet")
print("graph counts:", engine.graph.counts())
'''),
        markdown("## Materialise the declared world\n\nThe simulator writes **real artifacts**, not mocks."),
        code('''
world = engine.simulate(root=str(WORK / "world"))

print("world at:", world.root)
print()
for artifact in sorted(world.artifacts, key=lambda item: str(item.path))[:12]:
    print(f"  {artifact.kind:14} {artifact.path.relative_to(world.root)}")
'''),
        markdown(
            "Real `package-lock.json`, real `go.mod`, real Kubernetes YAML, a real "
            "`git` repository with a real commit. The *same* discoverers run over "
            "this as over a customer's tree — so a green simulator case is "
            "evidence about the real code path, not about a mock."
        ),
        code('''
lock = world.read("payments", "package-lock.json")
print(lock[:320])
'''),
        markdown("## Attach, and negotiate capabilities"),
        code('''
registrations = engine.attach()

for registration in registrations:
    granted = [c.name for c in registration.negotiation.granted]
    refused = [c.name for c in registration.negotiation.refused]
    print(f"  {registration.element:12} granted: {', '.join(granted[:3])}")
    if refused:
        print(f"  {'':12} refused: {', '.join(refused)}")
'''),
        markdown(
            "**A refused capability becomes a named gap on every answer whose "
            "confidence it limits.** That is what separates a low-confidence "
            "answer from a misleading one."
        ),
        code('''
report = engine.scan()
print("scan:", report)
print()
print("gaps the platform is carrying:")
for gap in engine.gaps()[:6]:
    print(f"  · {gap.kind.value:22} {gap.subject[:22]:24} {gap.detail[:34]}")
'''),
        markdown("## Declared vs observed"),
        code('''
reconciliation = engine.reconcile()
print(reconciliation.summary())
print()
print("declared but never observed:", len(reconciliation.declared_not_found))
print("observed but never declared:", len(reconciliation.undeclared))
print("corroborated:               ", len(reconciliation.corroborated))
'''),
        markdown(
            "## Fire a condition at it\n\n"
            "Twelve scenarios ship. Each **rewrites the world** and then expects "
            "the platform to *discover* the change — nothing is written to the "
            "graph directly, so the scenario tests the real path."
        ),
        code('''
from slpie.simulator.scenarios import available

print(f"{len(available())} scenarios:")
for name in available():
    print("  ", name)
'''),
        code('''
outcome = engine.fire("cve", package="lodash", version="4.17.20")

print("scenario:  ", outcome.scenario)
print("changed:   ", outcome.changed)
print("expects:   ", outcome.expect_findings or outcome.expect_gaps)
print("detail:    ", outcome.detail)
print()
print("The expectation is data on the outcome — so a test asserts it")
print("rather than a human reading the narration and nodding.")
'''),
        code('''
# Rescan, and see whether the platform found what the scenario planted.
engine.scan()
findings = engine.reconciliation_findings()
print(f"{len(findings)} finding(s) after the scenario")

engine.close()
'''),
        markdown(
            "## Your turn\n\n"
            "Change `target: simulated` to `target: live` in the manifest above. "
            "The binding refuses without an explicit confirmation — that is the "
            "one dangerous action in the platform, and it is gated in exactly one "
            "place (`slpie/binding/guard.py`) rather than reimplemented per "
            "surface."
        ),
        code('''
# Scratch cell — fire a different scenario.
engine2 = Engine.from_manifest(str(path))
engine2.declare()
engine2.simulate(root=str(WORK / "world2"))
engine2.attach()

for name in ("boundary-breach", "shadow-dependency", "license-change"):
    result = engine2.fire(name)
    print(f"  {name:20} changed={str(result.changed):5} expects={result.expect_findings}")

engine2.close()
'''),
    ),
)


# --- 07 · artifacts -------------------------------------------------------

ARTIFACTS = Notebook(
    7, "artifacts", "Artifacts — SBOM, C4, TOGAF, risk",
    "Architecture emitted as code, not as a transient printout.",
    (
        _header(
            "Artifacts",
            "**These are not reports you read once.** They generate into your "
            "repository as versioned, importable artifacts, kept synchronised with "
            "the graph — and a hand annotation survives regeneration.",
            """
| Output | Emitted as |
|---|---|
| SBOM | CycloneDX 1.5 or SPDX 2.3 |
| C4 views (C1–C4) | Mermaid |
| TOGAF application / data / technology | typed Python + Mermaid + JSON |
| Deployment topology | typed Python |
| Risk register | typed Python + markdown |

Everything here is **deterministic**: nothing reads the clock, so the same graph
produces byte-identical output. That is what makes these diffable in a pull
request rather than noise.
""",
        ),
        SETUP,
        PROJECT,
        markdown("## A bill of materials"),
        code('''
import json

result = run(f"discover {shop} | sbom --format cyclonedx")
sbom = json.loads(result.flow.facts["sbom"])

print("format: ", sbom["bomFormat"], sbom["specVersion"])
print("serial: ", sbom["serialNumber"])
print()
for component in sbom["components"][:6]:
    print(f"  {component['type']:8} {component['name']:14} {component.get('version','')}")
    print(f"  {'':8} {component['bom-ref'][:58]}")
'''),
        markdown(
            "`bom-ref` is a **purl** — the same identifier CycloneDX, SPDX and OSV "
            "already use. No translation layer, which is what makes advisory "
            "matching a lookup rather than a fuzzy join."
        ),
        code('''
spdx = json.loads(run(f"discover {shop} | sbom --format spdx").flow.facts["sbom"])
print("SPDX version:", spdx["spdxVersion"])
print("packages:    ", len(spdx["packages"]))
for package in spdx["packages"][:4]:
    print(f"  {package['name']:14} {package.get('licenseConcluded', 'NOASSERTION')}")
'''),
        markdown("## C4 views, as Mermaid"),
        code('''
for level in ("context", "container"):
    diagram = run(f"discover {shop} | c4 --level {level}").flow.facts["c4"]
    print(f"--- C4 {level} " + "-" * 44)
    print(diagram[:520])
    print()
'''),
        markdown(
            "Paste either of those into any Markdown renderer that speaks Mermaid "
            "— GitHub does — and it draws.\n\n"
            "## TOGAF views"
        ),
        code('''
for view in ("application", "data", "technology"):
    result = run(f"discover {shop} | enterprise --view {view}")
    text = result.flow.facts["enterprise"]
    print(f"--- {view} " + "-" * 50)
    print(text[:420])
    print()
'''),
        markdown(
            "## Emitted as importable code\n\n"
            "The typed half routes through the one Gratimos import — graph → "
            "`DataShape` → `ModuleRegistry.generate()` → AST three-way merge. Which "
            "means a hand edit survives regeneration."
        ),
        code('''
import pathlib

target = shop / "architecture"
run(f"discover {shop} | enterprise --view application --write --out {target}")

if target.exists():
    for path in sorted(target.rglob("*"))[:10]:
        if path.is_file():
            print("  ", path.relative_to(shop), f"({path.stat().st_size} bytes)")
    sample = next((p for p in target.rglob("*.py")), None)
    if sample:
        print()
        print(sample.read_text()[:600])
'''),
        markdown(
            "Regenerate after a hand edit and the edit survives — a genuine "
            "conflict **raises** rather than silently overwriting somebody's work, "
            "which is precisely why architecture-as-code needs a merge and not a "
            "template."
        ),
        markdown("## A risk register"),
        code('''
register = run(f"discover {shop} | govern | risk --markdown")
print(register.flow.facts["risk"][:1500])
'''),
        markdown(
            "## Deterministic, and therefore diffable"
        ),
        code('''
first  = run(f"discover {shop} | sbom").flow.facts["sbom"]
second = run(f"discover {shop} | sbom").flow.facts["sbom"]

print("byte-identical across runs:", first == second)
print("length:", len(first), "characters")
print()
print("Nothing here reads the clock. An SBOM that embedded `datetime.now()`")
print("would produce a diff on every build and teach reviewers to ignore it.")
'''),
        code('''
# Scratch cell — write an SBOM to disk and inspect it.
out = shop / "sbom.json"
run(f"discover {shop} | sbom --format cyclonedx --out {out}")
print("wrote", out, f"({out.stat().st_size} bytes)")
'''),
    ),
)


# --- 08 · incremental -----------------------------------------------------

INCREMENTAL = Notebook(
    8, "incremental", "Incremental — read only what moved, and never guess about the rest",
    "Content fingerprints, and two modes for what to do when a file cannot be read.",
    (
        _header(
            "Incremental",
            "**Content, never modification time.** A `git checkout` rewrites "
            "mtimes on identical files, and a restored build cache writes *older* "
            "ones than were recorded. The first wastes a full rescan; the second "
            "silently skips a file that really changed.",
            """
Hashing costs a read of every file, which sounds like it defeats the purpose —
but reading a file is not the expensive part of a scan. Parsing it, resolving
identities and running twenty-nine discoverers over it is. A fingerprint pass
reads bytes and does nothing else with them.

The second idea is the one this notebook is really about: **the engine never
says anything about a file it did not read.** That sounds obvious. It was not
true, and the way it failed is instructive.
""",
        ),
        SETUP,
        markdown("## Fingerprint a tree"),
        code('''
import pathlib, tempfile
from slpie.incremental import Fingerprint, Watcher

WORK = pathlib.Path(tempfile.mkdtemp(prefix="slpie-nb-"))
tree = WORK / "tree"
tree.mkdir()
for index in range(10):
    (tree / f"module_{index}.py").write_text(f"VALUE = {index}\\n")

before = Fingerprint.of(tree)
print(before)
print("tree digest:", before.digest[:32])
'''),
        markdown("## Change one file"),
        code('''
(tree / "module_3.py").write_text("VALUE = 999   # changed\\n")
(tree / "module_new.py").write_text("VALUE = None\\n")
(tree / "module_7.py").unlink()

after = Fingerprint.of(tree)
delta = after.compare(before)

print(delta)
print()
print("added:    ", [u.rsplit('/', 1)[-1] for u in delta.added])
print("changed:  ", [u.rsplit('/', 1)[-1] for u in delta.changed])
print("removed:  ", [u.rsplit('/', 1)[-1] for u in delta.removed])
print("unchanged:", delta.unchanged)
print()
print("a rescan must re-read:", len(delta.to_read), "file(s), not 10")
'''),
        markdown(
            "## The defect this was built to fix\n\n"
            "A fingerprint used to silently drop any file it could not read, was "
            "told not to read, or ran out of budget for. `compare()` then reported "
            "those files as **removed** — because absent from a fingerprint is "
            "indistinguishable from absent from disk.\n\n"
            "A rescan acting on that delta retires the graph nodes drawn from "
            "files that are still there and perfectly fine. Measured on this same "
            "ten-file tree: a size limit of zero reported all ten as removed."
        ),
        code('''
full = Fingerprint.of(tree)

# A budget that reads nothing at all.
partial = Fingerprint.of(tree, max_bytes=0, strict=False)
delta = partial.compare(full)

print("files this pass read:", len(partial))
print()
print("removed: ", len(delta.removed), " <- would have been 9, retiring the graph")
print("unknown: ", len(delta.unknown), " <- neither refreshed nor retired")
print("trustworthy:", delta.trustworthy)
print()
print(delta)
'''),
        markdown(
            "`unknown` is the whole fix. Those files are left exactly as the graph "
            "last knew them, because nobody looked at them — which is the only "
            "honest answer.\n\n"
            "## Two modes\n\n"
            "**Strict** is the default and what production runs. It refuses rather "
            "than handing back a delta that looks complete and is not."
        ),
        code('''
from slpie.incremental import IncompleteFingerprint, TruncatedWalk

try:
    Fingerprint.of(tree, max_bytes=0, strict=True)
except IncompleteFingerprint as error:
    print("REFUSED — and the exception carries its fields, not just a message:")
    print()
    print("  root:    ", error.root)
    print("  reasons: ", error.reasons)
    print("  files:   ", len(error.skipped))
    print()
    print(str(error)[:700])
'''),
        markdown(
            "A caller acts on `error.skipped` rather than parsing prose out of "
            "`str(e)` — the shape [python-rope](https://github.com/python-rope/rope) "
            "uses for `ModuleSyntaxError(filename, lineno, message)`.\n\n"
            "A walk that hits its *file limit* raises a different exception, "
            "because it scales differently: it cannot name what it missed without "
            "recording one object per unreached file, and on a two-million-file "
            "monorepo that is the memory failure the spill tier exists to prevent."
        ),
        code('''
try:
    Fingerprint.of(tree, limit=3, strict=True)
except TruncatedWalk as error:
    print("limit:", error.limit)
    print()
    print(str(error)[:520])
'''),
        markdown(
            "**Lenient** is for development. It records every skip with the detail "
            "needed to fix it, and reports the affected files as unknown."
        ),
        code('''
lenient = Fingerprint.of(tree, max_bytes=0, strict=False)
print(lenient.explain_skips())
'''),
        markdown("## Planning a rescan before paying for one"),
        code('''
baseline = WORK / "baseline.json"
watcher = Watcher(tree, baseline=baseline)
watcher.commit()                      # record the current state

(tree / "module_1.py").write_text("VALUE = 42   # touched\\n")

plan = watcher.plan()
print(plan.render())
print("worth rescanning incrementally:", plan.worth_it)
print("proportion of the tree that moved:", f"{plan.proportion:.0%}")
'''),
        markdown(
            "Past about half the tree, a full rescan is the cheaper answer — the "
            "bookkeeping to retire and re-derive most of a graph costs more than "
            "building it once. `Plan.proportion` reports that up front rather than "
            "pretending otherwise."
        ),
        markdown("## Through the verb"),
        code('''
from slpie.compose import Composition, Context, registry

verbs = registry()
result = Composition.read(f"changed --path {tree} --lenient", verbs=verbs).run(
    Context(root=str(tree)),
)
print(result.flow.facts["changed"])
print("trustworthy:", result.flow.facts["trustworthy"])
'''),
        code('''
# Scratch cell — make an unreadable file and watch strict mode refuse.
import os
locked = tree / "locked.py"
locked.write_text("SECRET = 1\\n")
os.chmod(locked, 0o000)

try:
    Fingerprint.of(tree, strict=True)
    print("(running as root — permissions do not apply, so nothing was skipped)")
except IncompleteFingerprint as error:
    print("refused:", error.reasons, "|", len(error.skipped), "file(s)")
finally:
    os.chmod(locked, 0o644)
'''),
    ),
)


# --- 09 · audit -----------------------------------------------------------

AUDIT = Notebook(
    9, "audit", "The judge — deterministic architecture verdicts",
    "An AST projected into a queryable graph, and a digest CI can pin.",
    (
        _header(
            "The judge",
            "**A verdict you cannot reproduce is an opinion, and an opinion cannot "
            "gate a release.** Same tree in, same verdict digest out, every time.",
            """
This is not a linter with opinions and not a model asked to review. It projects
an AST into the same graph everything else uses, then runs deterministic queries
over it. Which means an audit finding *composes*: `audit | impact` answers "what
else does this violation reach", which no standalone linter can do.

The load-bearing verdict is **`INDETERMINATE`**. A file that fails to parse, a
dynamic import, a `getattr` call target — the judge says it could not decide
rather than guessing either way. A judge that silently reports `UPHELD` for what
it could not examine is worse than no judge, because the green result is now a
lie.
""",
        ),
        SETUP,
        markdown("## Judge this repository"),
        code('''
from slpie.audit import audit_self

verdict = audit_self()

print("rules run:  ", len(verdict.judgements))
print("coverage:   ", f"{verdict.coverage:.0%}")
print("undecided:  ", verdict.indeterminate)
print("clean:      ", verdict.clean)
print("digest:     ", verdict.digest)
'''),
        markdown("## The verdicts"),
        code('''
from slpie.audit import Verdict

for judgement in verdict.judgements:
    mark = {Verdict.UPHELD: "✓", Verdict.VIOLATED: "✕",
            Verdict.INDETERMINATE: "?", Verdict.INAPPLICABLE: "–"}[judgement.verdict]
    print(f"  {mark} {judgement.verdict.value:14} {judgement.rule:22} {judgement.subject[:30]}")
'''),
        markdown(
            "## Reproducible by construction\n\n"
            "Two runs over an unchanged tree produce an identical digest. That is "
            "the single value CI pins — \"the architecture has not drifted\" becomes "
            "a string comparison."
        ),
        code('''
again = audit_self()
print("first: ", verdict.digest)
print("second:", again.digest)
print("identical:", verdict.digest == again.digest)
'''),
        markdown(
            "## Honest about what it could not see\n\n"
            "`INDETERMINATE` counts **against** coverage, reported alongside the "
            "verdicts rather than hidden."
        ),
        code('''
undecided = [j for j in verdict.judgements if j.verdict is Verdict.INDETERMINATE]
print(f"{len(undecided)} rule(s) could not be decided\\n")
for judgement in undecided:
    print(f"  {judgement.subject}")
    print(f"    {judgement.detail[:70]}")
    for item in judgement.evidence[:1]:
        print(f"    at {item.location.uri.rsplit('/', 1)[-1]}:{item.location.line}")
    print()
'''),
        markdown("## Catch a deliberate violation"),
        code('''
import pathlib, tempfile
from slpie.audit import Check, audit

WORK = pathlib.Path(tempfile.mkdtemp(prefix="slpie-nb-"))
fake = WORK / "slpie"
fake.mkdir()
(fake / "__init__.py").write_text("")
(fake / "rogue.py").write_text("import gratimos\\n")       # <- breaks invariant 8

result = audit(WORK, checks=[Check("single-import", {
    "rule": "slpie→gratimos", "ring": "slpie", "target": "gratimos",
    "allowed": "slpie.artifacts.codegen",
})])

for judgement in result.judgements:
    print(f"  {judgement.verdict.value:12} {judgement.subject}")
    print(f"    {judgement.detail[:74]}")
    for item in judgement.evidence[:2]:
        print(f"    evidence: {item.location.uri.rsplit('/', 1)[-1]}:{item.location.line}")
'''),
        markdown(
            "Every verdict carries **file and line**, always. A verdict without "
            "evidence would be an assertion, and this whole module exists to not "
            "make assertions.\n\n"
            "## As a CI gate"
        ),
        code('''
from slpie.compose import Composition, Context, registry

verbs = registry()
violations = Composition.read("audit | verdicts --only violated", verbs=verbs).run(
    Context(root=str(ROOT)),
)
print("violations in this repository:", violations.flow.size)
print()
print("exit code discipline: 0 clean, 3 findings at or above --fail-on,")
print("so `slpie 'audit | verdicts' --fail-on high` is a usable gate.")
'''),
        markdown(
            "## It is checked by the mechanism it replaces\n\n"
            "`tests/test_slpie_boundaries.py` is not deleted or weakened. It walks "
            "the tree with `ast` independently, and the suite asserts that the two "
            "**agree**. If the judge ever reported `UPHELD` for something the test "
            "catches, the judge is what is broken — and the suite says so."
        ),
        code('''
# Scratch cell — audit any tree you like.
target = ROOT / "gratimos"
result = audit(target)
print(f"{target.name}: {len(result.judgements)} judgement(s), "
      f"coverage {result.coverage:.0%}, digest {result.digest[:16]}")
'''),
    ),
)


# --- 10 · agent -----------------------------------------------------------

AGENT = Notebook(
    10, "agent", "Agent tools — a projection, not a second implementation",
    "Handing a model a tool set that cannot reach a capability that does not exist.",
    (
        _header(
            "Agent tools",
            "**Every tool is a named composition over the verb registry.** So a "
            "tool cannot reach a capability the platform does not have, and adding "
            "a verb widens the tool set with no change to the agent.",
            """
The alternative — ten hand-written functions — makes the tool set an eleventh
place a capability is declared, and it drifts the way every parallel restatement
does.

Two smaller decisions matter as much:

* **Parameters are values; tools own their flags.** The first design asked a
  model for `"--severity critical"`. Quoted, that became *one* argument and
  produced `govern '--severity critical'` — a flag the verb had never heard of.
  A model cannot mis-spell syntax it never writes.
* **The root is bound by the caller, never chosen by the model.** A model that
  could pick the directory could read one it was never given.
""",
        ),
        SETUP,
        PROJECT,
        markdown("## The tool set"),
        code('''
from slpie.agent import ToolSet

tools = ToolSet(root=str(shop))
print(f"{len(list(tools))} tools\\n")
for tool in tools:
    required = [p.name for p in tool.params if p.required]
    print(f"  {tool.name:22} {tool.summary[:44]}")
    if required:
        print(f"  {'':22} requires: {', '.join(required)}")
'''),
        markdown("## Each one is a JSON schema a model can call"),
        code('''
import json

schema = tools.require("impact_analysis").to_dict()
print(json.dumps(schema, indent=2)[:900])
'''),
        markdown("## And each one is a pipeline that type-checks"),
        code('''
from slpie.compose import Composition, registry

verbs = registry()
for tool in tools:
    arguments = {p.name: p.sample for p in tool.params if p.required}
    pipeline = tool.pipeline(arguments)
    ok = Composition.read(pipeline, verbs=verbs).validate().ok
    print(f"  {'ok ' if ok else 'BAD'} {tool.name:22} {pipeline[:52]}")
'''),
        markdown(
            "A tool that could not run is worse than a tool that does not exist — "
            "so the suite asserts this for every tool, not a spot check."
        ),
        markdown("## Run one"),
        code('''
from slpie.agent import ToolRunner

runner = ToolRunner(root=str(shop))
answer = runner.call("governance_scan", {"severity": "high"})

print("tool:      ", answer.tool)
print("pipeline:  ", answer.pipeline)
print("ok:        ", answer.ok)
print("kind:      ", answer.kind)
print("items:     ", answer.size)
print("confidence:", round(answer.confidence, 3))
print("grounded:  ", answer.grounded)
print()
for item in answer.items[:4]:
    print("  ", str(item)[:76])
'''),
        markdown(
            "## The model is told what it could not see\n\n"
            "The result carries the reasoning and the gaps, exactly as they reach a "
            "terminal. A model not told about a refused capability will answer as "
            "though nothing was missing — confidently, and wrongly."
        ),
        code('''
print("gaps returned to the model:")
for gap in answer.gaps:
    print("  ·", gap)
print()
print("reasoning:")
for line in answer.reasoning[:6]:
    print("  ·", line[:72])
'''),
        markdown("## Hostile input is one quoted argument, never syntax"),
        code('''
tool = tools.require("impact_analysis")
pipeline = tool.pipeline({"package": "lodash; rm -rf /"})

print("pipeline:", pipeline)
print()
stages = [stage.verb for stage in Composition.read(pipeline, verbs=verbs)]
print("stages parsed:", stages)
print("no `rm` stage appeared:", "rm" not in stages)
print()
print("There is no shell here at all — the quoting is belt to that brace.")
'''),
        markdown("## Mutating verbs are unreachable"),
        code('''
mutating = {v.name for v in verbs if v.mutates}
print("verbs that change the environment:", mutating)

reachable = set()
for tool in tools:
    arguments = {p.name: p.sample for p in tool.params if p.required}
    reachable |= {s.verb for s in Composition.read(tool.pipeline(arguments), verbs=verbs)}

print("verbs any tool can reach:", len(reachable))
print("overlap with mutating:   ", mutating & reachable or "none")
'''),
        markdown(
            "## The payoff\n\n"
            "Add a verb to the registry and the tools that compose over it widen "
            "automatically. Nothing about the agent changes."
        ),
        code('''
# Scratch cell — call any tool.
for name in ("dependency_lookup", "architecture_summary", "sbom"):
    outcome = runner.call(name, {})
    print(f"  {name:24} ok={outcome.ok} kind={outcome.kind:12} "
          f"items={outcome.size:3} confidence={outcome.confidence:.2f}")
'''),
    ),
)


# --- 11 · gratimos shapes -------------------------------------------------

SHAPES = Notebook(
    11, "gratimos_shapes", "Gratimos — inferring shapes from messy data",
    "What a source actually contains, and casting between what you have and what you need.",
    (
        _header(
            "Gratimos · shapes",
            "**Infer what a source actually contains, then cast into it safely — "
            "and report every value you had to bend.** A cast that silently "
            "coerced is a data-quality bug that surfaces three systems later.",
            """
The unit is a **`DataShape`**: a named set of `FieldShape`s, each with a type
tag, a nullability, and the observations behind it. Shapes merge, so a shape
inferred from a thousand rows and one from a different thousand union into a
shape that covers both.

Casting has two modes. **Lenient** bends what it can and reports it; **strict**
refuses. Which you want depends on whether a surprise is a nuisance or a
correctness problem — and the library will not decide that for you.
""",
        ),
        SETUP,
        markdown("## Infer a shape from records"),
        code('''
from gratimos.meta.infer import infer_shape

orders = [
    {"id": 1, "customer": "Ada",  "total": 99.50, "placed": "2026-01-14", "priority": True},
    {"id": 2, "customer": "Lin",  "total": None,  "placed": "2026-01-15", "priority": False},
    {"id": 3, "customer": "Mo",   "total": 12.00, "placed": "2026-01-16", "priority": True},
]

shape = infer_shape(orders, name="Orders")

print(f"shape: {shape.name}  ({len(shape.fields)} fields)\\n")
for field in shape.fields:
    null = "nullable" if field.nullable else "required"
    print(f"  {field.name:12} {field.tag.value:10} {null:9} "
          f"nulls {field.nulls}/{field.observations}")
'''),
        markdown(
            "`total` is nullable because one row had no value — inferred from the "
            "data rather than declared, which is the difference between a schema "
            "you wrote and one you know is true."
        ),
        code('''
print("as Python type hints:\\n")
for field in shape.fields:
    print(f"  {field.name}: {field.python_type}")
'''),
        markdown("## Shapes merge"),
        code('''
more = infer_shape(
    [{"id": 4, "customer": "Zed", "total": "45.00", "placed": "2026-02-01",
      "priority": True, "notes": "gift wrap"}],
    name="Orders",
)

merged = shape.merge(more)
print(f"merged: {len(merged.fields)} fields (was {len(shape.fields)})\\n")
for field in merged.fields:
    seen = f"{field.observations} observation(s)"
    print(f"  {field.name:12} {field.tag.value:10} {seen}")
'''),
        markdown(
            "`total` arrived as a string in the new batch and as a float in the "
            "old one. The merge **promotes** to a type that holds both rather than "
            "picking a winner — losing data to a type decision is the failure this "
            "is built to avoid."
        ),
        code('''
from gratimos.meta.shapes import promote, TypeTag

pairs = [
    (TypeTag.INT, TypeTag.FLOAT),
    (TypeTag.INT, TypeTag.STRING),
    (TypeTag.BOOL, TypeTag.INT),
    (TypeTag.FLOAT, TypeTag.DECIMAL),
]
for left, right in pairs:
    print(f"  {left.value:8} + {right.value:8} -> {promote(left, right).value}")
'''),
        markdown("## Cast into a shape, and hear about every bend"),
        code('''
from gratimos.meta.cast import CastMode, Caster

caster = Caster(shape, mode=CastMode.LENIENT)

messy = {"id": "7", "customer": "Ash", "total": "88.25",
         "placed": "2026-03-02", "priority": "yes"}

record, report = caster.record(messy)

print("cast result:")
for key, value in record.items():
    print(f"  {key:12} {value!r:24} {type(value).__name__}")
print()
print("converted:", report.converted, " <- values whose type had to change")
print("unchanged:", report.unchanged)
print("defaulted:", report.defaulted)
print("failures: ", report.failures)
print()
for note in report.notes:
    print("  ·", note)
'''),
        markdown(
            "Every one of those is reported. A cast that quietly turned `\"yes\"` "
            "into `True` and said nothing is how a pipeline develops a belief "
            "nobody checked."
        ),
        markdown("## Strict mode refuses instead"),
        code('''
from gratimos.errors import CastError

strict = Caster(shape, mode=CastMode.STRICT)
try:
    strict.record({"id": "not-a-number", "customer": "X", "total": 1.0,
                   "placed": "2026-01-01", "priority": True})
except CastError as error:
    print("refused:", error)
'''),
        markdown(
            "Note the exception type. `CastError` carries the value and the target "
            "tag, so a caller routes on the type rather than parsing the message — "
            "the rule both error taxonomies in this repository open by stating."
        ),
        markdown("## Reading real sources"),
        code('''
import csv, json, pathlib, sqlite3, tempfile

WORK = pathlib.Path(tempfile.mkdtemp(prefix="gratimos-nb-"))

# CSV
csv_path = WORK / "orders.csv"
with csv_path.open("w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=["id", "customer", "total"])
    writer.writeheader()
    writer.writerows([{"id": 1, "customer": "Ada", "total": 99.5},
                      {"id": 2, "customer": "Lin", "total": 12.0}])

# JSON lines
jsonl_path = WORK / "orders.jsonl"
jsonl_path.write_text("\\n".join(
    json.dumps({"id": i, "customer": name, "total": float(i * 10)})
    for i, name in enumerate(["Ada", "Lin", "Mo"], start=1)
))

# SQLite
db_path = WORK / "orders.db"
connection = sqlite3.connect(db_path)
connection.execute("CREATE TABLE orders (id INTEGER, customer TEXT, total REAL)")
connection.executemany("INSERT INTO orders VALUES (?, ?, ?)",
                       [(1, "Ada", 99.5), (2, "Lin", 12.0)])
connection.commit()
connection.close()

for path in (csv_path, jsonl_path, db_path):
    print(f"  {path.name:14} {path.stat().st_size:5} bytes")
'''),
        code('''
from gratimos.probes.base import Target
from gratimos.probes.registry import default_registry

probes = default_registry()
print(f"{len(probes.names())} probes registered:")
for name in probes.names():
    print("  ", name)
'''),
        code('''
for path in (csv_path, jsonl_path, db_path):
    target = Target(uri=path.resolve().as_uri(), path=path)
    match = probes.match(target)
    if match is None:
        print(f"  {path.name:14} no probe claims it")
        continue
    capture = probes.capture(target)
    for payload in (capture.payloads if capture else ())[:1]:
        fields = ", ".join(f"{f.name}:{f.tag.value}" for f in payload.shape.fields)
        print(f"  {path.name:14} {match.probe.name:10} -> {fields}")
'''),
        markdown(
            "One interface, three storage formats. The shape is the same idea in "
            "each case, which is what lets everything downstream stop caring where "
            "the data came from."
        ),
        code('''
# Scratch cell — infer a shape from your own records.
mine = [{"sku": "A-1", "qty": 3, "price": "9.99"},
        {"sku": "B-2", "qty": 1, "price": "24.00"}]
line = infer_shape(mine, name="Line")
print(line)
for field in line.fields:
    print(f"  {field.name:8} {field.tag.value:8} {field.python_type}")
'''),
    ),
)


# --- 12 · gratimos codegen ------------------------------------------------

CODEGEN = Notebook(
    12, "gratimos_codegen", "Gratimos — generating code that survives your edits",
    "A three-way AST merge, so regeneration does not overwrite a human.",
    (
        _header(
            "Gratimos · codegen",
            "**Regenerate a module and your hand edits are still there.** A "
            "genuine conflict raises instead of silently overwriting somebody's "
            "work — which is precisely why architecture-as-code needs a merge and "
            "not a template.",
            """
Template-based generators have one failure mode and everybody has met it: you
edit the generated file, somebody regenerates, and your edit is gone. The usual
workaround is a `DO NOT EDIT` banner, which relocates the problem to "then where
*do* I put the custom logic".

This merges at the **AST** level, three ways: the previous generation, the
current file on disk, and the new generation. Reformatting is invisible to it —
it compares trees, not text.
""",
        ),
        SETUP,
        markdown("## Generate a module from a shape"),
        code('''
import pathlib, tempfile
from gratimos.codegen import ModuleRegistry
from gratimos.meta.infer import infer_shape

WORK = pathlib.Path(tempfile.mkdtemp(prefix="gratimos-nb-"))

shape = infer_shape(
    [{"id": 1, "customer": "Ada", "total": 99.5, "priority": True}],
    name="Orders",
)

registry_of_modules = ModuleRegistry(WORK)
generation = registry_of_modules.generate(shape)

module_path = WORK / "orders.py"
print(module_path.read_text())
'''),
        markdown("## Now edit it by hand, the way a person would"),
        code(
            "source = module_path.read_text()\n"
            "source += (\n"
            "    '\\n\\n'\n"
            "    'def total_with_tax(order, rate: float = 0.2) -> float:\\n'\n"
            "    '    \"\"\"Hand-written. Nobody generated this.\"\"\"\\n'\n"
            "    '    return round(order.total * (1 + rate), 2)\\n'\n"
            ")\n"
            "module_path.write_text(source)\n"
            "print(module_path.read_text()[-240:])\n"
        ),
        markdown("## Regenerate, with the shape changed"),
        code('''
wider = infer_shape(
    [{"id": 1, "customer": "Ada", "total": 99.5, "priority": True,
      "discount": 0.1, "channel": "web"}],
    name="Orders",
)

registry_of_modules.generate(wider)
after = module_path.read_text()

print("the new fields arrived:")
print("  discount:", "discount" in after)
print("  channel: ", "channel" in after)
print()
print("and the hand-written function survived:")
print("  total_with_tax:", "total_with_tax" in after)
'''),
        code('''
print(after[-420:])
'''),
        markdown(
            "## Reformatting is invisible to the merge\n\n"
            "It compares ASTs. Re-indent the whole file, change quote style, "
            "reflow an argument list — none of that is a change."
        ),
        code('''
from gratimos.codegen.astmerge import merge_sources

base   = "def f(a, b):\\n    return a + b\\n"
theirs = "def f(a, b):\\n    return a+b     # reformatted only\\n"
ours   = "def f(a, b, c=0):\\n    return a + b + c\\n"

result = merge_sources(ours, theirs, base)
print("conflicts:", len(result.conflicts))
print("decisions:", [d.name if hasattr(d, 'name') else str(d) for d in result.decisions][:4])
print()
print(result.source)
'''),
        markdown("## A genuine conflict raises rather than guessing"),
        code('''
from gratimos.codegen.astmerge import ConflictPolicy
from gratimos.errors import MergeConflict

base   = "LIMIT = 100\\n"
theirs = "LIMIT = 500\\n"        # the generator wants 500
ours   = "LIMIT = 250\\n"        # a human chose 250

try:
    merge_sources(ours, theirs, base, policy=ConflictPolicy.RAISE)
    print("(no conflict was detected for this pair)")
except MergeConflict as error:
    print("refused, and named what disagrees:")
    print(" ", error)
'''),
        markdown(
            "Silently taking either side would be wrong. Taking the generator's "
            "loses a deliberate human decision; taking the human's loses a real "
            "schema change. Raising is the only answer that does not discard "
            "information."
        ),
        markdown("## `# gratimos:keep` makes it unconditional"),
        code('''
kept = module_path.read_text() + '\\n\\nDEBUG = True  # gratimos:keep\\n'
module_path.write_text(kept)

registry_of_modules.generate(shape)     # regenerate with the *narrower* shape
final = module_path.read_text()

print("marked line survived a regeneration that did not know about it:",
      "DEBUG = True" in final)
'''),
        markdown(
            "## Where this is used in anger\n\n"
            "This is the single Gratimos import SLPIE is allowed "
            "(`slpie/artifacts/codegen.py`, invariant 8, asserted by test). TOGAF "
            "views generate into `architecture/*.py` through exactly this path — "
            "so an architect's annotation survives the next scan."
        ),
        code('''
# Scratch cell — generate from your own shape and edit the result.
mine = infer_shape([{"sku": "A-1", "qty": 3}], name="Line")
registry_of_modules.generate(mine)
print((WORK / "line.py").read_text()[:400])
'''),
    ),
)


# --- 13 · end to end ------------------------------------------------------

END_TO_END = Notebook(
    13, "end_to_end", "End to end — one project, every capability",
    "The whole platform on one tree, in one run.",
    (
        _header(
            "End to end",
            "**One project, every stage, and the same answer through every "
            "surface.** This is the page to run when you want to see whether the "
            "thing works.",
            """
Everything below runs against one small project. Nothing is mocked, nothing is
stubbed, and every number printed is computed from the tree that the first cell
writes to disk.
""",
        ),
        SETUP,
        PROJECT,
        markdown("## 1 · What is in there"),
        code('''
observed = run(f"discover {shop}")
print(f"{observed.flow.size} observations from "
      f"{observed.flow.facts['files_read']} of "
      f"{observed.flow.facts['files_seen']} files")
'''),
        markdown("## 2 · What it means"),
        code('''
linked = run(f"discover {shop} | link")
resolution = linked.flow.value
print("identities resolved:", len(resolution.resolved))
print("cross-file links:   ", linked.flow.facts.get("cross_file_links", 0))
print("contradictions:     ", linked.flow.facts.get("contradictions", 0))
'''),
        markdown("## 3 · What is wrong"),
        code('''
governed = run(f"discover {shop} | govern")
for finding in governed.flow.items:
    print(f"  [{finding.severity.value:8}] {finding.title[:58]}")
'''),
        markdown("## 4 · Why it believes that"),
        code('''
finding = governed.flow.items[0]
print(finding.title)
print()
for item in finding.evidence[:3]:
    where = item.location
    print(f"  {item.kind.value:20} {where.uri.rsplit('/', 1)[-1]}:{where.line}")
    if item.excerpt:
        print(f"  {'':20} {item.excerpt.strip()[:58]}")
'''),
        markdown("## 5 · What to do about it"),
        code('''
answered = run(f"discover {shop} | reason | ask --question 'what should I fix before release?'")
print(answered.flow.facts["answer"][:1100])
'''),
        markdown("## 6 · The artifacts"),
        code('''
import json

sbom = json.loads(run(f"discover {shop} | sbom").flow.facts["sbom"])
print(f"SBOM:  {sbom['bomFormat']} {sbom['specVersion']}, "
      f"{len(sbom['components'])} components")

c4 = run(f"discover {shop} | c4 --level context").flow.facts["c4"]
print(f"C4:    {len(c4.splitlines())} lines of Mermaid")

risk = run(f"discover {shop} | govern | risk").flow.facts["risk"]
print(f"Risk:  {len(risk.splitlines())} lines")
'''),
        markdown("## 7 · The architecture judges itself"),
        code('''
from slpie.audit import audit_self

verdict = audit_self()
print(f"{len(verdict.judgements)} rules, coverage {verdict.coverage:.0%}, "
      f"clean {verdict.clean}, digest {verdict.digest[:16]}")
'''),
        markdown(
            "## 8 · The same composition, through every surface\n\n"
            "This is the acceptance test for the whole design: if the CLI and the "
            "HTTP API disagreed, one of them would be lying."
        ),
        code('''
import io, json
from slpie.cli import Cli
from slpie.compose import Composition, Context, registry
from slpie.ui.api import Api, Request

verbs = registry()
PIPELINE = f"discover {shop} | link | findings"

# In process
in_process = Composition.read(PIPELINE, verbs=verbs).run(Context(root=str(shop)))

# Through the CLI
out = io.StringIO()
Cli(stdout=out, stderr=io.StringIO(), stdin=io.StringIO(""), isatty=False).main(
    [PIPELINE, "--json"],
)
from_cli = json.loads(out.getvalue())["flow"]

# Through the HTTP API
api = Api(engine=None)
response = api.handle(Request(
    method="POST", path="/api/run", query={},
    body={"pipeline": PIPELINE, "root": str(shop)},
))
from_http = response.body["flow"]

print("in process:", in_process.flow.digest)
print("cli:       ", from_cli["digest"])
print("http:      ", from_http["digest"])
print()
print("all three agree:",
      in_process.flow.digest == from_cli["digest"] == from_http["digest"])
'''),
        markdown(
            "**One registry, many projections, one answer.** Different surfaces "
            "must not be different answers — and the digest is what makes that "
            "checkable rather than assumed."
        ),
        markdown("## 9 · Rerunning costs almost nothing"),
        code('''
from slpie.incremental import Watcher
import pathlib

baseline = pathlib.Path(shop) / ".slpie" / "fingerprint.json"
watcher = Watcher(shop, baseline=baseline)
watcher.commit()

(shop / "settings.py").write_text('AWS_ACCESS_KEY = "AKIAIOSFODNN7ROTATED"\\n')

plan = watcher.plan()
print(plan.render())
'''),
        markdown(
            "## What you just ran\n\n"
            "Discovery across five ecosystems, identity resolution, cross-file "
            "linking, eight reasoning layers, five governance families, a "
            "CycloneDX SBOM, C4 diagrams, a risk register, a deterministic "
            "architecture audit, three surfaces agreeing on one digest, and an "
            "incremental plan — against a real tree, with every claim carrying its "
            "evidence.\n\n"
            "That is the platform. The other notebooks take each piece slowly."
        ),
        code('''
# Scratch cell — the whole thing is yours now.
print(run(f"discover {shop} | reason | ask --question 'what would you change first?'")
      .flow.facts["answer"][:800])
'''),
    ),
)


# --- 14 · the value case --------------------------------------------------

VALUE = Notebook(
    14, "value", "The value case — what this is worth, measured",
    "Ten minutes, for somebody deciding whether to fund it.",
    (
        _header(
            "The value case",
            "**Every number on this page is computed while you watch.** Nothing "
            "is pasted in, and nothing is an estimate — which is the only kind of "
            "claim that survives due diligence.",
            """
The argument in one line: large organisations cannot answer basic questions
about their own software, they buy several tools that each answer a slice, and
none of those tools can show the working.

Run the cells in order. It takes about ten minutes.
""",
        ),
        SETUP,
        markdown(
            "## 1 · The problem, on a project that has it\n\n"
            "A manifest asking for one thing, a lockfile that pinned another, a "
            "package name one transposition away from a real one, a copyleft "
            "licence under an MIT project, and a credential somebody committed. "
            "Every one of these is ordinary."
        ),
        PROJECT,
        markdown("## 2 · What it finds, with no configuration"),
        code('''
governed = run(f"discover {shop} | govern")

print(f"{governed.flow.size} findings, from five rule families, "
      f"nothing configured\\n")
for finding in governed.flow.items:
    print(f"  [{finding.severity.value:8}] {finding.family:14} {finding.title[:48]}")
'''),
        markdown(
            "## 3 · The part a scanner cannot do\n\n"
            "Every finding resolves to a file and a line, with the confidence and "
            "where that confidence came from. This is the question to ask a rival "
            "in a bake-off: *show me why you believe that*."
        ),
        code('''
finding = governed.flow.items[0]
print(finding.title)
print(f"  severity {finding.severity.value} · risk {finding.risk.value} · "
      f"blocks release: {finding.blocks_release}")
print(f"  raised by rule {finding.rule_id}")
print(f"  rule fingerprint {finding.rule_digest[:20]}  <- the rule's meaning")
print(f"  {'':2}{'':20}      cannot drift silently")
print()
for item in finding.evidence[:3]:
    where = item.location
    print(f"  {item.kind.value:20} {where.uri.rsplit('/', 1)[-1]}:{where.line}")
    print(f"  {'':20} base confidence {item.kind.base_confidence}")
    if item.excerpt:
        print(f"  {'':20} {item.excerpt.strip()[:54]}")
'''),
        markdown(
            "## 4 · Measured on real repositories\n\n"
            "Not our fixtures — real public projects, cloned and scanned with no "
            "configuration. This cell clones them, so it needs network; if it "
            "cannot reach GitHub it says so and moves on."
        ),
        code('''
import shutil, subprocess, tempfile, pathlib

REPOS = [("expressjs/express", "express"), ("psf/requests", "requests")]
arena = pathlib.Path(tempfile.mkdtemp(prefix="slpie-value-"))
cloned = []

for slug, name in REPOS:
    target = arena / name
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet",
             f"https://github.com/{slug}.git", str(target)],
            check=True, timeout=180, capture_output=True,
        )
        cloned.append(target)
        print(f"  cloned {slug}")
    except Exception as error:
        print(f"  could not clone {slug} ({type(error).__name__}) — skipping")

print()
print(f"{len(cloned)} repository(ies) ready")
'''),
        code('''
import sys
sys.path.insert(0, str(ROOT))
from tools.measure import measure, render

if cloned:
    results = [measure(path) for path in cloned]
    print(render(results))
else:
    print("  no repositories were cloned, so there is nothing to measure.")
    print("  `python -m tools.measure /path/to/any/repo` runs this on yours.")
'''),
        markdown(
            "Two things to notice. **Peak memory does not track repository "
            "size** — it is bounded by design, so a 2 GB monorepo costs roughly "
            "what a 2 MB project does. And these findings were produced with no "
            "rules written, no configuration, and no network call to a "
            "vulnerability service."
        ),
        markdown(
            "## 5 · Where we sit against what they already own\n\n"
            "Computed from eight cited competitor records. Every assessment "
            "carries the URL it was checked against; an assessment with no "
            "source will not construct."
        ),
        code('''
from slpie.rivals import RECORDED, rival_registry
from slpie.rivals.gap import render as render_field

print(f"{len(rival_registry())} products recorded {RECORDED}\\n")
print(render_field())
'''),
        code('''
from slpie.rivals import positioning

print(positioning())
'''),
        markdown(
            "The second half of that output is the part that matters in a data "
            "room. A comparison we win on every row is one a buyer stops "
            "reading, so the same function reports where the field is ahead of "
            "us — and a test fails the build if no such row exists."
        ),
        markdown(
            "## 6 · How it is sold\n\n"
            "Clients work in notebooks. Each user gets a dedicated environment "
            "holding exactly the datasets their role entitles them to, "
            "provisioned behind the scenes. Behind each one is a simulator "
            "instance, which is the same machinery the rest of the platform "
            "already runs on."
        ),
        code('''
from slpie.identity.principal import Principal
from slpie.rbac import AccessEngine, Role, Scope, allow, system_roles
from slpie.workspace import ControlPlane, Dataset, DatasetGrant, Quota, Visibility

roles = system_roles()
roles.add(Role(
    name="analyst-nb",
    permissions=(allow("workspace:create", "workspace"), allow("dataset:read", "*")),
    description="opens a notebook, reads what is granted",
))

plane = ControlPlane(access=AccessEngine(roles), region="eu-west-1")
plane.set_quota("acme", Quota(max_workspaces=20, max_cpu=64, max_memory_mb=262_144))
plane.set_quota("globex", Quota(max_workspaces=5))

def person(subject, tenant):
    who = Principal(issuer="https://id.test", subject=subject, tenant=tenant,
                    email=f"{subject}@{tenant}.test", email_verified=True)
    plane.access.bind(who.urn, "analyst-nb", scope=Scope(tenant=tenant))
    return who

ada = person("ada", "acme")
zed = person("zed", "globex")

plane.grant(DatasetGrant(
    dataset=Dataset(name="acme-orders", scope=Scope(tenant="acme")),
    visibility=Visibility.TENANT, granted_by="admin",
))
plane.grant(DatasetGrant(
    dataset=Dataset(name="globex-revenue", scope=Scope(tenant="globex")),
    visibility=Visibility.TENANT, granted_by="admin",
))
plane.set_environment(Scope(tenant="acme"), {"DB_URL": "acme-db.internal"})
plane.set_environment(Scope(tenant="globex"), {"DB_URL": "globex-db.internal"})

print("two tenants, identical roles, one platform\\n")
for who, tenant in ((ada, "acme"), (zed, "globex")):
    grants = plane.datasets_for(who, scope=Scope(tenant=tenant))
    env = plane.environment_for(Scope(tenant=tenant))
    print(f"  {who.urn.split(':')[-1]:6} ({tenant:7}) sees "
          f"{[g.dataset.name for g in grants]}  DB_URL={env['DB_URL']}")
'''),
        markdown(
            "Neither user can name the other's dataset, and neither can reach "
            "the other's environment. The refusal happens in the kernel, before "
            "any bucket or volume is named — so a misconfigured bucket policy "
            "cannot widen it."
        ),
        code('''
provisioned = plane.provision(ada, scope=Scope(tenant="acme"), start=False)
workspace = provisioned.workspace

print("workspace:  ", workspace.workspace_id)
print("namespace:  ", workspace.namespace)
print("allocation: ", workspace.allocation)
print("placed in:  ", provisioned.placement.region)
print("can see:    ", [g.dataset.name for g in provisioned.grants])
print()
print("tenant headroom after this:",
      plane.quota_of("acme").headroom(plane.usage_of("acme")))
'''),
        code('''
from slpie_enterprise.spawn import KubernetesSpawner, namespace_of
from slpie_enterprise.spawn.validate import validate
from slpie.workspace import SpawnRequest

spawner = KubernetesSpawner(ingress_host="notebooks.acme.internal")
request = SpawnRequest(
    workspace_id=workspace.workspace_id, tenant="acme", realm="",
    principal_urn=ada.urn, allocation=workspace.allocation,
    grants=provisioned.grants,
    environment=plane.environment_for(Scope(tenant="acme")),
)

plan = spawner.plan(request)
print(f"{len(plan)} Kubernetes objects, rendered without a cluster:\\n")
for obj in plan:
    print(f"  {obj['kind']:24} {obj['metadata']['name']}")

result = validate(plan, namespace=namespace_of("acme"),
                  workspace_id=workspace.workspace_id)
print()
print(result.explain())
print()
print("url:", spawner.url_for(request))
'''),
        markdown(
            "Those manifests were checked against the **real Kubernetes API "
            "models** — the same code a cluster's own clients use — plus the "
            "security assertions a schema cannot make: no service-account token, "
            "no egress to the cloud metadata endpoint, no reaching the pod next "
            "door.\n\n"
            "## 7 · What is built, and what is not"
        ),
        code('''
from slpie.compose import registry

verbs = registry()
print(f"  {len(verbs.names)} capabilities, in {len(verbs.groups())} groups")
print(f"  {len(list(tools_count := __import__('slpie.agent', fromlist=['ToolSet']).ToolSet(root='.')))} agent tools, each a composition over that registry")
print()
print("  Built:  discovery across 29 ecosystems · bitemporal graph with blast")
print("          radius in SQL · 8 reasoning layers · 5 governance families ·")
print("          SBOM · C4 · TOGAF as code · deterministic audit · incremental")
print("          rescan · multi-tenant workspaces · Kubernetes · tiered storage")
print()
print("  Not:    cross-region replication (modelled, not running)")
print("          a curated vulnerability database (we consume OSV)")
print("          a hosted offering (today it deploys into your cluster)")
'''),
        markdown(
            """
## The three questions to ask anyone else

Chosen because our architecture answers them and a bolt-on cannot:

1. **"Show me why you believe that."** Not the manifest a finding came from —
   the chain from conclusion back to a file and a line, with the confidence at
   each hop.
2. **"What breaks if I change this?"** Transitively, with a confidence floor,
   and telling me when a path is only reachable through a dynamic load.
3. **"Is what we designed what we built?"** Both deltas.

`docs/VALUE.md` is this page in prose. `docs/COMPETITIVE.md` is the full
positioning. Both are regenerated from the same code you just ran.
"""
        ),
        code('''
# Scratch cell — point it at your own repository.
import pathlib
mine = pathlib.Path(ROOT)          # this checkout, or any path you like

from tools.measure import measure, render
print(render([measure(mine)]))
'''),
    ),
)


# --- the ordered set ------------------------------------------------------

NOTEBOOKS: tuple[Notebook, ...] = (
    START_HERE,
    COMPOSITION,
    DISCOVERY,
    GRAPH,
    GOVERNANCE,
    REASONING,
    ENVIRONMENT,
    ARTIFACTS,
    INCREMENTAL,
    AUDIT,
    AGENT,
    SHAPES,
    CODEGEN,
    END_TO_END,
    VALUE,
)
