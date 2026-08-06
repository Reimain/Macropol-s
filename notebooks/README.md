# The notebooks

Fourteen executable pages. Every cell in every one of them runs — CI executes
the lot on each push and fails if any cell raises, so a page that cannot run
does not reach the branch.

**Start with [`00_start_here.ipynb`](00_start_here.ipynb).** It gets you from
nothing to a real answer, with evidence, in about a minute.

## Run them

| Where | How |
|---|---|
| **Locally** | `make setup && make lab` |
| **Colab** | open any notebook from GitHub — the first cell clones and installs |
| **Binder** | [![Binder](https://mybinder.org/badge_logo.svg)](https://mybinder.org/v2/gh/Reimain/Macropol-s/claude/interactive-notebooks?urlpath=lab/tree/notebooks) |
| **VS Code / Codespaces** | open the repository; the devcontainer installs everything |

Locally, from a clean checkout, the whole thing is:

```bash
git clone https://github.com/Reimain/Macropol-s.git
cd Macropol-s
make setup      # installs the kernel, the notebook layer, and a Jupyter kernel
make lab        # opens JupyterLab on this directory
```

The first cell of every notebook installs the package if it is not already
importable, so a notebook opened on its own in Colab works with no further
setup.

## The pages

| | Notebook | What it covers |
|---|---|---|
| 00 | [`start_here`](00_start_here.ipynb) | The five-minute version: find something wrong, and see why it believes that |
| 01 | [`composition`](01_composition.ipynb) | Verbs, typed pipes, and why an impossible pipeline is refused before it runs |
| 02 | [`discovery`](02_discovery.ipynb) | 29 discoverers, and the confidence ladder they feed |
| 03 | [`graph`](03_graph.ipynb) | Bitemporal graph, blast radius and cycles — traversed in SQL |
| 04 | [`governance`](04_governance.ipynb) | Five rule families, and how a finding earns its severity |
| 05 | [`reasoning`](05_reasoning.ipynb) | L1–L8, asking in English, and a layer declining to rule |
| 06 | [`environment`](06_environment.ipynb) | Manifests, the simulator, and firing conditions at a world |
| 07 | [`artifacts`](07_artifacts.ipynb) | SBOM, C4, TOGAF views, risk registers |
| 08 | [`incremental`](08_incremental.ipynb) | Rescanning only what moved, and refusing to guess about the rest |
| 09 | [`audit`](09_audit.ipynb) | The judge: deterministic verdicts with a reproducible digest |
| 10 | [`agent`](10_agent.ipynb) | A tool set that is a projection of the registry |
| 11 | [`gratimos_shapes`](11_gratimos_shapes.ipynb) | Inferring shapes from messy data, and casting safely |
| 12 | [`gratimos_codegen`](12_gratimos_codegen.ipynb) | Generated code that survives your edits |
| 13 | [`end_to_end`](13_end_to_end.ipynb) | All of it, one project, one run |

Each one stands alone. Opening notebook 7 without having run notebook 3 works —
every page builds whatever it needs in a temp directory, which is also what makes
them safe to run in any order.

## They are generated

The notebooks are **built from
[`tools/notebooks/spec.py`](../tools/notebooks/spec.py)**, not edited in place.

```bash
make notebooks          # regenerate from the spec
make notebooks-check    # fail if any notebook is stale
make notebooks-run      # execute every one; any cell that raises fails
```

This is the same argument §24 makes for the verb registry: a notebook is a
*projection* of the platform, and a projection maintained by hand drifts from
what it projects. A drifted notebook is worse than stale documentation, because
it looks executable and therefore looks checked.

So editing works like this:

* **Exploring?** Change any cell and run it. That is what the pages are for, and
  nothing stops you.
* **Keeping the change?** Make it in `tools/notebooks/spec.py` and run
  `make notebooks`. CI runs `--check`, so an edit made only in the `.ipynb`
  is caught rather than silently overwritten on the next rebuild.

Outputs are **not committed**. A committed output embeds one run's temp paths
and timings, which puts noise in every diff. The thing worth committing is the
source of a page known to run; `make notebooks-run --write` produces a rendered
copy if you want one to publish.

## Conventions

Every notebook follows three rules, and each is there because the alternative
produces a page that looks fine and teaches nothing:

1. **Every code cell runs.** No `...`, no pseudo-code, no cell needing a file the
   reader has to supply.
2. **Every notebook stands alone.** No cross-notebook state.
3. **Outputs are shown, not described.** A cell that prints a digest prints the
   digest — prose saying "you will see a digest" is prose nobody can check.

Most pages end with a **scratch cell**: a working starting point meant to be
edited. That is where the interactive work actually happens.
