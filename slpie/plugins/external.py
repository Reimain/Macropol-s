"""Running an external plugin — a subprocess, JSON on stdio, and a leash.

The lifecycle is deliberately unforgiving. A plugin that violates the protocol,
exceeds its wall clock, or dies is **quarantined for the run**: withdrawn, its
failure recorded as a capability gap, and never consulted again in that session.
Retrying a misbehaving analyzer on every file would turn one broken plugin into
a scan that never finishes.

Quarantine is a reported gap rather than a crash, which is the same principle as
a refused capability. The platform keeps working and says what it stopped being
able to see.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..domain.finding import Gap, GapKind
from ..errors import PluginError, PluginProtocolError, PluginQuarantined
from .manifest import PluginManifest
from .protocol import DiscoveryResult, Message, Observation, parse_result
from .sandbox import DEFAULT_LIMITS, Limits

#: How many unparseable responses a plugin gets before it is withdrawn.
MAX_PROTOCOL_FAILURES = 3


@dataclass(slots=True)
class ExternalPlugin:
    """A plugin running in its own process."""

    manifest: PluginManifest
    limits: Limits = DEFAULT_LIMITS
    process: subprocess.Popen | None = None
    quarantined: str = ""
    calls: int = 0
    failures: int = 0
    protocol_failures: int = 0
    _next_id: int = 0
    _lock: threading.RLock = field(default_factory=threading.RLock)

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        """Launch the subprocess under its limits."""
        if self.quarantined:
            raise PluginQuarantined(self.manifest.id, self.quarantined)
        if self.process is not None and self.process.poll() is None:
            return

        command = list(self.manifest.entrypoint)
        working_directory = self.manifest.source or None
        try:
            self.process = subprocess.Popen(
                command,
                cwd=working_directory,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=self.limits.environment(),
                preexec_fn=self.limits.preexec(),
            )
        except (OSError, ValueError) as error:
            self.quarantine(f"could not start: {error}")
            raise PluginQuarantined(self.manifest.id, self.quarantined) from None

    def stop(self) -> None:
        if self.process is None:
            return
        try:
            if self.process.stdin and not self.process.stdin.closed:
                self.process.stdin.close()
            self.process.terminate()
            self.process.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            self.process.kill()
        finally:
            self.process = None

    def quarantine(self, reason: str) -> Gap:
        """Withdraw the plugin and turn its failure into a reported gap."""
        self.quarantined = reason
        self.stop()
        return self.gap()

    def gap(self) -> Gap:
        return Gap(
            kind=GapKind.PLUGIN_QUARANTINED if self.quarantined else GapKind.PLUGIN_UNAVAILABLE,
            subject=self.manifest.id,
            detail=(
                f"{self.manifest.id} was withdrawn: {self.quarantined}"
                if self.quarantined
                else f"{self.manifest.id} is not running"
            ),
            remediation=f"fix or remove the {self.manifest.id} plugin",
            confidence_impact=0.3,
        )

    @property
    def alive(self) -> bool:
        return (
            not self.quarantined
            and self.process is not None
            and self.process.poll() is None
        )

    # -- calls -----------------------------------------------------------

    def call(self, method: str, **params: Any) -> Mapping[str, Any]:
        """One request/response round trip, bounded by the wall clock."""
        if self.quarantined:
            raise PluginQuarantined(self.manifest.id, self.quarantined)

        with self._lock:
            self.start()
            assert self.process is not None and self.process.stdin is not None

            self._next_id += 1
            request = Message(method=method, id=self._next_id, params=params)

            try:
                self.process.stdin.write(request.encode() + "\n")
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as error:
                self.failures += 1
                self.quarantine(f"died while being called: {error}")
                raise PluginQuarantined(self.manifest.id, self.quarantined) from None

            line = self._read_line(self.limits.wall_seconds)
            self.calls += 1

            try:
                response = Message.decode(line)
            except PluginProtocolError:
                # A transport-level failure means the plugin is unusable, not
                # that one file confused it. Tolerate a couple in case the
                # process was mid-startup, then withdraw it — otherwise it is
                # called once per file and fails once per file for the whole scan.
                self.failures += 1
                self.protocol_failures += 1
                if self.protocol_failures >= MAX_PROTOCOL_FAILURES:
                    self.quarantine(
                        f"sent {self.protocol_failures} unparseable responses"
                    )
                raise
            if response.error:
                self.failures += 1
                raise PluginProtocolError(f"{self.manifest.id}: {response.error}")
            if response.id != request.id:
                # A mismatched id means the stream is desynchronised, and every
                # later answer would be attributed to the wrong question.
                self.failures += 1
                self.quarantine(
                    f"replied to id {response.id} when asked id {request.id}"
                )
                raise PluginQuarantined(self.manifest.id, self.quarantined)
            return response.result

    def _read_line(self, timeout: float) -> str:
        """Read one response, killing the plugin if it never arrives.

        The read happens on a helper thread because a blocking readline on a
        subprocess pipe cannot otherwise be interrupted — and a plugin that
        stops answering must not hang the scan.
        """
        assert self.process is not None and self.process.stdout is not None
        collected: list[str] = []

        def read() -> None:
            try:
                collected.append(self.process.stdout.readline())  # type: ignore[union-attr]
            except (OSError, ValueError):
                collected.append("")

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(timeout=timeout)

        if reader.is_alive():
            self.failures += 1
            self.quarantine(f"did not answer within {timeout}s")
            raise PluginQuarantined(self.manifest.id, self.quarantined)

        line = collected[0] if collected else ""
        if not line:
            self.failures += 1
            stderr = ""
            if self.process is not None and self.process.stderr is not None:
                try:
                    stderr = self.process.stderr.read()[:400]
                except (OSError, ValueError):  # pragma: no cover - racing shutdown
                    stderr = ""
            self.quarantine(f"closed its output{f': {stderr}' if stderr else ''}")
            raise PluginQuarantined(self.manifest.id, self.quarantined)
        if len(line) > self.limits.max_output_bytes:
            self.quarantine(f"sent a {len(line)} byte line, over the output limit")
            raise PluginQuarantined(self.manifest.id, self.quarantined)
        return line

    # -- the two methods -------------------------------------------------

    def describe(self) -> Mapping[str, Any]:
        """Ask the plugin what it is. Verified against its manifest."""
        result = self.call("describe")
        declared = result.get("id")
        if declared and declared != self.manifest.id:
            self.quarantine(
                f"describes itself as {declared!r} but its manifest says {self.manifest.id!r}"
            )
            raise PluginQuarantined(self.manifest.id, self.quarantined)
        return result

    def discover(self, uri: str, *, digest: str = "", content: str = "") -> DiscoveryResult:
        """Ask the plugin to analyse one source, and validate what comes back."""
        params: dict[str, Any] = {"uri": uri}
        if digest:
            params["digest"] = digest
        if content:
            params["content"] = content

        result = self.call("discover", **params)
        return parse_result(
            result,
            plugin_id=self.manifest.id,
            plugin_version=self.manifest.version,
            allowed=self.manifest.evidence_kinds,
        )

    def status(self) -> dict[str, Any]:
        return {
            "id": self.manifest.id,
            "version": self.manifest.version,
            "runtime": self.manifest.runtime.value,
            "alive": self.alive,
            "quarantined": bool(self.quarantined),
            "reason": self.quarantined,
            "calls": self.calls,
            "failures": self.failures,
            "protocol_failures": self.protocol_failures,
            "limits": self.limits.to_dict(),
        }

    def __enter__(self) -> "ExternalPlugin":
        self.start()
        return self

    def __exit__(self, *_exception: Any) -> None:
        self.stop()

    def __repr__(self) -> str:  # pragma: no cover - display only
        state = "quarantined" if self.quarantined else ("alive" if self.alive else "stopped")
        return f"<ExternalPlugin {self.manifest.id} {state}>"
