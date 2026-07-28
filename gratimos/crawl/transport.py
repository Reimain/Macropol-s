"""The one place bytes come off a socket — and the seam that lets them not.

Every fetch in the crawler goes through a `Transport`. There are two
implementations and they are interchangeable: `UrllibTransport` talks to the
network, `RecordedTransport` replays captured payloads. Nothing above this
module knows which it has, which is what makes the whole crawler testable
without a network and runnable inside an air-gapped environment.

This is the same trick SLPIE plays with simulated and live bindings, for the
same reason: a test that exercises a mock of the fetcher proves the mock works.
A test that exercises the real fetcher over a recorded transport proves the
rate limiter, the robots gate, the retry logic and the cache revalidation all
work, because every one of them actually ran.
"""

from __future__ import annotations

import gzip
import json
import zlib
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable
from urllib.parse import urlsplit

from .policy import FetchError


@dataclass(frozen=True, slots=True)
class Response:
    """What came back, plus how we got it."""

    url: str
    status: int
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)
    from_cache: bool = False
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def not_modified(self) -> bool:
        return self.status == 304

    def header(self, name: str, default: str = "") -> str:
        """Case-insensitive lookup — HTTP header casing is not dependable."""
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return default

    @property
    def etag(self) -> str:
        return self.header("etag")

    @property
    def last_modified(self) -> str:
        return self.header("last-modified")

    @property
    def retry_after(self) -> float | None:
        raw = self.header("retry-after")
        if not raw:
            return None
        try:
            return float(raw.strip())
        except ValueError:
            # The date form of Retry-After. Rather than parse it and risk a
            # clock-skew argument, fall back to the policy's own backoff.
            return None

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding, errors="replace")

    def json(self) -> Any:
        """Parse as JSON, naming the URL when it is not.

        A registry returning an HTML error page with a 200 is common enough
        that a bare `JSONDecodeError` several frames up is a genuinely
        confusing way to find out.
        """
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FetchError(f"{self.url} did not return JSON: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url, "status": self.status,
            "bytes": len(self.body), "from_cache": self.from_cache,
            "elapsed": round(self.elapsed, 4),
        }


@runtime_checkable
class Transport(Protocol):
    """Performs one HTTP GET. No retries, no rate limiting, no caching.

    Kept this dumb deliberately: every policy decision belongs in the fetcher,
    where it is shared by every transport, rather than being reimplemented
    once per transport and drifting.
    """

    def get(self, url: str, headers: Mapping[str, str], timeout: float) -> Response:
        ...


def _decode(body: bytes, encoding: str) -> bytes:
    """Undo Content-Encoding. Registries gzip by default and it matters."""
    token = encoding.strip().lower()
    if token in ("gzip", "x-gzip"):
        return gzip.decompress(body)
    if token == "deflate":
        try:
            return zlib.decompress(body)
        except zlib.error:
            return zlib.decompress(body, -zlib.MAX_WBITS)
    return body


class UrllibTransport:
    """The live transport, on stdlib `urllib` — no third-party HTTP client.

    Reads at most `max_bytes` and then stops. A registry serving a multi-gigabyte
    response, by accident or otherwise, must not be able to exhaust memory in a
    process whose job is to read a few kilobytes of package metadata.
    """

    def __init__(self, *, max_bytes: int = 8 * 1024 * 1024) -> None:
        self.max_bytes = max_bytes

    def get(self, url: str, headers: Mapping[str, str], timeout: float) -> Response:
        import urllib.error
        import urllib.request

        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise FetchError(f"refusing to fetch {url!r}: only http and https are supported")

        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as raw:
                payload = raw.read(self.max_bytes + 1)
                response_headers = {k: v for k, v in raw.headers.items()}
                status = raw.status
        except urllib.error.HTTPError as exc:
            # A 304 or a 429 is information, not a failure — hand it back so the
            # fetcher can revalidate or back off. Only the transport-level
            # failures below are genuinely unrecoverable here.
            body = exc.read(self.max_bytes + 1) if exc.fp is not None else b""
            return Response(
                url=url, status=exc.code, body=body,
                headers={k: v for k, v in (exc.headers or {}).items()},
            )
        except urllib.error.URLError as exc:
            raise FetchError(f"could not reach {url}: {exc.reason}") from exc
        except OSError as exc:
            raise FetchError(f"could not reach {url}: {exc}") from exc

        if len(payload) > self.max_bytes:
            raise FetchError(
                f"{url} returned more than the {self.max_bytes} byte ceiling; refusing it"
            )

        encoding = ""
        for key, value in response_headers.items():
            if key.lower() == "content-encoding":
                encoding = value
                break
        if encoding:
            try:
                payload = _decode(payload, encoding)
            except (OSError, zlib.error) as exc:
                raise FetchError(f"{url} sent undecodable {encoding} content: {exc}") from exc

        return Response(url=url, status=status, body=payload, headers=response_headers)


class RecordedTransport:
    """Replays captured responses. The offline half of the seam.

    Unknown URLs return 404 rather than raising, because that is what an
    absent resource does on the network too — a crawler that crashes on a
    missing page has a bug that only shows up in production otherwise.
    """

    def __init__(self, responses: Mapping[str, Response | bytes | Any] | None = None) -> None:
        self._responses: dict[str, Response] = {}
        self.requests: list[tuple[str, dict[str, str]]] = []
        for url, value in (responses or {}).items():
            self.record(url, value)

    def record(self, url: str, value: Response | bytes | str | Any, *, status: int = 200,
               headers: Mapping[str, str] | None = None) -> None:
        """Register a payload. JSON-serialisable values are encoded for you."""
        if isinstance(value, Response):
            self._responses[url] = value
            return
        if isinstance(value, bytes):
            body = value
        elif isinstance(value, str):
            body = value.encode("utf-8")
        else:
            body = json.dumps(value).encode("utf-8")
        self._responses[url] = Response(
            url=url, status=status, body=body, headers=dict(headers or {}),
        )

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def urls(self) -> tuple[str, ...]:
        return tuple(url for url, _ in self.requests)

    def get(self, url: str, headers: Mapping[str, str], timeout: float) -> Response:
        self.requests.append((url, dict(headers)))
        recorded = self._responses.get(url)
        if recorded is None:
            return Response(url=url, status=404)

        # Honour conditional requests, so cache revalidation is exercised by the
        # offline transport exactly as it would be against a real registry.
        if recorded.etag and headers.get("If-None-Match") == recorded.etag:
            return Response(url=url, status=304, headers=recorded.headers)
        if recorded.last_modified and headers.get("If-Modified-Since") == recorded.last_modified:
            return Response(url=url, status=304, headers=recorded.headers)
        return recorded
