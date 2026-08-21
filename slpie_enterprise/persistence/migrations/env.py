"""Alembic's entry point, kept to the minimum that actually does something.

The schema is `../schema.sql`, and that is deliberate: it is the one statement
of what the tables are, mirrored column for column against ring 0's and checked
by `test_the_two_schemas_declare_the_same_columns`. Alembic's job here is
*versioning* — knowing which revision a database is at — not owning the DDL.

No `target_metadata`, therefore no autogenerate. Autogenerate compares a
database against SQLAlchemy models this project does not have, and adding
models purely to satisfy it would create a third statement of the schema for
the two that exist to drift from.
"""

from __future__ import annotations

from alembic import context

from slpie_enterprise.persistence.engine import configured


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """A SQLAlchemy connection, because Alembic reads `connection.dialect`.

    This is the whole reason SQLAlchemy is in the `enterprise` extra, and it
    touches nothing else: the stores use psycopg directly, and the traversal —
    the most performance-sensitive and most carefully reasoned SQL in the
    platform — is never rewritten into an expression language. Handing Alembic
    a raw psycopg connection fails with `'Connection' object has no attribute
    'dialect'`, which is Alembic saying it wants the thing it was built for.
    """
    from sqlalchemy import create_engine

    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


def _url() -> str:
    """The connection string, as SQLAlchemy spells it.

    psycopg's own URL scheme is `postgresql://`; SQLAlchemy wants
    `postgresql+psycopg://` to pick the v3 driver rather than psycopg2, which
    is not installed. One place converts, so nobody has to remember.
    """
    found = configured()
    if found.startswith("postgresql://"):
        return found.replace("postgresql://", "postgresql+psycopg://", 1)
    return found


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
