"""Reading a shell command without running it.

This is the layer that has to be right. Everything above it — task prediction,
routing to a model, promotion to automation — is judgement. This is the part
that decides whether `rm -rf $BUILD/` is a build step or an outage, and it must
decide *before* anything executes.

Two rules shape it:

**Parse, never execute.** Nothing here runs a subprocess, expands a glob, or
resolves a variable. A "safe" analyser that shells out to check something has
already lost. `shlex` in POSIX mode does the tokenising, and a command it cannot
tokenise is treated as dangerous rather than waved through — an unparseable
command is one nobody can review either.

**Unknown is a finding, not silence.** A binary the environment does not have,
an unrecognised flag on a destructive tool, an unquoted variable in a path being
deleted: each produces a stated finding. The failure mode this whole layer
exists to prevent is a plausible-looking script full of things nobody checked,
which runs once, works, and is then trusted forever — a routine written in a
language the next reader cannot verify.

The patterns below are the ones that actually cause incidents, not a general
theory of shell safety. `curl | sh`, `rm -rf` with an unquoted variable,
`chmod 777`, `dd of=/dev/sda`, `git push --force` to a default branch,
`kill -9 1`, `history -c`, `eval "$(…)"`. Each is a named finding with a reason
a reviewer can act on.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Iterable, Mapping, Sequence

from ..errors import GratimosError
from .environment import Environment


class ShellError(GratimosError):
    """A command could not be read, or was refused before execution."""


class Risk(IntEnum):
    """How bad it is if this is wrong. Ordered, and compared as such."""

    SAFE = 0            # reads, or writes only where it was told to
    CAUTION = 1         # changes state that can be put back
    DANGEROUS = 2       # destroys or exposes something recoverable only from backup
    CATASTROPHIC = 3    # unrecoverable, or hands the machine to someone else

    @property
    def label(self) -> str:
        return self.name.lower()


class Category(str, Enum):
    """What kind of thing the command does. Drives reporting, not the verdict."""

    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    NETWORK = "network"
    PRIVILEGE = "privilege"
    PACKAGE = "package"
    PROCESS = "process"
    VCS = "vcs"
    UNKNOWN = "unknown"


#: Shell builtins, which never appear on PATH and must not be reported missing.
BUILTINS = frozenset({
    "cd", "echo", "export", "set", "unset", "source", ".", "test", "[", "true",
    "false", "exit", "return", "shift", "read", "eval", "exec", "trap", "wait",
    "alias", "unalias", "pwd", "umask", "type", "hash", "history", "printf",
    "local", "declare", "let", "if", "then", "else", "fi", "for", "while", "do",
    "done", "case", "esac", "function", "time",
})

#: Binaries that only ever read. Used to keep the noise down: a plan full of
#: `ls` and `cat` marked CAUTION teaches people to ignore the warnings.
READERS = frozenset({
    "ls", "cat", "head", "tail", "grep", "rg", "find", "wc", "sort", "uniq",
    "diff", "stat", "file", "du", "df", "ps", "top", "env", "which", "whoami",
    "date", "uname", "id", "jq", "awk", "sed", "less", "more", "tree",
})

#: Commands that reach the network. Not dangerous alone; dangerous piped.
NETWORK = frozenset({"curl", "wget", "ssh", "scp", "rsync", "nc", "telnet", "ftp"})

#: Package managers — state changes that are usually recoverable.
PACKAGES = frozenset({
    "apt", "apt-get", "dnf", "yum", "pacman", "apk", "brew", "npm", "pnpm",
    "yarn", "pip", "pip3", "uv", "gem", "cargo", "go", "choco", "winget",
})

#: Anything here needs a reason before it runs.
DESTRUCTIVE = frozenset({
    "rm", "rmdir", "shred", "dd", "mkfs", "fdisk", "parted", "truncate",
    "kill", "pkill", "killall", "reboot", "shutdown", "halt", "poweroff",
})


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing worth saying about a command before it runs."""

    code: str
    risk: Risk
    message: str
    remediation: str = ""
    segment: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code, "risk": self.risk.label, "message": self.message,
            "remediation": self.remediation, "segment": self.segment,
        }

    def __str__(self) -> str:
        return f"[{self.risk.label}] {self.code}: {self.message}"


@dataclass(frozen=True, slots=True)
class Segment:
    """One stage of a pipeline."""

    raw: str
    tokens: tuple[str, ...]

    @property
    def binary(self) -> str:
        """The program being run, seeing past `sudo` and `env VAR=x`."""
        for token in self.tokens:
            if token in ("sudo", "doas", "env", "nohup", "nice", "time", "xargs"):
                continue
            if "=" in token and not token.startswith("-"):
                continue      # a leading VAR=value assignment
            return token
        return self.tokens[0] if self.tokens else ""

    @property
    def flags(self) -> tuple[str, ...]:
        return tuple(token for token in self.tokens if token.startswith("-"))

    @property
    def operands(self) -> tuple[str, ...]:
        return tuple(
            token for token in self.tokens[1:]
            if not token.startswith("-") and "=" not in token
        )

    def has_flag(self, *names: str) -> bool:
        """Handles both `-rf` and `-r -f`, which is where naive checks fail."""
        for flag in self.flags:
            if flag in names:
                return True
            if flag.startswith("-") and not flag.startswith("--"):
                letters = set(flag[1:])
                if any(name.startswith("-") and len(name) == 2 and name[1] in letters
                       for name in names):
                    return True
        return False


@dataclass(frozen=True, slots=True)
class Command:
    """A parsed command line. Data — nothing here can run."""

    raw: str
    segments: tuple[Segment, ...] = ()
    parsed: bool = True
    parse_error: str = ""

    @property
    def binaries(self) -> tuple[str, ...]:
        return tuple(segment.binary for segment in self.segments if segment.binary)

    @property
    def elevated(self) -> bool:
        return any("sudo" in segment.tokens or "doas" in segment.tokens
                   for segment in self.segments)

    @property
    def piped_to_interpreter(self) -> bool:
        """`curl … | sh` — remote code, executed unread, in one line."""
        if len(self.segments) < 2:
            return False
        interpreters = {"sh", "bash", "zsh", "python", "python3", "perl", "ruby", "node"}
        fetchers = {"curl", "wget", "fetch"}
        for index, segment in enumerate(self.segments[:-1]):
            if segment.binary in fetchers and self.segments[index + 1].binary in interpreters:
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw, "binaries": list(self.binaries),
            "elevated": self.elevated, "parsed": self.parsed,
            "parse_error": self.parse_error,
        }

    def __str__(self) -> str:
        return self.raw


def parse(text: str) -> Command:
    """Tokenise a command line. Never runs anything, never expands anything."""
    raw = text.strip()
    if not raw:
        return Command(raw="", segments=(), parsed=False,
                       parse_error="the command is empty")

    stages = [part.strip() for part in _split_pipeline(raw)]
    segments: list[Segment] = []
    for stage in stages:
        if not stage:
            continue
        try:
            tokens = tuple(shlex.split(stage, posix=True))
        except ValueError as exc:
            # Unbalanced quotes. Refusing beats guessing: a command that cannot
            # be tokenised cannot be reviewed by a human either.
            return Command(raw=raw, segments=(), parsed=False,
                           parse_error=f"cannot read {stage!r}: {exc}")
        if tokens:
            segments.append(Segment(raw=stage, tokens=tokens))

    return Command(raw=raw, segments=tuple(segments))


def _split_pipeline(text: str) -> list[str]:
    """Split on `|` outside quotes. `||` is a separator too, not a pipe."""
    parts: list[str] = []
    current: list[str] = []
    quote = ""
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            current.append(char)
            if char == quote and (index == 0 or text[index - 1] != "\\"):
                quote = ""
        elif char in "\"'":
            quote = char
            current.append(char)
        elif char == "|":
            parts.append("".join(current))
            current = []
            if index + 1 < len(text) and text[index + 1] == "|":
                index += 1
        elif char == ";" or (char == "&" and index + 1 < len(text) and text[index + 1] == "&"):
            parts.append("".join(current))
            current = []
            if char == "&":
                index += 1
        else:
            current.append(char)
        index += 1
    parts.append("".join(current))
    return parts


#: An unquoted `$VAR` or `${VAR}` in an operand. Empty-expansion is what turns
#: `rm -rf $DIR/` into `rm -rf /`, and it has taken down real companies.
_VARIABLE = re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?")

#: Paths that must never be a deletion target.
_ROOTS = frozenset({"/", "/*", "~", "~/", "/usr", "/etc", "/var", "/home", "/boot",
                    "C:\\", "C:/", "/System", "/Library"})


@dataclass(frozen=True, slots=True)
class Analysis:
    """What reading the command turned up."""

    command: Command
    findings: tuple[Finding, ...] = ()
    categories: frozenset[Category] = frozenset()
    unknown_binaries: tuple[str, ...] = ()

    @property
    def risk(self) -> Risk:
        """The worst thing found. Never an average — one catastrophe is enough."""
        return max((finding.risk for finding in self.findings), default=Risk.SAFE)

    @property
    def blocking(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.risk >= Risk.DANGEROUS)

    def of(self, code: str) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.code == code)

    def explain(self) -> str:
        if not self.findings:
            return f"{self.command.raw} — nothing of concern found"
        lines = [f"{self.command.raw} — {self.risk.label}"]
        for finding in sorted(self.findings, key=lambda f: -f.risk):
            lines.append(f"  {finding}")
            if finding.remediation:
                lines.append(f"      → {finding.remediation}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command.to_dict(),
            "risk": self.risk.label,
            "categories": sorted(category.value for category in self.categories),
            "unknown_binaries": list(self.unknown_binaries),
            "findings": [finding.to_dict() for finding in self.findings],
        }

    def __str__(self) -> str:
        return f"{self.command.raw} [{self.risk.label}]"


def analyse(
    text: str | Command,
    environment: Environment | None = None,
    *,
    trusted: Iterable[str] = (),
) -> Analysis:
    """Read a command and report everything worth knowing before it runs.

    `trusted` names binaries the deployment vouches for even though the probe
    did not find them — a tool on a PATH the probe cannot see, or one that is
    installed as part of the task itself. They are treated as present rather
    than merely exempted from refusal: leaving the finding in place would keep
    driving the command's risk up, and a policy that trusts a binary but still
    calls it dangerous is two policies disagreeing.
    """
    command = text if isinstance(text, Command) else parse(text)

    if not command.parsed:
        return Analysis(
            command=command,
            findings=(Finding(
                code="unreadable", risk=Risk.DANGEROUS,
                message=command.parse_error,
                remediation="rewrite it so it can be tokenised and reviewed",
            ),),
        )

    findings: list[Finding] = []
    categories: set[Category] = set()
    unknown: list[str] = []
    vouched = frozenset(trusted)

    if command.piped_to_interpreter:
        findings.append(Finding(
            code="remote-code-execution", risk=Risk.CATASTROPHIC,
            message="downloads code and pipes it straight into an interpreter",
            remediation=(
                "fetch to a file, read it, verify its checksum, then run it"
            ),
            segment=command.raw,
        ))
        categories.update({Category.NETWORK, Category.PRIVILEGE})

    for segment in command.segments:
        binary = segment.binary
        if not binary:
            continue

        if binary in READERS:
            categories.add(Category.READ)
        elif binary in NETWORK:
            categories.add(Category.NETWORK)
        elif binary in PACKAGES:
            categories.add(Category.PACKAGE)
        elif binary in DESTRUCTIVE:
            categories.add(Category.DELETE)
        elif binary in ("git", "hg", "svn"):
            categories.add(Category.VCS)
        else:
            categories.add(Category.UNKNOWN)

        if (
            environment is not None
            and binary not in BUILTINS
            and binary not in vouched
            and not environment.has(binary)
        ):
            unknown.append(binary)
            findings.append(Finding(
                code="unknown-binary", risk=Risk.DANGEROUS,
                message=f"{binary!r} is not installed on this machine",
                remediation=(
                    f"install it first"
                    + (f" ({environment.package_manager} install {binary})"
                       if environment.package_manager else "")
                    + ", or choose a tool that is present"
                ),
                segment=segment.raw,
            ))

        findings.extend(_inspect(segment, command))

    if command.elevated:
        categories.add(Category.PRIVILEGE)
        findings.append(Finding(
            code="elevated", risk=Risk.CAUTION,
            message="runs with elevated privilege",
            remediation="confirm every path it touches is one you meant",
            segment=command.raw,
        ))

    return Analysis(
        command=command,
        findings=tuple(findings),
        categories=frozenset(categories),
        unknown_binaries=tuple(dict.fromkeys(unknown)),
    )


def _inspect(segment: Segment, command: Command) -> list[Finding]:
    """The specific footguns, each named."""
    binary = segment.binary
    found: list[Finding] = []

    if binary in ("rm", "shred") and segment.has_flag("-r", "-R", "--recursive", "-f"):
        for operand in segment.operands:
            if operand in _ROOTS or operand.rstrip("/*") in ("", "/"):
                found.append(Finding(
                    code="recursive-delete-of-root", risk=Risk.CATASTROPHIC,
                    message=f"recursively deletes {operand!r}",
                    remediation="target a specific directory, never a filesystem root",
                    segment=segment.raw,
                ))
            elif _VARIABLE.search(operand):
                found.append(Finding(
                    code="unquoted-variable-delete", risk=Risk.CATASTROPHIC,
                    message=(
                        f"recursively deletes {operand!r}, which expands at runtime; "
                        f"if the variable is empty this deletes from the root"
                    ),
                    remediation=(
                        'guard it: `[ -n "$VAR" ] || exit 1` before the delete, '
                        "and quote the path"
                    ),
                    segment=segment.raw,
                ))
            else:
                found.append(Finding(
                    code="recursive-delete", risk=Risk.DANGEROUS,
                    message=f"recursively deletes {operand!r}",
                    remediation="confirm the path, and that it is backed up or reproducible",
                    segment=segment.raw,
                ))

    if binary == "dd":
        for token in segment.tokens:
            if token.startswith("of=/dev/"):
                found.append(Finding(
                    code="raw-device-write", risk=Risk.CATASTROPHIC,
                    message=f"writes directly to the block device {token[3:]!r}",
                    remediation="verify the device name; this destroys the disk if wrong",
                    segment=segment.raw,
                ))

    if binary in ("mkfs", "fdisk", "parted") or binary.startswith("mkfs."):
        found.append(Finding(
            code="filesystem-format", risk=Risk.CATASTROPHIC,
            message=f"{binary!r} rewrites a partition table or filesystem",
            remediation="confirm the target device out of band before running it",
            segment=segment.raw,
        ))

    if binary == "chmod" and any(mode in segment.operands for mode in ("777", "0777", "a+rwx")):
        found.append(Finding(
            code="world-writable", risk=Risk.DANGEROUS,
            message="grants write permission to every user on the machine",
            remediation="grant the narrowest mode that works, usually 750 or 640",
            segment=segment.raw,
        ))

    if binary in ("kill", "pkill") and segment.has_flag("-9") and "1" in segment.operands:
        found.append(Finding(
            code="kill-init", risk=Risk.CATASTROPHIC,
            message="sends SIGKILL to PID 1, which halts the machine or container",
            remediation="signal the actual process, or use the service manager",
            segment=segment.raw,
        ))

    if binary == "git":
        if "push" in segment.operands and segment.has_flag("--force", "-f"):
            found.append(Finding(
                code="force-push", risk=Risk.DANGEROUS,
                message="force-pushes, which discards commits on the remote",
                remediation="use --force-with-lease so a concurrent push is not lost",
                segment=segment.raw,
            ))
        if "clean" in segment.operands and segment.has_flag("-f", "-d", "-x"):
            found.append(Finding(
                code="working-tree-clean", risk=Risk.DANGEROUS,
                message="deletes untracked files, which are not recoverable from git",
                remediation="run it with --dry-run first",
                segment=segment.raw,
            ))
        if "reset" in segment.operands and segment.has_flag("--hard"):
            found.append(Finding(
                code="hard-reset", risk=Risk.CAUTION,
                message="discards uncommitted work in the working tree",
                remediation="stash first if anything there matters",
                segment=segment.raw,
            ))

    if binary in ("eval", "exec") or "eval" in segment.tokens[:1]:
        found.append(Finding(
            code="dynamic-execution", risk=Risk.DANGEROUS,
            message="executes a string assembled at runtime, so what runs is not what is written",
            remediation="write the command out, or refuse it",
            segment=segment.raw,
        ))

    if binary == "history" and segment.has_flag("-c"):
        found.append(Finding(
            code="audit-erasure", risk=Risk.DANGEROUS,
            message="clears the shell history, removing the record of what was run",
            remediation="leave the history; if it holds a secret, rotate the secret",
            segment=segment.raw,
        ))

    if binary == "iptables" and segment.has_flag("-F"):
        found.append(Finding(
            code="firewall-flush", risk=Risk.CATASTROPHIC,
            message="flushes every firewall rule, exposing the machine",
            remediation="delete the specific rule, and keep a restore ready",
            segment=segment.raw,
        ))

    if binary in ("reboot", "shutdown", "halt", "poweroff"):
        found.append(Finding(
            code="power-state", risk=Risk.DANGEROUS,
            message=f"{binary!r} takes the machine down",
            remediation="confirm this is the machine you think it is",
            segment=segment.raw,
        ))

    if binary == "curl" and segment.has_flag("-k", "--insecure"):
        found.append(Finding(
            code="tls-verification-disabled", risk=Risk.DANGEROUS,
            message="disables TLS verification, so the response cannot be trusted",
            remediation="fix the certificate chain rather than skipping the check",
            segment=segment.raw,
        ))

    return found
