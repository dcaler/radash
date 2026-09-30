"""Shared fixtures. Every test runs against an in-memory SQLite database and
touches no real source: raDash's whole point is that it reads the outside world
read-only, so the suite must never need the outside world to be present.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine


@pytest.fixture(autouse=True)
def _settings_are_not_shared_between_tests():
    """Settings overrides live in the process, not in the request.

    `app.settings` keeps the effective values in a module-level dict so every
    read is cheap, which is right for one container and wrong for a suite: a
    test that turns Offline on would leave it on for everything that ran
    afterwards, and the failure would land somewhere else entirely.
    """
    from app import settings

    before = dict(settings._overrides)
    yield
    settings._overrides.clear()
    settings._overrides.update(before)


@pytest.fixture
def engine():
    """In-memory engine shared across connections for the duration of a test."""
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    SQLModel.metadata.drop_all(eng)


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient whose database and output dir are per-test temporaries.

    Sources are pointed at non-existent paths by default so a test machine
    without the real mounts behaves like a freshly deployed container.
    """
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'radash.db'}")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("ZOTERO_SQLITE", str(tmp_path / "absent" / "zotero.sqlite"))
    monkeypatch.setenv("PROJECTS_DIR", str(tmp_path / "absent" / "projects"))
    monkeypatch.setenv("PROFESSIONAL_DIR", str(tmp_path / "absent" / "professional"))
    monkeypatch.setenv("IMPORT_DIR", str(tmp_path / "absent" / "import"))
    # Nothing listens here, so the network adapters take their unreachable path
    # without the suite depending on the tailnet being up.
    monkeypatch.setenv("TRUNDLR_URL", "http://127.0.0.1:1")
    # Emptied rather than pointed elsewhere: with no author ids the OpenAlex
    # adapter reports `missing` and never opens a socket, so the suite cannot
    # reach a live API even when the container's own environment is configured.
    monkeypatch.setenv("OPENALEX_AUTHOR_IDS", "")
    monkeypatch.setenv("S2_API_KEY", "")

    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def anyio_backend():
    """asyncio only. The scheduler is an asyncio task and trio is not
    installed; without this anyio parametrises every async test over both and
    errors on the one that cannot run."""
    return "asyncio"
