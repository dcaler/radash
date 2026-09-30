"""Semantic Scholar — the second opinion on the automated citation lane.

S2 and OpenAlex disagree about citation counts, routinely and by a lot. That
disagreement is information: it bounds how precisely any of these numbers can
be stated. raDash keeps both and takes `max()` at merge time (M2-T5), on the
reasoning that citation indexes under-count far more often than they invent
citations.

Lookup is by DOI, one work at a time. S2 offers a `POST /paper/batch` endpoint
which would be politer to their servers, and it is not used here: every request
raDash makes to a source it observes is a `GET`, without exception, so that the
read-only property is checkable by inspection rather than by argument. At ~27
works the difference is a few seconds once a week.

The API key is read from the environment at call time and never stored on
`Config`, so it cannot leak through `/api/config`.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from sqlmodel import Session

from app.config import Config
from app.models import SourceStatus
from app.sources import SourceReport, timer
from app.sources import cache

KEY = "s2"

BASE = "https://api.semanticscholar.org/graph/v1"
FIELDS = "title,year,citationCount,influentialCitationCount,externalIds,venue"
TIMEOUT = 15.0

# S2 rate-limits hard, and a key does not exempt you: a real run against 22
# DOIs at 0.1s spacing drew 429s on half of them. The spacing below is what
# that measurement argued for, and 429 is retried with backoff rather than
# counted as a failed lookup — a throttled DOI is one raDash has not asked
# about yet, which is a different fact from one S2 does not have.
SLEEP_UNKEYED = 1.1
SLEEP_KEYED = 0.4
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_BACKOFF = 2.0


@dataclass
class S2Paper:
    doi: str
    paper_id: Optional[str] = None
    title: str = ""
    year: Optional[int] = None
    citation_count: int = 0
    influential_count: int = 0
    venue: Optional[str] = None


@dataclass
class S2Result:
    papers: dict[str, S2Paper] = field(default_factory=dict)   # by DOI
    looked_up: int = 0
    not_found: list[str] = field(default_factory=list)
    rate_limited: list[str] = field(default_factory=list)
    from_cache: bool = False

    @property
    def citation_sum(self) -> int:
        return sum(p.citation_count for p in self.papers.values())


def api_key() -> str:
    return os.getenv("S2_API_KEY", "").strip()


def read(cfg: Config, dois: Optional[list[str]] = None,
         session: Optional[Session] = None) -> SourceReport:
    """Look up citation counts for `dois`.

    With no DOIs this reports `missing` rather than guessing: S2 is a lookup
    source, and what to look up is decided by the works ledger, not here.
    """
    report = SourceReport(key=KEY)
    dois = [d.strip().lower() for d in (dois or []) if d and d.strip()]
    if not dois:
        report.status = SourceStatus.missing
        report.note = "no DOIs to look up (S2 is driven by the works ledger)"
        return report

    key = api_key()
    headers = {"User-Agent": f"raDash/1.0 ({cfg.contact_email or 'anonymous'})"}
    if key:
        headers["x-api-key"] = key
    delay = SLEEP_KEYED if key else SLEEP_UNKEYED

    result = S2Result()
    payload: dict[str, dict] = {}
    try:
        with timer(report):
            with httpx.Client(timeout=TIMEOUT, headers=headers) as client:
                for i, doi in enumerate(dois):
                    if i:
                        time.sleep(delay)
                    try:
                        raw = _lookup(client, doi)
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code == 404:
                            result.not_found.append(doi)
                        elif exc.response.status_code == 429:
                            result.rate_limited.append(doi)
                        else:
                            report.degrade(f"{doi}: HTTP {exc.response.status_code}")
                        continue
                    except Exception as exc:
                        report.degrade(f"{doi}: {type(exc).__name__}: {exc}")
                        continue
                    result.looked_up += 1
                    if raw is not None:
                        payload[doi] = raw
                        result.papers[doi] = _paper(doi, raw)
    except Exception as exc:
        return _fall_back(report, session, f"{type(exc).__name__}: {exc}")

    # Throttling is not unreachability. A run where every DOI was rate-limited
    # still learned something — which DOIs are outstanding — and falling back to
    # the cache here would report "S2 unreachable" for a server that answered
    # every time, just with 429.
    if not (result.papers or result.not_found or result.rate_limited):
        return _fall_back(report, session, report.note or "no lookup succeeded")

    cache.save(session, KEY, payload, item_count=len(result.papers))
    report.data = result
    report.count = len(result.papers)
    report.source_mtime = report.read_at
    report.counts = {
        "requested": len(dois),
        "found": len(result.papers),
        "not_found": len(result.not_found),
        "rate_limited": len(result.rate_limited),
        "citations_summed": result.citation_sum,
        "keyed": 1 if key else 0,
    }
    if report.status is SourceStatus.missing:
        report.status = SourceStatus.ok

    # Throttled and unknown are reported separately: the first means the
    # number is missing and retrievable, the second that S2 has no such paper.
    notes = []
    if result.rate_limited:
        notes.append(f"{len(result.rate_limited)} DOI(s) throttled by S2 after "
                     f"{RATE_LIMIT_RETRIES} attempts — retry the refresh")
    if result.not_found:
        notes.append(f"{len(result.not_found)} DOI(s) unknown to S2")
    if notes:
        report.note = "; ".join(notes)
        if report.status is SourceStatus.ok:
            report.status = SourceStatus.degraded
    return report


def _lookup(client: httpx.Client, doi: str) -> Optional[dict]:
    """One lookup, backing off through S2's throttle.

    `Retry-After` is honoured when sent; otherwise the wait doubles. A 429 that
    survives every attempt is re-raised so the caller can count it as throttled
    rather than as an answer.
    """
    wait = RATE_LIMIT_BACKOFF
    for attempt in range(RATE_LIMIT_RETRIES):
        resp = client.get(f"{BASE}/paper/DOI:{doi}", params={"fields": FIELDS})
        if resp.status_code == 429 and attempt < RATE_LIMIT_RETRIES - 1:
            retry_after = resp.headers.get("retry-after")
            try:
                pause = float(retry_after) if retry_after else wait
            except ValueError:
                pause = wait
            time.sleep(min(pause, 10.0))
            wait *= 2
            continue
        resp.raise_for_status()
        body = resp.json()
        return body if isinstance(body, dict) else None
    return None


def _paper(doi: str, raw: dict) -> S2Paper:
    return S2Paper(
        doi=doi,
        paper_id=raw.get("paperId"),
        title=raw.get("title") or "",
        year=raw.get("year"),
        citation_count=int(raw.get("citationCount") or 0),
        influential_count=int(raw.get("influentialCitationCount") or 0),
        venue=raw.get("venue") or None,
    )


def _fall_back(report: SourceReport, session: Optional[Session], why: str) -> SourceReport:
    payload, fetched_at = cache.load(session, KEY)
    if not isinstance(payload, dict):
        report.fail(f"S2 unreachable and nothing cached ({why})")
        return report
    result = S2Result(from_cache=True, papers={
        doi: _paper(doi, raw) for doi, raw in payload.items() if isinstance(raw, dict)
    })
    report.data = result
    report.count = len(result.papers)
    report.status = SourceStatus.stale
    report.source_mtime = fetched_at
    report.note = f"S2 unreachable — serving cache ({why})"
    report.failures.append(why)
    report.counts = {"found": len(result.papers), "citations_summed": result.citation_sum}
    return report
