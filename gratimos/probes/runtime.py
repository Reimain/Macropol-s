"""Shell probe: scripts as data first, as executables only on request.

A folder scan that runs every ``.sh`` it finds is not a feature, it is a remote
code execution primitive pointed at whatever the agent was aimed at. So the
default behaviour here is to *read* scripts: capture the text, the interpreter,
the declared variables, and the commands invoked. That is genuinely useful
context, and it is inert.

Execution is available, but only when the caller passes ``allow_exec=True`` and
the script matches an explicit allow list. Executed scripts run under the same
resource limits as sandboxed transformations, with output captured and capped.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..errors import TransformError
from .base import BaseProbe, Capture, Target

_SHEBANG_RE = re.compile(r"^#!\s*(\S+)(?:\s+(\S+))?")
_ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_FUNC_RE = re.compile(r"^\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_-]*)\s*\(\)\s*\{")

#: Commands whose presence in a script is worth surfacing to a policymaker.
_NOTABLE = frozenset({
    "curl", "wget", "rm", "sudo", "ssh", "scp", "docker", "kubectl", "aws", "az",
    "gcloud", "psql", "mysql", "python", "python3", "pip", "npm", "git", "chmod", "dd",
})


@dataclass(slots=True)
class ScriptFacts:
    """What a script declares about itself, read without running it."""

    interpreter: str = ""
    lines: int = 0
    functions: list[str] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    notable: list[str] = field(default_factory=list)
    reads_stdin: bool = False

    def to_record(self, target: Target) -> dict[str, Any]:
        return {
            "uri": target.uri,
            "filename": target.path.name if target.path else target.stem,
            "interpreter": self.interpreter,
            "lines": self.lines,
            "functions": self.functions,
            "variables": self.variables,
            "commands": self.commands,
            "notable_commands": self.notable,
            "reads_stdin": self.reads_stdin,
        }


def read_script(text: str) -> ScriptFacts:
    """Static read of a shell script — no execution, no evaluation."""
    facts = ScriptFacts()
    lines = text.splitlines()
    facts.lines = len(lines)
    if lines and (match := _SHEBANG_RE.match(lines[0])):
        facts.interpreter = match.group(2) if match.group(1).endswith("env") and match.group(2) else match.group(1)

    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if match := _FUNC_RE.match(line):
            facts.functions.append(match.group(1))
            continue
        if match := _ASSIGN_RE.match(line):
            name, value = match.group(1), match.group(2).strip()
            if "$(" not in value and "`" not in value:
                facts.variables[name] = value.strip("\"'")[:120]
            continue
        try:
            tokens = shlex.split(stripped, comments=True)
        except ValueError:
            continue
        for index, token in enumerate(tokens):
            if index == 0 or token in ("|", "&&", "||", ";"):
                command = tokens[index + 1] if token in ("|", "&&", "||", ";") and index + 1 < len(tokens) else token
                base = Path(command).name
                if base and base not in seen and base.isidentifier() or base in _NOTABLE:
                    seen.add(base)
                    facts.commands.append(base)
        if "read " in stripped or "/dev/stdin" in stripped:
            facts.reads_stdin = True

    facts.commands = sorted(set(facts.commands))[:40]
    facts.notable = sorted(set(facts.commands) & _NOTABLE)
    return facts


@dataclass(frozen=True, slots=True)
class ExecPolicy:
    """Conditions under which a script may actually be run."""

    allow_exec: bool = False
    #: Absolute paths, or glob patterns, that may be executed.
    allow_paths: tuple[str, ...] = ()
    timeout_s: float = 30.0
    max_output_bytes: int = 4 << 20
    env: Mapping[str, str] = field(default_factory=dict)
    #: Environment variables passed through from the parent process.
    passthrough_env: tuple[str, ...] = ("PATH", "LANG", "LC_ALL", "TZ")
    cwd: str = ""

    def permits(self, path: Path) -> bool:
        if not self.allow_exec:
            return False
        if not self.allow_paths:
            return False
        resolved = path.resolve()
        return any(resolved.match(pattern) or str(resolved) == pattern for pattern in self.allow_paths)

    def environment(self) -> dict[str, str]:
        env = {key: os.environ[key] for key in self.passthrough_env if key in os.environ}
        env.update(self.env)
        return env


@dataclass(slots=True)
class ScriptResult:
    """Outcome of an executed script."""

    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def run_script(
    path: str | Path,
    *,
    policy: ExecPolicy,
    args: Sequence[str] = (),
) -> ScriptResult:
    """Run a script under an :class:`ExecPolicy`, capturing bounded output."""
    import time

    script = Path(path).expanduser().resolve()
    if not script.is_file():
        raise TransformError(f"no such script: {script}")
    if not policy.permits(script):
        raise TransformError(
            f"execution of {script} is not permitted; add it to ExecPolicy.allow_paths "
            "and set allow_exec=True to run it"
        )

    facts = read_script(script.read_text(encoding="utf-8", errors="replace"))
    interpreter = facts.interpreter or "/bin/sh"
    command = [interpreter, str(script), *map(str, args)]
    started = time.monotonic()
    try:
        completed = subprocess.run(  # noqa: S603 - command is an allow-listed script
            command,
            capture_output=True,
            timeout=policy.timeout_s,
            env=policy.environment(),
            cwd=policy.cwd or str(script.parent),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TransformError(f"script timed out after {policy.timeout_s}s: {script}") from exc
    except OSError as exc:
        raise TransformError(f"cannot execute {script}: {exc}") from exc

    cap = policy.max_output_bytes
    truncated = len(completed.stdout) > cap or len(completed.stderr) > cap
    return ScriptResult(
        exit_code=completed.returncode,
        stdout=completed.stdout[:cap].decode("utf-8", "replace"),
        stderr=completed.stderr[:cap].decode("utf-8", "replace"),
        duration_s=round(time.monotonic() - started, 4),
        truncated=truncated,
    )


class ShellProbe(BaseProbe):
    """Reads shell scripts. Runs them only under an explicit policy."""

    name = "shell"
    suffixes = (".sh", ".bash", ".zsh", ".ksh")

    def __init__(self, policy: ExecPolicy | None = None) -> None:
        self.policy = policy if policy is not None else ExecPolicy()

    def sniff(self, target: Target) -> float:
        if target.suffix in self.suffixes:
            return 0.9
        if target.head.startswith(b"#!") and b"sh" in target.head[:40]:
            return 0.8
        return 0.0

    def capture(self, target: Target, *, limit: int = 0) -> Capture:
        capture = self._capture(target)
        text = self._read_text(target)
        capture.bytes_read = len(text.encode("utf-8"))
        facts = read_script(text)
        capture.payloads.append(self._wrap([facts.to_record(target)], target, name=f"{target.entity}_script"))
        if facts.notable:
            capture.warnings.append(f"script invokes notable commands: {', '.join(facts.notable)}")

        if target.path is not None and self.policy.permits(target.path):
            capture.warnings.append("script executed under ExecPolicy")
            try:
                result = run_script(target.path, policy=self.policy)
            except TransformError as exc:
                capture.warnings.append(str(exc))
                return capture
            capture.labels = {"exit_code": str(result.exit_code), "duration_s": str(result.duration_s)}
            payload = self._shape_output(result.stdout, self._limit(limit))
            if payload is not None:
                capture.payloads.append(self._wrap(payload, target, name=f"{target.entity}_stdout", layout="stdout"))
            if not result.ok:
                capture.warnings.append(f"script exited {result.exit_code}: {result.stderr[:200]}")
        return capture

    @staticmethod
    def _shape_output(stdout: str, limit: int) -> Any:
        """Script output is JSON if it parses as JSON, otherwise lines."""
        text = stdout.strip()
        if not text:
            return None
        if text[:1] in ("{", "["):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
        records = [json.loads(line) for line in text.splitlines() if line.strip().startswith("{")] \
            if text.lstrip().startswith("{") else []
        if records:
            return records
        return [{"line_no": i, "line": line} for i, line in enumerate(text.splitlines()[:limit], 1)]
