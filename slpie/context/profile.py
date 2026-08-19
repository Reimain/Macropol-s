"""Which words a given reader gets, and where the choice comes from.

A `ContextProfile` is the resolved answer to *whose vocabulary is this*. It is
deliberately thin — a name, and the terms that name overlays — because the
identity half of the question is already answered elsewhere: `Request.context`
is populated by the gateway before any route runs (`slpie/ui/api.py`), so a
route asking for a lexicon is asking about a caller the platform has already
identified. Building a second notion of who the caller is would be a second
place for the answer to be different, which is the same argument §16 makes for
not reimplementing the live guard behind FastAPI.

Profiles are files, in the shape `slpie/governance/policies.py` already uses:
the manifest's YAML subset, parsed by `slpie/environment/schema.py`, with a
malformed file recording an error while the rest still load. That property is
the reason to reuse the shape rather than invent one — an operator who fat-
fingers one profile should lose that profile, not the console.

Ring 0, stdlib. No YAML library: `parse_yaml` is the manifest's own subset.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ..errors import ManifestError
from .lexicon import Lexicon, LexiconError, default

#: Where profiles live, relative to the environment root. Beside `.slpie/apim/`,
#: which §30 established as the home for operator-authored policy.
PROFILE_DIR = Path(".slpie") / "lexicon"

EXTENSIONS = (".yaml", ".yml", ".json")


@dataclass(frozen=True, slots=True)
class ContextProfile:
    """One reader's vocabulary, resolved."""

    name: str = "default"
    tenant: str = ""
    role: str = ""
    domain: str = ""
    terms: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "tenant": self.tenant, "role": self.role,
            "domain": self.domain, "source": self.source,
            "terms": {key: dict(value) for key, value in sorted(self.terms.items())},
        }


@dataclass(frozen=True, slots=True)
class ProfileSet:
    """Everything that loaded, and everything that did not.

    Errors are carried rather than raised, so one bad file costs that file. The
    console still renders — in the platform's own words, which is the correct
    degradation: a reader seeing `finding` where they expected `risk` has a
    cosmetic problem, and a reader seeing a blank screen has an outage.
    """

    profiles: tuple[ContextProfile, ...] = ()
    errors: tuple[str, ...] = ()

    def get(self, name: str) -> ContextProfile | None:
        return next((item for item in self.profiles if item.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profiles": [item.to_dict() for item in self.profiles],
            "errors": list(self.errors),
        }

    def __len__(self) -> int:
        return len(self.profiles)


def _parse(document: Any, *, name: str, source: str) -> ContextProfile:
    if not isinstance(document, Mapping):
        raise LexiconError(f"{source} is not a lexicon profile")
    terms = document.get("terms") or {}
    if not isinstance(terms, Mapping):
        raise LexiconError(f"{source}: `terms` must be a mapping of key to word")

    cleaned: dict[str, dict[str, str]] = {}
    for key, value in terms.items():
        if isinstance(value, str):
            # `node: service` — the short form, because most renames are one word.
            cleaned[str(key)] = {"word": value}
        elif isinstance(value, Mapping):
            cleaned[str(key)] = {
                str(field_name): str(field_value)
                for field_name, field_value in value.items()
            }
        else:
            raise LexiconError(
                f"{source}: term {key!r} must be a word or a mapping, "
                f"not {type(value).__name__}"
            )

    return ContextProfile(
        name=str(document.get("name") or name),
        tenant=str(document.get("tenant") or ""),
        role=str(document.get("role") or ""),
        domain=str(document.get("domain") or ""),
        terms=cleaned,
        source=source,
    )


def load_profile_file(path: str | Path) -> ContextProfile:
    """Read one profile. JSON or the manifest's YAML subset, by extension."""
    from ..environment.schema import parse_yaml

    location = Path(path)
    try:
        text = location.read_text(encoding="utf-8")
    except OSError as error:
        raise LexiconError(f"cannot read lexicon profile {location}: {error}") from None

    try:
        document = (
            json.loads(text) if location.suffix.lower() == ".json"
            else parse_yaml(text)
        )
    except (ValueError, ManifestError) as error:
        raise LexiconError(f"{location} is not a valid profile: {error}") from None

    return _parse(document, name=location.stem, source=location.as_posix())


def load_profiles(root: str | Path = ".") -> ProfileSet:
    """Every profile under `.slpie/lexicon/`, and every one that failed."""
    directory = Path(root) / PROFILE_DIR
    if not directory.is_dir():
        return ProfileSet()

    found: list[ContextProfile] = []
    errors: list[str] = []
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in EXTENSIONS:
            continue
        try:
            found.append(load_profile_file(path))
        except LexiconError as error:
            errors.append(str(error))
    return ProfileSet(tuple(found), tuple(errors))


def resolve(
    context: Mapping[str, Any] | None = None,
    *,
    root: str | Path = ".",
    base: Lexicon | None = None,
) -> Lexicon:
    """The lexicon for a request's context.

    `context` is what the gateway wrote onto the `Request` — so this reads an
    identity that has already been established rather than establishing one.
    An unknown or absent profile yields the platform's own words, which is the
    right default: the reader sees a real console rather than an error about
    vocabulary.

    A profile that names a term the platform does not define, or tries to rename
    a control, raises — because that is an authored file and a silent no-op
    there is a rename somebody believes happened.
    """
    lexicon = base if base is not None else default()
    wanted = str((context or {}).get("profile") or "").strip()
    if not wanted:
        return lexicon

    profile = load_profiles(root).get(wanted)
    if profile is None:
        return lexicon
    return lexicon.overlay(profile.terms, name=profile.name)
