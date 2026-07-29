"""AsyncAPI 2.x and 3.x — channels, and which way the messages actually go.

**The inversion.** In AsyncAPI 2.x the operation keywords describe the channel
from the point of view of *someone else*, not of the application the document
describes. Under a channel:

* ``publish`` documents a message the application **receives** — it is what a
  client may publish *to* this application. The application is a *subscriber*.
* ``subscribe`` documents a message the application **sends** — it is what a
  client may subscribe *to*. The application is a *publisher*.

This is the single most common misreading of the format, and reading it the
intuitive way reverses every event edge in the graph: producers become
consumers, and impact analysis then walks the dependency backwards, reporting
that the service which emits an event is at risk from the ones that read it.

AsyncAPI 3.0 fixed this by naming the direction from the application's own point
of view: an operation carries ``action: send`` (the application publishes) or
``action: receive`` (the application subscribes). No inversion.

So: 2.x ``publish`` and 3.x ``receive`` both mean *subscribes*; 2.x
``subscribe`` and 3.x ``send`` both mean *publishes*. That mapping is
:data:`DIRECTIONS`, and it is a table rather than a conditional so it can be
read and checked in one glance.
"""

from __future__ import annotations

from typing import Any, Iterator, Mapping

from ...domain.evidence import EvidenceKind
from ...domain.identity import Urn
from ...plugins.protocol import DiscoveryResult, Observation
from ..base import Source, declares, evidence_at, result
from .openapi import load_specification, slug

EXTRACTOR = "slpie.asyncapi"

#: `(major version, keyword) -> observation kind`, from the application's own
#: point of view. The 2.x row is deliberately the inverse of the keyword.
DIRECTIONS: dict[tuple[str, str], str] = {
    ("2", "publish"): "subscribes",    # a client publishes *to* us; we receive
    ("2", "subscribe"): "publishes",   # a client subscribes *to* us; we send
    ("3", "receive"): "subscribes",
    ("3", "send"): "publishes",
}


def topic_urn(channel: str) -> Urn:
    """A channel address becomes a topic. Wildcards survive percent-encoding."""
    return Urn.create("topic", channel or "unnamed")


def application_urn(name: str) -> Urn:
    return Urn.create("service", name)


def discover_asyncapi(source: Source) -> DiscoveryResult:
    """Read channels, and the direction of every operation on them."""
    document, error = load_specification(source)
    if error:
        return result((), errors=[error])
    if not isinstance(document, dict):
        return result((), errors=[f"{source.uri}: expected a mapping at the top level"])

    version = str(document.get("asyncapi", ""))
    if not version:
        return result(())
    major = version.split(".")[0]
    if major not in ("2", "3"):
        return result((), errors=[f"{source.uri}: unsupported AsyncAPI version {version!r}"])

    info = document.get("info") if isinstance(document.get("info"), dict) else {}
    title = str(info.get("title", "") or source.name)
    application = application_urn(slug(title)).to_string()

    def cite(*needles: str, kind: EvidenceKind = EvidenceKind.MANIFEST_DECLARED):
        line = 1
        for needle in needles:
            if needle and source.line(needle):
                line = source.line(needle)
                break
        return evidence_at(
            source.uri, kind=kind, extractor=EXTRACTOR,
            line=line, excerpt=source.excerpt(line), digest=source.digest,
        )

    observations: list[Observation] = [declares(
        application,
        cite("title", "asyncapi"),
        node_kind="service",
        application_title=title,
        asyncapi_version=version,
        servers=sorted(str(name) for name in (document.get("servers") or {})) if isinstance(document.get("servers"), Mapping) else [],
        protocols=sorted(_protocols(document)),
    )]
    errors: list[str] = []

    channels = document.get("channels")
    if not isinstance(channels, Mapping):
        errors.append(f"{source.uri}: no channels section")
        channels = {}

    addresses: dict[str, str] = {}
    for key, channel in channels.items():
        address = str(key)
        if major == "3" and isinstance(channel, Mapping) and channel.get("address"):
            address = str(channel["address"])
        addresses[str(key)] = address
        subject = topic_urn(address).to_string()
        observations.append(declares(
            subject, cite(f"{key}:"),
            node_kind="event",
            channel=str(key), address=address,
            description=str(channel.get("description", ""))[:200] if isinstance(channel, Mapping) else "",
            wildcard=any(character in address for character in "+#{}"),
        ))

    if major == "2":
        pairs = _operations_v2(channels)
    else:
        pairs = _operations_v3(document, addresses)

    for keyword, channel_key, operation in pairs:
        kind = DIRECTIONS.get((major, keyword))
        if kind is None:
            errors.append(f"{source.uri}: unrecognised operation {keyword!r}")
            continue
        address = addresses.get(channel_key, channel_key)
        operation_id = str(operation.get("operationId", "")) if isinstance(operation, Mapping) else ""
        observations.append(Observation(
            kind=kind,
            subject=application,
            object=topic_urn(address).to_string(),
            evidence=cite(
                f"operationId: {operation_id}" if operation_id else "",
                f"{keyword}:", f"{channel_key}:",
            ),
            properties={
                # The keyword as written *and* what it means, because the two
                # differ in 2.x and an explanation has to show both.
                "keyword": keyword,
                "asyncapi_version": version,
                "channel": channel_key,
                "address": address,
                "operation_id": operation_id,
                "message": _message_name(operation),
            },
        ))

    return result(observations, errors=errors)


def _operations_v2(channels: Mapping[str, Any]) -> Iterator[tuple[str, str, Any]]:
    """2.x nests operations under the channel, keyed by `publish`/`subscribe`."""
    for key, channel in channels.items():
        if not isinstance(channel, Mapping):
            continue
        for keyword in ("publish", "subscribe"):
            operation = channel.get(keyword)
            if isinstance(operation, Mapping):
                yield keyword, str(key), operation


def _operations_v3(
    document: Mapping[str, Any], addresses: Mapping[str, str]
) -> Iterator[tuple[str, str, Any]]:
    """3.x lifts operations to the top level and names the direction itself."""
    operations = document.get("operations")
    if not isinstance(operations, Mapping):
        return
    for name, operation in operations.items():
        if not isinstance(operation, Mapping):
            continue
        action = str(operation.get("action", "")).strip().lower()
        channel = _channel_key(operation.get("channel"), addresses)
        yield action, channel or str(name), operation


def _channel_key(reference: Any, addresses: Mapping[str, str]) -> str:
    """`channel: {$ref: '#/channels/orders'}` names a channel by key."""
    if isinstance(reference, Mapping):
        target = str(reference.get("$ref", ""))
        key = target.rstrip("/").rsplit("/", 1)[-1]
        if key in addresses:
            return key
        return key
    if isinstance(reference, str):
        return reference.rstrip("/").rsplit("/", 1)[-1]
    return ""


def _message_name(operation: Any) -> str:
    if not isinstance(operation, Mapping):
        return ""
    message = operation.get("message")
    if isinstance(message, Mapping):
        name = message.get("name") or message.get("$ref") or message.get("title")
        if name:
            return str(name).rstrip("/").rsplit("/", 1)[-1]
    if isinstance(message, list) and message:
        first = message[0]
        if isinstance(first, Mapping):
            return str(first.get("$ref", first.get("name", ""))).rstrip("/").rsplit("/", 1)[-1]
    messages = operation.get("messages")
    if isinstance(messages, list) and messages and isinstance(messages[0], Mapping):
        return str(messages[0].get("$ref", "")).rstrip("/").rsplit("/", 1)[-1]
    return ""


def _protocols(document: Mapping[str, Any]) -> set[str]:
    servers = document.get("servers")
    if not isinstance(servers, Mapping):
        return set()
    return {
        str(server.get("protocol", ""))
        for server in servers.values()
        if isinstance(server, Mapping) and server.get("protocol")
    }


# --- registration --------------------------------------------------------

DISCOVERERS = (
    (
        "slpie.asyncapi", discover_asyncapi,
        ("asyncapi.json", "asyncapi.yaml", "asyncapi.yml",
         "*.asyncapi.json", "*.asyncapi.yaml", "*.asyncapi.yml"),
        ("publishes", "subscribes", "declares"),
        (EvidenceKind.MANIFEST_DECLARED,),
    ),
)
