"""The join key: Zotero collection ↔ trundlr project ↔ project folder.

Three systems name the same work three ways. trundlr calls it `lathe_dev`,
Zotero calls it `lathe`, and the folder is `260601_lathe`. Nothing enforces
agreement between them, so the join is a *proposal* with a stated basis, never
an assumed identity.

Three bases, strongest first:

1. **Declared** — `haarpi.yaml` carries `trundlr_project_id`, which is an
   explicit statement by the project itself. Where it exists it wins outright
   and no name is compared.
2. **Exact** — names identical once case and punctuation are normalised.
3. **Stem** — identical after a role suffix (`_dev`, `_run`, `_old`) is
   removed. `lathe_dev` and `lathe` are the same research project tracked at
   two levels of abstraction; `DigiPros_old` is a superseded reading list for a
   live project.

What does *not* match is reported rather than dropped. An unmatched Zotero
collection means reading with no project behind it; an unmatched trundlr
project means tracked work with no reading behind it. Both are findings — the
second is most of what the "active work" panel exists to surface — so the
matcher returns them beside the matches instead of quietly returning only what
lined up.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# Suffixes that mark a role, not a different body of work. Order matters only
# in that the longest is tried first.
ROLE_SUFFIXES = ("_dev", "_run", "_old", "-dev", "-run", "-old")

_PUNCT = re.compile(r"[^a-z0-9]+")


def normalize(name: str) -> str:
    """Casefold and strip punctuation. `Schelling-Chords` → `schellingchords`."""
    return _PUNCT.sub("", (name or "").casefold())


def stem(name: str) -> str:
    """Normalised name with any role suffix removed."""
    low = (name or "").casefold()
    for suf in ROLE_SUFFIXES:
        if low.endswith(suf) and len(low) > len(suf):
            low = low[: -len(suf)]
            break
    return normalize(low)


@dataclass
class Match:
    """One proposed identity across the three systems."""
    slug: str                                  # the stem, used as raDash's own id
    basis: str                                 # "declared" | "exact" | "stem"
    zotero_collection: Optional[str] = None    # collection name
    zotero_collection_key: Optional[str] = None
    trundlr_project_id: Optional[int] = None
    trundlr_name: Optional[str] = None
    also_trundlr_ids: list[int] = field(default_factory=list)  # absorbed by suffix
    trundlr_priority: Optional[int] = None
    folder: Optional[str] = None               # project folder path
    haarpi_name: Optional[str] = None
    item_count: Optional[int] = None           # size of the Zotero collection
    notes: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Present in all three systems — the only case with no caveat."""
        return bool(self.zotero_collection and self.trundlr_project_id and self.folder)


@dataclass
class MatchReport:
    matches: list[Match] = field(default_factory=list)
    unmatched_zotero: list[str] = field(default_factory=list)
    unmatched_trundlr: list[str] = field(default_factory=list)
    unmatched_folders: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "matched": len(self.matches),
            "complete": sum(1 for m in self.matches if m.complete),
            "by_basis": {
                b: sum(1 for m in self.matches if m.basis == b)
                for b in ("declared", "exact", "stem")
            },
            "unmatched_zotero": self.unmatched_zotero,
            "unmatched_trundlr": self.unmatched_trundlr,
            "unmatched_folders": self.unmatched_folders,
        }


def match(
    collections: list,      # ZoteroCollection
    projects: list,         # TrundlrProject
    haarpi_projects: list,  # HaarpiProject
) -> MatchReport:
    """Propose the three-way join. Pure: no I/O, so it is trivially testable."""
    report = MatchReport()

    by_slug: dict[str, Match] = {}

    def slot(slug: str, basis: str) -> Match:
        m = by_slug.get(slug)
        if m is None:
            m = Match(slug=slug, basis=basis)
            by_slug[slug] = m
        elif _rank(basis) < _rank(m.basis):
            m.basis = basis
        return m

    trundlr_by_id = {p.id: p for p in projects}
    claimed_projects: set[int] = set()

    # 1. Declared — haarpi.yaml states its own trundlr id.
    for hp in haarpi_projects:
        slug = stem(hp.name or hp.folder_name)
        if not slug:
            continue
        basis = "declared" if hp.trundlr_project_id in trundlr_by_id else "stem"
        m = slot(slug, basis)
        m.haarpi_name = hp.name
        m.folder = hp.folder
        proj = trundlr_by_id.get(hp.trundlr_project_id)
        if proj is not None:
            m.trundlr_project_id = proj.id
            m.trundlr_name = proj.name
            m.trundlr_priority = proj.priority
            claimed_projects.add(proj.id)
        elif hp.trundlr_project_id is not None:
            m.notes.append(
                f"haarpi.yaml declares trundlr_project_id={hp.trundlr_project_id}, "
                "which trundlr does not have"
            )

    # 2/3. Names, for whatever the declarations did not already settle.
    for proj in projects:
        if proj.id in claimed_projects:
            continue
        slug = stem(proj.name)
        if not slug:
            continue
        m = slot(slug, "exact" if normalize(proj.name) == slug else "stem")
        if m.trundlr_project_id is None:
            m.trundlr_project_id = proj.id
            m.trundlr_name = proj.name
            m.trundlr_priority = proj.priority
            m.folder = m.folder or (proj.folder or None)
        else:
            # `lathe` and `lathe_dev` are one body of work tracked twice. The
            # second is absorbed, and recorded so it is not then reported as a
            # project nobody reads for.
            m.also_trundlr_ids.append(proj.id)
            m.notes.append(f"trundlr also tracks this as {proj.name!r} (#{proj.id})")
        claimed_projects.add(proj.id)

    for col in collections:
        slug = stem(col.name)
        if not slug:
            continue
        m = by_slug.get(slug)
        if m is None:
            report.unmatched_zotero.append(col.name)
            continue
        if m.zotero_collection is None:
            m.zotero_collection = col.name
            m.zotero_collection_key = col.key
            m.item_count = col.size
        else:
            # Two collections stem to one project — e.g. digipros + DigiPros_old.
            m.notes.append(f"a second collection also matches: {col.name}")
            m.item_count = (m.item_count or 0) + col.size

    report.matches = sorted(by_slug.values(), key=lambda m: m.slug)
    # A trundlr project is "unmatched" when no Zotero collection sits behind
    # it: work that is tracked but not read for. Stalled reading surfaces here.
    report.unmatched_trundlr = sorted(
        m.trundlr_name for m in report.matches
        if m.trundlr_project_id and not m.zotero_collection and m.trundlr_name
    )

    # A folder is always represented in `matches` — it created its own slot —
    # so "unmatched" here means the useful thing: a folder that reached neither
    # trundlr nor Zotero, and is therefore invisible to every other system.
    orphan_slugs = {
        m.slug for m in report.matches
        if m.haarpi_name and not m.trundlr_project_id and not m.zotero_collection
    }
    report.unmatched_folders = sorted(
        hp.folder_name for hp in haarpi_projects if stem(hp.name or hp.folder_name) in orphan_slugs
    )
    report.unmatched_zotero.sort()
    return report


def _rank(basis: str) -> int:
    return {"declared": 0, "exact": 1, "stem": 2}.get(basis, 3)
