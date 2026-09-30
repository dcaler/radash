"""Merge semantics — M2-T5.

Given candidates a human has agreed are one work, decide what the ledger entry
says. Three rules, each chosen because the alternative loses information:

**`max()` on citations, per lane.** Citation indexes under-count far more often
than they invent citations: a paper missing from one index's crawl reads zero
there and forty elsewhere. Taking the maximum within a lane is therefore the
better estimate, and taking it *within* a lane rather than across all of them
keeps the automated and manual figures separately reportable, which is what the
two-lane display needs.

**Union of venues, one canonical.** A preprint server and a journal are both
true statements about where the work appeared. The ledger shows the journal and
keeps the rest, so "this was on SSRN first" stays answerable.

**Provenance per number, not per record.** Every figure records which source
produced it. A blended total nobody can attribute is exactly the kind of number
this project exists to avoid.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from app.ledger import links as links_mod
from app.ledger.propose import PREPRINT_VENUES
from app.models import CandidateSource

# Which lane a source reports into.
AUTOMATED = {CandidateSource.openalex, CandidateSource.s2}
MANUAL = {CandidateSource.scholar}


@dataclass
class MergedWork:
    fingerprint: str
    title: str = ""
    year: Optional[int] = None
    venue: Optional[str] = None
    doi: Optional[str] = None
    work_type: Optional[str] = None
    venues_seen: list[str] = field(default_factory=list)
    citations_automated: Optional[int] = None
    citations_manual: Optional[int] = None
    provenance: dict = field(default_factory=dict)
    counts_by_year: dict[int, int] = field(default_factory=dict)
    candidate_ids: list[int] = field(default_factory=list)
    on_cv: bool = False


def _is_preprint_venue(venue: Optional[str]) -> bool:
    v = (venue or "").casefold()
    return any(p in v for p in PREPRINT_VENUES)


def _best_title(cands) -> str:
    """Your CV's wording wins; otherwise the fullest title from a publisher.

    Longest is a decent proxy for fullest: indexes truncate subtitles far more
    often than they invent them.
    """
    cv = [c for c in cands if c.source == CandidateSource.cv and c.title]
    pool = cv or [c for c in cands if not _is_preprint_venue(c.venue) and c.title]
    pool = pool or [c for c in cands if c.title]
    return max(pool, key=lambda c: len(c.title)).title if pool else ""


def merge(fingerprint: str, cands: list) -> MergedWork:
    """Collapse one agreed group of candidates into a ledger entry."""
    out = MergedWork(fingerprint=fingerprint)
    if not cands:
        return out

    out.candidate_ids = [c.id for c in cands if c.id is not None]
    out.on_cv = any(c.source == CandidateSource.cv for c in cands)
    out.title = _best_title(cands)

    # Published beats preprint for the canonical venue, DOI, year and type.
    published = [c for c in cands if c.venue and not _is_preprint_venue(c.venue)]
    canonical = published or cands

    venues, seen = [], set()
    for c in cands:
        if c.venue and c.venue.casefold() not in seen:
            seen.add(c.venue.casefold())
            venues.append(c.venue)
    out.venues_seen = venues
    out.venue = next((c.venue for c in canonical if c.venue), None)
    out.work_type = next((c.work_type for c in canonical if c.work_type), None)
    out.doi = next((c.doi for c in canonical if c.doi),
                   next((c.doi for c in cands if c.doi), None))

    years = [c.year for c in canonical if c.year] or [c.year for c in cands if c.year]
    # Earliest among the canonical rows: indexes sometimes stamp a reissue year,
    # and a work's year is when it appeared, not when it was last touched.
    out.year = min(years) if years else None

    out.citations_automated, auto_src = _best_count(cands, AUTOMATED)
    out.citations_manual, manual_src = _best_count(cands, MANUAL)
    out.provenance = {
        "automated": {"count": out.citations_automated, "source": auto_src},
        "manual": {"count": out.citations_manual, "source": manual_src},
        "title": "cv" if any(c.source == CandidateSource.cv for c in cands) else "index",
        "candidates": [
            {"source": c.source.value, "profile": c.source_profile,
             "doi": c.doi, "cited_by": c.cited_by, "source_id": c.source_id,
             "links": links_mod.candidate_links(c)}
            for c in cands
        ],
    }
    out.counts_by_year = _merge_accrual(cands)
    return out


def _best_count(cands, lane) -> tuple[Optional[int], Optional[str]]:
    """Highest count within one lane, and which source gave it."""
    best, src = None, None
    for c in cands:
        if c.source in lane and c.cited_by is not None:
            if best is None or c.cited_by > best:
                best, src = c.cited_by, c.source.value
    return best, src


def _merge_accrual(cands) -> dict[int, int]:
    """Per-year accrual, taking the highest figure offered for each year.

    Same reasoning as the totals, applied year by year: a year one source has
    not finished crawling reads low, and the shape of the series is what the
    accrual panels read.
    """
    out: dict[int, int] = {}
    for c in cands:
        if not c.counts_by_year:
            continue
        try:
            series = json.loads(c.counts_by_year)
        except (ValueError, TypeError):
            continue
        if not isinstance(series, dict):
            continue
        for year, n in series.items():
            try:
                y, v = int(year), int(n)
            except (TypeError, ValueError):
                continue
            if v > out.get(y, -1):
                out[y] = v
    return dict(sorted(out.items()))
