"""`/api/sources` — the M1 exit criterion, served.

The endpoint is exercised against a container that has nothing mounted, which
is both the honest first-boot state and the state that catches the failure mode
this milestone is guarding against: a source that is absent must produce a
labelled absence, never a zero that reads like a fact.
"""
from app.models import SourceCache, SourceState


def test_reports_every_source(client):
    body = client.get("/api/sources").json()
    keys = {s["key"] for s in body["sources"]}
    assert keys == {"zotero", "haarpi", "scholar", "trundlr", "openalex", "s2", "website"}


def test_absent_sources_are_labelled_not_zeroed(client):
    body = client.get("/api/sources").json()
    by_key = {s["key"]: s for s in body["sources"]}
    assert by_key["zotero"]["status"] == "missing"
    assert by_key["zotero"]["count"] is None, "absent is not the same as empty"
    assert "not mounted" in by_key["zotero"]["note"]


def test_get_does_not_touch_the_network(client):
    """trundlr points at a dead port; a GET that tried it would take the error
    path. Served from cache-or-nothing instead, and says which."""
    body = client.get("/api/sources").json()
    trundlr = next(s for s in body["sources"] if s["key"] == "trundlr")
    assert body["refreshed"] is False
    assert "refresh" in trundlr["note"]


def test_a_source_never_fetched_is_waiting_not_failing(client):
    """The fresh-deploy state. Nothing is broken, so nothing reports broken."""
    body = client.get("/api/sources").json()
    assert body["failing"] == [], "nothing has failed on a fresh deploy"
    assert set(body["waiting"]) == {"zotero", "haarpi", "scholar", "trundlr",
                                    "openalex", "s2", "website"}
    assert body["ok"] is True
    trundlr = next(s for s in body["sources"] if s["key"] == "trundlr")
    assert trundlr["status"] == "missing"
    assert "not fetched yet" in trundlr["note"]


def test_refresh_is_a_post_and_reports_the_attempt(client):
    body = client.post("/api/sources/refresh").json()
    assert body["refreshed"] is True
    trundlr = next(s for s in body["sources"] if s["key"] == "trundlr")
    assert trundlr["status"] == "error"
    assert trundlr["failures"], "an unreachable source names its failure"


def test_source_state_is_persisted(client):
    client.get("/api/sources")
    from app.database import _engine
    from sqlmodel import Session, select
    with Session(_engine) as s:
        rows = {r.key: r for r in s.exec(select(SourceState)).all()}
    assert set(rows) == {"zotero", "haarpi", "scholar", "trundlr", "openalex", "s2", "website"}
    assert rows["zotero"].last_read_at is not None
    assert rows["zotero"].last_ok_at is None, "a missing source never read cleanly"


def test_join_endpoint_survives_having_nothing_to_join(client):
    body = client.get("/api/sources/join").json()
    assert body["matches"] == []


def test_failing_means_error_and_nothing_else(client):
    """`missing` must never be counted as a failure -- M0 settled that for
    mounts and the same honesty applies to sources."""
    body = client.post("/api/sources/refresh").json()
    assert set(body["failing"]) == {s["key"] for s in body["sources"]
                                    if s["status"] == "error"}
    assert "trundlr" in body["failing"], "a dead port is a real failure"
    assert "scholar" not in body["failing"], "no import yet is not a failure"
    assert body["ok"] is False
