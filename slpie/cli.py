"""The primary front door.

`slpie` is a composition runner before it is anything else. That ordering is the
design: the first thing the tool does is accept a pipeline, because composition is
the philosophy rather than a feature bolted onto a set of subcommands.

Both pipe forms work:

    slpie 'discover . | link | findings --severity high'   # internal — primary
    slpie discover . | slpie link | slpie findings          # real OS pipes

The internal form is primary. It keeps full object fidelity, costs no
serialisation per stage, and works for every kind. The OS form is genuinely
useful — it composes with `jq`, `grep` and the rest of the shell — but a flow
crossing a process boundary must survive JSON, and `slpie/compose/wire.py` refuses
rather than degrading when it cannot. A silent degradation there would hand the
next verb half-built objects, which produces a plausible wrong answer instead of
an error.

Everything a human reads — `help`, `help <verb>`, `help compose`, `manual` — is
generated from the verb registry. There is no hand-written help text anywhere in
this file, which is what stops the CLI from documenting a flag that no longer
exists.

Exit codes, because a CLI that always returns 0 cannot be used in CI:
`0` success · `1` the composition failed at a stage · `2` invalid usage or an
invalid composition · `3` findings were raised at or above the requested severity.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .compose import (
    Composition,
    CompositionError,
    Context,
    Flow,
    Kind,
    ParseError,
    VerbError,
    registry,
)
from .compose.wire import ENVELOPE, WireError, decode, encode
from .errors import SlpieError
from .manual import compose_help, markdown, overview, verb_help

#: Flags the runner consumes itself. Everything else belongs to a verb, and the
#: separation is strict so that a verb parameter can never be shadowed by a
#: runner flag added later.
RUNNER_FLAGS = frozenset({
    "--explain", "--json", "--confirm", "--plan", "--dry-run", "--quiet",
    "--no-color", "--wire", "--root", "--actor", "--fail-on",
})

OK = 0
FAILED = 1
USAGE = 2
FINDINGS = 3


class Cli:
    """Holds the streams so the whole surface is testable without a subprocess."""

    def __init__(
        self,
        *,
        stdout: Any = None,
        stderr: Any = None,
        stdin: Any = None,
        isatty: bool | None = None,
    ) -> None:
        self.stdout = stdout if stdout is not None else sys.stdout
        self.stderr = stderr if stderr is not None else sys.stderr
        self.stdin = stdin if stdin is not None else sys.stdin
        self._isatty = isatty
        self.verbs = registry()

    # -- entry point -----------------------------------------------------

    def main(self, argv: Sequence[str]) -> int:
        arguments = list(argv)
        if not arguments:
            self._out(overview(verbs=self.verbs))
            return OK

        head = arguments[0]

        if head in ("help", "--help", "-h"):
            return self._help(arguments[1:])
        if head in ("version", "--version"):
            return self._version()
        if head == "manual":
            self._out(markdown(verbs=self.verbs))
            return OK
        if head == "verbs":
            self._out(json.dumps(self.verbs.to_dict(), indent=2))
            return OK
        if head == "contract":
            return self._contract(arguments[1:])
        if head == "demo":
            from .demo import Demo

            return Demo(stdout=self.stdout).run()
        if head == "plan":
            return self._plan(arguments[1:])
        if head == "routine":
            return self._run([_routine_command(arguments[1:])])

        resolved = self._routine_key(head)
        if resolved is not None:
            return self._run([resolved, *arguments[1:]])

        return self._run(arguments)

    def _routine_key(self, head: str) -> str | None:
        """A claimed key to its composition.

        Checked only when the token is not a verb and not a pipeline, so a key can
        never shadow a capability — `Routines.claim` refuses a key that collides
        with a verb, and this is the second half of that guarantee.
        """
        if "|" in head or " " in head or head in self.verbs:
            return None
        try:
            from .suggest import Routines

            routines = Routines(Path(".slpie") / "routines.json", verbs=self.verbs)
            pipeline = routines.resolve(head)
        except Exception:  # noqa: BLE001 - no routine by that name is not an error
            return None
        routines.used(head)
        return pipeline

    # -- help ------------------------------------------------------------

    def _help(self, arguments: Sequence[str]) -> int:
        if not arguments:
            self._out(overview(verbs=self.verbs))
            return OK
        topic = arguments[0]
        if topic in ("compose", "composition", "pipes", "recipes"):
            self._out(compose_help(verbs=self.verbs))
            return OK
        try:
            self._out(verb_help(topic, verbs=self.verbs))
        except VerbError as error:
            self._err(str(error))
            return USAGE
        return OK

    def _version(self) -> int:
        try:
            from importlib.metadata import version

            self._out(f"slpie {version('gratimos')}")
        except Exception:  # noqa: BLE001 - not installed as a distribution
            self._out("slpie (from source)")
        return OK

    def _contract(self, arguments: Sequence[str]) -> int:
        """`slpie contract --openapi | --typescript` — the generated contract.

        Emitted rather than maintained, so the stdlib server, the FastAPI adapter
        and every client build from one source and cannot drift.
        """
        from .ui.contract import openapi, typescript

        if "--typescript" in arguments or "--ts" in arguments:
            self._out(typescript(verbs=self.verbs))
            return OK

        try:
            from .ui.api import Api

            routes = Api(engine=None).routes
        except Exception:  # noqa: BLE001 - the document is still useful without them
            routes = ()
        self._out(json.dumps(
            openapi(verbs=self.verbs, routes=routes), indent=2,
        ))
        return OK

    # -- planning --------------------------------------------------------

    def _plan(self, arguments: Sequence[str]) -> int:
        """`slpie plan "<question>"` — write the composition, then show it."""
        question = " ".join(item for item in arguments if not item.startswith("--"))
        if not question:
            self._err('plan needs a question: slpie plan "what breaks if lodash 5 lands?"')
            return USAGE

        try:
            from .planner import plan_for
        except ImportError:
            self._err("the planner is not available in this build")
            return USAGE

        planned = plan_for(question, verbs=self.verbs)
        self._out(planned.render())

        if "--run" in arguments:
            if not planned.pipeline:
                self._err("nothing to run: the planner produced no composition")
                return USAGE
            return self._run([planned.pipeline, *[
                flag for flag in arguments if flag in RUNNER_FLAGS
            ]])
        return OK

    # -- running ---------------------------------------------------------

    def _run(self, arguments: Sequence[str]) -> int:
        options, rest = self._split(arguments)
        if not rest:
            self._err("nothing to run. `slpie help` lists every verb")
            return USAGE

        text = " ".join(rest) if len(rest) > 1 or "|" in rest[0] else rest[0]

        try:
            composition = Composition.read(text, verbs=self.verbs)
        except ParseError as error:
            self._err(str(error))
            return USAGE

        # stdin is read *before* validating, because what is on the pipe is part
        # of the composition's type context. Validating first rejected
        # `slpie discover . | slpie link` for starting with a transform verb,
        # when its input was sitting right there on the pipe.
        try:
            upstream = self._upstream(composition)
        except WireError as error:
            self._err(str(error))
            return USAGE

        validation = composition.validate(upstream.kind)
        if not validation.ok:
            self._err(validation.explain())
            return USAGE

        if options.get("plan") or options.get("dry_run"):
            self._out(composition.explain(upstream.kind))
            return OK

        context = Context(
            actor=str(options.get("actor") or "cli"),
            confirmed=bool(options.get("confirm")),
            root=str(options.get("root") or "."),
            engine=self._engine(composition),
        )

        try:
            result = composition.run_from(upstream, context)
        except CompositionError as error:
            self._err(str(error))
            return USAGE
        except SlpieError as error:
            self._err(str(error))
            return FAILED

        return self._render(result, options)

    def _upstream(self, composition: Composition) -> Flow:
        """Read a flow from stdin, but only when a stage could consume one.

        Gated on the first verb rather than on `isatty`, and that is the fix for a
        real hang: `slpie discover . --wire` in a context where stdin is not a
        terminal would block reading input that was never going to arrive. A source
        verb consumes nothing, so reading stdin for it is not merely wasteful — it
        is a deadlock waiting for a writer that does not exist.
        """
        first = self.verbs.get(composition.stages[0].verb) if composition.stages else None
        if first is None or first.is_source:
            return Flow.start()
        if self._interactive():
            return Flow.start()
        try:
            raw = self.stdin.read()
        except Exception:  # noqa: BLE001 - a closed stdin is not an error
            return Flow.start()
        if not (raw or "").strip():
            return Flow.start()
        return decode(raw)

    def _engine(self, composition: Composition) -> Any:
        """Open an environment only if a stage in this composition needs one.

        Opening it unconditionally would make `slpie discover .` fail in a
        directory with no manifest — which is exactly the case the offline verbs
        exist to serve.
        """
        needs = any(
            self.verbs.require(stage.verb).group == "environment"
            for stage in composition
        )
        if not needs:
            return None
        try:
            from .engine import Engine

            for candidate in ("slpie.environment.yaml", "slpie.environment.yml"):
                path = Path(candidate)
                if path.exists():
                    return Engine.from_manifest(str(path))
        except Exception as error:  # noqa: BLE001 - report, do not crash
            self._err(f"could not open the environment: {error}")
        return None

    # -- output ----------------------------------------------------------

    def _render(self, result: Any, options: dict[str, Any]) -> int:
        if options.get("wire"):
            if not result.ok:
                self._err(str(result))
                return FAILED
            try:
                self._out(encode(result.flow))
            except WireError as error:
                self._err(str(error))
                return USAGE
            return OK

        if options.get("json"):
            self._out(json.dumps(result.to_dict(), indent=2, default=str))
            return OK if result.ok else FAILED

        if not result.ok:
            self._err(str(result))
            if result.partial and not options.get("quiet"):
                self._err(f"  partial result: {result.flow}")
            return FAILED

        flow = result.flow
        if not options.get("quiet"):
            self._out(self._body(flow))

        # Guidance is printed after the answer, never instead of it. A suggestion
        # that displaced the result would make `| suggest` unsafe to append to a
        # command somebody depends on.
        for name in ("guidance", "routines"):
            if name in flow.facts and not options.get("quiet"):
                self._out("")
                self._out(str(flow.facts[name]))

        if "explanation" in flow.facts and options.get("explain") is not False:
            self._out(flow.facts["explanation"])
        elif options.get("explain"):
            from .compose.verbs.shaping import verbs as shaping_verbs

            explainer = next(v for v in shaping_verbs() if v.name == "explain")
            self._out(explainer.run(flow, {"remediation": True}, Context())
                      .facts["explanation"])

        return self._exit_code(flow, options)

    def _body(self, flow: Flow) -> str:
        """The value, rendered for a terminal. One line per item.

        The `json` fact is only used when `json` was the *last* stage. Preferring
        it whenever present meant a later stage's real output was hidden behind an
        earlier stage's serialisation — `... | json | tool --name jq` printed the
        document it had piped into jq rather than what jq returned.
        """
        if flow.stages and flow.stages[-1] == "json" and "json" in flow.facts:
            return str(flow.facts["json"])

        lines = [f"{flow}"]
        if flow.empty:
            lines.append("  (nothing)")
            return "\n".join(lines)

        for item in flow.items[:200]:
            lines.append(f"  {_line(item)}")
        if flow.size > 200:
            lines.append(f"  ... and {flow.size - 200} more")
        return "\n".join(lines)

    def _exit_code(self, flow: Flow, options: dict[str, Any]) -> int:
        """`--fail-on high` is what makes this usable as a CI gate."""
        threshold = str(options.get("fail_on") or "").lower()
        if not threshold or flow.kind is not Kind.FINDINGS:
            return OK

        from .domain.lifecycle import Severity

        try:
            floor = Severity(threshold).rank
        except ValueError:
            self._err(
                f"--fail-on {threshold!r} is not a severity; use one of "
                f"{', '.join(item.value for item in Severity)}"
            )
            return USAGE

        breached = [
            item for item in flow.items
            if getattr(getattr(item, "severity", None), "rank", -1) >= floor
        ]
        if breached:
            self._err(
                f"{len(breached)} finding(s) at or above {threshold}"
            )
            return FINDINGS
        return OK

    # -- plumbing --------------------------------------------------------

    def _split(self, arguments: Sequence[str]) -> tuple[dict[str, Any], list[str]]:
        """Runner flags out, everything else through to the composition."""
        options: dict[str, Any] = {}
        rest: list[str] = []
        index = 0
        while index < len(arguments):
            token = arguments[index]
            if token in RUNNER_FLAGS:
                name = token.lstrip("-").replace("-", "_")
                if token in ("--root", "--actor", "--fail-on") and index + 1 < len(arguments):
                    options[name] = arguments[index + 1]
                    index += 1
                else:
                    options[name] = True
            else:
                rest.append(token)
            index += 1
        return options, rest

    def _interactive(self) -> bool:
        if self._isatty is not None:
            return self._isatty
        try:
            return bool(self.stdin.isatty())
        except Exception:  # noqa: BLE001 - a StringIO has no isatty
            return True

    def _out(self, text: str) -> None:
        self.stdout.write(text.rstrip("\n") + "\n")

    def _err(self, text: str) -> None:
        self.stderr.write(text.rstrip("\n") + "\n")


def _line(item: Any) -> str:
    """One item, on one line, preferring the object's own rendering."""
    text = str(item)
    if "\n" in text:
        text = text.splitlines()[0] + " ..."
    return text[:200]


def main(argv: Sequence[str] | None = None) -> int:
    return Cli().main(list(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())


def _routine_command(arguments: Sequence[str]) -> str:
    """`routine claim release-check '<pipeline>'` → one properly quoted stage.

    A subcommand shape for one verb, because `routine --action claim --name x
    --pipeline y` is what the composition grammar produces and not what anybody
    would type. The verb keeps the general form; this is sugar over it.

    The quoting is load-bearing rather than cosmetic: a routine's pipeline contains
    `|`, and handing it back unquoted meant the runner re-split it into stages and
    tried to run `link` as the second stage of `routine`.
    """
    import shlex

    if not arguments:
        return "routine"

    action, *rest = arguments
    if action not in ("list", "claim", "forget"):
        return " ".join(shlex.quote(item) for item in ["routine", *arguments])

    parts = ["routine", "--action", action]
    positional = [item for item in rest if not item.startswith("--")]
    passthrough = [item for item in rest if item.startswith("--")]

    if positional:
        parts += ["--name", positional[0]]
    if len(positional) > 1:
        parts += ["--pipeline", positional[1]]
    parts += passthrough

    return " ".join(
        item if item.startswith("--") or item == "routine" else shlex.quote(item)
        for item in parts
    )
