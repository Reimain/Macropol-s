"""Fixture builders: a synthetic environment for the kernel to discover.

These construct real files in their real formats — a genuine OOXML workbook, a
genuine SQLite database, a genuine PNG — because the probes parse real headers
and a mock would only prove the mock works.
"""

from __future__ import annotations

import json
import sqlite3
import struct
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ORDERS = [
    {"order_id": "1001", "customer": "Ada", "total": "99.50", "placed": "2026-01-14", "priority": "true"},
    {"order_id": "1002", "customer": "Alan", "total": "12.00", "placed": "2026-01-15", "priority": "false"},
    {"order_id": "1003", "customer": "Grace", "total": "441.25", "placed": "2026-02-02", "priority": "true"},
]

CUSTOMERS = [
    {"id": 1, "name": "Ada", "segment": "enterprise", "meta": {"region": "EU", "tier": 1}},
    {"id": 2, "name": "Alan", "segment": "smb", "meta": {"region": "EU"}},
    {"id": 3, "name": "Grace", "segment": "enterprise", "meta": {"region": "US", "tier": 2}},
]


def write_csv(path: Path) -> Path:
    lines = ["order_id,customer,total,placed,priority"]
    lines += [",".join(row[k] for k in ("order_id", "customer", "total", "placed", "priority")) for row in ORDERS]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_json(path: Path) -> Path:
    path.write_text(json.dumps({"customers": CUSTOMERS}, indent=2), encoding="utf-8")
    return path


def write_jsonl(path: Path) -> Path:
    path.write_text("\n".join(json.dumps(row) for row in ORDERS) + "\n", encoding="utf-8")
    return path


def write_sqlite(path: Path) -> Path:
    connection = sqlite3.connect(path)
    with connection:
        connection.execute(
            "CREATE TABLE invoices (id INTEGER PRIMARY KEY, customer TEXT, amount REAL, paid INTEGER)"
        )
        connection.executemany(
            "INSERT INTO invoices (customer, amount, paid) VALUES (?, ?, ?)",
            [("Ada", 99.5, 1), ("Alan", 12.0, 0), ("Grace", 441.25, 1)],
        )
    connection.close()
    return path


def write_xlsx(path: Path, *, sheet: str = "Orders") -> Path:
    """Write a minimal but structurally valid OOXML workbook."""
    header = ["order_id", "customer", "total"]
    rows = [[r["order_id"], r["customer"], float(r["total"])] for r in ORDERS]
    strings: list[str] = []

    def shared(value: str) -> int:
        if value not in strings:
            strings.append(value)
        return strings.index(value)

    def cell(ref: str, value: object) -> str:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f'<c r="{ref}"><v>{value}</v></c>'
        return f'<c r="{ref}" t="s"><v>{shared(str(value))}</v></c>'

    body = []
    for row_index, row in enumerate([header, *rows], start=1):
        cells = "".join(cell(f"{chr(65 + i)}{row_index}", v) for i, v in enumerate(row))
        body.append(f'<row r="{row_index}">{cells}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(body)}</sheetData></worksheet>"
    )
    shared_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(strings)}" '
        f'uniqueCount="{len(strings)}">'
        + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings)
        + "</sst>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{escape(sheet)}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/sharedStrings" Target="sharedStrings.xml"/></Relationships>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.sharedStrings+xml"/></Types>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        archive.writestr("xl/sharedStrings.xml", shared_xml)
    return path


def write_png(path: Path, *, width: int = 640, height: int = 480) -> Path:
    """A valid 1x1-data PNG whose IHDR advertises the requested dimensions."""
    import binascii
    import zlib

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", binascii.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" * 16)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    )
    return path


def write_shell(path: Path) -> Path:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'REGION="eu-west-1"\n'
        "export BATCH_SIZE=500\n"
        "sync_orders() {\n"
        '  curl -s "https://api.example.com/orders?region=$REGION"\n'
        "}\n"
        "sync_orders | jq .\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def build_environment(root: Path) -> Path:
    """Create the full synthetic environment used across the tests."""
    root.mkdir(parents=True, exist_ok=True)
    write_csv(root / "orders.csv")
    write_json(root / "customers.json")
    write_jsonl(root / "events.jsonl")
    write_sqlite(root / "billing.db")
    write_xlsx(root / "quarterly.xlsx")
    write_png(root / "logo.png")
    write_shell(root / "sync.sh")
    (root / "notes.txt").write_text("first line\nsecond line\n", encoding="utf-8")
    return root
