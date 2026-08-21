"""Reusable parsing primitives, dispatched rather than duplicated.

A `Strategy` is one parsing primitive; a parser for a format is a *composition* of
strategies. The reuse is the point rather than a nicety:

    XLSX = zip_container → xml_tree
    DOCX = zip_container → xml_tree      (different semantics at L3)
    PPTX = zip_container → xml_tree
    JAR  = zip_container
    pom  = xml_tree

One `xml_tree` implementation serves six formats. Writing six was the alternative,
and the fifth would have had a different bug from the first — the reason this is
worth the indirection is not elegance, it is that a fix lands once.

Everything here is stdlib. `zipfile`, `xml.etree`, `csv`, `json`, `tomllib`,
`html.parser`, `zlib` cover every format below except PDF's exotic cases, and the
boundary is stated rather than hidden: what the stdlib reader cannot extract
becomes an `UNREADABLE` block with the reason, which the firewall then quarantines.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Mapping, Sequence

from .document import Block, BlockKind, unreadable
from .locator import (
    Locator,
    at_body,
    at_cell,
    at_element,
    at_line,
    at_pointer,
    of_file,
    parse_a1,
)

#: A cap on how much of any one document is modelled. A 200MB spreadsheet is a
#: data warehouse, not a manifest, and modelling all of it would spend more memory
#: than the answer is worth. Truncation is reported, never silent.
MAX_BLOCKS = 20_000

#: Office XML namespaces, which are verbose and unavoidable.
_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}


class StrategyError(Exception):
    """A primitive could not read its input. Callers turn this into a block."""


# --- zip container -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Entry:
    """One member of a zip container."""

    name: str
    size: int
    data: bytes = b""

    @property
    def text(self) -> str:
        return self.data.decode("utf-8", errors="replace")


def zip_container(
    payload: bytes,
    *,
    wanted: Sequence[str] = (),
    max_bytes: int = 64 * 1024 * 1024,
) -> dict[str, Entry]:
    """A zip's members. Serves XLSX, DOCX, PPTX, JAR, whl, nupkg.

    `max_bytes` guards decompression: a zip bomb is a few kilobytes on disk and
    gigabytes expanded, and reading a member without checking its declared size
    first is how a capture takes down the process that was inspecting it.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except (zipfile.BadZipFile, OSError) as error:
        raise StrategyError(f"not a readable zip: {error}") from None

    declared = sum(item.file_size for item in archive.infolist())
    if declared > max_bytes:
        raise StrategyError(
            f"the archive expands to {declared} bytes, beyond the {max_bytes} "
            f"limit; refusing to decompress it"
        )

    found: dict[str, Entry] = {}
    for info in archive.infolist():
        if info.is_dir():
            continue
        if wanted and not any(
            info.filename == name or info.filename.startswith(name)
            for name in wanted
        ):
            found[info.filename] = Entry(info.filename, info.file_size)
            continue
        try:
            data = archive.read(info.filename)
        except (zipfile.BadZipFile, OSError, RuntimeError):
            found[info.filename] = Entry(info.filename, info.file_size)
            continue
        found[info.filename] = Entry(info.filename, info.file_size, data)
    return found


# --- xml tree ------------------------------------------------------------


def xml_tree(payload: bytes | str) -> Any:
    """Parse XML defensively.

    `xml.etree` resolves neither external entities nor DTDs by default in modern
    Python, which is what makes it usable here: an XXE payload in a captured
    `pom.xml` would otherwise read files off the host that the reviewer never
    intended to share.
    """
    import xml.etree.ElementTree as ET

    text = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else payload
    parser = ET.XMLParser()
    try:
        return ET.fromstring(text, parser=parser)
    except ET.ParseError as error:
        raise StrategyError(f"malformed XML: {error}") from None


def xml_blocks(root: Any, uri: str, *, limit: int = MAX_BLOCKS) -> list[Block]:
    """An XML tree as blocks, each addressed by its element path."""
    found: list[Block] = []

    def walk(node: Any, path: list[str], depth: int) -> None:
        if len(found) >= limit:
            return
        tag = _localname(node.tag)
        here = [*path, tag]
        text = (node.text or "").strip()
        if text:
            found.append(Block(
                kind=BlockKind.PARAGRAPH, text=text, level=depth,
                locator=at_element(uri, here),
                attributes=dict(node.attrib) if node.attrib else {},
            ))
        counts: dict[str, int] = {}
        for child in list(node):
            name = _localname(child.tag)
            counts[name] = counts.get(name, 0) + 1
            indexed = [*here[:-1], f"{tag}", f"{name}[{counts[name]}]"]
            walk(child, indexed[:-1], depth + 1)

    walk(root, [], 0)
    return found


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


# --- delimited -----------------------------------------------------------


def delimited(text: str, uri: str, *, limit: int = MAX_BLOCKS) -> list[Block]:
    """CSV/TSV as a table of addressable cells.

    The dialect is sniffed rather than assumed. A European CSV is semicolon
    separated, and reading it as comma-separated yields one column of garbage that
    looks like data — which is worse than failing.
    """
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    rows: list[Block] = []
    try:
        reader = csv.reader(io.StringIO(text), dialect)
    except csv.Error as error:
        return [unreadable(uri, f"unreadable delimited data: {error}")]

    for row_index, values in enumerate(reader, start=1):
        if len(rows) >= limit:
            break
        cells = tuple(
            Block(
                kind=BlockKind.CELL, text=value,
                locator=at_cell(uri, "", column_index, row_index),
                attributes={"row": row_index, "column": column_index},
            )
            for column_index, value in enumerate(values, start=1)
        )
        rows.append(Block(
            kind=BlockKind.ROW, level=1, children=cells,
            locator=at_line(uri, row_index),
        ))

    return [Block(
        kind=BlockKind.TABLE, children=tuple(rows), locator=of_file(uri),
        attributes={"rows": len(rows), "delimiter": getattr(dialect, "delimiter", ",")},
    )]


# --- json tree -----------------------------------------------------------


def json_tree(text: str, uri: str, *, limit: int = MAX_BLOCKS) -> list[Block]:
    """JSON as blocks addressed by RFC 6901 pointer.

    Every leaf gets a pointer, which is what lets a finding cite
    `package.json#/dependencies/lodash` rather than the whole file.
    """
    try:
        body = json.loads(text)
    except ValueError as error:
        raise StrategyError(f"malformed JSON: {error}") from None

    found: list[Block] = []

    def walk(node: Any, path: list[str], depth: int) -> None:
        if len(found) >= limit:
            return
        if isinstance(node, Mapping):
            for key, value in node.items():
                walk(value, [*path, str(key)], depth + 1)
        elif isinstance(node, (list, tuple)):
            for index, value in enumerate(node):
                walk(value, [*path, str(index)], depth + 1)
        else:
            found.append(Block(
                kind=BlockKind.CELL, text="" if node is None else str(node),
                level=depth, locator=at_pointer(uri, path),
                attributes={"type": type(node).__name__},
            ))

    walk(body, [], 0)
    return found


# --- key/value -----------------------------------------------------------

_KEYVALUE = re.compile(r"^\s*([A-Za-z_][\w.\-]*)\s*[:=]\s*(.*?)\s*$")


def keyvalue(text: str, uri: str) -> list[Block]:
    """`.env`, `.properties`, INI. One block per assignment, addressed by line."""
    found: list[Block] = []
    section = ""
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
            found.append(Block(
                kind=BlockKind.SECTION, text=section, locator=at_line(uri, number),
            ))
            continue
        match = _KEYVALUE.match(stripped)
        if match:
            found.append(Block(
                kind=BlockKind.CELL, text=match.group(2),
                locator=at_line(uri, number),
                attributes={"key": match.group(1), "section": section},
            ))
    return found


# --- html ----------------------------------------------------------------


def html_blocks(text: str, uri: str, *, limit: int = MAX_BLOCKS) -> list[Block]:
    """HTML as headings, paragraphs, list items and links."""
    from html.parser import HTMLParser

    headings = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

    class Reader(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.blocks: list[Block] = []
            self.stack: list[str] = []
            self.buffer: list[str] = []
            self.skip = 0

        def handle_starttag(self, tag: str, attrs: Any) -> None:
            if tag in ("script", "style"):
                self.skip += 1
                return
            if tag in headings or tag in ("p", "li", "pre", "code", "a"):
                self._flush()
                self.stack.append(tag)
                if tag == "a":
                    href = dict(attrs).get("href", "")
                    if href:
                        self.blocks.append(Block(
                            kind=BlockKind.LINK, text=href,
                            locator=at_element(uri, ["a"], line=self.getpos()[0]),
                        ))

        def handle_endtag(self, tag: str) -> None:
            if tag in ("script", "style"):
                self.skip = max(0, self.skip - 1)
                return
            if self.stack and self.stack[-1] == tag:
                self._flush()
                self.stack.pop()

        def handle_data(self, data: str) -> None:
            if not self.skip and data.strip():
                self.buffer.append(data)

        def _flush(self) -> None:
            text = " ".join(" ".join(self.buffer).split())
            self.buffer.clear()
            if not text or len(self.blocks) >= limit:
                return
            tag = self.stack[-1] if self.stack else "p"
            kind = (
                BlockKind.HEADING if tag in headings
                else BlockKind.LIST_ITEM if tag == "li"
                else BlockKind.CODE if tag in ("pre", "code")
                else BlockKind.PARAGRAPH
            )
            self.blocks.append(Block(
                kind=kind, text=text, level=headings.get(tag, 0),
                locator=at_element(uri, [tag], line=self.getpos()[0]),
            ))

    reader = Reader()
    try:
        reader.feed(text)
        reader._flush()
    except Exception as error:  # noqa: BLE001 - HTMLParser raises broadly
        reader.blocks.append(unreadable(uri, f"malformed HTML: {error}"))
    return reader.blocks


# --- office ---------------------------------------------------------------


def xlsx_blocks(payload: bytes, uri: str, *, limit: int = MAX_BLOCKS) -> list[Block]:
    """XLSX as sheets of addressed cells. `zip_container` → `xml_tree`.

    An `.xlsx` is a zip of XML, so the two primitives above compose into a
    complete reader with no third-party dependency. Formulae are kept as an
    attribute alongside the cached value: a cell whose formula and cached value
    disagree is a real finding, and discarding one of them makes it invisible.
    """
    entries = zip_container(payload, wanted=["xl/"])
    shared = _shared_strings(entries)

    found: list[Block] = []
    for name, entry in sorted(entries.items()):
        if not name.startswith("xl/worksheets/sheet") or not entry.data:
            continue
        sheet = name.rsplit("/", 1)[-1].removesuffix(".xml")
        try:
            root = xml_tree(entry.data)
        except StrategyError as error:
            found.append(unreadable(uri, f"{sheet}: {error}"))
            continue

        cells: list[Block] = []
        for cell in root.iter():
            if _localname(cell.tag) != "c" or len(cells) >= limit:
                continue
            address = cell.attrib.get("r", "")
            column, row = parse_a1(address)
            if not row:
                continue
            value_node = cell.find("main:v", _NS) if _NS else None
            if value_node is None:
                value_node = next(
                    (item for item in cell if _localname(item.tag) == "v"), None,
                )
            raw = (value_node.text or "") if value_node is not None else ""
            if cell.attrib.get("t") == "s" and raw.isdigit():
                raw = shared.get(int(raw), raw)

            formula = next(
                (item for item in cell if _localname(item.tag) == "f"), None,
            )
            attributes: dict[str, Any] = {"address": address}
            if formula is not None and formula.text:
                attributes["formula"] = formula.text

            cells.append(Block(
                kind=BlockKind.CELL, text=raw,
                locator=at_cell(uri, sheet, column, row),
                attributes=attributes,
            ))

        found.append(Block(
            kind=BlockKind.SHEET, text=sheet, children=tuple(cells),
            locator=of_file(uri), attributes={"cells": len(cells)},
        ))
    return found


def _shared_strings(entries: Mapping[str, Entry]) -> dict[int, str]:
    """XLSX stores repeated text once and refers to it by index."""
    entry = entries.get("xl/sharedStrings.xml")
    if entry is None or not entry.data:
        return {}
    try:
        root = xml_tree(entry.data)
    except StrategyError:
        return {}
    found: dict[int, str] = {}
    for index, item in enumerate(
        node for node in root if _localname(node.tag) == "si"
    ):
        found[index] = "".join(
            (part.text or "") for part in item.iter()
            if _localname(part.tag) == "t"
        )
    return found


def docx_blocks(payload: bytes, uri: str, *, limit: int = MAX_BLOCKS) -> list[Block]:
    """DOCX body text and tables. The same two primitives, different semantics."""
    entries = zip_container(payload, wanted=["word/"])
    entry = entries.get("word/document.xml")
    if entry is None or not entry.data:
        return [unreadable(uri, "no word/document.xml in the archive")]

    try:
        root = xml_tree(entry.data)
    except StrategyError as error:
        return [unreadable(uri, str(error))]

    found: list[Block] = []
    for index, node in enumerate(
        item for item in root.iter() if _localname(item.tag) == "p"
    ):
        if len(found) >= limit:
            break
        text = "".join(
            (run.text or "") for run in node.iter()
            if _localname(run.tag) == "t"
        ).strip()
        if not text:
            continue
        style = next(
            (
                item.attrib.get(f"{{{_NS['w']}}}val", "")
                for item in node.iter()
                if _localname(item.tag) == "pStyle"
            ),
            "",
        )
        heading = style.lower().startswith("heading")
        found.append(Block(
            kind=BlockKind.HEADING if heading else BlockKind.PARAGRAPH,
            text=text,
            level=int(style[-1]) if heading and style[-1:].isdigit() else 0,
            locator=at_body(uri, index + 1),
            attributes={"style": style} if style else {},
        ))
    return found


# --- the registry --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Strategy:
    """One primitive, named so a parser can be described as a composition."""

    name: str
    summary: str
    binary: bool = False

    def __str__(self) -> str:
        return self.name


STRATEGIES: dict[str, Strategy] = {
    "zip_container": Strategy(
        "zip_container", "a zip of members — XLSX, DOCX, PPTX, JAR, whl, nupkg",
        binary=True,
    ),
    "xml_tree": Strategy(
        "xml_tree", "an XML tree — pom.xml, XLSX sheets, DOCX body, .csproj",
    ),
    "json_tree": Strategy("json_tree", "JSON, addressed by RFC 6901 pointer"),
    "delimited": Strategy("delimited", "CSV, TSV — dialect sniffed, not assumed"),
    "keyvalue": Strategy("keyvalue", ".env, .properties, INI"),
    "html_dom": Strategy("html_dom", "HTML and XHTML"),
    "stream_ops": Strategy("stream_ops", "PDF content streams", binary=True),
    "python_ast": Strategy("python_ast", "Python — the one format with a stdlib AST"),
    "media_header": Strategy(
        "media_header",
        "audio and video containers — metadata read, content stated as unread",
        binary=True,
    ),
}


def pptx_blocks(payload: bytes, uri: str, *, limit: int = MAX_BLOCKS) -> list[Block]:
    """PPTX slide text. `zip_container` → `xml_tree` a third time.

    Slides are numbered by filename rather than by document order, so `slide10`
    sorting before `slide2` lexically would present a deck out of order — the
    numeric key is what keeps "slide 7" meaning slide 7.
    """
    entries = zip_container(payload, wanted=["ppt/slides/"])
    slides = sorted(
        (name for name in entries if name.startswith("ppt/slides/slide")
         and name.endswith(".xml")),
        key=lambda name: int(
            "".join(ch for ch in name.rsplit("/", 1)[-1] if ch.isdigit()) or 0
        ),
    )
    if not slides:
        return [unreadable(uri, "no slides in the archive")]

    found: list[Block] = []
    for number, name in enumerate(slides, start=1):
        entry = entries.get(name)
        if entry is None or not entry.data:
            found.append(unreadable(uri, f"slide {number} could not be read"))
            continue
        try:
            root = xml_tree(entry.data)
        except StrategyError as error:
            found.append(unreadable(uri, f"slide {number}: {error}"))
            continue

        runs = [
            (node.text or "").strip()
            for node in root.iter()
            if _localname(node.tag) == "t" and (node.text or "").strip()
        ]
        if not runs:
            continue
        children = tuple(
            Block(
                kind=BlockKind.PARAGRAPH, text=text,
                locator=at_element(uri, [f"slide[{number}]", f"t[{index}]"]),
            )
            for index, text in enumerate(runs[:limit], start=1)
        )
        found.append(Block(
            kind=BlockKind.PAGE, text=runs[0], level=number,
            locator=at_element(uri, [f"slide[{number}]"]),
            children=children, attributes={"slide": number},
        ))
    return found
