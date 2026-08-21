"""The default driver: subprocess, argv lists, no shell, ever.

Three rules, and each one is the absence of a specific disaster:

**No `shell=True`, and no command built by string concatenation.** Every call takes
an argv tuple. A path with a space in it, a branch named `$(rm -rf /)`, a package
called `--upload-pack=…` — all harmless as argv elements and all catastrophic
through a shell. `tests/test_slpie_dispatch.py` walks this package with `ast` to
assert the rule holds, because a code review will eventually miss one.

**A normalised environment.** `LC_ALL=C`, `TZ=UTC`, no inherited locale, no colour.
Inheriting the operator's environment means `sort` collates differently on their
machine than in CI, and a pipeline whose answer depends on shell settings is a
pipeline nobody can reproduce.

**A missing binary is an answer, not an exception.** `probe` reports absence, the
caller falls back, and the fallback is *announced* as a gap. Silently substituting
an approximation produces an answer that looks identical to the real one and is
not — which is the failure mode the whole gap mechanism exists to prevent.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Any, Mapping, Sequence

from ..errors import SlpieError
from .tool import NORMALISED_ENV, Determinism, Invocation, Outcome, Tool


class DispatchError(SlpieError):
    """A dispatch was constructed wrongly. A tool failing is an `Outcome`."""


#: Probe results, cached per process. `shutil.which` hits the filesystem for every
#: entry on PATH, and a discovery pass asking "is rg here" per file would spend
#: more time probing than searching.
_PROBED: dict[str, tuple[bool, str, tuple[str, ...]]] = {}


def probe(tool: Tool, *, refresh: bool = False) -> tuple[bool, str, tuple[str, ...]]:
    """Is it installed, which variant, and what version.

    Returns `(available, version, argv)`. The argv is the *resolved* one — which
    matters because `grep` prefers `rg` and falls back to POSIX `grep` with
    different flags, and the caller must know which it got.
    """
    if not refresh and tool.name in _PROBED:
        return _PROBED[tool.name]

    for candidate in tool.candidates():
        if not candidate:
            continue
        if shutil.which(candidate[0]) is None:
            continue
        version = _version(tool, candidate)
        result = (True, version, tuple(candidate))
        _PROBED[tool.name] = result
        return result

    result = (False, "", ())
    _PROBED[tool.name] = result
    return result


def available(tool: Tool) -> bool:
    return probe(tool)[0]


def reset_probes() -> None:
    """Forget what is installed. For tests that pretend a tool is missing."""
    _PROBED.clear()


def _version(tool: Tool, argv: Sequence[str]) -> str:
    """The tool's version, captured because a differing answer must be attributable."""
    if not tool.version_argv:
        return ""
    probe_argv = (
        (argv[0], *tool.version_argv[1:]) if tool.version_argv[0] != argv[0]
        else tuple(tool.version_argv)
    )
    try:
        completed = subprocess.run(          # noqa: S603 - argv list, no shell
            list(probe_argv),
            capture_output=True, text=True, timeout=5,
            env=_environment(), check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (completed.stdout or completed.stderr or "").strip().splitlines()[:1][0] \
        if (completed.stdout or completed.stderr).strip() else ""


def _environment() -> dict[str, str]:
    """A normalised environment, keeping only what a process genuinely needs.

    PATH has to survive or nothing is findable; HOME because some tools refuse to
    start without it. Everything else is replaced rather than inherited.
    """
    kept = {
        name: os.environ[name]
        for name in ("PATH", "HOME", "SYSTEMROOT", "TEMP", "TMP")
        if name in os.environ
    }
    kept.update(NORMALISED_ENV)
    return kept


class LocalDispatcher:
    """Runs a tool on this machine. The stdlib default driver."""

    name = "local"

    def __init__(self, *, allow: Sequence[str] = (), root: str = ".") -> None:
        #: An allow-list, when a caller wants to constrain what may be dispatched.
        #: Empty means "any known tool" — the registry is already the constraint,
        #: since nothing outside it can be named.
        self.allow = frozenset(allow)
        self.root = root

    def dispatch(
        self,
        tool: Tool,
        arguments: Sequence[str] = (),
        *,
        cwd: str = "",
        stdin: str = "",
        timeout: float | None = None,
    ) -> Outcome:
        """Run it, or report honestly why it did not run."""
        if self.allow and tool.name not in self.allow:
            return Outcome(
                tool=tool.name, available=False,
                error=(
                    f"{tool.name} is not in this dispatcher's allow-list; "
                    f"permitted: {', '.join(sorted(self.allow))}"
                ),
            )

        found, version, resolved = probe(tool)
        if not found:
            return Outcome(
                tool=tool.name, available=False,
                error=(
                    f"{tool.binary} is not installed. {tool.fallback}"
                    if tool.fallback else f"{tool.binary} is not installed"
                ),
            )

        argv = (*resolved, *(str(item) for item in arguments))
        self._check(argv)

        started = time.monotonic()
        try:
            completed = subprocess.run(      # noqa: S603 - argv list, no shell
                list(argv),
                capture_output=True, text=True,
                cwd=cwd or self.root,
                input=stdin or None,
                timeout=timeout or tool.timeout,
                env=_environment(),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return Outcome(
                tool=tool.name, argv=argv, version=version, available=True,
                error=(
                    f"{tool.binary} did not finish within "
                    f"{timeout or tool.timeout}s and was stopped"
                ),
                duration=time.monotonic() - started,
            )
        except OSError as error:
            return Outcome(
                tool=tool.name, argv=argv, version=version, available=True,
                error=f"{tool.binary} could not be run: {error}",
                duration=time.monotonic() - started,
            )

        stdout = completed.stdout or ""
        truncated = len(stdout) > tool.max_output
        if truncated:
            # Truncation is reported rather than hidden: an answer built on a
            # clipped result is a different answer, and the caller has to know.
            stdout = stdout[:tool.max_output]

        return Outcome(
            tool=tool.name, argv=argv, stdout=stdout,
            stderr=(completed.stderr or "")[:8000],
            code=completed.returncode, version=version,
            duration=time.monotonic() - started, truncated=truncated,
        )

    def _check(self, argv: Sequence[str]) -> None:
        """Refuse an argv that only makes sense if a shell were involved.

        Nothing here interprets these characters, so their presence means the
        caller believed a shell was going to — and that belief, left uncorrected,
        is how a literal `;` in a filename becomes a command separator the day
        somebody adds `shell=True` for convenience.
        """
        for item in argv[1:]:
            if item.startswith("-"):
                continue
            if any(marker in item for marker in ("$(", "`", "&&", "||", ";")):
                raise DispatchError(
                    f"{item!r} contains shell syntax, and nothing in this path "
                    f"runs a shell. Pass the value plainly, or if you genuinely "
                    f"need a shell pipeline use the `sh` verb, which is guarded"
                )


def determinism_of(outcomes: Sequence[Outcome], tools: Mapping[str, Tool]) -> Determinism:
    """The weakest reproducibility guarantee among everything that was dispatched.

    A flow is exactly as reproducible as its least reproducible input, which is the
    same rule `ReasoningPath.confidence` applies to confidence — a chain is as
    strong as its weakest link, and averaging would let one volatile tool hide
    behind four deterministic ones.
    """
    weakest = Determinism.DETERMINISTIC
    order = {
        Determinism.DETERMINISTIC: 0,
        Determinism.ENVIRONMENTAL: 1,
        Determinism.VOLATILE: 2,
    }
    for outcome in outcomes:
        tool = tools.get(outcome.tool)
        if tool is None:
            continue
        if order[tool.determinism] > order[weakest]:
            weakest = tool.determinism
    return weakest
