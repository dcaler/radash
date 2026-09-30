"""Network adapters: parsing, and the cache path that keeps panels alive.

No test here reaches the network. The adapters are exercised through their own
cache fallback and through their pure parsing functions, which is where the
logic actually is.
"""
import json

import httpx
import pytest
from sqlmodel import Session

from app.config import Config
from app.models import SourceCache, SourceStatus
from app.sources import cache, openalex, s2, trundlr

PROJECTS = [
    {"id": 20, "name": "parableSower", "priority": 1, "folder": "/p/260621_parableSower",
     "archived": False, "description": None, "created_at": "2026-06-21"},
    {"id": 27, "name": "Kindred", "priority": 2, "folder": "", "archived": True,
     "description": None, "created_at": "2026-01-01"},
]

WORK = {
    "id": "https://openalex.org/W123", "doi": "https://doi.org/10.1016/J.FICT.2015.01.001",
    "title": "The Dispossessed", "publication_year": 2015, "type": "article",
    "cited_by_count": 348,
    "counts_by_year": [{"year": 2026, "cited_by_count": 15},
                       {"year": 2025, "cited_by_count": 40},
                       {"year": 2023, "cited_by_count": 54}],
    "authorships": [{"author": {"display_name": "Octavia E. Butler"}}],
    "primary_location": {"source": {"display_name": "Renewable Energy"}},
    "topics": [{"display_name": "Social Acceptance of Renewable Energy"}],
    "abstract_inverted_index": {"Solar": [0], "adoption": [1], "diffuses": [2]},
    "is_retracted": False,
}


def test_reconstruct_abstract_rebuilds_word_order():
    assert openalex.reconstruct_abstract(WORK["abstract_inverted_index"]) == \
        "Solar adoption diffuses"


def test_reconstruct_abstract_tolerates_junk():
    for junk in (None, {}, [], {"a": "notalist"}, {"a": [None]}):
        assert openalex.reconstruct_abstract(junk) == ""


def test_work_parsing_normalises_doi_and_keeps_accrual():
    w = openalex._work(WORK, "A0000000001")
    assert w.doi == "10.1016/j.fict.2015.01.001", "DOI normalised for matching"
    assert w.counts_by_year == {2026: 15, 2025: 40, 2023: 54}
    assert w.venue == "Renewable Energy"
    assert w.abstract == "Solar adoption diffuses"
    assert w.source_author_id == "A0000000001"
    assert w.short_id == "W123"


def test_momentum_is_a_share_of_the_total():
    w = openalex._work(WORK)
    assert w.citations_since(2025) == 55
    assert w.momentum(2026) == pytest.approx(55 / 348)


def test_momentum_is_none_when_never_cited():
    w = openalex._work({**WORK, "cited_by_count": 0, "counts_by_year": []})
    assert w.momentum(2026) is None


def test_trundlr_serves_cache_when_unreachable(session, monkeypatch):
    cache.save(session, trundlr.KEY, PROJECTS, item_count=2)
    monkeypatch.setenv("TRUNDLR_URL", "http://127.0.0.1:1")   # nothing listens
    report = trundlr.read(Config(), session)
    assert report.status is SourceStatus.stale
    assert report.count == 2
    assert report.data.from_cache is True
    assert report.data.active[0].name == "parableSower"
    assert "unreachable" in report.note


def test_trundlr_with_no_cache_is_an_error_not_a_lie(session, monkeypatch):
    monkeypatch.setenv("TRUNDLR_URL", "http://127.0.0.1:1")
    report = trundlr.read(Config(), session)
    assert report.status is SourceStatus.error
    assert report.count is None


def test_trundlr_counts_split_active_from_archived(session):
    ledger = trundlr._ledger(PROJECTS, version="1.0")
    counts = trundlr._counts(ledger)
    assert counts == {"projects": 2, "active": 1, "archived": 1, "with_folder": 1,
                      "priority_1": 1, "priority_2": 0, "priority_3": 0, "priority_4": 0}


def test_openalex_keeps_profiles_separate(session):
    cache.save(session, openalex.KEY, {"A1": [WORK], "A2": [WORK]}, item_count=2)
    report = openalex._fall_back(
        openalex.SourceReport(key=openalex.KEY), session, "test")
    assert report.status is SourceStatus.stale
    assert len(report.data.profiles) == 2
    assert report.count == 2, "two rows, not one merged work"
    assert report.counts["distinct_dois"] == 1, "and the overlap is visible"


def test_openalex_without_author_ids_is_missing(monkeypatch):
    monkeypatch.setenv("OPENALEX_AUTHOR_IDS", "")
    report = openalex.read(Config())
    assert report.status is SourceStatus.missing
    assert "OPENALEX_AUTHOR_IDS" in report.note


def test_s2_without_dois_waits_rather_than_guessing():
    report = s2.read(Config(), dois=[])
    assert report.status is SourceStatus.missing
    assert "works ledger" in report.note


def test_s2_paper_parsing():
    p = s2._paper("10.1/x", {"paperId": "abc", "title": "T", "year": 2015,
                             "citationCount": 300, "influentialCitationCount": 12,
                             "venue": "Renewable Energy"})
    assert (p.citation_count, p.influential_count, p.venue) == (300, 12, "Renewable Energy")


def test_cache_roundtrip_and_corrupt_payload(session):
    cache.save(session, "x", {"a": 1})
    payload, fetched = cache.load(session, "x")
    assert payload == {"a": 1} and fetched is not None

    row = session.get(SourceCache, "x")
    row.payload = "{not json"
    session.add(row)
    session.commit()
    assert cache.load(session, "x") == (None, None)


def test_cache_without_a_session_is_a_noop():
    cache.save(None, "x", {"a": 1})
    assert cache.load(None, "x") == (None, None)


def test_s2_backs_off_then_succeeds_on_429(monkeypatch):
    """A throttled DOI is retried, not counted as an answer."""
    calls = []

    class Resp:
        def __init__(self, code, body=None, headers=None):
            self.status_code, self._body = code, body or {}
            self.headers = headers or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("err", request=None, response=self)

        def json(self):
            return self._body

    sequence = [Resp(429, headers={"retry-after": "0"}),
                Resp(200, {"paperId": "p", "title": "T", "citationCount": 7})]

    def fake_get(self, url, **kw):
        calls.append(url)
        return sequence.pop(0)

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    monkeypatch.setattr(s2.time, "sleep", lambda *_: None)
    report = s2.read(Config(), dois=["10.1/x"])
    assert len(calls) == 2, "the 429 was retried"
    assert report.counts["found"] == 1
    assert report.counts["rate_limited"] == 0


def test_s2_reports_persistent_throttling_separately_from_not_found(monkeypatch):
    class Resp:
        status_code = 429
        headers = {}

        def raise_for_status(self):
            raise httpx.HTTPStatusError("429", request=None, response=self)

        def json(self):
            return {}

    monkeypatch.setattr(httpx.Client, "get", lambda self, url, **kw: Resp())
    monkeypatch.setattr(s2.time, "sleep", lambda *_: None)
    report = s2.read(Config(), dois=["10.1/x", "10.1/y"])
    assert report.counts["rate_limited"] == 2
    assert report.counts["not_found"] == 0
    assert "throttled" in report.note
