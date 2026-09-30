"""The four signals, scored separately — M6-T1, T2, T3, T4.

`DESIGN.md` §4 is emphatic that these never combine into one number, and the
reason is practical rather than aesthetic. A region you have read densely and
never written in calls for writing. A region the field is busy with and you
have not touched calls for reading. Average the two and the top row of the
ranking is a thing nobody can act on, because the action was the information
the average destroyed.

So: four scores, four gates, four sections on the page, and no total anywhere.

Every score is a weighted sum of named inputs, each carrying its value, its
weight and what it contributed. A ranking you cannot take apart is one you
cannot disagree with, and disagreeing is most of what this page is for.

**Signal 3 carries the honesty mechanism.** A region where your reading is
deep and the literature has gone quiet is either a real hole in the field or a
hole in your own collecting, and those look identical from inside the library.
The frontier is the only thing that can tell them apart, so every signal-3
candidate is confirmed against it — and a region the frontier has never been
asked about is unconfirmed, not confirmed by default. Silence from a source
that was never queried is not agreement.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from app import settings
from app.signals.regions import FRONTIER_RECENT_YEARS, Region

# Deliberately few and deliberately visible. These are the whole of the
# judgement: everything else is a count.
WEIGHTS = {
    # 1 · read, never written
    "collecting": 2.0,
    "reading_evidence": 1.5,
    "working_here": 1.0,
    "distance_from_your_work": 1.0,     # a factor, not a term
    # 2 · field-active, you're absent
    "field_activity": 2.5,
    "field_recency": 1.0,
    "thin_reading": 1.5,
    # 3 · thin literature, strong grounding
    "grounding": 2.0,
    "corpus_quiet": 2.0,
    "nothing_recent": 1.0,
    # 4 · thin reading
    "low_density": 2.0,
}

# A region densely enough read to ground a claim about its literature, and one
# thin enough to count as unexplored. Both are judgements about your own
# library rather than facts about the world, so both are settings.
DENSE_REGION = 25
THIN_REGION = 15

# Years since the newest thing you hold in a region before its literature
# counts as having gone quiet.
QUIET_YEARS = 3

# At most this many recent on-target frontier papers and the field agrees the
# area is quiet. Above it, the frontier is telling you the papers exist and
# you have not collected them.
CONFIRM_RECENT_MAX = 2

SIGNALS = {
    1: ("Read, never written", "you already know it — write"),
    2: ("Field-active, you're absent", "read in, then decide"),
    3: ("Thin literature, strong grounding", "a real hole you're equipped for"),
    4: ("Thin reading", "scout — may be quiet for good reason"),
}


@dataclass
class Input:
    """One named term of a score, with what it was computed from."""
    name: str
    effect: str          # "adds" | "scales"
    weight: float
    value: float
    contribution: float
    detail: str

    def as_dict(self) -> dict:
        return {"input": self.name, "effect": self.effect,
                "weight": round(self.weight, 3), "value": round(self.value, 4),
                "contribution": round(self.contribution, 3),
                "detail": self.detail}


@dataclass
class Scored:
    signal: int
    region: Region
    score: float = 0.0
    inputs: list = field(default_factory=list)
    confirmation: Optional[dict] = None


@dataclass
class Scale:
    """The library's own maxima, so every value is relative to this corpus."""
    collected: int = 1
    work_distance: float = 0.0
    frontier_citations: int = 0
    spread: float = 0.0
    median_spread: float = 0.0
    has_works: bool = False


def thresholds() -> dict:
    """The three judgements that decide which gates open, and their source."""
    return {
        "dense_region": int(settings.num("DENSE_REGION", DENSE_REGION)),
        "thin_region": int(settings.num("THIN_REGION", THIN_REGION)),
        "quiet_years": int(settings.num("QUIET_YEARS", QUIET_YEARS)),
    }


def scale_of(regions: list) -> Scale:
    spreads = sorted(r.spread for r in regions if r.spread)
    return Scale(
        collected=max((r.collected for r in regions), default=1) or 1,
        work_distance=max((r.work_distance or 0.0 for r in regions), default=0.0),
        frontier_citations=max((r.frontier_citations for r in regions), default=0),
        spread=max(spreads, default=0.0),
        median_spread=spreads[len(spreads) // 2] if spreads else 0.0,
        has_works=any(r.work_distance is not None for r in regions),
    )


def score_all(regions: list) -> tuple[list, list]:
    """Score every region under every signal whose gate it passes.

    A region may appear under more than one signal, and that is not a bug: a
    thin region the field is busy with is a signal-2 candidate *and* a
    signal-4 one, read two different ways. Each row says which reading it is.

    Returns the candidates and the regions that could not be assessed, which
    is a different thing from scoring zero.
    """
    scale = scale_of(regions)
    limits = thresholds()
    scored, unassessable = [], []
    for r in regions:
        for fn in (signal_one, signal_two, signal_three, signal_four):
            out = fn(r, scale, limits)
            if isinstance(out, Scored):
                scored.append(out)
            elif out is not None:
                unassessable.append(out)
    return scored, unassessable


# --- 1 · read, never written — M6-T1 ---------------------------------------


def signal_one(r: Region, scale: Scale, limits: dict):
    """Corpus-dense regions far from every ledger work.

    Reading investment × distance from your nearest publication, exactly as
    `DESIGN.md` §4 states it: the distance is a factor rather than a term,
    because a region you have read heavily and *already written in* is not an
    opportunity however much of it you hold.
    """
    if r.written or not r.collected:
        return None

    inputs = [
        Input("collecting", "adds", WEIGHTS["collecting"],
              _log_share(r.collected, scale.collected), 0.0,
              f"{r.collected} items collected here, against "
              f"{scale.collected} in your largest region"),
        Input("reading_evidence", "adds", WEIGHTS["reading_evidence"],
              r.read / max(r.collected, 1), 0.0,
              f"{r.read} of {r.collected} carry evidence of being read"
              f"{f', {r.engaged} with a note or annotation' if r.engaged else ''}"),
    ]
    if r.projects:
        inputs.append(Input(
            "working_here", "adds", WEIGHTS["working_here"], 1.0, 0.0,
            f"{r.projects[0]} sits in this region and nothing is published here"))

    if not scale.has_works:
        distance = Input("distance_from_your_work", "scales",
                         WEIGHTS["distance_from_your_work"], 1.0, 1.0,
                         "no publication of yours could be placed on this map, "
                         "so distance ranks nothing here")
    else:
        value = _share(r.work_distance, scale.work_distance)
        near = r.nearest_work or {}
        distance = Input(
            "distance_from_your_work", "scales",
            WEIGHTS["distance_from_your_work"], value, value,
            (f"your nearest published work is {near.get('label', 'unknown')} "
             f"at {r.work_distance:.3f}, against {scale.work_distance:.3f} at "
             "the region you sit furthest from — this is a share of that, not "
             "a distance anyone else could read"
             if r.work_distance is not None
             else "no work of yours could be placed near this region"))
    return _assemble(1, r, inputs, distance)


# --- 2 · field-active, you're absent — M6-T2 --------------------------------


def signal_two(r: Region, scale: Scale, limits: dict):
    """Thin in your reading and your writing, busy in the frontier.

    Cannot be computed from Zotero alone, and cannot be computed from a region
    the frontier was never asked about. A region with no gathered set is
    returned as unassessable rather than scored zero — the gatherer visits
    live regions first and dormant ones last, so "no frontier items" is very
    often a statement about the budget and not about the field.
    """
    if r.written or r.collected > limits["thin_region"]:
        return None
    if not r.frontier_queried:
        return {
            "signal": 2, "cluster": r.cluster, "region": r.label(),
            "reason": ("no frontier set has been gathered for this region, so "
                       "whether the field is busy here is unknown. Gather on "
                       "the Frontier page and it will be scored."),
        }

    on_target = r.on_target
    if not on_target:
        return None

    inputs = [
        Input("field_activity", "adds", WEIGHTS["field_activity"],
              _share(r.frontier_citations, scale.frontier_citations), 0.0,
              f"the median on-target frontier paper here has "
              f"{r.frontier_citations} citations"),
        Input("field_recency", "adds", WEIGHTS["field_recency"],
              r.frontier_recent / max(len(on_target), 1), 0.0,
              f"{r.frontier_recent} of {len(on_target)} landed in the last "
              f"{FRONTIER_RECENT_YEARS} years"),
        Input("thin_reading", "adds", WEIGHTS["thin_reading"],
              1.0 - _share(r.collected, limits["thin_region"]), 0.0,
              f"you hold {r.collected} items here, under the "
              f"{limits['thin_region']} that counts as thin"),
    ]
    return _assemble(2, r, inputs)


# --- 3 · thin literature, strong grounding — M6-T3 --------------------------


def signal_three(r: Region, scale: Scale, limits: dict):
    """Densely read regions whose literature has gone quiet — and the split.

    The strongest signal available and the easiest to fool yourself with. A
    region where you have read deeply and nothing recent has arrived looks
    identical whether the field stopped publishing or you stopped collecting,
    and only the frontier can tell you which. So every candidate here is
    labelled, and an unconfirmed one is stated as a reading gap rather than
    ranked as an opening.
    """
    quiet = r.quiet_years
    if r.collected < limits["dense_region"] or quiet is None:
        return None
    if quiet < limits["quiet_years"]:
        return None

    horizon = max(limits["quiet_years"] * 3, 1)
    inputs = [
        Input("grounding", "adds", WEIGHTS["grounding"],
              _log_share(r.collected, scale.collected), 0.0,
              f"{r.collected} items collected here and {r.written} of your "
              f"works sit in it"),
        Input("reading_evidence", "adds", WEIGHTS["reading_evidence"],
              r.read / max(r.collected, 1), 0.0,
              f"{r.read} of {r.collected} carry evidence of being read"),
        Input("corpus_quiet", "adds", WEIGHTS["corpus_quiet"],
              _share(quiet, horizon), 0.0,
              f"the newest thing you hold here is from {r.newest_year}, "
              f"{quiet} years ago"),
        Input("nothing_recent", "adds", WEIGHTS["nothing_recent"],
              1.0 - (r.recent_collected / max(r.collected, 1)), 0.0,
              f"{r.recent_collected} of {r.collected} were published recently"),
    ]
    out = _assemble(3, r, inputs)
    out.confirmation = _confirm(r)
    return out


def _confirm(r: Region) -> dict:
    """Is the hole in the field, or only in your library?

    Three answers, and two of them are "unconfirmed". A region the frontier
    has never been asked about cannot confirm anything: silence from a source
    that was never queried is not agreement, and treating it as agreement is
    exactly how this signal would come to flatter you.
    """
    recent = r.frontier_recent
    if not r.frontier_queried:
        return {
            "confirmed": False, "basis": "unchecked",
            "detail": ("no frontier set has been gathered for this region, so "
                       "nothing outside your library has checked this. Until "
                       "it has, the honest reading is a gap in your "
                       "collecting."),
        }
    if recent <= CONFIRM_RECENT_MAX:
        return {
            "confirmed": True, "basis": "the frontier agrees",
            "detail": (f"the frontier returned {recent} on-target paper"
                       f"{'' if recent == 1 else 's'} from the last "
                       f"{FRONTIER_RECENT_YEARS} years across "
                       f"{len(r.on_target)} retrieved — the field is quiet "
                       "here too."),
        }
    return {
        "confirmed": False, "basis": "the frontier says otherwise",
        "detail": (f"{recent} recent on-target papers came back that you do "
                   "not hold. The literature is not thin; your collection of "
                   "it is. This is a reading gap, not an opportunity."),
    }


# --- 4 · thin reading — M6-T4 ----------------------------------------------


def signal_four(r: Region, scale: Scale, limits: dict):
    """Low corpus density, no activity filter, ranked last.

    One input, deliberately. Every filter that would sharpen this signal is
    the thing that makes one of the other three, and applying them here would
    produce a fifth signal wearing the fourth's name. Some of what it returns
    is quiet because nobody cares, and the page says so beside every row.
    """
    if r.collected > limits["thin_region"]:
        return None
    inputs = [
        Input("low_density", "adds", WEIGHTS["low_density"],
              1.0 - _share(r.collected, scale.collected), 0.0,
              f"{r.collected} items, against {scale.collected} in your "
              f"largest region"),
    ]
    return _assemble(4, r, inputs)


# --- shared ----------------------------------------------------------------


def _assemble(signal: int, r: Region, inputs: list,
              factor: Optional[Input] = None) -> Scored:
    total = 0.0
    for i in inputs:
        i.value = max(0.0, min(i.value, 1.0))
        i.contribution = i.weight * i.value
        total += i.contribution
    if factor is not None:
        factor.value = max(0.0, min(factor.value, 1.0))
        factor.contribution = factor.value
        total *= factor.value
        inputs = inputs + [factor]
    return Scored(signal=signal, region=r, score=round(total, 4), inputs=inputs)


def _share(value, ceiling) -> float:
    if not ceiling or value is None:
        return 0.0
    return max(0.0, min(float(value) / float(ceiling), 1.0))


def _log_share(value, ceiling) -> float:
    """Counts compress: ninety-nine items is not ten times ten items' worth of
    grounding, and a linear share would say it was."""
    if not ceiling:
        return 0.0
    return max(0.0, min(math.log1p(value) / math.log1p(ceiling), 1.0))
