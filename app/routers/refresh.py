"""`POST /api/refresh` — the whole cycle, on demand — M8-T1.

The weekly tick and this endpoint run the same function, deliberately: a
scheduled path that differs from the one you can press is a path nobody
debugs. `DESIGN.md` §6 puts it plainly — refresh is a POST away as well as
weekly, so a rebuild never waits on the schedule.
"""
from fastapi import APIRouter, Depends, Query, Request
from sqlmodel import Session

from app import refresh as refresh_mod
from app.config import get_config
from app.database import get_db

router = APIRouter(prefix="/api", tags=["refresh"])


@router.post("/refresh")
def run_refresh(request: Request,
                fit: bool = Query(True, description="refit the map"),
                gather: bool = Query(True, description="gather the frontier"),
                db: Session = Depends(get_db)):
    """Sources, ledger, map, frontier — then prune to the last twelve.

    Synchronous, because it is measured in seconds to low minutes and a job
    queue for something you press once a week is its own maintenance burden.
    Each step is reported separately: a frontier that could not be gathered
    costs you the frontier panel, not the snapshot.
    """
    result = refresh_mod.cycle(get_config(), db, trigger="manual",
                               fit=fit, gather=gather)
    return result.summary()


@router.get("/refresh")
def refresh_status(request: Request, db: Session = Depends(get_db)):
    """When the last cycle ran, and whether one is owed."""
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        return scheduler.status()
    age = refresh_mod.days_since_last(db)
    return {"enabled": False, "running": False,
            "snapshot_age_days": round(age, 2) if age is not None else None,
            "due": refresh_mod.is_due(db),
            "note": "no scheduler is attached to this process"}
