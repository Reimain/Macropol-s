"""The hash chain — why the ledger's history cannot be quietly edited.

Each record's hash covers its own content *and* the previous record's hash, so
altering any historical record invalidates every hash after it. That is what
makes "immutable" a checkable property rather than a promise about how carefully
the code was written.

The canonical form matters as much as the hash function. Two processes appending
the same event must produce byte-identical input to the digest, or verification
fails for a reason that has nothing to do with tampering — so serialisation here
is sorted, separator-fixed, and non-ASCII-preserving, and nothing may pass
through it that `json` cannot render deterministically.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from ..errors import ChainBroken

#: The hash of the record before the first one. A fixed, recognisable root.
GENESIS = "0" * 64


def canonical(payload: Mapping[str, Any]) -> str:
    """Deterministic JSON — the exact bytes that get hashed."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def record_hash(
    *,
    sequence: int,
    previous_hash: str,
    event_id: str,
    kind: str,
    subject: str,
    payload: Mapping[str, Any],
    occurred_at: int,
    actor: str,
) -> str:
    """The digest for one ledger record.

    Chained through ``previous_hash``: recomputing any record requires every
    record before it, so a tamperer must rewrite the whole tail.
    """
    material = canonical({
        "sequence": sequence,
        "previous_hash": previous_hash,
        "event_id": event_id,
        "kind": kind,
        "subject": subject,
        "payload": payload,
        "occurred_at": occurred_at,
        "actor": actor,
    })
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def verify_chain(records: Sequence["LedgerRecord"]) -> None:  # noqa: F821 - see record.py
    """Raise :class:`ChainBroken` at the first record that does not verify.

    Two failure modes, both reported the same way because both mean the same
    thing — the history on disk is not the history that was written:

    * a record whose stored hash does not match its content (edited in place)
    * a record whose ``previous_hash`` does not match its predecessor (a record
      was removed, inserted, or reordered)
    """
    previous = GENESIS
    expected_sequence = 1

    for record in records:
        if record.sequence != expected_sequence:
            raise ChainBroken(record.sequence, str(expected_sequence), str(record.sequence))
        if record.previous_hash != previous:
            raise ChainBroken(record.sequence, previous, record.previous_hash)
        recomputed = record.compute_hash()
        if recomputed != record.hash:
            raise ChainBroken(record.sequence, recomputed, record.hash)
        previous = record.hash
        expected_sequence += 1


def head_digest(records: Sequence["LedgerRecord"]) -> str:  # noqa: F821
    """The hash of the last record — the whole history in one value.

    Two ledgers with the same head digest hold identical histories, which is what
    lets a snapshot id be compared across runs and across machines.
    """
    return records[-1].hash if records else GENESIS
