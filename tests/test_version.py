from app.main import _compute_version


def test_version_is_stable_across_calls():
    assert _compute_version() == _compute_version()


def test_version_is_a_short_hash():
    v = _compute_version()
    assert len(v) == 7
    assert all(c in "0123456789abcdef" for c in v)


def test_version_changes_when_source_changes(tmp_path, monkeypatch):
    """The hash must track app/ contents, or the ?v= cache-bust key is a lie."""
    import app.main as main

    before = main._compute_version()
    scratch = main._APP_DIR / "_version_probe.py"
    scratch.write_text("# temporary file written by test_version\n")
    try:
        assert main._compute_version() != before
    finally:
        scratch.unlink()
    assert main._compute_version() == before
