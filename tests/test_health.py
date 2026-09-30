def test_health_is_dependency_free(client):
    """The container healthcheck must not depend on any source being present."""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_api_health_reports_version(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert len(body["version"]) == 7
    assert body["started_at"].endswith("UTC")


def test_version_endpoint(client):
    r = client.get("/api/version")
    assert r.status_code == 200
    assert "·" in r.json()["version"]


def test_starts_with_no_sources_present(client):
    """raDash boots with Zotero absent and trundlr unreachable."""
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/mounts").status_code == 200
