"""SQLModel tables.

M0 defines only what the skeleton itself needs: a record of when each source
was last read, and a key/value store for app settings. The tables the later
milestones need — the works ledger (M2), the embedding cache and fitted space
(M3), frontier results (M5) — are deliberately absent. Each milestone brings
its own schema so the skeleton stays honest about what exists.
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SourceStatus(str, Enum):
    ok = "ok"              # read cleanly
    stale = "stale"        # served from cache; refresh failed or was skipped
    degraded = "degraded"  # read, but partially — see `note`
    missing = "missing"    # not mounted / not reachable
    error = "error"        # read failed outright


class SourceState(SQLModel, table=True):
    """Last-read state for one external source.

    This is what makes the dashboard's coverage-honesty panel possible: every
    panel can say how old its inputs are and whether they were complete. A
    source that has never been read has no row, which reads as "unknown" rather
    than as "fine".
    """
    key: str = Field(primary_key=True)       # "zotero" | "trundlr" | "openalex" | ...
    status: SourceStatus = Field(default=SourceStatus.missing)
    last_read_at: Optional[datetime] = Field(default=None)
    last_ok_at: Optional[datetime] = Field(default=None)
    item_count: Optional[int] = Field(default=None)
    note: Optional[str] = Field(default=None)  # parse failures, partial reads, drift
    updated_at: datetime = Field(default_factory=_utcnow)


class Setting(SQLModel, table=True):
    """Key/value app settings that outlive a container restart."""
    key: str = Field(primary_key=True)
    value: Optional[str] = Field(default=None)
    updated_at: datetime = Field(default_factory=_utcnow)


class SourceCache(SQLModel, table=True):
    """Last good response from a network source, kept so raDash degrades
    instead of blanking.

    trundlr lives on the tailnet and Tailscale is occasionally down; OpenAlex
    and S2 are public APIs with their own weather. When one of them is
    unreachable the honest render is last week's numbers *labelled as last
    week's*, which needs the old payload to still exist — so every successful
    network read lands here, and the adapter falls back to it with status
    `stale` rather than reporting `error` and losing the panel.

    Only network sources are cached. The filesystem sources are already local;
    caching them would add an age without removing a failure mode.
    """
    key: str = Field(primary_key=True)        # "trundlr" | "openalex" | "s2"
    payload: str = Field(default="")          # JSON, as returned
    fetched_at: datetime = Field(default_factory=_utcnow)
    item_count: Optional[int] = Field(default=None)
    note: Optional[str] = Field(default=None)


# ---------------------------------------------------------------------------
# M2 · the works ledger
#
# The shape here follows one rule: **a refresh may replace every number, and
# must never replace a decision.** Machine-read facts are snapshot-scoped and
# disposable; the judgements you make at the gate are not scoped to anything
# and survive every later refresh. Those are different lifetimes, so they are
# different tables — collapsing them is how a ledger quietly loses a ruling
# you made in March.
# ---------------------------------------------------------------------------


class Snapshot(SQLModel, table=True):
    """One refresh of the observable world.

    Retained rather than overwritten, so a refresh that goes wrong is
    comparable against the one before it (`DESIGN.md` §6) and the status page
    can lead with what changed. `is_current` marks the one panels read from;
    exactly one row should carry it.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_utcnow, index=True)
    label: Optional[str] = Field(default=None)         # "weekly" | "manual" | ...
    is_current: bool = Field(default=False, index=True)
    source_status: Optional[str] = Field(default=None)  # JSON: per-source summary
    # Snapshot-scoped, because it is a statement about this read of the world:
    # what your public CV claimed, against what the indexes held, that week.
    drift: Optional[str] = Field(default=None)          # JSON: drift summary
    note: Optional[str] = Field(default=None)


class CandidateSource(str, Enum):
    openalex = "openalex"
    s2 = "s2"
    scholar = "scholar"
    cv = "cv"                  # your own pasted publication list
    site = "site"              # your public CV page -- a claim, not a record
    folder = "folder"          # 1_Publication/ directory names


class WorkCandidate(SQLModel, table=True):
    """One source's claim that a work exists, as that source stated it.

    Never edited and never merged in place. Two OpenAlex profiles listing the
    same paper produce two candidates, because that duplication is the finding
    M2 exists to surface — collapsing it here would hide what the gate is for.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    snapshot_id: int = Field(foreign_key="snapshot.id", index=True)
    source: CandidateSource = Field(index=True)
    source_id: Optional[str] = Field(default=None, index=True)  # OpenAlex id, S2 paperId
    source_profile: Optional[str] = Field(default=None)         # which author profile
    doi: Optional[str] = Field(default=None, index=True)        # normalised, lowercase
    title: str = Field(default="")
    year: Optional[int] = Field(default=None)
    venue: Optional[str] = Field(default=None)
    work_type: Optional[str] = Field(default=None)
    authors: Optional[str] = Field(default=None)     # JSON list, as stated
    cited_by: Optional[int] = Field(default=None)
    counts_by_year: Optional[str] = Field(default=None)  # JSON {year: n}
    fingerprint: str = Field(default="", index=True)     # stable identity, see ledger.py


class LedgerRuling(str, Enum):
    """What a human decided about a proposal."""
    pending = "pending"
    confirmed = "confirmed"    # yes, these candidates are one work
    split = "split"            # no, they are separate works
    excluded = "excluded"      # not my work / not a work at all


class WorkDecision(SQLModel, table=True):
    """A human ruling, deliberately **not** snapshot-scoped.

    Keyed by the fingerprint pair it rules on rather than by candidate row id,
    because candidate rows are rebuilt every refresh and ids do not survive.
    A ruling made once holds until you change it, which is the entire reason
    the gate is worth an hour of your time.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    fingerprint: str = Field(index=True)                 # the work being ruled on
    other_fingerprint: Optional[str] = Field(default=None, index=True)  # for merges
    ruling: LedgerRuling = Field(default=LedgerRuling.pending)
    decided_at: datetime = Field(default_factory=_utcnow)
    rationale: Optional[str] = Field(default=None)       # why, in your words


class Work(SQLModel, table=True):
    """A ledger entry: one work, as raDash currently believes it to be.

    Rebuilt each snapshot from candidates plus every standing decision, so it
    is derived state — the candidates and the rulings are the sources of truth.
    Citation numbers carry their provenance per number rather than as one
    blended figure, because the lanes disagree and that disagreement bounds how
    precisely any of this can be stated.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    snapshot_id: int = Field(foreign_key="snapshot.id", index=True)
    fingerprint: str = Field(index=True)
    title: str = Field(default="")
    year: Optional[int] = Field(default=None)
    venue: Optional[str] = Field(default=None)
    doi: Optional[str] = Field(default=None, index=True)
    work_type: Optional[str] = Field(default=None)
    venues_seen: Optional[str] = Field(default=None)     # JSON list, union
    citations_automated: Optional[int] = Field(default=None)   # max(OpenAlex, S2)
    citations_manual: Optional[int] = Field(default=None)      # Scholar
    citations_provenance: Optional[str] = Field(default=None)  # JSON {lane: {n, src}}
    counts_by_year: Optional[str] = Field(default=None)        # JSON {year: n}
    candidate_ids: Optional[str] = Field(default=None)   # JSON list of WorkCandidate.id
    on_cv: bool = Field(default=False)
    confirmed: bool = Field(default=False)   # a human has ruled on this entry
    category: str = Field(default="other", index=True)
    category_source: str = Field(default="inferred")   # inferred | your CV page | you
    on_site: bool = Field(default=False)   # claimed on your public CV page


class WorkCategory(str, Enum):
    """What kind of output a ledger entry is.

    Deliberately finer than "publication or not". The hard cases are real: an
    IEEE proceedings paper is peer-reviewed and a conference abstract is not,
    and both arrive from OpenAlex as `conference-paper`. Rather than guess
    which, `conference` is its own category — visible, counted separately, and
    yours to reassign. Nothing here is thrown away; the portfolio ledger simply
    stops mixing a talk in with a journal article.
    """
    publication = "publication"   # journal article, book, chapter
    conference = "conference"     # proceedings paper or presentation
    preprint = "preprint"
    software = "software"         # software, datasets, deposits
    report = "report"
    other = "other"


class WorkClassification(SQLModel, table=True):
    """Your override of a work's category.

    Same lifetime rule as `WorkDecision`: keyed by fingerprint, scoped to no
    snapshot, and untouched by any refresh. A category you set once is a fact
    about your record, not about this week's read of OpenAlex.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    fingerprint: str = Field(index=True, unique=True)
    category: WorkCategory = Field(default=WorkCategory.other)
    decided_at: datetime = Field(default_factory=_utcnow)
    rationale: Optional[str] = Field(default=None)


# ---------------------------------------------------------------------------
# M3 · the map
#
# A fitted space is a claim about where your work sits relative to itself, and
# it is only meaningful alongside what produced it. So every space carries a
# reproducibility stamp — backend, model, parameters, corpus hash, row count,
# timestamp — and a space whose stamp does not match the corpus in front of it
# is stale by definition rather than by guess.
# ---------------------------------------------------------------------------


class MapSpace(SQLModel, table=True):
    """One fit: a corpus, a backend, and the parameters that made it."""
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_utcnow, index=True)
    is_current: bool = Field(default=False, index=True)
    backend: str = Field(default="lsa")        # only "lsa" ships today
    model: Optional[str] = Field(default=None)  # vectoriser or embedding model
    params: Optional[str] = Field(default=None)  # JSON, as passed
    corpus_hash: str = Field(default="", index=True)
    row_count: int = Field(default=0)
    dimensions: int = Field(default=0)
    explained_variance: Optional[float] = Field(default=None)
    fit_seconds: Optional[float] = Field(default=None)
    vectors_path: Optional[str] = Field(default=None)   # .npz beside the db
    note: Optional[str] = Field(default=None)


class MapPointKind(str, Enum):
    corpus = "corpus"      # something you have read (a Zotero item)
    work = "work"          # something you have written (a ledger work)
    project = "project"    # something you are doing (a project brief)


class MapPoint(SQLModel, table=True):
    """One item's position in a fitted space.

    Only the display coordinates live here. The full vectors are far too large
    to be worth a row each and are written beside the database as a single
    array; this table is what the map, the tables and the drift view read.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    space_id: int = Field(foreign_key="mapspace.id", index=True)
    kind: MapPointKind = Field(index=True)
    ref: str = Field(index=True)          # Zotero key, work fingerprint, slug
    label: str = Field(default="")
    year: Optional[int] = Field(default=None)
    x: float = Field(default=0.0)
    y: float = Field(default=0.0)
    row: int = Field(default=0)           # index into the stored vectors
    cluster: Optional[int] = Field(default=None, index=True)   # -1 = noise
    # How far this item got: collected | opened | noted | annotated. Kept on
    # the point so the dashboard can separate a corpus from a reading history
    # without re-reading Zotero on every request.
    reading: str = Field(default="collected", index=True)
    cited_by: Optional[int] = Field(default=None)
    # Carried so a region can be resolved to its members' OpenAlex topics, and
    # so the frontier can exclude what you already hold, without re-reading
    # Zotero on every request.
    doi: Optional[str] = Field(default=None, index=True)


class MapCluster(SQLModel, table=True):
    """A region of the space, with the terms that distinguish it.

    `lineage` is what makes a region the *same* region across refits. Cluster
    numbers are an artefact of the algorithm and change freely between runs;
    without a stable identity a renamed region reads as a new one, and the
    drift panel would report movement that never happened.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    space_id: int = Field(foreign_key="mapspace.id", index=True)
    cluster: int = Field(index=True)
    lineage: Optional[str] = Field(default=None, index=True)
    label: Optional[str] = Field(default=None)    # your name for it, if given
    terms: Optional[str] = Field(default=None)    # JSON: distinguishing terms
    size: int = Field(default=0)
    exemplar_ref: Optional[str] = Field(default=None)
    exemplar_label: Optional[str] = Field(default=None)
    centroid_x: float = Field(default=0.0)
    centroid_y: float = Field(default=0.0)


class MapAxis(SQLModel, table=True):
    """One component's poles, read straight off the term loadings.

    Deliberately not named by anything clever. A component gets the terms that
    load most strongly at each end and nothing else; a confabulated label that
    reads plausibly is worse than a numbered axis, because it invites you to
    believe a structure the fit may not contain (`BUILD_PLAN.md`, risks).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    space_id: int = Field(foreign_key="mapspace.id", index=True)
    component: int = Field(index=True)
    positive_terms: Optional[str] = Field(default=None)   # JSON list
    negative_terms: Optional[str] = Field(default=None)   # JSON list
    explained_variance: Optional[float] = Field(default=None)
    name: Optional[str] = Field(default=None)       # yours, at the gate
    confirmed: bool = Field(default=False)


# ---------------------------------------------------------------------------
# M5 · the frontier
# ---------------------------------------------------------------------------


class FrontierItem(SQLModel, table=True):
    """A recent paper in one of your regions that you do not have.

    Scoped to a region's *lineage* rather than its cluster number, because
    cluster numbers are an artefact of each fit and a frontier set should
    survive a refit of the map it was gathered for.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    lineage: str = Field(index=True)
    cluster: Optional[int] = Field(default=None, index=True)
    openalex_id: str = Field(index=True)
    doi: Optional[str] = Field(default=None, index=True)
    title: str = Field(default="")
    year: Optional[int] = Field(default=None)
    venue: Optional[str] = Field(default=None)
    cited_by: Optional[int] = Field(default=None)
    topic_id: Optional[str] = Field(default=None)
    topic_name: Optional[str] = Field(default=None)
    abstract: Optional[str] = Field(default=None)
    fetched_at: datetime = Field(default_factory=_utcnow, index=True)
    # Where it lands in the fitted space, when the space could place it.
    x: Optional[float] = Field(default=None)
    y: Optional[float] = Field(default=None)
    distance_to_region: Optional[float] = Field(default=None)
    already_held: bool = Field(default=False)   # its DOI is in your library
    # Found by one of the region's topics, but it landed far enough from the
    # region that the topic is probably broader than the region is.
    off_target: bool = Field(default=False)


class RegionTopic(SQLModel, table=True):
    """A region's OpenAlex topics, derived from the papers actually in it.

    Grounded in members rather than in a keyword search over the region's
    label: "scratch, toolkits, replicating" is a fine human label and a poor
    search query, while the topics its papers carry are what OpenAlex itself
    would use to find more of them.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    lineage: str = Field(index=True)
    cluster: Optional[int] = Field(default=None)
    topic_id: str = Field(index=True)
    topic_name: str = Field(default="")
    share: float = Field(default=0.0)     # fraction of members carrying it
    members: int = Field(default=0)
    resolved_at: datetime = Field(default_factory=_utcnow)
