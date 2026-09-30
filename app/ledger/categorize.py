"""What kind of output is this?

OpenAlex gives every work a `type`, and the mapping from that to something a
portfolio cares about is mostly obvious and occasionally not. The two that
matter:

**`conference-paper` is ambiguous and is not resolved here.** An IEEE
proceedings paper is peer-reviewed and cited; an APPAM abstract is a talk. Both
arrive with the same type, and no field in the data separates them. So they get
their own category rather than a guess — counted apart from journal articles,
not demoted to nothing, and one click from being reassigned.

**A deposit is not a publication.** Zenodo software releases are real outputs
and belong in the record, but averaging them into a publication count makes the
count mean less.

Inference is the default; your override always wins and outlives every refresh.
"""
from __future__ import annotations

from typing import Optional

from app.models import WorkCategory

# OpenAlex `type` → category. Anything unlisted falls through to `other`,
# which is visible in the UI rather than silently dropped.
BY_TYPE = {
    "article": WorkCategory.publication,
    "book": WorkCategory.publication,
    "book-chapter": WorkCategory.publication,
    "review": WorkCategory.publication,
    "editorial": WorkCategory.publication,
    "letter": WorkCategory.publication,
    "conference-paper": WorkCategory.conference,
    "proceedings-article": WorkCategory.conference,
    "preprint": WorkCategory.preprint,
    "posted-content": WorkCategory.preprint,
    "software": WorkCategory.software,
    "dataset": WorkCategory.software,
    "report": WorkCategory.report,
    "dissertation": WorkCategory.other,
}

# Venue words that override the type. A deposit host is a deposit host whatever
# OpenAlex decided to call the thing on it.
VENUE_HINTS = (
    (("zenodo", "figshare", "dryad", "software heritage"), WorkCategory.software),
    (("ssrn", "arxiv", "biorxiv", "medrxiv", "osf", "research square"),
     WorkCategory.preprint),
)


def infer(work_type: Optional[str], venue: Optional[str] = None) -> WorkCategory:
    """Best guess from what the sources said. Never the last word."""
    v = (venue or "").casefold()
    for needles, category in VENUE_HINTS:
        if any(n in v for n in needles):
            return category
    return BY_TYPE.get((work_type or "").casefold(), WorkCategory.other)


# Categories that belong in "publications by venue and year" (DESIGN.md §6).
PORTFOLIO = frozenset({WorkCategory.publication})
