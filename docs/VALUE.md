# What this is, and who pays for it

Every number on this page was computed by running the platform. `tools/measure.py`
reproduces all of them; nothing here is an estimate.

---

## In one paragraph

Large organisations cannot answer basic questions about their own software:
*what depends on this?*, *is what we designed what we actually built?*, *why do
you believe that?* They buy several tools that each answer a slice — a scanner
for vulnerabilities, a portal for ownership, a bot for version bumps — and none
of them can show the working. **We answer those questions from one graph, and
every answer resolves to a file and a line.** Clients work in notebooks, so we
sell it as a notebook platform: each user gets a dedicated environment with the
datasets their role entitles them to, provisioned behind the scenes.

---

## What it does that the tools they already own do not

Computed from eight cited competitor records in `slpie/rivals/registry.py`.
Run `slpie 'rivals --gaps'` to regenerate it — nothing below is typed by hand.

| Capability | Mean coverage across 8 recorded products |
|---|---|
| **Reconcile what was declared against what was built** | **0.00** — absent from all eight |
| **Blast radius: what breaks if this changes** | 0.25 — absent from four |
| **Runs air-gapped, no service, no key** | 0.50 |
| **Every answer resolves to a file and a line** | 0.62 |

The first row is the product. Backstage records what teams *declared* in YAML
and never checks it. A scanner sees what is *there* and has no declaration to
check against. Holding both, with confidence on each, is a position neither can
reach without becoming the other.

**Where the field is ahead of us, and should be assumed to stay:** vulnerability
matching (Snyk's curated database is a data business, not a software one) and
dependency update pull requests (Renovate is free, open source and excellent).
The right move is to consume OSV and integrate with Renovate, not to compete.

---

## Measured, on real repositories

Three public repositories, cloned at HEAD, scanned with no configuration:

| | files read | observations | identities | findings | seconds | peak memory |
|---|---:|---:|---:|---:|---:|---:|
| `expressjs/express` | 159 | 350 | 198 | 56 | 9.1 | 5.9 MB |
| `pallets/flask` | 106 | 689 | 218 | 134 | 10.7 | 8.5 MB |
| `psf/requests` | 52 | 146 | 65 | 42 | 4.5 | 10.8 MB |
| **total** | **317** | **1,185** | **481** | **232** | **24.3** | |

Among them, one **critical** finding: credentials embedded in a URL, at
`requests/HISTORY.md:213`. Nobody configured a rule for that; it is one of five
governance families that run by default.

Two things to notice in that table. **Peak memory does not track repository
size** — it is bounded by design, so a 2 GB monorepo costs roughly what a 2 MB
project does. And **flask produced more observations than express from fewer
files**, because it declares across two ecosystems; the platform reads what is
there rather than what it was told to expect.

---

## Who buys it

| Buyer | The question they cannot currently answer | What they pay for today |
|---|---|---|
| **Platform / architecture lead** | "Is production what our architecture diagram says?" | A diagram somebody redraws quarterly and nobody trusts |
| **Head of security** | "If this package is compromised, what is our exposure?" | A CVE list with no blast radius |
| **Compliance / audit** | "Show me the evidence for that control, at the time of the decision" | Screenshots and a spreadsheet |
| **Data / research teams** | "Give me an isolated notebook with only my data in it" | A shared cluster and a naming convention |

The fourth row is why this ships as a notebook platform rather than a library.
It is also the row with an existing budget line in most organisations.

---

## Why it is hard to copy

Three of the four differentiators are architectural decisions taken at the type
level on the first commit, not features:

* **No relationship exists without evidence.** `Edge.__post_init__` refuses to
  construct one. Retrofitting that means rewriting every producer of every fact,
  which is why the products that have it have it in one narrow domain.
* **Confidence is derived, never assigned.** No caller passes a number; it is a
  pure function of evidence kind and independent corroboration.
* **Zero third-party dependencies in the kernel.** A constraint held from the
  start, asserted by a CI job that installs with no extras and checks what came
  with it. A hosted competitor cannot adopt it — it is their business model.

The fourth, the graph itself, is the one a rival could build. It would take them
the same eighteen months.

---

## What is built, and what is not

**Built and tested** — 2,564 tests, no network, no services:
discovery across 29 ecosystems and formats · the bitemporal graph with blast
radius and cycles in SQL · eight reasoning layers · five governance families ·
CycloneDX and SPDX SBOM · C4 and TOGAF views as importable code · a deterministic
architecture audit with a reproducible digest · incremental rescanning · the
multi-tenant workspace control plane · Kubernetes provisioning · tiered object
storage · fourteen executable notebooks that CI runs on every push.

**Not built.** Cross-region replication is modelled but not running. The
vulnerability database is consumed, not curated. There is no hosted offering
yet — today it deploys into the customer's own cluster, which several buyers
prefer and one segment requires.

---

## The honest risks

* **The catalogue segment is crowded and Backstage is free.** Our claim there is
  not a better catalogue, it is one whose entries were verified against the tree.
  If that distinction does not land with buyers, that segment is not ours.
* **Kubernetes provisioning is validated, not yet run at scale.** Every manifest
  is checked against the real Kubernetes API models in CI, and the security
  posture is asserted — but no production cluster has run it.
* **Two engines is a harder story to tell than one.** Gratimos and SLPIE are
  genuinely separable, and the notebook platform currently leads with SLPIE.

---

## Try it in ten minutes

```bash
git clone https://github.com/Reimain/Macropol-s.git && cd Macropol-s
make setup && make lab      # opens the notebooks
```

Or without installing anything, open `notebooks/00_start_here.ipynb` in Colab —
the first cell installs the package. It finds a real defect in a real project and
shows you the line it came from.

```bash
slpie 'discover . | govern'          # what is wrong here
slpie 'rivals --gaps'                # this page's competitive table, computed
python -m tools.measure /path/to/repo  # this page's numbers, on your code
```
