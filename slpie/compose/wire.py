"""Carrying a flow between processes, so `slpie a | slpie b` is real.

Two pipe forms exist and they are not equivalent, which is worth being explicit
about rather than letting somebody discover it:

* **the internal pipe** — `slpie 'discover . | link'`. One process, full object
  fidelity, no serialisation cost per stage. This is the primary form and the one
  the manual teaches.
* **the OS pipe** — `slpie discover . | slpie link`. Two processes, and the flow
  crosses as JSON. Anything that cannot round-trip through JSON *cannot* cross,
  and this module refuses rather than degrading.

That refusal is the decision worth defending. A silent degradation here would be
the worst kind: the downstream verb would receive dicts where it expected domain
objects, and instead of failing it would produce a plausible, wrong answer from
attributes that happened to be missing. So `rehydrate` knows exactly which kinds
can be reconstructed, and says so by name for the ones that cannot — pointing at
the internal form, which always works.

`OBSERVATIONS` round-trips because `Observation.from_dict` already exists for the
out-of-process plugin protocol (§6). That is the same problem — a domain object
crossing a process boundary as JSON — so it is the same solution rather than a
second one.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from ..domain.evidence import Evidence, EvidenceKind
from ..domain.finding import Gap, GapKind
from ..domain.reasoning import ReasoningPath, ReasoningStep
from ..errors import SlpieError
from .flow import Flow, Kind

#: The marker that tells a downstream `slpie` its stdin is a flow rather than
#: arbitrary text. Without it, piping a text file in would be read as a flow and
#: fail obscurely instead of clearly.
ENVELOPE = "slpie/flow/v1"

#: Kinds that can cross a process boundary and be reconstructed faithfully.
CROSSABLE = frozenset({
    Kind.OBSERVATIONS, Kind.GAPS, Kind.REPORT, Kind.TEXT, Kind.NOTHING,
})


class WireError(SlpieError):
    """A flow could not cross a process boundary."""


def encode(flow: Flow, *, limit: int = 100_000) -> str:
    """One flow → one line of JSON, for stdout.

    Refuses a kind that could not be read back. Emitting it would succeed here and
    fail in the *next* process, which puts the error one command away from its
    cause; and `--wire` promises the output is consumable by another `slpie`, so
    producing something that is not is the wrong kind of cooperative.
    """
    if flow.kind not in CROSSABLE:
        raise WireError(
            f"a {flow.kind.label} flow cannot be written to a pipe for another "
            f"`slpie` to read — it holds objects JSON cannot reconstruct "
            f"faithfully. Use --json for a readable rendering, or keep the "
            f"pipeline internal: slpie '... | <next verb>'"
        )
    return json.dumps({
        "envelope": ENVELOPE,
        "kind": flow.kind.value,
        "stages": list(flow.stages),
        "facts": dict(flow.facts),
        "digest": flow.digest,
        "gaps": [gap.to_dict() for gap in flow.gaps],
        "reasoning": [_step_out(step) for step in flow.reasoning.steps],
        "value": _value_out(flow),
    }, default=str)[:limit * 40]


def decode(text: str) -> Flow:
    """One line of JSON → the flow it encoded, reconstructed."""
    stripped = (text or "").strip()
    if not stripped:
        return Flow.start()
    try:
        body = json.loads(stripped)
    except ValueError as error:
        raise WireError(
            f"stdin is not a slpie flow: {error}. If you meant to pass a path, "
            f"pass it as an argument rather than on stdin"
        ) from None
    if not isinstance(body, Mapping) or body.get("envelope") != ENVELOPE:
        raise WireError(
            "stdin is not a slpie flow; it has no slpie envelope. Pipe from "
            "another `slpie` command, or use the internal form: "
            "slpie '<verb> | <verb>'"
        )

    try:
        kind = Kind(body.get("kind", "nothing"))
    except ValueError:
        raise WireError(f"unknown flow kind {body.get('kind')!r}") from None

    return Flow(
        kind=kind,
        value=rehydrate(kind, body.get("value")),
        reasoning=ReasoningPath(steps=tuple(
            _step_in(item) for item in body.get("reasoning", [])
        )),
        gaps=tuple(_gap_in(item) for item in body.get("gaps", [])),
        stages=tuple(body.get("stages", ())),
        facts=dict(body.get("facts", {})),
    )


def rehydrate(kind: Kind, value: Any) -> Any:
    """JSON → domain objects, or a refusal naming the kind that cannot cross."""
    if kind not in CROSSABLE:
        raise WireError(
            f"a {kind.label} flow cannot cross a process boundary — it holds "
            f"objects that JSON cannot reconstruct faithfully, and handing the "
            f"next verb half-built objects would produce a plausible wrong "
            f"answer rather than an error. Use the internal pipe instead: "
            f"slpie '... | <next verb>'"
        )

    if kind is Kind.OBSERVATIONS:
        from ..plugins.protocol import Observation

        found = []
        for item in value or ():
            if not isinstance(item, Mapping):
                continue
            try:
                found.append(Observation.from_dict(
                    item, plugin_id="slpie.wire",
                    allowed=tuple(EvidenceKind),
                ))
            except Exception as error:  # noqa: BLE001 - protocol raises its own
                raise WireError(
                    f"an observation on stdin could not be read: {error}"
                ) from None
        return tuple(found)

    if kind is Kind.GAPS:
        return tuple(_gap_in(item) for item in value or ())

    return value


# --- element codecs ------------------------------------------------------


def _value_out(flow: Flow) -> Any:
    if flow.kind is Kind.OBSERVATIONS:
        return [item.to_dict() for item in flow.items]
    if flow.kind is Kind.GAPS:
        return [item.to_dict() for item in flow.items]
    return flow.to_dict()["value"]


def _step_out(step: ReasoningStep) -> dict[str, Any]:
    return {
        "claim": step.claim, "layer": step.layer, "operation": step.operation,
        "confidence": step.confidence, "inputs": list(step.inputs),
        "evidence": [item.to_dict() for item in step.evidence],
    }


def _step_in(body: Mapping[str, Any]) -> ReasoningStep:
    return ReasoningStep(
        claim=body.get("claim", ""), layer=body.get("layer", ""),
        operation=body.get("operation", ""),
        confidence=float(body.get("confidence", 1.0)),
        inputs=tuple(body.get("inputs", ())),
        evidence=tuple(
            _evidence_in(item) for item in body.get("evidence", [])
            if isinstance(item, Mapping)
        ),
    )


def _evidence_in(body: Mapping[str, Any]) -> Evidence:
    return Evidence.from_dict(body)


def _gap_in(body: Mapping[str, Any]) -> Gap:
    if isinstance(body, Gap):
        return body
    return Gap(
        kind=GapKind(body.get("kind", "not_implemented")),
        subject=body.get("subject", ""),
        detail=body.get("detail", ""),
        remediation=body.get("remediation", ""),
        confidence_impact=float(body.get("confidence_impact", 0.0)),
    )


def crossable(kind: Kind) -> bool:
    return kind in CROSSABLE
