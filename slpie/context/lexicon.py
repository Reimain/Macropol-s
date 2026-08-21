"""The same platform, in the reader's words.

A framework stays generalistic: it ships `Table`, `List`, `Item`, and every
product built on it wears the framework's vocabulary rather than its own. This
one goes the other way. The kernel knows what a thing *is*; what a given reader
*calls* it is a different fact, and keeping the two apart is what lets one
console read as a platform-engineering tool to one tenant and a compliance tool
to another without forking a screen.

**The default is derived, not authored.** Terms come from `slpie/domain/*.py` —
whose package docstring already describes itself as "the vocabulary every other
layer is written in" — and from the top-level package names under `slpie/`,
each glossed by the first line of its own docstring. So the platform cannot ship
a word its own code does not use, and a term that stops existing stops being
offered.

**A profile may rename the product. It may never rename a control.** The words
that carry policy — every `Severity`, every `GapKind`, every `Verdict`, every
target state, and the vocabulary of refusal — are protected, and `overlay`
refuses them rather than silently ignoring them. A tenant renaming *refused* to
*pending* is how a control becomes invisible, and it would be invisible to us
too. The protected set is derived from the enums themselves, so a severity added
next year is protected the day it is added rather than the day somebody
remembers to add it to a list.

Stdlib only, ring 0. Reads docstrings and enum members; no parser, no schema
library, no network.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from ..errors import SlpieError


class LexiconError(SlpieError):
    """A profile tried to rename something it may not, or a term nobody defines."""


#: Key prefixes a profile may never overlay. Each names a vocabulary that a
#: decision is read from rather than a noun the product happens to use.
#:
#: `refusal` is the one that is not an enum and is the most important of them.
#: The rest are derived from their enums below, so the set grows with the code.
PROTECTED_PREFIXES = ("severity.", "gap.", "verdict.", "target.", "refusal.")


@dataclass(frozen=True, slots=True)
class Term:
    """One word this context uses, and where the word came from."""

    key: str
    word: str
    plural: str = ""
    verb: str = ""
    gloss: str = ""
    source: str = ""            # the module it was derived from, or the profile

    def __post_init__(self) -> None:
        if not self.key or not self.word:
            raise LexiconError("a term needs both a key and a word")
        if not self.plural:
            object.__setattr__(self, "plural", _plural(self.word))

    @property
    def protected(self) -> bool:
        """Whether a profile is forbidden from replacing this word."""
        return self.key.startswith(PROTECTED_PREFIXES)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "word": self.word, "plural": self.plural,
            "verb": self.verb, "gloss": self.gloss, "source": self.source,
            "protected": self.protected,
        }


def _plural(word: str) -> str:
    """English plurals, to the depth this actually needs.

    Deliberately not a library and deliberately not clever. The words in play
    are the product's own nouns — node, finding, dependency, policy — and a
    profile that wants something irregular supplies `plural` explicitly, which
    is a smaller surface than an inflection engine that is wrong about
    somebody's domain vocabulary in a way they cannot override.
    """
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return f"{word}es"
    if word.endswith("y") and word[-2:-1] not in "aeiou":
        return f"{word[:-1]}ies"
    return f"{word}s"


class Lexicon:
    """Stable keys, and the words one context puts on them."""

    def __init__(self, terms: Iterable[Term] = (), *, name: str = "default") -> None:
        self.name = name
        self._terms: dict[str, Term] = {}
        for term in terms:
            self._terms[term.key] = term

    # -- access ----------------------------------------------------------

    @property
    def terms(self) -> tuple[Term, ...]:
        return tuple(self._terms[key] for key in sorted(self._terms))

    def get(self, key: str) -> Term | None:
        return self._terms.get(key)

    def word(self, key: str, *, plural: bool = False, title: bool = False) -> str:
        """The word for a key, or the key itself when nobody defines one.

        Falling back to the key rather than raising is deliberate: a missing
        term should show a slightly ugly label, never take a screen down. The
        *profile* is where a bad key fails loudly, because that is authored and
        checkable; a render is neither.
        """
        term = self._terms.get(key)
        if term is None:
            return key
        word = term.plural if plural else term.word
        return word[:1].upper() + word[1:] if title else word

    @property
    def protected(self) -> tuple[Term, ...]:
        return tuple(term for term in self.terms if term.protected)

    @property
    def renameable(self) -> tuple[Term, ...]:
        return tuple(term for term in self.terms if not term.protected)

    # -- overlay ---------------------------------------------------------

    def overlay(self, changes: Mapping[str, Mapping[str, str]], *, name: str) -> "Lexicon":
        """A new lexicon with a profile's words applied over these.

        Three refusals, each of which would otherwise be a silent failure:

        * an unknown key — a profile typo that renames nothing and looks like it
          worked;
        * a protected key — the whole point of the protected set;
        * an empty word — a term that renders as nothing.
        """
        merged = dict(self._terms)
        for key, fields in sorted(changes.items()):
            held = self._terms.get(key)
            if held is None:
                raise LexiconError(
                    f"{key!r} is not a term this platform defines; "
                    f"the closest are {', '.join(self._near(key)) or 'none'}"
                )
            if held.protected:
                raise LexiconError(
                    f"{key!r} carries a decision, not a name, and cannot be "
                    f"renamed. Renaming {held.word!r} would make a control "
                    f"unrecognisable to the operator reading it — and to us."
                )
            word = str(fields.get("word") or "").strip()
            if not word:
                raise LexiconError(f"{key!r} was given an empty word")
            merged[key] = Term(
                key=key, word=word,
                plural=str(fields.get("plural") or "").strip(),
                verb=str(fields.get("verb") or held.verb),
                gloss=str(fields.get("gloss") or held.gloss),
                source=f"profile:{name}",
            )
        return Lexicon(merged.values(), name=name)

    def _near(self, key: str) -> tuple[str, ...]:
        head = key.split(".", 1)[0]
        return tuple(
            item for item in sorted(self._terms)
            if item.startswith(head[:4]) or head in item
        )[:3]

    # -- projection ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "digest": self.digest,
            "terms": {term.key: term.to_dict() for term in self.terms},
        }

    def words(self) -> dict[str, dict[str, str]]:
        """The compact form the browser consumes — key to word and plural."""
        return {
            term.key: {"word": term.word, "plural": term.plural,
                       "gloss": term.gloss}
            for term in self.terms
        }

    @property
    def digest(self) -> str:
        body = "\n".join(
            f"{term.key}\x1f{term.word}\x1f{term.plural}" for term in self.terms
        )
        return hashlib.blake2b(body.encode("utf-8"), digest_size=16).hexdigest()

    def __len__(self) -> int:
        return len(self._terms)

    def __iter__(self) -> Iterator[Term]:
        return iter(self.terms)

    def __contains__(self, key: object) -> bool:
        return key in self._terms


# --- the derived default ------------------------------------------------


def _docline(path: Path) -> str:
    """The first line of a module's docstring, without importing it."""
    import ast

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return ""
    doc = ast.get_docstring(tree) or ""
    head = doc.split("\n", 1)[0].strip()
    # "Nodes — the things an ecosystem is made of." → the half that explains.
    for separator in (" — ", " - ", ": "):
        if separator in head:
            return head.split(separator, 1)[1].strip()
    return head


def _protected_terms() -> list[Term]:
    """The vocabulary a decision is read from. Derived from the enums.

    Deriving rather than listing is what keeps the protection honest: a
    `Severity` added next year is protected the day it is added, not the day
    somebody remembers to add it to a list here.
    """
    from ..audit.judge import Verdict
    from ..binding.target import Target
    from ..domain.finding import GapKind, Severity

    terms: list[Term] = [
        Term("refusal.refused", "refused",
             gloss="A guard declined this, and said why.",
             source="slpie/ui/api.py"),
        Term("refusal.reason", "reason", gloss="Why it was declined.",
             source="slpie/rbac/engine.py"),
    ]
    for member in Severity:
        terms.append(Term(
            f"severity.{member.value}", member.value,
            gloss=f"Severity {member.value}.",
            source="slpie/domain/finding.py",
        ))
    for member in GapKind:
        terms.append(Term(
            f"gap.{member.value}", member.value.replace("_", " "),
            gloss="Something the platform could not see, and what it cost.",
            source="slpie/domain/finding.py",
        ))
    for member in Verdict:
        terms.append(Term(
            f"verdict.{member.value}", member.value,
            gloss=f"Verdict {member.value}.", source="slpie/audit/judge.py",
        ))
    for member in Target:
        terms.append(Term(
            f"target.{member.value}", member.value,
            gloss=f"The {member.value} binding.", source="slpie/binding/target.py",
        ))
    return terms


def default(root: Path | None = None) -> Lexicon:
    """The lexicon this platform ships, derived from its own code."""
    from .index import _repository

    base = root or _repository()
    terms: list[Term] = []
    seen: set[str] = set()

    domain = base / "slpie" / "domain"
    for path in sorted(domain.glob("*.py")) if domain.is_dir() else ():
        if path.name == "__init__.py":
            continue
        key = path.stem
        terms.append(Term(
            key=key, word=key, gloss=_docline(path),
            source=f"slpie/domain/{path.name}",
        ))
        seen.add(key)

    slpie = base / "slpie"
    for path in sorted(slpie.iterdir()) if slpie.is_dir() else ():
        init = path / "__init__.py"
        if not path.is_dir() or not init.is_file() or path.name.startswith(("_", ".")):
            continue
        key = path.name
        if key in seen:
            continue
        terms.append(Term(
            key=key, word=key,
            # A package named `artifacts`, `plugins` or `connectors` is already
            # the plural, and running it through `_plural` produced
            # "artifactses". Passing the word through as its own plural is
            # right for every such package here, and a profile that disagrees
            # says so explicitly — which it can, and the default cannot.
            plural=key if key.endswith("s") else "",
            gloss=_docline(init),
            source=f"slpie/{key}/__init__.py",
        ))
        seen.add(key)

    terms.extend(_protected_terms())
    return Lexicon(terms, name="default")
