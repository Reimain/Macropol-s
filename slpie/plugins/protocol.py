"""The plugin wire protocol — newline-delimited JSON over stdio.

This is the only honest route to polyglot analysis. A Go analyzer should use
`go/ast` and a TypeScript one the TS compiler API; a Python approximation of
either would be a permanent source of quiet inaccuracy. So external plugins run
as subprocesses and speak JSON.

An :class:`Observation` is what a plugin sends back. It names a subject, an
object and a relationship, and it *must* carry evidence — the same invariant the
graph enforces, applied at the boundary so an external plugin cannot introduce
an unjustified edge that in-process code could not.

The protocol is deliberately tiny: `describe` and `discover`. Every extension
point that needs more can be expressed as a discoverer that produces different
observations, and a small protocol is one an implementer in another language can
finish in an afternoon.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..domain.evidence import Evidence, EvidenceKind, SourceLocation
from ..errors import PluginProtocolError

PROTOCOL_VERSION = "1"


@dataclass(frozen=True, slots=True)
class Message:
    """One request or response frame."""

    method: str = ""
    id: int = 0
    params: Mapping[str, Any] = field(default_factory=dict)
    result: Mapping[str, Any] = field(default_factory=dict)
    error: str = ""

    def encode(self) -> str:
        body: dict[str, Any] = {"id": self.id}
        if self.method:
            body["method"] = self.method
            body["params"] = dict(self.params)
        elif self.error:
            body["error"] = self.error
        else:
            body["result"] = dict(self.result)
        return json.dumps(body)

    @classmethod
    def decode(cls, line: str) -> "Message":
        try:
            body = json.loads(line)
        except ValueError as error:
            raise PluginProtocolError(
                f"plugin sent a line that is not JSON: {line[:200]!r} ({error})"
            ) from None
        if not isinstance(body, dict):
            raise PluginProtocolError(f"expected a JSON object, got {type(body).__name__}")
        return cls(
            method=str(body.get("method", "")),
            id=int(body.get("id", 0)),
            params=body.get("params") or {},
            result=body.get("result") or {},
            error=str(body.get("error", "")),
        )


@dataclass(frozen=True, slots=True)
class Observation:
    """One thing a plugin observed, with the evidence for it.

    The evidence is not optional at this boundary either. A plugin that cannot
    cite a file and a line has not observed anything, and letting an external
    process assert a relationship without one would put a hole in the platform's
    central invariant exactly where it is least visible.
    """

    kind: str                        # "imports", "depends_on", "declares", ...
    subject: str                     # purl or urn
    object: str = ""                 # purl or urn, for relationships
    evidence: Evidence | None = None
    properties: Mapping[str, Any] = field(default_factory=dict)
    qualifier: str = ""

    @property
    def is_relationship(self) -> bool:
        return bool(self.object)

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "kind": self.kind, "subject": self.subject,
            "properties": dict(self.properties),
        }
        if self.object:
            body["object"] = self.object
        if self.qualifier:
            body["qualifier"] = self.qualifier
        if self.evidence is not None:
            body["evidence"] = {
                "kind": self.evidence.kind.value,
                "location": self.evidence.location.to_dict(),
                "excerpt": self.evidence.excerpt,
            }
        return body

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        plugin_id: str,
        plugin_version: str = "0",
        allowed: Sequence[EvidenceKind] = (),
    ) -> "Observation":
        """Parse and validate an observation from a plugin.

        ``allowed`` is the plugin's declared evidence kinds. Enforced here
        because confidence is derived from evidence kind — so the freedom to
        pick your own kind is the freedom to pick your own confidence.
        """
        if not data.get("kind"):
            raise PluginProtocolError(f"{plugin_id}: an observation must name its kind")
        if not data.get("subject"):
            raise PluginProtocolError(f"{plugin_id}: an observation must name a subject")

        raw = data.get("evidence")
        if not isinstance(raw, Mapping):
            raise PluginProtocolError(
                f"{plugin_id}: observation {data['kind']!r} on {data['subject']!r} "
                f"carries no evidence"
            )

        try:
            kind = EvidenceKind(raw.get("kind", ""))
        except ValueError:
            raise PluginProtocolError(
                f"{plugin_id}: unknown evidence kind {raw.get('kind')!r}"
            ) from None

        if allowed and kind not in allowed:
            raise PluginProtocolError(
                f"{plugin_id}: claimed evidence kind {kind.value!r}, which it did not "
                f"declare; declared kinds are "
                f"{', '.join(k.value for k in allowed) or 'none'}"
            )

        location = raw.get("location") or {}
        if not location.get("uri"):
            raise PluginProtocolError(
                f"{plugin_id}: evidence must name the file it came from"
            )

        return cls(
            kind=str(data["kind"]),
            subject=str(data["subject"]),
            object=str(data.get("object", "")),
            qualifier=str(data.get("qualifier", "")),
            properties=dict(data.get("properties", {})),
            evidence=Evidence(
                kind=kind,
                location=SourceLocation(
                    uri=str(location["uri"]),
                    line=int(location.get("line", 0) or 0),
                    column=int(location.get("column", 0) or 0),
                ),
                extractor=plugin_id,
                extractor_version=plugin_version,
                excerpt=str(raw.get("excerpt", ""))[:500],
                content_digest=str(raw.get("content_digest", "")),
            ),
        )

    def __str__(self) -> str:
        if self.object:
            return f"{self.subject} -{self.kind}-> {self.object}"
        return f"{self.subject} ({self.kind})"


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """What one `discover` call produced, including what it could not do."""

    observations: tuple[Observation, ...] = ()
    errors: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "observations": [o.to_dict() for o in self.observations],
            "errors": list(self.errors),
            "skipped": list(self.skipped),
        }

    def __len__(self) -> int:
        return len(self.observations)

    def __iter__(self):
        return iter(self.observations)


def parse_result(
    result: Mapping[str, Any],
    *,
    plugin_id: str,
    plugin_version: str = "0",
    allowed: Sequence[EvidenceKind] = (),
) -> DiscoveryResult:
    """Turn a plugin's `discover` result into validated observations.

    A malformed observation is dropped and reported rather than aborting the
    batch. One bad line from a third-party analyzer must not cost the platform
    the forty good ones beside it — but it must never pass silently either.
    """
    observations: list[Observation] = []
    errors: list[str] = list(str(item) for item in result.get("errors", ()))

    for raw in result.get("observations", ()):
        if not isinstance(raw, Mapping):
            errors.append(f"{plugin_id}: observation is not an object")
            continue
        try:
            observations.append(Observation.from_dict(
                raw, plugin_id=plugin_id, plugin_version=plugin_version, allowed=allowed,
            ))
        except PluginProtocolError as error:
            errors.append(str(error))

    return DiscoveryResult(
        observations=tuple(observations),
        errors=tuple(errors),
        skipped=tuple(str(item) for item in result.get("skipped", ())),
    )
