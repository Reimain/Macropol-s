# Macropol-s

We build for leaders

---

## Gratimos

This repository also hosts **Gratimos**, a self-building agent kernel. Point it
at an environment and it reads what is there, infers the shape of it, generates
code against those shapes, and records every step on a versioned spine that can
be rolled back.

```bash
pip install -e .
gratimos govern ./data --depth govern
```

```python
from gratimos import Depth, govern

report = govern("./data", depth=Depth.GENERATE)
print(report.summary())
```

It reads JSON, CSV, XLSX, SQLite, APIs, images, video, and shell scripts;
generates dataclass modules and protobuf schemas that merge at the AST level
instead of overwriting your edits; runs operator transformations in a gated,
resource-limited sandbox; keeps schema evolution in a reversible Alembic-shaped
ledger; and talks to other agents over A2A — including UiPath processes and
Claude.

- **[docs/README.md](docs/README.md)** — usage, CLI, and the safety guarantees
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — why the boundaries sit where they do

The kernel has no third-party dependencies; optional extras widen format and
storage coverage without ever being required to start.
