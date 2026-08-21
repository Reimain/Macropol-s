# API reference

Every module in the repository, generated from the source. There is no curated
subset: a reference that showed only the parts somebody thought were interesting
would answer "does this exist?" with silence for everything else.

The four packages are layered, and the layering is enforced by tests rather than
by convention:

```{list-table}
:header-rows: 1
:widths: 22 12 66

* - Package
  - Ring
  - What it is
* - {py:mod}`slpie`
  - 0
  - The architecture intelligence kernel. Standard library only, offline at
    every phase. It does not know that a framework exists.
* - {py:mod}`gratimos`
  - 0
  - The data-shaping kernel. Also standard library only; the optional extras
    widen format coverage without ever being required to start.
* - {py:mod}`slpie_enterprise`
  - 1
  - Kubernetes, object storage and the adapters that need real infrastructure.
    Imports ring 0's public API and nothing private; ring 0 never imports it.
* - {py:mod}`tools`
  - —
  - Not shipped. The notebook generator and the measurement harness that
    produces the numbers in {doc}`../VALUE`.
```

Exactly one module in `slpie` may import `gratimos` — {py:mod}`slpie.artifacts.codegen`,
the code-generation bridge — and that is asserted by an `ast` walk in the suite
and judged again by {py:mod}`slpie.audit`, so the two have to agree.

## Where to start

```{list-table}
:header-rows: 1
:widths: 34 66

* - If you want to understand
  - Read
* - How capabilities compose
  - {py:mod}`slpie.compose.flow`, {py:mod}`slpie.compose.verb`, {py:mod}`slpie.compose.pipeline`
* - Why an answer is believed
  - {py:mod}`slpie.domain.evidence`, {py:mod}`slpie.domain.reasoning`
* - What is in a tree
  - {py:mod}`slpie.discovery`, {py:mod}`slpie.capture`
* - How the graph is queried
  - {py:mod}`slpie.graph.sqlite_graph`, {py:mod}`slpie.graph.traversal`
* - What is wrong with a tree
  - {py:mod}`slpie.governance`, {py:mod}`slpie.reasoning`
* - Multi-tenant notebook workspaces
  - {py:mod}`slpie.workspace`, {py:mod}`slpie_enterprise.spawn`
* - Shapes, casting and codegen
  - {py:mod}`gratimos.meta`, {py:mod}`gratimos.codegen`
```

## The packages

```{eval-rst}
.. autosummary::
   :toctree: generated
   :template: autosummary/module.rst
   :recursive:

   slpie
   gratimos
   slpie_enterprise
   tools
```
