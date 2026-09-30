"""`/api/sources` — what raDash can see, how old it is, and what failed.

M0's `/api/mounts` answered *are the sources visible and read-only*. This
answers the next question: *is what they contain actually usable*. Counts,
staleness and parse failures per source, plus the proposed three-way join, so
a coverage problem is visible here before it becomes a wrong number on a panel.

The read is cheap by default — local sources live, network sources from cache.
`POST /api/sources/refresh` is the explicit, deliberate network fetch.
"""
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlmodel import Session

from app.config import get_config
from app.database import get_db
from app.sources import collect as collect_mod
from app.sources import imports
from app.sources import scholar as scholar_src

router = APIRouter(prefix="/api/sources", tags=["sources"])


def _payload(result: collect_mod.Collection) -> dict:
    sources = [result.reports[k].summary() for k in sorted(result.reports)]
    # `missing` is not a failure. M0 settled this for mounts — "raDash runs
    # with sources absent and degrades the panels that need them" — and the
    # same holds for a source that has simply not been fetched or imported
    # yet. Reporting those as failures on a fresh deploy tells you something
    # is broken when the honest answer is that you have not pressed refresh.
    failing = [s["key"] for s in sources if s["status"] == "error"]
    waiting = [s["key"] for s in sources if s["status"] == "missing"]
    body = {
        "sources": sources,
        "refreshed": result.refreshed,
        # Deliberately not touched, as against touched and unreachable. The
        # panel reads very differently and so should the reader.
        "offline": result.offline,
        "ok": not failing,
        "failing": failing,
        "waiting": waiting,
        "stale": [s["key"] for s in sources if s["status"] == "stale"],
        "network_sources": list(collect_mod.NETWORK_SOURCES),
    }
    if result.join is not None:
        body["join"] = result.join.summary()
    return body


@router.get("")
def sources(db: Session = Depends(get_db)):
    """Per-source counts, staleness and failures. No network calls."""
    return _payload(collect_mod.collect(get_config(), db, refresh=False))


@router.post("/refresh")
def refresh(db: Session = Depends(get_db)):
    """Re-fetch the network sources and re-read everything else.

    A POST because it reaches out to third parties and updates the cache —
    still read-only with respect to every source raDash observes.
    """
    return _payload(collect_mod.collect(get_config(), db, refresh=True))


@router.get("/join")
def join(db: Session = Depends(get_db)):
    """The proposed Zotero ↔ trundlr ↔ folder join, with its basis per match.

    Served in full rather than summarised: this is the one output a human has
    to eyeball before the ledger and the map can be trusted.
    """
    result = collect_mod.collect(get_config(), db, refresh=False)
    if result.join is None:
        return {"matches": [], "note": "no source available to join yet"}
    return {
        "summary": result.join.summary(),
        "matches": [
            {
                "slug": m.slug,
                "basis": m.basis,
                "zotero_collection": m.zotero_collection,
                "item_count": m.item_count,
                "trundlr_project_id": m.trundlr_project_id,
                "trundlr_name": m.trundlr_name,
                "trundlr_priority": m.trundlr_priority,
                "folder": m.folder,
                "complete": m.complete,
                "notes": m.notes,
            }
            for m in result.join.matches
        ],
    }


# --- M2-T8: the Scholar lane, fed by paste rather than by a bind mount -------

SCHOLAR_KIND = "scholar"


# Why a countless CSV is refused rather than accepted and labelled.
#
# `citations.csv` was checked against a real export: the header is Authors,
# Title, Publication, Volume, Number, Pages, Year, Publisher. No citation
# column, in that export or any other Scholar offers, because the counts exist
# only on the rendered profile page.
#
# Storing it anyway looked generous and was not. This lane exists to carry the
# numbers the automated lanes cannot see; without them the file contributes
# nothing but DOI-less work candidates, and every one of those is a duplicate
# proposal you have to rule on at the gate. Accepting it costs you an hour of
# gate queue to add no citation and no work the indexes did not already hold,
# with a DOI attached, which the CSV lacks.
#
# A CSV you extended by hand with a count column is a different file and is
# accepted, because the rule is about counts and not about the extension.
CSV_WITHOUT_COUNTS = (
    "This is Scholar's own CSV export, and it carries no citation counts — "
    "Scholar puts them in none of its exports. Without them it cannot move "
    "the manual lane, and its entries would reach the ledger as DOI-less "
    "candidates you then have to rule on one by one. Copy the profile page "
    "itself instead: the rows reading \"Cited by 402\" are the whole point.")


def _refusal(parsed) -> Optional[str]:
    """Why this paste should not be stored, if it should not be."""
    if parsed.entries and parsed.shape == "csv" and parsed.with_counts == 0:
        return CSV_WITHOUT_COUNTS
    return None


@router.post("/scholar/preview")
def preview_scholar(text: str = Body(..., embed=True)):
    """Parse a pasted Scholar profile without storing it.

    Shows how many entries carried a `Cited by` count, because that is the
    whole point of pasting rather than exporting. A parse that produced none
    is returned with the reason it will be refused rather than as a 422: you
    asked what is in the file, and the answer is more use than an error.
    """
    try:
        imports.validate(text, SCHOLAR_KIND)
    except imports.ImportError_ as exc:
        raise HTTPException(422, str(exc))
    parsed = scholar_src.parse(text)
    return {
        "shape": parsed.shape,
        "count": len(parsed.entries),
        "with_citations": parsed.with_counts,
        "citations_summed": parsed.citation_sum,
        "refused": _refusal(parsed),
        "entries": [
            {"title": e.title, "year": e.year, "venue": e.venue,
             "cited_by": e.cited_by}
            for e in parsed.entries[:100]
        ],
        "note": ("Entries without a count are recorded as uncaptured, not as "
                 "zero." if parsed.with_counts < len(parsed.entries) else None),
    }


@router.post("/scholar/import")
def import_scholar(text: str = Body(..., embed=True)):
    """Store the paste where the Scholar reader already looks."""
    cfg = get_config()
    parsed = scholar_src.parse(text)
    refused = _refusal(parsed)
    if refused:
        raise HTTPException(422, refused)
    try:
        path = imports.save(cfg.import_dir, text, SCHOLAR_KIND)
    except imports.ImportError_ as exc:
        raise HTTPException(422, str(exc))
    except OSError as exc:
        raise HTTPException(500, f"could not write the import: {exc}")
    return {"stored": path.name, "entries": len(parsed.entries),
            "with_citations": parsed.with_counts,
            "note": "The Scholar source reads the newest import on next refresh."}
