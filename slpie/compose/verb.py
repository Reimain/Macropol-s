"""A verb — one capability, declared as data so every surface can read it.

The whole composition philosophy rests on this dataclass being *data*. A verb
does not know it is a CLI subcommand, an HTTP route, a manual page, a planner
vocabulary entry or a chip in a UI palette; it declares what it consumes, what it
produces, what parameters it takes and how to run, and each surface projects that
declaration its own way. Five projections of one truth cannot drift; five
hand-written restatements of it drift by the second release.

Two fields do more than they appear to.

`consumes` / `produces` type the pipe, which buys three separate features from one
mechanism: a composition is validated before anything executes, the manual can
generate "what may follow this verb" as a query rather than as maintained prose,
and the offline planner gets a search space instead of a guess.

`mutates` marks the verbs that change something outside the process. It is
checked **at plan time, over the whole composition**, so a four-stage pipeline
whose last stage flips a live target is confirmed before its first stage runs —
rather than after three stages have already had effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Iterable,
    Mapping,
    Protocol,
    Sequence,
    runtime_checkable,
)

from ..errors import SlpieError
from .flow import Flow, Kind


class VerbError(SlpieError):
    """A verb was declared or invoked wrongly."""


#: Parameter types a verb may declare. Deliberately few: every one has an
#: unambiguous parse from a command line, an HTTP query string and a JSON body,
#: because a parameter that means different things on different surfaces is a bug
#: waiting for the first customer who uses both.
TYPES = ("str", "int", "float", "bool", "path", "list")


@dataclass(frozen=True, slots=True)
class Param:
    """One typed parameter, carrying its own help text.

    The help lives here rather than in a manual file because a manual that can
    disagree with the code eventually does.
    """

    name: str
    type: str = "str"
    help: str = ""
    default: Any = None
    required: bool = False
    choices: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.type not in TYPES:
            raise VerbError(
                f"{self.name}: {self.type!r} is not a parameter type; "
                f"choose one of {', '.join(TYPES)}"
            )

    def parse(self, raw: Any) -> Any:
        """One textual value → the declared type. Refuses rather than coerces.

        A parameter that silently accepted `--depth banana` as 0 would produce a
        plausible answer to a question nobody asked.
        """
        if raw is None:
            return self.default
        if self.type == "bool":
            if isinstance(raw, bool):
                return raw
            text = str(raw).strip().lower()
            if text in ("", "true", "yes", "1", "on"):
                return True
            if text in ("false", "no", "0", "off"):
                return False
            raise VerbError(f"--{self.name}: {raw!r} is not true or false")
        if self.type == "int":
            try:
                return int(str(raw).strip())
            except ValueError:
                raise VerbError(f"--{self.name}: {raw!r} is not a whole number") from None
        if self.type == "float":
            try:
                return float(str(raw).strip())
            except ValueError:
                raise VerbError(f"--{self.name}: {raw!r} is not a number") from None
        if self.type == "list":
            if isinstance(raw, (list, tuple)):
                return tuple(str(item) for item in raw)
            return tuple(part.strip() for part in str(raw).split(",") if part.strip())

        text = str(raw)
        if self.choices and text not in self.choices:
            raise VerbError(
                f"--{self.name}: {text!r} is not one of {', '.join(self.choices)}"
            )
        return text

    @property
    def usage(self) -> str:
        """`--depth INT` — the form a manual and a `--help` both want."""
        if self.type == "bool":
            return f"--{self.name}"
        return f"--{self.name} {self.type.upper()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "type": self.type, "help": self.help,
            "default": self.default, "required": self.required,
            "choices": list(self.choices), "usage": self.usage,
        }

    def __str__(self) -> str:
        return self.usage if self.required else f"[{self.usage}]"


@dataclass(slots=True)
class Context:
    """What a verb is allowed to reach. Everything ambient, named.

    Passed explicitly rather than read from module state so that a verb is
    testable without an engine, and so the same verb runs identically under the
    CLI, the HTTP API and a Celery worker — which is what invariant 7 requires of
    everything above `binding/`.
    """

    engine: Any = None
    actor: str = "cli"
    confirmed: bool = False           # the operator said yes to the mutating verbs
    root: str = "."
    options: Mapping[str, Any] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=dict)
    #: This run's isolated share of memory and disk. Created on first use, so a
    #: composition that never accumulates anything large never creates a session
    #: and never touches the filesystem.
    spill: Any = None

    def session(self) -> Any:
        """The spill session for this run, created on demand.

        On the `Context` rather than a module global because concurrency is the
        point: two requests in one process must get two sessions, and anything
        reached through module state would give them one. The `Context` is
        already the per-run object every verb receives, so it is the right place
        for the per-run isolation boundary.
        """
        if self.spill is None:
            from ..spill import SpillSession

            self.spill = SpillSession()
        return self.spill

    def close(self) -> int:
        """Release this run's memory and delete its blocks. Idempotent.

        Deliberately *not* called at the end of a composition. The result's flow
        may still be a spilled sequence and the caller has not rendered it yet,
        so sweeping there would delete the answer between producing it and
        printing it. The owning lifetime is the request, and the request is what
        holds the `Context` — so closing belongs here, where the surfaces (CLI,
        HTTP, worker) each call it once they are done reading.
        """
        if self.spill is None:
            return 0
        return self.spill.close()

    def __enter__(self) -> "Context":
        return self

    def __exit__(self, *_exception: Any) -> None:
        self.close()

    def require_engine(self, verb: str) -> Any:
        if self.engine is None:
            raise VerbError(
                f"{verb} needs an environment; declare one with a manifest first "
                f"(`slpie init` scaffolds it) — running it against nothing would "
                f"return an empty answer that looks like a real one"
            )
        return self.engine


#: The signature every verb's implementation has.
Run = Callable[[Flow, Mapping[str, Any], Context], Flow]


@dataclass(frozen=True, slots=True)
class Verb:
    """One composable capability."""

    name: str
    summary: str
    produces: Kind
    run: Run
    consumes: Kind = Kind.NOTHING
    group: str = "general"
    params: tuple[Param, ...] = ()
    examples: tuple[str, ...] = ()
    mutates: bool = False
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.name or " " in self.name:
            raise VerbError(f"{self.name!r} is not a usable verb name")
        if not self.summary:
            raise VerbError(
                f"{self.name} has no summary; the manual is generated from these, "
                f"so a verb without one ships an empty page"
            )
        if self.produces is Kind.NOTHING:
            raise VerbError(
                f"{self.name} produces nothing, so nothing can follow it and it "
                f"cannot be composed; every verb returns something, even a report"
            )
        if self.produces is Kind.SAME and self.consumes is Kind.NOTHING:
            raise VerbError(
                f"{self.name} claims to pass through what it was given but "
                f"consumes nothing; a source verb must name what it produces"
            )

    @property
    def is_source(self) -> bool:
        """Whether it starts a pipeline rather than continuing one."""
        return self.consumes is Kind.NOTHING

    @property
    def polymorphic(self) -> bool:
        return self.consumes is Kind.ANY

    def param(self, name: str) -> Param | None:
        for candidate in self.params:
            if candidate.name == name:
                return candidate
        return None

    def bind(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Raw arguments → parsed, defaulted, validated.

        Unknown parameters are refused with the known ones listed. Accepting a
        misspelled flag and ignoring it is how somebody runs a filter that
        silently did not filter.
        """
        known = {param.name for param in self.params}
        for name in arguments:
            if name not in known:
                raise VerbError(
                    f"{self.name} has no --{name}"
                    + (f"; it takes {', '.join(sorted(known))}" if known
                       else " and takes no parameters")
                )

        bound: dict[str, Any] = {}
        for param in self.params:
            if param.name in arguments:
                bound[param.name] = param.parse(arguments[param.name])
            elif param.required:
                raise VerbError(f"{self.name} needs --{param.name}: {param.help}")
            else:
                bound[param.name] = param.default
        return bound

    def accepts(self, upstream: Kind) -> bool:
        return self.consumes.accepts(upstream)

    def output_for(self, upstream: Kind) -> Kind:
        """What this verb yields given that input — resolving `SAME`."""
        return upstream if self.produces is Kind.SAME else self.produces

    @property
    def usage(self) -> str:
        parts = [self.name]
        parts.extend(str(param) for param in self.params)
        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "summary": self.summary, "detail": self.detail,
            "group": self.group, "consumes": self.consumes.value,
            "produces": self.produces.value, "source": self.is_source,
            "mutates": self.mutates, "usage": self.usage,
            "params": [param.to_dict() for param in self.params],
            "examples": list(self.examples),
        }

    def __str__(self) -> str:
        arrow = "→" if self.is_source else f"{self.consumes.label} →"
        return f"{self.name}: {arrow} {self.produces.label}"


@runtime_checkable
class VerbProvider(Protocol):
    """What a plugin registering verbs looks like. Built-ins use it too."""

    def verbs(self) -> Sequence[Verb]:
        ...
