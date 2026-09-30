"""The frontier — what is being published in your regions that you do not have.

Three decisions shape this.

**Regions are resolved to topics by their members, not their labels.** A
region's label is "toolkits, scratch, replicating", which is a good name for a
human and a poor search query. The papers in it carry OpenAlex topics, and
those are what OpenAlex itself would use to find more of the same. So the
region's topics are its members' topics, weighted by how many carry each.

**Everything is scoped to lineage, not cluster number.** Cluster numbers are
an artefact of a particular fit. A frontier set gathered for region 7 should
survive the refit that renames it region 3, and lineage is what makes that
possible.

**Unreachable is a normal state.** Every set is cached with the time it was
gathered, and an offline read serves the cache with its age attached rather
than an empty panel. The dashboard's whole stance is that stale-and-labelled
beats absent.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from app.config import Config
from app.ledger.fingerprint import normalize_doi
from app.sources.openalex import BASE, TIMEOUT, _headers, _params, reconstruct_abstract

# How far back a "frontier" reaches. Long enough that a quiet field still has
# something in it, short enough that the list is about now.
FRONTIER_MONTHS = 18

# Kept per region. More than this is a reading list nobody opens.
PER_REGION = 12

# Retrieved per region, before the map filters them. An OpenAlex topic is far
# broader than one of these regions — the solar region resolves to "Energy and
# Environment Impacts", which is most of energy research — so querying it and
# taking the top twelve by citations returns whatever that whole field is
# excited about. Retrieving a hundred and keeping the ones that land nearest
# the region turns a broad topic into a narrow question, and on the solar
# region moves the list from hydrogen electrolysis to PV co-location.
RETRIEVE_PER_REGION = 100

# A topic has to describe a real share of a region before it is worth
# searching: one paper in ninety carrying a topic says nothing about the
# region, and querying it would return a neighbouring field's output.
MIN_TOPIC_SHARE = 0.15
TOPICS_PER_REGION = 2

# Between requests, so a refresh of twenty regions is a polite forty seconds
# rather than a burst.
SLEEP_BETWEEN = 0.4


@dataclass
class Topic:
    topic_id: str
    name: str
    share: float
    members: int


@dataclass
class FrontierResult:
    items: list = field(default_factory=list)
    topics: list = field(default_factory=list)
    regions_queried: int = 0
    failures: list = field(default_factory=list)
    seconds: float = 0.0

    def summary(self) -> dict:
        return {"items": len(self.items), "regions": self.regions_queried,
                "topics": len(self.topics), "failures": self.failures,
                "seconds": round(self.seconds, 2)}


def topics_for_region(member_dois: list, enrichment: dict) -> list[Topic]:
    """A region's topics, from the topics its own papers carry."""
    counts: dict[str, dict] = {}
    seen = 0
    for doi in member_dois:
        row = enrichment.get(normalize_doi(doi))
        if not isinstance(row, dict):
            continue
        seen += 1
        for t in row.get("topics") or []:
            tid = t.get("id")
            if not tid:
                continue
            entry = counts.setdefault(tid, {"name": t.get("name") or tid, "n": 0})
            entry["n"] += 1
    if not seen:
        return []
    out = [Topic(topic_id=tid, name=v["name"], members=v["n"], share=v["n"] / seen)
           for tid, v in counts.items()]
    out.sort(key=lambda t: -t.share)
    return [t for t in out if t.share >= MIN_TOPIC_SHARE][:TOPICS_PER_REGION]


def fetch(cfg: Config, topics: list[Topic], held_dois: set,
          per_region: int = PER_REGION) -> tuple[list, Optional[str]]:
    """Recent, well-cited work in these topics that is not already yours.

    Sorted by citation count rather than date: a frontier list ordered by
    recency is dominated by whatever was posted yesterday, and the question is
    what the field has taken up, not what it has most recently emitted.
    """
    if not topics:
        return [], None
    since = (datetime.now(timezone.utc)
             - timedelta(days=30 * FRONTIER_MONTHS)).date().isoformat()
    filters = ("topics.id:" + "|".join(t.topic_id for t in topics)
               + f",from_publication_date:{since}")
    params = _params(
        cfg, filter=filters, sort="cited_by_count:desc",
        select="id,doi,title,display_name,publication_year,cited_by_count,"
               "primary_location,abstract_inverted_index,topics")
    params["per-page"] = RETRIEVE_PER_REGION

    try:
        with httpx.Client(timeout=TIMEOUT, headers=_headers(cfg)) as client:
            resp = client.get(f"{BASE}/works", params=params)
            resp.raise_for_status()
            rows = resp.json().get("results") or []
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"

    out = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        doi = normalize_doi(raw.get("doi") or "")
        # Something already in your library is not frontier, it is backlog —
        # and it is already ranked on the reading list.
        held = bool(doi and doi in held_dois)
        loc = raw.get("primary_location") or {}
        src = (loc.get("source") or {}) if isinstance(loc, dict) else {}
        topic = (raw.get("topics") or [{}])[0]
        out.append({
            "openalex_id": (raw.get("id") or "").rsplit("/", 1)[-1],
            "doi": doi or None,
            "title": raw.get("title") or raw.get("display_name") or "",
            "year": raw.get("publication_year"),
            "venue": src.get("display_name") if isinstance(src, dict) else None,
            "cited_by": raw.get("cited_by_count"),
            "topic_id": (topic.get("id") or "").rsplit("/", 1)[-1] or None,
            "topic_name": topic.get("display_name"),
            "abstract": reconstruct_abstract(raw.get("abstract_inverted_index")),
            "already_held": held,
        })
    return out, None


def pace() -> None:
    """Between regions. A refresh is not a race."""
    time.sleep(SLEEP_BETWEEN)
