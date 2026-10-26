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


def test_readme_names_the_current_version():
    """The README carries the version so the web interface can be checked
    against GitHub at a glance. A line that has fallen behind is worse than
    none, because it would be believed."""
    import re

    import pytest

    import app.main as main

    path = main._APP_DIR.parent / "README.md"
    if not path.exists():
        pytest.skip("README.md is not in this tree (the image copies app/ only)")
    readme = path.read_text()
    stated = re.search(r"\*\*Version `([0-9a-f]{7})`", readme)
    assert stated, "README.md has no **Version `xxxxxxx`** line"
    assert stated.group(1) == main._compute_version(), (
        f"README.md says {stated.group(1)}, the code is "
        f"{main._compute_version()} — update the Version line")
