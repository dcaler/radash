"""The dedup proposal engine — M2-T4.

Given every candidate in a snapshot, propose which of them are the same work.
Each proposal carries the signals that fired and a confidence, and none of them
takes effect until a human rules.

Four signals, and they are not equal:

- **DOI** — decisive, and only when the DOIs are identical. A shared registrar
  or an adjacent number means nothing: Zenodo allocates from a global sequence,
  so neighbouring DOIs are usually strangers.
- **Title** — strong but treacherous. A preprint and its published version
  usually share a title exactly, which is why title agreement alone proposes a
  merge *and* flags the preprint case rather than concluding it.
- **Year** — corroborating only. Preprint and publication typically differ by
  one, so a year gap is evidence about *which* case this is, never about
  whether the works are related.
- **Authorship overlap** — corroborating. Cheap to compute, and it is what
  separates a common title in a large field from an actual duplicate.

The engine is deliberately biased toward proposing. A proposal you reject costs
you a few seconds at the gate; a duplicate it never raised becomes a permanent
error in every count that follows.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Iterable, Optional

from app import settings
from app.ledger import links as links_mod
from app.ledger.fingerprint import normalize_doi, normalize_title, title_tokens

# Above this, two titles are "the same title" for proposal purposes.
TITLE_RATIO = 0.92
# Above this they are close enough to look at, with other signals deciding.
TITLE_RATIO_WEAK = 0.80

# Venue words that mark a preprint server rather than a publication.
PREPRINT_VENUES = frozenset({
    "arxiv", "ssrn", "osf", "biorxiv", "medrxiv", "socarxiv", "preprints",
    "research square", "researchsquare", "zenodo", "edarxiv",
})

# Work types that OpenAlex uses for things that are not papers.
SOFTWARE_TYPES = frozenset({"software", "dataset", "other"})

# Deposit hosts, which mint a fresh DOI per release. A repeated title here is
# a version series; the DOIs themselves say nothing about the relationship.
REPOSITORY_VENUES = frozenset({"zenodo", "figshare", "osf", "dryad",
                               "software heritage"})


@dataclass
class Signal:
    name: str
    fired: bool
    detail: str
    weight: float

    def as_dict(self) -> dict:
        return {"name": self.name, "fired": self.fired,
                "detail": self.detail, "weight": self.weight}


@dataclass
class Proposal:
    """A proposed identity between two candidates, with its reasoning."""
    left_fingerprint: str
    right_fingerprint: str
    kind: str                     # "duplicate" | "preprint" | "version" | "adopt"
    confidence: float
    signals: list[Signal] = field(default_factory=list)
    counter_case: Optional[str] = None   # why this might be wrong
    left_label: str = ""
    right_label: str = ""
    left_type: Optional[str] = None      # conference-paper, software, article…
    right_type: Optional[str] = None
    left_links: list = field(default_factory=list)
    right_links: list = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        return tuple(sorted((self.left_fingerprint, self.right_fingerprint)))

    def as_dict(self) -> dict:
        return {
            "left": self.left_fingerprint,
            "right": self.right_fingerprint,
            "left_label": self.left_label,
            "right_label": self.right_label,
            "left_type": self.left_type,
            "right_type": self.right_type,
            "left_links": self.left_links,
            "right_links": self.right_links,
            "kind": self.kind,
            "confidence": round(self.confidence, 3),
            "signals": [s.as_dict() for s in self.signals if s.fired],
            "counter_case": self.counter_case,
        }


def _label(c) -> str:
    year = f" ({c.year})" if c.year else ""
    venue = f" — {c.venue}" if c.venue else ""
    return f"{(c.title or '')[:70]}{year}{venue} [{c.source.value}]"


def _authors(c) -> frozenset[str]:
    """Surnames, casefolded, from whatever the source stated."""
    raw = c.authors
    if not raw:
        return frozenset()
    try:
        names = json.loads(raw)
    except (ValueError, TypeError):
        return frozenset()
    out = set()
    for n in names if isinstance(names, list) else []:
        parts = str(n).replace(",", " ").split()
        if parts:
            out.add(parts[-1].casefold())
    return frozenset(out)


def _is_preprint(c) -> bool:
    venue = (c.venue or "").casefold()
    return any(p in venue for p in PREPRINT_VENUES)


def _is_repository(c) -> bool:
    """A deposit rather than a publication: Zenodo, OSF, and their kin."""
    venue = (c.venue or "").casefold()
    return any(r in venue for r in REPOSITORY_VENUES)


def _title_ratio(a, b) -> float:
    ta, tb = normalize_title(a.title), normalize_title(b.title)
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    return SequenceMatcher(None, ta, tb).ratio()


def compare(left, right) -> Optional[Proposal]:
    """Compare two candidates. Returns a proposal, or None if unrelated."""
    signals: list[Signal] = []

    ldoi, rdoi = normalize_doi(left.doi), normalize_doi(right.doi)
    same_doi = bool(ldoi and rdoi and ldoi == rdoi)
    signals.append(Signal("doi", same_doi, f"{ldoi or '—'} / {rdoi or '—'}", 1.0))

    ratio = _title_ratio(left, right)
    # Read at call time so the setting takes effect without a restart. The
    # weak threshold tracks the strong one at a fixed distance rather than
    # being tunable separately: two knobs that must stay ordered are two ways
    # to get an incoherent engine.
    strong = settings.num("TITLE_RATIO", TITLE_RATIO)
    weak = max(0.5, strong - (TITLE_RATIO - TITLE_RATIO_WEAK))

    # A repository release series: same title, both deposited, different DOIs.
    # Detected from evidence rather than from DOI arithmetic.
    both_repo = _is_repository(left) and _is_repository(right)
    same_series = bool(both_repo and ratio >= strong and not same_doi)
    signals.append(Signal("release_series", same_series,
                          "same title, both deposited, different DOIs", 0.9))
    signals.append(Signal("title", ratio >= strong,
                          f"similarity {ratio:.2f}", 0.6))

    ly, ry = left.year, right.year
    year_gap = abs(ly - ry) if (ly and ry) else None
    signals.append(Signal("year", year_gap == 0,
                          f"{ly or '—'} vs {ry or '—'}", 0.2))

    la, ra = _authors(left), _authors(right)
    overlap = len(la & ra) / max(1, min(len(la), len(ra))) if (la and ra) else 0.0
    signals.append(Signal("authors", overlap >= 0.5,
                          f"{len(la & ra)} shared surname(s)", 0.3))

    # Decide what kind of relationship this is, if any.
    kind, confidence, counter = None, 0.0, None

    if same_doi:
        kind, confidence = "duplicate", 1.0
        counter = ("A shared DOI is near-conclusive; reject only if one row is "
                   "an index error attaching your DOI to someone else's work.")
    elif same_series:
        kind, confidence = "version", 0.9
        counter = ("Two deposits with the same title — almost always releases "
                   "of one artefact, counted once per version. Reject if you "
                   "intend each release to stand as a separate output.")
    elif ratio >= strong and overlap < 0.5 and not (la and ra):
        # Neither side stated authors — the folder and CV sources never do. The
        # absence is not corroboration, so a title match alone proposes only
        # when the years agree, and says plainly that it is thin.
        if year_gap == 0:
            kind, confidence = "duplicate", 0.6
            counter = ("Matched on title and year, with no author list on either "
                       "side to corroborate. A field with formulaic titles "
                       "produces this often; check before confirming.")
    elif ratio >= strong and overlap >= 0.5:
        lp, rp = _is_preprint(left), _is_preprint(right)
        if lp != rp:
            kind, confidence = "preprint", 0.85
            counter = ("Same title, one on a preprint server and one not — "
                       "usually one work. Reject if the preprint was "
                       "substantially rewritten and both are cited separately.")
        else:
            kind = "duplicate"
            confidence = 0.75 + (0.1 if year_gap == 0 else 0.0)
            counter = ("Matched on title and authors without a shared DOI. "
                       "Reject if these are separate papers in a series, which "
                       "a field with formulaic titles produces often.")
    elif ratio >= weak and overlap >= 0.5 and year_gap in (0, 1):
        kind, confidence = "duplicate", 0.55
        counter = ("A weak match: titles differ somewhat. Likely a subtitle "
                   "dropped by one index, possibly two real papers.")

    if kind is None:
        return None

    return Proposal(
        left_fingerprint=left.fingerprint, right_fingerprint=right.fingerprint,
        kind=kind, confidence=confidence, signals=signals, counter_case=counter,
        left_label=_label(left), right_label=_label(right),
        left_type=left.work_type, right_type=right.work_type,
        left_links=links_mod.candidate_links(left),
        right_links=links_mod.candidate_links(right),
    )


def propose(candidates: Iterable) -> list[Proposal]:
    """Every proposal across a snapshot's candidates, strongest first.

    Blocked on a cheap key before the quadratic comparison, so the engine stays
    linear-ish in practice: only candidates sharing a DOI family or a title
    token are ever compared. At ~30 candidates this is theatre; at the scale
    the frontier feed reaches in M5 it is not.
    """
    cands = [c for c in candidates]
    blocks: dict[str, list] = {}
    for c in cands:
        keys = set()
        doi = normalize_doi(c.doi)
        if doi:
            keys.add(f"d:{doi}")
        toks = title_tokens(c.title)
        # The two rarest-looking tokens are enough to co-block near-duplicates
        # without putting every paper about "networks" in one bucket.
        for t in sorted(toks, key=len, reverse=True)[:2]:
            keys.add(f"t:{t}")
        for k in keys:
            blocks.setdefault(k, []).append(c)

    seen: set[tuple[str, str]] = set()
    out: list[Proposal] = []
    for group in blocks.values():
        for i, left in enumerate(group):
            for right in group[i + 1:]:
                if left.fingerprint == right.fingerprint:
                    continue
                pair = tuple(sorted((left.fingerprint, right.fingerprint)))
                if pair in seen:
                    continue
                seen.add(pair)
                p = compare(left, right)
                if p is not None:
                    out.append(p)

    out.sort(key=lambda p: (-p.confidence, p.left_label))
    return out
