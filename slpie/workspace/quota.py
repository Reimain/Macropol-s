"""What a workspace may consume, and what the tenant has left.

The platform decides the allocation; the user does not request it. That is not
paternalism — it is the only way a tenant's total stays bounded when a hundred
users each open a notebook and leave it running over a weekend.

Three types, and the separation matters:

===========  ================================================================
`Quota`      a ceiling. What a tenant bought.
`Allocation` what one workspace is given. Always within the tenant's quota.
`Usage`      what is actually consumed right now. Measured, never assumed.
===========  ================================================================

`Usage` is separate from `Allocation` because they diverge, and the gap between
them is the entire cost conversation: a tenant paying for forty allocations that
are using six has a problem worth telling them about, and one cannot see that
from the allocation alone.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from ..errors import SlpieError


class QuotaError(SlpieError):
    """An allocation would exceed what the tenant has, or is malformed."""


#: Below this a JupyterLab kernel will not start reliably. Refused at the model
#: rather than discovered as a pod that crash-loops with no explanation.
MIN_MEMORY_MB = 512
MIN_CPU = 0.1


@dataclass(frozen=True, slots=True)
class Allocation:
    """What one workspace is given."""

    cpu: float = 1.0
    memory_mb: int = 2048
    disk_gb: int = 10
    gpu: int = 0
    #: Idle time before the workspace is reclaimed. The single most effective
    #: cost control there is, because the failure mode is not a busy workspace —
    #: it is four hundred idle ones.
    idle_timeout_minutes: int = 60

    def __post_init__(self) -> None:
        if self.cpu < MIN_CPU:
            raise QuotaError(
                f"{self.cpu} CPU is below the {MIN_CPU} floor; a kernel that "
                f"cannot start is a support ticket, not a saving"
            )
        if self.memory_mb < MIN_MEMORY_MB:
            raise QuotaError(
                f"{self.memory_mb}MB is below the {MIN_MEMORY_MB}MB floor; "
                f"JupyterLab will be killed before the first cell runs"
            )
        if self.disk_gb < 1:
            raise QuotaError("a workspace needs at least 1GB of disk")
        if self.idle_timeout_minutes < 1:
            raise QuotaError(
                "an idle timeout of zero would reclaim a workspace the moment "
                "it started; use a large number to mean 'never'"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu": self.cpu, "memory_mb": self.memory_mb,
            "disk_gb": self.disk_gb, "gpu": self.gpu,
            "idle_timeout_minutes": self.idle_timeout_minutes,
        }

    def __str__(self) -> str:
        gpu = f", {self.gpu} GPU" if self.gpu else ""
        return f"{self.cpu} CPU, {self.memory_mb}MB, {self.disk_gb}GB{gpu}"


@dataclass(frozen=True, slots=True)
class Usage:
    """What is actually consumed. Measured, never inferred from the allocation."""

    workspaces: int = 0
    cpu: float = 0.0
    memory_mb: int = 0
    disk_gb: int = 0
    gpu: int = 0

    def plus(self, allocation: Allocation) -> "Usage":
        return replace(
            self,
            workspaces=self.workspaces + 1,
            cpu=round(self.cpu + allocation.cpu, 3),
            memory_mb=self.memory_mb + allocation.memory_mb,
            disk_gb=self.disk_gb + allocation.disk_gb,
            gpu=self.gpu + allocation.gpu,
        )

    def minus(self, allocation: Allocation) -> "Usage":
        return replace(
            self,
            workspaces=max(0, self.workspaces - 1),
            cpu=round(max(0.0, self.cpu - allocation.cpu), 3),
            memory_mb=max(0, self.memory_mb - allocation.memory_mb),
            disk_gb=max(0, self.disk_gb - allocation.disk_gb),
            gpu=max(0, self.gpu - allocation.gpu),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspaces": self.workspaces, "cpu": self.cpu,
            "memory_mb": self.memory_mb, "disk_gb": self.disk_gb,
            "gpu": self.gpu,
        }


@dataclass(frozen=True, slots=True)
class Quota:
    """A tenant's ceiling."""

    max_workspaces: int = 10
    max_cpu: float = 16.0
    max_memory_mb: int = 65_536
    max_disk_gb: int = 500
    max_gpu: int = 0

    def admits(self, usage: Usage, allocation: Allocation) -> tuple[bool, str]:
        """Whether one more allocation fits, and if not, which limit stopped it.

        Returns the reason rather than a bare False. "Quota exceeded" sends an
        operator to a dashboard; "at 10 of 10 workspaces" tells them what to
        change, which is the difference between a message and an answer.
        """
        after = usage.plus(allocation)
        checks = (
            (after.workspaces, self.max_workspaces, "workspaces", ""),
            (after.cpu, self.max_cpu, "CPU", ""),
            (after.memory_mb, self.max_memory_mb, "memory", "MB"),
            (after.disk_gb, self.max_disk_gb, "disk", "GB"),
            (after.gpu, self.max_gpu, "GPU", ""),
        )
        for wanted, ceiling, name, unit in checks:
            if wanted > ceiling:
                held = getattr(usage, {
                    "workspaces": "workspaces", "CPU": "cpu", "memory": "memory_mb",
                    "disk": "disk_gb", "GPU": "gpu",
                }[name])
                return False, (
                    f"the tenant is at {held}{unit} of {ceiling}{unit} {name}; "
                    f"this workspace would need {wanted}{unit}"
                )
        return True, ""

    def headroom(self, usage: Usage) -> dict[str, float]:
        """What is left. What an administrator's dashboard renders."""
        return {
            "workspaces": self.max_workspaces - usage.workspaces,
            "cpu": round(self.max_cpu - usage.cpu, 3),
            "memory_mb": self.max_memory_mb - usage.memory_mb,
            "disk_gb": self.max_disk_gb - usage.disk_gb,
            "gpu": self.max_gpu - usage.gpu,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_workspaces": self.max_workspaces, "max_cpu": self.max_cpu,
            "max_memory_mb": self.max_memory_mb, "max_disk_gb": self.max_disk_gb,
            "max_gpu": self.max_gpu,
        }
