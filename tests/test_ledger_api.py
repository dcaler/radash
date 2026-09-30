"""The ledger endpoints and the paste imports."""
import pytest

from app.ledger.candidates import parse_publication_list

CV_PASTE = """Butler, O. E., & Jemisin, N. (2015). The Dispossessed: an ambiguous utopia of residential solar. Renewable Energy, 12, 101-120. https://doi.org/10.1016/j.fict.2015.01.001

Butler, O. E. (2022). Kindred effects in the diffusion of solar photovoltaic panels. JASSS, 31(2).
"""

SCHOLAR_PASTE = """The Dispossessed: an ambiguous utopia of residential solar
OE Butler, N Jemisin - Renewable Energy, 2015
Cited by 300
"""


def test_ledger_is_empty_before_any_snapshot(client):
    body = client.get("/api/ledger").json()
    assert body["snapshot"] is None
    assert body["works"] == []
    assert "rebuild" in body["note"]


def test_rebuild_produces_a_snapshot_even_with_no_sources(client):
    body = client.post("/api/ledger/rebuild").json()
    assert body["summary"]["snapshot_id"] is not None
    assert body["summary"]["works"] == 0


def test_publication_preview_does_not_store_anything(client, tmp_path):
    body = client.post("/api/ledger/publications/preview",
                       json={"text": CV_PASTE}).json()
    assert body["count"] == 2
    assert any("The Dispossessed" in e["title"] for e in body["entries"])
    assert client.get("/api/ledger").json()["works"] == []


def test_publication_parse_reads_year_and_doi_reliably():
    entries = parse_publication_list(CV_PASTE)
    assert entries[0]["year"] == 2015
    assert entries[0]["doi"] == "10.1016/j.fict.2015.01.001"
    assert entries[1]["year"] == 2022
    assert entries[1]["doi"] is None


def test_publication_import_then_rebuild_folds_it_in(client):
    stored = client.post("/api/ledger/publications/import",
                         json={"text": CV_PASTE}).json()
    assert stored["stored"].endswith("_publications_ra.txt")
    body = client.post("/api/ledger/rebuild").json()
    assert body["summary"]["by_source"].get("cv") == 2


def test_an_oversized_paste_is_refused(client):
    resp = client.post("/api/ledger/publications/preview",
                       json={"text": "x" * (600 * 1024)})
    assert resp.status_code == 422
    assert "limit" in resp.json()["detail"]


def test_an_empty_paste_is_refused(client):
    resp = client.post("/api/ledger/publications/import", json={"text": "   "})
    assert resp.status_code == 422


def test_scholar_preview_reports_how_many_carried_counts(client):
    body = client.post("/api/sources/scholar/preview",
                       json={"text": SCHOLAR_PASTE}).json()
    assert body["count"] == 1
    assert body["with_citations"] == 1
    assert body["citations_summed"] == 300


def test_scholar_import_is_read_by_the_scholar_source(client):
    client.post("/api/sources/scholar/import", json={"text": SCHOLAR_PASTE})
    body = client.get("/api/sources").json()
    scholar = next(s for s in body["sources"] if s["key"] == "scholar")
    assert scholar["status"] in ("ok", "stale")
    assert scholar["count"] == 1


def test_a_ruling_is_recorded_and_survives(client):
    client.post("/api/ledger/rebuild")
    resp = client.post("/api/ledger/rule", json={
        "fingerprint": "ttl:aaa", "other_fingerprint": "ttl:bbb",
        "ruling": "confirmed", "rationale": "same paper",
    })
    assert resp.status_code == 200
    decisions = client.get("/api/ledger/decisions").json()["decisions"]
    assert len(decisions) == 1
    assert decisions[0]["rationale"] == "same paper"


def test_a_repeated_ruling_updates_rather_than_duplicates(client):
    for ruling in ("confirmed", "split"):
        client.post("/api/ledger/rule", json={
            "fingerprint": "ttl:aaa", "other_fingerprint": "ttl:bbb",
            "ruling": ruling})
    decisions = client.get("/api/ledger/decisions").json()["decisions"]
    assert len(decisions) == 1
    assert decisions[0]["ruling"] == "split"


def test_an_unknown_ruling_is_refused(client):
    resp = client.post("/api/ledger/rule",
                       json={"fingerprint": "ttl:a", "ruling": "probably"})
    assert resp.status_code == 422


def test_split_requires_the_other_fingerprint(client):
    resp = client.post("/api/ledger/rule",
                       json={"fingerprint": "ttl:a", "ruling": "split"})
    assert resp.status_code == 422
    assert "other_fingerprint" in resp.json()["detail"]


def test_confirming_alone_is_an_adoption_not_an_error(client):
    """An adoption proposal has no second side: "yes, this one is mine"."""
    resp = client.post("/api/ledger/rule",
                       json={"fingerprint": "ttl:a", "ruling": "confirmed",
                             "rationale": "yes, mine"})
    assert resp.status_code == 200
    decisions = client.get("/api/ledger/decisions").json()["decisions"]
    assert decisions[0]["other"] is None
    assert decisions[0]["ruling"] == "confirmed"


def test_exclusion_needs_only_one_fingerprint(client):
    resp = client.post("/api/ledger/rule",
                       json={"fingerprint": "ttl:a", "ruling": "excluded"})
    assert resp.status_code == 200


def test_proposals_endpoint_states_that_nothing_has_moved(client):
    client.post("/api/ledger/rebuild")
    body = client.get("/api/ledger/proposals").json()
    assert "rule on it" in body["note"]


def test_classify_sets_a_category_that_outlives_a_rebuild(client):
    client.post("/api/ledger/publications/import", json={"text": CV_PASTE})
    client.post("/api/ledger/rebuild")
    works = client.get("/api/ledger").json()["works"]
    fp = works[0]["fingerprint"]

    resp = client.post("/api/ledger/classify",
                       json={"fingerprint": fp, "category": "conference",
                             "rationale": "a talk, not a paper"})
    assert resp.status_code == 200

    client.post("/api/ledger/rebuild")
    after = {w["fingerprint"]: w for w in client.get("/api/ledger").json()["works"]}
    assert after[fp]["category"] == "conference"
    assert after[fp]["category_source"] == "you"


def test_an_unknown_category_is_refused(client):
    resp = client.post("/api/ledger/classify",
                       json={"fingerprint": "ttl:a", "category": "masterpiece"})
    assert resp.status_code == 422
    assert "unknown category" in resp.json()["detail"]


def test_excluding_a_work_removes_it_from_the_next_rebuild(client):
    client.post("/api/ledger/publications/import", json={"text": CV_PASTE})
    client.post("/api/ledger/rebuild")
    works = client.get("/api/ledger").json()["works"]
    fp = works[0]["fingerprint"]

    client.post("/api/ledger/rule", json={"fingerprint": fp, "ruling": "excluded",
                                          "rationale": "different author"})
    client.post("/api/ledger/rebuild")
    after = client.get("/api/ledger").json()["works"]
    assert fp not in {w["fingerprint"] for w in after}


def test_the_publication_total_counts_publications_only(client):
    client.post("/api/ledger/publications/import", json={"text": CV_PASTE})
    client.post("/api/ledger/rebuild")
    body = client.get("/api/ledger").json()
    assert body["totals"]["publications"] <= body["totals"]["works"]
    assert set(body["by_category"]) <= set(body["categories"])


SITE_PAGE = """<html><body>
<p><a href="/s/260812_CV.pdf">CV</a></p>
<p>PEER REVIEWED PUBLICATIONS</p>
<p>1. Butler, O. E., Jemisin, N. The Snow Queen: Winter Consumers and
   Strategic Technology Adoption. <i>Energy Policy.</i> 2018.</p>
</body></html>"""


def test_proposals_never_ask_you_to_merge_a_work_with_your_own_cv_line(client, monkeypatch):
    """The gate showed "duplicate: Energy Policy paper vs. [site] Peer Reviewed
    Publication" -- a proposal to merge a paper with your description of it.

    build() filtered site rows before grouping and the proposals endpoint,
    which re-reads every candidate from the database, did not.
    """
    import httpx

    class Resp:
        status_code = 200
        headers = {"content-type": "text/html"}
        text = SITE_PAGE

        def raise_for_status(self):
            return None

    monkeypatch.setenv("CV_URL", "https://example.com/page-cv")
    # Patched only across the rebuild: TestClient itself speaks httpx, so a
    # standing patch on Client.get hijacks the test's own requests.
    monkeypatch.setattr(httpx.Client, "get", lambda self, url, **kw: Resp())
    client.post("/api/ledger/rebuild?refresh=true")
    monkeypatch.undo()

    body = client.get("/api/ledger/proposals").json()
    labels = " ".join(f"{p['left_label']} {p['right_label']}"
                      for p in body["proposals"])
    assert "[site]" not in labels, "a CV line is not a candidate work"
