"""One notebook environment per user, and what that user is allowed to see.

Clients work in notebooks. So the product is not a library somebody imports — it
is a **workspace**: a running JupyterLab, dedicated to one user, holding exactly
the datasets and environment variables that user's scope entitles them to, with
a resource allocation the platform decided rather than the user requested.

Behind each workspace is a `SimulatedWorld`. That is not a shortcut — it is the
same decision §2 already took for the whole platform: the simulator materialises
**real artifacts on a real filesystem**, and the same discoverers run over them
as over a customer's tree. A user exploring their workspace is exploring
something the platform can reason about honestly, and flipping one tag points
the identical workspace at their live environment.

============  ===============================================================
`workspace`   the unit: a user, a tenant scope, an allocation, its datasets
`dataset`     a grant — what a scope may see, and at which tier it lives
`quota`       what one workspace may consume, and what the tenant has left
`spawn`       the `Spawner` protocol a runtime implements
`store`       the `ObjectStore` protocol a storage tier implements
`plane`       the control plane: place, provision, list, reclaim
============  ===============================================================

**Segregation is decided here, not by the backend.** Every read of a dataset goes
through `AccessEngine.check` against the caller's `Scope(tenant, realm)` before a
bucket, a volume or a path is named. A misconfigured bucket policy therefore
cannot widen access, because the bucket was never asked. This is the same
default-deny, explicit-deny-wins resolution `slpie/rbac/engine.py` already
implements and `slpie/capture/firewall.py` already mirrors — a third
implementation would be a third place for the rule to be subtly different.

**This package is stdlib-only** (invariant 4). It holds the model and the rules;
Kubernetes, S3 and JupyterLab live in `slpie_enterprise/` behind the two
protocols here. A control plane that could not be tested without a cluster would
be a control plane nobody could test.
"""

from .dataset import Dataset, DatasetGrant, Tier, Visibility
from .plane import ControlPlane, Placement, Provisioned
from .quota import Allocation, Quota, Usage
from .spawn import Runtime, Spawner, SpawnRequest, Started
from .store import ObjectRef, ObjectStore, StoreError
from .workspace import State, Workspace, WorkspaceError

__all__ = [
    "Allocation",
    "ControlPlane",
    "Dataset",
    "DatasetGrant",
    "ObjectRef",
    "ObjectStore",
    "Placement",
    "Provisioned",
    "Quota",
    "Runtime",
    "SpawnRequest",
    "Spawner",
    "Started",
    "State",
    "StoreError",
    "Tier",
    "Usage",
    "Visibility",
    "Workspace",
    "WorkspaceError",
]
