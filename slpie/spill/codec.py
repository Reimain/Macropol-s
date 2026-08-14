"""Encoding a block so that what comes back is what went out.

This is the module the whole spill tier rests on, and the property it must hold
is narrow: **decode(encode(x)) == x, or encode refuses.** Anything weaker is
worse than not spilling at all. An out-of-memory kill is loud, immediate and
obviously a failure; a lossy spill produces a complete-looking answer built on
observations whose evidence quietly lost its content digest three stages back,
and nothing anywhere reports a problem.

`slpie/compose/wire.py` reached the same conclusion for process boundaries and
refuses kinds it cannot reconstruct. This is the same decision one layer down,
and it has a specific trap in it:

**`Observation.to_dict()` is not lossless and must not be used here.** It is the
plugin wire format, where a reduced evidence rendering is correct — an external
analyser has no business round-tripping our content digests. But it drops
`Evidence.content_digest` and `Evidence.labels`, and `labels` is where the hash
algorithm for an SBOM checksum lives. Spilling through it would have produced
SBOMs missing exactly the hashes the tree supplied, on precisely the large scans
that spill in the first place.

So observations are encoded field by field with `Evidence.to_dict()`, which *is*
lossless, and a round-trip test asserts equality on the whole object rather than
on the fields somebody remembered to check.

The format is newline-delimited JSON: one record per line. That is what makes a
block streamable — a reader can pull one record without parsing the file, which
is the difference between a bounded window and loading the block back whole.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable, Iterator, Sequence

from ..errors import SpillError


class Unspillable(SpillError):
    """A value cannot be encoded without losing something.

    Carries the type so the caller is told *what* could not be spilled rather
    than that "something" could not. A verb that produces an unspillable kind
    keeps working in memory; it simply does not get a ceiling, and the gap says
    so.
    """

    def __init__(self, subject: Any, detail: str = "") -> None:
        self.subject = type(subject).__name__
        super().__init__(
            f"a {self.subject} cannot be spilled without losing part of it"
            + (f": {detail}" if detail else "")
            + ". It stays in memory rather than being written back wrong"
        )


#: What one record looks like on the wire: `{"t": <type tag>, "v": <payload>}`.
#: Tagged rather than inferred, because a bare dict and an encoded observation
#: are both dicts, and guessing between them on read is how a decoder starts
#: returning plausible wrong objects.
TYPE_KEY = "t"
VALUE_KEY = "v"

OBSERVATION = "obs"
PLAIN = "raw"


def encode_observation(observation: Any) -> dict[str, Any]:
    """One observation → a JSON-safe mapping that reconstructs it exactly.

    Deliberately not `Observation.to_dict()`. See the module docstring: that is
    the plugin wire format and it drops evidence fields this platform depends on.
    """
    evidence = observation.evidence
    return {
        "kind": observation.kind,
        "subject": observation.subject,
        "object": observation.object,
        "qualifier": observation.qualifier,
        "properties": dict(observation.properties),
        # `Evidence.to_dict` round-trips in full, including `content_digest` and
        # `labels`. Asserted by `test_slpie_spill.py`, not assumed.
        "evidence": evidence.to_dict() if evidence is not None else None,
    }


def decode_observation(payload: dict[str, Any]) -> Any:
    """A mapping produced by `encode_observation` → the observation it was."""
    from ..domain.evidence import Evidence
    from ..plugins.protocol import Observation

    raw = payload.get("evidence")
    return Observation(
        kind=payload["kind"],
        subject=payload["subject"],
        object=payload.get("object", ""),
        qualifier=payload.get("qualifier", ""),
        properties=dict(payload.get("properties", {})),
        evidence=Evidence.from_dict(raw) if raw is not None else None,
    )


def _is_observation(value: Any) -> bool:
    """Structural, so a plugin's own observation type spills the same way."""
    return (
        hasattr(value, "kind")
        and hasattr(value, "subject")
        and hasattr(value, "evidence")
        and hasattr(value, "properties")
    )


def encode(value: Any) -> str:
    """One value → one line of JSON. Raises `Unspillable` rather than degrading."""
    if _is_observation(value):
        record = {TYPE_KEY: OBSERVATION, VALUE_KEY: encode_observation(value)}
    elif isinstance(value, (str, int, float, bool, type(None), list, dict)):
        record = {TYPE_KEY: PLAIN, VALUE_KEY: value}
    else:
        raise Unspillable(
            value,
            "only observations and JSON-native values have a lossless encoding",
        )

    try:
        return json.dumps(record, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as error:
        raise Unspillable(value, str(error)) from error


def decode(line: str) -> Any:
    """One line of JSON → the value it encoded."""
    try:
        record = json.loads(line)
    except ValueError as error:
        raise SpillError(
            f"a spilled block holds a line that is not JSON: {error}. The block "
            f"is corrupt; it is not silently skipped, because a short answer "
            f"that looks complete is the failure this tier exists to avoid"
        ) from error

    tag = record.get(TYPE_KEY)
    if tag == OBSERVATION:
        return decode_observation(record[VALUE_KEY])
    if tag == PLAIN:
        return record[VALUE_KEY]
    raise SpillError(
        f"a spilled record carries the unknown type tag {tag!r}; refusing to "
        f"guess what it was"
    )


def encode_all(values: Iterable[Any]) -> Iterator[str]:
    """Lazily, so encoding a million records never holds a million strings."""
    for value in values:
        yield encode(value)


def spillable(value: Any) -> bool:
    """Whether `value` round-trips. Cheap enough to ask before committing."""
    try:
        encode(value)
    except SpillError:
        return False
    return True
