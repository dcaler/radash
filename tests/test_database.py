from datetime import datetime, timezone

from sqlmodel import select

from app.database import apply_migrations, create_db_and_tables, get_engine
from app.models import SourceState, SourceStatus, Setting


def test_tables_are_created(engine):
    from sqlalchemy import inspect
    names = set(inspect(engine).get_table_names())
    assert {"sourcestate", "setting"} <= names


def test_apply_migrations_is_idempotent(tmp_path):
    eng = get_engine(f"sqlite:///{tmp_path / 'm.db'}")
    create_db_and_tables(eng)
    apply_migrations(eng)
    apply_migrations(eng)  # running twice must be harmless


def test_source_state_defaults_to_missing(session):
    session.add(SourceState(key="zotero"))
    session.commit()
    got = session.exec(select(SourceState)).one()
    assert got.status is SourceStatus.missing
    assert got.last_read_at is None


def test_source_state_records_counts_and_notes(session):
    session.add(SourceState(
        key="trundlr",
        status=SourceStatus.degraded,
        last_read_at=datetime.now(timezone.utc),
        item_count=27,
        note="2 projects had unparsable haarpi.yaml",
    ))
    session.commit()
    got = session.exec(select(SourceState).where(SourceState.key == "trundlr")).one()
    assert got.item_count == 27
    assert "unparsable" in got.note


def test_setting_roundtrip(session):
    session.add(Setting(key="last_full_rebuild", value="2026-09-19T14:22:00Z"))
    session.commit()
    got = session.exec(select(Setting)).one()
    assert got.value.startswith("2026-09-19")


def test_migrations_add_columns_to_an_existing_table(tmp_path):
    """The deploy failure this exists to prevent.

    raDash's database lives in a named volume that outlives the image, so a
    milestone that adds a column meets a table created by an older build.
    `create_all` will not touch it, and every query selecting the new column
    fails with "no such column".
    """
    from sqlalchemy import inspect, text
    from app.database import apply_migrations, get_engine

    db = tmp_path / "old.db"
    engine = get_engine(f"sqlite:///{db}")
    # A `work` table as an earlier milestone created it: no category columns.
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE work (
                id INTEGER PRIMARY KEY, snapshot_id INTEGER NOT NULL,
                fingerprint VARCHAR NOT NULL, title VARCHAR NOT NULL,
                on_cv BOOLEAN NOT NULL, confirmed BOOLEAN NOT NULL)"""))
        conn.execute(text(
            "INSERT INTO work (snapshot_id, fingerprint, title, on_cv, confirmed) "
            "VALUES (1, 'doi:10.1/x', 'An older row', 0, 0)"))

    added = apply_migrations(engine)
    assert any(a.startswith("work.category") for a in added)

    columns = {c["name"] for c in inspect(engine).get_columns("work")}
    assert {"category", "category_source", "on_site"} <= columns

    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT title, category, on_site FROM work")).one()
    assert row.title == "An older row", "existing rows are preserved"
    assert row.category == "other", "and backfilled with the model default"


def test_migrations_are_idempotent(tmp_path):
    from app.database import apply_migrations, create_db_and_tables, get_engine

    engine = get_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    create_db_and_tables(engine)
    assert apply_migrations(engine) == [], "a current schema needs no changes"
    assert apply_migrations(engine) == []


def test_migrations_leave_a_missing_table_to_create_all(tmp_path):
    """A table that does not exist is not migrated into being piecemeal."""
    from app.database import apply_migrations, create_db_and_tables, get_engine
    from sqlalchemy import inspect

    engine = get_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    assert apply_migrations(engine) == []
    create_db_and_tables(engine)
    assert "work" in set(inspect(engine).get_table_names())
