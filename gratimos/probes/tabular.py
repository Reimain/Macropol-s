"""Tabular probes: delimited text and Excel workbooks.

The XLSX reader is native. An ``.xlsx`` is a zip of XML parts, so parsing it
with :mod:`zipfile` and :mod:`xml.etree` keeps the kernel dependency-free on the
most common "someone dropped a spreadsheet in the folder" path. When
``openpyxl`` *is* installed we use it instead, because it handles the long tail
(number formats, dates, merged regions) far better than 200 lines of XML ever
will.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import re
import zipfile
from typing import Any, Iterator
from xml.etree import ElementTree as ET

from ..ids import slug
from .base import BaseProbe, Capture, Target

_CELL_RE = re.compile(r"^([A-Z]+)(\d+)$")
#: Excel serial dates count from 1899-12-30 (the Lotus leap-year bug is baked in).
_EXCEL_EPOCH = _dt.date(1899, 12, 30)


def column_index(ref: str) -> int:
    """``"A"`` -> 0, ``"AB"`` -> 27."""
    match = _CELL_RE.match(ref)
    letters = match.group(1) if match else ref
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - 64)
    return index - 1


class CsvProbe(BaseProbe):
    """Delimited text. Dialect is sniffed, not assumed."""

    name = "csv"
    suffixes = (".csv", ".tsv", ".psv", ".txt")

    def sniff(self, target: Target) -> float:
        if target.suffix in (".csv", ".tsv", ".psv"):
            return 0.9
        if not target.head:
            return 0.0
        text = target.text_head
        lines = [line for line in text.splitlines()[:10] if line.strip()]
        if len(lines) < 2:
            return 0.0
        for delimiter in (",", "\t", ";", "|"):
            counts = [line.count(delimiter) for line in lines]
            if counts[0] > 0 and len(set(counts)) == 1:
                return 0.7 if target.suffix == ".txt" else 0.6
        return 0.0

    def capture(self, target: Target, *, limit: int = 0) -> Capture:
        capture = self._capture(target)
        text = self._read_text(target)
        capture.bytes_read = len(text.encode("utf-8"))
        if not text.strip():
            capture.warnings.append("file is empty")
            return capture

        dialect, has_header = self._dialect(text, target)
        reader = csv.reader(io.StringIO(text), dialect)
        try:
            rows = list(self._take(reader, self._limit(limit) + (1 if has_header else 0)))
        except csv.Error as exc:
            capture.warnings.append(f"csv parse stopped: {exc}")
            return capture
        if not rows:
            return capture

        header = [str(h).strip() or f"c{i}" for i, h in enumerate(rows[0])] if has_header else [
            f"c{i}" for i in range(len(rows[0]))
        ]
        body = rows[1:] if has_header else rows
        records = [
            {header[i] if i < len(header) else f"c{i}": value for i, value in enumerate(row)}
            for row in body
        ]
        if not has_header:
            capture.warnings.append("no header row detected; columns named positionally")
        capture.payloads.append(self._wrap(records, target, delimiter=dialect.delimiter))
        return capture

    def _dialect(self, text: str, target: Target) -> tuple[Any, bool]:
        sample = text[:16384]
        sniffer = csv.Sniffer()
        try:
            dialect = sniffer.sniff(sample, delimiters=",\t;|")
        except csv.Error:
            dialect = csv.excel
            if target.suffix == ".tsv":
                dialect = csv.excel_tab
        try:
            has_header = sniffer.has_header(sample)
        except csv.Error:
            has_header = True
        return dialect, has_header

    @staticmethod
    def _take(reader: Iterator[list[str]], count: int) -> Iterator[list[str]]:
        for index, row in enumerate(reader):
            if index >= count:
                return
            if any(cell.strip() for cell in row):
                yield row


class XlsxProbe(BaseProbe):
    """Excel workbooks. One payload per sheet, named after the sheet."""

    name = "xlsx"
    suffixes = (".xlsx", ".xlsm", ".xltx")

    def sniff(self, target: Target) -> float:
        if target.suffix in self.suffixes:
            return 0.95
        # An xlsx is a zip whose first entry is usually [Content_Types].xml.
        if target.head.startswith(b"PK\x03\x04") and b"[Content_Types].xml" in target.head[:512]:
            return 0.8
        return 0.0

    def capture(self, target: Target, *, limit: int = 0) -> Capture:
        capture = self._capture(target)
        if target.path is None:
            capture.warnings.append("xlsx probe needs a local path")
            return capture
        capture.bytes_read = target.size

        try:
            sheets = self._read_openpyxl(target, self._limit(limit))
            engine = "openpyxl"
        except ImportError:
            sheets = self._read_native(target, self._limit(limit))
            engine = "native"
        except Exception as exc:  # noqa: BLE001 - fall back rather than fail the scan
            capture.warnings.append(f"openpyxl failed ({exc}); using native reader")
            sheets = self._read_native(target, self._limit(limit))
            engine = "native"

        for sheet_name, rows in sheets.items():
            if not rows:
                continue
            header = [str(h).strip() if h is not None else "" for h in rows[0]]
            header = [h or f"c{i}" for i, h in enumerate(header)]
            records = [
                {header[i] if i < len(header) else f"c{i}": value for i, value in enumerate(row)}
                for row in rows[1:]
            ]
            if not records:
                capture.warnings.append(f"sheet {sheet_name!r} has a header but no rows")
                continue
            entity = f"{target.entity}_{slug(sheet_name)}" if len(sheets) > 1 else target.entity
            capture.payloads.append(self._wrap(records, target, name=entity, sheet=sheet_name, engine=engine))
        if not capture.payloads:
            capture.warnings.append("workbook contained no readable sheets")
        return capture

    # -- engines ---------------------------------------------------------

    def _read_openpyxl(self, target: Target, limit: int) -> dict[str, list[list[Any]]]:
        import openpyxl  # noqa: PLC0415 - optional, probed at call time

        book = openpyxl.load_workbook(target.path, read_only=True, data_only=True)
        try:
            out: dict[str, list[list[Any]]] = {}
            for sheet in book.worksheets:
                rows: list[list[Any]] = []
                for row in sheet.iter_rows(max_row=limit + 1, values_only=True):
                    if any(cell is not None for cell in row):
                        rows.append(list(row))
                out[sheet.title] = rows
            return out
        finally:
            book.close()

    def _read_native(self, target: Target, limit: int) -> dict[str, list[list[Any]]]:
        """Parse the OOXML parts directly — no third-party reader required."""
        with zipfile.ZipFile(target.path) as archive:
            shared = self._shared_strings(archive)
            out: dict[str, list[list[Any]]] = {}
            for name, part in self._sheet_parts(archive).items():
                out[name] = self._sheet_rows(archive, part, shared, limit)
            return out

    @staticmethod
    def _sheet_parts(archive: zipfile.ZipFile) -> dict[str, str]:
        """Map sheet display names to their part paths via workbook rels."""
        rels: dict[str, str] = {}
        try:
            rel_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            for rel in rel_root.findall("{*}Relationship"):
                rels[rel.get("Id", "")] = rel.get("Target", "")
        except KeyError:
            pass

        sheets: dict[str, str] = {}
        try:
            book = ET.fromstring(archive.read("xl/workbook.xml"))
        except KeyError:
            return {"Sheet1": "xl/worksheets/sheet1.xml"}
        for index, sheet in enumerate(book.findall("./{*}sheets/{*}sheet"), start=1):
            title = sheet.get("name") or f"Sheet{index}"
            rid = next((v for k, v in sheet.attrib.items() if k.endswith("}id")), "")
            target_part = rels.get(rid, f"worksheets/sheet{index}.xml")
            part = target_part if target_part.startswith("xl/") else f"xl/{target_part.lstrip('/')}"
            if part in archive.namelist():
                sheets[title] = part
        return sheets or {"Sheet1": "xl/worksheets/sheet1.xml"}

    @staticmethod
    def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
        try:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        except KeyError:
            return []
        out: list[str] = []
        for item in root.findall("{*}si"):
            # A string is either one <t> or a sequence of formatted <r><t> runs.
            out.append("".join(node.text or "" for node in item.iter() if node.tag.endswith("}t")))
        return out

    def _sheet_rows(
        self, archive: zipfile.ZipFile, part: str, shared: list[str], limit: int
    ) -> list[list[Any]]:
        try:
            raw = archive.read(part)
        except KeyError:
            return []
        root = ET.fromstring(raw)
        rows: list[list[Any]] = []
        for row in root.findall("./{*}sheetData/{*}row"):
            if len(rows) > limit:
                break
            cells: dict[int, Any] = {}
            for cell in row.findall("{*}c"):
                ref = cell.get("r", "")
                index = column_index(ref) if ref else len(cells)
                cells[index] = self._cell_value(cell, shared)
            if not cells:
                continue
            width = max(cells) + 1
            values = [cells.get(i) for i in range(width)]
            if any(v is not None and v != "" for v in values):
                rows.append(values)
        return rows

    @staticmethod
    def _cell_value(cell: ET.Element, shared: list[str]) -> Any:
        kind = cell.get("t", "n")
        if kind == "inlineStr":
            node = cell.find("{*}is")
            return "".join(t.text or "" for t in node.iter() if t.tag.endswith("}t")) if node is not None else None
        value_node = cell.find("{*}v")
        if value_node is None or value_node.text is None:
            return None
        raw = value_node.text
        if kind == "s":
            try:
                return shared[int(raw)]
            except (ValueError, IndexError):
                return raw
        if kind == "b":
            return raw not in ("0", "false", "FALSE")
        if kind in ("str", "e"):
            return raw
        try:
            number = float(raw)
        except ValueError:
            return raw
        return int(number) if number.is_integer() else number


def excel_serial_to_date(serial: float) -> _dt.date:
    """Convert an Excel serial number to a date.

    Exposed for transformations: the native reader deliberately returns raw
    numbers, because deciding a column is a date needs the style table, and
    guessing wrong silently corrupts data.
    """
    return _EXCEL_EPOCH + _dt.timedelta(days=int(serial))
