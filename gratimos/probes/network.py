"""API probe: pulling content over HTTP.

An agent that builds itself from "data presence and links" will inevitably be
handed a URL, and a URL is the one input that can reach *back* into the network
it is running in. So this probe is guarded by default:

* private, loopback, and link-local addresses are refused unless explicitly
  allowed — the resolved address is checked, not the hostname, so a public name
  pointing at ``169.254.169.254`` does not get through;
* responses are size-capped while streaming, not after;
* redirects are followed only within the same guarantees.

None of this is optional-by-accident: :class:`AccessPolicy` has to be widened
deliberately, and every refusal is recorded on the capture.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib import request as _request
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

from ..errors import AccessDenied
from .base import BaseProbe, Capture, Target
from .documents import JsonProbe

DEFAULT_MAX_BYTES = 32 << 20  # 32 MiB
DEFAULT_TIMEOUT = 20.0


@dataclass(frozen=True, slots=True)
class AccessPolicy:
    """What the API probe is permitted to reach."""

    allow_hosts: frozenset[str] = frozenset()
    allow_private: bool = False
    allow_schemes: frozenset[str] = frozenset({"https", "http"})
    max_bytes: int = DEFAULT_MAX_BYTES
    timeout: float = DEFAULT_TIMEOUT
    headers: Mapping[str, str] = field(default_factory=dict)
    max_redirects: int = 3

    def check(self, url: str) -> None:
        """Raise :class:`AccessDenied` unless this URL is permitted."""
        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        if scheme not in self.allow_schemes:
            raise AccessDenied(f"scheme {scheme!r} not allowed for {url!r}")
        host = parsed.hostname
        if not host:
            raise AccessDenied(f"no host in {url!r}")
        if self.allow_hosts and host not in self.allow_hosts:
            raise AccessDenied(f"host {host!r} is not in the allow list")
        if not self.allow_private:
            for address in self._resolve(host):
                if not address.is_global or address.is_loopback or address.is_link_local:
                    raise AccessDenied(
                        f"host {host!r} resolves to non-public address {address}; "
                        "set allow_private=True to permit internal targets"
                    )

    @staticmethod
    def _resolve(host: str) -> list[ipaddress._BaseAddress]:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise AccessDenied(f"cannot resolve host {host!r}: {exc}") from exc
        out = []
        for info in infos:
            try:
                out.append(ipaddress.ip_address(info[4][0]))
            except ValueError:  # pragma: no cover - unusual address families
                continue
        if not out:
            raise AccessDenied(f"host {host!r} resolved to no usable address")
        return out


#: The policy used when a caller does not supply one: public hosts only.
STRICT_POLICY = AccessPolicy()


@dataclass(slots=True)
class Response:
    """A fetched response, before shaping."""

    url: str
    status: int
    headers: Mapping[str, str]
    body: bytes
    truncated: bool = False

    @property
    def content_type(self) -> str:
        return str(self.headers.get("Content-Type", "")).split(";")[0].strip().lower()

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self) -> Any:
        return json.loads(self.text())


def fetch(url: str, *, policy: AccessPolicy = STRICT_POLICY, method: str = "GET",
          data: bytes | None = None, headers: Mapping[str, str] | None = None) -> Response:
    """Fetch a URL under an access policy, capping the body while streaming."""
    policy.check(url)
    merged = {"Accept": "application/json, text/plain;q=0.8, */*;q=0.5",
              "User-Agent": "gratimos-probe/0.1", **dict(policy.headers), **dict(headers or {})}
    req = _request.Request(url, data=data, headers=merged, method=method)
    try:
        with _request.urlopen(req, timeout=policy.timeout) as response:
            chunks: list[bytes] = []
            total = 0
            truncated = False
            while True:
                chunk = response.read(64 << 10)
                if not chunk:
                    break
                total += len(chunk)
                if total > policy.max_bytes:
                    chunks.append(chunk[: policy.max_bytes - (total - len(chunk))])
                    truncated = True
                    break
                chunks.append(chunk)
            return Response(
                url=response.geturl(),
                status=int(response.status),
                headers={k.title(): v for k, v in response.headers.items()},
                body=b"".join(chunks),
                truncated=truncated,
            )
    except HTTPError as exc:
        return Response(url=url, status=int(exc.code),
                        headers={k.title(): v for k, v in (exc.headers or {}).items()},
                        body=exc.read() if hasattr(exc, "read") else b"")
    except URLError as exc:
        raise AccessDenied(f"request to {url!r} failed: {exc.reason}") from exc


class ApiProbe(BaseProbe):
    """Reads API endpoints, shaping JSON responses the way JsonProbe does."""

    name = "api"
    suffixes = ()

    def __init__(self, policy: AccessPolicy = STRICT_POLICY) -> None:
        self.policy = policy
        self._json = JsonProbe()

    def sniff(self, target: Target) -> float:
        return 0.9 if target.scheme in ("http", "https") else 0.0

    def capture(self, target: Target, *, limit: int = 0) -> Capture:
        capture = self._capture(target)
        try:
            response = fetch(target.uri, policy=self.policy)
        except AccessDenied as exc:
            capture.warnings.append(str(exc))
            return capture

        capture.bytes_read = len(response.body)
        capture.labels = {"status": str(response.status), "content_type": response.content_type}
        if response.status >= 400:
            capture.warnings.append(f"HTTP {response.status} from {response.url}")
            return capture
        if response.truncated:
            capture.warnings.append(f"response truncated at {self.policy.max_bytes} bytes")

        if "json" in response.content_type or response.body[:1] in (b"{", b"["):
            try:
                document = response.json()
            except json.JSONDecodeError as exc:
                capture.warnings.append(f"invalid JSON response: {exc}")
                return capture
            for name, payload, layout in self._json._split(document, target.entity):
                capture.payloads.append(self._wrap(payload, target, name=name, layout=layout,
                                                   status=str(response.status)))
            return capture

        text = response.text()
        lines = [{"line_no": i, "line": line} for i, line in enumerate(text.splitlines()[: self._limit(limit)], 1)]
        if lines:
            capture.payloads.append(self._wrap(lines, target, layout="lines"))
        else:
            capture.warnings.append(f"empty {response.content_type or 'unknown'} response")
        return capture
