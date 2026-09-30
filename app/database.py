"""Engine, session factory and the FastAPI session dependency.

Mirrors trundlr's shape so the two are navigable side by side: get_engine /
create_db_and_tables / apply_migrations / init_engine / get_db.
"""
from typing import Generator

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from app import models  # noqa: F401  — import registers tables on SQLModel.metadata

_engine = None


def get_engine(database_url: str = "sqlite:///radash.db"):
    """Create and configure the SQLAlchemy engine.

    For SQLite, enables foreign key constraint enforcement via PRAGMA.
    """
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args)

    if database_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_db_and_tables(engine):
    """Create all tables in the database."""
    SQLModel.metadata.create_all(engine)


def apply_migrations(engine):
    """Add columns that exist on the models and not yet in the database.

    `create_all` creates missing *tables* and never touches an existing one,
    so a deployed database keeps whatever shape it was created with. raDash's
    data lives in a named volume that outlives every image, which means a
    milestone adding a column to an existing table breaks the deploy on the
    next start: the table is there, the column is not, and every query
    selecting it fails. That is not hypothetical — M2 added `work.category`
    and `snapshot.drift`, and the deployed instance returned 500 until this
    function stopped being a stub.

    Additive only, and derived from the models rather than hand-listed, so a
    future column needs no migration written for it. New columns are added
    nullable and then backfilled with the model's default, because SQLite
    cannot add a NOT NULL column without one. A destructive change — a dropped
    column, a changed type — is deliberately not handled here and gets a table
    recreation written by hand.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    added = []

    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if table.name not in existing:
                continue          # create_all will make it, whole and correct
            have = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in have:
                    continue
                ddl_type = column.type.compile(engine.dialect)
                conn.execute(text(
                    f'ALTER TABLE "{table.name}" '
                    f'ADD COLUMN "{column.name}" {ddl_type}'))
                default = _scalar_default(column)
                if default is not None:
                    conn.execute(
                        text(f'UPDATE "{table.name}" SET "{column.name}" = :v '
                             f'WHERE "{column.name}" IS NULL'), {"v": default})
                added.append(f"{table.name}.{column.name}")
    return added


def _scalar_default(column):
    """The model's default, when it is a plain value we can backfill with."""
    default = getattr(column, "default", None)
    if default is None:
        return None
    arg = getattr(default, "arg", None)
    if arg is None or callable(arg):
        return None            # a factory (timestamps); leave it NULL
    if isinstance(arg, bool):
        return int(arg)        # SQLite stores booleans as integers
    if isinstance(arg, (str, int, float)):
        return arg
    value = getattr(arg, "value", None)   # an Enum default
    return value if isinstance(value, (str, int, float)) else None


def get_session(engine) -> Generator[Session, None, None]:
    """Yield a session bound to an explicit engine (used by tests)."""
    with Session(engine) as session:
        yield session


def init_engine(database_url: str = "sqlite:///radash.db"):
    """Initialize the module-level engine used by get_db. Called once on startup."""
    global _engine
    _engine = get_engine(database_url)
    return _engine


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency for route handlers; uses the module-level engine."""
    with Session(_engine) as session:
        yield session
