# Where we sit

Generated from `slpie/rivals/registry.py`, where every product carries a
homepage, every capability assessment carries the URL it was checked against and
the month, and an assessment with no source **will not construct** —
`Coverage.UNKNOWN` is the honest alternative and it counts against our
confidence, not against the product.

Regenerate with `slpie rivals` and `slpie 'rivals --gaps'`.

**These products are good at what they do.** The argument is not that they are
bad; it is that they answer different questions.

## The table

```
The field, as recorded 2026-07
  ### full   ## partial   · none   ? not verified

  capability                       snyk dependabo backstage  renovate    socket     fossa sourcegra databrick
  -----------------------------------------------------------------------------------------------------------
  vulnerability_matching            ###       ###        ##        ##       ###       ###        ##         ·
  licence_compliance                ###        ##         ·         ·        ##       ###         ·         ·
  secret_detection                   ##       ###         ·         ·        ##         ·        ##         ·
  dependency_updates                ###       ###         ·       ###         ·         ·        ##         ·
  service_catalogue                  ##         ·       ###         ·         ·         ·         ·        ##
  blast_radius                        ·         ·        ##         ·         ·        ##        ##        ##
  evidence_provenance                ##        ##         ·        ##       ###       ###       ###        ##
  declared_vs_observed                ·         ·         ·         ·         ·         ·         ·         ·
  offline_operation                  ##         ·       ###       ###         ·        ##       ###         ·

  verified share of these records: 100%
```

## Computed positioning

```
Positioning, computed from 8 recorded products (2026-07)
  ========================================================================

  What we do that the recorded field mostly does not:

    · declared vs observed
        reconciles intended architecture against reality
        absent from 8 of 8 recorded products; mean coverage 0.00
        why it is hard to copy: Backstage has the declaration and never checks it.

    · blast radius
        answers what breaks if this changes, transitively
        absent from 4 of 8 recorded products; mean coverage 0.25
        why it is hard to copy: Requires a typed graph with per-edge confidence and bitemporal history.

    · offline operation
        runs air-gapped, with no service and no key
        absent from 3 of 8 recorded products; mean coverage 0.50
        why it is hard to copy: Not a feature — a constraint held from the first commit.

    · evidence provenance
        every answer resolves to a file and a line
        absent from 1 of 8 recorded products; mean coverage 0.62
        why it is hard to copy: This is an architectural decision taken at the type level on day one.

  Where the field is ahead of us, and should be assumed to stay.
  Named on purpose: a comparison we win on every row is one
  a buyer stops reading.

    · dependency updates
        led by snyk, dependabot, renovate; mean coverage 0.44
        still missing here: The whole pull-request pipeline: branching, CI integration, auto-merge, per-ecosystem update strategies
    · vulnerability matching
        led by snyk, dependabot, socket, fossa; mean coverage 0.69
        still missing here: A curated, continuously updated advisory database — which is a data business, not a software one

  Every row above is derived from the cited records in
  `slpie/rivals/registry.py`. Nothing here is typed by hand.
```

## Product by product

| Product | What it is for | Where the boundary is |
|---|---|---|
| **Snyk** | Vulnerabilities in dependencies, with fix PRs | Tells you a package has a CVE. Cannot tell you what breaks if you change it. Reachability of a vulnerable *function* is a different question from what depends on a *package*. |
| **Dependabot** | Free version bumps and GitHub advisories | The bar, not a competitor. Ubiquitous and good. No graph, no reconciliation, hosted only. |
| **Backstage** | The developer portal and service catalogue | Records what teams **declared** in YAML and never checks it against the tree. That gap is our first row. |
| **Renovate** | Dependency updates, more ecosystems than anyone | Best-in-class at the narrow job. Integrate, do not compete. |
| **Socket** | Supply-chain behaviour — install scripts, network, obfuscation | Genuinely strong on evidence, scoped to package behaviour rather than the whole graph. |
| **FOSSA** | Licence compliance and SBOM at enterprise scale | Better than us at the legal workflow, and should be assumed to stay so. |
| **Sourcegraph** | Code search and large-scale change | Precise references answer "who calls this symbol" — one level below "what breaks". Indexes symbols; does not model an architecture. |
| **Databricks** | Multi-tenant notebooks with governed data | In this table as the bar for the **workspace** half of our product, not as an architecture competitor. Their Unity Catalog lineage is the same idea in another domain. |

## The three questions to ask a rival

Chosen because they are the ones our architecture answers and a bolt-on cannot:

1. **"Show me why you believe that."** Not the manifest the finding came from —
   the chain from conclusion back to a file and a line, with the confidence at
   each hop and where it came from.
2. **"What breaks if I change this?"** Transitively, with a confidence floor, and
   telling me when a path is only reachable through a dynamic load.
3. **"Is what we designed what we built?"** Both deltas: declared-and-absent, and
   observed-and-undeclared.

## What we would do in their position

Worth writing down, because assuming a competitor is stupid is how a roadmap
gets built on sand.

* **Snyk** could add blast radius. They have the dependency graph; they lack
  per-edge confidence and bitemporal history, so it would be a reachability
  feature rather than an answer with provenance. Eighteen months, and it would
  be good.
* **Backstage** could add reconciliation via a plugin that scans and diffs
  against the catalogue. The obstacle is cultural rather than technical — the
  catalogue's premise is that teams declare, and checking the declaration
  undermines the adoption story.
* **Databricks** could add software lineage beside data lineage. This is the one
  to watch: they have the workspace, the governance, and the buyer.

**Our defence is not that these are impossible.** It is that each requires the
thing to have been built on evidence from the first commit, and the honest
window is about eighteen months.
