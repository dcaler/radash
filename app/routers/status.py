"""`/api/status` — the dashboard's numbers, each carrying its own age.

The milestone's exit criterion is that every panel renders from cache and
shows how old it is, so age is not decoration here: a block without an `as_of`
is a block that cannot say whether it is telling you about today or about the
last time somebody pressed refresh.

Two things this module refuses to do.

**Nothing is hardcoded.** The coverage panel counts what is actually thin,
actually unclustered, actually unconfirmed, actually stale. A reassuring
constant would be worse than no panel, because it would be believed.

**Nothing is averaged across lanes.** Citations appear as automated and manual
figures side by side, never blended, for the same reason they are kept apart
in the ledger: the disagreement between them is the honest bound on both.
"""
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.config import get_config
from app.database import get_db
from app.mapping import fit as fit_mod
from app.models import (
    LedgerRuling, MapCluster, MapPoint, MapSpace, Snapshot, SourceState, Work,
    WorkCategory, WorkDecision,
)

router = APIRouter(prefix="/api/status", tags=["status"])

# Citations earned in this window count as "recent" for momentum. Two years is
# long enough that a single good month does not dominate and short enough that
# a paper coasting on a decade of citations does not read as active.
MOMENTUM_YEARS = 2


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _age_days(dt: Optional[datetime]) -> Optional[float]:
    dt = _aware(dt)
    if dt is None:
        return None
    return round((datetime.now(timezone.utc) - dt).total_seconds() / 86400, 2)


def _json(raw, default):
    try:
        return json.loads(raw) if raw else default
    except (ValueError, TypeError):
        return default


def _works(db: Session, snapshot: Optional[Snapshot]) -> list[Work]:
    if snapshot is None:
        return []
    return db.exec(select(Work).where(Work.snapshot_id == snapshot.id)).all()


@router.get("")
def status(db: Session = Depends(get_db)):
    """Everything the dashboard shows, with the provenance of each block."""
    snapshot = db.exec(select(Snapshot).where(Snapshot.is_current)).first()
    previous = db.exec(
        select(Snapshot).where(Snapshot.is_current == False)  # noqa: E712
        .order_by(Snapshot.id.desc())).first()
    space = fit_mod.current(db)
    works = _works(db, snapshot)

    from app.main import _APP_VERSION, _STARTED_AT

    return {
        # The build, on the page rather than only in the corner. After a
        # redeploy the first question is whether you are looking at the new
        # one, and the version is a content hash of app/ — it changes if and
        # only if the code did.
        "build": {"version": _APP_VERSION, "started_at": _STARTED_AT},
        "changes": _changes(db, snapshot, previous, works),
        "headline": _headline(snapshot, works),
        "accrual": _accrual(snapshot, works),
        "momentum": _momentum(snapshot, works),
        "areas": _areas(db, space),
        "drift": _drift(db, snapshot, space),
        "coverage": _coverage(db, snapshot, space, works),
    }


def _live_regions(db: Session, points) -> dict:
    """Regions with something current behind them.

    A region is live when one of your project briefs sits in it, or when you
    have published in it recently. Every project folder with a brief counts:
    trundlr's archived flag is not consulted. Corpus mass says where
    you have been; this says where you are. Without it the reading list keeps
    recommending a region you retired years ago purely because you once
    collected a lot of it.
    """
    from app.models import Work as _Work

    live: dict = {}
    for p in points:
        if p.cluster is None:
            continue
        if p.kind.value == "project":
            live.setdefault(p.cluster, f"project {p.label or 'a project'} sits in this region")
    recent_year = datetime.now(timezone.utc).year - ACTIVE_YEARS
    for p in points:
        if (p.cluster is not None and p.kind.value == "work"
                and p.year and p.year >= recent_year):
            live.setdefault(p.cluster, f"you published here in {p.year}")
    return live


def _frontier_candidates(db: Session, clusters, live, this_year,
                         drift=None) -> list:
    """Frontier papers, ranked beside what you already hold.

    These were two lists and should not have been: "what should I read next"
    does not care whether raDash found the paper in your library or in the
    literature. A frontier paper is not yours yet, which is a point in its
    favour rather than a reason to keep it on another page.
    """
    from app.models import FrontierItem

    rows = []
    by_lineage = {c.lineage: c for c in clusters.values() if c.lineage}
    for item in db.exec(select(FrontierItem)).all():
        region = by_lineage.get(item.lineage)
        if region is None or item.already_held:
            continue
        reasons, score = [], READING_WEIGHTS["frontier"]
        reasons.append({"signal": "frontier", "effect": "adds", "weight": READING_WEIGHTS["frontier"],
                        "detail": "not in your library — found in the literature"})
        if region.cluster in live:
            score += READING_WEIGHTS["live_project"]
            reasons.append({"signal": "live_project", "effect": "adds",
                            "weight": READING_WEIGHTS["live_project"],
                            "detail": live[region.cluster]})
        else:
            score *= DORMANT_FACTOR
            reasons.append({"signal": "dormant", "effect": "damps",
                            "weight": DORMANT_FACTOR,
                            "detail": "nothing current in this region"})
        if item.off_target:
            score *= 0.5
            reasons.append({"signal": "off_target", "effect": "damps",
                            "weight": 0.5,
                            "detail": "landed outside the region that found it"})
        if item.year and item.year >= this_year - RECENT_YEARS:
            bump = READING_WEIGHTS["recent"] * (
                1 - (this_year - item.year) / RECENT_YEARS)
            score += bump
            reasons.append({"signal": "recent", "effect": "adds", "weight": round(bump, 3),
                            "detail": f"published {item.year}"})
        # Frontier items were projected into the same space when they were
        # gathered, so they sit on the drift axis beside your own backlog.
        # Ranking them on one axis is the reason this list is one list.
        drift_reason = _drift_reason(drift, item.x, item.y)
        if drift_reason:
            score += drift_reason["weight"]
            reasons.append(drift_reason)
        rows.append({
            "ref": item.openalex_id, "label": item.title, "year": item.year,
            "cited_by": item.cited_by, "cluster": region.cluster,
            "region": ", ".join(_json(region.terms, [])[:3]),
            "score": round(score, 3), "reasons": reasons, "frontier": True,
            "url": (f"https://doi.org/{item.doi}" if item.doi
                    else f"https://openalex.org/{item.openalex_id}"),
        })
    return rows


def _block(as_of: Optional[datetime], source: str, **rest) -> dict:
    """Every panel is a block, and every block says when and from what."""
    return {"as_of": _aware(as_of).isoformat() if as_of else None,
            "age_days": _age_days(as_of), "source": source, **rest}


def _headline(snapshot, works) -> dict:
    """The figures that answer "where does the portfolio stand"."""
    automated = sum(w.citations_automated or 0 for w in works)
    manual = sum(w.citations_manual or 0 for w in works)
    publications = [w for w in works
                    if w.category == WorkCategory.publication.value]
    years = [w.year for w in works if w.year]
    return _block(
        snapshot.created_at if snapshot else None, "works ledger",
        works=len(works), publications=len(publications),
        citations_automated=automated, citations_manual=manual,
        # Side by side, never blended: where the lanes disagree, that gap is
        # the honest bound on how precisely either can be stated.
        lane_gap=(manual - automated) if manual else None,
        span=[min(years), max(years)] if years else None,
        unconfirmed=sum(1 for w in works if not w.confirmed),
    )


def _accrual(snapshot, works) -> dict:
    """Per-year citation counts, per work.

    A total cannot say whether a paper is alive. Each series is returned raw
    and scaled to its own peak by the view, so the *shape* is comparable
    between a paper with four hundred citations and one with eleven.
    """
    series = []
    for w in works:
        counts = {int(k): int(v) for k, v in
                  _json(w.counts_by_year, {}).items() if str(k).isdigit()}
        if not counts:
            continue
        series.append({
            "fingerprint": w.fingerprint, "title": w.title, "year": w.year,
            "category": w.category, "total": w.citations_automated or 0,
            "counts": dict(sorted(counts.items())),
        })
    series.sort(key=lambda s: -s["total"])
    return _block(snapshot.created_at if snapshot else None,
                  "OpenAlex counts_by_year", works=series)


def _momentum(snapshot, works) -> dict:
    """Share of each work's citations earned in the last two years.

    Ranks attention, not quality, and is unreadable without age beside it — a
    paper published last year reads 100% by construction. The publication year
    travels with every row so the view cannot show one without the other.
    """
    current_year = datetime.now(timezone.utc).year
    cutoff = current_year - MOMENTUM_YEARS
    rows = []
    for w in works:
        counts = {int(k): int(v) for k, v in
                  _json(w.counts_by_year, {}).items() if str(k).isdigit()}
        total = sum(counts.values())
        if total <= 0:
            continue
        recent = sum(n for y, n in counts.items() if y > cutoff)
        rows.append({
            "fingerprint": w.fingerprint, "title": w.title, "year": w.year,
            "total": total, "recent": recent, "share": round(recent / total, 4),
            # Years since publication, so "100% recent" can be read as either
            # momentum or novelty rather than silently as the first.
            "age": (current_year - w.year) if w.year else None,
        })
    rows.sort(key=lambda r: -r["share"])
    return _block(snapshot.created_at if snapshot else None,
                  "OpenAlex counts_by_year", window_years=MOMENTUM_YEARS,
                  works=rows)


def _areas(db: Session, space: Optional[MapSpace]) -> dict:
    """Map regions, with how much reading and how much writing sits in each.

    The pairing is the point: a region where you have read ninety papers and
    written none is a different thing from one where you have written three
    and read four, and neither is visible from a single number.
    """
    if space is None:
        return _block(None, "map", regions=[], note="no space has been fitted")

    clusters = db.exec(select(MapCluster)
                       .where(MapCluster.space_id == space.id)).all()
    points = db.exec(select(MapPoint)
                     .where(MapPoint.space_id == space.id)).all()

    # Collected and read are counted apart. Conflating them overstated what
    # had been absorbed by roughly twenty to one on this library, and the gap
    # between the two columns is the more interesting number: a region with
    # ninety items and four read is a backlog, not a body of knowledge.
    collected, read, written = {}, {}, {}
    for p in points:
        if p.cluster is None:
            continue
        if p.kind.value == "corpus":
            collected[p.cluster] = collected.get(p.cluster, 0) + 1
            if p.reading != "collected":
                read[p.cluster] = read.get(p.cluster, 0) + 1
        else:
            written[p.cluster] = written.get(p.cluster, 0) + 1

    regions = [{
        "cluster": c.cluster, "lineage": c.lineage,
        "name": c.label, "terms": _json(c.terms, []),
        "collected": collected.get(c.cluster, 0),
        "read": read.get(c.cluster, 0),
        "written": written.get(c.cluster, 0),
        "exemplar": c.exemplar_label,
    } for c in clusters]
    regions.sort(key=lambda r: -r["collected"])
    return _block(space.created_at, "map",
                  regions=regions,
                  unclustered=sum(1 for p in points
                                  if p.kind.value == "corpus" and p.cluster is None))


def _drift(db: Session, snapshot, space: Optional[MapSpace]) -> dict:
    """Two kinds of drift, kept apart because they answer different questions.

    `public` is your CV against the record — what you have published and not
    claimed. `position` is published work against work in progress, as two
    centres of mass in the fitted space with the vector between them. The
    second is the one that makes "am I becoming a different researcher than my
    CV says" a measurable question.
    """
    public = _json(snapshot.drift, None) if snapshot else None

    position = None
    if space is not None:
        points = db.exec(select(MapPoint)
                         .where(MapPoint.space_id == space.id)).all()
        published = [p for p in points if p.kind.value == "work"]
        ongoing = [p for p in points if p.kind.value == "project"]
        if published and ongoing:
            mean = lambda rows, k: sum(getattr(r, k) for r in rows) / len(rows)
            position = {
                "published": {"x": round(mean(published, "x"), 4),
                              "y": round(mean(published, "y"), 4),
                              "n": len(published)},
                "in_progress": {"x": round(mean(ongoing, "x"), 4),
                                "y": round(mean(ongoing, "y"), 4),
                                "n": len(ongoing)},
            }
            position["displacement"] = round(
                ((position["published"]["x"] - position["in_progress"]["x"]) ** 2
                 + (position["published"]["y"] - position["in_progress"]["y"]) ** 2)
                ** 0.5, 4)

    return _block(space.created_at if space else None, "map and public CV",
                  public=public, position=position,
                  note=("Displacement is in the units of whichever components "
                        "the map is drawn on, so it compares across refits of "
                        "the same space and not across different ones."))


def _coverage(db: Session, snapshot, space, works) -> dict:
    """What raDash cannot see, counted rather than asserted.

    Every figure here is computed from state. The panel exists to be read
    before the others, and a reassuring constant would be worse than nothing
    because it would be believed.
    """
    sources = db.exec(select(SourceState)).all()
    decisions = db.exec(select(WorkDecision)).all()
    clusters = (db.exec(select(MapCluster)
                        .where(MapCluster.space_id == space.id)).all()
                if space else [])
    points = (db.exec(select(MapPoint).where(MapPoint.space_id == space.id)).all()
              if space else [])

    corpus_points = [p for p in points if p.kind.value == "corpus"]
    unclustered = sum(1 for p in corpus_points if p.cluster is None)
    public = _json(snapshot.drift, {}) if snapshot else {}

    return _block(
        space.created_at if space else (snapshot.created_at if snapshot else None),
        "computed from current state",
        sources=[{"key": s.key, "status": s.status.value,
                  "items": s.item_count, "age_days": _age_days(s.last_read_at),
                  "last_clean": _age_days(s.last_ok_at)}
                 for s in sorted(sources, key=lambda s: s.key)],
        fitted_documents=space.row_count if space else 0,
        unclustered=unclustered,
        unclustered_share=(round(unclustered / len(corpus_points), 3)
                           if corpus_points else None),
        regions=len(clusters),
        unconfirmed_works=sum(1 for w in works if not w.confirmed),
        rulings_made=len(decisions),
        excluded=sum(1 for d in decisions if d.ruling is LedgerRuling.excluded),
        unclaimed_publicly=len((public or {}).get("unclaimed") or []),
        public_cv_age_days=(public or {}).get("claim_age_days"),
        manual_lane_empty=not any(w.citations_manual for w in works),
        # The gap this project exists to stop hiding.
        corpus_collected=sum(1 for p in corpus_points
                             if p.reading == "collected"),
        corpus_with_reading_evidence=sum(1 for p in corpus_points
                                         if p.reading != "collected"),
    )


def _changes(db: Session, snapshot, previous, works) -> dict:
    """What moved since the last snapshot — the panel that leads the page.

    Weekly cadence means nothing here rewards watching, so the dashboard opens
    with difference rather than level: the numbers themselves are on every
    other panel, and the only question a weekly glance can answer is what is
    not the same as last week.
    """
    if snapshot is None:
        return _block(None, "snapshots", available=False,
                      note="no snapshot yet")
    if previous is None:
        return _block(snapshot.created_at, "snapshots", available=False,
                      note=("This is the first snapshot, so there is nothing "
                            "to compare against. The panel fills in on the "
                            "next refresh."))

    before = {w.fingerprint: w for w in _works(db, previous)}
    now = {w.fingerprint: w for w in works}

    added = [now[f].title for f in now.keys() - before.keys()]
    removed = [before[f].title for f in before.keys() - now.keys()]

    moved = []
    for f in now.keys() & before.keys():
        was = before[f].citations_automated or 0
        is_ = now[f].citations_automated or 0
        if is_ != was:
            moved.append({"title": now[f].title, "was": was, "now": is_,
                          "delta": is_ - was})
    moved.sort(key=lambda m: -abs(m["delta"]))

    def _total(rows):
        return sum(w.citations_automated or 0 for w in rows)

    return _block(
        snapshot.created_at, "snapshots", available=True,
        since=_aware(previous.created_at).isoformat(),
        since_days=_age_days(previous.created_at),
        works_added=added, works_removed=removed,
        citations_before=_total(before.values()),
        citations_now=_total(works),
        citations_delta=_total(works) - _total(before.values()),
        works_moved=moved[:20],
    )


# --- what to read next ------------------------------------------------------

# Weights for the reading list. Deliberately few and deliberately visible: a
# score nobody can decompose is a ranking nobody can argue with.
READING_WEIGHTS = {
    "with_your_drift": 3.0,   # lies the way your work has been moving
    "near_your_work": 3.0,    # sits in a region you are actively writing in
    "close_to_a_brief": 2.5,  # near something you are working on right now
    "live_project": 2.0,      # that region has an active, prioritised project
    "taken_up": 1.5,          # the field has cited it
    "recent": 1.0,            # published lately
    "frontier": 1.2,          # new to you: not in your library at all
}

# The window that splits your older work from your recent work when the drift
# direction is computed. Six years, matching the arrow drawn on the map: the
# ranking and the picture have to describe the same movement or one of them is
# lying.
DRIFT_YEARS = 6

# How far along the drift a candidate has to sit to earn the full weight, as a
# multiple of the drift's own length. At 1 it is level with your recent work;
# at 2 it is as far beyond that again. Twice is the target because the list is
# for what to read *next*, and matching where you already are is not next.
DRIFT_FULL_AT = 2.0

# A region you have stopped working in should stop being recommended, however
# much of it you once collected. Corpus mass is a record of the past; a
# project brief is a statement about the present. Regions with
# neither a live project nor recent work are damped rather than dropped —
# you may come back to them, and raDash should not decide that you will not.
DORMANT_FACTOR = 0.35

# Work published within this many years counts as evidence you are still in
# a region.
ACTIVE_YEARS = 4

# Past this many years a paper stops counting as recent. Not a judgement about
# its worth — an old paper you have not read may be exactly what you need — but
# recency is the one signal here that decays.
RECENT_YEARS = 5


def _drift_axis(points) -> Optional[dict]:
    """The direction your published work has travelled, as a unit vector.

    Older works to recent works, split at six years, and measured on the two
    components the map is drawn on. Display coordinates rather than the full
    space for one reason that outweighs the loss of precision: this is the
    arrow on the Landscape page, and a ranking ordered by a direction you
    cannot see is one you cannot argue with. Frontier items carry the same two
    coordinates and no others, so it is also the only geometry in which your
    backlog and the literature can be ranked on one axis at all.
    """
    works = [p for p in points if p.kind.value == "work" and p.year]
    if len(works) < 4:
        return None
    cut = max(w.year for w in works) - DRIFT_YEARS
    older = [w for w in works if w.year <= cut]
    recent = [w for w in works if w.year > cut]
    if not older or not recent:
        return None

    mean = lambda rows, k: sum(getattr(r, k) for r in rows) / len(rows)
    fx, fy = mean(older, "x"), mean(older, "y")
    tx, ty = mean(recent, "x"), mean(recent, "y")
    length = ((tx - fx) ** 2 + (ty - fy) ** 2) ** 0.5
    if length <= 0:
        return None
    return {"from": (fx, fy), "unit": ((tx - fx) / length, (ty - fy) / length),
            "length": length, "cut": cut,
            "older": len(older), "recent": len(recent)}


def _along_drift(drift: Optional[dict], x, y) -> Optional[float]:
    """How far along the drift a point sits, in drift-lengths.

    0 is the centre of your older work, 1 the centre of your recent work.
    Negative is behind you, and returns 0 rather than a penalty: a paper on
    the far side of where you started is not evidence against itself, it
    simply earns nothing from this signal.
    """
    if drift is None or x is None or y is None:
        return None
    dx, dy = x - drift["from"][0], y - drift["from"][1]
    ux, uy = drift["unit"]
    return max(0.0, (dx * ux + dy * uy) / drift["length"])


def _drift_reason(drift, x, y) -> Optional[dict]:
    """The drift term for one candidate, or nothing where it cannot be had."""
    t = _along_drift(drift, x, y)
    if t is None:
        return None
    contribution = READING_WEIGHTS["with_your_drift"] * min(t / DRIFT_FULL_AT, 1.0)
    if contribution <= 0:
        return None
    return {"signal": "with_your_drift", "effect": "adds",
            "weight": round(contribution, 3),
            "detail": (f"sits {t:.2f} drift-lengths along the direction your "
                       f"work has moved since {drift['cut']}")}


@router.get("/reading")
def reading(limit: int = 25, db: Session = Depends(get_db)):
    """Unread items worth reading next, with the reason for each.

    Every candidate is something you collected and there is no evidence you
    read. That is a weaker claim than "unread": reading in Preview, on a
    tablet or on paper leaves no trace, so this is a list to dismiss from as
    much as to work through. Marking an item read in Zotero removes it.

    Nothing here is a verdict. Each row carries the signals that put it there
    and what it is near, because a ranking you cannot decompose is one you
    cannot disagree with — and disagreeing is the point of a reading list.
    """
    import math

    space = fit_mod.current(db)
    if space is None:
        return _block(None, "map", candidates=[],
                      note="no space has been fitted, so nothing can be ranked")

    points = db.exec(select(MapPoint).where(MapPoint.space_id == space.id)).all()
    clusters = {c.cluster: c for c in db.exec(
        select(MapCluster).where(MapCluster.space_id == space.id)).all()}

    mine = [p for p in points if p.kind.value in ("work", "project")]
    briefs = [p for p in points if p.kind.value == "project"]
    activity: dict[int, int] = {}
    for p in mine:
        if p.cluster is not None:
            activity[p.cluster] = activity.get(p.cluster, 0) + 1

    live = _live_regions(db, points)

    vectors = fit_mod.load_vectors(space)
    brief_rows = [p.row for p in briefs] if vectors is not None else []

    this_year = datetime.now(timezone.utc).year
    drift = _drift_axis(points)
    frontier_rows = _frontier_candidates(db, clusters, live, this_year, drift)
    best_cited = max((p.cited_by or 0 for p in points if p.kind.value == "corpus"),
                     default=0)

    candidates = []
    for p in points:
        if p.kind.value != "corpus" or p.reading != "collected":
            continue
        reasons, score = [], 0.0

        here = activity.get(p.cluster, 0) if p.cluster is not None else 0
        if here:
            contribution = READING_WEIGHTS["near_your_work"] * min(here / 4, 1.0)
            score += contribution
            region = clusters.get(p.cluster)
            reasons.append({
                "signal": "near_your_work", "effect": "adds",
                "weight": round(contribution, 3),
                "detail": (f"{here} of your works or projects sit in this "
                           f"region ({', '.join((_json(region.terms, []) if region else [])[:3])})")})

        if vectors is not None and brief_rows and p.row < vectors.shape[0]:
            import numpy as np
            distances = np.linalg.norm(
                vectors[brief_rows] - vectors[p.row], axis=1)
            nearest = float(distances.min())
            closeness = 1.0 / (1.0 + nearest * 8)
            contribution = READING_WEIGHTS["close_to_a_brief"] * closeness
            if closeness > 0.25:
                score += contribution
                who = briefs[int(distances.argmin())]
                reasons.append({
                    "signal": "close_to_a_brief", "effect": "adds",
                    "weight": round(contribution, 3),
                    "detail": f"close to your {who.label} brief"})

        if p.cited_by and best_cited:
            contribution = (READING_WEIGHTS["taken_up"]
                            * math.log1p(p.cited_by) / math.log1p(best_cited))
            score += contribution
            reasons.append({"signal": "taken_up", "effect": "adds", "weight": round(contribution, 3),
                            "detail": f"cited {p.cited_by} times"})

        if p.year and p.year >= this_year - RECENT_YEARS:
            contribution = (READING_WEIGHTS["recent"]
                            * (1 - (this_year - p.year) / RECENT_YEARS))
            score += contribution
            reasons.append({"signal": "recent", "effect": "adds", "weight": round(contribution, 3),
                            "detail": f"published {p.year}"})

        drift_reason = _drift_reason(drift, p.x, p.y)
        if drift_reason:
            score += drift_reason["weight"]
            reasons.append(drift_reason)

        if p.cluster in live:
            contribution = READING_WEIGHTS["live_project"]
            score += contribution
            reasons.append({"signal": "live_project", "effect": "adds", "weight": round(contribution, 3),
                            "detail": live[p.cluster]})
        elif p.cluster is not None and p.cluster in activity:
            # Collected in a region with nothing current behind it. Not
            # dropped — you may come back — but it should not outrank live work.
            score *= DORMANT_FACTOR
            reasons.append({"signal": "dormant", "effect": "damps",
                            "weight": DORMANT_FACTOR,
                            "detail": "no active project or recent work in this "
                                      "region"})

        if not reasons:
            continue
        candidates.append({
            "ref": p.ref, "label": p.label, "year": p.year,
            "cited_by": p.cited_by, "cluster": p.cluster,
            "region": ", ".join((_json(clusters[p.cluster].terms, [])[:3]
                                 if p.cluster in clusters else [])),
            "score": round(score, 3), "reasons": reasons,
        })

    candidates.extend(frontier_rows)
    candidates.sort(key=lambda c: -c["score"])
    collected = sum(1 for p in points
                    if p.kind.value == "corpus" and p.reading == "collected")
    marked = sum(1 for p in points
                 if p.kind.value == "corpus" and p.reading != "collected")

    return _block(
        space.created_at, "map, Zotero, OpenAlex and the frontier feed",
        candidates=candidates[:limit],
        collected_unread=collected, with_reading_evidence=marked,
        weights=READING_WEIGHTS,
        drift=({"cut": drift["cut"], "older": drift["older"],
                "recent": drift["recent"], "length": round(drift["length"], 4)}
               if drift else None),
        note=("Candidates are items you collected with no evidence of reading, "
              "and papers from the frontier feed you do not hold at all. The "
              "first is weaker than 'unread' — reading outside Zotero leaves "
              "no trace — so mark what you have read and the list will stop "
              "offering it."),
    )
