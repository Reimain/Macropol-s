"""Building a `Document` — dispatched on format, composed of strategies.

There is no chain of `if format == ...` here. `COMPOSITION` in `format.py` names
which strategies a format is made of, and this module runs them. Adding a format is
registering strategies; the dispatcher does not change, which is what stops the
seventh format from being the one that breaks the third.

The other half is depth. `parse` respects the `Depth` it is given and does no more
work than that: `PROBE` returns a format and nothing else, `STRUCTURE` lists the
container, and only `MODEL` reads the whole thing. That is what lets a scan
identify ten thousand files and model only the dozen that matter.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from .document import Block, BlockKind, Document, unreadable
from .format import COMPOSITION, Depth, Format, identify
from .locator import of_file
from .strategies import (
    MAX_BLOCKS,
    StrategyError,
    delimited,
    docx_blocks,
    html_blocks,
    json_tree,
    keyvalue,
    pptx_blocks,
    xlsx_blocks,
    xml_blocks,
    xml_tree,
    zip_container,
)


def parse(
    payload: bytes,
    uri: str = "",
    *,
    depth: Depth = Depth.MODEL,
    format: Format | None = None,
) -> tuple[Format, Document]:
    """Identify, then model to the requested depth. Never deeper.

    Returns the format alongside the document because the caller almost always
    needs both — the firewall decides on the format, and the graph is built from
    the document.
    """
    found = format or identify(payload, uri, depth=depth)

    if depth < Depth.STRUCTURE:
        return found, Document(uri=uri, format=found.name)

    if depth < Depth.MODEL:
        return found, _structure(payload, uri, found)

    return found, _model(payload, uri, found)


def _structure(payload: bytes, uri: str, found: Format) -> Document:
    """The container's index, without reading its contents.

    For a zip this is the member list — enough to know an XLSX has four sheets,
    without decompressing any of them.
    """
    if "zip_container" not in found.strategies:
        return Document(uri=uri, format=found.name)

    try:
        entries = zip_container(payload, wanted=[])
    except StrategyError as error:
        return Document(
            uri=uri, format=found.name,
            blocks=(unreadable(uri, str(error)),),
            unreadable=(str(error),),
        )

    members = tuple(
        Block(
            kind=BlockKind.SECTION, text=name, locator=of_file(uri),
            attributes={"size": entry.size},
        )
        for name, entry in sorted(entries.items())
    )
    return Document(uri=uri, format=found.name, blocks=members)


def _model(payload: bytes, uri: str, found: Format) -> Document:
    """The full canonical document. Dispatched on the format's composition."""
    reader = _READERS.get(found.name)
    if reader is None:
        # A format with no reader is not an error and not an empty document: it is
        # a document that says it could not be read, which is what the firewall
        # quarantines on.
        return Document(
            uri=uri, format=found.name,
            blocks=(unreadable(
                uri,
                f"no reader is registered for {found.name}; "
                f"{'ring 1 supplies richer extractors' if found.binary else 'it was captured as text'}",
            ),),
            unreadable=(f"no reader for {found.name}",),
        )

    try:
        blocks = reader(payload, uri)
    except StrategyError as error:
        return Document(
            uri=uri, format=found.name,
            blocks=(unreadable(uri, str(error)),),
            unreadable=(str(error),),
        )
    except Exception as error:  # noqa: BLE001 - a reader may hit anything
        message = f"{type(error).__name__}: {error}"
        return Document(
            uri=uri, format=found.name,
            blocks=(unreadable(uri, message),),
            unreadable=(message,),
        )

    return Document(
        uri=uri, format=found.name, blocks=tuple(blocks),
        unreadable=tuple(
            str(block.attributes.get("reason", "unreadable"))
            for block in blocks if not block.readable
        ),
        truncated=len(blocks) >= MAX_BLOCKS,
    )


def _text(payload: bytes) -> str:
    return payload.decode("utf-8", errors="replace")


#: format → the function that models it. Each is a *composition* of the primitives
#: in `strategies.py`; XLSX and DOCX both being zip+xml is the reuse this design
#: exists for.
_READERS: Mapping[str, Callable[[bytes, str], Sequence[Block]]] = {
    "json": lambda payload, uri: json_tree(_text(payload), uri),
    "jsonl": lambda payload, uri: json_tree(
        "[" + ",".join(
            line for line in _text(payload).splitlines() if line.strip()
        ) + "]",
        uri,
    ),
    "xml": lambda payload, uri: xml_blocks(xml_tree(payload), uri),
    "html": lambda payload, uri: html_blocks(_text(payload), uri),
    "csv": lambda payload, uri: delimited(_text(payload), uri),
    "tsv": lambda payload, uri: delimited(_text(payload), uri),
    "ini": lambda payload, uri: keyvalue(_text(payload), uri),
    "properties": lambda payload, uri: keyvalue(_text(payload), uri),
    "dotenv": lambda payload, uri: keyvalue(_text(payload), uri),
    "toml": lambda payload, uri: keyvalue(_text(payload), uri),
    "xlsx": xlsx_blocks,
    "docx": docx_blocks,
    "pptx": pptx_blocks,
    "pdf": lambda payload, uri: _pdf(payload, uri),
    **{
        name: (lambda payload, uri: _media(payload, uri))
        for name in (
            "mp4", "mov", "m4v", "m4a", "matroska", "avi", "mp3", "wav",
            "flac", "ogg", "aiff",
        )
    },
    "text": lambda payload, uri: _lines(_text(payload), uri),
    "markdown": lambda payload, uri: _lines(_text(payload), uri),
    "yaml": lambda payload, uri: _lines(_text(payload), uri),
    "python": lambda payload, uri: _lines(_text(payload), uri),
}


def _pdf(payload: bytes, uri: str) -> list[Block]:
    from .pdf import extract

    return list(extract(payload, uri))


def _media(payload: bytes, uri: str) -> list[Block]:
    from .media import blocks

    return list(blocks(payload, uri))


def _lines(text: str, uri: str) -> list[Block]:
    """Plain text as addressed lines. The floor every text format falls back to."""
    from .locator import at_line

    return [
        Block(kind=BlockKind.PARAGRAPH, text=line, locator=at_line(uri, number))
        for number, line in enumerate(text.splitlines(), start=1)
        if line.strip()
    ][:MAX_BLOCKS]


def strategies_for(name: str) -> tuple[str, ...]:
    """Which primitives a format is composed of. The dispatch table, readable."""
    return tuple(COMPOSITION.get(name, ()))


def readable_formats() -> tuple[str, ...]:
    """Every format that can be modelled here. The honest coverage list."""
    return tuple(sorted(_READERS))
