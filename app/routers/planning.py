"""`/api/planning` — the four signals, and the candidates they produce.

Advisory. It ranks, explains, and stops (`DESIGN.md` §7).

Nothing here is stored, and that is the milestone's exit criterion rather than
an optimisation: candidates are recomputed from the current space and the
current frontier every time this endpoint is read, so two reads of the same
state give the same candidates with the same evidence. A cached candidate
table would have to be invalidated by a refit, a gather, a ruling and a
setting change, and the first one that was missed would leave a score on the
page that nothing on the machine could reproduce.

The cost of that choice is a few hundred milliseconds of arithmetic over a
couple of thousand points. The alternative was a table whose honesty depended
on five invalidation paths all being right.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.database import get_db
from app.signals import assemble as assemble_mod
from app.signals import regions as regions_mod
from app.signals import signals as signals_mod

router = APIRouter(prefix="/api/planning", tags=["planning"])

# What each signal is, in the terms `DESIGN.md` §4 sets out. Carried in the
# response rather than written into the view, so the page cannot describe a
# signal differently from the thing that computed it.
NOTES = {
    1: ("Corpus-dense regions far from every work in your ledger, ranked by "
        "reading investment times distance from your nearest publication. "
        "The most directly actionable of the four: the grounding is already "
        "paid for."),
    2: ("Regions thin in both your reading and your writing, but busy in the "
        "frontier feed. Highest upside, and the one signal here that cannot "
        "be computed from your library alone — a region the frontier has "
        "never been asked about is listed below as unassessed, not scored."),
    3: ("Regions you have read densely whose literature has gone quiet. The "
        "strongest signal available and the easiest to fool yourself with, "
        "so every candidate is confirmed against the frontier: an "
        "unconfirmed one is a gap in your collecting, not in the field, and "
        "routes to reading rather than to writing."),
    4: ("Low corpus density, no activity filter, ranked last and labelled as "
        "scouting. Some of what is here is quiet because nobody cares, and "
        "nothing in this signal can tell you which."),
}


@router.get("")
def planning(limit: int = Query(8, ge=1, le=50),
             db: Session = Depends(get_db)):
    """Every candidate the four signals produce, with the evidence behind each."""
    regions, context = regions_mod.gather(db)
    if not regions:
        return _block(context, sections=[], unassessable=[], counted=0,
                      note=("No space has been fitted, so there are no regions "
                            "to score. Fit the map first."))

    scored, unassessable = signals_mod.score_all(regions)
    limits = signals_mod.thresholds()
    scale = signals_mod.scale_of(regions)
    rows = assemble_mod.candidates(scored, scale, limits)

    sections = []
    for number, (name, move) in sorted(signals_mod.SIGNALS.items()):
        here = [c for c in rows if c["signal"] == number]
        sections.append({
            "signal": number, "name": name, "move": move,
            "note": NOTES[number],
            "found": len(here),
            "candidates": here[:limit],
        })

    return _block(context, sections=sections, unassessable=unassessable,
                  counted=len(regions), limits=limits, scale=scale)


def _block(context, sections, unassessable, counted: int,
           limits: Optional[dict] = None,
           scale: Optional[signals_mod.Scale] = None,
           note: Optional[str] = None) -> dict:
    """The response, with the same age-and-source stamp every panel carries."""
    from app.routers.status import _block as stamped

    space = context.space
    return stamped(
        space.created_at if space else None, "the map and the frontier feed",
        signals=sections,
        unassessable=unassessable,
        weights=signals_mod.WEIGHTS,
        thresholds=limits or signals_mod.thresholds(),
        space={"id": space.id, "rows": space.row_count,
               "dimensions": space.dimensions,
               "corpus_hash": space.corpus_hash} if space else None,
        geometry=context.geometry,
        exact_geometry=context.exact_geometry,
        regions=counted,
        frontier_queried=context.frontier_queried,
        works_placed=context.works_placed,
        briefs_placed=context.briefs_placed,
        unclustered=context.unclustered,
        largest_region=scale.collected if scale else None,
        note=note or (
            "Four signals, scored separately and never blended: they call for "
            "different responses, and an average of them would rank a thing "
            "nobody could act on. Every score is recomputed from the current "
            "space and the current frontier, so nothing here is a cached "
            "number whose inputs have moved on."),
    )
