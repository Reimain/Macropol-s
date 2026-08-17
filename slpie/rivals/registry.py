"""The field, as recorded in 2026-07.

Every entry cites a public page. Where a capability was not verified it says
`UNKNOWN` rather than guessing, and `Rival.verified_share` reports how much of
each record we actually checked — because a comparison that looks complete and
was filled in from memory is the kind of document that loses a deal in diligence.

**These products are good at what they do.** The point of this file is not that
they are bad; it is that they answer different questions. Snyk tells you a
package has a CVE. Backstage tells you who owns a service. Neither tells you
*what breaks if this package changes*, and neither can show you the file and line
behind the answer it did give.

The capability list below is the one thing here that is ours to choose, and it is
chosen to be checkable rather than flattering. Three of the nine are things
several rivals do better than we do, and they are in the table for that reason.
"""

from __future__ import annotations

from typing import Any

from .rival import Capability, Coverage, Evidence, Rival, Segment

#: When this file was last checked against the products it describes. Shown
#: beside every rendering, because the honest thing to say about a fast-moving
#: market is when you last looked.
RECORDED = "2026-07"

#: The capabilities compared. Deliberately includes things we do *not* lead on —
#: a scorecard where we win every row is a scorecard nobody believes.
CAPABILITIES: tuple[tuple[str, str], ...] = (
    ("vulnerability_matching", "matches known CVEs against resolved versions"),
    ("licence_compliance", "detects licence conflicts, obligations, and SBOM"),
    ("secret_detection", "finds credentials committed to the tree"),
    ("dependency_updates", "opens pull requests to move versions forward"),
    ("service_catalogue", "records ownership, services, and their metadata"),
    ("blast_radius", "answers what breaks if this changes, transitively"),
    ("evidence_provenance", "every answer resolves to a file and a line"),
    ("declared_vs_observed", "reconciles intended architecture against reality"),
    ("offline_operation", "runs air-gapped, with no service and no key"),
)


def _cite(source: str, quote: str = "", checked: str = RECORDED) -> Evidence:
    return Evidence(source=source, checked=checked, quote=quote)


def rival_registry() -> tuple[Rival, ...]:
    """Every product recorded, with citations."""
    return (
        Rival(
            id="snyk",
            name="Snyk",
            vendor="Snyk Ltd",
            segments=(Segment.SCA, Segment.SAST, Segment.LICENSING),
            homepage="https://snyk.io/product/open-source-security-management/",
            summary=(
                "Developer-first security. Scans dependencies against a curated "
                "vulnerability database, opens fix pull requests, and gates CI. "
                "The strongest product in this list at the job it does."
            ),
            capabilities=(
                Capability("vulnerability_matching", Coverage.FULL, _cite(
                    "https://snyk.io/product/open-source-security-management/",
                    "finds and automatically fixes vulnerabilities in dependencies",
                )),
                Capability("licence_compliance", Coverage.FULL, _cite(
                    "https://snyk.io/product/open-source-license-compliance/",
                )),
                Capability("secret_detection", Coverage.PARTIAL, _cite(
                    "https://snyk.io/product/snyk-code/",
                ), note="part of the code product rather than the SCA product"),
                Capability("dependency_updates", Coverage.FULL, _cite(
                    "https://docs.snyk.io/scan-with-snyk/pull-requests",
                )),
                Capability("service_catalogue", Coverage.PARTIAL, _cite(
                    "https://snyk.io/product/apprisk/",
                ), note="asset inventory, not an ownership catalogue"),
                Capability("blast_radius", Coverage.NONE, _cite(
                    "https://docs.snyk.io/scan-with-snyk/snyk-open-source",
                ), note=(
                    "reports reachability of a vulnerable function, which is a "
                    "different question from what depends on this package"
                )),
                Capability("evidence_provenance", Coverage.PARTIAL, _cite(
                    "https://docs.snyk.io/scan-with-snyk/snyk-open-source",
                ), note="cites the manifest path; not a per-claim evidence chain"),
                Capability("declared_vs_observed", Coverage.NONE, _cite(
                    "https://snyk.io/product/open-source-security-management/",
                )),
                Capability("offline_operation", Coverage.PARTIAL, _cite(
                    "https://docs.snyk.io/enterprise-setup/snyk-broker",
                ), note="broker keeps code on-premise; the database is hosted"),
            ),
        ),
        Rival(
            id="dependabot",
            name="Dependabot",
            vendor="GitHub (Microsoft)",
            segments=(Segment.DEP_UPDATES, Segment.SCA),
            homepage="https://docs.github.com/en/code-security/dependabot",
            summary=(
                "Version bumps as pull requests, plus alerts from the GitHub "
                "Advisory Database. Free, ubiquitous, and the default answer for "
                "most teams — which makes it the bar rather than a competitor."
            ),
            open_source=True,
            capabilities=(
                Capability("vulnerability_matching", Coverage.FULL, _cite(
                    "https://docs.github.com/en/code-security/dependabot/dependabot-alerts/about-dependabot-alerts",
                )),
                Capability("licence_compliance", Coverage.PARTIAL, _cite(
                    "https://docs.github.com/en/code-security/supply-chain-security/understanding-your-software-supply-chain/about-dependency-review",
                ), note="license check in dependency review, not full compliance"),
                Capability("secret_detection", Coverage.FULL, _cite(
                    "https://docs.github.com/en/code-security/secret-scanning/about-secret-scanning",
                ), note="a separate GitHub feature, not Dependabot itself"),
                Capability("dependency_updates", Coverage.FULL, _cite(
                    "https://docs.github.com/en/code-security/dependabot/dependabot-version-updates",
                )),
                Capability("service_catalogue", Coverage.NONE, _cite(
                    "https://docs.github.com/en/code-security/dependabot",
                )),
                Capability("blast_radius", Coverage.NONE, _cite(
                    "https://docs.github.com/en/code-security/dependabot",
                )),
                Capability("evidence_provenance", Coverage.PARTIAL, _cite(
                    "https://docs.github.com/en/code-security/dependabot/dependabot-alerts/about-dependabot-alerts",
                ), note="names the manifest; no chain from conclusion to source"),
                Capability("declared_vs_observed", Coverage.NONE, _cite(
                    "https://docs.github.com/en/code-security/dependabot",
                )),
                Capability("offline_operation", Coverage.NONE, _cite(
                    "https://docs.github.com/en/code-security/dependabot",
                ), note="a hosted GitHub service"),
            ),
        ),
        Rival(
            id="backstage",
            name="Backstage",
            vendor="Spotify / CNCF",
            segments=(Segment.CATALOGUE,),
            homepage="https://backstage.io/",
            summary=(
                "An open developer portal. A software catalogue of components, "
                "APIs and their owners, assembled from YAML that teams write. "
                "It is the standard answer to 'who owns this service'."
            ),
            open_source=True,
            capabilities=(
                Capability("vulnerability_matching", Coverage.PARTIAL, _cite(
                    "https://backstage.io/plugins/",
                ), note="through third-party plugins, not core"),
                Capability("licence_compliance", Coverage.NONE, _cite(
                    "https://backstage.io/docs/features/software-catalog/",
                )),
                Capability("secret_detection", Coverage.NONE, _cite(
                    "https://backstage.io/docs/features/software-catalog/",
                )),
                Capability("dependency_updates", Coverage.NONE, _cite(
                    "https://backstage.io/docs/features/software-catalog/",
                )),
                Capability("service_catalogue", Coverage.FULL, _cite(
                    "https://backstage.io/docs/features/software-catalog/",
                    "keeps track of ownership and metadata for all the software",
                )),
                Capability("blast_radius", Coverage.PARTIAL, _cite(
                    "https://backstage.io/docs/features/software-catalog/system-model",
                ), note=(
                    "relations between catalogue entries — but the graph is what "
                    "teams *declared* in YAML, so it is only as true as the YAML"
                )),
                Capability("evidence_provenance", Coverage.NONE, _cite(
                    "https://backstage.io/docs/features/software-catalog/descriptor-format",
                ), note="entries are hand-written declarations, uncorroborated"),
                Capability("declared_vs_observed", Coverage.NONE, _cite(
                    "https://backstage.io/docs/features/software-catalog/",
                ), note=(
                    "this is the gap that matters: Backstage records the "
                    "declaration and never checks it against the tree"
                )),
                Capability("offline_operation", Coverage.FULL, _cite(
                    "https://backstage.io/docs/deployment/",
                ), note="self-hosted by design"),
            ),
        ),
        Rival(
            id="renovate",
            name="Renovate",
            vendor="Mend.io",
            segments=(Segment.DEP_UPDATES,),
            homepage="https://docs.renovatebot.com/",
            summary=(
                "Dependency updates across far more ecosystems than Dependabot, "
                "with deep configuration. Self-hostable. The best-in-class tool "
                "for the narrow job of moving versions forward."
            ),
            open_source=True,
            capabilities=(
                Capability("vulnerability_matching", Coverage.PARTIAL, _cite(
                    "https://docs.renovatebot.com/configuration-options/#vulnerabilityalerts",
                )),
                Capability("licence_compliance", Coverage.NONE, _cite(
                    "https://docs.renovatebot.com/",
                )),
                Capability("secret_detection", Coverage.NONE, _cite(
                    "https://docs.renovatebot.com/",
                )),
                Capability("dependency_updates", Coverage.FULL, _cite(
                    "https://docs.renovatebot.com/",
                    "automated dependency updates. Multi-platform and multi-language",
                )),
                Capability("service_catalogue", Coverage.NONE, _cite(
                    "https://docs.renovatebot.com/",
                )),
                Capability("blast_radius", Coverage.NONE, _cite(
                    "https://docs.renovatebot.com/",
                )),
                Capability("evidence_provenance", Coverage.PARTIAL, _cite(
                    "https://docs.renovatebot.com/configuration-options/",
                )),
                Capability("declared_vs_observed", Coverage.NONE, _cite(
                    "https://docs.renovatebot.com/",
                )),
                Capability("offline_operation", Coverage.FULL, _cite(
                    "https://docs.renovatebot.com/self-hosting/",
                )),
            ),
        ),
        Rival(
            id="socket",
            name="Socket",
            vendor="Socket, Inc",
            segments=(Segment.SUPPLY_CHAIN, Segment.SCA),
            homepage="https://socket.dev/",
            summary=(
                "Supply-chain attack detection: inspects what a package actually "
                "does — install scripts, network access, obfuscation — rather "
                "than only matching it against a CVE list."
            ),
            capabilities=(
                Capability("vulnerability_matching", Coverage.FULL, _cite(
                    "https://docs.socket.dev/docs/what-is-socket",
                )),
                Capability("licence_compliance", Coverage.PARTIAL, _cite(
                    "https://docs.socket.dev/docs/license-policy",
                )),
                Capability("secret_detection", Coverage.PARTIAL, _cite(
                    "https://docs.socket.dev/docs/alert-types",
                )),
                Capability("dependency_updates", Coverage.NONE, _cite(
                    "https://docs.socket.dev/docs/what-is-socket",
                )),
                Capability("service_catalogue", Coverage.NONE, _cite(
                    "https://docs.socket.dev/docs/what-is-socket",
                )),
                Capability("blast_radius", Coverage.NONE, _cite(
                    "https://docs.socket.dev/docs/what-is-socket",
                )),
                Capability("evidence_provenance", Coverage.FULL, _cite(
                    "https://docs.socket.dev/docs/alert-types",
                ), note=(
                    "genuinely strong here — alerts point at the offending code. "
                    "Scoped to package behaviour rather than to the whole graph"
                )),
                Capability("declared_vs_observed", Coverage.NONE, _cite(
                    "https://docs.socket.dev/docs/what-is-socket",
                )),
                Capability("offline_operation", Coverage.NONE, _cite(
                    "https://docs.socket.dev/docs/what-is-socket",
                )),
            ),
        ),
        Rival(
            id="fossa",
            name="FOSSA",
            vendor="FOSSA, Inc",
            segments=(Segment.LICENSING, Segment.SCA),
            homepage="https://fossa.com/",
            summary=(
                "Licence compliance and SBOM at enterprise scale, with legal "
                "workflow around obligations and attribution. The reference "
                "product for the compliance buyer."
            ),
            capabilities=(
                Capability("vulnerability_matching", Coverage.FULL, _cite(
                    "https://docs.fossa.com/docs/vulnerabilities",
                )),
                Capability("licence_compliance", Coverage.FULL, _cite(
                    "https://docs.fossa.com/docs/license-compliance",
                )),
                Capability("secret_detection", Coverage.NONE, _cite(
                    "https://docs.fossa.com/docs",
                )),
                Capability("dependency_updates", Coverage.NONE, _cite(
                    "https://docs.fossa.com/docs",
                )),
                Capability("service_catalogue", Coverage.NONE, _cite(
                    "https://docs.fossa.com/docs",
                )),
                Capability("blast_radius", Coverage.PARTIAL, _cite(
                    "https://docs.fossa.com/docs/dependency-graph",
                ), note="renders the dependency graph; does not reason over it"),
                Capability("evidence_provenance", Coverage.FULL, _cite(
                    "https://docs.fossa.com/docs/license-compliance",
                ), note="attribution requires it, so it is strong here"),
                Capability("declared_vs_observed", Coverage.NONE, _cite(
                    "https://docs.fossa.com/docs",
                )),
                Capability("offline_operation", Coverage.PARTIAL, _cite(
                    "https://docs.fossa.com/docs/on-premises-installation",
                )),
            ),
        ),
        Rival(
            id="sourcegraph",
            name="Sourcegraph",
            vendor="Sourcegraph, Inc",
            segments=(Segment.CODE_SEARCH,),
            homepage="https://sourcegraph.com/",
            summary=(
                "Code search and large-scale change across every repository an "
                "organisation has. Best-in-class at finding and changing code; "
                "it indexes symbols rather than modelling an architecture."
            ),
            capabilities=(
                Capability("vulnerability_matching", Coverage.PARTIAL, _cite(
                    "https://sourcegraph.com/docs/code_search",
                ), note="you can search for a vulnerable pattern; not a scanner"),
                Capability("licence_compliance", Coverage.NONE, _cite(
                    "https://sourcegraph.com/docs",
                )),
                Capability("secret_detection", Coverage.PARTIAL, _cite(
                    "https://sourcegraph.com/docs/code_search",
                ), note="findable by search, not reported as findings"),
                Capability("dependency_updates", Coverage.PARTIAL, _cite(
                    "https://sourcegraph.com/docs/batch_changes",
                ), note="batch changes can bump versions across repositories"),
                Capability("service_catalogue", Coverage.NONE, _cite(
                    "https://sourcegraph.com/docs",
                )),
                Capability("blast_radius", Coverage.PARTIAL, _cite(
                    "https://sourcegraph.com/docs/code-intelligence",
                ), note=(
                    "precise references answer 'who calls this symbol', which is "
                    "the same question one level down from 'what breaks'"
                )),
                Capability("evidence_provenance", Coverage.FULL, _cite(
                    "https://sourcegraph.com/docs/code_search",
                ), note="every result is a file and a line, by construction"),
                Capability("declared_vs_observed", Coverage.NONE, _cite(
                    "https://sourcegraph.com/docs",
                )),
                Capability("offline_operation", Coverage.FULL, _cite(
                    "https://sourcegraph.com/docs/admin/deploy",
                )),
            ),
        ),
        Rival(
            id="databricks",
            name="Databricks",
            vendor="Databricks, Inc",
            segments=(Segment.DATA_PLATFORM,),
            homepage="https://www.databricks.com/",
            summary=(
                "The reference multi-tenant notebook platform: per-user compute, "
                "governed data access through Unity Catalog, and collaborative "
                "notebooks. In this table as the bar for the *workspace* half of "
                "our product, not as an architecture-intelligence competitor."
            ),
            capabilities=(
                Capability("vulnerability_matching", Coverage.NONE, _cite(
                    "https://docs.databricks.com/en/index.html",
                )),
                Capability("licence_compliance", Coverage.NONE, _cite(
                    "https://docs.databricks.com/en/index.html",
                )),
                Capability("secret_detection", Coverage.NONE, _cite(
                    "https://docs.databricks.com/en/index.html",
                )),
                Capability("dependency_updates", Coverage.NONE, _cite(
                    "https://docs.databricks.com/en/index.html",
                )),
                Capability("service_catalogue", Coverage.PARTIAL, _cite(
                    "https://docs.databricks.com/en/data-governance/unity-catalog/index.html",
                ), note="a data catalogue, not a software one"),
                Capability("blast_radius", Coverage.PARTIAL, _cite(
                    "https://docs.databricks.com/en/data-governance/unity-catalog/data-lineage.html",
                ), note="table-level data lineage — the same idea, other domain"),
                Capability("evidence_provenance", Coverage.PARTIAL, _cite(
                    "https://docs.databricks.com/en/data-governance/unity-catalog/data-lineage.html",
                )),
                Capability("declared_vs_observed", Coverage.NONE, _cite(
                    "https://docs.databricks.com/en/index.html",
                )),
                Capability("offline_operation", Coverage.NONE, _cite(
                    "https://docs.databricks.com/en/index.html",
                ), note="a hosted cloud platform"),
            ),
        ),
    )


def field() -> dict[str, Any]:
    """The whole comparison, as data. What the notebook and the API render."""
    rivals = rival_registry()
    return {
        "recorded": RECORDED,
        "capabilities": [
            {"id": name, "description": description}
            for name, description in CAPABILITIES
        ],
        "rivals": [rival.to_dict() for rival in rivals],
        "verified_share": round(
            sum(rival.verified_share for rival in rivals) / len(rivals), 3
        ),
    }
