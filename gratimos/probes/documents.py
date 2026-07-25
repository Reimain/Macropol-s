"""Document probes: JSON, JSON Lines, YAML, and plain text.

JSON is where "unstructured or structured" stops being a clean binary. A single
object, an array of objects, and an object whose values are arrays are three
different shapes that all arrive as ``.json``. The probe normalizes them by
finding the *record-bearing* part of the document rather than forcing the caller
to know which layout they got.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from ..ids import slug
from .base import BaseProbe, Capture, Target


class JsonProbe(BaseProbe):
    """JSON documents and JSON Lines streams."""

    name = "json"
    suffixes = (".json", ".jsonl", ".ndjson", ".geojson")

    def sniff(self, target: Target) -> float:
        if target.suffix in self.suffixes:
            return 0.95
        head = target.text_head.lstrip()
        if head[:1] in ("{", "["):
            try:
                json.loads(target.text_head)
                return 0.8
            except json.JSONDecodeError:
                return 0.5  # plausibly JSON, merely truncated by the peek
        return 0.0

    def capture(self, target: Target, *, limit: int = 0) -> Capture:
        capture = self._capture(target)
        text = self._read_text(target)
        capture.bytes_read = len(text.encode("utf-8"))
        if not text.strip():
            capture.warnings.append("file is empty")
            return capture

        if target.suffix in (".jsonl", ".ndjson"):
            records, bad = self._read_lines(text, self._limit(limit))
            if bad:
                capture.warnings.append(f"{bad} malformed line(s) skipped")
            if records:
                capture.payloads.append(self._wrap(records, target, layout="jsonl"))
            return capture

        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            records, bad = self._read_lines(text, self._limit(limit))
            if records:
                capture.warnings.append(f"parsed as JSON Lines after object parse failed: {exc}")
                capture.payloads.append(self._wrap(records, target, layout="jsonl"))
            else:
                capture.warnings.append(f"invalid JSON: {exc}")
            return capture

        for name, payload, layout in self._split(document, target.entity):
            capture.payloads.append(self._wrap(payload, target, name=name, layout=layout))
        return capture

    @staticmethod
    def _read_lines(text: str, limit: int) -> tuple[list[Any], int]:
        records: list[Any] = []
        bad = 0
        for line in text.splitlines():
            if len(records) >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
        return records, bad

    def _split(self, document: Any, entity: str) -> list[tuple[str, Any, str]]:
        """Find the record-bearing parts of a document.

        A top-level object whose values are all lists of objects is an envelope
        holding several entities (the shape most APIs return), so each key
        becomes its own payload. Anything else is taken at face value.
        """
        if isinstance(document, list):
            return [(entity, document, "array")]
        if isinstance(document, Mapping):
            collections = {
                key: value
                for key, value in document.items()
                if isinstance(value, list) and value and all(isinstance(v, Mapping) for v in value)
            }
            if collections and len(collections) >= 1 and len(collections) == sum(
                1 for v in document.values() if isinstance(v, list)
            ):
                return [(f"{entity}_{slug(key)}", value, "envelope") for key, value in collections.items()]
            return [(entity, document, "object")]
        return [(entity, document, "scalar")]


class YamlProbe(BaseProbe):
    """YAML configuration and data files, when PyYAML is available."""

    name = "yaml"
    suffixes = (".yaml", ".yml")

    def sniff(self, target: Target) -> float:
        return 0.9 if target.suffix in self.suffixes else 0.0

    def capture(self, target: Target, *, limit: int = 0) -> Capture:
        capture = self._capture(target)
        text = self._read_text(target)
        capture.bytes_read = len(text.encode("utf-8"))
        try:
            import yaml  # noqa: PLC0415 - optional
        except ImportError:
            capture.warnings.append("PyYAML not installed; captured as text")
            capture.payloads.append(self._wrap({"text": text}, target, layout="text"))
            return capture
        try:
            documents = [d for d in yaml.safe_load_all(text) if d is not None]
        except Exception as exc:  # noqa: BLE001 - yaml raises a family of errors
            capture.warnings.append(f"invalid YAML: {exc}")
            return capture
        if not documents:
            capture.warnings.append("no YAML documents found")
            return capture
        payload = documents[0] if len(documents) == 1 else documents
        capture.payloads.append(self._wrap(payload, target, layout="yaml"))
        return capture


class TextProbe(BaseProbe):
    """Last-resort probe: any decodable text becomes a line-oriented payload."""

    name = "text"
    suffixes = (".txt", ".md", ".log", ".rst", ".ini", ".cfg", ".conf")
    default_limit = 2000

    def sniff(self, target: Target) -> float:
        if target.suffix in self.suffixes:
            return 0.5
        if not target.head:
            return 0.0
        # Printable-ratio test: binary content has NULs and few printable bytes.
        if b"\x00" in target.head:
            return 0.0
        printable = sum(1 for b in target.head if 9 <= b <= 13 or 32 <= b <= 126 or b >= 128)
        return 0.2 if printable / max(len(target.head), 1) > 0.85 else 0.0

    def capture(self, target: Target, *, limit: int = 0) -> Capture:
        capture = self._capture(target)
        text = self._read_text(target)
        capture.bytes_read = len(text.encode("utf-8"))
        lines = text.splitlines()[: self._limit(limit)]
        records = [{"line_no": index, "line": line} for index, line in enumerate(lines, start=1)]
        if not records:
            capture.warnings.append("file is empty")
            return capture
        capture.payloads.append(self._wrap(records, target, layout="lines"))
        return capture
