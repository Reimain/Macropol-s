# Transformations

Drop `.py` files here (or in a workspace's `transformations/` directory) to
reshape data as the kernel absorbs it. Each file is statically checked before it
is ever executed, then run in a separate process under OS resource limits,
exchanging JSON only.

## The contract

```python
NAME = "normalize_orders"      # optional; defaults to the filename
APPLIES_TO = "orders*"         # glob against the entity name
DESCRIPTION = "Uppercase customer names and derive a margin"
VERSION = "1"

def transform(records, context):
    """Return the transformed records.

    records: list of dicts, one per row of the incoming payload.
    context: entity name, the observed shape, the source, and `log(*parts)`.
    """
    context["log"]("saw", len(records), "rows of", context["entity"])
    return [dict(row, customer=row["customer"].upper()) for row in records]
```

Return a list of dicts. Shapes are **re-inferred** from what you return, so it
is fine to add, drop, or retype fields — the generated model follows on the next
cycle.

Scaffold one with:

```bash
gratimos transforms new ./workspace normalize_orders --applies-to 'orders*'
```

## What you can use

The standard library's data modules: `datetime`, `decimal`, `json`, `re`,
`math`, `statistics`, `itertools`, `functools`, `collections`, `hashlib`,
`uuid`, `csv`, `io`, `textwrap`, `zoneinfo`, and friends.

## What you cannot

`os`, `sys`, `socket`, `subprocess`, `urllib`, `pathlib`, `importlib`,
`pickle`, `ctypes`, `inspect`, `threading` — and `eval`, `exec`, `open`,
`compile`, `globals`, `__import__`, plus dunder attribute access such as
`__class__` or `__subclasses__`.

Rejections are reported, not silent:

```bash
$ gratimos transforms list ./workspace
ok        normalize_orders     applies_to='orders*'
REJECTED  fetch_extra          import of 'urllib' is not permitted at line 3 [Import]
```

To widen the allow-list deliberately:

```python
from gratimos.transforms import DEFAULT_POLICY
policy = DEFAULT_POLICY.with_imports("numpy", "pandas")
```

Some imports can never be allowed; `with_imports` raises rather than granting
them.

## Limits

Wall clock, CPU seconds, address space, and maximum file size are all enforced
by the OS in a child process — a busy loop is killed by `RLIMIT_CPU`, not by
cooperation. Defaults: 30s wall, 20s CPU, 512 MiB, no file writes.

## What this is not

A security boundary against a determined author. It is a blast-radius limiter
for code an operator deliberately placed here: it catches accidents and bounds
the damage when a transformation misbehaves. For genuinely untrusted code, run
it in a container or VM and point the kernel at that.

## Ordering

Transformations matching an entity run in name order, each seeing the previous
one's output — so `10_clean.py` before `20_enrich.py` is a working convention.
A failure stops that entity's chain and is reported; other entities continue.
