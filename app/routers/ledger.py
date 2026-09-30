"""`/api/ledger` — the works ledger and the gate that settles it.

Read endpoints serve the current snapshot. The two write endpoints do different
things and the difference matters: `rebuild` re-reads the world and may change
every number, while `rule` records a judgement that no later rebuild may
overturn. That asymmetry is the milestone.
"""
import json
from datetime import timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlmodel import Session, select

from app import settings as settings_mod
from app.config import get_config
from app.database import get_db
from app.ledger import build as build_mod
from app.ledger import candidates as cand_mod
from app.ledger import links
from app.models import (
    LedgerRuling, Snapshot, Work, WorkCandidate, WorkCategory,
    WorkClassification, WorkDecision,
)
from app.sources import collect as collect_mod
from app.sources import imports

router = APIRouter(prefix="/api/ledger", tags=["ledger"])

PUBLICATIONS_KIND = "publications"


def _current(db: Session) -> Optional[Snapshot]:
    return db.exec(select(Snapshot).where(Snapshot.is_current)).first()


def _work_dict(w: Work) -> dict:
    def _json(raw, default):
        try:
            return json.loads(raw) if raw else default
        except (ValueError, TypeError):
            return default

    prov = _json(w.citations_provenance, {})
    return {
        "fingerprint": w.fingerprint,
        "title": w.title,
        "year": w.year,
        "venue": w.venue,
        "venues_seen": _json(w.venues_seen, []),
        "doi": w.doi,
        "url": links.doi_url(w.doi),
        "type": w.work_type,
        "category": w.category,
        "category_source": w.category_source,
        "on_cv": w.on_cv,
        "on_site": w.on_site,
        "confirmed": w.confirmed,
        # Two lanes, never blended. The manual lane is usually larger and always
        # older; showing them apart is what keeps that legible.
        "citations": {
            "automated": w.citations_automated,
            "manual": w.citations_manual,
            "provenance": prov,
        },
        "counts_by_year": _json(w.counts_by_year, {}),
        "sources": sorted({c.get("source") for c in prov.get("candidates", [])
                           if c.get("source")}),
        # Every way to go and look at the thing, deduplicated across the
        # candidates that claimed it.
        "links": [dict(t) for t in {
            tuple(sorted(l.items()))
            for c in prov.get("candidates", []) for l in (c.get("links") or [])
        }],
    }


@router.get("")
def ledger(db: Session = Depends(get_db)):
    """The current snapshot's works, with both citation lanes."""
    snap = _current(db)
    if snap is None:
        return {"snapshot": None, "works": [],
                "note": "no snapshot yet — POST /api/ledger/rebuild"}
    # A threshold changed after this snapshot was built silently changes what
    # the numbers below would be, so the page says so rather than showing them
    # as current.
    moved = settings_mod.changed_at(db)
    created = snap.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    stale_settings = bool(moved and created and moved > created)

    works = db.exec(select(Work).where(Work.snapshot_id == snap.id)).all()
    works = sorted(works, key=lambda w: (-(w.year or 0), w.title))
    return {
        "snapshot": {"id": snap.id, "created_at": snap.created_at.isoformat(),
                     "label": snap.label},
        "drift": json.loads(snap.drift) if snap.drift else None,
        "stale_settings": stale_settings,
        "works": [_work_dict(w) for w in works],
        "totals": {
            "works": len(works),
            "automated": sum(w.citations_automated or 0 for w in works),
            "manual": sum(w.citations_manual or 0 for w in works),
            "on_cv": sum(1 for w in works if w.on_cv),
            "unconfirmed": sum(1 for w in works if not w.confirmed),
            # The portfolio figure counts publications only. Talks, deposits
            # and preprints stay in the ledger and out of this number.
            "publications": sum(1 for w in works
                                if w.category == WorkCategory.publication.value),
        },
        "by_category": _by_category(works),
        "categories": [c.value for c in WorkCategory],
    }


def _by_category(works) -> dict:
    out: dict = {}
    for w in works:
        row = out.setdefault(w.category, {"works": 0, "automated": 0, "manual": 0})
        row["works"] += 1
        row["automated"] += w.citations_automated or 0
        row["manual"] += w.citations_manual or 0
    return out


@router.post("/rebuild")
def rebuild(refresh: bool = False, db: Session = Depends(get_db)):
    """Re-read the sources and rebuild the ledger.

    `refresh=false` by default, so a rebuild costs no API calls and replays the
    cached payloads — the common case while you are working through proposals.
    """
    cfg = get_config()
    sources = collect_mod.collect(cfg, db, refresh=refresh)
    pubs = imports.read_latest(cfg.import_dir, PUBLICATIONS_KIND)
    result = build_mod.build(cfg, sources, db, publication_list=pubs)
    return {"summary": result.summary(),
            "proposals": [p.as_dict() for p in result.proposals]}


@router.get("/proposals")
def proposals(db: Session = Depends(get_db)):
    """Open proposals for the gate, strongest first.

    Recomputed rather than stored: the engine is cheap, and a stored proposal
    would go stale against the rulings made since.
    """
    snap = _current(db)
    if snap is None:
        return {"proposals": [], "note": "no snapshot yet"}
    cands = db.exec(
        select(WorkCandidate).where(WorkCandidate.snapshot_id == snap.id)).all()
    decisions = db.exec(select(WorkDecision)).all()
    open_props, groups, excluded = build_mod._group(cands, decisions)
    open_props += build_mod._adoptions(groups, decisions, get_config())
    return {
        "snapshot_id": snap.id,
        "proposals": [p.as_dict() for p in open_props],
        "excluded": excluded,
        "note": ("Nothing here has moved a number. A proposal takes effect only "
                 "when you rule on it."),
    }


@router.post("/rule")
def rule(
    fingerprint: str = Body(..., embed=True),
    ruling: str = Body(..., embed=True),
    other_fingerprint: Optional[str] = Body(None, embed=True),
    rationale: Optional[str] = Body(None, embed=True),
    db: Session = Depends(get_db),
):
    """Record a judgement. Not snapshot-scoped: it outlives every refresh."""
    try:
        decided = LedgerRuling(ruling)
    except ValueError:
        raise HTTPException(422, f"unknown ruling {ruling!r}; expected one of "
                                 f"{[r.value for r in LedgerRuling]}")
    # `split` is inherently about two things. `confirmed` is not: with a pair
    # it means "these are one work", and alone it means "yes, this one is
    # mine" — the ruling an adoption proposal asks for, which has no second
    # side to name.
    if decided is LedgerRuling.split and not other_fingerprint:
        raise HTTPException(422, "split rules on a pair and needs "
                                 "other_fingerprint")

    existing = db.exec(
        select(WorkDecision)
        .where(WorkDecision.fingerprint == fingerprint)
        .where(WorkDecision.other_fingerprint == other_fingerprint)
    ).first()
    row = existing or WorkDecision(fingerprint=fingerprint,
                                   other_fingerprint=other_fingerprint)
    row.ruling = decided
    row.rationale = rationale
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"fingerprint": row.fingerprint, "other": row.other_fingerprint,
            "ruling": row.ruling.value, "rationale": row.rationale,
            "note": "Rebuild to see it applied."}


@router.post("/classify")
def classify(
    fingerprint: str = Body(..., embed=True),
    category: str = Body(..., embed=True),
    rationale: Optional[str] = Body(None, embed=True),
    db: Session = Depends(get_db),
):
    """Set a work's category. Outlives every refresh, like any other ruling.

    This is the answer to "that is mine, but it is not a publication" — a
    sentence the ledger previously had no way to express, leaving exclusion as
    the only exit and recording a falsehood to get a talk out of a list.
    """
    try:
        chosen = WorkCategory(category)
    except ValueError:
        raise HTTPException(422, f"unknown category {category!r}; expected one of "
                                 f"{[c.value for c in WorkCategory]}")
    row = db.exec(select(WorkClassification)
                  .where(WorkClassification.fingerprint == fingerprint)).first()
    row = row or WorkClassification(fingerprint=fingerprint)
    row.category = chosen
    row.rationale = rationale
    db.add(row)
    db.commit()
    return {"fingerprint": fingerprint, "category": chosen.value,
            "note": "Rebuild to see it applied."}


@router.get("/decisions")
def decisions(db: Session = Depends(get_db)):
    """Every standing ruling — the part of the ledger a refresh cannot touch."""
    rows = db.exec(select(WorkDecision)).all()
    return {"decisions": [
        {"fingerprint": r.fingerprint, "other": r.other_fingerprint,
         "ruling": r.ruling.value, "rationale": r.rationale,
         "decided_at": r.decided_at.isoformat()}
        for r in rows
    ]}


@router.post("/publications/preview")
def preview_publications(text: str = Body(..., embed=True)):
    """Show what the parser extracted, before anything is stored (M2-T3)."""
    try:
        imports.validate(text, PUBLICATIONS_KIND)
    except imports.ImportError_ as exc:
        raise HTTPException(422, str(exc))
    entries = cand_mod.parse_publication_list(text)
    return {
        "entries": entries,
        "count": len(entries),
        "uncertain": sum(1 for e in entries if not e["confident"]),
        "note": ("The title is a guess; the year and DOI are not. Check the "
                 "uncertain rows before importing."),
    }


@router.post("/publications/import")
def import_publications(text: str = Body(..., embed=True),
                        db: Session = Depends(get_db)):
    """Store the pasted list so every later rebuild reads it."""
    cfg = get_config()
    try:
        path = imports.save(cfg.import_dir, text, PUBLICATIONS_KIND)
    except imports.ImportError_ as exc:
        raise HTTPException(422, str(exc))
    except OSError as exc:
        raise HTTPException(500, f"could not write the import: {exc}")
    entries = cand_mod.parse_publication_list(text)
    return {"stored": path.name, "entries": len(entries),
            "note": "POST /api/ledger/rebuild to fold it into the ledger."}
