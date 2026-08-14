"""Sphinx configuration for the whole codebase.

The docstrings in this repository are the documentation. They are long, they
argue for the decisions they describe, and several of them are the only place a
constraint is written down — so the job here is to publish them faithfully
rather than to write a second, thinner description beside them.

Three decisions that are not defaults, each because the alternative loses
something:

**`default_role = "code"`.** The house style writes `Edge.__post_init__` in
single backticks, 1,872 times across 278 files. reStructuredText's default role
is `:title-reference:`, which would render every one of those in italics as
though it were the name of a book. Setting the role to `code` makes the existing
prose come out as intended without touching a single docstring.

**A Markdown-to-reST bridge for the two constructs that would otherwise break.**
Fenced code blocks appear in 17 files and pipe tables in 3. Rather than rewriting
those docstrings into reST — which would make the source worse to read, and the
source is what people actually read — `_bridge` converts them on the way into
Sphinx. Everything else in the house style is already valid reST: `**bold**`,
`*emphasis*`, bullet lists, and the 36 simple tables that were written as reST
tables to begin with.

**`undoc-members` is on.** A reference that silently omitted the members without
docstrings would answer "does this exist?" with "no" for things that do exist.
An undocumented member is listed with its signature, which is a true and useful
answer; leaving it out is a false one.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# --- project ---------------------------------------------------------------

project = "Macropol-s"
author = "Macropol-s"
copyright = "Macropol-s"  # noqa: A001 - the name Sphinx requires

try:
    from importlib.metadata import version as _version

    release = _version("gratimos")
except Exception:  # pragma: no cover - an editable checkout without metadata
    release = "0.1.0"
version = release

# --- extensions ------------------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx.ext.coverage",
    "sphinx.ext.githubpages",
    "myst_parser",
    "sphinx_copybutton",
]

templates_path = ["_templates"]
exclude_patterns = [
    "_build", "Thumbs.db", ".DS_Store",
    # `make manual` writes `docs/MANUAL.md` from the verb registry. It is
    # generated on demand and not committed, so it is in no toctree — and under
    # `-W` an orphan page is a failed build. Excluding it means running
    # `make manual` cannot break `make docs-strict` for somebody who then has to
    # work out why. The verb reference is reachable as `slpie help` and as the
    # `slpie.compose` pages either way.
    "MANUAL.md",
]
source_suffix = {".rst": "restructuredtext", ".md": "markdown"}

#: Single backticks are the house style for a code identifier. Without this they
#: would render as title references — italic, and wrong 1,872 times over.
default_role = "code"

# --- autodoc ---------------------------------------------------------------

autosummary_generate = True
autosummary_imported_members = False

autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
    # Source order, not alphabetical. These modules are written to be read top to
    # bottom — the type a file is about comes first and the machinery after it —
    # and sorting alphabetically would scatter that.
    "member-order": "bysource",
    # `__init__` is where several classes do their validation, and the docstring
    # explaining a refusal lives there.
    "special-members": "__init__, __post_init__",
    "exclude-members": "__weakref__, __dict__, __module__",
    # Document what a module *defines*, not what it re-exports. Without this,
    # `gratimos/__init__.py`'s `from .contextflow.flow import ContextFlow` gets
    # `ContextFlow` documented twice — once on the package page and once on the
    # module that defines it — and Sphinx has to pick one as the link target.
    # The submodule is the right canonical home; the package page keeps its
    # docstring and its list of submodules, which is what it is for.
    "ignore-module-all": True,
}
autodoc_typehints = "signature"
autodoc_preserve_defaults = True
autodoc_class_signature = "mixed"

#: Ring 1 (`slpie_enterprise/`) imports Kubernetes, boto3 and PyYAML, and the
#: optional Gratimos extras reach openpyxl, SQLAlchemy, Pillow and deltalake.
#: Mocking them means the documentation builds from a kernel-only install — the
#: same install invariant 4's CI job asserts is possible — instead of the docs
#: quietly becoming the one thing that needs the extras.
autodoc_mock_imports = [
    "kubernetes", "boto3", "botocore", "yaml",
    "openpyxl", "sqlalchemy", "alembic", "deltalake", "PIL",
    "nbformat", "nbclient",
]

# No `sphinx.ext.napoleon`, and its absence is deliberate. Nothing in this
# repository uses a Google or NumPy section — not one `Args:`, `Returns:` or
# `Attributes:` across 354 modules — so it had nothing to convert. What it did
# instead was actively wrong: Napoleon reads an attribute or property docstring
# as `type: description` and splits it on the **first colon**, so
# `SourceLocation.reference`'s ``"`file.py:42` — the form a human wants"`` was
# turned into a type of ``` `file.py ``` and a description of ``` 42` — ... ```,
# leaving an unclosed backtick and a mangled page. Two docstrings hit it; any
# docstring whose first line contains a colon inside inline markup would.

# --- MyST ------------------------------------------------------------------

# No `linkify`: it needs `linkify-it-py` at build time and only auto-links bare
# URLs written without markup, which is not worth another package in an extra
# whose point is that it stays small.
myst_enable_extensions = ["colon_fence", "deflist", "substitution"]
myst_heading_anchors = 3

# --- links out -------------------------------------------------------------

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}
#: Off, unconditionally. A docs build that reaches the network is a docs build
#: that fails when the network does, and nothing else in this repository needs
#: it. `SPHINX_INTERSPHINX=1` is still read so a release build can opt in, but it
#: no longer changes anything else — see the note under `suppress_warnings`.
OFFLINE = os.environ.get("SPHINX_INTERSPHINX") != "1"
if OFFLINE:
    intersphinx_mapping = {}

#: Suppressed **always**, and the reason is a fact about this codebase rather
#: than about the build environment.
#:
#: Type annotations mention `bytes`, `object` and `type`. Sphinx's Python domain
#: resolves an unqualified name by searching every documented object, and this
#: repository documents `BlockRef.bytes`, `SpilledSequence.bytes`,
#: `Dataset.bytes`, `Fact.object`, `Pattern.object` and several more — so the
#: search finds many candidates and reports the ambiguity, 181 times.
#:
#: An earlier version of this file suppressed the category only when intersphinx
#: was off, on the theory that loading CPython's inventory would make `bytes`
#: resolve to the builtin instead. **That theory was wrong**, and it was wrong in
#: the worst possible way: it was never executed. Every local build ran with the
#: suppression on, CI ran with it off, and the first thing to exercise that
#: branch was a deploy — which failed on 181 warnings under `-W`.
#:
#: The ambiguity is not a defect the build should refuse to proceed past. It is
#: what happens when a project names an attribute `bytes`, which is a reasonable
#: thing to name a byte count. Suppressing it in one place, always, means the
#: configuration a contributor runs is the configuration CI runs.
suppress_warnings = ["ref.python"]

# --- html ------------------------------------------------------------------

html_theme = "furo"
html_title = f"Macropol-s {release}"
html_static_path = []
html_theme_options = {
    "source_repository": "https://github.com/Reimain/Macropol-s/",
    "source_branch": "main",
    "source_directory": "docs/",
}

# --- the Markdown bridge ---------------------------------------------------

_FENCE = re.compile(
    r"^(?P<indent>[ \t]*)```(?P<lang>[A-Za-z0-9_+-]*)[ \t]*\n"
    r"(?P<body>.*?)"
    r"^(?P=indent)```[ \t]*$",
    re.M | re.S,
)


def _fences(text: str) -> str:
    """Turn a Markdown fenced block into a reST `code-block` directive.

    reST renders an unrecognised fence as three literal backticks followed by
    the code as a blockquote, which is legible but wrong, and loses the
    highlighting. The languages actually used here are bash, python, sql, json
    and yaml; an unlabelled fence becomes `text`, which never fails to lex —
    `default` would try to guess and emit a warning per block when it could not.
    """

    def replace(match: re.Match[str]) -> str:
        indent = match.group("indent")
        language = match.group("lang") or "text"
        body = match.group("body").rstrip("\n")
        shifted = "\n".join(
            (indent + "   " + line[len(indent):]) if line.strip() else ""
            for line in body.split("\n")
        )
        return f"{indent}.. code-block:: {language}\n\n{shifted}\n"

    return _FENCE.sub(replace, text)


_ROW = re.compile(r"^[ \t]*\|.*\|[ \t]*$")
_DIVIDER = re.compile(r"^[ \t]*\|[\s:|-]+\|[ \t]*$")


def _tables(lines: list[str]) -> list[str]:
    """Turn a Markdown pipe table into a reST `list-table`.

    `list-table` rather than a grid table because it needs no column-width
    arithmetic, and getting that arithmetic wrong produces a malformed-table
    error rather than a slightly narrow column.
    """
    out: list[str] = []
    index = 0
    while index < len(lines):
        if not _ROW.match(lines[index]):
            out.append(lines[index])
            index += 1
            continue

        start = index
        block: list[str] = []
        while index < len(lines) and _ROW.match(lines[index]):
            block.append(lines[index])
            index += 1

        if len(block) < 2 or not _DIVIDER.match(block[1]):
            out.extend(block)                 # a stray pipe, not a table
            continue

        indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
        rows = [
            [cell.strip() for cell in row.strip().strip("|").split("|")]
            for position, row in enumerate(block) if position != 1
        ]
        width = max(len(row) for row in rows)

        out.append(f"{indent}.. list-table::")
        out.append(f"{indent}   :header-rows: 1")
        out.append("")
        for row in rows:
            padded = row + [""] * (width - len(row))
            for position, cell in enumerate(padded):
                marker = "*" if position == 0 else " "
                out.append(f"{indent}   {marker} - {cell}")
        out.append("")

    return out


def _bridge(text: str) -> str:
    return "\n".join(_tables(_fences(text).split("\n")))


def _process_docstring(_app, _what, _name, _obj, _options, lines: list[str]) -> None:
    converted = _bridge("\n".join(lines)).split("\n")
    lines[:] = converted


def setup(app):  # noqa: D103 - the Sphinx entry point
    app.connect("autodoc-process-docstring", _process_docstring)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
