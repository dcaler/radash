"""Snapshot assembly, and the property the milestone rests on:
**a refresh may replace every number, and must never replace a decision.**
"""
import json

import pytest
from sqlmodel import select

from app.config import Config
from app.ledger import build as build_mod
from app.ledger.fingerprint import fingerprint
from app.models import (
    CandidateSource, LedgerRuling, Snapshot, Work, WorkCandidate, WorkDecision,
)
from app.sources import SourceReport
from app.sources.collect import Collection


def cand(source=CandidateSource.openalex, **kw):
    kw.setdefault("title", "")
    c = WorkCandidate(source=source, **kw)
    c.fingerprint = fingerprint(doi=c.doi, title=c.title, year=c.year)
    return c


class FakeSources(Collection):
    """An M1 Collection with nothing in it; candidates are injected instead."""

    def __init__(self):
        super().__init__()
        for key in ("openalex", "s2", "scholar", "zotero", "haarpi", "trundlr"):
            self.reports[key] = SourceReport(key=key)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("PROFESSIONAL_DIR", str(tmp_path / "prof"))
    monkeypatch.setenv("IMPORT_DIR", str(tmp_path / "import"))
    return Config()


def build_with(session, cfg, cands, monkeypatch, **kw):
    monkeypatch.setattr(build_mod, "collect_candidates", lambda c, s: list(cands))
    return build_mod.build(cfg, FakeSources(), session, **kw)


def test_exact_doi_rows_collapse_without_anyone_ruling(session, cfg, monkeypatch):
    """A shared DOI is a join, not a judgement."""
    cands = [
        cand(doi="10.1/x", title="P", year=2015, cited_by=340),
        cand(CandidateSource.s2, doi="10.1/x", title="P", year=2015, cited_by=348),
    ]
    out = build_with(session, cfg, cands, monkeypatch)
    assert len(out.works) == 1
    assert out.works[0].citations_automated == 348
    assert out.proposals == [], "nothing ambiguous, so nothing to ask"


def test_an_ambiguous_pair_does_not_merge_and_moves_no_number(session, cfg, monkeypatch):
    cands = [
        cand(title="Barriers to adoption", year=2014, venue="SSRN",
             authors=json.dumps(["O Butler"]), cited_by=10),
        cand(title="Barriers to adoption", year=2015, venue="Renewable Energy",
             authors=json.dumps(["O Butler"]), cited_by=348),
    ]
    out = build_with(session, cfg, cands, monkeypatch)
    assert len(out.works) == 2, "a proposal must not merge on its own"
    assert len(out.proposals) == 1
    assert out.proposals[0].kind == "preprint"


def test_a_confirmed_ruling_merges_on_the_next_rebuild(session, cfg, monkeypatch):
    cands = [
        cand(title="Barriers to adoption", year=2014, venue="SSRN",
             authors=json.dumps(["O Butler"]), cited_by=10),
        cand(title="Barriers to adoption", year=2015, venue="Renewable Energy",
             authors=json.dumps(["O Butler"]), cited_by=348),
    ]
    first = build_with(session, cfg, cands, monkeypatch)
    p = first.proposals[0]
    session.add(WorkDecision(fingerprint=p.left_fingerprint,
                             other_fingerprint=p.right_fingerprint,
                             ruling=LedgerRuling.confirmed))
    session.commit()

    second = build_with(session, cfg, cands, monkeypatch)
    assert len(second.works) == 1, "the ruling took effect"
    assert second.proposals == [], "and stopped being asked"
    assert second.works[0].citations_automated == 348


def test_a_split_ruling_stops_the_question_being_asked_again(session, cfg, monkeypatch):
    cands = [
        cand(title="Barriers to adoption", year=2014, venue="SSRN",
             authors=json.dumps(["O Butler"])),
        cand(title="Barriers to adoption", year=2015, venue="Renewable Energy",
             authors=json.dumps(["O Butler"])),
    ]
    first = build_with(session, cfg, cands, monkeypatch)
    p = first.proposals[0]
    session.add(WorkDecision(fingerprint=p.left_fingerprint,
                             other_fingerprint=p.right_fingerprint,
                             ruling=LedgerRuling.split))
    session.commit()

    second = build_with(session, cfg, cands, monkeypatch)
    assert len(second.works) == 2, "they stay apart"
    assert second.proposals == [], "and you are not asked twice"


def test_an_exclusion_drops_the_work_entirely(session, cfg, monkeypatch):
    keep = cand(doi="10.1/keep", title="Mine", year=2020)
    drop = cand(doi="10.1/drop", title="Someone else's", year=2020)
    session.add(WorkDecision(fingerprint=drop.fingerprint,
                             ruling=LedgerRuling.excluded))
    session.commit()
    out = build_with(session, cfg, [keep, drop], monkeypatch)
    assert [w.title for w in out.works] == ["Mine"]
    assert drop.fingerprint in out.excluded


def test_rulings_survive_a_rebuild_that_changes_every_number(session, cfg, monkeypatch):
    """The milestone's core claim, stated as a test."""
    early = [
        cand(title="Barriers to adoption", year=2014, venue="SSRN",
             authors=json.dumps(["O Butler"]), cited_by=10),
        cand(title="Barriers to adoption", year=2015, venue="Renewable Energy",
             authors=json.dumps(["O Butler"]), cited_by=100),
    ]
    first = build_with(session, cfg, early, monkeypatch)
    p = first.proposals[0]
    session.add(WorkDecision(fingerprint=p.left_fingerprint,
                             other_fingerprint=p.right_fingerprint,
                             ruling=LedgerRuling.confirmed,
                             rationale="same paper, SSRN first"))
    session.commit()

    # Every citation figure changes; the titles and years do not.
    later = [
        cand(title="Barriers to adoption", year=2014, venue="SSRN",
             authors=json.dumps(["O Butler"]), cited_by=12),
        cand(title="Barriers to adoption", year=2015, venue="Renewable Energy",
             authors=json.dumps(["O Butler"]), cited_by=348),
    ]
    second = build_with(session, cfg, later, monkeypatch)
    assert len(second.works) == 1
    assert second.works[0].citations_automated == 348, "numbers refreshed"
    kept = session.exec(select(WorkDecision)).all()
    assert len(kept) == 1 and kept[0].rationale == "same paper, SSRN first"


def test_the_previous_snapshot_is_retained_not_overwritten(session, cfg, monkeypatch):
    cands = [cand(doi="10.1/x", title="P", year=2020)]
    build_with(session, cfg, cands, monkeypatch)
    build_with(session, cfg, cands, monkeypatch)
    snaps = session.exec(select(Snapshot)).all()
    assert len(snaps) == 2, "a bad refresh stays comparable against the last"
    assert sum(1 for s in snaps if s.is_current) == 1


def test_candidates_are_recorded_per_source_without_dedup(session, cfg, monkeypatch):
    cands = [
        cand(doi="10.1/x", title="P", year=2015, source_profile="A1"),
        cand(doi="10.1/x", title="P", year=2015, source_profile="A2"),
    ]
    out = build_with(session, cfg, cands, monkeypatch)
    rows = session.exec(select(WorkCandidate)).all()
    assert len(rows) == 2, "both profiles' claims are kept"
    assert len(out.works) == 1, "even though they resolve to one work"
    assert out.counts["by_source"]["openalex"] == 2


def test_a_pasted_publication_list_is_folded_in(session, cfg, monkeypatch):
    cands = [cand(doi="10.1/x", title="Index version", year=2020)]
    out = build_with(session, cfg, cands, monkeypatch,
                     publication_list="Butler, O. E. (2020). A fuller title as written. Venue.")
    assert out.counts["by_source"].get("cv") == 1


def test_secondary_profile_work_is_proposed_for_adoption(session, cfg, monkeypatch):
    """A duplicate profile is not evidence the work is yours."""
    monkeypatch.setenv("OPENALEX_AUTHOR_IDS", "A_PRIMARY,A_SECOND")
    cands = [
        cand(doi="10.1/mine", title="Mine", year=2020, source_profile="A_PRIMARY"),
        cand(doi="10.1/maybe", title="Only on the other profile", year=2019,
             source_profile="A_SECOND"),
    ]
    out = build_with(session, Config(), cands, monkeypatch)
    adopts = [p for p in out.proposals if p.kind == "adopt"]
    assert len(adopts) == 1
    assert "Only on the other profile" in adopts[0].left_label
    assert "confirm it is yours" in adopts[0].counter_case


def test_a_work_on_both_profiles_needs_no_adoption(session, cfg, monkeypatch):
    monkeypatch.setenv("OPENALEX_AUTHOR_IDS", "A_PRIMARY,A_SECOND")
    cands = [
        cand(doi="10.1/x", title="Shared", year=2020, source_profile="A_PRIMARY"),
        cand(doi="10.1/x", title="Shared", year=2020, source_profile="A_SECOND"),
    ]
    out = build_with(session, Config(), cands, monkeypatch)
    assert [p for p in out.proposals if p.kind == "adopt"] == []


def test_corroboration_from_another_source_settles_adoption(session, cfg, monkeypatch):
    """If S2 or your own CV also lists it, the secondary profile is not the
    only witness and there is nothing to ask about."""
    monkeypatch.setenv("OPENALEX_AUTHOR_IDS", "A_PRIMARY,A_SECOND")
    cands = [
        cand(doi="10.1/y", title="Corroborated", year=2019, source_profile="A_SECOND"),
        cand(CandidateSource.s2, doi="10.1/y", title="Corroborated", year=2019),
    ]
    out = build_with(session, Config(), cands, monkeypatch)
    assert [p for p in out.proposals if p.kind == "adopt"] == []


def test_a_ruled_adoption_stops_being_asked(session, cfg, monkeypatch):
    monkeypatch.setenv("OPENALEX_AUTHOR_IDS", "A_PRIMARY,A_SECOND")
    cands = [cand(doi="10.1/maybe", title="Theirs", year=2019,
                  source_profile="A_SECOND")]
    first = build_with(session, Config(), cands, monkeypatch)
    fp = first.proposals[0].left_fingerprint
    session.add(WorkDecision(fingerprint=fp, ruling=LedgerRuling.confirmed))
    session.commit()
    second = build_with(session, Config(), cands, monkeypatch)
    assert [p for p in second.proposals if p.kind == "adopt"] == []


# --- categories ------------------------------------------------------------

def test_categories_are_inferred_from_type_and_venue(session, cfg, monkeypatch):
    from app.models import WorkCategory
    cands = [
        cand(doi="10.1/a", title="Journal work", year=2020, work_type="article",
             venue="Renewable Energy"),
        cand(doi="10.1/b", title="A talk", year=2019, work_type="conference-paper",
             venue="2019 APPAM Fall Research Conference"),
        cand(doi="10.5281/zenodo.1", title="A tool", year=2026, work_type="software",
             venue="Zenodo"),
        cand(doi="10.1/d", title="A preprint", year=2024, work_type="preprint",
             venue="SSRN Electronic Journal"),
    ]
    out = build_with(session, cfg, cands, monkeypatch)
    by_title = {w.title: w.category for w in out.works}
    assert by_title["Journal work"] == WorkCategory.publication.value
    assert by_title["A talk"] == WorkCategory.conference.value
    assert by_title["A tool"] == WorkCategory.software.value
    assert by_title["A preprint"] == WorkCategory.preprint.value
    assert out.counts["portfolio"] == 1, "only the journal work is a publication"


def test_a_conference_paper_is_not_silently_called_a_publication(session, cfg, monkeypatch):
    """OpenAlex cannot tell an IEEE proceedings paper from an APPAM abstract,
    so neither does raDash: they get their own category rather than a guess."""
    from app.models import WorkCategory
    cands = [cand(doi="10.1109/x", title="Proceedings paper", year=2013,
                  work_type="conference-paper", venue="IEEE PES")]
    out = build_with(session, cfg, cands, monkeypatch)
    assert out.works[0].category == WorkCategory.conference.value
    assert out.counts["portfolio"] == 0


def test_a_venue_hint_overrides_a_wrong_type(session, cfg, monkeypatch):
    """A deposit host is a deposit host whatever OpenAlex called the thing."""
    from app.models import WorkCategory
    cands = [cand(doi="10.5281/zenodo.9", title="Mislabelled", year=2026,
                  work_type="article", venue="Zenodo (CERN)")]
    out = build_with(session, cfg, cands, monkeypatch)
    assert out.works[0].category == WorkCategory.software.value


def test_your_classification_beats_the_inference_and_survives(session, cfg, monkeypatch):
    from app.models import WorkCategory, WorkClassification
    c = cand(doi="10.1/x", title="A talk that is really a paper", year=2013,
             work_type="conference-paper", venue="IEEE PES")
    first = build_with(session, cfg, [c], monkeypatch)
    assert first.works[0].category == WorkCategory.conference.value

    session.add(WorkClassification(fingerprint=c.fingerprint,
                                   category=WorkCategory.publication,
                                   rationale="peer reviewed proceedings"))
    session.commit()

    second = build_with(session, cfg, [c], monkeypatch)
    assert second.works[0].category == WorkCategory.publication.value
    assert second.works[0].category_source == "you"
    assert second.counts["portfolio"] == 1


def test_a_folder_name_alone_does_not_become_a_work(session, cfg, monkeypatch):
    """`2022_JASSS_FIFTH` says a 2022 JASSS paper exists. It does not say what
    it is called, so it must not enter the ledger as a work named "FIFTH"."""
    cands = [
        cand(doi="10.1/real", title="A properly titled paper", year=2022,
             venue="JASSS", work_type="article"),
        cand(CandidateSource.folder, title="FIFTH", year=2022, venue="JASSS"),
    ]
    out = build_with(session, cfg, cands, monkeypatch)
    titles = {w.title for w in out.works}
    assert "FIFTH" not in titles
    assert titles == {"A properly titled paper"}
    assert any("FIFTH" in u for u in out.unmatched_folders)


def test_folder_corroboration_is_reported_not_dropped(session, cfg, monkeypatch):
    """Unmatched publication folders are a coverage finding worth surfacing."""
    cands = [cand(CandidateSource.folder, title="Hopkinson", year=2024,
                  venue="ResourceCons")]
    out = build_with(session, cfg, cands, monkeypatch)
    assert out.works == []
    assert out.unmatched_folders == ["2024_ResourceCons_Hopkinson"]
    assert out.summary()["unmatched_folders"] == ["2024_ResourceCons_Hopkinson"]


def test_a_three_way_duplicate_merges_transitively(session, cfg, monkeypatch):
    """Proposals are pairwise; merging is not. Confirming two of the three
    pairs settles all three, and the third is not then asked about."""
    import json as _json
    releases = [cand(doi=f"10.5281/zenodo.{n}", title="A tool", year=2026,
                     venue="Zenodo", authors=_json.dumps(["O Butler"]))
                for n in (1, 2, 3)]
    first = build_with(session, cfg, releases, monkeypatch)
    assert len(first.proposals) == 3, "every pair is offered"
    assert len(first.works) == 3, "and nothing has merged"

    a, b, c = (r.fingerprint for r in releases)
    session.add(WorkDecision(fingerprint=a, other_fingerprint=b,
                             ruling=LedgerRuling.confirmed))
    session.add(WorkDecision(fingerprint=a, other_fingerprint=c,
                             ruling=LedgerRuling.confirmed))
    session.commit()

    after = build_with(session, cfg, releases, monkeypatch)
    assert len(after.works) == 1, "all three collapse into one work"
    assert after.proposals == [], "B=C follows, so it is not asked"


def test_a_pair_already_merged_is_not_re_asked(session, cfg, monkeypatch):
    """The ordering trap: whether B=C was asked used to depend on where it sat
    in the list relative to the rulings that made it redundant."""
    import json as _json
    releases = [cand(doi=f"10.5281/zenodo.{n}", title="A tool", year=2026,
                     venue="Zenodo", authors=_json.dumps(["O Butler"]))
                for n in (1, 2, 3)]
    a, b, c = (r.fingerprint for r in releases)
    session.add(WorkDecision(fingerprint=b, other_fingerprint=c,
                             ruling=LedgerRuling.confirmed))
    session.add(WorkDecision(fingerprint=a, other_fingerprint=c,
                             ruling=LedgerRuling.confirmed))
    session.commit()
    out = build_with(session, cfg, releases, monkeypatch)
    assert len(out.works) == 1
    assert out.proposals == []
