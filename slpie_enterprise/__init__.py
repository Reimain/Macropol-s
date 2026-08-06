"""Ring 1 — everything the kernel deliberately refuses to know about.

`slpie/` is stdlib-only, offline-capable, and installs with zero third-party
packages. That is invariant 4, and it is asserted by a CI job that installs with
no extras and checks what came with it. It is also incompatible with running a
fleet of notebook servers on Kubernetes against S3.

The resolution is not to weaken the kernel. It is that this package **implements
protocols the kernel published** and the kernel never imports it:

    ring 0   slpie/               stdlib · offline · every test unchanged
    ring 1   slpie_enterprise/    kubernetes · boto3 · optional extras
    ring 2   clients/             React web · Tauri desktop · React Native

============================  =============================================
`spawn.kubernetes`            `Spawner` — a namespace, PVC and pod per user
`storage.filesystem`          `ObjectStore` — the working tier
`storage.s3`                  `ObjectStore` — the shared-corpus tier
`storage.tiered`              routes by `Tier`, so callers never choose
============================  =============================================

**Nothing here decides who may see what.** By the time a request reaches this
package the RBAC decision has been made, the quota checked, and the dataset list
narrowed — see `slpie/workspace/plane.py`. A spawner that could re-derive
entitlement would be a second place for the answer to be different.

`tests/test_enterprise_boundaries.py` asserts both directions with the same
`ast` walk `tests/test_slpie_boundaries.py` uses: ring 1 may import ring 0's
public API, and ring 0 may not import ring 1 at all.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
