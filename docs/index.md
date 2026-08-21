# Macropol-s

Two systems in one repository, neither of which has a third-party dependency in
its kernel.

**SLPIE** is an architecture intelligence engine. Point it at a tree or a
manifest and it tells you what is in there, what depends on what, what is wrong —
and *why it believes each answer*, down to a file and a line.

**Gratimos** is a data-shaping kernel. It infers shapes from messy sources, casts
safely between them, and generates typed code with a three-way AST merge that
survives your edits.

## Reading this documentation

The API reference below is generated from the source, and in this repository that
is not a formality: **the docstrings are the documentation.** They are long, they
argue for the decisions they describe, and for several constraints they are the
only place the reasoning is written down. A module docstring here typically says
what the module does, what it deliberately does not do, and what went wrong in
the version before this one.

So the reference is worth reading as prose, not only consulted as a lookup. Good
places to start:

- {py:mod}`slpie.compose.flow` — why the pipe carries provenance rather than bytes
- {py:mod}`slpie.domain.evidence` — how confidence is derived and never assigned
- {py:mod}`slpie.capture.firewall` — the four verdicts, and why the fourth is not redundant
- {py:mod}`slpie.workspace.plane` — the order of operations *is* the security argument
- {py:mod}`gratimos.meta.shapes` — what a source actually contains

```{toctree}
:caption: The case
:maxdepth: 2

VALUE
COMPETITIVE
```

```{toctree}
:caption: How it is built
:maxdepth: 2

UI
ARCHITECTURE
AUDIT
README
```

```{toctree}
:caption: Build records
:maxdepth: 1
:glob:

PHASE*
```

```{toctree}
:caption: API reference
:maxdepth: 2

reference/index
```

<!-- The build records glob deliberately. `docs/PHASE15.md` was added and not
     listed, and the strict build has been red ever since — an orphan page is a
     warning and `-W` turns a warning into a failure. A pattern picks up the
     next one on its own, so the phase after this cannot break the build by
     being written. -->

## What holds it together

* **No relationship without evidence.** Enforced in the type, not by review —
  {py:meth}`slpie.domain.edge.Edge.__post_init__` refuses to construct one.
* **Confidence is derived, never assigned.** No caller passes a number.
* **Every answer carries its reasoning and its gaps**, including through a
  four-stage pipe.
* **Zero third-party dependencies in either kernel**, asserted by a CI job that
  installs with no extras and checks what came with it. Sphinx is a `docs` extra
  and mocks the ring-1 imports, so this documentation builds from that same
  kernel-only install.
* **It declines to rule where it cannot see.** A layer with missing inputs
  abstains; an audit says `INDETERMINATE`; an incremental scan reports a file it
  could not read as *unknown* rather than as deleted.

## Building these pages

```bash
pip install -e '.[docs]'
make docs           # html into docs/_build/html
make docs-strict    # the same build, with warnings as errors
make docs-coverage  # what is documented and what is not, measured
```

## Licence

**Source-available, not open source.** Read it, study it, evaluate it — but
**commercial use of any kind requires a separate written licence**, including
internal use inside a company and anything run in production. The full terms are
in the repository's `LICENSE` file and they govern; the summary here does not.
