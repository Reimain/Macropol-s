"""The shared tier: corpora many tenants read, in object storage.

Works against S3 and anything that speaks it — MinIO, R2, Ceph — because the
endpoint is a parameter rather than an assumption.

Two decisions worth stating:

**The bucket policy is not the security boundary.** `ObjectRef` refused anything
outside the prefix before this class was reached, and `list` re-checks every key
it yields. A bucket policy is a control that lives in somebody else's console, is
invisible from this codebase, and is wrong about once per migration; treating it
as the boundary is how a cross-tenant read becomes possible without any code
changing.

**`boto3` is imported inside the constructor.** Importing at module scope would
make `slpie_enterprise.storage` unimportable without it, which would take the
filesystem tier down with the cloud one on a machine that only needs the former.
"""

from __future__ import annotations

from typing import Any, Iterator

from slpie.workspace import ObjectRef
from slpie.workspace.store import StoreError, within


class S3Store:
    """`ObjectStore` over an S3-compatible bucket."""

    tier = "shared"

    def __init__(
        self,
        bucket: str,
        *,
        client: Any = None,
        endpoint_url: str = "",
        region: str = "",
        **options: Any,
    ) -> None:
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        if client is not None:
            self._client = client
            return
        try:
            import boto3
        except ImportError as error:      # pragma: no cover - depends on install
            raise StoreError(
                "the shared tier needs boto3, which is an optional extra: "
                "`pip install -e '.[cloud]'`. The working tier does not, so a "
                "filesystem-only deployment is unaffected"
            ) from error
        settings: dict[str, Any] = dict(options)
        if endpoint_url:
            settings["endpoint_url"] = endpoint_url
        if region:
            settings["region_name"] = region
        self._client = boto3.client("s3", **settings)

    def put(self, ref: ObjectRef, content: bytes) -> int:
        self._client.put_object(Bucket=self.bucket, Key=ref.path, Body=content)
        return len(content)

    def get(self, ref: ObjectRef) -> bytes:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=ref.path)
        except Exception as error:  # noqa: BLE001 - botocore raises broadly
            raise StoreError(f"no object at {ref.path}: {error}") from error
        return response["Body"].read()

    def exists(self, ref: ObjectRef) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=ref.path)
            return True
        except Exception:  # noqa: BLE001
            return False

    def list(self, prefix: str) -> Iterator[str]:
        token = ""
        while True:
            request: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                request["ContinuationToken"] = token
            page = self._client.list_objects_v2(**request)
            for item in page.get("Contents", []):
                key = item["Key"]
                # A bucket prefix is a string match: `acme` matches `acme-corp`.
                # Re-checked on a segment boundary here, because that difference
                # is a cross-tenant read that looks like a working filter.
                if within(prefix, key):
                    yield key
            if not page.get("IsTruncated"):
                return
            token = page.get("NextContinuationToken", "")

    def delete(self, ref: ObjectRef) -> bool:
        self._client.delete_object(Bucket=self.bucket, Key=ref.path)
        return True

    def size(self, ref: ObjectRef) -> int:
        try:
            head = self._client.head_object(Bucket=self.bucket, Key=ref.path)
        except Exception as error:  # noqa: BLE001
            raise StoreError(f"no object at {ref.path}: {error}") from error
        return int(head["ContentLength"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier, "backend": "s3", "bucket": self.bucket,
            "endpoint_url": self.endpoint_url,
        }
