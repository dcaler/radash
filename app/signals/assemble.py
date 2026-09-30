"""Candidate assembly — M6-T6.

A score is not a candidate. `DESIGN.md` §7 sets the bar: each candidate shows
its signal type, its score with the inputs broken out, the corpus items behind
it, the frontier papers behind it, which of your work is nearest, and the
honest counter-case. **No candidate appears as a bare number.**

The counter-case is the part that does the work. Everything else on a row
argues for the candidate — that is what a score is — and a page of arguments
for things is a page you agree with by default. So every row also carries the
reasons not to: that the grounding is collecting rather than reading, that the
region is dormant, that it is diffuse enough to be a neighbourhood rather than
a topic, that the frontier set behind it is months old or was never gathered.
None of those are computed to be reassuring and none of them are optional.

Routing follows `DESIGN.md` §8 exactly. A signal 1, 2, or confirmed 3
candidate routes to a **read-in brief**; an unconfirmed 3 or a signal 4 routes
to a **corpus fill list** — because the honest response to a reading gap is
reading, and offering to draft a paper there would be flattery.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.mapping.cluster import MIN_CLUSTER_SIZE
from app.signals import signals as signals_mod
from app.signals.regions import Region

# How many of each kind of evidence travels with a candidate. Enough to
# recognise the region, not so many that the counter-case is below the fold.
EVIDENCE = 5

# Past this, the frontier set behind a candidate is old enough to say so.
FRONTIER_STALE_DAYS = 30

# A region whose members sit this much further from their own centre than the
# median region's do is diffuse enough that its label describes a
# neighbourhood rather than a topic.
DIFFUSE_RATIO = 1.5

ROUTES = {
    "brief": {
        "output": "brief",
        "label": "Read-in brief",
        "file": "{YYMMDD}_{slug}_readin_ra.txt",
        "detail": ("topic, focus lines, date range, seed DOIs from both the "
                   "corpus items and the frontier papers behind the score, "
                   "and the command to run"),
    },
    "fill": {
        "output": "fill",
        "label": "Corpus fill list",
        "file": "{YYMMDD}_{slug}_fill_ra.txt",
        "detail": ("the region's frontier minus everything already in your "
                   "library, ranked by the role each paper would play. If a "
                   "project lit review is what is actually needed, "
                   "rabbitHole's gather is the better tool and this says so"),
    },
}


def candidates(scored: list, scale: signals_mod.Scale, limits: dict) -> list:
    """Every scored region, assembled into something a person can argue with."""
    rows = [candidate(s, scale, limits) for s in scored]
    # Within a signal, by score. Across signals the order is the signal
    # number, and signal 4 is last by construction — it is the loosest of the
    # four and `DESIGN.md` §4 ranks it last deliberately.
    rows.sort(key=lambda c: (c["signal"], -c["score"]))
    return rows


def candidate(s: signals_mod.Scored, scale: signals_mod.Scale,
              limits: dict) -> dict:
    r = s.region
    name, move = signals_mod.SIGNALS[s.signal]
    confirmed = bool(s.confirmation is None or s.confirmation["confirmed"])
    route = ROUTES["brief" if (s.signal != 4 and confirmed) else "fill"]

    return {
        "signal": s.signal,
        "signal_name": name,
        "move": move,
        "cluster": r.cluster,
        "lineage": r.lineage,
        "region": r.label(),
        "terms": r.terms,
        "exemplar": r.exemplar,
        "score": s.score,
        "inputs": [i.as_dict() for i in s.inputs],
        "confirmation": s.confirmation,
        "bridge": r.bridge,
        "nearest_work": r.nearest_work,
        "counts": {
            "collected": r.collected, "read": r.read, "engaged": r.engaged,
            "written": r.written, "recent_written": r.recent_written,
            "newest_year": r.newest_year, "median_year": r.median_year,
            "recent_collected": r.recent_collected,
            "projects": r.projects, "live": r.live,
            "live_because": r.live_because,
            "spread": round(r.spread, 4) if r.spread else None,
        },
        "corpus": _corpus_evidence(r),
        "frontier": _frontier_evidence(r),
        "topics": [{"id": t.topic_id, "name": t.topic_name,
                    "share": round(t.share, 3), "members": t.members}
                   for t in r.topics[:3]],
        "frontier_age_days": _age_days(r.frontier_fetched_at),
        "counter_case": counter_case(s, scale, limits),
        "route": {**route, "why": _why_routed(s, confirmed)},
    }


def _why_routed(s: signals_mod.Scored, confirmed: bool) -> str:
    if s.signal == 4:
        return ("Signal 4 applies no activity filter, so the response is to "
                "go and look, not to go and write.")
    if s.signal == 3 and not confirmed:
        return ("An unconfirmed signal 3 is a gap in your library rather than "
                "in the field, and the honest response to a reading gap is "
                "reading.")
    if s.signal == 2:
        return ("The field is moving here and you are not in it, so the first "
                "move is to read in properly.")
    if s.signal == 3:
        return ("The frontier agrees the area is quiet and you are already "
                "grounded in it.")
    return ("The grounding is already paid for — you have read here and "
            "published nothing.")


# --- evidence ---------------------------------------------------------------


def _corpus_evidence(r: Region) -> list:
    """The items behind the score: what you have read here, most-taken-up first.

    Ordered with the read items ahead of the merely collected, because the
    question this list answers is "what do I actually know about this region",
    and an unopened PDF is not an answer to it.
    """
    ordered = sorted(
        r.members,
        key=lambda p: (0 if p.reading != "collected" else 1, -(p.cited_by or 0)))
    return [{
        "ref": p.ref, "label": p.label, "year": p.year,
        "reading": p.reading, "cited_by": p.cited_by,
        "url": f"https://doi.org/{p.doi}" if p.doi else None,
    } for p in ordered[:EVIDENCE]]


def _frontier_evidence(r: Region) -> list:
    """The frontier papers behind the score — on-target only.

    An off-target paper was found by a topic this region only nominally has,
    so it is not evidence about the region. It is counted in the counter-case
    instead, where it belongs.
    """
    ordered = sorted(r.on_target, key=lambda i: -(i.cited_by or 0))
    return [{
        "title": i.title, "year": i.year, "venue": i.venue,
        "cited_by": i.cited_by, "topic": i.topic_name,
        "distance": i.distance_to_region,
        "url": (f"https://doi.org/{i.doi}" if i.doi
                else f"https://openalex.org/{i.openalex_id}"),
    } for i in ordered[:EVIDENCE]]


# --- the counter-case -------------------------------------------------------


def counter_case(s: signals_mod.Scored, scale: signals_mod.Scale,
                 limits: dict) -> list:
    """Why this might be a bad idea. Computed, never omitted."""
    r = s.region
    against = []

    if s.confirmation and not s.confirmation["confirmed"]:
        against.append(s.confirmation["detail"])

    if r.collected and r.read / r.collected < 0.2:
        against.append(
            f"{r.collected - r.read} of {r.collected} items here carry no "
            "evidence of being read, so the grounding this score rests on is "
            "collecting rather than reading. Zotero only sees what you tag, "
            "annotate, or open inside it.")

    if not r.live:
        against.append(
            "Nothing current sits behind this region — no project brief, and "
            "no publication of yours recently. Corpus mass is a record of "
            "where you have been, not of where you are.")

    off = len(r.frontier) - len(r.on_target)
    if off:
        against.append(
            f"{off} of {len(r.frontier)} frontier papers landed outside this "
            "region, so the OpenAlex topic that found them is broader than "
            "the region is.")

    if (r.spread and scale.median_spread
            and r.spread > scale.median_spread * DIFFUSE_RATIO):
        against.append(
            f"Its members sit a mean of {r.spread:.3f} from their own centre "
            f"against a median of {scale.median_spread:.3f} across your "
            "regions. This is a neighbourhood rather than a topic, and its "
            "terms describe it loosely.")

    if r.collected <= MIN_CLUSTER_SIZE + 2:
        against.append(
            f"At {r.collected} items this is near the floor at which raDash "
            "will call anything a region at all.")

    if not r.frontier_queried:
        # Signal 3 has already said this, in the words of the confirmation it
        # could not make. Saying it twice on one row reads as two problems.
        if s.confirmation is None:
            against.append(
                "No frontier set has been gathered here, so nothing outside "
                "your own library has been consulted about it.")
    else:
        age = _age_days(r.frontier_fetched_at)
        if age is not None and age > FRONTIER_STALE_DAYS:
            against.append(
                f"The frontier set behind this is {age:.0f} days old.")

    if s.signal == 1 and r.nearest_work and r.spread:
        near = r.nearest_work.get("distance")
        if near is not None and near < r.spread:
            against.append(
                f"Your nearest published work sits {near:.3f} from this "
                f"region's centre, closer than its own members average "
                f"({r.spread:.3f}). \"Never written\" here is a statement "
                "about which region that work landed in, not about distance.")

    if s.signal == 2:
        against.append(
            f"Activity is measured over the {len(r.on_target)} on-target "
            "papers the gatherer retrieved for this region, not over the "
            "field. A capped sample can only tell you the field is at least "
            "this busy.")

    if s.signal == 4:
        against.append(
            "Signal 4 applies no activity filter at all. Some of what it "
            "returns is thin because nobody has found it worth writing about, "
            "and nothing here distinguishes that case from an opening.")

    if s.signal == 3 and r.newest_year:
        against.append(
            f"\"Quiet since {r.newest_year}\" is measured on publication "
            "years in your library. An item you collected last week with a "
            "2015 date does not move it, and a field that moved to "
            "preprints will read as quiet here.")

    if not against:
        # Not "nothing is wrong with this" — "none of the checks fired". The
        # list above is finite and was written for the failure modes seen on
        # this library, which is not the same as every reason a candidate
        # might be a bad idea.
        against.append(
            "None of the checks here fired, which is weaker than it reads: "
            "they are a fixed list written for the ways this library has "
            "misled before, not a survey of the ways an area can be a bad "
            "bet.")

    return against


def _age_days(dt: Optional[datetime]) -> Optional[float]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc) - dt).total_seconds() / 86400, 2)
