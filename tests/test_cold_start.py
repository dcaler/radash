"""Cold start — M8-T3.

An empty volume with no sources mounted is the state a fresh deploy is in for
its first minute, and it is the state this whole suite runs in. The property
worth asserting is not that raDash works without its sources — it cannot —
but that it **degrades to a shaped answer everywhere rather than to a stack
trace anywhere**, and that one `POST /api/refresh` takes it from empty to a
populated dashboard.

Endpoints are enumerated from the OpenAPI schema rather than listed here, so
a route added in a later milestone is covered by this the day it appears. That
matters more than it sounds: every panel that ever broke on a fresh deploy
broke because it assumed something the volume did not have yet.
"""
def _get_paths(client) -> list:
    """Every GET endpoint that needs no path parameter."""
    schema = client.get("/openapi.json").json()
    return sorted(path for path, ops in schema["paths"].items()
                  if "get" in ops and "{" not in path)


def test_every_read_endpoint_answers_on_an_empty_volume(client):
    """No database rows, no mounts, no network.

    Nothing may 500, and an endpoint that genuinely cannot answer — the map
    benchmark needs a corpus to time — must say 503 *with the reason*. That
    distinction is the whole point: unavailable-and-explained is a state
    raDash is designed to be in, and an unhandled exception is not.
    """
    paths = _get_paths(client)
    assert len(paths) >= 10, "the schema should list the whole read surface"

    for path in paths:
        if path in ("/openapi.json", "/docs", "/redoc",
                    "/docs/oauth2-redirect"):
            continue
        resp = client.get(path)
        assert resp.status_code != 500, (
            f"GET {path} crashed on a cold start: {resp.text[:200]}")
        if resp.status_code >= 500:
            detail = resp.json().get("detail")
            assert detail and len(detail) > 20, (
                f"GET {path} returned {resp.status_code} with no usable "
                f"reason: {resp.text[:200]}")


def test_the_panels_say_what_is_missing_rather_than_going_blank(client):
    """A blank panel is indistinguishable from a broken one. Each of these
    has to name the thing it is waiting for."""
    expectations = {
        "/api/ledger": "rebuild",
        "/api/planning": "fitted",
        "/api/frontier": "fitted",
        "/api/status/reading": "fitted",
    }
    for path, expected in expectations.items():
        body = client.get(path).json()
        text = str(body.get("note") or "")
        assert expected in text, (
            f"GET {path} should say what it is waiting for, said {text!r}")


def test_the_dashboard_renders_before_anything_has_been_read(client):
    body = client.get("/api/status").json()
    assert body["build"]["version"], "the build stamp is always available"
    for panel in ("changes", "headline", "areas", "drift", "coverage"):
        assert panel in body, f"{panel} must render on an empty volume"
    assert body["changes"]["available"] is False
    assert body["coverage"]["fitted_documents"] == 0


def test_the_mounts_panel_reports_absence_as_absence_not_as_failure(client):
    body = client.get("/api/mounts").json()
    assert body["mounts"], "the mounts are listed even when none exist"
    assert all(m["state"] == "missing" for m in body["mounts"])
    # A missing mount is expected on a fresh deploy. A *writable* one is the
    # defect, because raDash's boundary is that it writes to nothing it sees.
    assert body["writable_sources"] == []


def test_one_refresh_takes_an_empty_volume_to_a_snapshot(client):
    """The documented cold start: deploy, press refresh, read the dashboard.

    The sources are absent here, so the ledger is empty — but the snapshot
    exists, the cycle reports every step, and the status page stops saying it
    has nothing to compare against on the second run.
    """
    assert client.get("/api/status").json()["changes"]["available"] is False

    first = client.post("/api/refresh?fit=false&gather=false").json()
    assert [s["step"] for s in first["steps"]] == ["sources", "ledger"]
    assert client.get("/api/ledger").json()["snapshot"] is not None

    second = client.post("/api/refresh?fit=false&gather=false").json()
    assert second["ok"] is True
    changes = client.get("/api/status").json()["changes"]
    assert changes["available"] is True, (
        "two snapshots means the delta panel can finally answer")


def test_a_cold_start_writes_only_its_own_volume(client, tmp_path):
    """The output directory is created on first run; nothing else is."""
    client.post("/api/refresh?fit=false&gather=false")
    assert (tmp_path / "output").is_dir()
    assert not (tmp_path / "absent").exists(), (
        "no source path is created by a cold start — they are observed, not "
        "made")


# --- M8-T4: the files a release needs ---------------------------------------

def test_the_release_metadata_is_present_and_parses():
    """A Zenodo deposit fails on a malformed `.zenodo.json` at upload time,
    which is the worst moment to find out. CITATION.cff is the same shape of
    problem: nothing reads it until somebody tries to cite you."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent

    zenodo = json.loads((root / ".zenodo.json").read_text())
    assert zenodo["upload_type"] == "software"
    assert zenodo["creators"] and zenodo["creators"][0]["name"]
    assert zenodo["title"] and zenodo["description"]

    citation = (root / "CITATION.cff").read_text()
    assert citation.startswith("cff-version:")
    for required in ("title:", "authors:", "license:", "repository-code:"):
        assert required in citation, f"CITATION.cff needs {required}"

    readme = (root / "README.md").read_text()
    # A logo may sit above it, but the first heading is the project's name.
    headings = [line for line in readme.splitlines() if line.startswith("#")]
    assert headings and headings[0] == "# raDash"
    assert "writes to nothing it observes" in readme
    assert "PolyForm" in readme, "the licence is named where people look"


def test_nothing_claims_an_identifier_it_does_not_have():
    """An ORCID or a DOI that was guessed at is worse than an absent one: it
    resolves to somebody else, and it resolves quietly."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for name in ("CITATION.cff", ".zenodo.json"):
        text = (root / name).read_text().lower()
        assert "orcid" not in text, (
            f"{name} names an ORCID — it must be a real one, added by hand")
        assert "10.5281/zenodo" not in text, (
            f"{name} names a DOI, which Zenodo mints on deposit")
