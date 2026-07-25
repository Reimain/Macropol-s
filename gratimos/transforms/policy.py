"""Sandbox policy: what a transformation is allowed to be.

**What this is.** A blast-radius limiter for semi-trusted code — the ``.py``
files an operator deliberately placed in the transformations folder. It catches
accidents (a transform that opens a socket, walks the filesystem, or allocates
until the box dies) and bounds the damage when one misbehaves.

**What this is not.** An adversarial sandbox. Python in-process isolation is not
a security boundary, and this does not pretend otherwise: a determined author
with write access to the transformations folder can escape any pure-Python gate.
If you need to run genuinely untrusted code, run it in a container, a VM, or
under seccomp — and point Gratimos at *that* as a remote executor.

Given that, the controls are deliberately layered:

1. a static AST gate, before the file is ever executed;
2. a restricted import set enforced at runtime;
3. OS resource limits and a wall-clock timeout in a separate process;
4. JSON-only data crossing the boundary, so nothing executes on the way back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

#: Modules a transformation may import. Chosen for data work, and deliberately
#: excluding anything that touches the network, the process table, or the
#: import system itself.
SAFE_IMPORTS = frozenset({
    "base64", "bisect", "calendar", "collections", "copy", "csv", "dataclasses",
    "datetime", "decimal", "enum", "fractions", "functools", "hashlib", "heapq",
    "html", "io", "itertools", "json", "math", "numbers", "operator", "random",
    "re", "statistics", "string", "textwrap", "time", "types", "typing",
    "unicodedata", "uuid", "zoneinfo",
})

#: Never importable: each is a direct route out of the sandbox.
BLOCKED_IMPORTS = frozenset({
    "builtins", "ctypes", "gc", "importlib", "inspect", "marshal", "multiprocessing",
    "os", "pathlib", "pickle", "platform", "shutil", "signal", "socket", "sqlite3",
    "ssl", "subprocess", "sys", "tempfile", "threading", "urllib", "webbrowser",
})

#: Builtins removed from the execution namespace.
BLOCKED_BUILTINS = frozenset({
    "breakpoint", "compile", "eval", "exec", "exit", "globals", "help", "input",
    "locals", "memoryview", "open", "quit", "vars", "__import__",
})

#: Attribute names that expose the object graph and, through it, everything else.
BLOCKED_ATTRIBUTES = frozenset({
    "__builtins__", "__class__", "__closure__", "__code__", "__dict__", "__globals__",
    "__import__", "__loader__", "__mro__", "__reduce__", "__reduce_ex__", "__self__",
    "__subclasses__", "f_back", "f_builtins", "f_globals", "f_locals", "gi_frame",
})

MIB = 1 << 20


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    """The limits one transformation runs under."""

    allowed_imports: frozenset[str] = SAFE_IMPORTS
    blocked_imports: frozenset[str] = BLOCKED_IMPORTS
    blocked_builtins: frozenset[str] = BLOCKED_BUILTINS
    blocked_attributes: frozenset[str] = BLOCKED_ATTRIBUTES

    #: Wall-clock ceiling for the whole subprocess.
    timeout_s: float = 30.0
    #: RLIMIT_CPU — CPU seconds, which a busy loop cannot evade.
    cpu_seconds: int = 20
    #: RLIMIT_AS — address space. 0 disables (some platforms account oddly).
    memory_bytes: int = 512 * MIB
    #: RLIMIT_FSIZE — largest file the transform may write. 0 forbids writes.
    max_file_bytes: int = 0
    #: Cap on the payload handed in and the result handed back.
    max_payload_bytes: int = 64 * MIB
    max_rows: int = 1_000_000

    #: Run in a separate process. Disabling makes limits advisory only.
    isolate: bool = True
    #: Environment variables visible to the subprocess.
    env: Mapping[str, str] = field(default_factory=dict)

    def permits_import(self, module: str) -> bool:
        root = module.split(".")[0]
        if root in self.blocked_imports:
            return False
        return root in self.allowed_imports

    def with_imports(self, *modules: str) -> "SandboxPolicy":
        """Widen the import set — the deliberate way to allow a dependency."""
        blocked = set(modules) & self.blocked_imports
        if blocked:
            raise ValueError(f"these imports can never be allowed: {sorted(blocked)}")
        return SandboxPolicy(
            allowed_imports=self.allowed_imports | frozenset(modules),
            blocked_imports=self.blocked_imports,
            blocked_builtins=self.blocked_builtins,
            blocked_attributes=self.blocked_attributes,
            timeout_s=self.timeout_s,
            cpu_seconds=self.cpu_seconds,
            memory_bytes=self.memory_bytes,
            max_file_bytes=self.max_file_bytes,
            max_payload_bytes=self.max_payload_bytes,
            max_rows=self.max_rows,
            isolate=self.isolate,
            env=self.env,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_imports": sorted(self.allowed_imports),
            "timeout_s": self.timeout_s,
            "cpu_seconds": self.cpu_seconds,
            "memory_bytes": self.memory_bytes,
            "max_file_bytes": self.max_file_bytes,
            "max_rows": self.max_rows,
            "isolate": self.isolate,
        }


#: The policy used when none is supplied.
DEFAULT_POLICY = SandboxPolicy()

#: For trusted, kernel-authored transformations: same gate, looser limits.
TRUSTED_POLICY = SandboxPolicy(
    allowed_imports=SAFE_IMPORTS | {"gratimos"},
    timeout_s=120.0,
    cpu_seconds=90,
    memory_bytes=2048 * MIB,
)
