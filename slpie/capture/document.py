"""One canonical document, so four container formats stop being four dialects.

PDF, XLSX, Word and HTML are unrelated containers that all ultimately carry the
same handful of things: headings, paragraphs, tables, rows, cells, lists. Letting
each one's quirks reach the reasoning layers would mean every rule downstream
learns four dialects — and the fourth would always be the one with the bug.

So capture normalises them into one `Document`: an ordered tree of typed `Block`s,
each carrying its `Locator` back into the original. A governance rule looking for a
licence clause then works identically over a PDF contract and an HTML licence page,
because both arrived as `Document`.

The rule that keeps this honest: **a block that could not be read is a block that
says so**, not an absent one. A page nobody could extract is `UNREADABLE` with the
reason attached — never silently omitted, which would render a document that
appears to legitimately contain nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator, Sequence

from .locator import Locator, of_file


class BlockKind(str, Enum):
    """What a piece of a document is. Small on purpose: every format maps here."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    ROW = "row"
    CELL = "cell"
    LIST_ITEM = "list_item"
    CODE = "code"
    LINK = "link"
    PAGE = "page"
    SHEET = "sheet"
    SECTION = "section"
    #: Could not be read. Present rather than omitted, because a document that
    #: silently dropped its unreadable half looks like a document that was empty.
    UNREADABLE = "unreadable"

    @property
    def structural(self) -> bool:
        """Whether it groups other blocks rather than carrying text itself."""
        return self in (
            BlockKind.TABLE, BlockKind.ROW, BlockKind.PAGE,
            BlockKind.SHEET, BlockKind.SECTION,
        )


@dataclass(frozen=True, slots=True)
class Block:
    """One addressable piece of a document."""

    kind: BlockKind
    text: str = ""
    locator: Locator = field(default_factory=lambda: of_file(""))
    level: int = 0                        # heading depth, or nesting depth
    children: tuple["Block", ...] = ()
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def readable(self) -> bool:
        return self.kind is not BlockKind.UNREADABLE

    def walk(self) -> Iterator["Block"]:
        """Depth-first, self first. The order a reader would meet them in."""
        yield self
        for child in self.children:
            yield from child.walk()

    def of_kind(self, *kinds: BlockKind) -> tuple["Block", ...]:
        wanted = set(kinds)
        return tuple(item for item in self.walk() if item.kind in wanted)

    def to_dict(self, *, depth: int = 0) -> dict[str, Any]:
        body: dict[str, Any] = {
            "kind": self.kind.value, "text": self.text[:2000],
            "locator": self.locator.to_dict(), "level": self.level,
        }
        if self.attributes:
            body["attributes"] = dict(self.attributes)
        if self.children and depth < 8:
            body["children"] = [
                child.to_dict(depth=depth + 1) for child in self.children[:500]
            ]
        elif self.children:
            body["children"] = len(self.children)
        return body

    def __str__(self) -> str:
        head = self.text.strip().splitlines()[:1]
        return f"{self.kind.value}@{self.locator.reference}: {head[0] if head else ''}"


@dataclass(frozen=True, slots=True)
class Document:
    """A normalised document, and an honest account of what could not be read."""

    uri: str
    format: str = ""
    blocks: tuple[Block, ...] = ()
    unreadable: tuple[str, ...] = ()      # reasons, one per thing that failed
    truncated: bool = False

    def walk(self) -> Iterator[Block]:
        for block in self.blocks:
            yield from block.walk()

    def of_kind(self, *kinds: BlockKind) -> tuple[Block, ...]:
        wanted = set(kinds)
        return tuple(item for item in self.walk() if item.kind in wanted)

    def cells(self) -> tuple[Block, ...]:
        return self.of_kind(BlockKind.CELL)

    def tables(self) -> tuple[Block, ...]:
        return self.of_kind(BlockKind.TABLE)

    def headings(self) -> tuple[Block, ...]:
        return self.of_kind(BlockKind.HEADING)

    @property
    def text(self) -> str:
        """Everything readable, in reading order. For search and for extraction."""
        return "\n".join(
            block.text for block in self.walk()
            if block.text and block.readable and not block.kind.structural
        )

    @property
    def complete(self) -> bool:
        """Whether everything in the source was read. Reported, never assumed."""
        return not self.unreadable and not self.truncated

    @property
    def coverage(self) -> float:
        """How much of it was read, as a fraction. Never rounded up to 1.0."""
        total = len(tuple(self.walk()))
        if not total:
            return 0.0
        unread = sum(1 for block in self.walk() if not block.readable)
        return round((total - unread) / total, 4)

    def find(self, needle: str, *, limit: int = 50) -> tuple[Block, ...]:
        """Blocks whose text contains this. Each one carries its own locator."""
        lowered = needle.lower()
        found: list[Block] = []
        for block in self.walk():
            if block.text and lowered in block.text.lower():
                found.append(block)
                if len(found) >= limit:
                    break
        return tuple(found)

    def to_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri, "format": self.format,
            "blocks": [block.to_dict() for block in self.blocks[:200]],
            "counts": {
                kind.value: len(self.of_kind(kind))
                for kind in BlockKind
                if self.of_kind(kind)
            },
            "unreadable": list(self.unreadable),
            "complete": self.complete, "coverage": self.coverage,
            "truncated": self.truncated,
        }

    def __len__(self) -> int:
        return len(tuple(self.walk()))

    def __iter__(self) -> Iterator[Block]:
        return self.walk()

    def __str__(self) -> str:
        note = "" if self.complete else f", {len(self.unreadable)} unreadable"
        return f"{self.format or 'document'} {self.uri.rsplit('/', 1)[-1]}: {len(self)} blocks{note}"


def unreadable(uri: str, reason: str, *, locator: Locator | None = None) -> Block:
    """A block standing in for something that could not be read.

    Its existence is the point. Omitting it would produce a document that appears
    to legitimately contain nothing there, and a reviewer cannot tell the
    difference between "empty" and "we could not open it" unless we say so.
    """
    return Block(
        kind=BlockKind.UNREADABLE, text="", level=0,
        locator=locator or of_file(uri),
        attributes={"reason": reason},
    )
