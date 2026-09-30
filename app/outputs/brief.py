"""The read-in brief — M7-T1.

One file, emitted on request for one candidate, shaped so its fields drop
straight into whatever you read with. `DESIGN.md` §8 lists what it has to
carry: topic, focus lines, date range, target size, seed DOIs drawn from the
gap analysis — **both** the corpus items and the frontier papers that
generated the score — the nearest existing Zotero collections, and the command
to run.

Two things it carries that the design does not demand, and should.

**The case against, in the file.** The brief outlives the page that produced
it. A month from now it is a list of papers with no memory of the fact that
the grounding was collecting rather than reading, and re-reading the argument
for something is not the same as re-reading the argument about it.

**The command is a template, not an invocation.** raDash does not know your
reading tool's flags and will not invent them. It lays the fields out in the
order a `gather` wants them and leaves the verb to you — which is also the
one copy-paste that stops raDash from being able to set work in motion off a
score nobody has checked.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

WIDTH = 78

# How much of each list reaches the file. A brief nobody finishes reading is a
# brief that did not hand anything off.
SEEDS = 8
COLLECTIONS = 4


def render(candidate: dict, collections: Optional[list] = None,
           now: Optional[datetime] = None) -> str:
    """The brief, as text."""
    now = now or datetime.now(timezone.utc)
    counts = candidate.get("counts") or {}
    out: list = []

    def rule(char="="):
        out.append(char * WIDTH)

    def head(title):
        out.append("")
        out.append(title.upper())
        out.append("-" * len(title))

    out.append("raDash read-in brief")
    rule()
    out.append(f"topic      {candidate['region']}")
    if candidate.get("terms"):
        out.append(f"terms      {', '.join(candidate['terms'])}")
    out.append(f"region     cluster {candidate['cluster']} · "
               f"lineage {candidate.get('lineage', '—')}")
    out.append(f"signal     {candidate['signal']} · {candidate['signal_name']}")
    out.append(f"the move   {candidate['move']}")
    out.append(f"score      {candidate['score']}")
    out.append(f"written    {now.strftime('%Y-%m-%d %H:%M UTC')} by raDash")
    if candidate.get("exemplar"):
        out.append(f"anchor     {candidate['exemplar']}")

    confirmation = candidate.get("confirmation")
    if confirmation:
        out.append(f"confirmed  {'yes' if confirmation['confirmed'] else 'no'}"
                   f" — {confirmation['basis']}")

    head("focus")
    for line in _wrap(_focus(candidate)):
        out.append(f"  {line}")

    head("why this area")
    for i in candidate.get("inputs", []):
        mark = "x" if i["effect"] == "scales" else "+"
        lead = f"  {mark} {i['input']:<24} {i['contribution']:>6}  "
        # The details run long — a distance term names the work it measured
        # against — so they wrap under their own column rather than off the
        # right-hand edge of a file someone has to read.
        for n, line in enumerate(_wrap(i["detail"], WIDTH - len(lead))):
            out.append((lead if n == 0 else " " * len(lead)) + line)

    head("the case against")
    for line in candidate.get("counter_case", []):
        out.extend(_bullet(line))

    head("dates")
    span = _span(counts)
    out.append(f"  your holdings   {span}")
    age = candidate.get("frontier_age_days")
    out.append("  the frontier    "
               + (f"the last 18 months, gathered {age:.0f} days ago"
                  if age is not None
                  else "nothing gathered for this region"))

    head("target size")
    held = len(candidate.get("corpus") or [])
    new = len(candidate.get("frontier") or [])
    out.append(f"  {held + new} seeds below: {held} already in your library, "
               f"{new} not yet")
    out.append(f"  the region holds {counts.get('collected', 0)} items, of "
               f"which {counts.get('read', 0)} carry evidence of being read")

    head("seeds — already in your library")
    out.extend(_seeds(candidate.get("corpus") or [], held=True))

    head("seeds — from the frontier")
    out.extend(_seeds(candidate.get("frontier") or [], held=False))

    head("nearest zotero collections")
    if collections:
        for c in collections[:COLLECTIONS]:
            out.append(f"  {c['name']:<34} {c['matched']} of "
                       f"{counts.get('collected', 0)} members")
    else:
        out.append("  none — no collection holds a usable share of this region")

    head("to run")
    out.extend(_run(candidate))

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
    rule("-")
    out.append("raDash writes to nothing it observes. This file is the whole "
               "hand-off.")
    return "\n".join(out) + "\n"


def _focus(candidate: dict) -> str:
    """One paragraph saying what the area is and why it surfaced."""
    counts = candidate.get("counts") or {}
    bits = [f"{candidate['region']} is a region of "
            f"{counts.get('collected', 0)} items you have collected, "
            f"{counts.get('read', 0)} of them with evidence of being read, "
            f"holding {counts.get('written', 0)} of your own works."]
    if counts.get("live_because"):
        bits.append(f"It is live: {counts['live_because']}.")
    else:
        bits.append("Nothing current sits behind it — no project brief and no "
                    "recent publication of yours.")
    if candidate.get("bridge"):
        bits.append(candidate["bridge"]["detail"].capitalize() + ".")
    bits.append(candidate["route"]["why"])
    return " ".join(bits)


def _span(counts: dict) -> str:
    newest, median = counts.get("newest_year"), counts.get("median_year")
    if not newest:
        return "no publication years on record"
    if median and median != newest:
        return f"to {newest}, median {median}"
    return f"to {newest}"


def _seeds(rows: list, held: bool) -> list:
    if not rows:
        return ["  none"]
    out = []
    for r in rows[:SEEDS]:
        title = (r.get("label") or r.get("title") or "").strip()
        year = r.get("year") or "—"
        cited = r.get("cited_by")
        doi = _doi(r.get("url"))
        stamp = f"  {doi or '(no doi)':<34} {year}"
        if cited is not None:
            stamp += f"  cited {cited}"
        out.append(stamp)
        for line in _wrap(title, WIDTH - 6):
            out.append(f"      {line}")
        if held and r.get("reading") and r["reading"] != "collected":
            out.append(f"      [{r['reading']}]")
    return out


def _run(candidate: dict) -> list:
    """The fields a gather wants, and no invented verb to put in front."""
    dois = [d for d in (_doi(r.get("url"))
                        for r in (candidate.get("corpus") or [])
                        + (candidate.get("frontier") or [])) if d]
    out = [
        f"  topic:  {candidate['region']}",
        f"  terms:  {', '.join(candidate.get('terms') or [])}",
        # Spelt out, because "since 2026" reads as either a start date or a
        # cutoff and the two ask for opposite things.
        f"  since:  {(candidate.get('counts') or {}).get('newest_year') or '—'}"
        "   (the newest thing you already hold here)",
        f"  seeds:  {len(dois)} DOIs, one per line below",
        "",
    ]
    out.extend(f"    {d}" for d in dois)
    out.append("")
    for n, line in enumerate(_wrap(
            "raDash does not know your reading tool's flags and will not "
            "invent them. The fields above are laid out in the order a gather "
            "wants them; the verb is yours. That copy-paste is the price of "
            "raDash never setting work in motion off a score nobody checked.",
            WIDTH - 4)):
        out.append(("  # " if n == 0 else "    ") + line)
    return out


def _bullet(text: str) -> list:
    """A hanging-indent bullet, so a long reason stays one reason."""
    return [("  - " if n == 0 else "    ") + line
            for n, line in enumerate(_wrap(text, WIDTH - 4))]


def _wrap(text: str, width: int = WIDTH - 2) -> list:
    words, lines, line = (text or "").split(), [], ""
    for w in words:
        if line and len(line) + 1 + len(w) > width:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        lines.append(line)
    return lines or [""]


def _doi(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    if "doi.org/" in url:
        return url.split("doi.org/", 1)[1]
    return None
