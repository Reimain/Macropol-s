"""Protocol Buffers — services, their methods, and what flows between them.

A `.proto` file is the most precise interface contract in common use: it names
the service, every method, the message each side sends, and whether either side
streams. All four matter to the graph, and the fourth is the one usually thrown
away — a server-streaming method and a unary one have very different failure
modes, and "who breaks if this becomes streaming" is a real question.

The unit is the **method**: `urn:slpie:api:grpc/payments.Payments/CreateOrder`,
matching the wire path gRPC actually uses, so a runtime trace and this static
reading name the same thing without a translation table.

What it is careful about:

* **The package qualifies everything.** Two files can both define `Order`, and
  without the `package` prefix they collapse into one node that is neither.
* **Imports are edges between files, not between messages.** `import
  "common/money.proto"` says this contract depends on that one — which is what
  a change-impact query walks.
* **Comments are blanked, not deleted**, so every line number still matches the
  file on disk. A `//` containing a brace would otherwise close a service early.

Parsed structurally rather than with a full proto3 grammar: services, methods,
messages, enums and imports, which is what the graph needs. Options, custom
extensions and `oneof` bodies are read past rather than interpreted.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

from ...domain.evidence import EvidenceKind
from ...domain.identity import Urn
from ...plugins.protocol import DiscoveryResult, Observation
from ..base import Source, declares, evidence_at, result, scope_of

EXTRACTOR = "slpie.grpc"

_PACKAGE = re.compile(r"^\s*package\s+(?P<name>[A-Za-z_][\w.]*)\s*;", re.MULTILINE)
_IMPORT = re.compile(r'^\s*import\s+(?:public\s+|weak\s+)?"(?P<path>[^"]+)"\s*;', re.MULTILINE)
_SERVICE = re.compile(r"^\s*service\s+(?P<name>\w+)\s*\{", re.MULTILINE)
_MESSAGE = re.compile(r"^\s*(?P<keyword>message|enum)\s+(?P<name>\w+)\s*\{", re.MULTILINE)

#: `rpc CreateOrder (stream OrderRequest) returns (stream OrderReply);`
_RPC = re.compile(
    r"\brpc\s+(?P<name>\w+)\s*\(\s*(?P<client_stream>stream\s+)?(?P<request>[\w.]+)\s*\)"
    r"\s*returns\s*\(\s*(?P<server_stream>stream\s+)?(?P<reply>[\w.]+)\s*\)"
)


def blank_comments(text: str) -> str:
    """Replace comments with their own newlines, preserving every line number."""

    def blank(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    without_blocks = re.sub(r"/\*[\s\S]*?\*/", blank, text)
    return re.sub(r"//[^\n]*", "", without_blocks)


def qualified(package: str, name: str) -> str:
    """`Order` in package `payments` is `payments.Order`, and never just `Order`."""
    cleaned = name.strip()
    if "." in cleaned or not package:
        return cleaned
    return f"{package}.{cleaned}"


def method_urn(package: str, service: str, method: str) -> Urn:
    """The wire path gRPC itself uses, so a trace and this reading agree."""
    return Urn.create("api", "grpc", f"{qualified(package, service)}/{method}")


def message_urn(package: str, name: str) -> Urn:
    return Urn.create("schema", "protobuf", qualified(package, name))


def _line_of(text: str, index: int) -> int:
    return text[:index].count("\n") + 1


def _body(text: str, start: int) -> tuple[str, int]:
    """The braced body beginning at `start`, and the index just past it."""
    depth = 1
    index = start
    while index < len(text) and depth:
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
        index += 1
    return text[start: index - 1], index


def discover_proto(source: Source) -> DiscoveryResult:
    """Read a `.proto` into services, methods, messages and file dependencies."""
    text = blank_comments(source.text)
    if not text.strip():
        return result(())

    package_match = _PACKAGE.search(text)
    package = package_match.group("name") if package_match else ""
    scope = scope_of(source.element, "grpc")
    service_node = Urn.create("service", scope, package or "default").to_string()

    observations: list[Observation] = []

    def cite(line: int, excerpt: str):
        return evidence_at(
            source.uri, kind=EvidenceKind.MANIFEST_DECLARED, extractor=EXTRACTOR,
            line=line, excerpt=excerpt[:200], digest=source.digest,
        )

    for match in _IMPORT.finditer(text):
        path = match.group("path")
        line = _line_of(text, match.start("path"))
        observations.append(Observation(
            kind="references",
            subject=Urn.create("schema", "protobuf", "file", source.uri).to_string(),
            object=Urn.create("schema", "protobuf", "file", path).to_string(),
            evidence=cite(line, f'import "{path}";'),
            properties={"protocol": "grpc", "relation": "import"},
        ))

    for match in _MESSAGE.finditer(text):
        name = match.group("name")
        line = _line_of(text, match.start("name"))
        observations.append(declares(
            message_urn(package, name).to_string(),
            cite(line, f"{match.group('keyword')} {name}"),
            node_kind="schema", protocol="protobuf", package=package,
            definition=match.group("keyword"),
        ))

    for match in _SERVICE.finditer(text):
        name = match.group("name")
        line = _line_of(text, match.start("name"))
        body, _end = _body(text, match.end())

        observations.append(declares(
            Urn.create("api", "grpc", qualified(package, name)).to_string(),
            cite(line, f"service {name}"),
            node_kind="api", protocol="grpc", package=package,
        ))

        for rpc in _RPC.finditer(body):
            method = rpc.group("name")
            request = rpc.group("request")
            reply = rpc.group("reply")
            client_stream = bool(rpc.group("client_stream"))
            server_stream = bool(rpc.group("server_stream"))
            rpc_line = line + body[: rpc.start()].count("\n")
            evidence = cite(rpc_line, rpc.group(0))

            operation = method_urn(package, name, method).to_string()
            observations.append(declares(
                operation, evidence, node_kind="api", protocol="grpc",
                service=qualified(package, name), method=method,
                request=qualified(package, request),
                reply=qualified(package, reply),
                # Kept because a unary call and a streaming one fail
                # differently, and "did this become streaming?" is a real
                # change-impact question.
                client_streaming=client_stream,
                server_streaming=server_stream,
                streaming=client_stream or server_stream,
            ))
            observations.append(Observation(
                kind="owns", subject=service_node, object=operation,
                evidence=evidence, properties={"protocol": "grpc"},
            ))
            for message in (request, reply):
                observations.append(Observation(
                    kind="references", subject=operation,
                    object=message_urn(package, message).to_string(),
                    evidence=evidence,
                    properties={
                        "protocol": "protobuf",
                        "role": "request" if message == request else "reply",
                    },
                ))

    return result(observations)


DISCOVERERS = (
    (
        "slpie.grpc", discover_proto,
        ("*.proto",), ("declares", "owns", "references"),
        (EvidenceKind.MANIFEST_DECLARED,),
    ),
)
