"""What you claim publicly, against what the record shows.

Your CV page is the statement of your work that other people actually read.
The ledger is what the indexes know. Neither is authoritative over the other,
and the gap between them is the useful output:

**In the record, not on your page.** A paper that came out and never made it
onto the site. This is the one worth being told about, and it is invisible
unless something compares the two — you do not notice the absence of a line
you never added.

**On your page, not in the record.** Usually fine and occasionally not: a
chapter no index has picked up, a venue outside OpenAlex's coverage, or a
title that drifted between the two. It bounds how much the automated numbers
can be trusted as a complete picture.

Matching is by title similarity, because the site carries no DOIs. That makes
every finding a *proposal to look*, not a verdict — the same standard as
everything else here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

from app import settings
from app.ledger.fingerprint import normalize_title
from app.ledger.links import doi_url
from app.models import WorkCategory

# Titles from a CV and from an index differ in punctuation and subtitles, so
# this is looser than the dedup engine's threshold. A miss here costs a line
# in a report; a miss there would merge two works.
MATCH_RATIO = 0.78


@dataclass
class Drift:
    unclaimed: list[dict] = field(default_factory=list)   # in ledger, not on site
    unindexed: list[dict] = field(default_factory=list)   # on site, not in ledger
    matched: int = 0
    unindexed_other: int = 0   # talks and sessions, not expected to be indexed
    claim_age_days: Optional[float] = None
    cv_file: Optional[str] = None
    checked: bool = False
    reason: Optional[str] = None      # why not, when checked is False

    def summary(self) -> dict:
        return {
            "checked": self.checked,
            "matched": self.matched,
            "unclaimed": self.unclaimed,
            "unindexed": self.unindexed,
            "unindexed_other": self.unindexed_other,
            "claim_age_days": (round(self.claim_age_days)
                               if self.claim_age_days is not None else None),
            "cv_file": self.cv_file,
            "reason": self.reason,
        }


def _similar(a: str, b: str) -> float:
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def match(items, claim) -> tuple[dict, Drift]:
    """Overlay the public claim onto the ledger.

    `items` is a sequence of
    `(fingerprint, title, year, venue, category, doi, links)`.
    Returns the per-fingerprint site entry that matched, and the drift.

    The identifiers travel with the work because the unclaimed list is, by
    construction, the rows you will not recognise -- that is why they are in
    it -- and a title you cannot open is not something you can rule on.

    The site is an overlay, never a row of its own. An entry on your CV is not
    a claim that a *separate* work exists — it is you describing a work the
    indexes either know about or do not. Treating those entries as candidates
    would double every paper you list and then report the duplicate as drift,
    which is the bug this function replaced.

    Each site entry matches at most one work, greedily and best-first, so one
    CV line cannot silently absolve two ledger rows.
    """
    out = Drift()
    matched: dict[str, object] = {}
    if claim is None or not claim.entries:
        return matched, out
    out.checked = True
    out.claim_age_days = claim.age_days
    out.cv_file = claim.cv_file

    site_entries = [e for e in claim.entries if e.published and e.title]
    comparable = {WorkCategory.publication.value, WorkCategory.conference.value}

    # Score every pair once, then take the strongest first: a greedy pass in
    # ledger order would let a weak early match consume an entry a later work
    # needed more.
    pairs = []
    items = list(items)
    for wi, (fp, title, year, venue, category, _doi, _links) in enumerate(items):
        for si, e in enumerate(site_entries):
            r = _similar(title, e.title)
            if r >= settings.num("MATCH_RATIO", MATCH_RATIO):
                pairs.append((r, wi, si))
    pairs.sort(reverse=True)

    used_w: set[int] = set()
    used_s: set[int] = set()
    for r, wi, si in pairs:
        if wi in used_w or si in used_s:
            continue
        used_w.add(wi)
        used_s.add(si)
        matched[items[wi][0]] = site_entries[si]
        out.matched += 1

    for wi, (fp, title, year, venue, category, doi, links) in enumerate(items):
        if wi in used_w or category not in comparable:
            continue
        out.unclaimed.append({
            "fingerprint": fp, "title": title, "year": year,
            "venue": venue, "category": category, "doi": doi,
            "url": doi_url(doi), "links": links,
        })

    for si, e in enumerate(site_entries):
        # Only publications are expected to be indexed. A talk at Cambridge or
        # a conference session is real work and no index will ever carry it, so
        # listing those here would bury the one finding that matters -- a
        # *paper* your CV claims that no index knows about -- under forty lines
        # of noise. They are counted, not enumerated.
        if si in used_s:
            continue
        if e.category is not WorkCategory.publication:
            out.unindexed_other += 1
            continue
        out.unindexed.append({
            "title": e.title, "year": e.year, "section": e.section,
            "category": e.category.value,
        })

    out.unclaimed.sort(key=lambda d: -(d["year"] or 0))
    out.unindexed.sort(key=lambda d: -(d["year"] or 0))
    return matched, out
