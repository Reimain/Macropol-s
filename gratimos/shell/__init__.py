"""Shell intelligence: predict the task, read the command, refuse the mistake.

The layer between "what does this context need?" and anything executing. It
exists because a generated shell command is the one artifact where being
plausible and being right are almost unrelated — `rm -rf $BUILD/` reads fine and
takes the machine with it when `$BUILD` is empty.

```
context + environment  ─→  candidate tasks
                              │
                    command  ─→  analyse()  ─→ findings, risk
                              │
                           Guard  ─→ ALLOW · CONFIRM · REFUSE
                              │
                    outcome ─→  Ladder ─→ SUGGESTED → CONFIRMED
                                          → TRUSTED → AUTOMATED (one click)
```

Four rules the layer is built on:

* **Nothing here executes.** `analyse` parses, `Guard` decides, `Ladder` records.
  Execution belongs to the caller, who must hold a permitting verdict first.
* **The environment is read, never assumed.** A task needing a binary the
  machine lacks is refused with the install command, not proposed hopefully.
* **Unattended is not approved.** A `CONFIRM` where nobody can be asked — CI, an
  autonomous agent — resolves to `REFUSE`, in the guard rather than in each
  caller's memory.
* **Risk caps automation and evidence cannot lift it.** A catastrophic command
  never reaches one-click, however many times it has worked; "it worked forty
  times" is the argument that precedes the forty-first.

The AI backends — Claude, OpenAI, Gemini CLI — attach through
`gratimos.runtime.Host` as extensions at the `shell-planner` point, so the
kernel never imports a vendor SDK and a deployment chooses its model in a config
file. The knowledge base in `gratimos.distill` supplies the routing: a need
already answered is recalled rather than re-asked, and the ladder turns repeated
success into automation.
"""

from __future__ import annotations

from .automation import (
    AUTOMATABLE,
    PROMOTION_RUNS,
    Ladder,
    Outcome,
    Routine,
    Transition,
    Trust,
)
from .command import (
    BUILTINS,
    Analysis,
    Category,
    Command,
    Finding,
    Risk,
    Segment,
    ShellError,
    analyse,
    parse,
)
from .environment import (
    PACKAGE_MANAGERS,
    PROBED,
    Environment,
    Platform,
    Shell,
    probe,
)
from .guard import DEVELOPMENT, PRODUCTION, Guard, Policy, Stance, Verdict, summarise

__all__ = [
    "Environment", "Platform", "Shell", "probe", "PROBED", "PACKAGE_MANAGERS",
    "Command", "Segment", "Analysis", "Finding", "Risk", "Category",
    "parse", "analyse", "ShellError", "BUILTINS",
    "Guard", "Policy", "Verdict", "Stance", "summarise", "DEVELOPMENT", "PRODUCTION",
    "Ladder", "Routine", "Trust", "Outcome", "Transition",
    "PROMOTION_RUNS", "AUTOMATABLE",
]
