"""The corpus fill list — M7-T2.

A thin region has two possible causes: the field is empty, or you have not
collected it. Signal 3's confirmed/unconfirmed split already tells those
apart, and until this milestone the unconfirmed case was a dead end — raDash
could diagnose a reading gap and do nothing about it.

This is its remedy. For a region where the frontier is active and your library
is thin, take the frontier set gathered for that region, subtract everything
already in your library, and rank what remains **by the role each paper would
play** rather than by one blended score (`DESIGN.md` §8):

    most-cited   the region's citation anchors — what everyone there cites
    central      nearest the region's centre — its core, not its edges
    bridging     connects this region to another you are active in
    frontier     published in the last 24 months — where the area is going

A paper can only earn one role, so the list reads as a shopping order rather
than as four overlapping lists. Roles are assigned in the order above: an
anchor that is also recent is still an anchor, because that is the reason to
collect it first.

Like the brief this is one file in `output/`. It adds nothing to Zotero; the
write stays yours.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.outputs.brief import WIDTH, _bullet, _wrap

# Published within this many years counts as where the area is going.
FRONTIER_YEARS = 2

# How many of each role reach the file. Five per role was the figure
# `DESIGN.md` §11 left open; it is a setting away from being tunable if a
# filled region turns out to need more.
PER_ROLE = 5

ROLES = (
    ("most-cited", "the region's citation anchors — what everyone there cites"),
    ("central", "nearest the region's centre — its core, not its edges"),
    ("bridging", "connects this region to another you are active in"),
    ("frontier", f"published in the last {FRONTIER_YEARS} years"),
)


def assign_roles(items: list, bridging_ids: Optional[set] = None,
                 now: Optional[datetime] = None) -> dict:
    """Sort the region's frontier into the four roles, each paper once.

    Each paper takes **the role it is best at**, not the first role that will
    have it. Assigning in order looked simpler and was wrong: "most-cited"
    would claim every paper carrying any citations at all, and the other three
    roles came back empty on a set where each was plainly represented. The
    point of four roles is that they are four different reasons to collect
    something, which a first-come rule quietly destroys.

    Bridging is decided first and separately, because it is the only role that
    is a fact about the map rather than a rank within this set: a paper
    landing inside another of your active regions is doing something none of
    the other three describe. The rest go to whichever of most-cited, central
    and frontier they rank highest on, compared against the other candidates
    rather than against a threshold nobody could set honestly.

    `items` are dicts as the frontier panel serves them: title, year,
    cited_by, distance, url.
    """
    now = now or datetime.now(timezone.utc)
    oldest_recent = now.year - FRONTIER_YEARS
    bridging_ids = bridging_ids or set()
    remaining = [i for i in items if not i.get("already_held")]
    out: dict = {name: [] for name, _ in ROLES}
    if not remaining:
        return out

    def ident(row):
        return row.get("url") or row.get("title")

    def strengths(rows):
        """Each paper's standing in each dimension, as a share of the field."""
        cited = _ranked(rows, lambda r: r.get("cited_by") or 0)
        # Nearest the centre scores highest, and an unplaceable paper scores
        # nothing rather than infinitely well.
        central = _ranked(rows, lambda r: -(r["distance"]
                                            if r.get("distance") is not None
                                            else float("inf")))
        recency = {}
        for r in rows:
            year = r.get("year") or 0
            recency[ident(r)] = (
                min(1.0, (year - oldest_recent + 1) / (FRONTIER_YEARS + 1))
                if year >= oldest_recent else 0.0)
        return cited, central, recency

    cited, central, recency = strengths(remaining)
    for row in remaining:
        key = ident(row)
        if key in bridging_ids:
            out["bridging"].append(row)
            continue
        scores = {"most-cited": cited[key], "central": central[key],
                  "frontier": recency[key]}
        out[max(scores, key=lambda k: scores[k])].append(row)

    out["most-cited"].sort(key=lambda r: -(r.get("cited_by") or 0))
    out["central"].sort(key=lambda r: r.get("distance")
                        if r.get("distance") is not None else float("inf"))
    out["bridging"].sort(key=lambda r: -(r.get("cited_by") or 0))
    out["frontier"].sort(key=lambda r: (-(r.get("year") or 0),
                                        -(r.get("cited_by") or 0)))
    return {name: rows[:PER_ROLE] for name, rows in out.items()}


def _ranked(rows: list, value) -> dict:
    """Each row's position in a ranking, from 0 (worst) to 1 (best)."""
    ordered = sorted(rows, key=value)
    last = max(len(ordered) - 1, 1)
    return {(r.get("url") or r.get("title")): n / last
            for n, r in enumerate(ordered)}


def render(region: dict, roles: dict, candidate: Optional[dict] = None,
           now: Optional[datetime] = None) -> str:
    """The fill list, as text."""
    now = now or datetime.now(timezone.utc)
    counts = (candidate or {}).get("counts") or {}
    out: list = []

    def head(title):
        out.append("")
        out.append(title.upper())
        out.append("-" * len(title))

    out.append("raDash corpus fill list")
    out.append("=" * WIDTH)
    out.append(f"topic      {region['name']}")
    if region.get("terms"):
        out.append(f"terms      {', '.join(region['terms'])}")
    out.append(f"region     cluster {region['cluster']} · "
               f"lineage {region.get('lineage', '—')}")
    out.append(f"written    {now.strftime('%Y-%m-%d %H:%M UTC')} by raDash")
    if candidate:
        out.append(f"signal     {candidate['signal']} · "
                   f"{candidate['signal_name']}")
        out.append(f"the move   {candidate['move']}")

    head("why this is a fill list and not a brief")
    out.extend(_bullet((candidate or {}).get("route", {}).get(
        "why",
        "A region thin in your library and active in the literature is a gap "
        "in your collecting. The honest response to a reading gap is reading, "
        "and offering to draft a paper there would be flattery.")))

    head("what you hold here")
    if counts:
        out.append(f"  {counts.get('collected', 0)} items collected, "
                   f"{counts.get('read', 0)} with evidence of being read, "
                   f"{counts.get('written', 0)} of your own works")
        if counts.get("newest_year"):
            out.append(f"  newest publication year in the region: "
                       f"{counts['newest_year']}")
    else:
        out.append("  — not computed for this region")

    total = 0
    for name, why in ROLES:
        rows = roles.get(name) or []
        total += len(rows)
        head(f"{name} ({len(rows)})")
        out.append(f"  {why}")
        out.append("")
        if not rows:
            out.append("  none in the gathered set")
            continue
        for r in rows:
            out.append(f"  {_doi(r.get('url')) or r.get('url') or '(no doi)'}")
            for line in _wrap(r.get("title") or "", WIDTH - 6):
                out.append(f"      {line}")
            bits = []
            if r.get("year"):
                bits.append(str(r["year"]))
            if r.get("cited_by") is not None:
                bits.append(f"cited {r['cited_by']}")
            if r.get("venue"):
                bits.append(r["venue"])
            if r.get("distance") is not None:
                bits.append(f"distance {r['distance']:.3f}")
            if bits:
                out.append(f"      {' · '.join(bits)}")

    head("totals")
    out.append(f"  {total} papers across {len(ROLES)} roles, none of which "
               "are in your library")

    head("two things to know")
    out.extend(_bullet(
        "This overlaps rabbitHole's gather at a different altitude. gather "
        "works a project's declared topic; this works the shape of your whole "
        "corpus. If a project lit review is what you actually need, that is "
        "the better tool."))
    out.append("")
    out.extend(_bullet(
        "Filling a region changes the map. New items move the fit and can "
        "reshuffle cluster boundaries, so the loop is fill, re-embed, re-read "
        "— never trust a pre-fill ranking afterwards."))

    out.append("")
    out.append("-" * WIDTH)
    out.append("raDash adds nothing to Zotero. The write stays yours.")
    return "\n".join(out) + "\n"


def _doi(url: Optional[str]) -> Optional[str]:
    if url and "doi.org/" in url:
        return url.split("doi.org/", 1)[1]
    return None
