"""The manual, generated from the verb registry.

Nothing in this package is written by hand. A hand-written manual disagrees with
the code within two releases — a parameter is renamed, a verb learns a new kind,
and the document keeps confidently describing the old behaviour. So every page is
a *view* of a `Verb` declaration, and the only way to change the manual is to
change the code.

Two documents, both generated:

* the **verb reference** (:mod:`~slpie.manual.page`) — usage, typed parameters,
  what it consumes and produces, and *what may pipe into it and what it may pipe
  into*. That last part is a query over the type graph rather than prose, which is
  what lets the manual teach composition instead of merely listing commands.
* the **cookbook** (:mod:`~slpie.manual.cookbook`) — curated recipes, each with
  the reason it is shaped the way it is. Every one is executed by
  `tests/test_slpie_manual.py`, because a documented composition that does not run
  teaches the wrong thing and costs the reader their trust in the rest.

Three renderings for three readers — terminal, markdown, JSON — from one source,
so a verb cannot be documented in one and missing from another.
"""

from .cookbook import PRIMER, Recipe, groups, offline, recipes
from .page import Page, page_for, pages
from .render import as_dict, compose_help, markdown, overview, verb_help

__all__ = [
    "PRIMER",
    "Page",
    "Recipe",
    "as_dict",
    "compose_help",
    "groups",
    "markdown",
    "offline",
    "overview",
    "page_for",
    "pages",
    "recipes",
    "verb_help",
]
