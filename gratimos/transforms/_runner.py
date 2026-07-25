"""Subprocess entry point for sandboxed transformations.

Runs as ``python -I -S _runner.py`` with a JSON request on stdin and a JSON
response on stdout. Deliberately imports nothing from ``gratimos``: the whole
point is that this process is small, isolated, and unable to reach back into the
kernel that spawned it.

Order of operations matters — resource limits are installed *before* the
transformation module is imported, so a module that misbehaves at import time is
already bounded.
"""

from __future__ import annotations

import builtins
import json
import sys
import traceback

_LOGS: list[str] = []
MAX_LOGS = 200


def _apply_limits(limits: dict) -> list[str]:
    """Install OS resource limits. Returns the ones that actually took."""
    applied: list[str] = []
    try:
        import resource
    except ImportError:  # pragma: no cover - non-POSIX platforms
        return ["unavailable: the resource module is POSIX-only"]

    cpu = int(limits.get("cpu_seconds") or 0)
    if cpu > 0:
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
            applied.append(f"RLIMIT_CPU={cpu}s")
        except (ValueError, OSError) as exc:
            applied.append(f"RLIMIT_CPU failed: {exc}")

    memory = int(limits.get("memory_bytes") or 0)
    if memory > 0:
        for name in ("RLIMIT_AS", "RLIMIT_DATA"):
            limit = getattr(resource, name, None)
            if limit is None:
                continue
            try:
                resource.setrlimit(limit, (memory, memory))
                applied.append(f"{name}={memory}")
                break
            except (ValueError, OSError) as exc:
                applied.append(f"{name} failed: {exc}")

    file_bytes = int(limits.get("max_file_bytes") or 0)
    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))
        applied.append(f"RLIMIT_FSIZE={file_bytes}")
    except (ValueError, OSError) as exc:  # pragma: no cover - platform dependent
        applied.append(f"RLIMIT_FSIZE failed: {exc}")

    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
        applied.append("RLIMIT_NPROC=0")
    except (ValueError, OSError, AttributeError):
        pass  # not available everywhere; the import gate already blocks subprocess
    return applied


def _restrict_imports(allowed: set[str], blocked: set[str]) -> None:
    """Enforce the import allow list at runtime, not just statically.

    The AST gate reads the transformation file itself; this catches imports
    reached indirectly — a permitted module importing a blocked one on the
    transformation's behalf.
    """
    real_import = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
        root = name.split(".")[0]
        if root in blocked or (allowed and root not in allowed):
            raise ImportError(f"import of {name!r} is blocked by the Gratimos sandbox policy")
        return real_import(name, globals, locals, fromlist, level)

    builtins.__import__ = guarded


def _strip_builtins(names: set[str]) -> None:
    for name in names:
        if name == "__import__":
            continue  # replaced by the guarded version, not removed
        if hasattr(builtins, name):
            try:
                delattr(builtins, name)
            except (AttributeError, TypeError):  # pragma: no cover
                pass


def _log(*parts: object) -> None:
    """The `log` helper handed to transformations, in place of print."""
    if len(_LOGS) < MAX_LOGS:
        _LOGS.append(" ".join(str(p) for p in parts)[:2000])


def _load_module(path: str, entrypoint: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location("gratimos_sandboxed_transform", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot build an import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["gratimos_sandboxed_transform"] = module
    spec.loader.exec_module(module)
    fn = getattr(module, entrypoint, None)
    if fn is None or not callable(fn):
        raise AttributeError(f"{path} defines no callable {entrypoint}()")
    return fn


def main() -> int:
    try:
        request = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        json.dump({"ok": False, "error": f"malformed request: {exc}"}, sys.stdout)
        return 2

    limits_applied = _apply_limits(request.get("limits", {}))
    allowed = set(request.get("allowed_imports", ()))
    blocked = set(request.get("blocked_imports", ()))
    # importlib is needed to load the transformation itself, so widen the set
    # for our own use and restrict only once the module is in hand.
    response: dict = {"ok": False, "limits_applied": limits_applied}

    try:
        fn = _load_module(request["module_path"], request.get("entrypoint", "transform"))
    except Exception as exc:  # noqa: BLE001
        response["error"] = f"failed to load transformation: {type(exc).__name__}: {exc}"
        response["traceback"] = traceback.format_exc()
        json.dump(response, sys.stdout, default=str)
        return 1

    _restrict_imports(allowed, blocked)
    _strip_builtins(set(request.get("blocked_builtins", ())))

    context = dict(request.get("context", {}))
    context["log"] = _log
    records = request.get("records", [])

    try:
        out = fn(records, context)
        result = list(out) if out is not None else []
        response.update({"ok": True, "records": result, "logs": _LOGS})
    except MemoryError:
        response.update({"error": "transformation exhausted its memory limit", "logs": _LOGS})
    except Exception as exc:  # noqa: BLE001
        response.update({
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
            "logs": _LOGS,
        })

    try:
        json.dump(response, sys.stdout, default=str)
    except (TypeError, ValueError) as exc:
        json.dump(
            {"ok": False, "error": f"transformation returned non-serializable data: {exc}",
             "limits_applied": limits_applied, "logs": _LOGS},
            sys.stdout,
        )
        return 1
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
