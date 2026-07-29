"""Composing verbs — validated before anything runs, explained afterwards.

Two properties make this worth having over a `for` loop.

**The type check happens first, over the whole composition.** `findings | attach`
is refused with "`findings` produces FINDINGS, `attach` consumes ELEMENTS" before
stage one executes. The alternative — discovering it at stage two — means the
first stage's effects have already happened, and for a pipeline containing a
mutating verb that is the difference between a refusal and a half-applied change.

**Confirmation is decided over the composition, not per stage.** A four-stage
pipeline whose last stage flips a live target needs the operator's yes *before*
the first stage runs. Asking at stage four is asking after three stages of work,
and the operator's answer then has to weigh sunk cost — which is exactly the
pressure a confirmation gate exists to remove.

Failure has one rule, consistent with the reasoning pipeline: a stage that raises
**stops the composition** and the partial flow is returned with the error
attached. This is deliberately *not* the layer rule, where a failure abstains and
the rest continue. Layers are independent contributors to one picture; pipeline
stages are dependent — `link` given nothing from `scan` cannot produce anything
meaningful, so continuing would fabricate an answer from a hole.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping, Sequence

from ..domain.finding import Gap, GapKind
from ..errors import SlpieError
from .flow import Flow, Kind
from .parse import Stage, parse, render
from .registry import VerbRegistry, registry as default_registry
from .verb import Context, Verb, VerbError


class CompositionError(SlpieError):
    """A composition is invalid. Raised before anything executes."""


@dataclass(frozen=True, slots=True)
class Mismatch:
    """One type error, stated so the fix is obvious."""

    position: int
    verb: str
    expected: Kind
    received: Kind
    upstream: str = ""

    def explain(self) -> str:
        if self.received is Kind.NOTHING and self.upstream == "":
            return (
                f"stage {self.position} `{self.verb}` needs "
                f"{self.expected.label} piped into it, but it starts the "
                f"pipeline; a source verb has to come first"
            )
        return (
            f"stage {self.position} `{self.verb}` consumes "
            f"{self.expected.label}, but `{self.upstream}` produces "
            f"{self.received.label}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": self.position, "verb": self.verb,
            "expected": self.expected.value, "received": self.received.value,
            "upstream": self.upstream, "explanation": self.explain(),
        }

    def __str__(self) -> str:
        return self.explain()


@dataclass(frozen=True, slots=True)
class Validation:
    """Whether a composition can run, and what it will do if it does."""

    ok: bool
    produces: Kind = Kind.NOTHING
    mismatches: tuple[Mismatch, ...] = ()
    unknown: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    mutating: tuple[str, ...] = ()

    @property
    def needs_confirmation(self) -> bool:
        return bool(self.mutating)

    def explain(self) -> str:
        if self.ok:
            note = (
                f"; {', '.join(self.mutating)} will change the environment"
                if self.mutating else ""
            )
            return f"produces {self.produces.label}{note}"
        return "\n".join(
            [*(f"there is no verb {name!r}" for name in self.unknown),
             *(mismatch.explain() for mismatch in self.mismatches),
             *self.errors]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "produces": self.produces.value,
            "mismatches": [m.to_dict() for m in self.mismatches],
            "unknown": list(self.unknown), "errors": list(self.errors),
            "mutating": list(self.mutating),
            "needs_confirmation": self.needs_confirmation,
            "explanation": self.explain(),
        }

    def __bool__(self) -> bool:
        return self.ok

    def __str__(self) -> str:
        return ("valid: " if self.ok else "invalid: ") + self.explain()


@dataclass(frozen=True, slots=True)
class Result:
    """What running a composition produced, including a partial run."""

    flow: Flow
    stages: tuple[Stage, ...] = ()
    completed: int = 0
    failed: str = ""
    error: str = ""
    duration: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def partial(self) -> bool:
        return bool(self.error) and self.completed > 0

    def to_dict(self, *, limit: int = 200) -> dict[str, Any]:
        return {
            "ok": self.ok, "partial": self.partial,
            "pipeline": render(self.stages),
            "completed": self.completed, "of": len(self.stages),
            "failed": self.failed, "error": self.error,
            "duration": round(self.duration, 4),
            "flow": self.flow.to_dict(limit=limit),
        }

    def __str__(self) -> str:
        if self.ok:
            return f"{self.flow}"
        return (
            f"failed at `{self.failed}` (stage {self.completed + 1} of "
            f"{len(self.stages)}): {self.error}"
        )


class Composition:
    """A pipeline of verbs. Parse it, check it, then run it — in that order."""

    def __init__(
        self,
        stages: Sequence[Stage],
        *,
        verbs: VerbRegistry | None = None,
    ) -> None:
        self.stages = tuple(stages)
        self.verbs = verbs if verbs is not None else default_registry()

    @classmethod
    def read(cls, text: str, *, verbs: VerbRegistry | None = None) -> "Composition":
        return cls(parse(text), verbs=verbs)

    # -- validation ------------------------------------------------------

    def validate(self, upstream: Kind = Kind.NOTHING) -> Validation:
        """Whether it can run, without running any of it.

        `upstream` is what is already flowing in — `Kind.NOTHING` for a pipeline
        starting fresh, and the incoming kind when a flow arrived on stdin from
        another `slpie` process. Without it, `slpie discover . | slpie link` would
        be rejected for starting with a transform verb, when in fact its input is
        sitting right there on the pipe.
        """
        unknown: list[str] = []
        mismatches: list[Mismatch] = []
        errors: list[str] = []
        mutating: list[str] = []

        current = upstream
        producer = ""

        for position, stage in enumerate(self.stages, start=1):
            verb = self.verbs.get(stage.verb)
            if verb is None:
                unknown.append(stage.verb)
                nearest = self.verbs.nearest(stage.verb)
                if nearest:
                    errors.append(
                        f"stage {position}: no verb {stage.verb!r} — "
                        f"did you mean {', '.join(nearest)}?"
                    )
                # The kind is now unknowable, so downstream checks would be
                # noise. Stop type-checking and report what is certain.
                break

            if not verb.accepts(current):
                mismatches.append(Mismatch(
                    position=position, verb=verb.name,
                    expected=verb.consumes, received=current, upstream=producer,
                ))
                break

            try:
                self._arguments(verb, stage)
            except VerbError as error:
                errors.append(f"stage {position}: {error}")

            if verb.mutates:
                mutating.append(verb.name)

            current = verb.output_for(current)
            producer = verb.name

        ok = not unknown and not mismatches and not errors
        return Validation(
            ok=ok, produces=current if ok else Kind.NOTHING,
            mismatches=tuple(mismatches), unknown=tuple(unknown),
            errors=tuple(errors), mutating=tuple(mutating),
        )

    def _arguments(self, verb: Verb, stage: Stage) -> dict[str, Any]:
        """Bind a stage's arguments, folding operands onto the first parameter.

        `slpie search redis` reads better than `slpie search --query redis`, and a
        positional operand is how every shell tool accepts its principal
        argument. Only the first non-flag parameter takes an operand, so the
        mapping stays unambiguous.
        """
        arguments = dict(stage.arguments)
        if stage.operands:
            positional = next(
                (param for param in verb.params if param.type != "bool"), None,
            )
            if positional is None:
                raise VerbError(
                    f"{verb.name} takes no positional argument; "
                    f"got {stage.operands[0]!r}"
                )
            if positional.name not in arguments:
                arguments[positional.name] = (
                    ",".join(stage.operands) if positional.type == "list"
                    else stage.operands[0]
                )
        return verb.bind(arguments)

    # -- execution -------------------------------------------------------

    def run(self, context: Context | None = None) -> Result:
        """Validate, gate, then execute. Never executes an invalid composition."""
        return self.run_from(Flow.start(), context)

    def run_from(self, upstream: Flow, context: Context | None = None) -> Result:
        """Run with a flow already in hand — the OS-pipe entry point.

        Everything the upstream process accumulated (its reasoning, its gaps)
        continues into this one, so provenance survives a process boundary the
        same way it survives a stage boundary. A pipeline that lost its gaps at
        the `|` would be a pipeline whose honesty depended on how it was invoked.
        """
        started = time.monotonic()
        active = context or Context()
        validation = self.validate(upstream.kind)

        if not validation.ok:
            raise CompositionError(validation.explain())

        if validation.needs_confirmation and not active.confirmed:
            raise CompositionError(
                f"{', '.join(validation.mutating)} would change the environment; "
                f"re-run with confirmation. This is checked before the first "
                f"stage so that nothing has happened yet when you decide"
            )

        flow = upstream
        completed = 0

        for stage in self.stages:
            verb = self.verbs.require(stage.verb)
            arguments = self._arguments(verb, stage)
            try:
                flow = verb.run(flow, arguments, active)
            except Exception as error:  # noqa: BLE001 - a verb may be third-party
                # Stop rather than abstain. A downstream stage handed nothing
                # would fabricate an answer out of a hole, which is worse than a
                # partial result that says where it stopped.
                return Result(
                    flow=flow, stages=self.stages, completed=completed,
                    failed=stage.verb,
                    error=f"{type(error).__name__}: {error}",
                    duration=time.monotonic() - started,
                )
            completed += 1

        return Result(
            flow=flow, stages=self.stages, completed=completed,
            duration=time.monotonic() - started,
        )

    # -- explanation -----------------------------------------------------

    def explain(self, upstream: Kind = Kind.NOTHING) -> str:
        """The composition, annotated — what `--explain` and `slpie plan` print.

        Shown before running, which is the point: you see what it will do and
        what it will cost while the decision is still free.
        """
        lines = [render(self.stages), ""]
        current = upstream

        for position, stage in enumerate(self.stages, start=1):
            verb = self.verbs.get(stage.verb)
            if verb is None:
                lines.append(f"  {position}. {stage.verb}: unknown verb")
                continue
            produced = verb.output_for(current)
            arrow = (
                f"{current.label} → {produced.label}" if not verb.is_source
                else f"→ {produced.label}"
            )
            mark = "  [changes the environment]" if verb.mutates else ""
            lines.append(f"  {position}. {stage.verb:<14} {arrow:<28} {verb.summary}{mark}")
            current = produced

        validation = self.validate(upstream)
        lines.append("")
        lines.append(f"  {validation.explain()}")
        return "\n".join(lines)

    @property
    def produces(self) -> Kind:
        return self.validate().produces

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline": render(self.stages),
            "stages": [stage.to_dict() for stage in self.stages],
            "validation": self.validate().to_dict(),
        }

    def __len__(self) -> int:
        return len(self.stages)

    def __iter__(self) -> Iterator[Stage]:
        return iter(self.stages)

    def __str__(self) -> str:
        return render(self.stages)


def run(
    text: str,
    context: Context | None = None,
    *,
    verbs: VerbRegistry | None = None,
) -> Result:
    """Parse, validate and run one pipeline. The one-call form."""
    return Composition.read(text, verbs=verbs).run(context)
