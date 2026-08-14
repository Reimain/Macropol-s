# Macropol-s

We build for leaders

---

Two systems, one repository, no third-party dependencies in either kernel.

| | |
|---|---|
| **SLPIE** | An architecture intelligence engine. Point it at a tree or a manifest and it tells you what is in there, what depends on what, what is wrong — and *why it believes each answer*, down to a file and a line. |
| **Gratimos** | A data-shaping kernel. It infers shapes from messy sources, casts safely between them, and generates typed code with a three-way AST merge that survives your edits. |

**[docs/VALUE.md](docs/VALUE.md)** — what this is worth, measured on real
repositories, with the competitive position computed rather than asserted.

## Start in a notebook

The interactive path is the one most people want. Sixteen executable pages,
every cell of which runs — CI executes all of them on every push.

[![Binder](https://mybinder.org/badge_logo.svg)](https://mybinder.org/v2/gh/Reimain/Macropol-s/claude/interactive-notebooks?urlpath=lab/tree/notebooks)
[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Reimain/Macropol-s/blob/claude/interactive-notebooks/notebooks/00_start_here.ipynb)

```bash
git clone https://github.com/Reimain/Macropol-s.git
cd Macropol-s
make setup      # kernel + notebook layer + Jupyter kernel
make lab        # opens JupyterLab
```

Then open [`notebooks/00_start_here.ipynb`](notebooks/00_start_here.ipynb). It
gets you from nothing to a real answer, with evidence, in about a minute.
[The full index is here.](notebooks/README.md)

## Or from the command line

```bash
pip install -e .

slpie 'discover . | govern'                    # what is wrong in this tree
slpie 'discover . | reason | ask --question "what should I fix first?"'
slpie 'discover . | sbom --format cyclonedx'   # a bill of materials
slpie audit                                    # judge the architecture, reproducibly
slpie demo                                     # the narrated end-to-end run
slpie help                                     # 45 verbs, generated from the registry
```

The interesting answers come from composition. `|` is a real pipe, and what
flows through it is not bytes — it is a value **plus its reasoning and its
gaps**, so composing accumulates the explanation instead of discarding it.

```bash
slpie 'discover . | link | constraints | findings --severity high' --explain
```

An impossible composition is refused *before anything runs*, with both kinds
named — not halfway through, with a stack trace and a half-changed environment.

## Gratimos

```bash
gratimos govern ./data --depth govern
```

```python
from gratimos import Depth, govern

report = govern("./data", depth=Depth.GENERATE)
print(report.summary())
```

It reads JSON, CSV, XLSX, SQLite, APIs, images, video, and shell scripts;
generates dataclass modules and protobuf schemas that merge at the AST level
instead of overwriting your edits; runs operator transformations in a gated,
resource-limited sandbox; keeps schema evolution in a reversible Alembic-shaped
ledger; and talks to other agents over A2A — including UiPath processes and
Claude.

## What holds it together

* **No relationship without evidence.** Enforced in the type, not by review.
* **Confidence is derived, never assigned.** No caller passes a number.
* **Every answer carries its reasoning and its gaps** — including through a
  four-stage pipe.
* **Zero third-party dependencies in either kernel**, including the UI. Asserted
  by a CI job that installs with no extras and checks what came with it.
* **The same composition gives the same answer through every surface** — CLI,
  HTTP, notebook — and the digest is what makes that checkable.
* **It declines to rule where it cannot see.** A layer with missing inputs
  abstains; an audit says `INDETERMINATE`; an incremental scan reports a file it
  could not read as *unknown* rather than as deleted.

## Working on it

```bash
make setup            # everything, from nothing
make test             # the whole suite, no network
make acceptance       # drive all 48 verbs end to end, then check four claims
make notebooks-run    # execute every notebook, fail on any cell that raises
make invariants       # the architectural boundaries, on their own
make audit            # the judge, on this repository
make help             # the rest
```

* **[docs/](docs/index.md)** — the full Sphinx reference: every module, from the
  docstrings. `make docs` builds it; the docstrings *are* the documentation here,
  so it reads as prose rather than only as a lookup.
* **[docs/VALUE.md](docs/VALUE.md)** — the value case, every number computed
* **[docs/COMPETITIVE.md](docs/COMPETITIVE.md)** — where we sit, with citations
* **[notebooks/](notebooks/README.md)** — the interactive pages, and how they are generated
* **[docs/AUDIT.md](docs/AUDIT.md)** — what is wrong with this codebase, measured
* **[docs/README.md](docs/README.md)** — Gratimos usage, CLI, safety guarantees
* **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — why the boundaries sit where they do

The kernel has no third-party dependencies; optional extras widen format and
storage coverage without ever being required to start.
