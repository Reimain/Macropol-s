"""The UI server — stdlib only, no build step, no CDN.

The kernel's zero-dependency rule includes the interface. That is not asceticism:
an architecture intelligence platform gets pointed at private infrastructure, and
a UI that fetched a script from a CDN would make every page load an outbound
request from inside whatever network it was deployed in. So the assets are local
files, served from disk, and the page works with no network at all.

`ThreadingHTTPServer` because SSE holds a connection open per client. A
single-threaded server would let one open feed block every other request.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..errors import UiError
from .api import Api, Request
from .stream import EventStream

APP_ROOT = Path(__file__).parent / "app"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".webmanifest": "application/manifest+json",
}

#: Refuse a body larger than this. The API takes small JSON objects; anything
#: bigger is a mistake or an attempt to exhaust memory.
MAX_BODY_BYTES = 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    """Routes to assets, the API, or the event stream."""

    server_version = "SLPIE"
    protocol_version = "HTTP/1.1"

    # -- plumbing --------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:
        """Silent by default. The event feed is the log that matters."""
        if getattr(self.server, "verbose", False):  # pragma: no cover - opt-in
            super().log_message(format, *args)

    @property
    def api(self) -> Api:
        return self.server.api  # type: ignore[attr-defined]

    @property
    def stream(self) -> EventStream:
        return self.server.stream  # type: ignore[attr-defined]

    # -- requests --------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's contract
        parsed = urlparse(self.path)
        if parsed.path == "/api/stream":
            self._serve_stream()
        elif parsed.path.startswith("/api/"):
            self._serve_api("GET", parsed)
        else:
            self._serve_asset(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self._send_json({"error": "not found"}, status=404)
            return
        self._serve_api("POST", parsed)

    def _serve_api(self, method: str, parsed: Any) -> None:
        body: dict[str, Any] = {}
        if method == "POST":
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length > MAX_BODY_BYTES:
                self._send_json({"error": "request body too large"}, status=413)
                return
            if length:
                raw = self.rfile.read(length)
                try:
                    parsed_body = json.loads(raw)
                    body = parsed_body if isinstance(parsed_body, dict) else {}
                except ValueError:
                    self._send_json({"error": "request body is not JSON"}, status=400)
                    return

        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        response = self.api.handle(Request(
            method=method, path=parsed.path, query=query, body=body,
        ))
        self._send_bytes(
            response.encode(), "application/json", status=response.status
        )

    def _serve_stream(self) -> None:
        """Hold the connection open and push events as they arrive."""
        client_id = f"{self.client_address[0]}:{self.client_address[1]}"
        client = self.stream.connect(client_id)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        try:
            for frame in self.stream.follow(client):
                self.wfile.write(frame)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # the tab closed; that is the normal way a stream ends
        finally:
            self.stream.disconnect(client_id)

    def _serve_asset(self, path: str) -> None:
        relative = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (APP_ROOT / relative).resolve()

        # Containment, for the same reason the connector checks it: a path
        # assembled from a request must not be able to read the whole disk.
        if APP_ROOT.resolve() not in target.parents and target != APP_ROOT.resolve():
            self._send_json({"error": "not found"}, status=404)
            return
        if not target.is_file():
            self._send_json({"error": f"no such asset: {relative}"}, status=404)
            return

        self._send_bytes(
            target.read_bytes(),
            CONTENT_TYPES.get(target.suffix, "application/octet-stream"),
        )

    # -- responses -------------------------------------------------------

    def _send_json(self, body: Any, *, status: int = 200) -> None:
        self._send_bytes(
            json.dumps(body, default=str).encode("utf-8"), "application/json", status=status
        )

    def _send_bytes(self, body: bytes, content_type: str, *, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # No external origins are ever contacted, so the policy can be absolute.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'",
        )
        self.end_headers()
        self.wfile.write(body)


class UiServer:
    """Serves the interface for an engine."""

    def __init__(
        self,
        engine: Any,
        *,
        host: str = "127.0.0.1",
        port: int = 8420,
        verbose: bool = False,
    ) -> None:
        if not APP_ROOT.is_dir():
            raise UiError(f"the interface assets are missing from {APP_ROOT}")

        self.engine = engine
        self.stream = EventStream()
        # The UI is a subscriber like any other. It needs no special support
        # anywhere else in the platform.
        engine.commands.events.subscribe(self.stream.name, self.stream.handle)
        engine.stream = self.stream

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._server.api = Api(engine)          # type: ignore[attr-defined]
        self._server.stream = self.stream       # type: ignore[attr-defined]
        self._server.verbose = verbose          # type: ignore[attr-defined]
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def host(self) -> str:
        return self._server.server_address[0]

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def start(self) -> "UiServer":
        """Serve in a background thread — for tests and for `slpie ui &`."""
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def serve_forever(self) -> None:  # pragma: no cover - blocking entry point
        self._server.serve_forever()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> "UiServer":
        return self.start()

    def __exit__(self, *_exception: Any) -> None:
        self.stop()

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<UiServer {self.url}>"
