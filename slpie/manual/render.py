"""One manual, three renderings: terminal, markdown, JSON.

The three exist for three readers and they are the same content. `help` is for
somebody at a prompt, `manual` writes a file that `docs/` can hold without
drifting, and the JSON is what the web, desktop and mobile clients build their
palettes and help drawers from.

They share a source — the registry — so a verb cannot be documented in one and
missing from another. That is asserted in `tests/test_slpie_manual.py` rather than
trusted.
"""

from __future__ import annotations

from typing import Any, Sequence

from ..compose.flow import Kind
from ..compose.registry import VerbRegistry, registry as default_registry
from .cookbook import PRIMER, Recipe, groups, recipes
from .page import Page, page_for, pages


def overview(*, verbs: VerbRegistry | None = None, width: int = 78) -> str:
    """`slpie help` — every verb, grouped, with the next step named."""
    verbs = verbs if verbs is not None else default_registry()
    lines = [
        "",
        "  slpie — architecture intelligence, composed",
        "",
        "  usage:",
        "    slpie '<verb> | <verb> | ...'      run a composition",
        "    slpie <verb> [--flag value]        run one verb",
        "    slpie help <verb>                  what it does, and what pipes into it",
        "    slpie help compose                 the recipes, and why they are shaped so",
        "    slpie plan \"<question>\"            let it write the composition for you",
        "    slpie manual                       the whole manual, as markdown",
        "    slpie contract --openapi|--typescript   the generated API contract",
        "    slpie demo                         the end-to-end run, narrated",
        "",
    ]

    for group, items in verbs.groups().items():
        lines.append(f"  {group}:")
        for verb in items:
            mark = " *" if verb.mutates else "  "
            lines.append(f"    {verb.name:<14}{mark}{verb.summary}")
        lines.append("")

    if verbs.mutating():
        lines.append("  * changes the environment; refused unless confirmed")
        lines.append("")

    lines.append(f"  {len(verbs)} verbs. Anything can be piped into `explain`.")
    lines.append("")
    return "\n".join(lines)


def verb_help(name: str, *, verbs: VerbRegistry | None = None, width: int = 78) -> str:
    """`slpie help <verb>`."""
    verbs = verbs if verbs is not None else default_registry()
    return "\n" + page_for(name, verbs=verbs).render(width=width) + "\n"


def compose_help(*, verbs: VerbRegistry | None = None, width: int = 78) -> str:
    """`slpie help compose` — the primer, then every recipe with its reason."""
    verbs = verbs if verbs is not None else default_registry()
    lines = ["", "  COMPOSING", ""]
    lines.extend(f"  {line}" for line in PRIMER.rstrip().splitlines())
    lines.append("")

    for group, items in groups().items():
        lines.append(f"  {group.upper()}")
        lines.append("")
        for recipe in items:
            mark = "  [needs an environment]" if recipe.needs_environment else ""
            lines.append(f"    slpie '{recipe.pipeline}'{mark}")
            lines.append(f"      {recipe.intent}")
            if recipe.why:
                import textwrap

                for line in textwrap.wrap(" ".join(recipe.why.split()), width - 8):
                    lines.append(f"        {line}")
            lines.append("")

    lines.append(f"  {len(recipes())} recipes, every one of them executed by the")
    lines.append("  test suite — a documented composition that does not run is")
    lines.append("  worse than an undocumented one.")
    lines.append("")
    return "\n".join(lines)


def markdown(*, verbs: VerbRegistry | None = None) -> str:
    """`slpie manual` — the whole thing, generated so `docs/` cannot drift."""
    verbs = verbs if verbs is not None else default_registry()
    lines = [
        "# SLPIE — the manual",
        "",
        "*Generated from the verb registry. Do not edit by hand: the registry is the",
        "source of truth, and anything written here would be overwritten and would",
        "disagree with the code in the meantime.*",
        "",
        "## Composing",
        "",
        PRIMER.rstrip(),
        "",
        "## Recipes",
        "",
    ]

    for group, items in groups().items():
        lines += [f"### {group.title()}", ""]
        for recipe in items:
            suffix = " *(needs an environment)*" if recipe.needs_environment else ""
            lines += [
                f"**{recipe.intent}**{suffix}",
                "",
                "```bash",
                f"slpie '{recipe.pipeline}'",
                "```",
                "",
            ]
            if recipe.why:
                lines += [f"{recipe.why}", ""]

    lines += ["## Verbs", ""]
    for group, items in verbs.groups().items():
        lines += [f"## {group.title()}", ""]
        for verb in items:
            lines += [page_for(verb.name, verbs=verbs).markdown(), ""]

    lines += [
        "## The kinds that flow between verbs",
        "",
        "| kind | produced by | consumed by |",
        "|---|---|---|",
    ]
    for kind in Kind:
        if kind.polymorphic or kind is Kind.NOTHING:
            continue
        producers = [v.name for v in verbs.verbs if v.produces is kind]
        consumers = [v.name for v in verbs.verbs if v.consumes is kind]
        if not producers and not consumers:
            continue
        lines.append(
            f"| `{kind.label}` | {', '.join(f'`{n}`' for n in producers) or '—'} "
            f"| {', '.join(f'`{n}`' for n in consumers) or '—'} |"
        )

    lines += [
        "",
        "`ANY` verbs (`" + "`, `".join(
            v.name for v in verbs.verbs if v.consumes is Kind.ANY
        ) + "`) accept every kind and pass it through unchanged.",
        "",
    ]
    return "\n".join(lines)


def as_dict(*, verbs: VerbRegistry | None = None) -> dict[str, Any]:
    """What the clients fetch. The same content, machine-readable."""
    verbs = verbs if verbs is not None else default_registry()
    return {
        "primer": PRIMER,
        "verbs": [page.to_dict() for page in pages(verbs)],
        "groups": {
            group: [verb.name for verb in items]
            for group, items in verbs.groups().items()
        },
        "recipes": [recipe.to_dict() for recipe in recipes()],
        "recipe_groups": {
            group: [item.pipeline for item in items]
            for group, items in groups().items()
        },
        "kinds": [
            {
                "kind": kind.value,
                "produced_by": [v.name for v in verbs.verbs if v.produces is kind],
                "consumed_by": [v.name for v in verbs.verbs if v.consumes is kind],
            }
            for kind in Kind
            if not kind.polymorphic and kind is not Kind.NOTHING
        ],
    }
