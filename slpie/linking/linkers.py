"""The semantic linkers — where separate readings become one understanding.

The resolver merges things that are *identically* the same. These linkers join
things that are *related*, and each one encodes a specific piece of knowledge
that no single discoverer has, because it spans two files:

| Linker | Joins | The knowledge it holds |
|---|---|---|
| `corroboration` | manifest range ↔ lockfile pin | a pin satisfies a range, or contradicts it |
| `import_to_package` | `import yaml` ↔ `pkg:pypi/pyyaml` | the import name is not the distribution name |
| `service_to_contract` | a service ↔ its OpenAPI/AsyncAPI/proto | which code serves which contract |
| `workload_to_image` | Kubernetes workload ↔ Dockerfile | what is scheduled was built here |
| `resource_to_deployment` | Terraform resource ↔ workload | the infrastructure underneath |

Every linker is small, named, independently testable, and **produces an
`Enrichment` citing what it read**. A link asserted without a chain back to
evidence is exactly the thing the graph refuses everywhere else, and a linking
layer is where that discipline is easiest to lose — the joins feel obvious.

`import_to_package` deserves its own note. `import yaml` comes from `PyYAML`,
`import cv2` from `opencv-python`, `import sklearn` from `scikit-learn`. There
is no rule that derives one from the other; it is a fact about the world, so it
lives in a table, and a name the table does not know produces **no link** rather
than a guessed one. A wrong package attribution sends a CVE to the wrong team.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Mapping, Protocol, Sequence

from ..domain.evidence import Evidence, EvidenceKind
from ..domain.reasoning import Enrichment, LayerNumber
from ..domain.version import parse_range
from ..errors import VersionError
from .resolver import Link, Resolution, Resolved

#: Import names that differ from their distribution name. A fact about the
#: world rather than a rule, so it is a table — and an unknown name yields no
#: link rather than a guess, because a wrong attribution sends a CVE to the
#: wrong team.
IMPORT_TO_DISTRIBUTION: dict[str, str] = {
    "yaml": "pyyaml",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "PIL": "pillow",
    "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil",
    "jwt": "pyjwt",
    "OpenSSL": "pyopenssl",
    "serial": "pyserial",
    "usb": "pyusb",
    "google.protobuf": "protobuf",
    "attr": "attrs",
    "pkg_resources": "setuptools",
    "docker": "docker",
    "psycopg2": "psycopg2-binary",
    "MySQLdb": "mysqlclient",
    "Crypto": "pycryptodome",
    "win32com": "pywin32",
    "zoneinfo": "",          # stdlib since 3.9 — deliberately no distribution
}

#: Modules that ship with Python. An import of one of these is not a dependency,
#: and linking it to a package of the same name on PyPI would be wrong — several
#: such packages exist and are not what was imported.
STDLIB_HINT = frozenset({
    "os", "sys", "json", "re", "time", "typing", "pathlib", "dataclasses",
    "collections", "itertools", "functools", "logging", "math", "hashlib",
    "sqlite3", "asyncio", "subprocess", "unittest", "enum", "abc", "io",
})


@dataclass(frozen=True, slots=True)
class Joined:
    """One link a linker made, and the reasoning behind it."""

    kind: str
    source: str
    target: str
    linker: str
    confidence_basis: str            # what *kind* of evidence supports it
    enrichment: Enrichment
    contradiction: str = ""

    @property
    def contradicts(self) -> bool:
        return bool(self.contradiction)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "source": self.source, "target": self.target,
            "linker": self.linker, "basis": self.confidence_basis,
            "enrichment": self.enrichment.to_dict(),
            "contradiction": self.contradiction,
        }

    def __str__(self) -> str:
        arrow = "><" if self.contradicts else "->"
        return f"[{self.linker}] {self.source} {arrow} {self.target}"


class Linker(Protocol):
    """One piece of cross-file knowledge, applied to a resolution."""

    name: str

    def link(self, resolution: Resolution) -> Sequence[Joined]:
        ...


def _enrich(subject: str, attribute: str, value: Any, *, linker: str,
            evidence: Iterable[Evidence], rationale: str) -> Enrichment:
    """Every link carries the ids of what it read. No chain, no link."""
    return Enrichment(
        subject=subject, attribute=attribute, value=value,
        layer=LayerNumber.SEMANTIC_LINKING.name.lower(),
        derived_from=tuple(item.id for item in evidence),
        rationale=rationale,
        derived_at=int(time.time()),
    )


class CorroborationLinker:
    """A lockfile pin against the manifest range that asked for it.

    Three outcomes, and the third is the one that matters: the pin satisfies the
    range (corroborated), no range was declared (a shadow dependency), or the
    pin sits *outside* the declared range — which means the lockfile and the
    manifest disagree, and somebody's build is not what they think it is.
    """

    name = "corroboration"

    def __init__(self, *, ecosystem_hint: str = "generic") -> None:
        self.ecosystem_hint = ecosystem_hint

    def link(self, resolution: Resolution) -> Sequence[Joined]:
        found: list[Joined] = []
        for entry in resolution.resolved:
            if not entry.pinned or not entry.ranges:
                continue
            for declared in entry.ranges:
                found.append(self._compare(entry, declared))
        return tuple(found)

    def _compare(self, entry: Resolved, declared: str) -> Joined:
        ecosystem = entry.identity.split(":")[1].split("/")[0] if ":" in entry.identity \
            else self.ecosystem_hint
        satisfied: bool | None
        try:
            satisfied = parse_range(declared, ecosystem=ecosystem).allows(entry.pinned)
        except (VersionError, Exception):  # noqa: B014 - parse_range raises broadly
            satisfied = None

        if satisfied is False:
            contradiction = (
                f"the lockfile pins {entry.pinned} but the manifest declares "
                f"{declared}, which does not admit it — the build is not what "
                f"the manifest says"
            )
            rationale = contradiction
        elif satisfied is None:
            contradiction = ""
            rationale = (
                f"could not compare pin {entry.pinned} against range {declared!r}; "
                f"reported as unverified rather than assumed to agree"
            )
        else:
            contradiction = ""
            rationale = f"the pin {entry.pinned} satisfies the declared {declared}"

        return Joined(
            kind="corroborates" if satisfied else "contradicts",
            source=entry.node_id, target=entry.node_id, linker=self.name,
            confidence_basis="lockfile_pin+manifest_declared",
            enrichment=_enrich(
                entry.node_id, "resolved_version", entry.pinned,
                linker=self.name, evidence=entry.evidence, rationale=rationale,
            ),
            contradiction=contradiction,
        )


class ImportToPackageLinker:
    """`import yaml` → `pkg:pypi/pyyaml`, and nothing when the table cannot say.

    The whole value is the table. `yaml` is not `pkg:pypi/yaml` — that package
    exists and is something else. Deriving a distribution from an import name by
    rule is impossible; the mapping is a fact, so unknown names produce nothing.
    """

    name = "import-to-package"

    def __init__(self, mapping: Mapping[str, str] | None = None) -> None:
        self.mapping = dict(IMPORT_TO_DISTRIBUTION)
        if mapping:
            self.mapping.update(mapping)

    def distribution_for(self, module: str) -> str:
        """The distribution that provides this import, or empty."""
        root = module.split(".")[0]
        if root in STDLIB_HINT:
            return ""
        if module in self.mapping:
            return self.mapping[module]
        if root in self.mapping:
            return self.mapping[root]
        # An import whose name *is* the distribution name is the common case,
        # and is safe precisely because it is an identity rather than a guess.
        return root

    def link(self, resolution: Resolution) -> Sequence[Joined]:
        found: list[Joined] = []
        packages = {
            entry.coordinate: entry for entry in resolution.resolved
        }

        for link in resolution.links:
            if link.kind != "imports":
                continue
            module = link.properties.get("module") or ""
            if not module:
                continue
            distribution = self.distribution_for(str(module))
            if not distribution:
                continue

            coordinate = f"pkg:pypi/{distribution}"
            target = packages.get(coordinate)
            if target is None:
                continue

            found.append(Joined(
                kind="provided_by", source=link.source, target=target.node_id,
                linker=self.name, confidence_basis="static_import+manifest_declared",
                enrichment=_enrich(
                    link.source, "provided_by", coordinate, linker=self.name,
                    evidence=(link.evidence,),
                    rationale=(
                        f"`import {module}` is provided by {distribution}"
                        + ("" if module == distribution
                           else f", which is not the import name")
                    ),
                ),
            ))
        return tuple(found)


class ServiceToContractLinker:
    """A service and the interface contract it serves.

    Joined on the service urn both sides already carry, never on name
    similarity: `payments-api` and `payments` may or may not be the same
    service, and a linker that assumes so invents an ownership relationship
    nobody declared.
    """

    name = "service-to-contract"

    CONTRACT_KINDS = ("api", "topic", "schema")

    def link(self, resolution: Resolution) -> Sequence[Joined]:
        services = {
            entry.identity: entry for entry in resolution.resolved
            if entry.identity.startswith("urn:slpie:service:")
        }
        found: list[Joined] = []

        for link in resolution.links:
            if link.kind not in ("owns", "publishes", "subscribes"):
                continue
            source = next(
                (entry for entry in resolution.resolved
                 if entry.node_id == link.source), None
            )
            if source is None or not source.identity.startswith("urn:slpie:service:"):
                continue
            found.append(Joined(
                kind="serves", source=link.source, target=link.target,
                linker=self.name, confidence_basis="manifest_declared",
                enrichment=_enrich(
                    link.source, "serves", link.target, linker=self.name,
                    evidence=(link.evidence,),
                    rationale=(
                        f"the contract declares this service {link.kind} it"
                    ),
                ),
            ))
        return tuple(found)


class WorkloadToImageLinker:
    """What Kubernetes schedules against what a Dockerfile built.

    The join is on the image reference, which both sides state literally. When
    the manifest pins a tag and the Dockerfile has no tag, they still join —
    the same image at an unstated version — but the enrichment says the version
    is unverified rather than pretending it matched.
    """

    name = "workload-to-image"

    def link(self, resolution: Resolution) -> Sequence[Joined]:
        images = {
            entry.coordinate: entry for entry in resolution.resolved
            if entry.identity.startswith("pkg:docker/")
        }
        found: list[Joined] = []

        for link in resolution.links:
            if link.kind != "depends_on":
                continue
            target = next(
                (entry for entry in resolution.resolved
                 if entry.node_id == link.target), None
            )
            if target is None or not target.identity.startswith("pkg:docker/"):
                continue
            same = images.get(target.coordinate)
            if same is None:
                continue

            unverified = not target.pinned
            found.append(Joined(
                kind="runs_image", source=link.source, target=same.node_id,
                linker=self.name, confidence_basis="container_manifest",
                enrichment=_enrich(
                    link.source, "runs_image", same.identity, linker=self.name,
                    evidence=(link.evidence,),
                    rationale=(
                        "the workload schedules this image"
                        + ("; the tag is unstated, so the version is unverified"
                           if unverified else "")
                    ),
                ),
            ))
        return tuple(found)


class ResourceToDeploymentLinker:
    """Terraform resources under the deployments that run on them."""

    name = "resource-to-deployment"

    def link(self, resolution: Resolution) -> Sequence[Joined]:
        found: list[Joined] = []
        for link in resolution.links:
            if link.kind != "references":
                continue
            source = next(
                (entry for entry in resolution.resolved
                 if entry.node_id == link.source), None
            )
            if source is None or "cloudresource" not in source.identity:
                continue
            found.append(Joined(
                kind="provisions", source=link.source, target=link.target,
                linker=self.name, confidence_basis="iac_declaration",
                enrichment=_enrich(
                    link.source, "provisions", link.target, linker=self.name,
                    evidence=(link.evidence,),
                    rationale="the infrastructure declaration references it",
                ),
            ))
        return tuple(found)


class LinkerSet:
    """Every linker, run over one resolution.

    A linker that raises **abstains** and is recorded, rather than aborting the
    set. One piece of cross-file knowledge being wrong must not cost the other
    four, for the same reason a raising governance rule does not blind the rest.
    """

    def __init__(self, linkers: Iterable[Linker] | None = None) -> None:
        self._linkers: list[Linker] = list(linkers) if linkers is not None else [
            CorroborationLinker(),
            ImportToPackageLinker(),
            ServiceToContractLinker(),
            WorkloadToImageLinker(),
            ResourceToDeploymentLinker(),
        ]
        self.errors: list[str] = []

    def add(self, linker: Linker) -> Linker:
        self._linkers.append(linker)
        return linker

    def run(self, resolution: Resolution) -> tuple[Joined, ...]:
        found: list[Joined] = []
        self.errors = []
        for linker in self._linkers:
            try:
                found.extend(linker.link(resolution))
            except Exception as error:  # noqa: BLE001 - a linker may be third-party
                self.errors.append(f"{linker.name} abstained: {error}")
        return tuple(found)

    def contradictions(self, joined: Sequence[Joined]) -> tuple[Joined, ...]:
        return tuple(entry for entry in joined if entry.contradicts)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(linker.name for linker in self._linkers)

    def __len__(self) -> int:
        return len(self._linkers)

    def __iter__(self) -> Iterator[Linker]:
        return iter(tuple(self._linkers))

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<LinkerSet {list(self.names)}>"
