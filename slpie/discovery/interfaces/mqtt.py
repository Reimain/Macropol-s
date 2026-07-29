"""MQTT — topics, wildcards, and the device classes behind them.

The environment manifest already declares IoT brokers and device classes
(`iot:` in `slpie.environment.yaml`). This reads the configuration files that
say which topics are actually used, so the declared world and the observed one
can be reconciled — an IoT estate's real problem is rarely the broker, it is the
forty topics nobody documented.

The wildcards are the whole difficulty. MQTT has two, and they are not
interchangeable:

    fleet/+/temperature     `+` matches exactly one level
    fleet/#                 `#` matches this level and everything below

A subscription to `fleet/#` covers `fleet/berlin/van-7/temperature`; one to
`fleet/+` does not. Treating them as the same wildcard — which `fnmatch` and a
naive regex both do — produces a graph where a narrow subscriber appears to
receive everything, and the blast radius of a topic change is wrong in the
direction that hides problems.

So `matches()` implements the rule properly, and a filter containing a wildcard
is recorded as a **filter**, never as a concrete topic: `fleet/+/temperature`
is a pattern that names no single thing, and making it a node that publishers
connect to would invent a hub that does not exist on the wire.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ...domain.evidence import EvidenceKind
from ...domain.identity import Urn
from ...environment.schema import parse_yaml
from ...plugins.protocol import DiscoveryResult, Observation
from ..base import Source, declares, evidence_at, find_line, result, scope_of

EXTRACTOR = "slpie.mqtt"

#: Matches exactly one topic level.
SINGLE = "+"

#: Matches this level and every level below. Only legal as the last segment.
MULTI = "#"

#: Keys under which a configuration names the topics it publishes to.
PUBLISH_KEYS = ("publish", "publishes", "produce", "produces", "outbound", "topics_out")

#: ...and subscribes to.
SUBSCRIBE_KEYS = ("subscribe", "subscribes", "consume", "consumes", "inbound", "topics_in")


def is_filter(topic: str) -> bool:
    """Whether this names a pattern rather than one topic."""
    return SINGLE in topic.split("/") or topic.split("/")[-1] == MULTI


def valid(topic: str) -> tuple[bool, str]:
    """Whether a filter is legal, and why not when it is not.

    `#` is only legal as the final segment. `fleet/#/temperature` is not a
    broader subscription — it is rejected by the broker, so recording it as a
    working one would describe an estate that cannot exist.
    """
    if not topic:
        return False, "an empty topic filter matches nothing"
    segments = topic.split("/")
    for index, segment in enumerate(segments):
        if MULTI in segment and segment != MULTI:
            return False, f"'{MULTI}' must be a whole level, not part of {segment!r}"
        if segment == MULTI and index != len(segments) - 1:
            return False, f"'{MULTI}' is only legal as the last level of {topic!r}"
        if SINGLE in segment and segment != SINGLE:
            return False, f"'{SINGLE}' must be a whole level, not part of {segment!r}"
    return True, ""


def matches(pattern: str, topic: str) -> bool:
    """Whether an MQTT filter covers a concrete topic.

    Implemented level by level rather than by translating to a regular
    expression, because the two wildcards differ in exactly the way a regex
    translation tends to flatten: `+` consumes one level and `#` consumes the
    rest. Getting that wrong makes a narrow subscriber look like it receives
    everything.
    """
    wanted = pattern.split("/")
    actual = topic.split("/")

    for index, segment in enumerate(wanted):
        if segment == MULTI:
            # Includes the parent level: the specification (MQTT 4.7.1.2) says
            # `sport/#` also matches the singular `sport`. Surprising enough
            # that an implementation which "fixes" it is the one that is wrong.
            return True
        if index >= len(actual):
            return False
        if segment != SINGLE and segment != actual[index]:
            return False
    return len(wanted) == len(actual)


def topic_urn(broker: str, topic: str) -> Urn:
    return Urn.create("topic", broker, topic)


def device_urn(fleet: str, name: str) -> Urn:
    return Urn.create("deviceclass", fleet, name)


def _named(block: Any, keys: Iterable[str]) -> list[str]:
    """Pull topic strings out of whichever key a configuration happens to use."""
    found: list[str] = []
    if not isinstance(block, Mapping):
        return found
    for key in keys:
        value = block.get(key)
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, list):
            found.extend(str(item) for item in value if isinstance(item, (str, int)))
        elif isinstance(value, Mapping):
            found.extend(str(name) for name in value)
    return found


def discover_config(source: Source) -> DiscoveryResult:
    """Read an MQTT configuration into topics, filters and device classes."""
    try:
        document = parse_yaml(source.text)
    except Exception as error:  # noqa: BLE001 - the reader raises its own types
        return result((), errors=[f"{source.uri}: not readable as YAML ({error})"])

    if not isinstance(document, Mapping):
        return result(())

    block = document.get("mqtt") if isinstance(document.get("mqtt"), Mapping) else document
    if not isinstance(block, Mapping):
        return result(())

    broker = str(block.get("broker") or block.get("host") or "").strip()
    if not broker and not any(
        key in block for key in (*PUBLISH_KEYS, *SUBSCRIBE_KEYS, "device_classes")
    ):
        # Not an MQTT configuration. This discoverer claims broad filenames, so
        # declining quietly matters more than reporting.
        return result(())

    scope = scope_of(source.element, "iot")
    broker_name = broker or scope
    observations: list[Observation] = []
    errors: list[str] = []
    service = Urn.create("service", scope, str(block.get("client_id") or "mqtt")).to_string()

    def cite(needle: str, excerpt: str):
        return evidence_at(
            source.uri, kind=EvidenceKind.CONFIG_REFERENCE, extractor=EXTRACTOR,
            line=find_line(source.text, needle), excerpt=excerpt[:200],
            digest=source.digest,
        )

    if broker:
        observations.append(declares(
            Urn.create("broker", broker_name).to_string(),
            cite(broker, f"broker: {broker}"),
            node_kind="broker", protocol="mqtt",
        ))

    for kind, keys in (("publishes", PUBLISH_KEYS), ("subscribes", SUBSCRIBE_KEYS)):
        for topic in _named(block, keys):
            legal, reason = valid(topic)
            if not legal:
                errors.append(f"{source.uri}: {reason}")
                continue

            evidence = cite(topic, f"{kind}: {topic}")
            subject = topic_urn(broker_name, topic).to_string()
            observations.append(declares(
                subject, evidence,
                node_kind="topic", protocol="mqtt",
                # A filter names a pattern, not a thing on the wire. Saying so
                # stops it being treated as a topic that publishers reach.
                is_filter=is_filter(topic),
                wildcard=MULTI if topic.split("/")[-1] == MULTI else (
                    SINGLE if SINGLE in topic.split("/") else ""
                ),
                levels=len(topic.split("/")),
            ))
            observations.append(Observation(
                kind=kind, subject=service, object=subject,
                evidence=evidence, properties={"protocol": "mqtt"},
            ))

    classes = block.get("device_classes") or block.get("devices")
    if isinstance(classes, list):
        for entry in classes:
            name = str(entry) if isinstance(entry, (str, int)) else str(
                entry.get("name", "") if isinstance(entry, Mapping) else ""
            )
            if not name:
                continue
            observations.append(declares(
                device_urn(broker_name, name).to_string(),
                cite(name, f"device class: {name}"),
                node_kind="deviceclass", protocol="mqtt",
            ))

    return result(observations, errors=errors)


DISCOVERERS = (
    (
        "slpie.mqtt", discover_config,
        ("*.mqtt.yaml", "*.mqtt.yml", "mqtt.yaml", "mqtt.yml", "mosquitto.conf.yaml"),
        ("declares", "publishes", "subscribes"),
        (EvidenceKind.CONFIG_REFERENCE,),
    ),
)
