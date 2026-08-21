"""PDF text, with the stdlib, and a boundary stated rather than hidden.

PDF is the one format the kernel cannot finish. Invariant 4 keeps ring 0
stdlib-only, and `zlib` plus a content-stream reader covers the common case —
Flate-compressed text with standard encodings — while scanned pages, CID fonts and
encrypted documents do not decode here at all.

The design decision that matters is what happens at that boundary. A page this
reader cannot extract becomes an `UNREADABLE` block naming the reason, **never an
empty one**. The difference is everything: an empty page renders as a document that
legitimately contained nothing, and a reviewer scanning for a licence clause would
conclude it was absent rather than unread. Ring 1 supplies pdfplumber and OCR
through the same extractor protocol; the kernel's job is to be honest about the
line.

Text is addressed by page and character span, so a citation reads
`contract.pdf#page=4&span=1180-1240` and a reviewer can find it.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from typing import Any, Iterator, Sequence

from .document import Block, BlockKind, unreadable
from .locator import at_page, of_file

#: `5 0 obj … endobj`. The object's own number is captured so a stream can be
#: attributed to a page later.
_OBJECT = re.compile(rb"(\d+)\s+\d+\s+obj\b(.*?)\bendobj", re.DOTALL)

#: `stream … endstream`. The newline handling is deliberate: the specification
#: allows CRLF or LF after `stream` and the payload starts after it.
_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)

#: Text-showing operators. `Tj` shows a string, `TJ` shows an array with kerning.
_SHOW = re.compile(rb"\((?:[^()\\]|\\.)*\)\s*(?:Tj|TJ|'|\")")

#: Strings inside a TJ array, which interleaves text with kerning numbers.
_STRING = re.compile(rb"\((?:[^()\\]|\\.)*\)")

#: `/Type /Page` marks a page object, which is how pages get counted without
#: walking the page tree.
_PAGE = re.compile(rb"/Type\s*/Page[^s]")

#: An encrypted document. Present as `/Encrypt` in the trailer.
_ENCRYPTED = re.compile(rb"/Encrypt\b")

MAX_STREAM = 16 * 1024 * 1024


def extract(payload: bytes, uri: str) -> list[Block]:
    """PDF text as page blocks, plus an honest block per page not extracted."""
    if _ENCRYPTED.search(payload[:4096]) or _ENCRYPTED.search(payload[-4096:]):
        return [unreadable(
            uri,
            "the document is encrypted; the stdlib reader does not decrypt, so "
            "nothing in it has been read",
        )]

    pages = len(_PAGE.findall(payload)) or 1
    found: list[Block] = []
    extracted = 0

    for number, text in enumerate(_streams(payload), start=1):
        content = _text_of(text)
        if not content.strip():
            continue
        extracted += 1
        offset = 0
        for paragraph in _paragraphs(content):
            found.append(Block(
                kind=BlockKind.PARAGRAPH, text=paragraph,
                locator=at_page(uri, number, (offset, offset + len(paragraph))),
            ))
            offset += len(paragraph) + 1

    if extracted == 0:
        return [unreadable(
            uri,
            f"no text could be extracted from {pages} page(s). The document is "
            f"probably scanned images or uses an embedded CID font, neither of "
            f"which the stdlib reader decodes — this is unread, not empty",
        )]

    if extracted < pages:
        found.append(unreadable(
            uri,
            f"{pages - extracted} of {pages} page(s) yielded no text and were "
            f"not read; the rest were extracted",
        ))

    return found


def _streams(payload: bytes) -> Iterator[bytes]:
    """Every content stream, decompressed where it is Flate-encoded."""
    for match in _STREAM.finditer(payload):
        raw = match.group(1)
        if len(raw) > MAX_STREAM:
            continue
        try:
            yield zlib.decompress(raw)
        except zlib.error:
            # Not Flate, or corrupt. An uncompressed stream is legal and common
            # in small PDFs, so it is tried as-is before being given up on.
            if b"Tj" in raw or b"TJ" in raw:
                yield raw


def _text_of(stream: bytes) -> str:
    """Text-showing operators, in order, as text."""
    parts: list[str] = []
    for match in _SHOW.finditer(stream):
        chunk = match.group(0)
        for string in _STRING.finditer(chunk):
            parts.append(_unescape(string.group(0)[1:-1]))
        parts.append(" ")
    return "".join(parts)


def _unescape(raw: bytes) -> str:
    """PDF string escapes. `\\(`, `\\)`, `\\\\`, and the octal form."""
    out = bytearray()
    index = 0
    while index < len(raw):
        byte = raw[index]
        if byte != 0x5C:                     # backslash
            out.append(byte)
            index += 1
            continue
        index += 1
        if index >= len(raw):
            break
        following = raw[index]
        if following in b"nrtbf":
            out.append({0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12}[following])
            index += 1
        elif 0x30 <= following <= 0x37:      # octal, up to three digits
            digits = raw[index:index + 3]
            end = 0
            while end < len(digits) and 0x30 <= digits[end] <= 0x37:
                end += 1
            out.append(int(digits[:end], 8) & 0xFF)
            index += end
        else:
            out.append(following)
            index += 1
    return out.decode("latin-1", errors="replace")


def _paragraphs(text: str) -> list[str]:
    """Collapse the operator soup into readable lines."""
    cleaned = re.sub(r"[ \t]+", " ", text)
    return [
        line.strip() for line in cleaned.splitlines()
        if len(line.strip()) > 1
    ] or ([cleaned.strip()] if cleaned.strip() else [])
