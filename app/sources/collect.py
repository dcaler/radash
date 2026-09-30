"""Read every source once, record what happened, and propose the join.

This is what `/api/sources` calls and what the weekly refresh will call in M4.
It exists as its own module rather than inside the router so the refresh job
and the endpoint cannot drift apart.

**Network sources are not refreshed by default.** A diagnostic that costs three
API round-trips every time the page is opened is a diagnostic nobody leaves
open, and it puts raDash's traffic on someone else's rate limit for no new
information. So the filesystem sources — which are local, fast, and the ones
that actually change between refreshes — are read live, while trundlr, OpenAlex
and S2 are served from cache unless `refresh=True`. The report says which of
the two happened for every source, so "cached" is never mistaken for "current".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session

from app import settings
from app.config import Config
from app.models import SourceState, SourceStatus
from app.sources import SourceReport, cache, utcnow
from app.sources import haarpi as haarpi_src
from app.sources import matcher as matcher_mod
from app.sources import openalex as openalex_src
from app.sources import scholar as scholar_src
from app.sources import trundlr as trundlr_src
from app.sources import website as website_src
from app.sources import zotero as zotero_src

# Sources that cost a network round-trip, and so are cached by default.
NETWORK_SOURCES = ("trundlr", "openalex", "s2", "website")


@dataclass
class Collection:
    reports: dict[str, SourceReport] = field(default_factory=dict)
    join: Optional[matcher_mod.MatchReport] = None
    refreshed: bool = False
    # Whether the network was deliberately not touched, as opposed to touched
    # and unreachable. Those are different facts and read differently.
    offline: bool = False

    def report(self, key: str) -> Optional[SourceReport]:
        return self.reports.get(key)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.reports.values())


def collect(cfg: Config, session: Optional[Session] = None,
            refresh: bool = False) -> Collection:
    """Read what is cheap, fetch what was asked for, and join the result.

    Offline is a normal state, not an error one. With `OFFLINE` set, a refresh
    is downgraded here rather than refused at each adapter: every network
    source falls back to its cache with the age attached, and the response
    says the fetch was not attempted. One choke point, so there is no adapter
    left to forget — a mode that holds in five places out of six is not a mode.
    """
    offline = settings.flag("OFFLINE")
    out = Collection(refreshed=refresh and not offline, offline=offline)
    if offline:
        refresh = False

    out.reports[zotero_src.KEY] = zotero_src.read(cfg)
    out.reports[haarpi_src.KEY] = haarpi_src.read(cfg)
    out.reports[scholar_src.KEY] = scholar_src.read(cfg)

    # The public CV page is fetched on refresh like any other remote source.
    out.reports[website_src.KEY] = (
        website_src.read(cfg) if refresh
        else SourceReport(
            key=website_src.KEY, status=SourceStatus.missing,
            note=("offline — your CV page was not fetched" if offline
                  else "not fetched yet — refresh to read your CV page")))

    if refresh:
        out.reports[trundlr_src.KEY] = trundlr_src.read(cfg, session)
        out.reports[openalex_src.KEY] = openalex_src.read(cfg, session)
    else:
        out.reports[trundlr_src.KEY] = _from_cache_only(
            cfg, session, trundlr_src.KEY, trundlr_src)
        out.reports[openalex_src.KEY] = _from_cache_only(
            cfg, session, openalex_src.KEY, openalex_src)

    # S2 is driven by the works ledger, which does not exist until M2. Until
    # then it reports what it is waiting for rather than pretending to be down.
    dois = _known_dois(out)
    if refresh and dois:
        from app.sources import s2 as s2_src
        out.reports[s2_src.KEY] = s2_src.read(cfg, dois, session)
    else:
        out.reports["s2"] = _s2_placeholder(cfg, session, dois)

    out.join = _join(out)
    _persist(session, out)
    return out


def _from_cache_only(cfg: Config, session, key: str, module) -> SourceReport:
    """Serve a network source from cache without touching the network."""
    report = module._fall_back(SourceReport(key=key), session, "not refreshed")
    if report.status is SourceStatus.stale:
        payload_age = report.source_mtime
        age = ""
        if payload_age:
            hours = (utcnow() - payload_age).total_seconds() / 3600
            age = f" ({hours:.1f}h old)"
        report.note = f"served from cache{age}; POST /api/sources/refresh to update"
        report.failures = [f for f in report.failures if f != "not refreshed"]
    elif report.status is SourceStatus.error:
        # Never fetched is not the same as unreachable. Until someone presses
        # refresh there is nothing to be wrong about.
        report.status = SourceStatus.missing
        report.note = "not fetched yet — refresh to populate"
        report.failures = []
    return report


def _s2_placeholder(cfg: Config, session, dois: list[str]) -> SourceReport:
    from app.sources import s2 as s2_src
    payload, fetched_at = cache.load(session, s2_src.KEY)
    if isinstance(payload, dict) and payload:
        report = s2_src._fall_back(SourceReport(key=s2_src.KEY), session,
                                   "not refreshed")
        # Not refreshed is not unreachable, and the fallback words both the
        # same way. Saying a source is down when nobody asked it anything is
        # the kind of false alarm that teaches you to ignore the panel.
        age = ""
        if report.source_mtime:
            hours = (utcnow() - report.source_mtime).total_seconds() / 3600
            age = f" ({hours:.1f}h old)"
        report.note = (f"served from cache{age}; refresh to look up citations "
                       "again")
        report.failures = [f for f in report.failures if f != "not refreshed"]
        return report
    report = SourceReport(key=s2_src.KEY, status=SourceStatus.missing)
    report.note = (f"{len(dois)} DOI(s) available to look up; S2 is fetched on refresh"
                   if dois else "waiting on the works ledger (M2) for DOIs")
    report.counts = {"dois_available": len(dois), "keyed": 1 if s2_src.api_key() else 0}
    return report


def _known_dois(out: Collection) -> list[str]:
    """DOIs raDash can currently name — from OpenAlex, else from Zotero."""
    oa = out.reports.get(openalex_src.KEY)
    if oa is not None and oa.data is not None:
        dois = sorted(oa.data.distinct_dois)
        if dois:
            return dois
    z = out.reports.get(zotero_src.KEY)
    if z is not None and z.data is not None:
        return sorted({i.doi for i in z.data.items if i.doi})
    return []


def _join(out: Collection) -> Optional[matcher_mod.MatchReport]:
    """Propose the three-way join from whatever is present.

    A collection you use to mark reading is not a research project, and
    reporting it as "reading with no project behind it" would put noise in the
    one panel that exists to find exactly that.
    """
    z = out.reports.get(zotero_src.KEY)
    t = out.reports.get(trundlr_src.KEY)
    h = out.reports.get(haarpi_src.KEY)
    collections = z.data.collections if (z and z.data) else []
    marker = settings.text("READ_COLLECTION", "").strip().casefold()
    if marker:
        collections = [c for c in collections
                       if (c.name or "").strip().casefold() != marker]
    projects = t.data.projects if (t and t.data) else []
    projects_h = h.data.projects if (h and h.data) else []
    if not (collections or projects or projects_h):
        return None
    return matcher_mod.match(collections, projects, projects_h)


def _persist(session: Optional[Session], out: Collection) -> None:
    """Record each source's state so staleness survives a restart."""
    if session is None:
        return
    now = datetime.now(timezone.utc)
    for key, report in out.reports.items():
        row = session.get(SourceState, key) or SourceState(key=key)
        row.status = report.status
        row.last_read_at = now
        if report.ok:
            row.last_ok_at = now
        row.item_count = report.count
        row.note = report.note
        row.updated_at = now
        session.add(row)
    session.commit()
