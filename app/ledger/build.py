"""Assemble a snapshot of the works ledger.

Order matters here, and the reason for it is the milestone's whole point:

1. **Candidates** are recorded exactly as each source stated them.
2. **Exact-DOI rows collapse for free**, because a shared DOI produces a shared
   fingerprint. That is a join, not a judgement, and it needs nobody's opinion.
3. **Standing decisions apply.** A ruling you made at any previous gate is
   re-applied, because decisions are keyed by fingerprint rather than by row id
   and so outlive the rows they were made about.
4. **Everything still ambiguous stays ambiguous** and becomes a proposal. It
   does not merge, it does not quietly pick a side, and it does not move a
   count until you rule on it.

The ledger a panel reads is therefore derived state: candidates and rulings are
the truth, and this rebuilds `Work` rows from them on every refresh.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from app.config import Config
from app.ledger import candidates as cand_mod
from app.ledger import categorize
from app.ledger import drift as drift_mod
from app.ledger import merge as merge_mod
from app.ledger import propose as propose_mod
from app.ledger import links as links_mod
from app.ledger.fingerprint import is_strong
from app.ledger.propose import Proposal, Signal
from app.models import (
    CandidateSource, LedgerRuling, Snapshot, Work, WorkCandidate, WorkCategory,
    WorkClassification, WorkDecision,
)


@dataclass
class LedgerBuild:
    snapshot_id: Optional[int] = None
    works: list = field(default_factory=list)
    proposals: list = field(default_factory=list)
    candidate_count: int = 0
    excluded: list[str] = field(default_factory=list)
    unmatched_folders: list[str] = field(default_factory=list)
    drift: Optional["drift_mod.Drift"] = None
    counts: dict = field(default_factory=dict)

    def summary(self) -> dict:
        pending = [p for p in self.proposals]
        return {
            "snapshot_id": self.snapshot_id,
            "candidates": self.candidate_count,
            "works": len(self.works),
            "open_proposals": len(pending),
            "by_kind": {
                k: sum(1 for p in pending if p.kind == k)
                for k in ("duplicate", "preprint", "version", "adopt")
            },
            "excluded": len(self.excluded),
            "unmatched_folders": self.unmatched_folders,
            "public_drift": self.drift.summary() if self.drift else None,
            **self.counts,
        }


class _Union:
    """Union-find over fingerprints, biased to keep a DOI-backed root."""

    def __init__(self):
        self.parent: dict[str, str] = {}

    def add(self, fp: str) -> None:
        self.parent.setdefault(fp, fp)

    def find(self, fp: str) -> str:
        self.add(fp)
        while self.parent[fp] != fp:
            self.parent[fp] = self.parent[self.parent[fp]]
            fp = self.parent[fp]
        return fp

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        # A DOI-backed fingerprint makes the better root: it is the one that
        # will still be generated identically next week.
        if is_strong(rb) and not is_strong(ra):
            ra, rb = rb, ra
        elif is_strong(ra) == is_strong(rb) and rb < ra:
            ra, rb = rb, ra
        self.parent[rb] = ra


def collect_candidates(cfg: Config, sources) -> list[WorkCandidate]:
    """Every source's claims, as rows. `sources` is an M1 `Collection`."""
    out: list[WorkCandidate] = []
    oa = sources.reports.get("openalex")
    s2 = sources.reports.get("s2")
    sch = sources.reports.get("scholar")
    out += cand_mod.from_openalex(oa.data if oa else None)
    out += cand_mod.from_s2(s2.data if s2 else None)
    out += cand_mod.from_scholar(sch.data if sch else None)
    site_report = sources.reports.get("website")
    out += cand_mod.from_website(site_report.data if site_report else None)
    out += cand_mod.from_folders(cfg)
    return out


def build(cfg: Config, sources, session: Session,
          publication_list: Optional[str] = None,
          label: str = "manual") -> LedgerBuild:
    """Build and persist one snapshot of the ledger."""
    result = LedgerBuild()

    cands = collect_candidates(cfg, sources)
    if publication_list:
        cands += cand_mod.from_publication_list(publication_list)
    result.candidate_count = len(cands)

    # Recorded for provenance; `_group` is what holds them out of grouping.
    # See drift.match: a CV line describes a work, it does not assert one.
    site_cands = [c for c in cands if c.source == CandidateSource.site]
    cands = [c for c in cands if c.source != CandidateSource.site]

    snapshot = Snapshot(created_at=datetime.now(timezone.utc), label=label)
    session.add(snapshot)
    session.commit()
    session.refresh(snapshot)
    result.snapshot_id = snapshot.id

    for c in cands + site_cands:
        c.snapshot_id = snapshot.id
        session.add(c)
    session.commit()
    for c in cands:
        session.refresh(c)

    decisions = session.exec(select(WorkDecision)).all()
    overrides = {c.fingerprint: c.category
                 for c in session.exec(select(WorkClassification)).all()}
    result.proposals, groups, excluded = _group(cands, decisions)
    result.proposals += _adoptions(groups, decisions, cfg)
    result.excluded = excluded

    # A folder name is corroboration, not a record. `2022_JASSS_FIFTH` tells
    # you a 2022 JASSS paper exists; it does not tell you its title, so a
    # group containing nothing but folder rows would enter the ledger as a
    # work called "FIFTH" with no venue, no citations and no way to recognise
    # it. Those are reported as unmatched corroboration instead.
    corroboration_only = {
        fp for fp, members in groups.items()
        if all(c.source == CandidateSource.folder for c in members)
    }
    result.unmatched_folders = sorted(
        f"{c.year or '????'}_{c.venue or '?'}_{c.title}"
        for fp in corroboration_only for c in groups[fp]
    )

    # Merge first, then overlay the public claim, then write the rows: the
    # claim is matched on title, so it needs the merged title to match against.
    merged_rows = []
    for fp, members in groups.items():
        if fp in corroboration_only:
            continue
        merged = merge_mod.merge(fp, members)
        merged_rows.append((fp, merged,
                            categorize.infer(merged.work_type, merged.venue)))

    site_report = sources.reports.get("website")
    on_site, result.drift = drift_mod.match(
        [(fp, m.title, m.year, m.venue, inferred.value, m.doi,
          _links_of(m)) for fp, m, inferred in merged_rows],
        site_report.data if site_report else None)
    if not result.drift.checked and site_report is not None:
        # "Not checked" has several causes and they need different actions:
        # unset, unreachable, the wrong kind of URL, or a page that parsed to
        # nothing. Carry the source's own account rather than guessing.
        result.drift.reason = site_report.note or f"status {site_report.status.value}"

    works = []
    for fp, merged, inferred in merged_rows:
        # Precedence: your explicit override, then how your CV page files it
        # (you classifying your own work), then inference from the indexes.
        override = overrides.get(fp)
        entry = on_site.get(fp)
        claimed = entry.category if entry else None
        category = override or claimed or inferred
        work = Work(
            category=category.value,
            category_source=("you" if override
                             else "your CV page" if claimed else "inferred"),
            on_site=bool(entry),
            snapshot_id=snapshot.id, fingerprint=fp, title=merged.title,
            year=merged.year, venue=merged.venue, doi=merged.doi,
            work_type=merged.work_type,
            venues_seen=json.dumps(merged.venues_seen),
            citations_automated=merged.citations_automated,
            citations_manual=merged.citations_manual,
            citations_provenance=json.dumps(merged.provenance),
            counts_by_year=json.dumps(merged.counts_by_year),
            candidate_ids=json.dumps(merged.candidate_ids),
            on_cv=merged.on_cv,
            confirmed=any(d.fingerprint == fp and d.ruling is LedgerRuling.confirmed
                          for d in decisions),
        )
        session.add(work)
        works.append(work)
    session.commit()

    # This snapshot becomes the one panels read, and the previous is retained
    # rather than dropped so a bad refresh stays comparable against the last
    # good one.
    for row in session.exec(select(Snapshot).where(Snapshot.is_current)).all():
        row.is_current = False
        session.add(row)
    snapshot.is_current = True
    snapshot.source_status = json.dumps(
        {k: r.status.value for k, r in sources.reports.items()})
    snapshot.drift = json.dumps(result.drift.summary()) if result.drift else None
    session.add(snapshot)
    session.commit()

    result.works = works
    by_category: dict[str, int] = {}
    for w in works:
        by_category[w.category] = by_category.get(w.category, 0) + 1
    result.counts = {
        "by_category": by_category,
        "portfolio": sum(1 for w in works
                         if w.category in {c.value for c in categorize.PORTFOLIO}),
        "on_cv": sum(1 for w in works if w.on_cv),
        "with_doi": sum(1 for w in works if w.doi),
        "citations_automated": sum(w.citations_automated or 0 for w in works),
        "citations_manual": sum(w.citations_manual or 0 for w in works),
        "by_source": _by_source(cands),
    }
    return result


def _group(cands, decisions):
    """Apply standing rulings, then leave the rest for the gate.

    Site rows are dropped here rather than by the caller. They were filtered in
    `build` and not in `/api/ledger/proposals`, which re-reads every candidate
    from the database — so the gate showed proposals to merge a paper with your
    own CV's description of it, complete with a "no link" on the side that is a
    sentence you wrote. Two call sites computing the same thing differently is
    the failure; one filter inside the function they share is the fix.
    """
    cands = [c for c in cands if c.source != CandidateSource.site]
    union = _Union()
    for c in cands:
        union.add(c.fingerprint)

    excluded = {d.fingerprint for d in decisions
                if d.ruling is LedgerRuling.excluded}
    live = [c for c in cands if c.fingerprint not in excluded]

    ruled: dict[tuple[str, str], LedgerRuling] = {}
    for d in decisions:
        if d.other_fingerprint:
            ruled[tuple(sorted((d.fingerprint, d.other_fingerprint)))] = d.ruling

    proposals = propose_mod.propose(live)

    # Two passes, because merging is transitive and asking is not. Confirm
    # A=B and A=C and all three are one work -- but B=C is still a proposal in
    # the list, and a single pass in list order would either ask it or not
    # depending on where it happened to sit. Apply every ruling first, then
    # ask only what is still genuinely open.
    for p in proposals:
        if ruled.get(p.key) is LedgerRuling.confirmed:
            union.union(p.left_fingerprint, p.right_fingerprint)

    open_proposals = []
    for p in proposals:
        ruling = ruled.get(p.key)
        if ruling is LedgerRuling.confirmed:
            continue          # already applied above
        if ruling is LedgerRuling.split:
            continue          # settled: they stay apart, and stop being asked
        if union.find(p.left_fingerprint) == union.find(p.right_fingerprint):
            continue          # already one work by implication; do not re-ask
        open_proposals.append(p)   # pending: nothing moves until you rule

    groups: dict[str, list] = {}
    for c in live:
        groups.setdefault(union.find(c.fingerprint), []).append(c)
    return open_proposals, groups, sorted(excluded)


def _links_of(merged) -> list[dict]:
    """Every distinct way to open a merged work, across its candidates."""
    seen, out = set(), []
    for c in merged.provenance.get("candidates", []):
        for link in c.get("links") or []:
            key = (link.get("label"), link.get("url"))
            if key not in seen:
                seen.add(key)
                out.append(link)
    return out


def _adoptions(groups, decisions, cfg) -> list[Proposal]:
    """Propose adopting works that only the secondary profile claims.

    A fragmented duplicate OpenAlex profile is not evidence that the work is
    yours — profiles get merged wrongly, and a stranger's paper arriving in a
    citation total is exactly the kind of quiet error this milestone exists to
    prevent. So a work seen *only* on a non-primary profile is proposed rather
    than absorbed, and the first id in `OPENALEX_AUTHOR_IDS` is taken as the
    one you have already vouched for.
    """
    ids = cfg.openalex_author_ids
    if len(ids) < 2:
        return []
    primary = ids[0]
    ruled = {d.fingerprint for d in decisions if d.other_fingerprint is None}

    out: list[Proposal] = []
    for fp, members in groups.items():
        if fp in ruled:
            continue
        profiles = {c.source_profile for c in members
                    if c.source == CandidateSource.openalex and c.source_profile}
        corroborated = any(c.source != CandidateSource.openalex for c in members)
        if not profiles or primary in profiles or corroborated:
            continue
        first = members[0]
        label = f"{(first.title or '')[:70]} ({first.year or '—'})"
        links = [l for c in members for l in links_mod.candidate_links(c)]
        out.append(Proposal(
            left_fingerprint=fp, right_fingerprint="", kind="adopt",
            confidence=0.5,
            signals=[Signal("secondary_profile", True,
                            f"only on {', '.join(sorted(profiles))}", 0.5)],
            counter_case=("Seen only on your secondary OpenAlex profile and "
                          "nowhere else. Duplicate profiles accumulate other "
                          "people's work, so confirm it is yours before its "
                          "citations join your totals."),
            left_label=label, right_label="(not on your primary profile)",
            left_type=first.work_type,
            left_links=[dict(t) for t in {tuple(sorted(l.items())) for l in links}],
        ))
    out.sort(key=lambda p: p.left_label)
    return out


def _by_source(cands) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in cands:
        out[c.source.value] = out.get(c.source.value, 0) + 1
    return out
