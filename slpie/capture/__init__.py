"""Format intelligence and the capture firewall.

Every store is downstream of a capture, and a capture is the one place something
arrives that nobody has vetted. Trusting an extension and routing on that guess is
how the wrong parser produces confident nonsense that then becomes graph facts
nobody can trace back to a decision.

**Identification is by content.** An extension is a ``NAME_HEURISTIC`` at 0.25 —
the same confidence ladder every other conclusion uses — while magic bytes and a
successful structural parse are far stronger. When the two disagree, the
disagreement is a finding rather than a tiebreak.

**Depth is the optimisation.** ``PROBE`` reads 4KB, ``STRUCTURE`` lists a
container, ``MODEL`` builds the canonical :class:`~slpie.capture.document.Document`,
``SEMANTIC`` adds the dialect. A scan identifies ten thousand files and models the
dozen that matter; it must never fully read a 200MB PDF to learn it is not a
lockfile.

**Strategies are dispatched, not duplicated.** A parser for a format is a
*composition* of primitives — XLSX and DOCX are both ``zip_container → xml_tree``
— so one implementation serves six formats and a fix lands once.

**Addressing reaches the cell.** ``inventory.xlsx#Sheet1!B12`` rather than
``inventory.xlsx``, because a file-level citation does not let anybody check the
claim.

**The firewall is an internet firewall, deliberately.** Ordered chains, default
deny, explicit-deny-wins (mirrored from :mod:`slpie.rbac.engine` rather than
reinvented), and four verdicts. ``QUARANTINE`` is the one that earns the design:
an unrecognised format must not be dropped, which loses data the operator believes
was captured, and must not be accepted, which poisons the graph. ``DROP`` versus
``REJECT`` is the genuinely firewall-shaped distinction — where the input is
hostile, the reason for refusing *is* the leak.
"""

from .document import Block, BlockKind, Document, unreadable
from .firewall import (
    MAX_CAPTURE,
    SECRET_NAMES,
    Candidate,
    Chain,
    Decision,
    Hit,
    Rule,
    Verdict,
    default_chain,
    digest_of,
)
from .format import COMPOSITION, EXTENSIONS, MAGIC, Depth, Format, identify
from .locator import (
    Locator,
    Pointer,
    at_body,
    at_cell,
    at_element,
    at_line,
    at_page,
    at_pointer,
    column_index,
    column_name,
    of_file,
    parse_a1,
)
from .parse import parse, readable_formats, strategies_for
from .strategies import STRATEGIES, Strategy, StrategyError

__all__ = [
    "COMPOSITION",
    "EXTENSIONS",
    "MAGIC",
    "MAX_CAPTURE",
    "SECRET_NAMES",
    "STRATEGIES",
    "Block",
    "BlockKind",
    "Candidate",
    "Chain",
    "Decision",
    "Depth",
    "Document",
    "Format",
    "Hit",
    "Locator",
    "Pointer",
    "Rule",
    "Strategy",
    "StrategyError",
    "Verdict",
    "at_body",
    "at_cell",
    "at_element",
    "at_line",
    "at_page",
    "at_pointer",
    "column_index",
    "column_name",
    "default_chain",
    "digest_of",
    "identify",
    "of_file",
    "parse",
    "parse_a1",
    "readable_formats",
    "strategies_for",
    "unreadable",
]
