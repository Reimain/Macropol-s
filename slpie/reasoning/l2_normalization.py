"""L2 — the layer where two dialects of the same thing become one thing.

Everything here delegates to `slpie.normalize`, which holds the actual rules.
What this layer contributes is the *provenance*: each normalisation becomes an
`Enrichment` citing the L1 fact it normalised, so the chain from a canonical
identity back to the manifest line that spelled it differently stays walkable.

That is why normalisation is a layer at all rather than a function called in
passing. `Typing_Extensions` and `typing-extensions` collapsing to one node is
a *conclusion*, and when somebody asks why the graph shows one package where
their manifest shows two, the answer has to be more than "it just does".

Three things are normalised, and the third is the interesting one:

* **identity** — purl canonicalisation, so `pkg:npm/@types/node` and
  `pkg:npm/%40types/node` are the same node id.
* **version** — into the shared algebra, keeping pins and ranges distinct.
* **licence** — into SPDX *or into nothing*. An unrecognised licence produces no
  enrichment and a stated reason, because a guessed licence is a compliance
  answer somebody will act on.
"""

from __future__ import annotations

from typing import Any, Callable

from ..domain.reasoning import Enrichment, LayerNumber, ReasoningStep
from ..errors import IdentityError, VersionError
from ..normalize.licenses import normalize_license
from ..normalize.purl import canonical_purl
from ..normalize.versions import canonical_ecosystem, normalize_version
from .layer import BaseLayer, LayerContext

#: Observation properties that carry a licence string, in the order publishers
#: actually populate them.
LICENSE_KEYS = ("license", "licence", "licenses", "license_expression")

#: Observation properties that carry a version, pinned or not.
VERSION_KEYS = ("version", "resolved", "range", "requirement")


class NormalizationLayer(BaseLayer):
    """Canonical identity, comparable versions, SPDX or an honest nothing."""

    name = "normalization"
    number = LayerNumber.NORMALIZATION

    def execute(
        self,
        context: LayerContext,
        emit: Callable[[Enrichment], None],
        step: Callable[[ReasoningStep], None],
    ) -> None:
        first_order = {
            enrichment.subject: enrichment
            for enrichment in context.of_layer("discovery")
        }

        identities = 0
        versions = 0
        licences = 0
        refused: list[str] = []
        seen: set[str] = set()

        for observation in context.observations:
            for identity in (observation.subject, observation.object):
                if not identity or identity in seen:
                    continue
                seen.add(identity)
                parent = first_order.get(identity)
                chain = (parent.id,) if parent else ()
                if not chain:
                    # Nothing to cite means nothing to assert. L1 refused this
                    # observation for want of evidence; normalising it anyway
                    # would smuggle it back in one layer later.
                    continue

                if identity.startswith("pkg:"):
                    try:
                        canonical = canonical_purl(identity)
                    except IdentityError as error:
                        refused.append(f"{identity}: {error}")
                        continue
                    rendered = canonical.to_string()
                    if rendered != identity:
                        emit(self.enrich(
                            identity, "canonical_identity", rendered,
                            derived_from=chain,
                            confidence=parent.confidence,
                            rationale=(
                                f"{identity} and {rendered} are one package under "
                                f"the {canonical.type} naming rules"
                            ),
                        ))
                    emit(self.enrich(
                        identity, "coordinate", canonical.coordinate,
                        derived_from=chain, confidence=parent.confidence,
                        rationale="the version-independent identity two dialects share",
                    ))
                    identities += 1

            parent = first_order.get(observation.subject)
            if parent is None:
                continue

            ecosystem = _ecosystem_of(observation.subject)
            for key in VERSION_KEYS:
                raw = observation.properties.get(key)
                if not raw:
                    continue
                try:
                    normalised = normalize_version(str(raw), ecosystem=ecosystem)
                except VersionError:
                    refused.append(f"{observation.subject}: {raw!r} is not a version")
                    break
                if normalised and normalised != str(raw):
                    emit(self.enrich(
                        observation.subject, f"normalised_{key}", normalised,
                        derived_from=(parent.id,), confidence=parent.confidence,
                        rationale=f"{raw!r} in {ecosystem} form is {normalised}",
                    ))
                versions += 1
                break

            for key in LICENSE_KEYS:
                raw = observation.properties.get(key)
                if not raw:
                    continue
                reading = normalize_license(raw)
                if reading.expression:
                    emit(self.enrich(
                        observation.subject, "license", reading.expression,
                        derived_from=(parent.id,), confidence=parent.confidence,
                        rationale=reading.reason,
                    ))
                    licences += 1
                else:
                    refused.append(f"{observation.subject}: {reading.reason}")
                break

        context.facts["normalised_identities"] = identities
        context.facts["normalised_versions"] = versions
        context.facts["normalised_licenses"] = licences
        context.facts["normalisation_refusals"] = refused

        step(ReasoningStep(
            claim=(
                f"canonicalised {identities} identities, {versions} versions "
                f"and {licences} licence expressions"
            ),
            layer=self.name, operation="normalize",
            evidence=tuple(context.evidence.values())[:8],
        ))

        if refused:
            step(ReasoningStep(
                claim=(
                    f"{len(refused)} value(s) could not be normalised and were "
                    f"left unresolved rather than guessed: {refused[0]}"
                    + (f" (and {len(refused) - 1} more)" if len(refused) > 1 else "")
                ),
                layer=self.name, operation="normalize", confidence=0.0,
            ))


def _ecosystem_of(identity: str) -> str:
    """The ecosystem a purl names, for the version dialect. `generic` otherwise."""
    if not identity.startswith("pkg:"):
        return "generic"
    kind = identity[4:].split("/", 1)[0]
    return canonical_ecosystem(kind) or "generic"
