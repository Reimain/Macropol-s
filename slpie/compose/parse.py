"""Reading a pipeline the way a shell reads one.

`scan --changed | link | findings --severity high` has to parse the same whether
it arrived on a command line, in an HTTP body or from the planner. So parsing
lives here once, and every surface calls it.

A note on provenance, because it matters for the architecture rather than for the
code: `gratimos/shell/command.py` already contains a proven pipeline splitter that
handles quotes, `||` and `;` correctly. Its *algorithm* is the reference for
`_split` below. It is deliberately **not imported**, because invariant 8 permits
exactly one Gratimos import from `slpie/` and that budget is spent on the codegen
bridge (§15). Reimplementing forty lines is the cheaper price than putting a
second hole in the boundary the whole architecture is asserted on.

Two decisions worth stating:

**Nothing is expanded.** No globbing, no variable substitution, no subshells. A
composition is a description of verbs, not a shell command, and `$HOME` in an
argument stays the literal four characters. The moment a parser expands things,
the text a user reviewed and the thing that ran are different objects.

**A malformed pipeline is refused, never guessed.** Unbalanced quotes produce an
error naming the offending stage. Guessing where the quote was meant to close is
how a filter silently filters something else.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Sequence

from ..errors import SlpieError


class ParseError(SlpieError):
    """A pipeline could not be read. It names the stage that failed."""


@dataclass(frozen=True, slots=True)
class Stage:
    """One verb invocation in a pipeline, parsed but not yet validated."""

    verb: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    operands: tuple[str, ...] = ()
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "verb": self.verb, "arguments": dict(self.arguments),
            "operands": list(self.operands), "raw": self.raw,
        }

    def render(self) -> str:
        """Back to text. Round-trips, which is what makes `--explain` honest."""
        parts = [self.verb]
        parts.extend(shlex.quote(operand) for operand in self.operands)
        for name, value in self.arguments.items():
            if value is True:
                parts.append(f"--{name}")
            elif isinstance(value, (list, tuple)):
                parts.append(f"--{name} {shlex.quote(','.join(str(v) for v in value))}")
            else:
                parts.append(f"--{name} {shlex.quote(str(value))}")
        return " ".join(parts)

    def __str__(self) -> str:
        return self.render()


def parse(text: str) -> tuple[Stage, ...]:
    """`a --x 1 | b | c` → three stages. Never runs, never expands."""
    raw = (text or "").strip()
    if not raw:
        raise ParseError("the pipeline is empty; `slpie help` lists every verb")

    stages: list[Stage] = []
    for fragment in _split(raw):
        stripped = fragment.strip()
        if not stripped:
            continue
        stages.append(_stage(stripped))

    if not stages:
        raise ParseError(f"{raw!r} contains no verbs")
    return tuple(stages)


def _stage(text: str) -> Stage:
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError as error:
        raise ParseError(f"cannot read {text!r}: {error}") from None
    if not tokens:
        raise ParseError(f"{text!r} names no verb")

    verb, *rest = tokens
    if verb.startswith("-"):
        raise ParseError(
            f"{text!r} starts with a flag rather than a verb; a stage is "
            f"`<verb> [--flag value]`"
        )

    arguments: dict[str, Any] = {}
    operands: list[str] = []

    index = 0
    while index < len(rest):
        token = rest[index]
        if token.startswith("--"):
            name, separator, inline = token[2:].partition("=")
            if not name:
                raise ParseError(f"{text!r}: `--` is not a parameter name")
            if separator:
                arguments[name] = inline
            elif index + 1 < len(rest) and not rest[index + 1].startswith("--"):
                arguments[name] = rest[index + 1]
                index += 1
            else:
                # A trailing flag with no value is a boolean. Which flags are
                # boolean is the verb's business, not the parser's — `Param.parse`
                # decides, so the parser never has to know the schema.
                arguments[name] = True
        elif token.startswith("-") and len(token) > 1:
            raise ParseError(
                f"{text!r}: short flags are not used; write --{token.lstrip('-')}"
            )
        else:
            operands.append(token)
        index += 1

    return Stage(verb=verb, arguments=arguments, operands=tuple(operands), raw=text)


def _split(text: str) -> list[str]:
    """Split on `|` outside quotes.

    `||` is a separator too, not a pipe carrying a flow — writing it is almost
    always a typo for `|`, and treating it as one pipe silently accepts the typo.
    Splitting on it means the second fragment parses as its own stage and the
    error, if any, is about the stage rather than about a character.
    """
    parts: list[str] = []
    current: list[str] = []
    quote = ""
    index = 0

    while index < len(text):
        char = text[index]
        if quote:
            current.append(char)
            if char == quote and (index == 0 or text[index - 1] != "\\"):
                quote = ""
        elif char in "\"'":
            quote = char
            current.append(char)
        elif char == "|":
            parts.append("".join(current))
            current = []
            if index + 1 < len(text) and text[index + 1] == "|":
                index += 1
        else:
            current.append(char)
        index += 1

    if quote:
        raise ParseError(
            f"unbalanced {quote} in {text!r}; a pipeline that cannot be "
            f"tokenised cannot be reviewed by a human either"
        )

    parts.append("".join(current))
    return parts


def render(stages: Sequence[Stage]) -> str:
    """Stages back to one line. Round-trips through `parse`."""
    return " | ".join(stage.render() for stage in stages)
