"""`/api/frontier` — what is being published in your regions.

Gathered per region and cached with the time it was gathered, so an offline
read serves what it has with its age attached. The dashboard's stance
throughout is that stale-and-labelled beats absent, and a frontier feed is the
panel where that matters most: it depends on somebody else's API being up.
"""
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.config import get_config
from app.database import get_db
from app.mapping import fit as fit_mod
from app.models import FrontierItem, MapCluster, MapPoint, RegionTopic
from app.sources import frontier as frontier_mod

router = APIRouter(prefix="/api/frontier", tags=["frontier"])


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _age_days(dt) -> Optional[float]:
    dt = _aware(dt)
    return None if dt is None else round(
        (datetime.now(timezone.utc) - dt).total_seconds() / 86400, 2)


@router.get("")
def read_frontier(db: Session = Depends(get_db)):
    """Every region's frontier set, with its age. Never touches the network."""
    space = fit_mod.current(db)
    if space is None:
        return {"regions": [], "note": "no space has been fitted"}

    clusters = db.exec(select(MapCluster)
                       .where(MapCluster.space_id == space.id)).all()
    items = db.exec(select(FrontierItem)).all()
    topics = db.exec(select(RegionTopic)).all()

    by_lineage: dict = {}
    for it in items:
        by_lineage.setdefault(it.lineage, []).append(it)
    topics_by_lineage: dict = {}
    for t in topics:
        topics_by_lineage.setdefault(t.lineage, []).append(t)

    regions = []
    for c in sorted(clusters, key=lambda c: -c.size):
        rows = sorted(by_lineage.get(c.lineage or "", []),
                      key=lambda i: -(i.cited_by or 0))
        fetched = max((_aware(i.fetched_at) for i in rows), default=None)
        regions.append({
            "cluster": c.cluster, "lineage": c.lineage,
            "name": c.label, "terms": json.loads(c.terms or "[]"),
            "size": c.size,
            "topics": [{"id": t.topic_id, "name": t.topic_name,
                        "share": round(t.share, 3), "members": t.members}
                       for t in topics_by_lineage.get(c.lineage or "", [])],
            "fetched_at": fetched.isoformat() if fetched else None,
            "age_days": _age_days(fetched),
            "items": [{
                "openalex_id": i.openalex_id, "doi": i.doi, "title": i.title,
                "year": i.year, "venue": i.venue, "cited_by": i.cited_by,
                "topic": i.topic_name, "already_held": i.already_held,
                "distance": i.distance_to_region, "off_target": i.off_target,
                "url": (f"https://doi.org/{i.doi}" if i.doi
                        else f"https://openalex.org/{i.openalex_id}"),
            } for i in rows],
        })

    stale = [r["name"] or r["terms"][:1] for r in regions if r["age_days"] is None]
    return {
        "regions": regions,
        "never_fetched": len(stale),
        "note": ("Each set is dated. Nothing here is fetched on read — use "
                 "refresh, which is a few dozen polite requests."),
    }


@router.post("/refresh")
def refresh(limit_regions: int = Query(30, ge=1, le=60),
            db: Session = Depends(get_db)):
    """Gather frontier sets for every region, politely.

    One request per region, paced. Regions too small or too diffuse to resolve
    to a topic are skipped and reported rather than queried on a guess.
    """
    import time

    from app import settings

    cfg = get_config()
    if settings.flag("OFFLINE"):
        raise HTTPException(
            503, "raDash is offline, so nothing was gathered. The sets "
                 "already held are served with their age on the Frontier "
                 "page; turn Offline off on Settings to gather again.")
    space = fit_mod.current(db)
    if space is None:
        raise HTTPException(503, "no space has been fitted; nothing to query")

    from app.sources import cache as cache_mod
    from app.sources.openalex import ENRICHMENT_KEY

    clusters = db.exec(select(MapCluster)
                       .where(MapCluster.space_id == space.id)).all()
    points = db.exec(select(MapPoint).where(MapPoint.space_id == space.id)).all()

    # A space fitted before raDash recorded DOIs on its points cannot resolve
    # any region to a topic, and no amount of fetching will change that. Say
    # which thing is stale rather than blaming the cache.
    corpus_dois = [p.doi for p in points if p.kind.value == "corpus" and p.doi]
    if not corpus_dois:
        raise HTTPException(
            503, "this space carries no DOIs on its points, so no region can "
                 "be resolved to a topic. It was fitted by an earlier build; "
                 "refit the map and gather again.")

    # Topics come from the enrichment cache, which the map fit fills as a side
    # effect. Depending on that ordering made this endpoint fail with advice
    # about a different page, so it now fetches what it is missing.
    enrichment, _ = cache_mod.load(db, ENRICHMENT_KEY)
    enrichment = enrichment if isinstance(enrichment, dict) else {}
    needed = [d for d in set(corpus_dois)
              if not isinstance(enrichment.get(d), dict)
              or "topics" not in enrichment[d]]
    if needed:
        from app.sources import openalex as openalex_src
        openalex_src.enrich_dois(needed, cfg, db)
        enrichment, _ = cache_mod.load(db, ENRICHMENT_KEY)
        enrichment = enrichment if isinstance(enrichment, dict) else {}

    # Everything you already hold, so the frontier excludes your own backlog.
    # Something already in your library is not a frontier paper, and it is
    # already ranked on the reading list.
    held_dois = {p.doi for p in points if p.doi}

    # Gather for the regions you are working in, not the ones you collected
    # most of. Size is a record of the past: a retired direction can be the
    # largest region on the map and keep surfacing in every list, which is
    # exactly what happened with a set of Iowa wind-farm documents. A region
    # is live when a project brief sits in it or you have published there
    # recently; live regions are queried first, and dormant ones only if the
    # budget is not spent.
    from app.routers.status import ACTIVE_YEARS

    recent_year = datetime.now(timezone.utc).year - ACTIVE_YEARS
    live_clusters = {p.cluster for p in points if p.cluster is not None and (
        p.kind.value == "project"
        or (p.kind.value == "work" and p.year and p.year >= recent_year))}

    ordered = sorted(
        clusters,
        key=lambda c: (0 if c.cluster in live_clusters else 1, -c.size))

    started = time.perf_counter()
    result = frontier_mod.FrontierResult()
    result.failures.extend(
        f"region {c.cluster} ({', '.join(json.loads(c.terms or '[]')[:2])}) is "
        "dormant — no project or recent work in it, so it was not queried"
        for c in ordered[limit_regions:] if c.cluster not in live_clusters)
    model = fit_mod.load_model(cfg, space)
    vectors = fit_mod.load_vectors(space)

    for c in ordered[:limit_regions]:
        lineage = c.lineage or f"c{c.cluster}"
        member_dois = [p.doi for p in points
                       if p.cluster == c.cluster
                       and p.kind.value == "corpus" and p.doi]
        topics = frontier_mod.topics_for_region(member_dois, enrichment)
        if not topics:
            result.failures.append(
                f"region {c.cluster}: no topic describes {int(frontier_mod.MIN_TOPIC_SHARE * 100)}% "
                "of its members, so it was not queried")
            continue

        rows, error = frontier_mod.fetch(cfg, topics, held_dois)
        result.regions_queried += 1
        if error:
            result.failures.append(f"region {c.cluster}: {error}")
            continue

        _replace_region(db, lineage, c.cluster, topics, rows, model, vectors,
                        c, points)
        result.items.extend(rows)
        result.topics.extend(topics)
        frontier_mod.pace()

    db.commit()
    result.seconds = time.perf_counter() - started
    return result.summary()


def _replace_region(db, lineage, cluster, topics, rows, model, vectors, c,
                    points) -> None:
    """Swap in a region's new set. Its previous one is not kept: a frontier is
    about now, and two dated sets for one region would only raise the question
    of which is being shown."""
    for old in db.exec(select(FrontierItem)
                       .where(FrontierItem.lineage == lineage)).all():
        db.delete(old)
    for old in db.exec(select(RegionTopic)
                       .where(RegionTopic.lineage == lineage)).all():
        db.delete(old)

    for t in topics:
        db.add(RegionTopic(lineage=lineage, cluster=cluster, topic_id=t.topic_id,
                           topic_name=t.name, share=t.share, members=t.members))

    placed = _project(model, vectors, rows, c)
    spread = _region_spread(points, cluster, c)

    # The map does the filtering. A hundred papers come back from a topic far
    # broader than this region; the ones worth showing are the ones that land
    # near it, and distance is the only measure of that which does not depend
    # on the topic being well chosen.
    scored = []
    seen_titles = set()
    for row, coords in zip(rows, placed):
        key = " ".join((row["title"] or "").lower().split())[:90]
        if key in seen_titles:
            continue        # OpenAlex holds the same paper twice often enough
        seen_titles.add(key)
        distance = coords[2] if coords else None
        row["off_target"] = bool(spread and distance and distance > spread * 2)
        # Nearest first; unplaceable items fall to the back rather than out,
        # since "the model could not place it" is not the same as "it is far".
        scored.append((distance if distance is not None else float("inf"),
                       row, coords))
    scored.sort(key=lambda t: t[0])

    # Keep the closest, and prefer ones you do not already hold: your own
    # backlog is already ranked on the reading list.
    keep = [t for t in scored if not t[1]["already_held"]][:frontier_mod.PER_REGION]
    for _, row, coords in keep:
        db.add(FrontierItem(
            lineage=lineage, cluster=cluster, **{
                k: row[k] for k in ("openalex_id", "doi", "title", "year",
                                    "venue", "cited_by", "topic_id",
                                    "topic_name", "abstract", "already_held")},
            off_target=row.get("off_target", False),
            x=coords[0] if coords else None, y=coords[1] if coords else None,
            distance_to_region=coords[2] if coords else None))


def _region_spread(points, cluster, c) -> Optional[float]:
    """How far this region's own members sit from its centre.

    The yardstick for "landed inside the region": a frontier paper twice this
    far out was found by a topic the region only nominally has.
    """
    import numpy as np

    members = [p for p in points if p.cluster == cluster
               and p.kind.value == "corpus"]
    if len(members) < 3:
        return None
    centre = np.array([c.centroid_x, c.centroid_y])
    d = [float(np.linalg.norm(np.array([p.x, p.y]) - centre)) for p in members]
    return float(np.mean(d)) or None


def _project(model, vectors, rows, c) -> list:
    """Place frontier papers in the fitted space, where the model survives.

    This is the check that the topic resolution worked: a paper that lands far
    from the region that asked for it was found by a topic the region only
    nominally has. Where the model cannot be loaded the items are stored
    unplaced rather than dropped.
    """
    import numpy as np

    texts = [f"{r['title']}\n\n{r.get('abstract') or ''}".strip() for r in rows]
    if model is None or not texts or vectors is None:
        return [None] * len(rows)
    try:
        placed = model.transform(texts)
    except Exception:
        return [None] * len(rows)
    centre = np.array([c.centroid_x, c.centroid_y])
    out = []
    for v in placed:
        x, y = float(v[fit_mod.DISPLAY_X]), float(v[fit_mod.DISPLAY_Y])
        out.append((x, y, float(np.linalg.norm(np.array([x, y]) - centre))))
    return out
