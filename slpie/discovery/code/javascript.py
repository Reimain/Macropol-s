"""JavaScript and TypeScript imports, without a JavaScript parser.

There is no `ast` for JS in the standard library, so this is a scanner rather
than a parser — and it says so, both here and in the confidence it produces.

Comments are blanked and every string body is replaced by a token that resolves
back to its literal. That removes the failure mode which makes naive regex
analysis untrustworthy — an `import` written inside a comment or a quoted code
sample no longer counts — while keeping the thing the scanner actually needs,
since the specifier in `require('lodash')` *is* a string.

What it cannot do is resolve aliases, follow re-exports, or understand a build
config's path mapping. **This is exactly the case the out-of-process plugin
protocol exists for** — a real TypeScript analyzer using the compiler API
registers as an external plugin and produces better evidence for the same files.
This built-in is the honest floor, not the ceiling.
"""

from __future__ import annotations

import re
from typing import Iterator

from ...domain.evidence import EvidenceKind
from ...domain.identity import Purl
from ...plugins.protocol import DiscoveryResult, Observation
from ..base import Source, declares, evidence_at, imports, module_urn, result
from ..ecosystems.npm import npm_purl

EXTRACTOR = "slpie.javascript"

#: `import x from "y"`, `import "y"`, `export ... from "y"`
_STATIC = re.compile(
    r"""(?:^|\s)(?:import|export)\s+(?:[^'"();]*?\s+from\s+)?['"]([^'"]+)['"]""",
    re.M,
)
#: `require("y")`
_REQUIRE = re.compile(r"""\brequire\s*\(\s*['"]([^'"]+)['"]\s*\)""")
#: `import("y")` — a real target, loaded lazily.
_DYNAMIC_LITERAL = re.compile(r"""\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)""")
#: `import(expression)` — a load site with no nameable target.
_DYNAMIC_COMPUTED = re.compile(r"""\bimport\s*\(\s*(?!['"])[^)]+\)""")

#: Node's own modules. Not dependencies on anything installable.
BUILTIN = frozenset({
    "assert", "async_hooks", "buffer", "child_process", "cluster", "console",
    "constants", "crypto", "dgram", "diagnostics_channel", "dns", "domain",
    "events", "fs", "http", "http2", "https", "inspector", "module", "net",
    "os", "path", "perf_hooks", "process", "punycode", "querystring",
    "readline", "repl", "stream", "string_decoder", "sys", "timers", "tls",
    "trace_events", "tty", "url", "util", "v8", "vm", "wasi", "worker_threads",
    "zlib",
})


def discover_javascript_source(source: Source) -> DiscoveryResult:
    """Scan one JS or TS file for the modules it pulls in."""
    masked, literals = _mask(source.text)
    subject = module_urn(source.element, _module_path(source)).to_string()

    observations: list[Observation] = [
        declares(
            subject,
            evidence_at(
                source.uri, kind=EvidenceKind.STATIC_IMPORT, extractor=EXTRACTOR,
                line=1, excerpt=source.excerpt(1), digest=source.digest,
            ),
            node_kind="module", language=_language(source),
        )
    ]

    seen: set[tuple[str, int]] = set()
    for pattern, kind in (
        (_STATIC, EvidenceKind.STATIC_IMPORT),
        (_REQUIRE, EvidenceKind.STATIC_IMPORT),
        (_DYNAMIC_LITERAL, EvidenceKind.DYNAMIC_LOAD),
    ):
        for match in pattern.finditer(masked):
            specifier = _resolve(match.group(1), literals)
            # Anchored to the captured group, not the match: the static pattern
            # consumes the whitespace before `import`, and that includes the
            # preceding newline — which would report every line one too early.
            line = masked.count("\n", 0, match.start(1)) + 1
            if not specifier or (specifier, line) in seen:
                continue
            seen.add((specifier, line))
            observation = _observe(source, subject, specifier, line, kind)
            if observation is not None:
                observations.append(observation)

    for match in _DYNAMIC_COMPUTED.finditer(masked):
        line = masked.count("\n", 0, match.start()) + 1
        observations.append(declares(
            subject,
            evidence_at(
                source.uri, kind=EvidenceKind.DYNAMIC_LOAD, extractor=EXTRACTOR,
                line=line, excerpt=source.excerpt(line), digest=source.digest,
            ),
            node_kind="module", language=_language(source),
            dynamic_import_site=True,
        ))

    return result(observations)


def _observe(
    source: Source, subject: str, specifier: str, line: int, kind: EvidenceKind
) -> Observation | None:
    package = _package_name(specifier)
    if package is None:
        return None
    return imports(
        subject,
        npm_purl(package).to_string(),
        evidence_at(
            source.uri, kind=kind, extractor=EXTRACTOR,
            line=line, excerpt=source.excerpt(line), digest=source.digest,
        ),
        specifier=specifier,
        language=_language(source),
        dynamic=kind is EvidenceKind.DYNAMIC_LOAD,
    )


def _package_name(specifier: str) -> str | None:
    """The installable package a specifier refers to, or None.

    Relative paths, absolute paths and URLs are not packages. `node:fs` and bare
    `fs` are the runtime, not a dependency. A deep import like
    `lodash/fp/merge` is still lodash.
    """
    cleaned = specifier.strip()
    if not cleaned or cleaned.startswith((".", "/", "http:", "https:", "data:", "node:")):
        return None

    if cleaned.startswith("@"):
        parts = cleaned.split("/")
        if len(parts) < 2 or not parts[1].strip():
            return None
        return f"{parts[0].strip()}/{parts[1].strip()}"

    root = cleaned.split("/")[0].strip()
    # A blank or builtin root is not an installable package. Without this guard
    # a whitespace-only specifier becomes a purl with no name at all.
    return None if not root or root in BUILTIN else root


#: Marks where a string literal was, so its contents cannot be mistaken for code.
_MASK = re.compile(r"\x00(\d+)\x00")


def _mask(text: str) -> tuple[str, list[str]]:
    """Blank comments, and replace each string's body with a reference to it.

    Two things have to be true at once. A specifier inside a string must still
    be *readable*, because that is the whole point — `require('lodash')` means
    lodash. But an `import` statement written inside a string must not be
    *executable* by the scanner, or a code sample in a docstring becomes a
    dependency.

    Masking gives both: the body is replaced by a token that resolves back to
    the literal, so a specifier survives while the text of a quoted import
    statement does not. Newlines are preserved so every line number stays exact.
    """
    out: list[str] = []
    values: list[str] = []
    index = 0
    length = len(text)

    while index < length:
        character = text[index]
        pair = text[index:index + 2]

        if pair == "//":
            end = text.find("\n", index)
            end = length if end < 0 else end
            out.append(" " * (end - index))
            index = end
        elif pair == "/*":
            end = text.find("*/", index + 2)
            end = length if end < 0 else end + 2
            out.append("".join(c if c == "\n" else " " for c in text[index:end]))
            index = end
        elif character in "\"'`":
            end = _string_end(text, index, character)
            body = text[index + 1:max(end - 1, index + 1)]
            values.append(body)
            out.append(character)
            out.append(f"\x00{len(values) - 1}\x00")
            out.append("\n" * body.count("\n"))
            if end - 1 < length:
                out.append(text[end - 1])
            index = end
        else:
            out.append(character)
            index += 1

    return "".join(out), values


def _resolve(captured: str, values: list[str]) -> str:
    """Turn a masked capture back into the literal it stands for."""
    match = _MASK.fullmatch(captured.strip())
    if match is None:
        return ""
    position = int(match.group(1))
    return values[position] if position < len(values) else ""


def _string_end(text: str, start: int, quote: str) -> int:
    index = start + 1
    length = len(text)
    while index < length:
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == quote:
            return index + 1
        if text[index] == "\n" and quote != "`":
            return index      # an unterminated string; do not swallow the file
        index += 1
    return length


def _language(source: Source) -> str:
    name = source.name
    if name.endswith((".ts", ".tsx", ".mts", ".cts")):
        return "typescript"
    return "javascript"


def _module_path(source: Source) -> str:
    path = _relative(source)
    for suffix in (".tsx", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".ts", ".js"):
        if path.endswith(suffix):
            return path[: -len(suffix)]
    return path


def _relative(source: Source) -> str:
    """The path within its element, so a module urn does not repeat the name."""
    path = source.uri
    for prefix in ("file://", "./"):
        if path.startswith(prefix):
            path = path[len(prefix):]
    if source.element and f"/{source.element}/" in f"/{path}":
        _, _, tail = path.partition(f"{source.element}/")
        return tail or path
    return path


DISCOVERERS = (
    (
        "slpie.javascript", discover_javascript_source,
        ("*.js", "*.jsx", "*.mjs", "*.cjs", "*.ts", "*.tsx", "*.mts", "*.cts"),
        ("imports", "declares"),
        (EvidenceKind.STATIC_IMPORT, EvidenceKind.DYNAMIC_LOAD),
    ),
)
