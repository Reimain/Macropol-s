"""The three stars this platform publishes, declared rather than derived.

Three, and the count is the design. Every question an operator has about an
estate is about one of these grains:

    fact_element   one element, as the platform currently sees it
    fact_edge      one relationship, with the evidence behind it
    fact_finding   one open finding

A fourth star for every screen would be a warehouse that grew a table per
question, which is how a warehouse becomes a pile. These three answer the
questions by *grouping*, which is what dimensions are for.

── Provenance is a column, not an afterthought ──────────────────────────

`fact_edge` carries `confidence`, `evidence_kind` and `read`, and `fact_element`
carries `declared` and `observed`. Those are not decoration: a count of
relationships means something different depending on whether they were pinned in
a lockfile or guessed from a name, and a warehouse that dropped the distinction
would be the one place in this platform where a number arrives unqualified.
"""

from __future__ import annotations

from .model import Column, Dimension, Fact, Star

#: Dimensions are shared across stars — one `dim_node` rather than a copy per
#: fact — because the whole reason to model this way is that "findings by team"
#: and "edges by team" are the same join.
DIM_NODE = Dimension(
    name="dim_node",
    doc="Every element the graph holds, as the thing facts are grouped by.",
    columns=(
        Column("node_id", "text", "The graph's own id. Never truncated.", key=True),
        Column("name", "text", "What a reader calls it."),
        Column("kind", "text", "package, service, table, deployment, team, …"),
        Column("identity", "text", "The purl or URN — what it is called outside here."),
        Column("lifecycle", "text", "active, deprecated, retired."),
        Column("risk", "text", "The risk class carried on the node."),
        Column("confidence", "real", "How sure the platform is this exists at all."),
        Column("environment", "text", "Where it lives, `unknown` when nothing says."),
        Column("team", "text", "Who owns it, from the manifest. Blank when undeclared."),
    ),
)

DIM_SEVERITY = Dimension(
    name="dim_severity",
    doc="The severity ladder, with its order as a column so a chart can sort "
        "by *seriousness* rather than alphabetically — which would put "
        "critical between medium and high.",
    columns=(
        Column("severity", "text", "info, low, medium, high, critical.", key=True),
        Column("rank", "integer", "0–4. The sort key a chart actually wants."),
        Column("blocks_release", "boolean", "Whether this severity stops a release."),
    ),
    rows=(
        {"severity": "info", "rank": 0, "blocks_release": False},
        {"severity": "low", "rank": 1, "blocks_release": False},
        {"severity": "medium", "rank": 2, "blocks_release": False},
        {"severity": "high", "rank": 3, "blocks_release": True},
        {"severity": "critical", "rank": 4, "blocks_release": True},
    ),
)

DIM_EVIDENCE = Dimension(
    name="dim_evidence",
    doc="The evidence ladder — the kinds a relationship can rest on, with the "
        "base confidence each carries. This is §10's table, as data a BI tool "
        "can join to, so 'how much of this is inference' is a query.",
    columns=(
        Column("evidence_kind", "text", "lockfile_pin, static_import, …", key=True),
        Column("base_confidence", "real", "What one observation of this kind is worth."),
        Column("read", "boolean",
               "True when the platform *read* it, false when it inferred it. "
               "The line between evidence and reasoning, drawn once."),
    ),
)

DIM_TIME = Dimension(
    name="dim_time",
    doc="One row per day the warehouse has facts for. A date dimension rather "
        "than a raw timestamp on the fact, because 'findings this week' is a "
        "join and not a date function every consumer writes differently.",
    columns=(
        Column("day", "text", "ISO date, UTC.", key=True),
        Column("epoch", "timestamp", "Midnight UTC, for range scans."),
        Column("weekday", "integer", "0 Monday … 6 Sunday."),
    ),
)

FACT_ELEMENT = Fact(
    name="fact_element",
    doc="What the platform currently believes exists.",
    grain="one element, as of the snapshot this was built from",
    columns=(
        Column("node_id", "text", "", key=True, dimension="dim_node"),
        Column("kind", "text", "Denormalised for the common group-by."),
        Column("environment", "text", "", dimension="dim_node"),
        Column("declared", "boolean", "Named in the environment manifest."),
        Column("observed", "boolean", "Independent evidence was found for it."),
        Column("confidence", "real", "Derived, never assigned."),
        Column("degree", "integer", "How many relationships touch it."),
        Column("day", "text", "", dimension="dim_time"),
    ),
)

FACT_EDGE = Fact(
    name="fact_edge",
    doc="Every recorded relationship, with what it rests on.",
    grain="one live relationship between two elements",
    columns=(
        Column("edge_id", "text", "The edge's own id — stable across "
               "rebuilds, so a fact row can be followed between them.",
               key=True),
        Column("source", "text", "", dimension="dim_node"),
        Column("target", "text", "", dimension="dim_node"),
        Column("kind", "text", "depends_on, calls, deploys_to, …"),
        Column("evidence_kind", "text", "", dimension="dim_evidence"),
        Column("confidence", "real", "Derived from the evidence and its corroboration."),
        Column("read", "boolean",
               "True when this was read from a file, false when inferred."),
        Column("validation", "text", "unverified, corroborated, contradicted, confirmed."),
        Column("day", "text", "", dimension="dim_time"),
    ),
)

FACT_FINDING = Fact(
    name="fact_finding",
    doc="Open findings. A suppressed finding is absent rather than flagged: a "
        "suppression is a decision with a reason on the record, and counting "
        "it here would make that decision invisible in every total.",
    grain="one open finding against one subject",
    columns=(
        Column("finding_id", "text", "Content-addressed, so the same finding "
               "raised twice is one row rather than two.", key=True),
        Column("subject", "text", "", dimension="dim_node"),
        Column("severity", "text", "", dimension="dim_severity"),
        Column("kind", "text", "The finding family: vulnerable_dependency, …"),
        Column("rule_id", "text", "Which rule fired."),
        Column("blocks_release", "boolean", "Denormalised from the severity."),
        Column("confidence_impact", "real", "How much this limits an answer."),
        Column("day", "text", "", dimension="dim_time"),
    ),
)

#: The published stars. A consumer names one of these; nothing outside this
#: module decides what a star contains.
STARS: tuple[Star, ...] = (
    Star(name="elements", doc="What exists, and how sure the platform is.",
         fact=FACT_ELEMENT, dimensions=(DIM_NODE, DIM_TIME)),
    Star(name="relationships", doc="What connects to what, and on what evidence.",
         fact=FACT_EDGE, dimensions=(DIM_NODE, DIM_EVIDENCE, DIM_TIME)),
    Star(name="findings", doc="What is wrong, ranked.",
         fact=FACT_FINDING, dimensions=(DIM_NODE, DIM_SEVERITY, DIM_TIME)),
)


def star(name: str) -> Star | None:
    return next((item for item in STARS if item.name == name), None)


def names() -> tuple[str, ...]:
    return tuple(item.name for item in STARS)
