"""Where to go and look at a thing.

The gate asks you to rule on works you may not recognise — a 2019 conference
abstract, a co-authored report, a deposit from a fragmented profile. Ruling
well means being able to open the record, and a title in a table is not
something you can open.

Every link here is built from an identifier a source already gave us. Nothing
is guessed: a candidate with no DOI and no source id gets no link, rather than
a search URL that might land on the wrong paper and make you confident about
the wrong thing.
"""
from __future__ import annotations

from typing import Optional

from app.ledger.fingerprint import normalize_doi
from app.models import CandidateSource

DOI_BASE = "https://doi.org/"
OPENALEX_BASE = "https://openalex.org/"
S2_BASE = "https://www.semanticscholar.org/paper/"


def doi_url(doi: Optional[str]) -> Optional[str]:
    d = normalize_doi(doi)
    return f"{DOI_BASE}{d}" if d else None


def source_url(source, source_id: Optional[str]) -> Optional[str]:
    """The record on the source's own site, where one can be addressed."""
    if not source_id:
        return None
    if source == CandidateSource.openalex:
        return f"{OPENALEX_BASE}{source_id}"
    if source == CandidateSource.s2:
        return f"{S2_BASE}{source_id}"
    return None


def candidate_links(c) -> list[dict]:
    """Every way to look at one candidate, labelled by where it leads."""
    out: list[dict] = []
    d = doi_url(c.doi)
    if d:
        out.append({"label": "DOI", "url": d})
    s = source_url(c.source, c.source_id)
    if s:
        out.append({"label": c.source.value, "url": s})
    return out
