"""OpenAlex — the automated citation lane, and the only source of accrual.

Two things come from here that nothing else in raDash can supply:

**`counts_by_year`.** A citation total cannot say whether a paper is alive. The
per-year series can, and it is the input to the accrual and momentum panels
(`DESIGN.md` §6) — which is why this adapter keeps the series even when the
total is all that gets displayed.

**Abstracts for work raDash did not read.** Frontier papers are not in Zotero
by definition, so their text has to come from somewhere; OpenAlex ships an
inverted index, which `reconstruct_abstract` turns back into prose.

Two author profiles are configured, and they overlap. This adapter deliberately
does **not** merge them — it returns both sets with their provenance intact and
leaves identity to M2's ledger, where a human confirms it. Summing two profiles
here would bake the double-count into every number downstream.

Requests go through the polite pool: OpenAlex asks for a `mailto` and gives
better service for it. `CONTACT_EMAIL` supplies it; without one the adapter
still works and says it is anonymous.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
from sqlmodel import Session

from app import settings
from app.config import Config
from app.models import SourceStatus
from app.sources import SourceReport, timer
from app.ledger.fingerprint import normalize_doi
from app.sources import cache

KEY = "openalex"

BASE = "https://api.openalex.org"
PER_PAGE = 200
MAX_PAGES = 20          # 4,000 works; a runaway cursor stops here
TIMEOUT = 20.0

# Only the fields raDash uses. Asking for fewer fields makes the response
# smaller and the API faster, and documents the dependency surface.
SELECT = ",".join((
    "id", "doi", "title", "display_name", "publication_year", "publication_date",
    "type", "cited_by_count", "counts_by_year", "authorships",
    "primary_location", "open_access", "abstract_inverted_index", "topics",
    "referenced_works_count", "is_retracted",
))


@dataclass
class OpenAlexWork:
    id: str
    doi: Optional[str] = None
    title: str = ""
    year: Optional[int] = None
    date: Optional[str] = None
    type: Optional[str] = None
    venue: Optional[str] = None
    cited_by_count: int = 0
    counts_by_year: dict[int, int] = field(default_factory=dict)
    authors: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    abstract: str = ""
    is_retracted: bool = False
    source_author_id: Optional[str] = None   # which profile produced this row

    @property
    def short_id(self) -> str:
        return self.id.rsplit("/", 1)[-1] if self.id else ""

    def citations_since(self, year: int) -> int:
        return sum(n for y, n in self.counts_by_year.items() if y >= year)

    def momentum(self, current_year: int) -> Optional[float]:
        """Share of citations earned in the last 24 months.

        Meaningless without the publication year beside it — a 2024 paper reads
        1.0 by construction — so the panel that shows this always shows `year`.
        """
        if not self.cited_by_count:
            return None
        return self.citations_since(current_year - 1) / self.cited_by_count


@dataclass
class OpenAlexProfile:
    author_id: str
    works: list[OpenAlexWork] = field(default_factory=list)
    total: int = 0

    @property
    def citation_sum(self) -> int:
        return sum(w.cited_by_count for w in self.works)


@dataclass
class OpenAlexResult:
    profiles: list[OpenAlexProfile] = field(default_factory=list)
    from_cache: bool = False

    @property
    def works(self) -> list[OpenAlexWork]:
        """Every row from every profile, un-deduplicated and labelled."""
        return [w for p in self.profiles for w in p.works]

    @property
    def distinct_dois(self) -> set[str]:
        return {w.doi for w in self.works if w.doi}


def reconstruct_abstract(inverted: Optional[dict]) -> str:
    """Rebuild prose from OpenAlex's `{word: [positions]}` index.

    Gaps are possible when the index is partial, so positions are filled into a
    sparse list and empty slots dropped rather than assumed contiguous.
    """
    if not isinstance(inverted, dict) or not inverted:
        return ""
    positions: dict[int, str] = {}
    for word, idxs in inverted.items():
        if not isinstance(idxs, list):
            continue
        for i in idxs:
            if isinstance(i, int):
                positions[i] = word
    if not positions:
        return ""
    return " ".join(positions[i] for i in sorted(positions))


def _work(raw: dict, author_id: Optional[str] = None) -> OpenAlexWork:
    loc = raw.get("primary_location") or {}
    src = (loc.get("source") or {}) if isinstance(loc, dict) else {}
    counts = {}
    for c in raw.get("counts_by_year") or []:
        if isinstance(c, dict) and isinstance(c.get("year"), int):
            counts[c["year"]] = int(c.get("cited_by_count") or 0)
    doi = raw.get("doi") or ""
    return OpenAlexWork(
        id=raw.get("id") or "",
        doi=doi.replace("https://doi.org/", "").strip().lower() or None,
        title=raw.get("title") or raw.get("display_name") or "",
        year=raw.get("publication_year"),
        date=raw.get("publication_date"),
        type=raw.get("type"),
        venue=(src.get("display_name") if isinstance(src, dict) else None),
        cited_by_count=int(raw.get("cited_by_count") or 0),
        counts_by_year=counts,
        authors=[
            (a.get("author") or {}).get("display_name", "")
            for a in (raw.get("authorships") or []) if isinstance(a, dict)
        ],
        topics=[
            t.get("display_name", "") for t in (raw.get("topics") or [])
            if isinstance(t, dict)
        ],
        abstract=reconstruct_abstract(raw.get("abstract_inverted_index")),
        is_retracted=bool(raw.get("is_retracted")),
        source_author_id=author_id,
    )


def _params(cfg: Config, **extra) -> dict:
    p = {"per-page": PER_PAGE, "select": SELECT}
    if cfg.contact_email:
        p["mailto"] = cfg.contact_email   # the polite pool
    p.update(extra)
    return p


def read(cfg: Config, session: Optional[Session] = None) -> SourceReport:
    """Fetch every work for each configured author profile, kept separate."""
    report = SourceReport(key=KEY)
    ids = cfg.openalex_author_ids
    if not ids:
        report.status = SourceStatus.missing
        report.note = "OPENALEX_AUTHOR_IDS is unset"
        return report

    result = OpenAlexResult()
    raw_payload: dict[str, list] = {}
    try:
        with timer(report):
            with httpx.Client(timeout=TIMEOUT, headers=_headers(cfg)) as client:
                for author_id in ids:
                    try:
                        rows, total = _fetch_author(client, cfg, author_id)
                    except Exception as exc:
                        report.degrade(f"{author_id}: {type(exc).__name__}: {exc}")
                        continue
                    raw_payload[author_id] = rows
                    result.profiles.append(OpenAlexProfile(
                        author_id=author_id,
                        works=[_work(r, author_id) for r in rows],
                        total=total,
                    ))
    except Exception as exc:
        return _fall_back(report, session, f"{type(exc).__name__}: {exc}")

    if not result.profiles:
        return _fall_back(report, session, report.note or "no profile could be read")

    cache.save(session, KEY, raw_payload, item_count=len(result.works))
    report.data = result
    report.count = len(result.works)
    report.source_mtime = report.read_at
    report.counts = _counts(result)
    if report.status is SourceStatus.missing:
        report.status = SourceStatus.ok
    if len(result.profiles) < len(ids):
        report.status = SourceStatus.degraded
    return report


def _fetch_author(client: httpx.Client, cfg: Config, author_id: str) -> tuple[list, int]:
    """Cursor-paginate one author's works."""
    rows: list[dict] = []
    cursor = "*"
    total = 0
    for _ in range(MAX_PAGES):
        params = _params(cfg, filter=f"author.id:{author_id}", cursor=cursor)
        resp = client.get(f"{BASE}/works", params=params)
        resp.raise_for_status()
        body = resp.json()
        meta = body.get("meta") or {}
        total = int(meta.get("count") or 0)
        page = body.get("results") or []
        rows.extend(r for r in page if isinstance(r, dict))
        cursor = meta.get("next_cursor")
        if not cursor or not page:
            break
    return rows, total


def _counts(result: OpenAlexResult) -> dict[str, int]:
    works = result.works
    counts = {
        "works_rows": len(works),
        "distinct_dois": len(result.distinct_dois),
        "citations_summed": sum(w.cited_by_count for w in works),
        "with_abstract": sum(1 for w in works if w.abstract),
        "with_accrual": sum(1 for w in works if w.counts_by_year),
        "retracted": sum(1 for w in works if w.is_retracted),
    }
    for p in result.profiles:
        counts[f"profile_{p.author_id}"] = len(p.works)
    return counts


def _fall_back(report: SourceReport, session: Optional[Session], why: str) -> SourceReport:
    payload, fetched_at = cache.load(session, KEY)
    if not isinstance(payload, dict):
        report.fail(f"OpenAlex unreachable and nothing cached ({why})")
        return report
    result = OpenAlexResult(from_cache=True, profiles=[
        OpenAlexProfile(author_id=aid, works=[_work(r, aid) for r in rows], total=len(rows))
        for aid, rows in payload.items() if isinstance(rows, list)
    ])
    report.data = result
    report.count = len(result.works)
    report.counts = _counts(result)
    report.status = SourceStatus.stale
    report.source_mtime = fetched_at
    report.note = f"OpenAlex unreachable — serving cache ({why})"
    report.failures.append(why)
    return report


def _headers(cfg: Config) -> dict[str, str]:
    who = cfg.contact_email or "anonymous"
    return {"User-Agent": f"raDash/1.0 (research portfolio observer; {who})"}


# --- corpus enrichment ------------------------------------------------------

# Keyed separately from the earlier abstract-only cache: the payload shape
# changed, and silently reading an old one as a new one is how a cache starts
# lying.
ENRICHMENT_KEY = "openalex_enrichment"

# What a cached row must contain to count as fetched. Adding a field here is
# enough to make the next refresh backfill it.
ENRICHMENT_FIELDS = ("abstract", "cited_by", "year", "topics")

# OpenAlex accepts an OR filter of this many values per request. 2,000 DOIs
# become forty requests rather than two thousand, which is the difference
# between a polite read and a hammering.
DOI_BATCH = 50

# A ceiling, so a corpus that grows unexpectedly cannot turn one refresh into
# an hour of somebody else's rate limit.
MAX_BATCHES = 80


def enrich_dois(dois, cfg: Config, session: Optional[Session] = None,
                force: bool = False) -> tuple[dict, dict]:
    """Fetch what OpenAlex knows about other people's work, by DOI.

    This is what makes the map possible on a library where nearly half the
    items carry no abstract. Zotero records what a reference manager is given,
    which for many items is a title and a venue; OpenAlex has the abstract for
    a large share of those same DOIs. Without this the fit sees a title and
    calls the item thin, and the corpus loses a third of itself to a field one
    source happens to be missing.

    Cached across runs and fetched incrementally: only DOIs never looked up
    before cost a request, so a weekly refresh asks about what is new.
    Returns `(abstracts, stats)` and never raises — a failed batch costs those
    abstracts, not the fit.
    """
    wanted = {normalize_doi(d) for d in dois if d}
    wanted.discard("")
    cached, _ = cache.load(session, ENRICHMENT_KEY)
    known: dict = dict(cached) if isinstance(cached, dict) else {}

    # An entry written before a field existed is incomplete, not present.
    # Skipping every DOI already in the cache meant that adding `topics` to
    # the request changed nothing: the rows were there, the field was not, and
    # every region resolved to no topics at all. A cache keyed the same while
    # its payload grows has to know what a complete row looks like.
    def _complete(row) -> bool:
        return isinstance(row, dict) and all(
            k in row for k in ENRICHMENT_FIELDS)

    missing = sorted(wanted if force
                     else {d for d in wanted if not _complete(known.get(d))})
    stats = {"requested": len(wanted), "already_known": len(wanted) - len(missing),
             "fetched": 0, "found": 0, "batches": 0, "failures": 0}
    if not missing:
        return {d: known[d] for d in wanted if known.get(d)}, stats

    # Offline: serve what was already looked up and say the rest was not
    # attempted. A map fitted on a partly-enriched corpus is a real map with
    # a stated gap; one that stalls on a socket is nothing at all.
    if settings.flag("OFFLINE"):
        stats["offline"] = True
        stats["not_attempted"] = len(missing)
        return {d: known[d] for d in wanted if known.get(d)}, stats

    batches = [missing[i:i + DOI_BATCH] for i in range(0, len(missing), DOI_BATCH)]
    if len(batches) > MAX_BATCHES:
        batches = batches[:MAX_BATCHES]
        stats["truncated"] = True

    try:
        with httpx.Client(timeout=TIMEOUT, headers=_headers(cfg)) as client:
            for batch in batches:
                stats["batches"] += 1
                try:
                    rows = _fetch_dois(client, cfg, batch)
                except Exception:
                    stats["failures"] += 1
                    continue
                for raw in rows:
                    doi = normalize_doi(raw.get("doi") or "")
                    if not doi:
                        continue
                    text = reconstruct_abstract(raw.get("abstract_inverted_index"))
                    # Recorded even when empty, so a work without an abstract
                    # is not asked about again every week.
                    known[doi] = {
                        "abstract": text,
                        "cited_by": raw.get("cited_by_count"),
                        "year": raw.get("publication_year"),
                        # What OpenAlex thinks this paper is about. A region's
                        # topics are then its members' topics, which is
                        # grounded in the papers rather than in a keyword
                        # search over its label.
                        "topics": [
                            {"id": (t.get("id") or "").rsplit("/", 1)[-1],
                             "name": t.get("display_name")}
                            for t in (raw.get("topics") or [])[:3]
                            if isinstance(t, dict)
                        ],
                    }
                    if text:
                        stats["found"] += 1
                stats["fetched"] += len(batch)
    except Exception:
        stats["failures"] += 1

    cache.save(session, ENRICHMENT_KEY, known, item_count=len(known))
    return {d: known[d] for d in wanted if known.get(d)}, stats


def _fetch_dois(client: httpx.Client, cfg: Config, dois: list) -> list:
    # cited_by_count rides along on the same requests. A reading list has to
    # rank somehow, and how much the field has taken up a paper is the one
    # measure of consequence available for work you have not read yet.
    params = _params(cfg, filter="doi:" + "|".join(dois),
                     select="doi,abstract_inverted_index,cited_by_count,"
                            "publication_year,topics")
    params["per-page"] = len(dois)
    resp = client.get(f"{BASE}/works", params=params)
    resp.raise_for_status()
    return [r for r in (resp.json().get("results") or []) if isinstance(r, dict)]


def abstracts_for_dois(dois, cfg: Config, session: Optional[Session] = None,
                       force: bool = False) -> tuple[dict, dict]:
    """Just the abstracts, for callers that want only text."""
    enriched, stats = enrich_dois(dois, cfg, session, force)
    return ({doi: row.get("abstract", "") for doi, row in enriched.items()
             if isinstance(row, dict) and row.get("abstract")}, stats)
