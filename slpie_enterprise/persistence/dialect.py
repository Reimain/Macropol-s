"""The four ways SQLite and Postgres disagree, named and translated.

Phase 15 is an implementation of a published protocol, not a redesign, and this
module is the reason it stays that size. Postgres has recursive CTEs, so the
four walks in :mod:`slpie.graph.sqlite_graph` port structurally; what does not
port is four pieces of dialect and one concurrency primitive. The fourth —
`anchors` — was not in the plan and was found by running the query: SQLite is
untyped and Postgres types a recursive CTE from its seed row, so a bare `1.0`
there is `numeric` against a `double precision` walk and the whole query is
refused.

Substitution rather than an ORM, deliberately. Three differences do not justify
putting SQLAlchemy Core in the traversal path — the queries are the platform's
most performance-sensitive code and the most carefully reasoned, and rewriting
them into an expression language would obscure both. SQLAlchemy stays for
Alembic's benefit and touches nothing here.

**The trap this module exists to make impossible.** SQLite's ``MIN`` is two
things wearing one name: a scalar that takes the smaller of two arguments, and
an ordinary aggregate. Only the *scalar* becomes ``LEAST``. Translating the
aggregate as well produces SQL Postgres accepts and answers differently — a
blast radius that is silently wrong rather than an error — so the two are
matched separately and each has its own test.
"""

from __future__ import annotations

import re

#: `instr(haystack, needle) = 0` is SQLite's "does not contain". Postgres spells
#: the same test `position(needle in haystack) = 0`, and the argument order is
#: reversed, which is the part that is easy to get backwards and produces a
#: cycle guard that never fires.
_INSTR = re.compile(r"\binstr\(\s*([^,]+?)\s*,\s*(.+?)\s*\)(?=\s*=)", re.DOTALL)

#: The *scalar* two-argument MIN. Anchored on a comma inside the parentheses and
#: on the argument shape, so `MIN(depth)` — the aggregate — cannot match.
_SCALAR_MIN = re.compile(r"\bMIN\(\s*([^(),]+?)\s*,\s*([^(),]+?)\s*\)")

#: SQLite named parameters. psycopg wants `%(name)s`.
_PARAM = re.compile(r"(?<![:\w]):([a-z_][a-z0-9_]*)", re.IGNORECASE)


def contains(sql: str) -> str:
    """`instr(a, b) = 0` → `position(b in a) = 0`, arguments swapped."""
    return _INSTR.sub(lambda found: f"position({found.group(2)} in {found.group(1)})", sql)


def least(sql: str) -> str:
    """The two-argument scalar `MIN` only. The aggregate is left alone."""
    return _SCALAR_MIN.sub(lambda found: f"LEAST({found.group(1)}, {found.group(2)})", sql)


def parameters(sql: str) -> str:
    """`:name` → `%(name)s`, which is the placeholder psycopg binds."""
    return _PARAM.sub(lambda found: f"%({found.group(1)})s", sql)


#: A bare float in a recursive CTE's seed row. See `anchors`.
_SEED_FLOAT = re.compile(r",(\s*)1\.0\b")


def anchors(sql: str) -> str:
    """Cast the confidence seed in a recursive CTE's anchor row.

    Found by running the real thing, and it is the difference nobody plans for.
    SQLite is untyped, so `SELECT :root, 0, '…', 1.0` seeds a walk whose
    recursive term produces a float and nothing complains. Postgres types the
    whole CTE from its anchor, sees `numeric` there and `double precision`
    coming back from `LEAST(min_conf, e.confidence)`, and refuses the query:

        recursive query "reach" column 4 has type numeric in the non-recursive
        term but type double precision overall

    A loud failure rather than a wrong answer, which is the good kind — but only
    because it is executed. It is here rather than inline in the SQL because
    ring 0's statement of what a blast radius *is* should not carry a cast that
    exists for one engine's type inference.
    """
    return _SEED_FLOAT.sub(r",\g<1>1.0::double precision", sql)


def booleans(sql: str) -> str:
    """`propagates = 1` → `propagates = TRUE`.

    SQLite has no boolean type and stores the flag as an integer. Postgres has
    one and refuses the comparison, so the column is a real `BOOLEAN` there and
    the predicate has to follow it.
    """
    return re.sub(r"\b(propagates)\s*=\s*1\b", r"\1 = TRUE", sql)


def translate(sql: str) -> str:
    """Every substitution, in the order they compose.

    Parameters last: the earlier rules rewrite fragments that contain `:root`
    and friends, and rewriting the placeholders first would leave `%(root)s`
    inside a pattern the others no longer match.
    """
    return parameters(booleans(anchors(least(contains(sql)))))


#: What `BEGIN IMMEDIATE` buys in SQLite: the write lock taken *before* the tail
#: is read, so two writers cannot both see the same last sequence and both claim
#: the next one. Postgres has no such statement; the equivalent is to lock the
#: row the sequence is derived from.
#:
#: `FOR UPDATE` on the tail row serialises exactly the writers that would
#: collide and nothing else, which is narrower than SQLite's whole-database
#: lock and therefore strictly better under load. The hash chain is unchanged:
#: it never depended on *how* the lock was taken, only that the read of the tail
#: and the write of the successor were atomic together.
LOCK_TAIL_SQL = """
SELECT sequence, hash FROM ledger ORDER BY sequence DESC LIMIT 1 FOR UPDATE
"""

#: `FOR UPDATE` locks a row that exists. The first append to an empty ledger has
#: no tail to lock, and two processes racing to write sequence 1 would both find
#: nothing and both claim it. An advisory lock covers exactly that window: it is
#: taken on a constant, needs no row, and is released with the transaction.
LOCK_EMPTY_SQL = "SELECT pg_advisory_xact_lock(%(key)s)"

#: The constant the advisory lock is taken on. Arbitrary, and fixed — two
#: processes must choose the same one or the lock protects nothing.
LEDGER_LOCK = 0x51D1E_1ED

