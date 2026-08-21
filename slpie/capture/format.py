"""What this actually is — decided by content, at the depth you asked for.

Two axes, and they solve different problems.

**Depth** is the optimisation. Four levels, each answering more and costing more,
and the caller names the one it needs:

    PROBE      first ~4KB          which family is this            constant
    STRUCTURE  container index     zip entries, sheet names        ~constant
    MODEL      full content        the canonical Document          linear
    SEMANTIC   + domain rules      "this is a Kubernetes manifest" linear + rules

This is what the whole capture path turns on. A pipeline asking "what packages are
declared here" needs SEMANTIC on the manifests and **PROBE on everything else** —
it must never fully model a 200MB PDF to discover it is not a lockfile.

**Identification is by content, never by name alone.** A `.json` that is actually
YAML, a `.yaml` that is a GitHub workflow rather than a Kubernetes manifest, a
`.txt` that is a lockfile. An extension is a `NAME_HEURISTIC` at 0.25 — the same
confidence ladder every other conclusion uses — and magic bytes or a successful
structural parse are far stronger. When extension and content disagree, that
disagreement is a *finding*, not a tiebreak: somebody named a file wrongly, and
that is worth knowing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Iterable, Mapping, Sequence

from ..domain.evidence import Evidence, EvidenceKind
from .locator import Locator, of_file


class Depth(IntEnum):
    """How far to look. Ordered, so `depth >= Depth.MODEL` reads naturally."""

    PROBE = 0
    STRUCTURE = 1
    MODEL = 2
    SEMANTIC = 3

    @property
    def label(self) -> str:
        return self.name.lower()

    @property
    def reads_everything(self) -> bool:
        return self >= Depth.MODEL


#: How many bytes a PROBE may read. Enough for every magic number and for a
#: structural guess; small enough that probing ten thousand files is free.
PROBE_BYTES = 4096

#: Magic prefixes. Checked before anything textual, because they are decisive:
#: a file starting with `PK\x03\x04` is a zip whatever it is called.
MAGIC: tuple[tuple[bytes, str, str], ...] = (
    (b"PK\x03\x04", "zip", "zip_container"),
    (b"%PDF-", "pdf", "stream_ops"),
    (b"OggS", "ogg", "media_header"),
    (b"fLaC", "flac", "media_header"),
    (b"\x1aE\xdf\xa3", "matroska", "media_header"),
    (b"RIFF", "riff", "media_header"),
    (b"ID3", "mp3", "media_header"),
    (b"\x89PNG\r\n\x1a\n", "png", ""),
    (b"\xff\xd8\xff", "jpeg", ""),
    (b"\x1f\x8b", "gzip", ""),
    (b"SQLite format 3\x00", "sqlite", ""),
    (b"\x7fELF", "elf", ""),
    (b"#!", "script", ""),
)

#: Which member of a zip decides what kind of zip it is. Order matters: an XLSX
#: and a DOCX are both zips of XML and only their contents tell them apart.
ZIP_MARKERS: tuple[tuple[str, str], ...] = (
    ("xl/workbook.xml", "xlsx"),
    ("word/document.xml", "docx"),
    ("ppt/presentation.xml", "pptx"),
    ("META-INF/MANIFEST.MF", "jar"),
    ("*.nuspec", "nupkg"),
    ("*.dist-info/METADATA", "wheel"),
)

#: Extensions, as a *hint* only. Weak on purpose — this is the signal the module
#: exists to distrust.
EXTENSIONS: Mapping[str, str] = {
    ".json": "json", ".jsonl": "jsonl", ".toml": "toml", ".yaml": "yaml",
    ".yml": "yaml", ".xml": "xml", ".html": "html", ".htm": "html",
    ".csv": "csv", ".tsv": "tsv", ".md": "markdown", ".txt": "text",
    ".py": "python", ".ini": "ini", ".cfg": "ini", ".properties": "properties",
    ".env": "dotenv", ".pdf": "pdf", ".xlsx": "xlsx", ".docx": "docx",
    ".lock": "lockfile", ".sql": "sql", ".pptx": "pptx",
    ".mp4": "mp4", ".mov": "mov", ".m4v": "m4v", ".webm": "matroska",
    ".mkv": "matroska", ".avi": "avi",
    ".mp3": "mp3", ".wav": "wav", ".m4a": "m4a", ".flac": "flac",
    ".ogg": "ogg", ".aac": "m4a", ".aiff": "aiff",
}

#: Containers whose *content* the kernel does not read. Identified precisely, with
#: metadata modelled and the absence of a transcript stated — never rendered as an
#: empty document, which would read as "it contained nothing".
MEDIA_FORMATS = frozenset({
    "mp4", "mov", "m4v", "m4a", "matroska", "avi", "mp3", "wav",
    "flac", "ogg", "aiff",
})

#: Which strategies each format is composed of. This *is* the dispatch table —
#: adding a format is registering strategies, not editing a chain of ifs.
COMPOSITION: Mapping[str, tuple[str, ...]] = {
    "xlsx": ("zip_container", "xml_tree"),
    "docx": ("zip_container", "xml_tree"),
    "pptx": ("zip_container", "xml_tree"),
    "jar": ("zip_container",),
    "wheel": ("zip_container",),
    "nupkg": ("zip_container", "xml_tree"),
    "json": ("json_tree",),
    "jsonl": ("json_tree",),
    "xml": ("xml_tree",),
    "html": ("html_dom",),
    "csv": ("delimited",),
    "tsv": ("delimited",),
    "ini": ("keyvalue",),
    "properties": ("keyvalue",),
    "dotenv": ("keyvalue",),
    "toml": ("keyvalue",),
    "python": ("python_ast",),
    "pdf": ("stream_ops",),
    **{name: ("media_header",) for name in (
        "mp4", "mov", "m4v", "m4a", "matroska", "avi", "mp3", "wav",
        "flac", "ogg", "aiff",
    )},
}

#: Structural signatures for text formats, tried in order. Each is a cheap test
#: that a full parse would confirm.
_TEXT_SIGNATURES: tuple[tuple[str, str], ...] = (
    ("json", "json"),
    ("xml", "xml"),
    ("html", "html"),
    ("python", "python"),
    ("yaml", "yaml"),
    ("toml", "toml"),
)

#: Python constructs that no other text format shares. Checked as a signature
#: because without one every `.py` file was identified on its extension alone —
#: a name heuristic at 0.25 — and the firewall then quarantined the entire source
#: tree for being unidentifiable. Content identifying content is the whole point.
_PYTHON_MARKERS = (
    "import ", "from ", "def ", "class ", "async def ",
    "if __name__", "#!/usr/bin/env python", "# -*- coding",
)


@dataclass(frozen=True, slots=True)
class Format:
    """What something is, how sure we are, and what the evidence was."""

    name: str
    media_type: str = "application/octet-stream"
    dialect: str = ""                     # the discriminator within a family
    confidence: float = 0.0
    depth: Depth = Depth.PROBE
    strategies: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    contradicts_extension: bool = False
    claimed: str = ""                     # what the extension said, when it differed
    reason: str = ""

    @property
    def known(self) -> bool:
        return self.name not in ("", "unknown")

    @property
    def binary(self) -> bool:
        return self.name in (
            "zip", "pdf", "png", "jpeg", "gzip", "elf", "sqlite",
            "xlsx", "docx", "pptx", "jar", "wheel", "nupkg", "binary",
        ) or self.name in MEDIA_FORMATS

    @property
    def media(self) -> bool:
        """Whether this is audio or video, whose content the kernel cannot read."""
        return self.name in MEDIA_FORMATS

    @property
    def modellable(self) -> bool:
        """Whether a canonical `Document` can be built from it."""
        return bool(self.strategies) and self.name in COMPOSITION

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "media_type": self.media_type,
            "dialect": self.dialect, "confidence": round(self.confidence, 4),
            "depth": self.depth.label, "strategies": list(self.strategies),
            "known": self.known, "binary": self.binary,
            "modellable": self.modellable, "media": self.media,
            "contradicts_extension": self.contradicts_extension,
            "claimed": self.claimed, "reason": self.reason,
            "sources": [item.location.reference for item in self.evidence],
        }

    def __str__(self) -> str:
        note = f" (named .{self.claimed})" if self.contradicts_extension else ""
        return f"{self.name}{f'/{self.dialect}' if self.dialect else ''}{note}"


def identify(
    payload: bytes,
    uri: str = "",
    *,
    depth: Depth = Depth.STRUCTURE,
) -> Format:
    """What this is. Reads only as far as the requested depth.

    The order is decisive-first: magic bytes, then container contents, then
    structure, and only then the filename. That ordering is the whole point —
    consulting the extension first and confirming it afterwards is how a
    mislabelled file gets read by the wrong parser with a confident result.
    """
    claimed = _from_extension(uri)
    head = payload[:PROBE_BYTES]

    # 0. Media containers, resolved precisely: `RIFF` is WAV or AVI, and an
    #    `ftyp` box at byte 4 is MP4, MOV or M4A depending on its brand. Checking
    #    before the generic magic table is what keeps those distinct.
    from .media import sniff

    found_media = sniff(payload)
    if found_media is not None:
        return _format(
            found_media.container, uri, claimed, depth,
            confidence=found_media.confidence, strategies=("media_header",),
            kind=EvidenceKind.RUNTIME_TRACE,
            reason=(
                f"a {found_media.container} {found_media.family} container"
                + (f", {found_media.duration}" if found_media.seconds else "")
            ),
        )

    # 1. Magic bytes. Decisive.
    for prefix, name, strategy in MAGIC:
        if head.startswith(prefix):
            if name == "zip" and depth >= Depth.STRUCTURE:
                return _zip_format(payload, uri, claimed, depth)
            return _format(
                name, uri, claimed, depth,
                confidence=0.98, strategies=(strategy,) if strategy else (),
                kind=EvidenceKind.RUNTIME_TRACE,
                reason=f"begins with the {name} magic number",
            )

    # 2. Structure, for text.
    text = _as_text(head)
    if text is None:
        return _format(
            "binary", uri, claimed, depth, confidence=0.6,
            reason="not decodable as text and matches no known magic number",
        )

    for signature, name in _TEXT_SIGNATURES:
        if _looks_like(signature, text):
            return _format(
                name, uri, claimed, depth, confidence=0.9,
                strategies=COMPOSITION.get(name, ()),
                kind=EvidenceKind.STATIC_IMPORT,
                reason=f"parses structurally as {name}",
                dialect=_dialect(name, text) if depth >= Depth.SEMANTIC else "",
            )

    if _looks_like("delimited", text):
        name = "tsv" if "\t" in text.splitlines()[0] else "csv"
        return _format(
            name, uri, claimed, depth, confidence=0.7,
            strategies=COMPOSITION.get(name, ()),
            reason="consistent column counts across rows",
        )

    if _looks_like("keyvalue", text):
        return _format(
            claimed if claimed in ("ini", "properties", "dotenv") else "properties",
            uri, claimed, depth, confidence=0.65,
            strategies=("keyvalue",),
            reason="every non-comment line is a key/value assignment",
        )

    # 3. The filename, last and weakest. A NAME_HEURISTIC at 0.25, exactly as the
    #    confidence ladder rates it everywhere else.
    if claimed:
        return _format(
            claimed, uri, claimed, depth, confidence=0.25,
            strategies=COMPOSITION.get(claimed, ()),
            kind=EvidenceKind.NAME_HEURISTIC,
            reason=(
                f"nothing in the content identified it; the .{claimed} extension "
                f"is the only signal, which is a weak one"
            ),
        )

    return _format(
        "text", uri, claimed, depth, confidence=0.3,
        reason="readable text of no recognised structure",
    )


def _zip_format(payload: bytes, uri: str, claimed: str, depth: Depth) -> Format:
    """Which kind of zip. An XLSX and a JAR are both zips; the members differ."""
    from .strategies import StrategyError, zip_container

    try:
        entries = zip_container(payload, wanted=[])
    except StrategyError as error:
        return _format(
            "zip", uri, claimed, depth, confidence=0.5,
            reason=f"a zip that could not be listed: {error}",
        )

    names = set(entries)
    for marker, name in ZIP_MARKERS:
        matched = (
            any(item.endswith(marker[1:]) for item in names)
            if marker.startswith("*") else marker in names
        )
        if matched:
            return _format(
                name, uri, claimed, depth, confidence=0.97,
                strategies=COMPOSITION.get(name, ("zip_container",)),
                kind=EvidenceKind.RUNTIME_TRACE,
                reason=f"the archive contains {marker}, which only a {name} does",
            )

    return _format(
        "zip", uri, claimed, depth, confidence=0.9,
        strategies=("zip_container",),
        reason=f"a zip of {len(names)} members matching no known layout",
    )


def _format(
    name: str,
    uri: str,
    claimed: str,
    depth: Depth,
    *,
    confidence: float,
    strategies: Sequence[str] = (),
    kind: EvidenceKind = EvidenceKind.STATIC_IMPORT,
    reason: str = "",
    dialect: str = "",
) -> Format:
    contradicts = bool(claimed) and claimed != name and not _compatible(claimed, name)
    return Format(
        name=name, media_type=_media_type(name), dialect=dialect,
        confidence=confidence, depth=depth,
        strategies=tuple(strategies or COMPOSITION.get(name, ())),
        contradicts_extension=contradicts,
        claimed=claimed if contradicts else "",
        reason=reason,
        evidence=(Evidence(
            kind=kind, location=of_file(uri).to_location(),
            extractor="slpie.capture.format", extractor_version="1",
            excerpt=reason[:300],
        ),),
    )


#: Names that are the same thing under two spellings, so a `.yml` holding YAML is
#: not reported as a contradiction.
#:
#: Each group must be **narrow**. A single group of `{lockfile, json, text, yaml}`
#: was the obvious way to say "a .lock file may hold any of these", and it also
#: made JSON and YAML compatible with each other by transitivity — so a YAML
#: document named `.json`, which is the exact mislabelling this module exists to
#: catch, was reported as no contradiction at all. `.lock` gets one pair per
#: format it may legitimately hold, and nothing else is joined.
_ALIASES: tuple[frozenset[str], ...] = (
    frozenset({"yaml", "yml"}),
    frozenset({"html", "htm", "xhtml"}),
    frozenset({"text", "markdown", "txt"}),
    frozenset({"lockfile", "json"}),
    frozenset({"lockfile", "yaml"}),
    frozenset({"lockfile", "toml"}),
    frozenset({"lockfile", "text"}),
    frozenset({"ini", "properties", "dotenv"}),
    frozenset({"csv", "text"}),
    frozenset({"tsv", "text"}),
)


def _compatible(claimed: str, found: str) -> bool:
    return any(
        claimed in group and found in group for group in _ALIASES
    )


def _from_extension(uri: str) -> str:
    name = uri.rsplit("/", 1)[-1].lower()
    for extension, format_name in EXTENSIONS.items():
        if name.endswith(extension):
            return format_name
    return ""


def _as_text(head: bytes) -> str | None:
    if b"\x00" in head:
        return None
    try:
        return head.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return head.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None


def _looks_like(signature: str, text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False

    if signature == "json":
        return stripped[0] in "{[" and _balanced(stripped)
    if signature == "xml":
        return stripped.startswith(("<?xml", "<")) and not _looks_like("html", text)
    if signature == "html":
        lowered = stripped[:400].lower()
        return "<!doctype html" in lowered or "<html" in lowered
    if signature == "python":
        head = stripped[:PROBE_BYTES]
        if not any(marker in head for marker in _PYTHON_MARKERS):
            return False
        # A marker alone is weak — "import" appears in prose. Requiring a marker
        # at the *start of a line* is what distinguishes source from a document
        # that mentions it.
        return any(
            line.startswith(("import ", "from ", "def ", "class ", "async def",
                             "@", "#!/usr/bin/env python"))
            for line in head.splitlines()
        )
    if signature == "yaml":
        # A YAML document either declares itself or is a run of `key: value` lines
        # at the left margin. Deliberately conservative: almost anything is
        # technically valid YAML, and claiming everything is YAML helps nobody.
        if stripped.startswith("---"):
            return True
        lines = [
            line for line in stripped.splitlines()[:20]
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if not lines:
            return False
        shaped = sum(
            1 for line in lines
            if ":" in line and not line.lstrip().startswith(("{", "["))
        )
        return shaped >= max(2, len(lines) // 2)
    if signature == "toml":
        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        return any(
            line.startswith("[") and line.endswith("]") for line in lines[:20]
        ) and any("=" in line for line in lines[:20])
    if signature == "delimited":
        rows = [line for line in stripped.splitlines()[:10] if line.strip()]
        if len(rows) < 2:
            return False
        for separator in (",", "\t", ";"):
            counts = {row.count(separator) for row in rows}
            if len(counts) == 1 and counts.pop() >= 1:
                return True
        return False
    if signature == "keyvalue":
        lines = [
            line.strip() for line in stripped.splitlines()[:20]
            if line.strip() and not line.strip().startswith(("#", ";"))
        ]
        if not lines:
            return False
        return all(
            ("=" in line or ":" in line)
            or (line.startswith("[") and line.endswith("]"))
            for line in lines
        )
    return False


def _balanced(text: str) -> bool:
    """A cheap sanity check before claiming JSON. Not a parse."""
    opening = text.count("{") + text.count("[")
    closing = text.count("}") + text.count("]")
    # A truncated probe legitimately has more openings than closings, so only a
    # surplus of *closings* is evidence against.
    return opening >= closing


def _dialect(name: str, text: str) -> str:
    """The SEMANTIC discriminator: which *kind* of YAML or JSON this is.

    A `.yaml` may be a Kubernetes manifest, a GitHub workflow, a docker-compose
    file or an OpenAPI document, and every one of those wants a different reader.
    Deciding by extension alone routes all four to whichever parser was written
    first.
    """
    head = text[:2000]
    if name == "yaml":
        if "apiVersion:" in head and "kind:" in head:
            return "kubernetes"
        if head.lstrip().startswith("on:") or "\njobs:" in head:
            return "github-workflow"
        if "services:" in head and ("image:" in head or "build:" in head):
            return "compose"
        if "openapi:" in head or "swagger:" in head:
            return "openapi"
        if "apiVersion: slpie/" in head:
            return "slpie-environment"
    if name == "json":
        if '"lockfileVersion"' in head:
            return "npm-lockfile"
        if '"openapi"' in head or '"swagger"' in head:
            return "openapi"
        if '"$schema"' in head and "json-schema" in head:
            return "json-schema"
        if '"dependencies"' in head and '"name"' in head:
            return "npm-manifest"
    return ""


def _media_type(name: str) -> str:
    return {
        "json": "application/json", "jsonl": "application/x-ndjson",
        "xml": "application/xml", "html": "text/html",
        "yaml": "application/yaml", "toml": "application/toml",
        "csv": "text/csv", "tsv": "text/tab-separated-values",
        "pdf": "application/pdf", "zip": "application/zip",
        "xlsx": "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet",
        "docx": "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document",
        "python": "text/x-python", "markdown": "text/markdown",
    }.get(name, "text/plain" if name in ("text", "ini", "properties", "dotenv")
          else "application/octet-stream")
