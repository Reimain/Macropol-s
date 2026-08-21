"""Addressing *inside* a document, so evidence reaches a cell rather than a file.

A file-level citation is useless in a warehouse. "The version came from
`inventory.xlsx`" does not let anybody check it; `inventory.xlsx#Sheet1!B12` does.
So the addressable unit for structured data is the **cell**, and `Locator` extends
`SourceLocation` with a within-document pointer.

The pointer dialect is the one native to the format rather than a lowest common
denominator. A JSON Pointer for JSON, A1 notation for a spreadsheet, an element
path for XML, a page and span for PDF. Inventing a single universal syntax would
make every one of them slightly wrong and none of them copy-pasteable into the
tool their users already have — and being copy-pasteable is most of the value.

This is not a new provenance mechanism. It is `SourceLocation.reference` rendering
one level deeper, so the whole `derived_from` walk gets cell precision for free.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Sequence

from ..domain.evidence import SourceLocation


class Pointer(str, Enum):
    """Which addressing dialect a locator uses."""

    NONE = "none"
    LINE = "line"                # plain text: a line number
    CELL = "cell"                # sheet + A1, e.g. Sheet1!B12
    JSON_POINTER = "json"        # RFC 6901, e.g. /dependencies/lodash
    ELEMENT = "element"          # XML/HTML path, e.g. /project/dependencies[3]
    PAGE_SPAN = "page"           # PDF: page + character span
    BODY_INDEX = "body"          # DOCX: /body/p[57]

    @property
    def fragment(self) -> str:
        """The URL fragment key this dialect uses, so links are unambiguous."""
        return {
            Pointer.NONE: "", Pointer.LINE: "L", Pointer.CELL: "",
            Pointer.JSON_POINTER: "", Pointer.ELEMENT: "",
            Pointer.PAGE_SPAN: "page", Pointer.BODY_INDEX: "",
        }[self]


#: A1 notation: one or more letters, then digits. `AA10` is a real address.
_A1 = re.compile(r"^([A-Z]{1,3})([1-9][0-9]{0,6})$")


@dataclass(frozen=True, slots=True)
class Locator:
    """Where inside a document something is. Renders as something you can paste."""

    uri: str
    pointer: Pointer = Pointer.NONE
    path: str = ""                    # the pointer itself, in its own dialect
    line: int = 0
    sheet: str = ""
    page: int = 0
    span: tuple[int, int] = (0, 0)

    @property
    def filename(self) -> str:
        return self.uri.rsplit("/", 1)[-1] or self.uri

    @property
    def fragment(self) -> str:
        """The part after `#`. Empty when the file itself is the address."""
        if self.pointer is Pointer.CELL:
            return f"{self.sheet}!{self.path}" if self.sheet else self.path
        if self.pointer is Pointer.PAGE_SPAN:
            start, end = self.span
            base = f"page={self.page}"
            return f"{base}&span={start}-{end}" if end else base
        if self.pointer is Pointer.LINE:
            return f"L{self.line}" if self.line else ""
        return self.path

    @property
    def reference(self) -> str:
        """`inventory.xlsx#Sheet1!B12` — what a reviewer opens to check a claim."""
        fragment = self.fragment
        return f"{self.filename}#{fragment}" if fragment else self.filename

    def to_location(self) -> SourceLocation:
        """The domain type, so a `Locator` slots into `Evidence` unchanged.

        `span` carries the fragment, which is why `SourceLocation` had a `span`
        field waiting for it — cell precision is an extension of the existing
        provenance chain rather than a parallel one.
        """
        return SourceLocation(
            uri=self.uri, line=self.line, span=self.fragment,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri, "pointer": self.pointer.value, "path": self.path,
            "reference": self.reference, "fragment": self.fragment,
            "line": self.line, "sheet": self.sheet, "page": self.page,
        }

    def __str__(self) -> str:
        return self.reference


# --- constructors, one per dialect --------------------------------------


def at_line(uri: str, line: int) -> Locator:
    return Locator(uri=uri, pointer=Pointer.LINE, line=line)


def at_cell(uri: str, sheet: str, column: int, row: int) -> Locator:
    """A spreadsheet cell, in the A1 notation its user already knows."""
    return Locator(
        uri=uri, pointer=Pointer.CELL, sheet=sheet,
        path=f"{column_name(column)}{row}", line=row,
    )


def at_pointer(uri: str, path: Sequence[str] | str) -> Locator:
    """An RFC 6901 JSON Pointer. Escaping matters and is done here, once."""
    if isinstance(path, str):
        rendered = path if path.startswith("/") else f"/{path}"
    else:
        rendered = "".join(f"/{escape_token(str(part))}" for part in path)
    return Locator(uri=uri, pointer=Pointer.JSON_POINTER, path=rendered or "/")


def at_element(uri: str, path: Sequence[str] | str, *, line: int = 0) -> Locator:
    rendered = path if isinstance(path, str) else "/" + "/".join(path)
    return Locator(uri=uri, pointer=Pointer.ELEMENT, path=rendered, line=line)


def at_page(uri: str, page: int, span: tuple[int, int] = (0, 0)) -> Locator:
    return Locator(uri=uri, pointer=Pointer.PAGE_SPAN, page=page, span=span)


def at_body(uri: str, index: int, *, tag: str = "p") -> Locator:
    return Locator(
        uri=uri, pointer=Pointer.BODY_INDEX, path=f"/body/{tag}[{index}]",
    )


def of_file(uri: str) -> Locator:
    return Locator(uri=uri)


# --- A1 notation ---------------------------------------------------------


def column_name(index: int) -> str:
    """1 → A, 26 → Z, 27 → AA. Spreadsheet columns are bijective base-26.

    Not ordinary base-26: there is no zero digit, so the usual divmod loop is off
    by one and produces `A@` for column 27. Subtracting one before each step is
    the correction, and it is the bug every implementation of this makes once.
    """
    if index < 1:
        return ""
    name = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


def column_index(name: str) -> int:
    """A → 1, AA → 27. The inverse, for reading an address back."""
    total = 0
    for character in name.upper():
        if not ("A" <= character <= "Z"):
            return 0
        total = total * 26 + (ord(character) - ord("A") + 1)
    return total


def parse_a1(address: str) -> tuple[int, int]:
    """`B12` → (column 2, row 12). (0, 0) when it is not an address."""
    match = _A1.match(address.strip().upper().replace("$", ""))
    if not match:
        return (0, 0)
    return column_index(match.group(1)), int(match.group(2))


def escape_token(token: str) -> str:
    """RFC 6901 escaping: `~` → `~0`, `/` → `~1`, in that order.

    The order is not arbitrary. Escaping `/` first would turn it into `~1`, and the
    subsequent `~` pass would mangle that into `~01`, so a key containing a slash
    would no longer round-trip.
    """
    return token.replace("~", "~0").replace("/", "~1")


def unescape_token(token: str) -> str:
    """The inverse, in the reverse order for the same reason."""
    return token.replace("~1", "/").replace("~0", "~")
