"""The agent-interoperability protocols, recorded with their provenance.

The paper's own words: agents increasingly "interact with other agents", and
"protocols for how agents can communicate with each other […] should reduce the
need for so much" bespoke integration. That makes the protocol landscape part of
the reasoning surface, not trivia — a reuse decision about an agent component
turns on which protocol it speaks.

**One rule governs this module: no entry without a spec URL.** It is the same
discipline as evidence on a graph edge. A registry of protocols assembled from
memory, with no citation, is exactly the artefact that ages into confident
wrongness — and this field moves fast enough that an uncited entry is wrong
within a quarter. Every record therefore names where it can be checked, and
`Protocol.recorded` says when it was written down so a reader can judge how much
to trust it.

**Ownership is recorded precisely, because it is routinely misattributed.** A2A
came from Google and was donated to the Linux Foundation; Microsoft is an
*adopter* of it, not its author. MCP is Anthropic's. Microsoft's own framework
is the Agent Framework, which speaks both. Getting this wrong is not pedantry:
it decides which spec you read, which governance body you file an issue with,
and whose deprecation notice affects you.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator, Mapping, Sequence

from ..errors import GratimosError


class ReferenceError(GratimosError):
    """A reference entry was declared without the evidence it requires."""


class Governance(str, Enum):
    """Who decides what the spec says. Changes the risk of adopting it."""

    FOUNDATION = "foundation"      # a neutral body: Linux Foundation, CNCF
    VENDOR_OPEN = "vendor_open"    # one company, published openly
    VENDOR = "vendor"              # one company, and its roadmap
    COMMUNITY = "community"

    @property
    def single_vendor_risk(self) -> bool:
        return self in (Governance.VENDOR, Governance.VENDOR_OPEN)


class Layer(str, Enum):
    """What problem the protocol actually solves. They are not substitutes."""

    AGENT_TO_AGENT = "agent_to_agent"     # peers delegating work
    AGENT_TO_TOOL = "agent_to_tool"       # an agent reaching a capability
    ORCHESTRATION = "orchestration"       # building and running agents
    DISCOVERY = "discovery"               # finding agents at all


@dataclass(frozen=True, slots=True)
class Protocol:
    """One interoperability protocol, as recorded — with where to check it."""

    id: str
    name: str
    owner: str
    governance: Governance
    layer: Layer
    spec: str
    summary: str = ""
    transport: tuple[str, ...] = ()
    concepts: tuple[str, ...] = ()
    adopters: tuple[str, ...] = ()
    speaks: tuple[str, ...] = ()      # other protocols it interoperates with
    recorded: str = ""                # when this entry was written down
    note: str = ""

    def __post_init__(self) -> None:
        if not self.spec.startswith("http"):
            raise ReferenceError(
                f"protocol {self.id!r} cites no specification URL; an entry "
                f"nobody can check is one that ages into confident wrongness"
            )
        if not self.summary.strip():
            raise ReferenceError(f"protocol {self.id!r} has no summary")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "owner": self.owner,
            "governance": self.governance.value, "layer": self.layer.value,
            "spec": self.spec, "summary": self.summary,
            "transport": list(self.transport), "concepts": list(self.concepts),
            "adopters": list(self.adopters), "speaks": list(self.speaks),
            "recorded": self.recorded, "note": self.note,
        }

    def __str__(self) -> str:
        return f"{self.name} ({self.owner}, {self.governance.value})"


#: When these entries were written down. Shown beside every record, because the
#: honest thing to say about a fast-moving landscape is when you last looked.
RECORDED = "2026-07"


def protocol_registry() -> "Registry":
    """The protocols an agent platform has to know about, with citations."""
    return Registry(
        Protocol(
            id="a2a",
            name="A2A (Agent2Agent)",
            owner="Google, donated to the Linux Foundation",
            governance=Governance.FOUNDATION,
            layer=Layer.AGENT_TO_AGENT,
            spec="https://a2a-protocol.org/",
            summary=(
                "Peer agents delegate tasks to each other over JSON-RPC, "
                "discovering one another through a published Agent Card."
            ),
            transport=("JSON-RPC 2.0 over HTTPS", "SSE for streaming",
                       "webhook push notifications"),
            concepts=("AgentCard", "AgentSkill", "Task", "TaskState", "Message",
                      "Part", "Artifact", "contextId"),
            adopters=("Microsoft (Azure AI Foundry, Copilot Studio)", "SAP",
                      "Salesforce", "ServiceNow", "UiPath"),
            recorded=RECORDED,
            note=(
                "Frequently misattributed to Microsoft, which is an adopter "
                "rather than the author. Gratimos implements this in "
                "`gratimos.a2a`, including the /.well-known/agent-card.json "
                "discovery path."
            ),
        ),
        Protocol(
            id="mcp",
            name="MCP (Model Context Protocol)",
            owner="Anthropic",
            governance=Governance.VENDOR_OPEN,
            layer=Layer.AGENT_TO_TOOL,
            spec="https://modelcontextprotocol.io/",
            summary=(
                "A model reaches tools, resources and prompts through one "
                "protocol, so an integration written once serves every client."
            ),
            transport=("JSON-RPC 2.0 over stdio", "streamable HTTP"),
            concepts=("tools", "resources", "prompts", "sampling", "roots",
                      "elicitation"),
            adopters=("OpenAI", "Google DeepMind", "Microsoft", "JetBrains"),
            speaks=(),
            recorded=RECORDED,
            note=(
                "Solves a different problem from A2A and is not an alternative "
                "to it: MCP connects an agent to capabilities, A2A connects "
                "agents to each other. Most real deployments use both."
            ),
        ),
        Protocol(
            id="microsoft-agent-framework",
            name="Microsoft Agent Framework",
            owner="Microsoft",
            governance=Governance.VENDOR_OPEN,
            layer=Layer.ORCHESTRATION,
            spec="https://learn.microsoft.com/en-us/agent-framework/",
            summary=(
                "Microsoft's SDK for building and running agents, converging "
                "Semantic Kernel and AutoGen into one supported line."
            ),
            transport=("in-process SDK", "Azure AI Foundry hosting"),
            concepts=("agents", "workflows", "threads", "middleware",
                      "observability"),
            speaks=("mcp", "a2a"),
            recorded=RECORDED,
            note=(
                "This is the Microsoft SDK in this landscape — it *speaks* A2A "
                "and MCP rather than defining either."
            ),
        ),
        Protocol(
            id="openai-agents-sdk",
            name="OpenAI Agents SDK",
            owner="OpenAI",
            governance=Governance.VENDOR_OPEN,
            layer=Layer.ORCHESTRATION,
            spec="https://openai.github.io/openai-agents-python/",
            summary=(
                "Agents, handoffs between them, guardrails and tracing, as a "
                "thin library rather than a framework."
            ),
            transport=("in-process SDK",),
            concepts=("Agent", "handoff", "guardrail", "session", "tracing"),
            speaks=("mcp",),
            recorded=RECORDED,
            note="Successor to the Swarm experiment.",
        ),
        Protocol(
            id="google-adk",
            name="Google Agent Development Kit",
            owner="Google",
            governance=Governance.VENDOR_OPEN,
            layer=Layer.ORCHESTRATION,
            spec="https://google.github.io/adk-docs/",
            summary="Google's SDK for building agents that expose and consume A2A.",
            transport=("in-process SDK", "Vertex AI Agent Engine"),
            concepts=("Agent", "tools", "sessions", "evaluation"),
            speaks=("a2a", "mcp"),
            recorded=RECORDED,
        ),
        Protocol(
            id="acp",
            name="ACP (Agent Communication Protocol)",
            owner="IBM / BeeAI, under the Linux Foundation",
            governance=Governance.FOUNDATION,
            layer=Layer.AGENT_TO_AGENT,
            spec="https://agentcommunicationprotocol.dev/",
            summary=(
                "A REST-shaped alternative for agent-to-agent messaging, with "
                "multipart messages and offline discovery."
            ),
            transport=("REST over HTTP",),
            concepts=("agent detail", "run", "await", "message parts"),
            recorded=RECORDED,
            note=(
                "Overlaps A2A substantially and has been converging with it "
                "under common governance; check current status before adopting."
            ),
        ),
        Protocol(
            id="agntcy",
            name="AGNTCY / Internet of Agents",
            owner="Cisco-led collective, Linux Foundation",
            governance=Governance.FOUNDATION,
            layer=Layer.DISCOVERY,
            spec="https://agntcy.org/",
            summary=(
                "Identity, directory and messaging for agents across "
                "organisational boundaries — the discovery layer the others assume."
            ),
            transport=("varies by component",),
            concepts=("agent directory", "OASF schema", "agent identity"),
            recorded=RECORDED,
        ),
    )


class Registry:
    """The protocols, addressable by id, layer or what they interoperate with."""

    def __init__(self, *protocols: Protocol) -> None:
        self._protocols: dict[str, Protocol] = {}
        for protocol in protocols:
            self.add(protocol)

    def add(self, protocol: Protocol) -> Protocol:
        if protocol.id in self._protocols:
            raise ReferenceError(f"protocol {protocol.id!r} is already recorded")
        self._protocols[protocol.id] = protocol
        return protocol

    def get(self, identifier: str) -> Protocol | None:
        return self._protocols.get(identifier)

    def require(self, identifier: str) -> Protocol:
        protocol = self._protocols.get(identifier)
        if protocol is None:
            raise ReferenceError(
                f"no protocol recorded as {identifier!r} "
                f"(known: {', '.join(sorted(self._protocols))})"
            )
        return protocol

    def at_layer(self, layer: Layer) -> tuple[Protocol, ...]:
        return tuple(p for p in self._protocols.values() if p.layer is layer)

    def by_owner(self, fragment: str) -> tuple[Protocol, ...]:
        needle = fragment.lower()
        return tuple(p for p in self._protocols.values() if needle in p.owner.lower())

    def interoperating_with(self, identifier: str) -> tuple[Protocol, ...]:
        """Everything recorded as speaking a given protocol."""
        return tuple(p for p in self._protocols.values() if identifier in p.speaks)

    def foundation_governed(self) -> tuple[Protocol, ...]:
        """The ones no single vendor can unilaterally change."""
        return tuple(
            p for p in self._protocols.values()
            if p.governance is Governance.FOUNDATION
        )

    def sources(self) -> tuple[str, ...]:
        """Every specification this registry rests on. Read before relying on it."""
        return tuple(sorted({p.spec for p in self._protocols.values()}))

    def render(self) -> str:
        lines = [f"{len(self._protocols)} protocols recorded {RECORDED}"]
        for layer in Layer:
            at = self.at_layer(layer)
            if not at:
                continue
            lines.append(f"  {layer.value}:")
            for protocol in sorted(at, key=lambda p: p.id):
                lines.append(f"    {protocol}")
                lines.append(f"      {protocol.summary}")
                lines.append(f"      spec: {protocol.spec}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "recorded": RECORDED,
            "protocols": [p.to_dict() for p in self._protocols.values()],
            "sources": list(self.sources()),
        }

    def __len__(self) -> int:
        return len(self._protocols)

    def __contains__(self, identifier: object) -> bool:
        return identifier in self._protocols

    def __iter__(self) -> Iterator[Protocol]:
        return iter(tuple(self._protocols.values()))

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<protocol Registry {sorted(self._protocols)}>"
