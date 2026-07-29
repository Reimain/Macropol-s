"""Merging identities and joining what spans two files.

Two responsibilities that look similar and must not be confused:

* :mod:`~slpie.linking.resolver` **merges on identity**. Two observations become
  one node when their canonical coordinates are equal — never when their names
  look alike. Similarity merging is how ``requests`` and ``requests-oauthlib``
  become one node, after which nothing downstream can tell they were separate.
* :mod:`~slpie.linking.linkers` **joins on knowledge**. `import yaml` is
  `PyYAML`; a lockfile pin answers a manifest range; a Kubernetes workload runs
  the image a Dockerfile built. Each join is a named, independently testable
  piece of cross-file knowledge, and each one cites what it read.

Both refuse rather than guess. An identity that is neither a purl nor an SLPIE
urn is reported unresolved; an import name the table does not know produces no
link. A wrong package attribution routes a CVE to the wrong team, which is worse
than no attribution at all.
"""

from .linkers import (
    IMPORT_TO_DISTRIBUTION,
    STDLIB_HINT,
    CorroborationLinker,
    ImportToPackageLinker,
    Joined,
    Linker,
    LinkerSet,
    ResourceToDeploymentLinker,
    ServiceToContractLinker,
    WorkloadToImageLinker,
)
from .resolver import (
    Link,
    Resolution,
    ResolutionError,
    Resolved,
    Resolver,
    merges_with,
)

__all__ = [
    "IMPORT_TO_DISTRIBUTION",
    "STDLIB_HINT",
    "CorroborationLinker",
    "ImportToPackageLinker",
    "Joined",
    "Link",
    "Linker",
    "LinkerSet",
    "Resolution",
    "ResolutionError",
    "Resolved",
    "Resolver",
    "ResourceToDeploymentLinker",
    "ServiceToContractLinker",
    "WorkloadToImageLinker",
    "merges_with",
]
