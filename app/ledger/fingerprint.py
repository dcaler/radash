"""Stable identity for a work, independent of which source described it.

A fingerprint answers "is this the same thing I saw last week", not "is this
the same thing as that other row" — the second question is `propose.py`'s, and
it is much harder. Here the goal is only that a refresh recognises what it
already had, so a ruling made in March still attaches to the right work in
September.

DOI first, because a DOI is the closest thing to an identifier anyone agrees
on. Where there is none, a normalised title plus year, which is weaker and
deliberately so: two different works with identical titles in the same year
collide, and collision is safer than churn. A fingerprint that changed whenever
a source tidied its punctuation would silently orphan every ruling.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

# Punctuation, spacing and case vary between sources describing one work.
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Subtitles after a colon are frequently dropped by one index and kept by
# another. The head is what stays stable.
_SUBTITLE = re.compile(r"\s*[:–—]\s*.*$")

STOPWORDS = frozenset({
    "a", "an", "the", "of", "and", "or", "in", "on", "for", "to", "with",
    "from", "by", "at", "as", "is", "are",
})


def normalize_doi(doi: str | None) -> str:
    """Lowercase, strip resolver prefixes and surrounding whitespace."""
    if not doi:
        return ""
    d = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    return d.strip()


def doi_family(doi: str | None) -> str:
    """The DOI itself. A version series is **not derivable from the string.**

    Kept as a named function because the instinct to collapse a Zenodo DOI is
    strong and wrong, and this is where to read why. Zenodo mints a concept DOI
    per record plus one per version, and those numbers are allocated from a
    global sequence — `zenodo.17148833` and `zenodo.17148834` are as likely to
    be two unrelated people's software as two releases of yours.

    An earlier version of this function collapsed the numeric tail to a
    wildcard, which made every Zenodo DOI on Earth one family and proposed
    merging three unrelated repositories of the author's own. A version series
    is detected in `propose.py` from title, venue and year, which is evidence
    rather than a coincidence of numbering.
    """
    return normalize_doi(doi)


def normalize_title(title: str | None) -> str:
    """Casefold, strip accents and punctuation, drop a trailing subtitle."""
    if not title:
        return ""
    t = unicodedata.normalize("NFKD", title)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = _SUBTITLE.sub("", t.casefold())
    return _NON_ALNUM.sub(" ", t).strip()


def title_tokens(title: str | None) -> frozenset[str]:
    """Content words of a title, for overlap comparison."""
    return frozenset(
        w for w in normalize_title(title).split() if w and w not in STOPWORDS
    )


def fingerprint(doi: str | None = None, title: str | None = None,
                year: int | None = None) -> str:
    """A stable, opaque id for one work.

    Uses the DOI **exactly**, not its family. Collapsing a Zenodo version
    series here would silently merge three software releases into one work
    without anyone ruling on it — and those are precisely the cases the gate
    exists to put in front of a human. The family is a proposal *signal*
    (`propose.py`), never an identity.

    Prefixed so the basis is visible in the database: `doi:` fingerprints are
    strong, `ttl:` ones are a best effort and should be treated as such when
    they turn up in a proposal.
    """
    d = normalize_doi(doi)
    if d:
        return "doi:" + d
    t = normalize_title(title)
    if not t:
        return "ttl:" + hashlib.sha1(b"").hexdigest()[:16]
    basis = f"{t}|{year or ''}"
    return "ttl:" + hashlib.sha1(basis.encode()).hexdigest()[:16]


def is_strong(fp: str) -> bool:
    """True when the fingerprint rests on a DOI rather than on a title."""
    return fp.startswith("doi:")
