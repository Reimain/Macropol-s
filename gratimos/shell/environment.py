"""What is actually on this machine — read, never assumed.

Task prediction without an environment probe produces confident nonsense: a plan
that says `apt-get install` on Alpine, `systemctl restart` inside a container
with no init, or `docker build` where docker is not installed. Every one of
those looks reasonable in a chat window and fails on the box.

So the layer above never guesses. It asks this module what exists, and a task
that needs a binary the machine does not have is not proposed — or is proposed
with the install step in front of it, stated.

Everything here is read locally with the standard library: `platform`,
`shutil.which`, `os.environ`, and a few well-known files. No network, no
subprocess execution of the thing being detected. Probing by *running* a
candidate command to see whether it works is how a probe becomes the incident.
"""

from __future__ import annotations

import os
import platform as _platform
import shutil
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


class Platform(str, Enum):
    LINUX = "linux"
    MACOS = "macos"
    WINDOWS = "windows"
    UNKNOWN = "unknown"

    @property
    def posix(self) -> bool:
        return self in (Platform.LINUX, Platform.MACOS)


class Shell(str, Enum):
    BASH = "bash"
    ZSH = "zsh"
    FISH = "fish"
    SH = "sh"
    POWERSHELL = "powershell"
    CMD = "cmd"
    UNKNOWN = "unknown"

    @property
    def posix(self) -> bool:
        return self in (Shell.BASH, Shell.ZSH, Shell.FISH, Shell.SH)


#: Binaries worth knowing about before proposing anything. Kept explicit rather
#: than scanning PATH wholesale: a full scan is slow, varies between runs, and
#: produces a list nobody reads.
PROBED = (
    "git", "docker", "podman", "kubectl", "helm", "terraform", "ansible",
    "python3", "pip", "uv", "node", "npm", "pnpm", "yarn", "go", "cargo",
    "java", "mvn", "gradle", "dotnet", "ruby", "php", "psql", "mysql",
    "redis-cli", "aws", "gcloud", "az", "systemctl", "curl", "wget", "jq",
    "make", "gcc", "rsync", "ssh", "tar", "unzip",
)

#: Package managers, most specific first — a machine with both `apt` and `snap`
#: should be told about `apt`.
PACKAGE_MANAGERS = (
    "apt-get", "apt", "dnf", "yum", "zypper", "pacman", "apk",
    "brew", "port", "choco", "winget", "snap", "flatpak",
)

#: Environment variables that mean "this is CI", by vendor. Presence is enough;
#: the value varies and nobody agrees on it.
CI_MARKERS = (
    "CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_URL", "BUILDKITE",
    "CIRCLECI", "TF_BUILD", "TEAMCITY_VERSION", "DRONE",
)


@dataclass(frozen=True, slots=True)
class Environment:
    """A machine, as read. Every field is evidence, not configuration."""

    platform: Platform = Platform.UNKNOWN
    shell: Shell = Shell.UNKNOWN
    distribution: str = ""
    package_managers: tuple[str, ...] = ()
    binaries: frozenset[str] = frozenset()
    python: str = ""
    container: bool = False
    ci: str = ""
    privileged: bool = False
    home: str = ""
    cwd: str = ""
    fingerprint_at: float = 0.0

    def has(self, binary: str) -> bool:
        return binary in self.binaries

    def missing(self, required: Iterable[str]) -> tuple[str, ...]:
        """Which of these are absent. The reason a task is not proposed."""
        return tuple(sorted(name for name in required if name not in self.binaries))

    @property
    def package_manager(self) -> str:
        """The one to use. Empty when the machine has none, which is real."""
        return self.package_managers[0] if self.package_managers else ""

    @property
    def interactive(self) -> bool:
        """Whether a human can be asked to confirm.

        Decides whether a `CONFIRM` verdict is a prompt or a refusal: in CI
        there is nobody to ask, and treating "unattended" as "approved" is how
        a destructive command runs at 2am.
        """
        return not self.ci and sys.stdin.isatty() if hasattr(sys.stdin, "isatty") else False

    @property
    def fingerprint(self) -> str:
        """What the routine layer compares against to detect drift.

        Deliberately coarse — platform, distribution, package manager, shell and
        the sorted binary set. A routine recorded against one fingerprint and
        replayed against another is a script written in a language the machine
        has since forgotten, and it is demoted rather than run.
        """
        import hashlib

        material = "\x1f".join([
            self.platform.value, self.distribution, self.shell.value,
            self.package_manager, ",".join(sorted(self.binaries)),
            "container" if self.container else "host",
        ])
        return hashlib.blake2b(material.encode("utf-8"), digest_size=12).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform.value,
            "shell": self.shell.value,
            "distribution": self.distribution,
            "package_managers": list(self.package_managers),
            "binaries": sorted(self.binaries),
            "python": self.python,
            "container": self.container,
            "ci": self.ci,
            "privileged": self.privileged,
            "fingerprint": self.fingerprint,
        }

    def summary(self) -> str:
        """One line, for the top of a plan."""
        where = self.distribution or self.platform.value
        marks = [where, self.shell.value]
        if self.package_manager:
            marks.append(self.package_manager)
        if self.container:
            marks.append("container")
        if self.ci:
            marks.append(f"ci:{self.ci}")
        if self.privileged:
            marks.append("root")
        return " · ".join(marks) + f" · {len(self.binaries)} tools"

    def __str__(self) -> str:
        return self.summary()


def _platform_of(system: str) -> Platform:
    return {
        "linux": Platform.LINUX, "darwin": Platform.MACOS, "windows": Platform.WINDOWS,
    }.get(system.lower(), Platform.UNKNOWN)


def _shell_of(environ: Mapping[str, str], platform: Platform) -> Shell:
    raw = environ.get("SHELL") or environ.get("ComSpec") or ""
    name = Path(raw).name.lower().removesuffix(".exe")
    for shell in Shell:
        if shell.value == name:
            return shell
    if name == "pwsh":
        return Shell.POWERSHELL
    return Shell.CMD if platform is Platform.WINDOWS else Shell.UNKNOWN


def _distribution(root: Path) -> str:
    """`/etc/os-release` is the only portable answer on Linux."""
    release = root / "etc" / "os-release"
    try:
        text = release.read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        if line.startswith("ID="):
            return line.partition("=")[2].strip().strip('"')
    return ""


def _in_container(root: Path, environ: Mapping[str, str]) -> bool:
    """Three independent signals, because none of them is reliable alone."""
    if environ.get("KUBERNETES_SERVICE_HOST"):
        return True
    if (root / ".dockerenv").exists():
        return True
    try:
        cgroup = (root / "proc" / "1" / "cgroup").read_text(encoding="utf-8")
    except OSError:
        return False
    return any(marker in cgroup for marker in ("docker", "kubepods", "containerd", "lxc"))


def _ci(environ: Mapping[str, str]) -> str:
    for marker in CI_MARKERS:
        if environ.get(marker):
            return marker.lower()
    return ""


def probe(
    *,
    environ: Mapping[str, str] | None = None,
    root: Path | str = "/",
    which=None,
    system: str = "",
) -> Environment:
    """Read the machine. Every input is injectable, so this is testable.

    `which`, `root` and `environ` are parameters rather than globals because a
    probe that can only be tested on the machine it runs on is a probe that gets
    tested once, on a laptop, and is wrong on every server afterwards.
    """
    import time

    source = os.environ if environ is None else environ
    locate = shutil.which if which is None else which
    base = Path(root)
    platform_name = system or _platform.system()
    detected = _platform_of(platform_name)

    binaries = frozenset(
        name for name in (*PROBED, *PACKAGE_MANAGERS) if locate(name)
    )
    managers = tuple(name for name in PACKAGE_MANAGERS if name in binaries)

    return Environment(
        platform=detected,
        shell=_shell_of(source, detected),
        distribution=_distribution(base) if detected is Platform.LINUX else "",
        package_managers=managers,
        binaries=binaries,
        python=source.get("PYTHON_VERSION") or _platform.python_version(),
        container=_in_container(base, source),
        ci=_ci(source),
        privileged=hasattr(os, "geteuid") and os.geteuid() == 0,
        home=source.get("HOME") or source.get("USERPROFILE") or "",
        cwd=source.get("PWD") or "",
        fingerprint_at=time.time(),
    )
