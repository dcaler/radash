"""`/api/milestones` — where each HAARPi project stands, as HAARPi reports it.

Each project's `.haarpi/status.json` is the source (see
`app/sources/milestones.py`). It already carries the release state, whose
move it is, the targets and the open trundlr work toward each milestone, so
this router only lists the projects, attaches each file's age, and orders
the result. Every figure on the page is HAARPi's.

The trundlr work in the file is as fresh as the file: it was read from the
board when the last `haarpi` verb ran. The age goes beside it for that
reason. The only thing taken from trundlr directly is whether the project is
archived there, from the cache, so nothing on this path touches the network.

Only HAARPi projects are listed — folders with a `haarpi.yaml`.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.config import get_config
from app.database import get_db
from app.sources import cache
from app.sources import haarpi as haarpi_src
from app.sources import milestones as milestones_src
from app.sources import trundlr as trundlr_src

router = APIRouter(prefix="/api/milestones", tags=["milestones"])

# Worst first. A project's headline health is its worst milestone's.
HEALTH_ORDER = ("late", "at_risk", "on_track", "no_target", "done")


def _age_days(dt: Optional[datetime]) -> Optional[float]:
    if dt is None:
        return None
    return round((datetime.now(timezone.utc) - dt).total_seconds() / 86400, 2)


def _project(p, status: milestones_src.StatusFile, tr: Optional[dict]) -> dict:
    ms = status.milestones
    # A release, once minted, stays minted: a stage reworked after its release
    # is in flight again but keeps the release in `released`.
    own = [m for m in ms if m.get("released")]
    healths = [m.get("health") for m in ms if m.get("health") in HEALTH_ORDER]
    meta = (status.payload or {}).get("project") or {}
    return {
        "folder": p.folder_name,
        "name": meta.get("name") or p.name,
        "trundlr_project_id": meta.get("trundlr_project_id") or p.trundlr_project_id,
        "priority": (tr or {}).get("priority") or meta.get("priority") or p.trundlr_priority,
        "archived": bool((tr or {}).get("archived")),
        "has_status": status.payload is not None,
        "note": status.note,
        "generated": (status.payload or {}).get("generated"),
        "generated_by": (status.payload or {}).get("generated_by"),
        "written_at": status.written_at.isoformat() if status.written_at else None,
        "age_days": _age_days(status.written_at),
        "trundlr": bool((status.payload or {}).get("trundlr")),
        "milestones": ms,
        "tracks": status.tracks,
        "done": sum(1 for m in ms if m.get("state") == "done"),
        # Releases on disk, as against done because something downstream is:
        # the second is HAARPi's inference, the first is a file.
        "released": len(own),
        "total": len(ms),
        "current": next((m["id"] for m in ms if m.get("state") != "done"), None),
        "health": min(healths, key=HEALTH_ORDER.index) if healths else None,
    }


@router.get("")
def milestones(db: Session = Depends(get_db)):
    """Every HAARPi project's milestones, from the status file HAARPi writes."""
    report = haarpi_src.read(get_config())
    corpus = report.data
    projects = [p for p in (corpus.projects if corpus else []) if p.is_haarpi]

    tr_payload, _ = cache.load(db, trundlr_src.KEY)
    tr_projects = {d.get("id"): d for d in (tr_payload or []) if isinstance(d, dict)}

    rows = []
    for p in projects:
        status = milestones_src.read(p.folder)
        tid = ((status.payload or {}).get("project") or {}).get("trundlr_project_id") \
            or p.trundlr_project_id
        rows.append(_project(p, status, tr_projects.get(tid)))

    rows.sort(key=lambda r: (r["archived"], not r["has_status"],
                             r["priority"] or 9, r["name"].casefold()))
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "source": "each project's .haarpi/status.json, written by HAARPi",
        "folders_status": report.status.value,
        "folders_note": report.note,
        "projects": rows,
    }
