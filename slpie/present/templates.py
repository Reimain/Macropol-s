"""The declared templates. Data, so a new one is an entry rather than a screen.

Six, covering the demands this platform can actually answer. The number is
deliberately small: a template per question would be a menu nobody reads, and
the selection engine exists precisely so that six well-chosen layouts cover the
space by being *chosen well* rather than by being numerous.

Every panel names a component from the browser's dictionary and a measure from
the warehouse registry. Neither is invented here — a template that named a
component this build does not have would render a hole, and a template that
named a measure nobody defined would render a number nobody can explain. Tests
assert both directions.
"""

from __future__ import annotations

from .template import Panel, Template

#: What a reader sees when the estate is being watched rather than studied.
SECURITY_BOARD = Template(
    key="security-board",
    title="Security",
    doc="What is wrong now, worst first, and how much of the estate it reaches.",
    utility="monitor", context="dashboard", domain="security",
    also=("quality",),
    panels=(
        Panel(component="stat", title="Blocking a release",
              star="findings", measures=("blocking",)),
        Panel(component="stat", title="Open findings",
              star="findings", measures=("findings",)),
        Panel(component="stat", title="Things affected",
              star="findings", measures=("subjects_affected",)),
        Panel(component="bars", title="By severity",
              star="findings", by="severity", measures=("findings",),
              # Sorted by the dimension's own rank, never alphabetically —
              # which would put critical between medium and high.
              options={"order": "rank", "descending": True}),
        Panel(component="table", title="Worst first", star="findings",
              options={"sort": "severity", "limit": 25}),
    ),
)

DEPENDENCY_REPORT = Template(
    key="dependency-report",
    title="Dependencies",
    doc="What the estate depends on, and how much of that was read rather than "
        "inferred. Paginated for a document rather than a glance.",
    utility="report", context="document", domain="dependencies",
    panels=(
        Panel(component="stat", title="Relationships",
              star="relationships", measures=("relationships",)),
        Panel(component="stat", title="Inferred rather than read",
              star="relationships", measures=("inferred_share",)),
        Panel(component="bars", title="By evidence",
              star="relationships", by="evidence_kind",
              measures=("relationships",)),
        Panel(component="table", title="Weakest relationships first",
              star="relationships",
              options={"sort": "confidence", "limit": 50}),
    ),
)

ARCHITECTURE_MAP = Template(
    key="architecture-map",
    title="Architecture",
    doc="The shape of the estate: what exists, where it lives, what connects.",
    utility="explore", context="console", domain="architecture",
    panels=(
        # No diagram panel, and the absence is deliberate. Drawing the estate
        # needs a node-and-edge payload; a star schema hands out rows. The graph
        # screen draws it, this template counts it, and a panel claiming to draw
        # a picture out of aggregates would be a promise the data cannot keep.
        Panel(component="stat", title="Things", star="elements",
              measures=("elements",)),
        Panel(component="stat", title="Relationships",
              star="relationships", measures=("relationships",)),
        Panel(component="bars", title="By kind", star="elements",
              by="kind", measures=("elements",)),
        Panel(component="bars", title="By environment", star="elements",
              by="environment", measures=("elements",)),
        Panel(component="grid", title="What connects what",
              star="relationships", options={"limit": 50}),
    ),
)

RECONCILIATION = Template(
    key="reconciliation",
    title="Declared against observed",
    doc="The two deltas: what was declared and never seen, and what was seen "
        "and never declared. The question a manifest exists to make askable.",
    utility="investigate", context="console", domain="architecture",
    also=("dependencies",),
    panels=(
        Panel(component="stat", title="Declared, never observed",
              star="elements", measures=("unobserved",)),
        Panel(component="stat", title="Observed, never declared",
              star="elements", measures=("undeclared",)),
        Panel(component="table", title="Every element", star="elements",
              options={"sort": "confidence", "limit": 100}),
    ),
)

OPERATIONS = Template(
    key="operations",
    title="Running",
    doc="What the platform itself is doing: queues, workers and what is stuck.",
    utility="monitor", context="dashboard", domain="operations",
    also=("cost",),
    panels=(
        Panel(component="stat", title="Elements tracked",
              star="elements", measures=("elements",)),
        # `metrics` rather than a status pill: the queue board is depth, workers
        # and health together, and a reader watching a queue needs all three at
        # once. A pill would show the health and hide the number behind it.
        Panel(component="metrics", title="Queue", star="",
              options={"source": "queue"}),
        Panel(component="table", title="Jobs", star="",
              options={"source": "queue", "limit": 25}),
    ),
)

#: The one a machine gets. No chrome, no ordering opinion, every measure the
#: star defines — because an API consumer wants the data and will decide for
#: itself, and a layout tuned for a person is noise on a wire.
EXTRACT = Template(
    key="extract",
    title="Extract",
    doc="The data, with its schema and its measures, and nothing arranged.",
    # No `also`, deliberately. An earlier version listed every domain, which
    # made this a universal second-best: it tied with the specialist template
    # on *every* subject and won on name order. A template that claims to serve
    # everything serves nothing in particular, and the tie it produced was the
    # engine correctly reporting that the claim was meaningless.
    #
    # It wins on `context: api` instead, which is the only thing that is
    # actually true of it — a machine consumer wants the data whatever the
    # subject is.
    utility="report", context="api", domain="dependencies",
    panels=(
        Panel(component="table", title="Rows", star="relationships"),
    ),
)

TEMPLATES: tuple[Template, ...] = (
    ARCHITECTURE_MAP, DEPENDENCY_REPORT, EXTRACT, OPERATIONS,
    RECONCILIATION, SECURITY_BOARD,
)


def template(key: str) -> Template | None:
    return next((item for item in TEMPLATES if item.key == key), None)


def keys() -> tuple[str, ...]:
    return tuple(item.key for item in TEMPLATES)
