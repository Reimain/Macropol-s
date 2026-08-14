"""Competitive intelligence, as verbs.

The analysis lives in `slpie/rivals/`; without verbs it would be a capability the
platform has and no surface can reach — the drift §24 exists to prevent, and the
same mistake the simulator is currently making with its twelve scenarios.

Making it a verb also means it composes. `rivals | json` is a data-room export,
and `rivals --gaps | filter --field leverage --equals shipped` is the slide.
"""

from __future__ import annotations

from typing import Any, Mapping

from ...domain.reasoning import ReasoningStep
from ..flow import Flow, Kind
from ..verb import Context, Param, Verb

GROUP = "rivals"


def _rivals(flow: Flow, arguments: Mapping[str, Any], _context: Context) -> Flow:
    """The recorded field, or the white space in it."""
    from ...rivals import RECORDED, field, opportunities, positioning
    from ...rivals.gap import render

    if arguments.get("gaps"):
        found = opportunities()
        return flow.then(
            Kind.REPORT, tuple(item.to_dict() for item in found), stage="rivals",
            steps=[ReasoningStep(
                claim=(
                    f"{len(found)} capability gap(s) computed from "
                    f"{len(field()['rivals'])} cited product records"
                ),
                layer="rivals", operation="shape",
            )],
            facts={"rivals": positioning(), "recorded": RECORDED},
        )

    body = field()
    return flow.then(
        Kind.REPORT, tuple(body["rivals"]), stage="rivals",
        steps=[ReasoningStep(
            claim=(
                f"{len(body['rivals'])} products recorded {RECORDED}, "
                f"{body['verified_share']:.0%} of assessments verified against a "
                f"cited source"
            ),
            layer="rivals", operation="shape",
        )],
        facts={"rivals": render(), "recorded": RECORDED,
               "verified_share": body["verified_share"]},
    )


def verbs() -> tuple[Verb, ...]:
    return (
        Verb(
            name="rivals", group=GROUP, produces=Kind.REPORT,
            summary="what else is on the market, and where nobody serves the buyer",
            detail=(
                "Every product carries a homepage, every capability assessment "
                "carries the URL it was checked against and the month it was "
                "checked, and an assessment with no source will not construct — "
                "`Coverage.UNKNOWN` is the honest answer instead.\n\n"
                "`--gaps` computes where the field is thin and ranks what it "
                "would take us to serve it. Nothing in that ranking is typed by "
                "hand: a capability we do not appear to lead on will not claim "
                "that we do, and the capabilities the field leads on are reported "
                "beside the ones we lead on."
            ),
            params=(
                Param("gaps", "bool", "the white space, ranked, instead of the "
                      "comparison table", default=False),
            ),
            examples=("rivals", "rivals --gaps", "rivals --gaps | json"),
            run=_rivals,
        ),
    )
