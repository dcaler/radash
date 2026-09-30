"""trundlr reader — the project ledger, over REST, degrading to cache.

trundlr is the authoritative list of what work exists and how it is
prioritised. It typically runs on a separate host across a private network, so
*unreachable* is a normal operating state here rather than an incident: the
machine sleeps, or the link is not up yet.

The response to unreachable is to serve the last good payload and label it
`stale` with its age. A dashboard that blanks when the network hiccups trains
you to distrust it; one that says "27 projects, as of Tuesday" stays useful and
stays honest. Only when there is no cache at all does this report `error`.

raDash issues `GET` only. trundlr's API has archive, copy, reflow and claim
endpoints; none are reachable from here, and `tests/test_sources_readonly.py`
asserts the client's verb list is exactly `{"GET"}`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import httpx
from sqlmodel import Session

from app.config import Config
from app.models import SourceStatus
from app.sources import SourceReport, timer, utcnow
from app.sources import cache

KEY = "trundlr"

# The only verb this adapter is permitted to use.
ALLOWED_METHODS = frozenset({"GET"})

# trundlr's routes are declared with a trailing slash and answer 307 without
# one, so the slash is part of the path here rather than a redirect to follow.
PROJECTS_PATH = "/api/projects/"
TASKS_PATH = "/api/tasks/"
VERSION_PATH = "/api/version"

TIMEOUT = 8.0


@dataclass
class TrundlrProject:
    id: int
    name: str
    priority: Optional[int] = None
    folder: Optional[str] = None
    description: Optional[str] = None
    archived: bool = False
    created_at: Optional[str] = None
    task_count: int = 0
    last_task_at: Optional[str] = None

    @classmethod
    def from_api(cls, d: dict) -> "TrundlrProject":
        return cls(
            id=d.get("id"),
            name=d.get("name") or "",
            priority=d.get("priority"),
            folder=(d.get("folder") or "").strip() or None,
            description=d.get("description"),
            archived=bool(d.get("archived")),
            created_at=d.get("created_at"),
        )


@dataclass
class TrundlrLedger:
    projects: list[TrundlrProject] = field(default_factory=list)
    version: Optional[str] = None
    from_cache: bool = False
    cached_at: Optional[datetime] = None

    @property
    def active(self) -> list[TrundlrProject]:
        return [p for p in self.projects if not p.archived]


def _get(client: httpx.Client, base: str, path: str) -> Any:
    """One GET. The method is a literal so no caller can widen it."""
    assert "GET" in ALLOWED_METHODS
    resp = client.get(f"{base.rstrip('/')}{path}", follow_redirects=True)
    resp.raise_for_status()
    return resp.json()


def read(cfg: Config, session: Optional[Session] = None) -> SourceReport:
    """Read the project list, falling back to the cache when trundlr is away."""
    report = SourceReport(key=KEY)
    base = cfg.trundlr_url
    payload: Optional[list] = None
    version: Optional[str] = None

    try:
        with timer(report):
            with httpx.Client(timeout=TIMEOUT, headers=_headers(cfg)) as client:
                payload = _get(client, base, PROJECTS_PATH)
                try:
                    version = (_get(client, base, VERSION_PATH) or {}).get("version")
                except Exception:
                    version = None  # version is decoration, not data
    except Exception as exc:
        return _fall_back(report, session, f"{type(exc).__name__}: {exc}", base)

    if not isinstance(payload, list):
        return _fall_back(report, session,
                          f"unexpected payload type {type(payload).__name__}", base)

    ledger = _ledger(payload, version)
    cache.save(session, KEY, payload, item_count=len(ledger.projects))

    report.status = SourceStatus.ok
    report.data = ledger
    report.count = len(ledger.projects)
    report.source_mtime = report.read_at  # a live read is as fresh as its read
    report.counts = _counts(ledger)
    return report


def _ledger(payload: list, version: Optional[str]) -> TrundlrLedger:
    projects = [TrundlrProject.from_api(d) for d in payload if isinstance(d, dict)]
    return TrundlrLedger(projects=projects, version=version)


def _counts(ledger: TrundlrLedger) -> dict[str, int]:
    counts = {
        "projects": len(ledger.projects),
        "active": len(ledger.active),
        "archived": len(ledger.projects) - len(ledger.active),
        "with_folder": sum(1 for p in ledger.projects if p.folder),
    }
    for pr in (1, 2, 3, 4):
        counts[f"priority_{pr}"] = sum(1 for p in ledger.active if p.priority == pr)
    return counts


def _fall_back(report: SourceReport, session: Optional[Session],
               why: str, base: str = "trundlr") -> SourceReport:
    """Unreachable: serve the last good payload, aged and labelled."""
    payload, fetched_at = cache.load(session, KEY)
    if payload is None:
        report.fail(f"{base} unreachable and nothing cached ({why})",
                    status=SourceStatus.error)
        return report

    ledger = _ledger(payload, version=None)
    ledger.from_cache = True
    ledger.cached_at = fetched_at
    report.data = ledger
    report.count = len(ledger.projects)
    report.counts = _counts(ledger)
    report.status = SourceStatus.stale
    report.source_mtime = fetched_at
    age = ""
    if fetched_at is not None:
        hours = (utcnow() - fetched_at).total_seconds() / 3600
        age = f", cached {hours:.1f}h ago"
    report.note = f"{base} unreachable{age} — serving cache ({why})"
    report.failures.append(why)
    return report


def _headers(cfg: Config) -> dict[str, str]:
    ua = "raDash/1.0 (read-only observer"
    if cfg.contact_email:
        ua += f"; {cfg.contact_email}"
    return {"User-Agent": ua + ")"}
