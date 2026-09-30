"""`/api/outputs` — emitting the two artifacts, and nothing else — M7.

The endpoints are deliberately thin. A brief is the candidate the planning
page already computed, rendered to text and written once; nothing new is
scored here, so the file and the page cannot disagree about why an area
surfaced.

`POST` rather than `GET` because these write, and `preview=true` exists so you
can read what would be written without writing it — the same courtesy the
Scholar importer offers, and for the same reason: a file that appears in
`output/` without your having seen it is a file you will not trust.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.config import get_config
from app.database import get_db
from app.outputs import brief as brief_mod
from app.outputs import emit as emit_mod
from app.outputs import fill as fill_mod
from app.signals import assemble as assemble_mod
from app.signals import regions as regions_mod
from app.signals import signals as signals_mod

router = APIRouter(prefix="/api/outputs", tags=["outputs"])

# A Zotero collection has to hold this share of a region's members before it
# is worth naming as "where this already lives". Below it the collection is
# somewhere a few of these papers happen to sit.
COLLECTION_SHARE = 0.1


def _candidate(db: Session, cluster: int, signal: Optional[int]) -> dict:
    """The candidate the planning page would show for this region.

    Recomputed rather than looked up, because nothing is stored — which is
    what makes the file reproducible from the same state rather than from a
    cache that may have moved on.
    """
    regions, _ = regions_mod.gather(db)
    if not regions:
        raise HTTPException(503, "no space has been fitted; nothing to write about")
    scored, _ = signals_mod.score_all(regions)
    rows = assemble_mod.candidates(
        scored, signals_mod.scale_of(regions), signals_mod.thresholds())

    here = [c for c in rows if c["cluster"] == cluster]
    if signal is not None:
        here = [c for c in here if c["signal"] == signal]
    if not here:
        raise HTTPException(
            404, f"no candidate for region {cluster}"
                 + (f" under signal {signal}" if signal else "")
                 + " — it passes no signal's gate, so there is nothing to "
                   "hand off")
    # The strongest reading of the region, when the caller did not name one.
    return sorted(here, key=lambda c: -c["score"])[0]


def _collections(cfg, candidate: dict) -> list:
    """Zotero collections that already hold a share of this region.

    Read live rather than carried on the map: a collection is a thing you
    reorganise, and a stale answer here would send you to a folder you emptied
    last month.
    """
    from app.sources import zotero as zotero_src

    refs = {r["ref"] for r in candidate.get("corpus") or []}
    if not refs:
        return []
    report = zotero_src.read(cfg)
    if report.data is None:
        return []
    # The region's members as the map knows them are a sample, so a collection
    # is scored on the share of that sample it holds.
    out = []
    for coll in report.data.collections:
        matched = len(refs & set(coll.item_keys))
        if matched and matched / max(len(refs), 1) >= COLLECTION_SHARE:
            out.append({"name": coll.name, "matched": matched,
                        "size": coll.size})
    out.sort(key=lambda c: -c["matched"])
    return out


def _bridging(db: Session, candidate: dict) -> set:
    """Frontier papers that also sit near another region you are active in.

    Measured on the two display components, because that is the only geometry
    a stored frontier item has coordinates in. A paper landing inside another
    active region's own spread is doing the thing `DESIGN.md` §8 calls
    bridging: connecting this area to one whose end is already yours.
    """
    from app.mapping import fit as fit_mod
    from app.models import FrontierItem, MapCluster, MapPoint

    space = fit_mod.current(db)
    if space is None:
        return set()
    points = db.exec(select(MapPoint).where(MapPoint.space_id == space.id)).all()
    clusters = db.exec(select(MapCluster)
                       .where(MapCluster.space_id == space.id)).all()

    active = {p.cluster for p in points if p.cluster is not None
              and p.kind.value in ("work", "project")}
    active.discard(candidate["cluster"])
    if not active:
        return set()

    spreads: dict = {}
    for c in clusters:
        if c.cluster not in active:
            continue
        members = [p for p in points if p.cluster == c.cluster
                   and p.kind.value == "corpus"]
        if len(members) < 3:
            continue
        spread = sum(((p.x - c.centroid_x) ** 2 + (p.y - c.centroid_y) ** 2) ** 0.5
                     for p in members) / len(members)
        if spread > 0:
            spreads[c.cluster] = (c.centroid_x, c.centroid_y, spread)
    if not spreads:
        return set()

    out = set()
    lineage = candidate.get("lineage")
    for item in db.exec(select(FrontierItem)
                        .where(FrontierItem.lineage == lineage)).all():
        if item.x is None or item.y is None:
            continue
        for cx, cy, spread in spreads.values():
            if ((item.x - cx) ** 2 + (item.y - cy) ** 2) ** 0.5 <= spread:
                out.add(f"https://doi.org/{item.doi}" if item.doi
                        else f"https://openalex.org/{item.openalex_id}")
                break
    return out


def _check_route(candidate: dict, wanted: str, preview: bool) -> None:
    """Refuse to emit the artifact the candidate does not route to.

    Both directions, which is the half that was missing: the brief endpoint
    checked and the fill endpoint did not, so a signal 1 — you have read here
    and written nothing, go and write — would quietly emit a shopping list of
    papers to collect. Routing is the design's honesty mechanism rather than a
    default, and a mechanism that holds one way round is a preference.

    A preview is still allowed, because seeing what the other artifact would
    say is how you disagree with the routing rather than merely obey it.
    """
    actual = candidate["route"]["output"]
    if actual == wanted or preview:
        return
    raise HTTPException(
        409,
        f"this candidate routes to a {candidate['route']['label']}, not a "
        f"{'read-in brief' if wanted == 'brief' else 'corpus fill list'} — "
        f"{candidate['route']['why']} Add preview=true to read what the other "
        "one would say.")


@router.get("")
def listing(db: Session = Depends(get_db)):
    """What raDash has emitted, newest first."""
    cfg = get_config()
    return {
        "output_dir": str(cfg.output_dir),
        "files": emit_mod.listing(cfg),
        "note": ("Everything raDash writes lands here and nowhere else. It "
                 "writes nothing into Zotero, into a project folder, or onto "
                 "trundlr."),
    }


@router.get("/file/{name}")
def read_file(name: str):
    """One emitted file, so the page can show what it wrote."""
    cfg = get_config()
    text = emit_mod.read(cfg, name)
    if text is None:
        raise HTTPException(404, f"no output named {name!r}")
    return {"name": name, "text": text}


@router.post("/brief")
def write_brief(cluster: int, signal: Optional[int] = None,
                preview: bool = Query(False),
                db: Session = Depends(get_db)):
    """A read-in brief for one region. `preview` writes nothing."""
    cfg = get_config()
    candidate = _candidate(db, cluster, signal)
    _check_route(candidate, "brief", preview)

    text = brief_mod.render(candidate, _collections(cfg, candidate))
    if preview:
        return {"preview": True, "text": text,
                "filename": emit_mod.filename(candidate["region"], "readin")}
    try:
        path = emit_mod.write(cfg, candidate["region"], "readin", text)
    except (emit_mod.OutsideOutput, OSError) as exc:
        raise HTTPException(500, f"could not write the brief: {exc}")
    return {"written": path.name, "bytes": len(text), "text": text,
            "note": "Written to raDash's output directory and nowhere else."}


@router.post("/fill")
def write_fill(cluster: int, signal: Optional[int] = None,
               preview: bool = Query(False),
               db: Session = Depends(get_db)):
    """A corpus fill list for one region. `preview` writes nothing."""
    cfg = get_config()
    candidate = _candidate(db, cluster, signal)
    _check_route(candidate, "fill", preview)

    items = candidate.get("frontier") or []
    if not items:
        raise HTTPException(
            409, "no frontier set has been gathered for this region, so there "
                 "is nothing to fill from. Gather on the Frontier page first.")

    roles = fill_mod.assign_roles(items, _bridging(db, candidate))
    region = {"name": candidate["region"], "terms": candidate["terms"],
              "cluster": candidate["cluster"], "lineage": candidate["lineage"]}
    text = fill_mod.render(region, roles, candidate)
    if preview:
        return {"preview": True, "text": text,
                "filename": emit_mod.filename(candidate["region"], "fill")}
    try:
        path = emit_mod.write(cfg, candidate["region"], "fill", text)
    except (emit_mod.OutsideOutput, OSError) as exc:
        raise HTTPException(500, f"could not write the fill list: {exc}")
    return {"written": path.name, "bytes": len(text), "text": text,
            "note": "Written to raDash's output directory and nowhere else."}
