"""An external tool, described well enough to be trusted with an answer.

Kernel thinking: a verb is a syscall, a dispatcher is a driver, and an external
tool is the device. The kernel does not reimplement the device — when a capability
wants `git log` or `rg`, it dispatches. Writing a Python approximation of ripgrep
would be slower, less correct, and a permanent maintenance tax on a problem
somebody else already solved.

Two fields here carry most of the weight, and both exist because dispatching has
consequences a naive version would discover in production.

`determinism` is the first. "The same composition produces the same digest" is a
promise made in §24, and system tools break it casually: `sort` changes collation
with `LC_ALL`, `git log` output depends on the clone, and anything touching the
network depends on the day. So a tool declares how reproducible it is, and a flow
that used an `ENVIRONMENTAL` tool is reported as environment-bound rather than
claiming a reproducibility it does not have.

`version_argv` is the second. A dispatched answer that differs between two
machines has to be *attributable*, so the tool's version is captured and travels
as evidence. Without it, "why did CI say something different" has no answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence


class Determinism(str, Enum):
    """How reproducible a tool's answer is. Ordered least to most surprising."""

    DETERMINISTIC = "deterministic"     # same input, same output, anywhere
    ENVIRONMENTAL = "environmental"     # depends on locale, platform, or version
    VOLATILE = "volatile"               # depends on time, network, or mutable state

    @property
    def reproducible(self) -> bool:
        return self is Determinism.DETERMINISTIC

    @property
    def note(self) -> str:
        return {
            Determinism.DETERMINISTIC: "reproducible on any machine",
            Determinism.ENVIRONMENTAL: (
                "depends on this machine's tool version and locale, so the same "
                "composition may answer differently elsewhere"
            ),
            Determinism.VOLATILE: (
                "depends on time or on state outside this repository, so the "
                "answer is a snapshot rather than a fact"
            ),
        }[self]


#: The environment every dispatch runs under. Normalised rather than inherited,
#: because `sort` under a different `LC_ALL` orders differently and a pipeline that
#: quietly changed its answer with the operator's shell settings would be worse
#: than one that refused to run.
NORMALISED_ENV: Mapping[str, str] = {
    "LC_ALL": "C",
    "LANG": "C",
    "TZ": "UTC",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",     # never block waiting for credentials
    "NO_COLOR": "1",                # colour codes are not data
    "TERM": "dumb",
}


@dataclass(frozen=True, slots=True)
class Tool:
    """One external binary this platform knows how to ask for.

    Declared as data so the set of tools is inspectable, testable and
    plugin-extensible — the same reason verbs are data in §24.
    """

    name: str                                  # the capability: "grep", "git"
    argv: tuple[str, ...]                      # the binary and its fixed prefix
    summary: str = ""
    determinism: Determinism = Determinism.ENVIRONMENTAL
    version_argv: tuple[str, ...] = ()         # how to ask its version
    alternatives: tuple[tuple[str, ...], ...] = ()   # tried in order if absent
    fallback: str = ""                         # what happens without it
    timeout: float = 30.0
    max_output: int = 8 * 1024 * 1024

    @property
    def binary(self) -> str:
        return self.argv[0] if self.argv else self.name

    def candidates(self) -> tuple[tuple[str, ...], ...]:
        """Every argv worth trying, preferred first.

        `rg` before `grep` is the point of this: ripgrep is faster and its absence
        should fall back rather than fail, but nothing should have to say so twice.
        """
        return (self.argv, *self.alternatives)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "argv": list(self.argv), "summary": self.summary,
            "determinism": self.determinism.value,
            "reproducible": self.determinism.reproducible,
            "alternatives": [list(item) for item in self.alternatives],
            "fallback": self.fallback, "timeout": self.timeout,
        }

    def __str__(self) -> str:
        return f"{self.name} ({self.binary}, {self.determinism.value})"


@dataclass(frozen=True, slots=True)
class Invocation:
    """One concrete call: the resolved argv, and where it runs."""

    tool: Tool
    argv: tuple[str, ...]
    cwd: str = "."
    stdin: str = ""

    @property
    def rendered(self) -> str:
        """The call as a human would read it. For explanations only.

        Deliberately *not* used to execute anything — execution takes the argv
        tuple. A rendered string that could be re-parsed and run is how shell
        injection gets in through the back door of a logging helper.
        """
        import shlex

        return " ".join(shlex.quote(item) for item in self.argv)

    def __str__(self) -> str:
        return self.rendered


@dataclass(frozen=True, slots=True)
class Outcome:
    """What a dispatch produced, including its failure and its provenance."""

    tool: str
    argv: tuple[str, ...] = ()
    stdout: str = ""
    stderr: str = ""
    code: int = 0
    version: str = ""
    duration: float = 0.0
    available: bool = True
    truncated: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.available and self.code == 0 and not self.error

    @property
    def lines(self) -> tuple[str, ...]:
        return tuple(line for line in self.stdout.splitlines() if line)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool, "argv": list(self.argv), "code": self.code,
            "version": self.version, "available": self.available,
            "ok": self.ok, "truncated": self.truncated, "error": self.error,
            "duration": round(self.duration, 4),
            "stdout_lines": len(self.lines),
            "stderr": self.stderr[:2000],
        }

    def __str__(self) -> str:
        if not self.available:
            return f"{self.tool}: not installed"
        if self.error:
            return f"{self.tool}: {self.error}"
        return f"{self.tool} exited {self.code}, {len(self.lines)} line(s)"


#: The tools this platform knows how to dispatch to. Registering one here is all
#: it takes for a capability to prefer the real thing over an approximation.
KNOWN: tuple[Tool, ...] = (
    Tool(
        name="git", argv=("git",),
        summary="repository history — git's own answer is the only correct one",
        determinism=Determinism.DETERMINISTIC,
        version_argv=("git", "--version"),
        fallback="history is unavailable; the working tree is still readable",
    ),
    Tool(
        name="grep", argv=("rg", "--no-heading", "--line-number", "--color", "never"),
        summary="fast content search across a large tree",
        determinism=Determinism.ENVIRONMENTAL,
        version_argv=("rg", "--version"),
        alternatives=(("grep", "-rn"),),
        fallback=(
            "the stdlib scanner runs instead — correct, and much slower on a "
            "large tree"
        ),
    ),
    Tool(
        name="jq", argv=("jq",),
        summary="reshape a wire flow's JSON in the dialect people already know",
        determinism=Determinism.DETERMINISTIC,
        version_argv=("jq", "--version"),
        fallback="use --json and pipe it to whatever you have",
    ),
    Tool(
        name="pdftotext", argv=("pdftotext", "-layout"),
        summary="PDF text the stdlib reader cannot reach",
        determinism=Determinism.ENVIRONMENTAL,
        version_argv=("pdftotext", "-v"),
        fallback=(
            "the stdlib PDF reader handles Flate-compressed text with standard "
            "encodings; anything else is quarantined rather than reported empty"
        ),
    ),
)


def known() -> dict[str, Tool]:
    return {tool.name: tool for tool in KNOWN}
