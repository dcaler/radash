"""The read-only boundary.

raDash's defining constraint is that it writes to nothing it observes. Docker's
`:ro` flags provide that; these tests check the property holds and that the app
reports honestly when it does not.
"""
import os

import pytest

from app.config import Config, get_config


def test_mounts_endpoint_lists_every_source(client):
    body = client.get("/api/mounts").json()
    assert {m["key"] for m in body["mounts"]} == {"zotero", "projects", "professional"}


def test_absent_source_is_missing_not_an_error(client):
    body = client.get("/api/mounts").json()
    assert all(m["state"] == "missing" for m in body["mounts"])
    assert body["writable_sources"] == []


def test_writable_source_is_reported_as_a_defect(client, tmp_path, monkeypatch):
    """A source we *can* write to must be flagged, never quietly accepted."""
    writable = tmp_path / "leaky-projects"
    writable.mkdir()
    monkeypatch.setenv("PROJECTS_DIR", str(writable))

    body = client.get("/api/mounts").json()
    projects = next(m for m in body["mounts"] if m["key"] == "projects")
    assert projects["exists"] is True
    assert projects["writable"] is True
    assert projects["state"] == "writable"
    assert projects["ok"] is False
    assert "projects" in body["writable_sources"]


def test_read_only_source_is_ok(client, tmp_path, monkeypatch):
    ro = tmp_path / "ro-projects"
    ro.mkdir()
    ro.chmod(0o500)
    monkeypatch.setenv("PROJECTS_DIR", str(ro))
    try:
        body = client.get("/api/mounts").json()
        projects = next(m for m in body["mounts"] if m["key"] == "projects")
        if projects["writable"]:
            pytest.skip("running as a user that bypasses mode bits (e.g. root)")
        assert projects["state"] == "read-only"
        assert projects["ok"] is True
    finally:
        ro.chmod(0o700)


def test_config_never_returns_secrets(client, monkeypatch):
    monkeypatch.setenv("ZOTERO_API_KEY", "zk-should-never-appear")
    monkeypatch.setenv("S2_API_KEY", "s2-should-never-appear")
    raw = client.get("/api/config").text
    assert "should-never-appear" not in raw


def test_writable_check_is_side_effect_free(tmp_path, monkeypatch):
    """Reading /api/mounts must not create anything anywhere."""
    d = tmp_path / "probe"
    d.mkdir()
    monkeypatch.setenv("PROJECTS_DIR", str(d))
    before = set(os.listdir(d))
    cfg = get_config()
    for m in cfg.mounts():
        _ = m.writable, m.exists
    assert set(os.listdir(d)) == before


@pytest.mark.parametrize("real_mount", [
    "/data/zotero/zotero.sqlite",
    "/data/projects",
    "/data/professional",
])
def test_container_mounts_are_read_only_when_present(real_mount):
    """In the container these paths exist and must not be writable.

    Skipped outside the container, where they are absent by design.
    """
    if not os.path.exists(real_mount):
        pytest.skip(f"{real_mount} not mounted (not running in the container)")
    if os.geteuid() == 0 and not os.path.ismount(real_mount):
        pytest.skip("root bypasses mode bits on non-mount paths")
    assert not os.access(real_mount, os.W_OK), (
        f"{real_mount} is writable — check the :ro flag in docker-compose.yml"
    )
