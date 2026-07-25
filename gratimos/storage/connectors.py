"""Connector registry: one URI vocabulary over many backends.

The kernel never imports ``boto3`` or ``deltalake`` at module scope. A connector
declares what it needs, reports whether that dependency is present, and is only
constructed when something actually resolves its scheme. That way a deployment
with no cloud credentials still starts, and a policymaker can *see* which tiers
are unavailable instead of discovering it through an ImportError mid-run.

Supported schemes out of the box::

    mem://name/…            in-process (tests, ephemeral staging)
    file:///abs/path        secured local repository
    s3://bucket/prefix      S3 or any S3-compatible endpoint (needs boto3)
    delta://path            Delta Lake table (needs deltalake)
    gs://bucket/prefix      Google Cloud Storage (needs google-cloud-storage)
    az://container/prefix   Azure Blob Storage (needs azure-storage-blob)
"""

from __future__ import annotations

import importlib.util
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import urlparse

from ..errors import StorageError
from ..ids import digest, now_ns
from .base import MemoryStore, ObjectRef, ObjectStore
from .local import LocalRepository

Factory = Callable[[str, Mapping[str, Any]], ObjectStore]


def _available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):  # pragma: no cover - odd import states
        return False


@dataclass(frozen=True, slots=True)
class ConnectorSpec:
    """A backend the registry knows how to build, present or not."""

    scheme: str
    requires: tuple[str, ...]
    factory: Factory
    description: str = ""
    tier: str = "warm"  # hot | warm | cold — informs spill policy

    @property
    def available(self) -> bool:
        return all(_available(module) for module in self.requires)

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(module for module in self.requires if not _available(module))

    def to_dict(self) -> dict[str, Any]:
        return {
            "scheme": self.scheme,
            "tier": self.tier,
            "available": self.available,
            "requires": list(self.requires),
            "missing": list(self.missing),
            "description": self.description,
        }


class ConnectorRegistry:
    """Resolves URIs to stores, caching one instance per URI."""

    def __init__(self) -> None:
        self._specs: dict[str, ConnectorSpec] = {}
        self._cache: dict[str, ObjectStore] = {}
        self._lock = threading.RLock()

    def register(self, spec: ConnectorSpec) -> ConnectorSpec:
        with self._lock:
            self._specs[spec.scheme] = spec
        return spec

    def spec(self, scheme: str) -> ConnectorSpec | None:
        return self._specs.get(scheme)

    def schemes(self) -> list[str]:
        return sorted(self._specs)

    def catalog(self) -> list[dict[str, Any]]:
        return [self._specs[s].to_dict() for s in self.schemes()]

    def available_schemes(self) -> list[str]:
        return [s for s in self.schemes() if self._specs[s].available]

    def resolve(self, uri: str, **options: Any) -> ObjectStore:
        """Build (or reuse) the store behind a URI."""
        scheme = (urlparse(uri).scheme or "file").lower()
        spec = self._specs.get(scheme)
        if spec is None:
            raise StorageError(
                f"no connector for scheme {scheme!r}; known: {', '.join(self.schemes()) or 'none'}"
            )
        if not spec.available:
            raise StorageError(
                f"connector {scheme!r} needs missing package(s): {', '.join(spec.missing)}. "
                f"install with: pip install 'gratimos[{scheme}]'"
            )
        cache_key = f"{uri}|{sorted(options.items())!r}"
        with self._lock:
            store = self._cache.get(cache_key)
            if store is None:
                store = spec.factory(uri, options)
                self._cache[cache_key] = store
            return store

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()


# --- built-in factories --------------------------------------------------


def _memory_factory(uri: str, options: Mapping[str, Any]) -> ObjectStore:
    name = urlparse(uri).netloc or "memory"
    return MemoryStore(name=name)


def _file_factory(uri: str, options: Mapping[str, Any]) -> ObjectStore:
    parsed = urlparse(uri)
    path = parsed.path or parsed.netloc or "."
    return LocalRepository(path, name=options.get("name", "local"))


def _s3_factory(uri: str, options: Mapping[str, Any]) -> ObjectStore:
    return S3Store(uri, **dict(options))


def _delta_factory(uri: str, options: Mapping[str, Any]) -> ObjectStore:
    return DeltaStore(uri, **dict(options))


class S3Store:
    """Thin S3 adapter. Constructed only when ``boto3`` is importable."""

    def __init__(self, uri: str, *, endpoint_url: str | None = None, **client_kwargs: Any) -> None:
        import boto3  # noqa: PLC0415 - deliberately deferred

        parsed = urlparse(uri)
        self.bucket = parsed.netloc
        self.prefix = parsed.path.strip("/")
        self.name = f"s3:{self.bucket}"
        self._client = boto3.client("s3", endpoint_url=endpoint_url, **client_kwargs)

    def _full(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def put(self, key: str, data: bytes, *, metadata: Mapping[str, str] | None = None) -> ObjectRef:
        self._client.put_object(
            Bucket=self.bucket, Key=self._full(key), Body=bytes(data),
            Metadata={str(k): str(v) for k, v in (metadata or {}).items()},
        )
        return ObjectRef(
            key=key, size=len(data), content_digest=digest(bytes(data)), store=self.name,
            uri=f"s3://{self.bucket}/{self._full(key)}", metadata=dict(metadata or {}),
        )

    def get(self, key: str) -> bytes:
        try:
            return self._client.get_object(Bucket=self.bucket, Key=self._full(key))["Body"].read()
        except Exception as exc:  # noqa: BLE001 - botocore raises client-specific errors
            raise StorageError(f"s3 read failed for {key!r}: {exc}") from exc

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=self._full(key))
            return True
        except Exception:  # noqa: BLE001
            return False

    def delete(self, key: str) -> bool:
        self._client.delete_object(Bucket=self.bucket, Key=self._full(key))
        return True

    def stat(self, key: str) -> ObjectRef:
        head = self._client.head_object(Bucket=self.bucket, Key=self._full(key))
        return ObjectRef(
            key=key, size=int(head.get("ContentLength", 0)),
            content_digest=str(head.get("ETag", "")).strip('"'), store=self.name,
            uri=f"s3://{self.bucket}/{self._full(key)}", metadata=dict(head.get("Metadata", {})),
        )

    def list(self, prefix: str = "") -> Iterator[ObjectRef]:
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self._full(prefix)):
            for item in page.get("Contents", []):
                key = item["Key"]
                relative = key[len(self.prefix) + 1 :] if self.prefix else key
                yield ObjectRef(
                    key=relative, size=int(item.get("Size", 0)),
                    content_digest=str(item.get("ETag", "")).strip('"'), store=self.name,
                    uri=f"s3://{self.bucket}/{key}",
                    ts_ns=int(item["LastModified"].timestamp() * 1e9) if item.get("LastModified") else now_ns(),
                )


class DeltaStore:
    """Delta Lake landing zone.

    Delta is a *table* format, not a blob store, so this adapter exposes the
    table-shaped operations the kernel actually needs (append records, read
    back, inspect version) and refuses the blob operations that have no
    meaning here rather than faking them.
    """

    def __init__(self, uri: str, *, storage_options: Mapping[str, str] | None = None) -> None:
        import deltalake  # noqa: PLC0415 - deliberately deferred

        self._deltalake = deltalake
        self.table_uri = uri[len("delta://") :] if uri.startswith("delta://") else uri
        self.storage_options = dict(storage_options or {})
        self.name = f"delta:{self.table_uri}"

    def append(self, records: list[Mapping[str, Any]], *, mode: str = "append") -> ObjectRef:
        self._deltalake.write_deltalake(
            self.table_uri, records, mode=mode, storage_options=self.storage_options
        )
        return ObjectRef(
            key=self.table_uri, size=len(records), content_digest=digest(records),
            store=self.name, uri=f"delta://{self.table_uri}",
            metadata={"mode": mode, "version": str(self.version())},
        )

    def version(self) -> int:
        return int(self._deltalake.DeltaTable(self.table_uri, storage_options=self.storage_options).version())

    def read(self) -> list[dict[str, Any]]:
        table = self._deltalake.DeltaTable(self.table_uri, storage_options=self.storage_options)
        return table.to_pyarrow_table().to_pylist()

    def put(self, key: str, data: bytes, *, metadata: Mapping[str, str] | None = None) -> ObjectRef:
        raise StorageError("delta connector stores records, not blobs; use append()")

    def get(self, key: str) -> bytes:
        raise StorageError("delta connector stores records, not blobs; use read()")

    def exists(self, key: str) -> bool:
        try:
            self.version()
            return True
        except Exception:  # noqa: BLE001 - table may simply not exist yet
            return False

    def delete(self, key: str) -> bool:
        raise StorageError("delta connector does not support blob deletion")

    def stat(self, key: str) -> ObjectRef:
        return ObjectRef(key=key, size=0, content_digest="", store=self.name, uri=f"delta://{self.table_uri}")

    def list(self, prefix: str = "") -> Iterator[ObjectRef]:
        yield self.stat(self.table_uri)


@dataclass(slots=True)
class _CloudSpec:
    scheme: str
    module: str
    description: str


def default_registry() -> ConnectorRegistry:
    """The registry the kernel boots with."""
    registry = ConnectorRegistry()
    registry.register(ConnectorSpec(
        scheme="mem", requires=(), factory=_memory_factory, tier="hot",
        description="in-process store for tests and ephemeral staging",
    ))
    registry.register(ConnectorSpec(
        scheme="file", requires=(), factory=_file_factory, tier="warm",
        description="path-contained local repository with atomic writes",
    ))
    registry.register(ConnectorSpec(
        scheme="s3", requires=("boto3",), factory=_s3_factory, tier="cold",
        description="S3 or S3-compatible object storage",
    ))
    registry.register(ConnectorSpec(
        scheme="delta", requires=("deltalake",), factory=_delta_factory, tier="cold",
        description="Delta Lake table for raw capture landing",
    ))
    for cloud in (
        _CloudSpec("gs", "google.cloud.storage", "Google Cloud Storage"),
        _CloudSpec("az", "azure.storage.blob", "Azure Blob Storage"),
    ):
        registry.register(ConnectorSpec(
            scheme=cloud.scheme, requires=(cloud.module,), tier="cold",
            factory=_unavailable_factory(cloud.scheme), description=cloud.description,
        ))
    return registry


def _unavailable_factory(scheme: str) -> Factory:
    """Placeholder for schemes whose adapter ships separately."""

    def factory(uri: str, options: Mapping[str, Any]) -> ObjectStore:
        raise StorageError(
            f"the {scheme!r} connector is declared but its adapter is not bundled; "
            "register a ConnectorSpec with your own factory to enable it"
        )

    return factory


REGISTRY = default_registry()


def resolve(uri: str, **options: Any) -> ObjectStore:
    """Resolve a URI against the process-wide registry."""
    return REGISTRY.resolve(uri, **options)
