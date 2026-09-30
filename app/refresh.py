"""The weekly cycle, and what it throws away — M8-T1.

raDash refreshes **weekly**, not continuously (`DESIGN.md` §6). Nothing it
watches changes on a timescale that rewards polling: citations accrue over
months, a project's stage moves in days, the corpus grows a few items a week.
The cadence removes API pressure entirely, makes cache-first rendering the
normal mode rather than a fallback, and turns each refresh into a snapshot —
which is what makes "what changed since last week" answerable at all.

**One cycle, in dependency order.** Sources, then the ledger, then the map,
then the frontier. Each step's output is the next one's input, and a step that
fails is recorded and stepped over rather than taking the cycle down: a
frontier that could not be gathered should cost you the frontier panel, not
the snapshot.

**Retention is twelve.** Snapshots and fitted spaces are both derived state —
rebuildable from the sources and the standing rulings — and both are large:
one space carries a row per document plus a vector array beside the database.
Twelve weeks is long enough to see a season and short enough that a NAS volume
does not quietly fill.

What retention must never touch: `WorkDecision` and `WorkClassification`. They
are not snapshot-scoped precisely so that a refresh may replace every number
and never a decision, and pruning them would lose a ruling you made in March
to a tidy-up in September.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from app import settings
from app.config import Config
from app.models import (MapAxis, MapCluster, MapPoint, MapSpace, Snapshot,
                        Work, WorkCandidate)

# How many snapshots and fitted spaces survive a cycle.
RETAIN = 12

# Days before the scheduler considers the picture stale enough to refresh.
REFRESH_DAYS = 7

# Never prune below this, whatever the setting says. Lineage matching reads
# the previous space to decide whether a region is the same region, and a
# retention policy that left one space would make every refit read as
# wholesale upheaval.
MIN_RETAIN = 2


@dataclass
class Step:
    name: str
    ok: bool = True
    seconds: float = 0.0
    detail: Optional[str] = None
    error: Optional[str] = None

    def as_dict(self) -> dict:
        return {"step": self.name, "ok": self.ok,
                "seconds": round(self.seconds, 2),
                "detail": self.detail, "error": self.error}


@dataclass
class Cycle:
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    steps: list = field(default_factory=list)
    pruned: dict = field(default_factory=dict)
    seconds: float = 0.0
    trigger: str = "manual"

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.steps)

    def summary(self) -> dict:
        return {
            "trigger": self.trigger,
            "started_at": self.started_at.isoformat(),
            "seconds": round(self.seconds, 2),
            "ok": self.ok,
            "steps": [s.as_dict() for s in self.steps],
            "pruned": self.pruned,
            "failed": [s.name for s in self.steps if not s.ok],
            "note": ("A step that failed costs you its panel, not the "
                     "snapshot. Every panel dates itself, so a stale one says "
                     "so rather than going blank."),
        }


def cycle(cfg: Config, db: Session, trigger: str = "manual",
          fit: bool = True, gather: bool = True) -> Cycle:
    """Everything a weekly refresh does, in the order it has to happen."""
    out = Cycle(trigger=trigger)
    started = time.perf_counter()

    def step(name, fn):
        s = Step(name=name)
        began = time.perf_counter()
        try:
            s.detail = fn()
        except Exception as exc:                  # a step never takes the cycle down
            s.ok = False
            s.error = f"{type(exc).__name__}: {exc}"
        s.seconds = time.perf_counter() - began
        out.steps.append(s)
        return s

    step("sources", lambda: _sources(cfg, db))
    step("ledger", lambda: _ledger(cfg, db))
    if fit:
        step("map", lambda: _map(cfg, db))
    if gather:
        step("frontier", lambda: _frontier(cfg, db))

    out.pruned = prune(cfg, db)
    out.seconds = time.perf_counter() - started
    return out


def _sources(cfg: Config, db: Session) -> str:
    from app.sources import collect as collect_mod

    result = collect_mod.collect(cfg, db, refresh=True)
    states = {k: r.status.value for k, r in result.reports.items()}
    return ", ".join(f"{k} {v}" for k, v in sorted(states.items()))


def _ledger(cfg: Config, db: Session) -> str:
    from app.ledger import build as build_mod
    from app.routers.ledger import PUBLICATIONS_KIND
    from app.sources import collect as collect_mod
    from app.sources import imports

    sources = collect_mod.collect(cfg, db, refresh=False)   # the cache just filled
    pubs = imports.read_latest(cfg.import_dir, PUBLICATIONS_KIND)
    result = build_mod.build(cfg, sources, db, publication_list=pubs,
                             label="weekly")
    summary = result.summary()
    return (f"{summary.get('works', 0)} works, "
            f"{len(result.proposals)} proposals open")


def _map(cfg: Config, db: Session) -> str:
    from app.mapping import fit as fit_mod
    from app.routers.map import _gather

    corpus_docs, placed_docs, weights, _ = _gather(cfg, db, refresh=False)
    if not corpus_docs:
        return "the corpus is not readable; the previous space stands"
    result = fit_mod.build(cfg, db, corpus_docs, placed_docs, weights=weights)
    if result.space_id is None:
        return result.note or "the fit produced nothing"
    return (f"space {result.space_id}: {result.clusters} regions, "
            f"{result.noise} unclustered")


def _frontier(cfg: Config, db: Session) -> str:
    from app.routers.frontier import refresh as frontier_refresh

    result = frontier_refresh(db=db)
    return (f"{result['items']} papers across {result['regions']} regions"
            + (f", {len(result['failures'])} skipped" if result["failures"]
               else ""))


# --- retention --------------------------------------------------------------


def prune(cfg: Config, db: Session, keep: Optional[int] = None) -> dict:
    """Drop all but the most recent `keep` snapshots and fitted spaces.

    Children first, because SQLite foreign keys are enforced here and a
    snapshot with works still pointing at it will not delete. Rulings are
    untouched by construction: they are keyed by fingerprint and scoped to no
    snapshot, which is the whole reason they are a separate table.
    """
    keep = max(int(keep or settings.num("RETAIN_SNAPSHOTS", RETAIN)), MIN_RETAIN)
    return {"snapshots": _prune_snapshots(db, keep),
            "spaces": _prune_spaces(cfg, db, keep)}


def _prune_snapshots(db: Session, keep: int) -> int:
    rows = db.exec(select(Snapshot).order_by(Snapshot.id.desc())).all()
    doomed = [s for s in rows[keep:] if not s.is_current]
    for snap in doomed:
        for work in db.exec(select(Work)
                            .where(Work.snapshot_id == snap.id)).all():
            db.delete(work)
        for cand in db.exec(select(WorkCandidate)
                            .where(WorkCandidate.snapshot_id == snap.id)).all():
            db.delete(cand)
        db.delete(snap)
    if doomed:
        db.commit()
    return len(doomed)


def _prune_spaces(cfg: Config, db: Session, keep: int) -> int:
    rows = db.exec(select(MapSpace).order_by(MapSpace.id.desc())).all()
    doomed = [s for s in rows[keep:] if not s.is_current]
    for space in doomed:
        for table in (MapPoint, MapCluster, MapAxis):
            for row in db.exec(select(table)
                               .where(table.space_id == space.id)).all():
                db.delete(row)
        _drop_vectors(cfg, space)
        db.delete(space)
    if doomed:
        db.commit()
    return len(doomed)


def _drop_vectors(cfg: Config, space: MapSpace) -> None:
    """The array and the fitted model beside the database.

    Inside raDash's own volume, like everything else it writes. A failure here
    costs disk, not correctness, so it is swallowed rather than raised.
    """
    from app.mapping.fit import VECTORS_DIR

    directory = Path(cfg.output_dir).parent / VECTORS_DIR
    for name in (f"space_{space.id}.npz", f"space_{space.id}.model"):
        try:
            (directory / name).unlink(missing_ok=True)
        except OSError:
            pass


# --- staleness --------------------------------------------------------------


def days_since_last(db: Session) -> Optional[float]:
    """Age of the newest snapshot, which is what the scheduler ticks against."""
    newest = db.exec(select(Snapshot).order_by(Snapshot.id.desc())).first()
    if newest is None:
        return None
    created = newest.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created).total_seconds() / 86400


def is_due(db: Session) -> bool:
    """Whether a cycle is owed, measured against the data rather than uptime.

    Ticking on elapsed process time would mean a container that restarts more
    often than weekly never refreshes at all, and one that restarts rarely
    refreshing at an arbitrary hour. The snapshot's own age is the honest
    clock, and it survives a redeploy.
    """
    every = settings.num("REFRESH_DAYS", REFRESH_DAYS)
    if every <= 0:
        return False
    age = days_since_last(db)
    return age is None or age >= every
