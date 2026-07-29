"""A manual page per verb, generated from the registry.

Nothing here is written by hand, and that is the entire point. A hand-written
manual disagrees with the code within two releases: a parameter gets renamed, a
verb learns a new kind, and the document keeps confidently describing the old
behaviour. So the page is a *view* of the `Verb` declaration, and the only way to
change the manual is to change the code.

The section that earns the design is **composition**. "What may follow this verb"
and "what may precede it" are queries over the type graph, not prose. That makes
them free, always correct, and — more usefully — it makes the manual teach
composition instead of merely listing commands, because the reader can see the
next move from wherever they are.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from ..compose.flow import Kind
from ..compose.registry import VerbRegistry
from ..compose.verb import Param, Verb


@dataclass(frozen=True, slots=True)
class Page:
    """One verb, everything a reader needs, derived from its declaration."""

    verb: Verb
    follows: tuple[str, ...] = ()      # what may precede it
    precedes: tuple[str, ...] = ()     # what may follow it
    origin: str = ""

    @property
    def name(self) -> str:
        return self.verb.name

    @property
    def signature(self) -> str:
        """`OBSERVATIONS -> RESOLUTION`, or `-> OBSERVATIONS` for a source."""
        if self.verb.produces is Kind.SAME:
            return f"{self.verb.consumes.label} -> (unchanged)"
        if self.verb.is_source:
            return f"-> {self.verb.produces.label}"
        return f"{self.verb.consumes.label} -> {self.verb.produces.label}"

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.verb.to_dict(),
            "signature": self.signature,
            "follows": list(self.follows),
            "precedes": list(self.precedes),
            "origin": self.origin,
        }

    def render(self, *, width: int = 78) -> str:
        """The page as plain text — what `slpie help <verb>` prints."""
        lines: list[str] = [
            f"  {self.verb.usage}",
            f"  {self.signature}",
            "",
            f"  {self.verb.summary}",
        ]

        if self.verb.detail:
            lines.append("")
            lines.extend(f"  {line}" for line in _wrap(self.verb.detail, width - 2))

        if self.verb.mutates:
            lines.append("")
            lines.append("  CHANGES THE ENVIRONMENT — refused unless confirmed.")

        if self.verb.params:
            lines.append("")
            lines.append("  parameters:")
            for param in self.verb.params:
                lines.extend(_param_lines(param, width))

        if self.verb.is_source:
            lines.append("")
            lines.append("  starts a pipeline; it consumes nothing")
        elif self.follows:
            lines.append("")
            lines.append("  pipe into it from:")
            lines.extend(_columns(self.follows, width))

        if self.precedes:
            lines.append("")
            lines.append("  and then pipe it into:")
            lines.extend(_columns(self.precedes, width))

        if self.verb.examples:
            lines.append("")
            lines.append("  examples:")
            for example in self.verb.examples:
                lines.append(f"    slpie '{example}'")

        return "\n".join(lines)

    def markdown(self) -> str:
        """The page as markdown — what `slpie manual` writes to a file."""
        lines = [
            f"### `{self.verb.name}`",
            "",
            f"`{self.verb.usage}` — `{self.signature}`",
            "",
            self.verb.summary + ".",
        ]
        if self.verb.detail:
            lines += ["", self.verb.detail]
        if self.verb.mutates:
            lines += ["", "> **Changes the environment.** Refused unless confirmed."]

        if self.verb.params:
            lines += ["", "| parameter | type | default | description |",
                      "|---|---|---|---|"]
            for param in self.verb.params:
                default = "*required*" if param.required else (
                    f"`{param.default}`" if param.default is not None else "—"
                )
                note = param.help + (
                    f" One of: {', '.join(param.choices)}." if param.choices else ""
                )
                lines.append(
                    f"| `--{param.name}` | {param.type} | {default} | {note} |"
                )

        if self.follows:
            lines += ["", f"**Pipe into it from:** {_code_list(self.follows)}"]
        elif self.verb.is_source:
            lines += ["", "**Starts a pipeline.**"]
        if self.precedes:
            lines += ["", f"**Then pipe it into:** {_code_list(self.precedes)}"]

        if self.verb.examples:
            lines += ["", "```bash"]
            lines += [f"slpie '{example}'" for example in self.verb.examples]
            lines += ["```"]

        return "\n".join(lines)

    def __str__(self) -> str:
        return self.render()


def page_for(name: str, *, verbs: VerbRegistry) -> Page:
    """The page for one verb. Raises the registry's own error if unknown."""
    verb = verbs.require(name)
    return Page(
        verb=verb,
        follows=tuple(item.name for item in verbs.predecessors(name)),
        precedes=tuple(item.name for item in verbs.successors(name)),
        origin=verbs.origin_of(name),
    )


def pages(verbs: VerbRegistry) -> tuple[Page, ...]:
    """Every page, in the order a reader should meet them."""
    return tuple(page_for(name, verbs=verbs) for name in verbs.names)


# --- text helpers --------------------------------------------------------


def _wrap(text: str, width: int) -> list[str]:
    """Wrap a paragraph. Stdlib `textwrap` would do, and does."""
    import textwrap

    return textwrap.wrap(" ".join(text.split()), width=max(20, width)) or [""]


def _param_lines(param: Param, width: int) -> list[str]:
    head = f"    {param.usage:<26}"
    note = param.help
    if param.choices:
        note += f" (one of: {', '.join(param.choices)})"
    if param.required:
        note += " [required]"
    elif param.default is not None:
        note += f" [default {param.default}]"

    wrapped = _wrap(note, max(20, width - 32))
    lines = [head + wrapped[0]]
    lines.extend(" " * 30 + item for item in wrapped[1:])
    return lines


def _columns(names: Sequence[str], width: int) -> list[str]:
    """Names wrapped into a block, so twenty successors do not print one per line."""
    return [f"    {line}" for line in _wrap(", ".join(sorted(names)), width - 4)]


def _code_list(names: Sequence[str]) -> str:
    return ", ".join(f"`{name}`" for name in sorted(names))
