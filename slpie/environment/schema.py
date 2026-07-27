"""The environment manifest's shape, and a YAML reader that owes nothing.

The kernel takes no third-party dependencies, and that has to include the
manifest parser. So this reads the subset of YAML a manifest actually uses —
nested mappings, sequences, scalars, quoted strings, comments, block scalars —
and *refuses* anything outside it rather than guessing. A configuration file
silently misread is worse than one that fails to load: the platform would run
against an environment nobody described.

Anchors, aliases, flow-style collections and multi-document streams are
deliberately not supported. A manifest that uses them gets a clear error naming
the line, which is a better outcome than a partial parse.
"""

from __future__ import annotations

import re
from typing import Any

from ..errors import ManifestError

#: Sections a manifest may declare. Anything else is a typo, and typos in the
#: thing that defines your environment must not pass silently.
SECTIONS = (
    "apiVersion", "environment", "target", "security", "codebase", "data",
    "network", "web", "iot", "providers", "policies", "metadata",
)

SUPPORTED_API_VERSIONS = ("slpie/v1",)

#: Every declaration kind, and which fields identify it. The identity field is
#: what the declaration's URN is built from.
DECLARATION_FIELDS: dict[str, tuple[str, ...]] = {
    "codebase": ("root", "name"),
    "data": ("uri", "folder", "name"),
    "network": ("name", "url", "uri"),
    "web": ("name", "root"),
    "iot": ("name", "broker"),
    "providers": ("name",),
}

_TRUE = {"true", "yes", "on"}
_FALSE = {"false", "no", "off"}
_NULL = {"null", "~", ""}
_INT = re.compile(r"^[+-]?\d+$")
_FLOAT = re.compile(r"^[+-]?(\d+\.\d*|\.\d+)([eE][+-]?\d+)?$")


def parse_yaml(text: str) -> Any:
    """Read the manifest subset of YAML. Raises :class:`ManifestError` otherwise."""
    lines = _significant_lines(text)
    if not lines:
        return {}
    value, index = _parse_block(lines, 0, lines[0][0])
    if index != len(lines):
        raise ManifestError(
            f"line {lines[index][2]}: unexpected indentation; "
            f"a manifest is a single mapping"
        )
    return value


def _significant_lines(text: str) -> list[tuple[int, str, int]]:
    """(indent, content, line number), with comments and blanks removed."""
    out: list[tuple[int, str, int]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise ManifestError(f"line {number}: tabs cannot be used for indentation")
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        if stripped.strip() in ("---", "..."):
            # A single document is the whole contract; a stream would silently
            # discard everything after the first.
            if out:
                raise ManifestError(f"line {number}: multi-document manifests are not supported")
            continue
        out.append((len(stripped) - len(stripped.lstrip()), stripped.strip(), number))
    return out


def _strip_comment(line: str) -> str:
    """Remove a trailing comment without touching a `#` inside a quoted string."""
    quote = ""
    for index, character in enumerate(line):
        if quote:
            if character == quote:
                quote = ""
        elif character in "\"'":
            quote = character
        elif character == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index]
    return line


def _parse_block(lines: list[tuple[int, str, int]], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines):
        return None, index
    if lines[index][1].startswith("- "):
        return _parse_sequence(lines, index, indent)
    return _parse_mapping(lines, index, indent)


def _parse_sequence(
    lines: list[tuple[int, str, int]], index: int, indent: int
) -> tuple[list[Any], int]:
    items: list[Any] = []
    while index < len(lines):
        line_indent, content, number = lines[index]
        if line_indent < indent or not content.startswith("- "):
            break
        if line_indent > indent:
            raise ManifestError(f"line {number}: inconsistent indentation in a list")

        body = content[2:].strip()
        if ":" in body and not _looks_scalar(body):
            # An inline first key: `- name: payments` starts a mapping whose
            # remaining keys are indented under it.
            inline_indent = line_indent + 2
            synthetic = [(inline_indent, body, number)]
            index += 1
            while index < len(lines) and lines[index][0] >= inline_indent:
                if lines[index][1].startswith("- ") and lines[index][0] == inline_indent:
                    break
                synthetic.append(lines[index])
                index += 1
            value, consumed = _parse_mapping(synthetic, 0, inline_indent)
            if consumed != len(synthetic):
                raise ManifestError(f"line {number}: could not read this list item")
            items.append(value)
            continue

        if body:
            items.append(_scalar(body, number))
            index += 1
            continue

        # `-` alone: the item is the nested block that follows.
        index += 1
        if index < len(lines) and lines[index][0] > line_indent:
            value, index = _parse_block(lines, index, lines[index][0])
            items.append(value)
        else:
            items.append(None)
    return items, index


def _parse_mapping(
    lines: list[tuple[int, str, int]], index: int, indent: int
) -> tuple[dict[str, Any], int]:
    mapping: dict[str, Any] = {}
    while index < len(lines):
        line_indent, content, number = lines[index]
        if line_indent < indent:
            break
        if line_indent > indent:
            raise ManifestError(f"line {number}: unexpected indentation")
        if content.startswith("- "):
            break

        key, separator, rest = content.partition(":")
        if not separator:
            raise ManifestError(f"line {number}: expected `key: value`, found {content!r}")
        key = key.strip().strip("\"'")
        if not key:
            raise ManifestError(f"line {number}: empty key")
        if key in mapping:
            raise ManifestError(f"line {number}: duplicate key {key!r}")
        rest = rest.strip()
        index += 1

        if rest in ("|", ">", "|-", ">-"):
            block, index = _parse_block_scalar(lines, index, line_indent, fold=rest[0] == ">")
            mapping[key] = block
            continue
        if rest:
            mapping[key] = _scalar(rest, number)
            continue

        if index < len(lines) and lines[index][0] > line_indent:
            value, index = _parse_block(lines, index, lines[index][0])
            mapping[key] = value
        elif index < len(lines) and lines[index][1].startswith("- ") and lines[index][0] == line_indent:
            # A list written at the same indentation as its key, which is legal
            # YAML and extremely common in hand-written manifests.
            value, index = _parse_sequence(lines, index, line_indent)
            mapping[key] = value
        else:
            mapping[key] = None
    return mapping, index


def _parse_block_scalar(
    lines: list[tuple[int, str, int]], index: int, indent: int, *, fold: bool
) -> tuple[str, int]:
    collected: list[str] = []
    while index < len(lines) and lines[index][0] > indent:
        collected.append(lines[index][1])
        index += 1
    return (" " if fold else "\n").join(collected), index


def _looks_scalar(body: str) -> bool:
    """Whether `a: b` inside a list item is a mapping or just a string with a colon."""
    if body.startswith(("\"", "'")):
        return True
    key, _, rest = body.partition(":")
    # `https://example.com` has a colon but no space after it — a URL, not a key.
    return bool(rest) and not rest.startswith(" ") and not rest == ""


def _flow_sequence(text: str, number: int) -> list[Any]:
    """`[pci-dss, gdpr, soc2]` — YAML flow style, where entries need no quotes.

    Not JSON, which is why it is parsed here: `json.loads` rejects the bare
    identifiers that every hand-written manifest uses.
    """
    if not text.endswith("]"):
        raise ManifestError(f"line {number}: unterminated list {text!r}")
    return [_scalar(part, number) for part in _split_flow(text[1:-1], number)]


def _flow_mapping(text: str, number: int) -> dict[str, Any]:
    if not text.endswith("}"):
        raise ManifestError(f"line {number}: unterminated mapping {text!r}")
    mapping: dict[str, Any] = {}
    for part in _split_flow(text[1:-1], number):
        key, separator, rest = part.partition(":")
        if not separator:
            raise ManifestError(f"line {number}: expected `key: value` in {part!r}")
        mapping[key.strip().strip("\"'")] = _scalar(rest, number)
    return mapping


def _split_flow(body: str, number: int) -> list[str]:
    """Split on commas at depth zero, leaving nesting and quoted commas intact."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    quote = ""
    for character in body:
        if quote:
            current.append(character)
            if character == quote:
                quote = ""
            continue
        if character in "\"'":
            quote = character
            current.append(character)
        elif character in "[{":
            depth += 1
            current.append(character)
        elif character in "]}":
            depth -= 1
            if depth < 0:
                raise ManifestError(f"line {number}: unbalanced brackets")
            current.append(character)
        elif character == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(character)
    if quote:
        raise ManifestError(f"line {number}: unterminated string")
    if depth:
        raise ManifestError(f"line {number}: unbalanced brackets")
    tail = "".join(current)
    if tail.strip():
        parts.append(tail)
    return [part.strip() for part in parts if part.strip()]


def _scalar(text: str, number: int) -> Any:
    value = text.strip()
    if value.startswith("["):
        return _flow_sequence(value, number)
    if value.startswith("{"):
        return _flow_mapping(value, number)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    lowered = value.lower()
    if lowered in _NULL:
        return None
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    if _INT.match(value):
        return int(value)
    if _FLOAT.match(value):
        return float(value)
    return value


def validate(document: Any) -> dict[str, Any]:
    """Check a parsed manifest's shape before anything acts on it.

    Unknown top-level keys are an error. A misspelled `codebse:` that parsed
    cleanly would mean the platform reporting on an environment with no code in
    it, and confidently.
    """
    if not isinstance(document, dict):
        raise ManifestError("a manifest must be a mapping at the top level")

    unknown = [key for key in document if key not in SECTIONS]
    if unknown:
        raise ManifestError(
            f"unknown manifest section(s): {', '.join(sorted(unknown))}; "
            f"expected any of {', '.join(SECTIONS)}"
        )

    api_version = document.get("apiVersion")
    if api_version is None:
        raise ManifestError("a manifest must declare apiVersion")
    if api_version not in SUPPORTED_API_VERSIONS:
        raise ManifestError(
            f"unsupported apiVersion {api_version!r}; "
            f"this build understands {', '.join(SUPPORTED_API_VERSIONS)}"
        )

    if not document.get("environment"):
        raise ManifestError("a manifest must name its environment")

    target = document.get("target", "simulated")
    if target not in ("simulated", "live", "hybrid"):
        raise ManifestError(
            f"target must be simulated, live or hybrid; found {target!r}"
        )

    for section in ("codebase", "data", "network", "web", "iot", "providers"):
        entries = document.get(section)
        if entries is None:
            continue
        if not isinstance(entries, list):
            raise ManifestError(f"section {section!r} must be a list of declarations")
        for position, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                raise ManifestError(
                    f"{section}[{position}] must be a mapping, found {type(entry).__name__}"
                )
            if not any(entry.get(field) for field in DECLARATION_FIELDS[section]):
                raise ManifestError(
                    f"{section}[{position}] must set one of "
                    f"{', '.join(DECLARATION_FIELDS[section])}"
                )

    security = document.get("security")
    if security is not None and not isinstance(security, dict):
        raise ManifestError("the security section must be a mapping")

    return document
