"""Two tiers, one protocol, and a router so callers never choose.

`Tier.WORK` is per-user, mutable, small, and read constantly — a filesystem or a
mounted volume. `Tier.SHARED` is a corpus many tenants read and nobody writes —
object storage, where the price of a byte matters more than the latency.

They are different backends because they have opposite requirements, and one
protocol because nothing above this should have to know that.

`TieredStore` routes on the tier of the dataset, so a caller writes
`store.get(ref)` and the tier decides where that lands. The alternative — every
caller picking a backend — puts the tenant-prefix check in every caller, which is
exactly where it eventually gets forgotten.
"""

from .filesystem import FilesystemStore
from .s3 import S3Store
from .tiered import TieredStore

__all__ = ["FilesystemStore", "S3Store", "TieredStore"]
