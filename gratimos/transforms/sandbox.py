"""The static gate and the isolated executor.

:func:`inspect_source` parses a transformation and refuses it before anything
runs. :class:`Sandbox` then executes the approved file in a subprocess under OS
resource limits, exchanging JSON on stdin/stdout so no object graph — and
therefore no code — crosses the boundary in either direction.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..errors import SandboxViolation, TransformError
from ..ids import now_ns
from .policy import DEFAULT_POLICY, SandboxPolicy

#: The function a transformation module must define.
ENTRYPOINT = "transform"
#: Optional module-level metadata a transformation may declare.
METADATA_NAMES = ("NAME", "APPLIES_TO", "DESCRIPTION", "VERSION", "REQUIRES")

_RUNNER = Path(__file__).with_name("_runner.py")


@dataclass(slots=True)
class Inspection:
    """The verdict of the static gate."""

    path: str
    approved: bool
    violations: list[SandboxViolation] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def reasons(self) -> list[str]:
        return [str(v) for v in self.violations]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "approved": self.approved,
            "violations": self.reasons,
            "imports": self.imports,
            "functions": self.functions,
            "metadata": self.metadata,
        }

    def raise_if_rejected(self) -> None:
        if not self.approved:
            raise self.violations[0] if self.violations else SandboxViolation("rejected")


class _Gate(ast.NodeVisitor):
    """Walks a transformation looking for the ways out."""

    def __init__(self, policy: SandboxPolicy) -> None:
        self.policy = policy
        self.violations: list[SandboxViolation] = []
        self.imports: list[str] = []
        self.functions: list[str] = []

    def _reject(self, reason: str, node: ast.AST) -> None:
        self.violations.append(
            SandboxViolation(reason, node=type(node).__name__, lineno=getattr(node, "lineno", 0))
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(alias.name)
            if not self.policy.permits_import(alias.name):
                self._reject(f"import of {alias.name!r} is not permitted", node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if node.level:
            self._reject("relative imports are not permitted in a transformation", node)
        else:
            self.imports.append(module)
            if not self.policy.permits_import(module):
                self._reject(f"import from {module!r} is not permitted", node)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in self.policy.blocked_attributes:
            self._reject(f"attribute {node.attr!r} exposes the interpreter internals", node)
        elif node.attr.startswith("__") and node.attr.endswith("__") and node.attr not in (
            "__name__", "__doc__", "__len__", "__iter__", "__str__", "__repr__"
        ):
            self._reject(f"dunder attribute {node.attr!r} is not permitted", node)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and node.id in self.policy.blocked_builtins:
            self._reject(f"builtin {node.id!r} is not permitted", node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self._reject("global statements are not permitted in a transformation", node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.generic_visit(node)


def inspect_source(
    source: str, *, path: str = "<transform>", policy: SandboxPolicy = DEFAULT_POLICY
) -> Inspection:
    """Statically approve or reject a transformation. Nothing is executed."""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        violation = SandboxViolation(f"syntax error: {exc.msg}", lineno=exc.lineno or 0)
        return Inspection(path=path, approved=False, violations=[violation])

    gate = _Gate(policy)
    gate.visit(tree)

    metadata: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in METADATA_NAMES:
                try:
                    metadata[name] = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    gate.violations.append(
                        SandboxViolation(
                            f"metadata {name!r} must be a literal", lineno=node.lineno
                        )
                    )

    if ENTRYPOINT not in gate.functions:
        gate.violations.append(
            SandboxViolation(f"no {ENTRYPOINT}() function defined in {path}")
        )

    return Inspection(
        path=path,
        approved=not gate.violations,
        violations=gate.violations,
        imports=sorted(set(gate.imports)),
        functions=sorted(set(gate.functions)),
        metadata=metadata,
    )


def inspect_file(path: str | Path, *, policy: SandboxPolicy = DEFAULT_POLICY) -> Inspection:
    file = Path(path)
    try:
        source = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise TransformError(f"cannot read transformation {file}: {exc}") from exc
    return inspect_source(source, path=str(file), policy=policy)


@dataclass(slots=True)
class SandboxResult:
    """What a transformation produced, and what it cost."""

    ok: bool
    records: list[Any] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    error: str = ""
    traceback: str = ""
    duration_s: float = 0.0
    rows_in: int = 0
    rows_out: int = 0
    limits_applied: list[str] = field(default_factory=list)
    ts_ns: int = field(default_factory=now_ns)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "duration_s": round(self.duration_s, 4),
            "error": self.error,
            "logs": self.logs[:50],
            "limits_applied": self.limits_applied,
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        state = "ok" if self.ok else f"failed: {self.error[:60]}"
        return f"<SandboxResult {state} rows {self.rows_in}->{self.rows_out}>"


class Sandbox:
    """Runs approved transformations under a policy."""

    def __init__(self, policy: SandboxPolicy = DEFAULT_POLICY) -> None:
        self.policy = policy

    def run(
        self,
        path: str | Path,
        records: Sequence[Mapping[str, Any]],
        *,
        context: Mapping[str, Any] | None = None,
        inspection: Inspection | None = None,
    ) -> SandboxResult:
        """Execute a transformation over records, returning plain data."""
        file = Path(path).resolve()
        checked = inspection if inspection is not None else inspect_file(file, policy=self.policy)
        checked.raise_if_rejected()

        rows = list(records)
        if len(rows) > self.policy.max_rows:
            raise TransformError(
                f"{len(rows)} rows exceeds the sandbox limit of {self.policy.max_rows}"
            )
        request = {
            "module_path": str(file),
            "entrypoint": ENTRYPOINT,
            "records": rows,
            "context": dict(context or {}),
            "limits": {
                "cpu_seconds": self.policy.cpu_seconds,
                "memory_bytes": self.policy.memory_bytes,
                "max_file_bytes": self.policy.max_file_bytes,
            },
            "blocked_builtins": sorted(self.policy.blocked_builtins),
            "allowed_imports": sorted(self.policy.allowed_imports),
            "blocked_imports": sorted(self.policy.blocked_imports),
        }
        try:
            payload = json.dumps(request, default=str).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise TransformError(f"records are not JSON-serializable: {exc}") from exc
        if len(payload) > self.policy.max_payload_bytes:
            raise TransformError(
                f"payload of {len(payload)} bytes exceeds the sandbox limit "
                f"of {self.policy.max_payload_bytes}"
            )

        if not self.policy.isolate:
            return self._run_inline(file, rows, dict(context or {}))
        return self._run_isolated(payload, rows_in=len(rows))

    # -- execution modes -------------------------------------------------

    def _run_isolated(self, payload: bytes, *, rows_in: int) -> SandboxResult:
        started = time.monotonic()
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            **dict(self.policy.env),
        }
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [sys.executable, "-I", "-S", str(_RUNNER)],
                input=payload,
                capture_output=True,
                timeout=self.policy.timeout_s,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                ok=False, rows_in=rows_in, duration_s=time.monotonic() - started,
                error=f"transformation exceeded the {self.policy.timeout_s}s wall-clock limit",
            )
        except OSError as exc:
            raise TransformError(f"cannot start the sandbox process: {exc}") from exc

        duration = time.monotonic() - started
        stdout = completed.stdout.decode("utf-8", "replace").strip()
        stderr = completed.stderr.decode("utf-8", "replace").strip()
        if not stdout:
            return SandboxResult(
                ok=False, rows_in=rows_in, duration_s=duration,
                error=(
                    f"sandbox produced no output (exit {completed.returncode}); "
                    "this usually means a resource limit killed it"
                ),
                traceback=stderr[:4000],
            )
        try:
            response = json.loads(stdout)
        except json.JSONDecodeError as exc:
            return SandboxResult(
                ok=False, rows_in=rows_in, duration_s=duration,
                error=f"sandbox returned malformed output: {exc}", traceback=stderr[:4000],
            )
        return SandboxResult(
            ok=bool(response.get("ok")),
            records=list(response.get("records", [])),
            logs=list(response.get("logs", [])),
            error=str(response.get("error", "")),
            traceback=str(response.get("traceback", ""))[:4000],
            duration_s=duration,
            rows_in=rows_in,
            rows_out=len(response.get("records", [])),
            limits_applied=list(response.get("limits_applied", [])),
        )

    def _run_inline(self, file: Path, rows: list[Any], context: dict[str, Any]) -> SandboxResult:
        """In-process execution: the AST gate still applies, OS limits do not.

        Only for debugging a transformation, and it says so in the result.
        """
        import importlib.util

        started = time.monotonic()
        spec = importlib.util.spec_from_file_location(f"gratimos_transform_{file.stem}", file)
        if spec is None or spec.loader is None:
            raise TransformError(f"cannot load transformation {file}")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            fn = getattr(module, ENTRYPOINT)
            out = fn(rows, context)
            records = list(out) if out is not None else []
            return SandboxResult(
                ok=True, records=records, rows_in=len(rows), rows_out=len(records),
                duration_s=time.monotonic() - started,
                limits_applied=["none: isolate=False, resource limits not enforced"],
                logs=["ran in-process; OS resource limits were not applied"],
            )
        except Exception as exc:  # noqa: BLE001 - report, do not propagate
            import traceback as _tb

            return SandboxResult(
                ok=False, rows_in=len(rows), duration_s=time.monotonic() - started,
                error=f"{type(exc).__name__}: {exc}", traceback=_tb.format_exc()[:4000],
                limits_applied=["none: isolate=False"],
            )
