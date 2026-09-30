"""The M1 exit criterion: no source handle this package opens is writable.

M0 proved the *mounts* are read-only, which is Docker's guarantee. This proves
the adapters do not rely on that guarantee — the Zotero connection refuses
writes on its own, and the trundlr client cannot issue anything but GET. Both
properties hold on a developer machine with no `:ro` mount in sight, which is
where a regression would otherwise go unnoticed until deploy.
"""
import sqlite3

import pytest

from app.config import Config
from app.sources import haarpi, openalex, s2, scholar, trundlr, zotero
from tests.zotero_fixture import build


def test_zotero_connection_refuses_writes(tmp_path):
    db = build(tmp_path / "zotero.sqlite")
    con = zotero._connect(db)
    try:
        with pytest.raises(sqlite3.OperationalError) as exc:
            con.execute("UPDATE items SET key='HACKED' WHERE itemID=1")
        assert "readonly" in str(exc.value).lower()
    finally:
        con.close()


def test_zotero_connection_takes_no_locks(tmp_path):
    """`immutable=1` is what makes reading a live library safe for the client."""
    db = build(tmp_path / "zotero.sqlite")
    con = zotero._connect(db)
    try:
        con.execute("SELECT count(*) FROM items").fetchone()
        # A second reader can open and write the same file: our handle is not
        # holding a lock on it.
        other = sqlite3.connect(db)
        other.execute("UPDATE items SET dateModified='2025-01-01' WHERE itemID=1")
        other.commit()
        other.close()
    finally:
        con.close()


def test_trundlr_client_is_get_only():
    assert trundlr.ALLOWED_METHODS == frozenset({"GET"})


def test_no_adapter_opens_a_source_for_writing():
    """Read the adapter sources and assert no write-mode file access appears.

    Crude on purpose: it catches the realistic regression, which is a later
    milestone adding a convenience write beside a source it was reading.
    """
    import inspect
    forbidden = ('open(', '.write_text(', '.write_bytes(', '.mkdir(', '.unlink(',
                 '.rename(', 'shutil.', 'os.remove', 'os.makedirs')
    for module in (zotero, haarpi, scholar, trundlr, openalex, s2):
        src = inspect.getsource(module)
        for token in forbidden:
            assert token not in src, f"{module.__name__} contains {token!r}"


def test_read_only_sources_are_never_asked_for_a_writable_path(tmp_path, monkeypatch):
    """Every adapter runs against absent sources without creating anything."""
    root = tmp_path / "absent"
    monkeypatch.setenv("ZOTERO_SQLITE", str(root / "zotero.sqlite"))
    monkeypatch.setenv("PROJECTS_DIR", str(root / "projects"))
    monkeypatch.setenv("IMPORT_DIR", str(root / "import"))
    cfg = Config()

    zotero.read(cfg)
    haarpi.read(cfg)
    scholar.read(cfg)

    assert not root.exists(), "reading an absent source must not create it"
