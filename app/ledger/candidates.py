"""Candidate assembly — M2-T2 and M2-T3.

Turns each source's report into `WorkCandidate` rows. Nothing is deduplicated
here and nothing is judged: two profiles listing the same paper produce two
rows, because that duplication is the finding, and a source's claim is recorded
as the source made it.

The two manual sources — your pasted publication list and the `1_Publication/`
folder names — are parsed with deliberately visible heuristics. A CV is prose
written for humans, so extraction is guesswork; the answer is not a cleverer
parser but showing you what it extracted before anything is committed, which is
what the import preview is for.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterable, Optional

from app.config import Config
from app.ledger.fingerprint import fingerprint, normalize_doi
from app.models import CandidateSource, WorkCandidate

# `2022_JASSS_FIFTH` — year, venue, slug. Structured enough to need no parser.
FOLDER_RE = re.compile(r"^(?P<year>(?:19|20)\d{2})[_-](?P<venue>[^_-]+)[_-](?P<slug>.+)$")

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:a-z0-9]+", re.IGNORECASE)
YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")

# A chunk that is mostly initials and commas is an author list, not a title.
INITIALS_RE = re.compile(r"\b[A-Z]\.\s*")

PUBLICATION_DIR = "1_Publication"

# Directory entries in 1_Publication/ that are not papers.
NOT_A_PAPER = frozenset({"distribution details", "venue notes", "@eadir"})


def _cand(source: CandidateSource, **kw) -> WorkCandidate:
    doi = normalize_doi(kw.pop("doi", None)) or None
    title = kw.pop("title", "") or ""
    year = kw.pop("year", None)
    c = WorkCandidate(source=source, doi=doi, title=title, year=year, **kw)
    c.fingerprint = fingerprint(doi=doi, title=title, year=year)
    return c


def from_openalex(result) -> list[WorkCandidate]:
    """One row per work per profile, provenance intact (M2-T2)."""
    out: list[WorkCandidate] = []
    if result is None:
        return out
    for profile in result.profiles:
        for w in profile.works:
            out.append(_cand(
                CandidateSource.openalex,
                source_id=w.short_id or None,
                source_profile=profile.author_id,
                doi=w.doi, title=w.title, year=w.year,
                venue=w.venue, work_type=w.type,
                authors=json.dumps(w.authors[:25]),
                cited_by=w.cited_by_count,
                counts_by_year=json.dumps(w.counts_by_year),
            ))
    return out


def from_s2(result) -> list[WorkCandidate]:
    out: list[WorkCandidate] = []
    if result is None:
        return out
    for doi, p in result.papers.items():
        out.append(_cand(
            CandidateSource.s2, source_id=p.paper_id, doi=doi,
            title=p.title, year=p.year, venue=p.venue, cited_by=p.citation_count,
        ))
    return out


def from_scholar(imported) -> list[WorkCandidate]:
    out: list[WorkCandidate] = []
    if imported is None:
        return out
    for e in imported.entries:
        out.append(_cand(
            CandidateSource.scholar, title=e.title, year=e.year,
            venue=e.venue, cited_by=e.cited_by,
            authors=json.dumps([e.authors] if e.authors else []),
        ))
    return out


def from_folders(cfg: Config) -> list[WorkCandidate]:
    """`1_Publication/` directory names — a second, independent record.

    Thin (a handful of folders against ~27 works) and therefore corroborating
    rather than grounding, but it needs no parsing at all: the names are
    already `{year}_{venue}_{slug}`.
    """
    root = cfg.professional_dir / PUBLICATION_DIR
    out: list[WorkCandidate] = []
    if not root.is_dir():
        return out
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return out
    for e in entries:
        if not e.is_dir(follow_symlinks=False):
            continue
        if e.name.casefold() in NOT_A_PAPER or e.name.startswith("."):
            continue
        m = FOLDER_RE.match(e.name)
        if not m:
            continue
        out.append(_cand(
            CandidateSource.folder,
            title=m.group("slug").replace("-", " ").replace("_", " "),
            year=int(m.group("year")), venue=m.group("venue"),
            source_id=e.name,
        ))
    return out


def parse_publication_list(text: str) -> list[dict]:
    """Extract entries from a pasted publication list (M2-T3).

    Entries are separated by blank lines where the paste has them, and
    otherwise by lines that begin a new citation. Within an entry the year and
    any DOI are reliable; the **title is a guess**, taken as the longest
    sentence-like chunk that does not look like an author list. Each entry
    carries that confidence so the preview can show which ones to check.
    """
    blocks = _blocks(text)
    out: list[dict] = []
    for block in blocks:
        flat = " ".join(block.split())
        if len(flat) < 20:
            continue
        year_m = YEAR_RE.search(flat)
        doi_m = DOI_RE.search(flat)
        title, confident = _guess_title(flat)
        out.append({
            "title": title,
            "year": int(year_m.group(1)) if year_m else None,
            "doi": doi_m.group(0).rstrip(".,;") if doi_m else None,
            "raw": flat,
            "confident": confident,
        })
    return out


def _blocks(text: str) -> list[str]:
    """Split a paste into entries, tolerating both blank-line and run-on styles."""
    chunks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if len(chunks) > 1:
        return chunks
    # One block: assume one entry per line, which is the other common shape.
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _guess_title(flat: str) -> tuple[str, bool]:
    """Longest chunk that does not read as an author list or a venue tail.

    Same two-step as the CV page parser, for the same reason: splitting on
    every period breaks a title at "U.S.", and not splitting after a capital
    stops author initials being boundaries. So the author block goes first.
    """
    # An "Authors (Year)." preamble, when the paste uses that style.
    stripped = re.sub(r"^.*?\((?:19|20)\d{2}[a-z]?\)\.?\s*", "", flat, count=1)
    had_preamble = stripped != flat
    if not had_preamble:
        stripped = _strip_authors(flat)
    parts = [p.strip() for p in re.split(r"(?<![A-Z])\.\s+", stripped) if p.strip()]
    parts = [p for p in parts if len(p) > 12]
    if not parts:
        return flat[:200], False
    scored = []
    for p in parts:
        initials = len(INITIALS_RE.findall(p))
        commas = p.count(",")
        scored.append((len(p) - initials * 6 - commas * 2, p))
    scored.sort(reverse=True)
    best = scored[0][1].rstrip(" .")
    return best[:300], bool((had_preamble or stripped != flat) and len(best) > 25)


_AUTHOR_TAIL = re.compile(r"[A-Z]\.\s*,?\s*(?=[A-Z])")
_AUTHOR_ZONE = 0.6


def _strip_authors(raw: str) -> str:
    """Drop a leading author list. See `website._strip_authors` for the rule."""
    cutoff = len(raw) * _AUTHOR_ZONE
    end = None
    for m in _AUTHOR_TAIL.finditer(raw):
        if m.end() > cutoff:
            break
        end = m.end()
    return raw[end:].strip() if end else raw


def from_website(claim) -> list[WorkCandidate]:
    """Your public CV page's entries.

    Recorded for provenance only: `build.py` holds these out of grouping and
    overlays them onto works by title instead, because a CV line describes a
    work rather than asserting a separate one. The category comes from the
    section you filed it under, which is you classifying your own output and
    so outranks any inference from an index.

    Entries the page marks as not yet out -- the research pipeline -- are
    skipped: a paper under revision is not a work the ledger should count.
    """
    out: list[WorkCandidate] = []
    if claim is None:
        return out
    for e in claim.entries:
        if not e.published or not e.title:
            continue
        c = _cand(CandidateSource.site, title=e.title, year=e.year, doi=e.doi,
                  venue=e.section)
        c.work_type = e.category.value
        out.append(c)
    return out


def from_publication_list(text: str) -> list[WorkCandidate]:
    """Your own list, the one record you maintain yourself."""
    out: list[WorkCandidate] = []
    for e in parse_publication_list(text):
        if not e["title"]:
            continue
        out.append(_cand(
            CandidateSource.cv, title=e["title"], year=e["year"], doi=e["doi"],
        ))
    return out
