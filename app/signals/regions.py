"""What each region is, before anything judges it — M6-T1…T5's shared input.

Every signal asks a different question of the same handful of facts: how much
of this region you have collected, how much of it you have evidence of having
read, what you have published near it, when its literature last moved, and
what the frontier returned when it was asked. Counting those once and scoring
four times keeps the four signals commensurable in their inputs while staying
separate in their verdicts.

Two things are deliberate here.

**Distance is measured in the geometry the regions were found in.** The map's
display coordinates are two components of a hundred; a distance read off them
is a distance in the picture, not in the space. Where the fitted vectors can
be loaded the signals use the clustering sub-space; where they cannot, they
fall back to the display coordinates and say so, because a degraded number
that announces itself beats an absent panel.

**A region that was never asked about is not a region with nothing in it.**
The frontier gathers live regions first and dormant ones only if the budget
holds, so "no frontier items" means one of two very different things. Regions
carry `frontier_queried`, and the signal that depends on the frontier refuses
to score a region that was never queried rather than reading silence as
quiet.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np
from sqlmodel import Session, select

from app.mapping import cluster as cluster_mod
from app.mapping import fit as fit_mod
from app.models import FrontierItem, MapCluster, MapPoint, MapSpace, RegionTopic

# Reading evidence, in the tiers Zotero can actually support. `collected` is
# the absence of evidence, not the presence of neglect — reading in Preview,
# on a tablet or on paper leaves no trace — so every count built on these is
# a floor.
READ_TIERS = ("opened", "noted", "annotated", "marked")
# Evidence you did something with the item rather than opened it. A note or a
# highlight is the strongest claim this library can make about grounding.
ENGAGED_TIERS = ("noted", "annotated", "marked")

# A frontier paper published within this many years counts as the field
# currently moving. The frontier query already bounds itself to 18 months;
# this catches the items whose stated year lags their publication date.
FRONTIER_RECENT_YEARS = 2


@dataclass
class Region:
    """One region of the map, counted rather than judged."""
    cluster: int
    lineage: str
    name: Optional[str] = None
    terms: list = field(default_factory=list)
    exemplar: Optional[str] = None

    # What you hold here.
    collected: int = 0
    read: int = 0            # any evidence at all
    engaged: int = 0         # a note or an annotation, not merely opened
    newest_year: Optional[int] = None
    median_year: Optional[int] = None
    recent_collected: int = 0
    members: list = field(default_factory=list)      # MapPoint, corpus only

    # What you have written here, and what you are doing here.
    written: int = 0
    recent_written: int = 0
    projects: list = field(default_factory=list)     # brief labels
    live_because: Optional[str] = None               # why it counts as live

    # Where it sits, and how far your nearest publication is from it.
    centroid: Optional["np.ndarray"] = None
    spread: Optional[float] = None
    nearest_work: Optional[dict] = None
    work_distance: Optional[float] = None

    # What the frontier said, if it was asked.
    frontier_queried: bool = False
    frontier: list = field(default_factory=list)     # FrontierItem
    topics: list = field(default_factory=list)       # RegionTopic
    frontier_fetched_at: Optional[datetime] = None

    # Between two regions you are active in (M6-T5).
    bridge: Optional[dict] = None

    @property
    def live(self) -> bool:
        return self.live_because is not None

    @property
    def on_target(self) -> list:
        return [i for i in self.frontier if not i.off_target]

    @property
    def frontier_recent(self) -> int:
        year = datetime.now(timezone.utc).year - FRONTIER_RECENT_YEARS
        return sum(1 for i in self.on_target if i.year and i.year >= year)

    @property
    def frontier_citations(self) -> int:
        """Median citations across the on-target frontier set.

        Median rather than total: a set of twelve papers is capped by the
        gatherer, so a sum measures how many were retrieved as much as how
        busy the field is.
        """
        cited = sorted(i.cited_by or 0 for i in self.on_target)
        return int(cited[len(cited) // 2]) if cited else 0

    @property
    def quiet_years(self) -> Optional[int]:
        """Years since the newest thing you hold here was published."""
        if self.newest_year is None:
            return None
        return max(0, datetime.now(timezone.utc).year - self.newest_year)

    def label(self) -> str:
        return (self.name or " · ".join(self.terms[:3])
                or f"region {self.cluster}")


@dataclass
class Context:
    """The space these regions came from, and how they were measured."""
    space: Optional[MapSpace] = None
    geometry: str = "display coordinates"
    exact_geometry: bool = False
    unclustered: int = 0
    works_placed: int = 0
    briefs_placed: int = 0
    frontier_queried: int = 0


def gather(db: Session,
           space: Optional[MapSpace] = None) -> tuple[list, Context]:
    """Every region of the current space, with the facts the signals need."""
    space = space or fit_mod.current(db)
    context = Context(space=space)
    if space is None:
        return [], context

    clusters = db.exec(select(MapCluster)
                       .where(MapCluster.space_id == space.id)).all()
    points = db.exec(select(MapPoint)
                     .where(MapPoint.space_id == space.id)).all()
    if not clusters:
        return [], context

    coords, context.geometry, context.exact_geometry = _geometry(space, points)
    works = [p for p in points if p.kind.value == "work"]
    briefs = [p for p in points if p.kind.value == "project"]
    context.works_placed = len(works)
    context.briefs_placed = len(briefs)
    context.unclustered = sum(1 for p in points
                              if p.kind.value == "corpus" and p.cluster is None)

    items_by_lineage: dict = {}
    for item in db.exec(select(FrontierItem)).all():
        items_by_lineage.setdefault(item.lineage, []).append(item)
    topics_by_lineage: dict = {}
    for topic in db.exec(select(RegionTopic)).all():
        topics_by_lineage.setdefault(topic.lineage, []).append(topic)

    this_year = datetime.now(timezone.utc).year
    from app.routers.status import ACTIVE_YEARS, RECENT_YEARS

    regions = []
    for c in clusters:
        lineage = c.lineage or f"c{c.cluster}"
        r = Region(cluster=c.cluster, lineage=lineage, name=c.label,
                   terms=_json(c.terms), exemplar=c.exemplar_label)

        r.members = [p for p in points
                     if p.cluster == c.cluster and p.kind.value == "corpus"]
        r.collected = len(r.members)
        r.read = sum(1 for p in r.members if p.reading in READ_TIERS)
        r.engaged = sum(1 for p in r.members if p.reading in ENGAGED_TIERS)
        years = sorted(p.year for p in r.members if p.year)
        if years:
            r.newest_year = years[-1]
            r.median_year = years[len(years) // 2]
            r.recent_collected = sum(1 for y in years
                                     if y >= this_year - RECENT_YEARS)

        here = [p for p in works if p.cluster == c.cluster]
        r.written = len(here)
        r.recent_written = sum(1 for p in here
                               if p.year and p.year >= this_year - ACTIVE_YEARS)
        r.projects = [p.label or "a project" for p in briefs
                      if p.cluster == c.cluster]
        if r.projects:
            r.live_because = f"project {r.projects[0]} sits in this region"
        elif r.recent_written:
            recent = max(p.year for p in here if p.year)
            r.live_because = f"you published here in {recent}"

        r.centroid, r.spread = _centre_and_spread(coords, r.members)
        r.nearest_work, r.work_distance = _nearest(coords, r.centroid, works)

        r.frontier = items_by_lineage.get(lineage, [])
        r.topics = sorted(topics_by_lineage.get(lineage, []),
                          key=lambda t: -t.share)
        r.frontier_queried = bool(r.frontier or r.topics)
        stamps = [_aware(i.fetched_at) for i in r.frontier if i.fetched_at]
        stamps += [_aware(t.resolved_at) for t in r.topics if t.resolved_at]
        r.frontier_fetched_at = max(stamps) if stamps else None
        regions.append(r)

    context.frontier_queried = sum(1 for r in regions if r.frontier_queried)
    _assign_bridges(regions)
    regions.sort(key=lambda r: -r.collected)
    return regions, context


# --- geometry ---------------------------------------------------------------


def _geometry(space: MapSpace, points) -> tuple[dict, str, bool]:
    """Coordinates per point row, in the best geometry available.

    The fitted vectors where they can be loaded; the display pair where they
    cannot. The second is a real degradation — two components out of a hundred
    — so it is named in the response rather than folded in silently.
    """
    vectors = fit_mod.load_vectors(space)
    if vectors is not None:
        needed = max((p.row for p in points), default=-1)
        if needed < vectors.shape[0]:
            try:
                reduced = cluster_mod.clustering_space(vectors)
                return ({p.row: reduced[p.row] for p in points},
                        f"the fitted space, {reduced.shape[1]} components",
                        True)
            except Exception:
                pass
    return ({p.row: np.array([p.x, p.y]) for p in points},
            "the map's two display components", False)


def _centre_and_spread(coords: dict, members: list):
    """A region's centre, and how far its own members sit from it.

    The spread is the yardstick every distance here is read against: a region
    whose members sit a long way from their own centre is a neighbourhood, not
    a topic, and a publication "near" it is near in a much weaker sense.
    """
    rows = [coords[p.row] for p in members if p.row in coords]
    if not rows:
        return None, None
    stacked = np.vstack(rows)
    centre = stacked.mean(axis=0)
    spread = float(np.linalg.norm(stacked - centre, axis=1).mean())
    return centre, spread


def _nearest(coords: dict, centre, works: list):
    """Your nearest published work to a region's centre."""
    if centre is None or not works:
        return None, None
    best, best_d = None, None
    for p in works:
        if p.row not in coords:
            continue
        d = float(np.linalg.norm(coords[p.row] - centre))
        if best_d is None or d < best_d:
            best, best_d = p, d
    if best is None:
        return None, None
    return ({"ref": best.ref, "label": best.label, "year": best.year,
             "distance": round(best_d, 4)}, best_d)


def _assign_bridges(regions: list) -> None:
    """Mark regions sitting between two you are already active in — M6-T5.

    An attribute rather than a fifth signal, as `DESIGN.md` §4 has it: cheap
    to compute, and what it marks is synthesis work whose two ends are both
    already yours.

    The test is geometric and deliberately strict. Take the two nearest active
    regions; the candidate is between them only if it sits inside the angle
    they subtend — the vectors from the candidate to each of them point in
    opposing directions. A region merely *near* two active ones is beside
    them, not between them, and calling that a bridge would mark half the map.
    """
    active = [r for r in regions
              if r.centroid is not None and (r.written or r.projects)]
    if len(active) < 2:
        return
    for r in regions:
        if r.centroid is None or r.written or r.projects:
            continue
        ranked = sorted(((float(np.linalg.norm(a.centroid - r.centroid)), a)
                         for a in active), key=lambda t: t[0])
        (d_a, a), (d_b, b) = ranked[0], ranked[1]
        if np.dot(a.centroid - r.centroid, b.centroid - r.centroid) >= 0:
            continue          # both on the same side: beside, not between
        r.bridge = {
            "between": [
                {"cluster": a.cluster, "label": a.label(),
                 "distance": round(d_a, 4)},
                {"cluster": b.cluster, "label": b.label(),
                 "distance": round(d_b, 4)},
            ],
            "detail": (f"sits between {a.label()} and {b.label()}, both of "
                       "which you are active in"),
        }


# --- small shared helpers ---------------------------------------------------


def _json(raw, default=None):
    try:
        return json.loads(raw) if raw else (default if default is not None else [])
    except (ValueError, TypeError):
        return default if default is not None else []


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
