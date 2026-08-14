"""The validation bottleneck, as vocabulary this system can reason with.

From *Conjecture Machines: AI agents and the new validation bottleneck in
science* (Google DeepMind, July 2026). Its argument in one line: agents are
making **ideas and candidate solutions abundant and cheap**, while validation
stays "physical, human and institutional — and so, costly and slow", and the
gap "in most disciplines is widening, not closing".

That is the same shape as this codebase's own problem. The crawler can propose
reuse candidates far faster than any validator confirms them; the shell layer
can propose commands far faster than anyone reviews them. So the paper's
vocabulary is imported here as *terms with definitions and sources*, and the
mechanisms it recommends are recorded next to the mechanism in this system that
answers them — or next to the admission that nothing does yet.

Two entries deserve the attention:

* **Calibration.** The paper points at AlphaFold, whose per-residue confidence
  lets researchers "know when to trust it and when to reach for other methods",
  and asks that agents "know when they don't know, and say so". Gratimos derives
  confidence from evidence but has never *measured* whether 0.8 means right
  eighty percent of the time. That is recorded below as an open gap, not as a
  solved problem.
* **Provenance records.** The paper asks for "short records detailing the
  prompts and outputs that produced the" result, so peer review can check agent
  work. Gratimos records domain events and reasoning paths but not agent runs.
  Also recorded as open.

Naming the gaps in the same registry as the answers is the point. A reference
that lists only what we already do is marketing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator, Mapping, Sequence

from .protocols import ReferenceError

#: The single source every entry here traces back to.
PAPER = (
    "https://deepmind.google/public-policy/"
    "conjecture-machines-ai-agents-and-the-new-validation-bottleneck-in-science/"
)


class Answered(str, Enum):
    """Whether this system addresses the idea, and how honestly.

    `OPEN` entries are the useful ones. A registry where everything is
    `IMPLEMENTED` has stopped being a map and become a brochure.
    """

    IMPLEMENTED = "implemented"    # a named module does this
    PARTIAL = "partial"            # some of it, with a stated shortfall
    OPEN = "open"                  # recognised, not built
    NOT_APPLICABLE = "n/a"         # about wet labs or policy, not software


@dataclass(frozen=True, slots=True)
class Term:
    """One idea from the paper, defined, sourced, and mapped to our code."""

    key: str
    name: str
    definition: str
    matters: str
    answered: Answered = Answered.OPEN
    module: str = ""
    source: str = PAPER

    def __post_init__(self) -> None:
        if not self.definition.strip():
            raise ReferenceError(f"term {self.key!r} has no definition")
        if self.answered in (Answered.IMPLEMENTED, Answered.PARTIAL) and not self.module:
            raise ReferenceError(
                f"term {self.key!r} claims to be {self.answered.value} but names "
                f"no module; an unlocatable claim is not a claim"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "name": self.name, "definition": self.definition,
            "matters": self.matters, "answered": self.answered.value,
            "module": self.module, "source": self.source,
        }

    def __str__(self) -> str:
        where = f" → {self.module}" if self.module else ""
        return f"{self.name} [{self.answered.value}]{where}"


@dataclass(frozen=True, slots=True)
class Tool:
    """A system the paper cites, and why it cites it."""

    key: str
    name: str
    org: str
    what: str
    cited_for: str
    url: str = ""

    def __post_init__(self) -> None:
        if not self.cited_for.strip():
            raise ReferenceError(
                f"tool {self.key!r} records no reason it was cited; a name with "
                f"no argument attached is trivia"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "name": self.name, "org": self.org,
            "what": self.what, "cited_for": self.cited_for, "url": self.url,
        }

    def __str__(self) -> str:
        return f"{self.name} ({self.org}) — {self.cited_for}"


def terminology() -> tuple[Term, ...]:
    """The paper's vocabulary, with our answer or our admission beside each."""
    return (
        Term(
            key="conjecture-machine",
            name="Conjecture machine",
            definition=(
                "An agent that generates hypotheses, proofs and candidate "
                "solutions far faster than any human team could."
            ),
            matters=(
                "It moves the constraint. When conjectures are cheap, the "
                "scarce thing is whatever checks them."
            ),
            answered=Answered.PARTIAL,
            module="gratimos.crawl, gratimos.reason",
        ),
        Term(
            key="validation-bottleneck",
            name="Validation bottleneck",
            definition=(
                "Validation remains physical, human and institutional — and so "
                "costly and slow — while generation becomes cheap; the gap is "
                "widening, not closing."
            ),
            matters=(
                "Every design decision about throughput is really a decision "
                "about where the validation queue forms."
            ),
            answered=Answered.PARTIAL,
            module="gratimos.distill.loop",
        ),
        Term(
            key="proof-indigestion",
            name="Proof indigestion",
            definition=(
                "More generated output than the field can read, review or "
                "absorb — proofs 'too long and hard to parse'."
            ),
            matters=(
                "An answer nobody has capacity to check is not knowledge yet. "
                "It is the reason ranked, explained output beats more output."
            ),
            answered=Answered.PARTIAL,
            module="gratimos.reuse.assess",
        ),
        Term(
            key="in-silico-validation",
            name="In-silico validation",
            definition=(
                "Checking a result computationally rather than at a bench — a "
                "proof expressed in a formal language and machine-verified."
            ),
            matters=(
                "The only part of validation that scales with compute. Where a "
                "claim can be checked in software, the bottleneck disappears."
            ),
            answered=Answered.IMPLEMENTED,
            module="gratimos.shell.guard, slpie.rbac",
        ),
        Term(
            key="generator-verifier-pairing",
            name="Generator/verifier pairing",
            definition=(
                "Pairing a system that proposes with a separate one that checks, "
                "as Aletheia pairs a proof generator with a verifier."
            ),
            matters=(
                "Separation is the whole value. A generator that grades its own "
                "work produces confidence, not correctness."
            ),
            answered=Answered.IMPLEMENTED,
            module="gratimos.distill.loop, gratimos.shell.guard",
        ),
        Term(
            key="calibration",
            name="Calibration",
            definition=(
                "An agent that knows when it does not know and says so — "
                "confidence that tracks the actual hit rate, as AlphaFold's "
                "per-residue score does."
            ),
            matters=(
                "Uncalibrated confidence is worse than none: it moves work to "
                "wherever the number is wrong."
            ),
            answered=Answered.OPEN,
            module="",
        ),
        Term(
            key="provenance-record",
            name="Agent run record",
            definition=(
                "Short records detailing the prompts and outputs that produced a "
                "result, so a reviewer can check agent work."
            ),
            matters=(
                "Peer review cannot assess what it cannot inspect, and writing "
                "quality has stopped being a discriminator."
            ),
            answered=Answered.OPEN,
            module="",
        ),
        Term(
            key="agent-ready-data",
            name="Agent-ready data",
            definition=(
                "Data held to interoperable standards and reachable under secure "
                "infrastructure, so an agent can work it without a human courier."
            ),
            matters=(
                "'A dataset analysis that currently takes years of doctoral work "
                "could, with the right secure agent infrastructure, run "
                "autonomously in days.'"
            ),
            answered=Answered.PARTIAL,
            module="slpie.connectors, slpie.identity",
        ),
        Term(
            key="agent-interoperability",
            name="Agent interoperability protocols",
            definition=(
                "Agreed protocols for how agents communicate, reducing bespoke "
                "integration between every pair."
            ),
            matters=(
                "Integration cost is what stops specialist tools being reachable "
                "from a general agent."
            ),
            answered=Answered.IMPLEMENTED,
            module="gratimos.a2a",
        ),
        Term(
            key="scaffolding",
            name="Scaffolding",
            definition=(
                "The middle tier of the paper's three-level pyramid — frontier "
                "models at the base, scaffolding above them, customisation on "
                "top — the harness that turns a model into an agent that can "
                "call databases, run tools and write code."
            ),
            matters=(
                "It is where reliability is won or lost, and it is the tier this "
                "codebase occupies."
            ),
            answered=Answered.IMPLEMENTED,
            module="gratimos.runtime, gratimos.shell",
        ),
        Term(
            key="customisation",
            name="Customisation",
            definition=(
                "The pyramid's top tier: agents users build to their own "
                "detailed specifications, accumulating a scientist's own context "
                "as they are used."
            ),
            matters=(
                "The paper's own example is a researcher who 'has distilled "
                "aspects of her research' into her agent — which is a knowledge "
                "base, not a prompt."
            ),
            answered=Answered.IMPLEMENTED,
            module="gratimos.runtime.plugins, gratimos.distill",
        ),
        Term(
            key="collaborator-not-oracle",
            name="Collaborator, not oracle",
            definition=(
                "Agents that reason with a person rather than returning verdicts; "
                "the paper pairs this with 'structured periods of agent-free "
                "work' in graduate training."
            ),
            matters=(
                "An oracle produces answers nobody can interrogate, which is the "
                "same failure as an unreviewable proof."
            ),
            answered=Answered.PARTIAL,
            module="gratimos.reuse.assess",
        ),
        Term(
            key="lab-sets-the-pace",
            name="The lab sets the pace",
            definition=(
                "'Cell lines need time to grow, chemical reactions take time to "
                "complete.' Physical validation cannot be compressed by compute."
            ),
            matters=(
                "The honest limit on automation. Some queues cannot be "
                "parallelised, only prioritised."
            ),
            answered=Answered.NOT_APPLICABLE,
        ),
    )


def tools() -> tuple[Tool, ...]:
    """Every system the paper names, and the argument each was named for."""
    return (
        Tool(
            key="co-scientist", name="AI Co-Scientist", org="Google DeepMind",
            what="A multi-agent system that proposes and ranks research hypotheses.",
            cited_for=(
                "Reproduced in days a superbug antibiotic-resistance mechanism "
                "that took José Penadés' Imperial College lab most of a decade — "
                "from unpublished work absent from any training data."
            ),
            url="https://research.google/blog/accelerating-scientific-breakthroughs-with-an-ai-co-scientist/",
        ),
        Tool(
            key="alphafold", name="AlphaFold", org="Google DeepMind",
            what="Protein-structure prediction with per-prediction confidence scores.",
            cited_for=(
                "The calibration exemplar: its confidence lets researchers know "
                "when to trust it and when to reach for another method."
            ),
            url="https://deepmind.google/science/alphafold/",
        ),
        Tool(
            key="aletheia", name="Aletheia", org="—",
            what="Pairs a proof generator with a verifier.",
            cited_for=(
                "The generator/verifier pattern — the architecture that makes "
                "in-silico validation trustworthy."
            ),
        ),
        Tool(
            key="lean", name="Lean", org="Lean FRO",
            what="A proof assistant and formal language with machine-checked proofs.",
            cited_for=(
                "Where a claim can be expressed formally, validation runs in "
                "silico and stops being the bottleneck."
            ),
            url="https://lean-lang.org/",
        ),
        Tool(
            key="frontiermath", name="FrontierMath", org="Epoch AI",
            what="A benchmark of unpublished research-level mathematics problems.",
            cited_for="Evidence that frontier reasoning has genuinely moved.",
            url="https://epoch.ai/frontiermath",
        ),
        Tool(
            key="first-proof", name="First Proof challenge", org="—",
            what="Research problems kept unpublished so they are absent from training data.",
            cited_for=(
                "Contamination control — the only way a benchmark result about "
                "novel reasoning means anything."
            ),
        ),
        Tool(
            key="deep-research", name="Gemini Deep Research", org="Google",
            what="An agent that plans, searches and synthesises a literature review.",
            cited_for="Ideation and literature work compressed from days to minutes.",
            url="https://gemini.google/overview/deep-research/",
        ),
        Tool(
            key="deep-think", name="Gemini Deep Think", org="Google DeepMind",
            what="Inference-time reasoning applied to mathematical and scientific discovery.",
            cited_for="The 'stronger frontier models' base of the pyramid.",
            url="https://deepmind.google/blog/accelerating-mathematical-and-scientific-discovery-with-gemini-deep-think/",
        ),
        Tool(
            key="opensafely", name="OpenSAFELY", org="University of Oxford / NHS",
            what=(
                "Analysis runs inside the data's secure environment; code is "
                "published, the records never leave."
            ),
            cited_for=(
                "The model for making sensitive national data agent-ready "
                "without moving it."
            ),
            url="https://www.opensafely.org/",
        ),
        Tool(
            key="genesis-mission", name="Genesis Mission", org="US Department of Energy",
            what="A national programme coupling compute, data and automated labs.",
            cited_for=(
                "Cited twice: as a funding model for agent compute, and for the "
                "build-out of automated laboratories at the DOE national labs."
            ),
        ),
        Tool(
            key="crick", name="Francis Crick Institute", org="—",
            what="A biomedical research institute running an automated wet lab.",
            cited_for="Automated labs as the answer to physical validation throughput.",
            url="https://www.crick.ac.uk/",
        ),
    )


class Reference:
    """The paper's vocabulary and citations, queryable."""

    def __init__(self) -> None:
        self._terms = {term.key: term for term in terminology()}
        self._tools = {tool.key: tool for tool in tools()}

    def term(self, key: str) -> Term | None:
        return self._terms.get(key)

    def tool(self, key: str) -> Tool | None:
        return self._tools.get(key)

    @property
    def terms(self) -> tuple[Term, ...]:
        return tuple(self._terms.values())

    @property
    def named_tools(self) -> tuple[Tool, ...]:
        return tuple(self._tools.values())

    def answered(self, state: Answered) -> tuple[Term, ...]:
        return tuple(term for term in self._terms.values() if term.answered is state)

    def gaps(self) -> tuple[Term, ...]:
        """What the paper asks for that this system does not do. The useful list."""
        return self.answered(Answered.OPEN)

    def priorities(self) -> tuple[str, ...]:
        """The four the paper sets out for policymakers, in its own order."""
        return (
            "Ensure widespread access to agents",
            "Make national data assets agent-ready",
            "Build out automated laboratories",
            "Empower peer reviewers with agents",
        )

    def render(self) -> str:
        lines = [f"Conjecture Machines — {len(self._terms)} terms, "
                 f"{len(self._tools)} systems cited", f"  source: {PAPER}", ""]
        lines.append("  four priorities:")
        lines.extend(f"    {index}. {item}"
                     for index, item in enumerate(self.priorities(), start=1))
        lines.append("")
        lines.append("  terminology:")
        for term in sorted(self._terms.values(), key=lambda t: t.answered.value):
            lines.append(f"    {term}")
        gaps = self.gaps()
        if gaps:
            lines.append("")
            lines.append("  what we do not answer yet:")
            lines.extend(f"    · {term.name}: {term.matters}" for term in gaps)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": PAPER,
            "priorities": list(self.priorities()),
            "terms": [term.to_dict() for term in self._terms.values()],
            "tools": [tool.to_dict() for tool in self._tools.values()],
            "gaps": [term.key for term in self.gaps()],
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Reference {len(self._terms)} terms, {len(self.gaps())} gaps>"
