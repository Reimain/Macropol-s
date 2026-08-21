"""Git — what the repository knows about itself.

Not dependencies: history. When a file last changed, who owns it, which remote
it came from. That feeds the questions dependency data cannot answer — is this
component still maintained, which team should be told, has this file been
touched since the CVE was published.

Read by shelling out to `git`, because a hand-rolled packfile reader would be a
large amount of subtle code to reimplement something already installed
everywhere. When git is absent, this reports a capability gap and the platform
carries on with one fewer thing to observe.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ...domain.evidence import EvidenceKind
from ...domain.identity import Urn
from ...plugins.protocol import DiscoveryResult, Observation
from ..base import declares, evidence_at, result

EXTRACTOR = "slpie.git"

#: Never wait longer than this on a git invocation. A repository with a
#: pathological history must not stall a scan.
TIMEOUT = 30


@dataclass(frozen=True, slots=True)
class Commit:
    """One commit, as the history walk sees it."""

    sha: str
    author: str
    email: str
    when: int
    subject: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sha": self.sha, "author": self.author, "email": self.email,
            "when": self.when, "subject": self.subject,
        }


def available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True, timeout=10)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True, text=True, timeout=TIMEOUT,
    )
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode, arguments, completed.stdout, completed.stderr
        )
    return completed.stdout


def is_repository(path: str | Path) -> bool:
    return (Path(path) / ".git").exists()


def discover_repository(
    path: str | Path, *, element: str = "", history: int = 50
) -> DiscoveryResult:
    """Read a repository's identity and recent history."""
    repository = Path(path)
    if not available():
        return result((), errors=["git is not installed; repository history is unavailable"])
    if not is_repository(repository):
        return result((), errors=[f"{repository} is not a git repository"])

    uri = f"{repository.as_uri()}/.git"
    name = element or repository.name
    subject = Urn.create("repository", name).to_string()
    observations: list[Observation] = []
    errors: list[str] = []

    properties: dict[str, Any] = {"node_kind": "repository", "scm": "git"}

    try:
        properties["head"] = _git(repository, "rev-parse", "HEAD").strip()
    except (subprocess.SubprocessError, OSError) as error:
        # A repository with no commits is a real state, not a failure.
        errors.append(f"{name}: no HEAD ({_reason(error)})")

    try:
        properties["branch"] = _git(
            repository, "rev-parse", "--abbrev-ref", "HEAD"
        ).strip()
    except (subprocess.SubprocessError, OSError):
        pass

    try:
        remotes = _remotes(repository)
        if remotes:
            properties["remotes"] = remotes
            properties["origin"] = remotes.get("origin", next(iter(remotes.values())))
    except (subprocess.SubprocessError, OSError):
        pass

    commits: list[Commit] = []
    try:
        commits = _log(repository, limit=history)
    except (subprocess.SubprocessError, OSError) as error:
        errors.append(f"{name}: could not read history ({_reason(error)})")

    if commits:
        properties["last_commit"] = commits[0].sha
        properties["last_commit_at"] = commits[0].when
        properties["commit_count"] = len(commits)
        properties["contributors"] = sorted({commit.email for commit in commits})

    observations.append(declares(
        subject,
        evidence_at(
            uri, kind=EvidenceKind.MANIFEST_DECLARED, extractor=EXTRACTOR,
            excerpt=(
                f"git repository at {properties.get('branch', 'HEAD')}"
                + (f", {len(commits)} recent commits" if commits else "")
            ),
        ),
        **properties,
    ))

    return result(observations, errors=errors)


def _remotes(repository: Path) -> dict[str, str]:
    remotes: dict[str, str] = {}
    for line in _git(repository, "remote", "-v").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            remotes.setdefault(parts[0], parts[1])
    return remotes


def _log(repository: Path, *, limit: int) -> list[Commit]:
    #: A separator that cannot appear in a commit subject.
    fields = "%H%x1f%an%x1f%ae%x1f%at%x1f%s"
    output = _git(repository, "log", f"-{limit}", f"--pretty=format:{fields}")

    commits: list[Commit] = []
    for line in output.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 5:
            continue
        try:
            when = int(parts[3])
        except ValueError:
            when = 0
        commits.append(Commit(
            sha=parts[0], author=parts[1], email=parts[2], when=when, subject=parts[4],
        ))
    return commits


def last_changed(repository: str | Path, paths: Sequence[str]) -> dict[str, int]:
    """When each path last changed, as a unix timestamp.

    One `git log` per path would be a process launch per file. This asks once
    for the whole set and splits the answer, which is the difference between a
    scan that finishes and one that does not.
    """
    root = Path(repository)
    if not paths or not available() or not is_repository(root):
        return {}

    changed: dict[str, int] = {}
    for path in paths:
        try:
            output = _git(root, "log", "-1", "--pretty=format:%at", "--", path).strip()
        except (subprocess.SubprocessError, OSError):
            continue
        if output:
            try:
                changed[path] = int(output)
            except ValueError:
                continue
    return changed


def _reason(error: Exception) -> str:
    if isinstance(error, subprocess.CalledProcessError):
        return (error.stderr or "").strip().splitlines()[0] if error.stderr else "failed"
    return str(error)
