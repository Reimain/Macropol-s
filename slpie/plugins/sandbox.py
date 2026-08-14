"""Resource limits for external plugins.

A third-party analyzer is code the platform did not write, running against code
the platform does not control. It gets a CPU limit, an address-space limit, a
file-size limit and a wall clock, applied in the child process between `fork`
and `exec` so the parent's own limits are untouched.

These are containment, not security. A plugin runs with the invoking user's
privileges and can read what that user can read — the limits stop a runaway
regex or a memory leak from taking the platform down with it, and nothing more.
Anything stronger belongs to the operating system, and claiming otherwise here
would be worse than claiming nothing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

try:  # pragma: no cover - platform dependent
    import resource
except ImportError:  # pragma: no cover - Windows
    resource = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class Limits:
    """What an external plugin is allowed to consume."""

    cpu_seconds: int = 30
    address_space_mb: int = 1024
    file_size_mb: int = 64
    wall_seconds: float = 60.0
    max_output_bytes: int = 32 * 1024 * 1024

    @property
    def available(self) -> bool:
        """Whether this platform can enforce them at all."""
        return resource is not None

    def preexec(self) -> Callable[[], None] | None:
        """A callable to run in the child, after fork and before exec.

        Returns None where `resource` is unavailable, so the caller can report a
        degraded-isolation gap rather than pretending the limits were applied.
        """
        if resource is None:  # pragma: no cover - platform dependent
            return None

        cpu = self.cpu_seconds
        address_space = self.address_space_mb * 1024 * 1024
        file_size = self.file_size_mb * 1024 * 1024

        def apply() -> None:  # pragma: no cover - runs in the child
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
            resource.setrlimit(resource.RLIMIT_FSIZE, (file_size, file_size))
            try:
                resource.setrlimit(resource.RLIMIT_AS, (address_space, address_space))
            except (ValueError, OSError):
                # Some platforms refuse RLIMIT_AS. The CPU and file-size limits
                # still applied, so this is a weaker sandbox rather than none.
                pass
            os.setsid()   # detach, so a timeout can kill the whole process group

        return apply

    def environment(self, base: dict[str, str] | None = None) -> dict[str, str]:
        """A minimal environment for the child.

        Deliberately narrow. A plugin inheriting the parent's full environment
        would pick up credentials that have nothing to do with parsing a
        lockfile, and the platform is routinely run in environments where those
        are present.
        """
        source = base if base is not None else os.environ
        keep = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")
        child = {key: source[key] for key in keep if key in source}
        child["SLPIE_PLUGIN"] = "1"
        child["PYTHONUNBUFFERED"] = "1"
        return child

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu_seconds": self.cpu_seconds,
            "address_space_mb": self.address_space_mb,
            "file_size_mb": self.file_size_mb,
            "wall_seconds": self.wall_seconds,
            "enforced": self.available,
        }


#: Applied to every external plugin unless one is registered with its own.
DEFAULT_LIMITS = Limits()
