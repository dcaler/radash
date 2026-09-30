"""Fingerprints, proposals and merge semantics — the parts with no I/O."""
import json

import pytest

from app.ledger import merge as merge_mod
from app.ledger import propose as propose_mod
from app.ledger.fingerprint import (
    doi_family, fingerprint, is_strong, normalize_doi, normalize_title,
)
from app.models import CandidateSource, WorkCandidate


def cand(source=CandidateSource.openalex, **kw):
    kw.setdefault("title", "")
    c = WorkCandidate(source=source, **kw)
    c.fingerprint = fingerprint(doi=c.doi, title=c.title, year=c.year)
    return c


# --- fingerprints ----------------------------------------------------------

def test_doi_is_normalised_from_any_form():
    for raw in ("https://doi.org/10.1/X", "doi:10.1/x", " 10.1/X "):
        assert normalize_doi(raw) == "10.1/x"


def test_no_two_distinct_dois_are_ever_treated_as_one():
    """Zenodo allocates DOIs from a global sequence, so adjacency means nothing.

    An earlier implementation collapsed the numeric tail and made every Zenodo
    DOI one family, which proposed merging three unrelated repositories.
    """
    assert doi_family("10.5281/zenodo.111") != doi_family("10.5281/zenodo.222")
    assert doi_family("10.1016/j.a.1") != doi_family("10.1016/j.a.2")
    assert doi_family("https://doi.org/10.1/X") == "10.1/x"


def test_title_normalisation_survives_cosmetic_differences():
    a = normalize_title("Peer Effects in Diffusion: A Study")
    b = normalize_title("peer effects in diffusion")
    assert a == b, "case and a dropped subtitle must not change identity"


def test_fingerprint_prefers_doi_and_marks_its_strength():
    fp = fingerprint(doi="10.1/x", title="whatever", year=2020)
    assert fp.startswith("doi:") and is_strong(fp)
    weak = fingerprint(title="no doi here", year=2020)
    assert weak.startswith("ttl:") and not is_strong(weak)


def test_fingerprint_is_stable_across_cosmetic_rewording():
    a = fingerprint(title="Peer Effects in Diffusion: A Study", year=2022)
    b = fingerprint(title="peer effects in diffusion", year=2022)
    assert a == b, "a ruling must not be orphaned by a tidied title"


# --- proposals -------------------------------------------------------------

def test_shared_doi_proposes_a_duplicate_with_full_confidence():
    a = cand(doi="10.1/x", title="A paper", year=2020)
    b = cand(CandidateSource.s2, doi="10.1/x", title="A paper", year=2020)
    p = propose_mod.compare(a, b)
    assert p.kind == "duplicate" and p.confidence == 1.0
    assert any(s.name == "doi" and s.fired for s in p.signals)


def test_preprint_and_publication_are_flagged_as_such():
    a = cand(title="Barriers to solar adoption", year=2014, venue="SSRN",
             authors=json.dumps(["O Butler", "N Jemisin"]))
    b = cand(title="Barriers to solar adoption", year=2015,
             venue="Renewable Energy", authors=json.dumps(["O Butler", "N Jemisin"]))
    p = propose_mod.compare(a, b)
    assert p.kind == "preprint"
    assert "preprint" in p.counter_case.lower()


def test_same_title_same_venue_is_a_plain_duplicate():
    a = cand(title="Networks and institutions", year=2021, venue="JASSS",
             authors=json.dumps(["O Butler"]))
    b = cand(CandidateSource.s2, title="Networks and institutions", year=2021,
             venue="JASSS", authors=json.dumps(["O Butler"]))
    assert propose_mod.compare(a, b).kind == "duplicate"


def test_unrelated_works_propose_nothing():
    a = cand(title="Solar adoption among households", year=2015)
    b = cand(title="A study of medieval land tenure", year=1998)
    assert propose_mod.compare(a, b) is None


def test_every_proposal_carries_a_counter_case():
    a = cand(doi="10.1/x", title="A paper", year=2020)
    b = cand(CandidateSource.s2, doi="10.1/x", title="A paper", year=2020)
    assert propose_mod.compare(a, b).counter_case


def test_propose_blocks_and_does_not_compare_everything():
    cands = [cand(title=f"Entirely distinct subject {i}", year=2000 + i)
             for i in range(30)]
    assert propose_mod.propose(cands) == []


def test_an_identical_doi_needs_no_proposal_at_all():
    """Same DOI means same fingerprint, so the rows are already one work."""
    a = cand(doi="10.1/x", title="Another paper", year=2019)
    b = cand(CandidateSource.s2, doi="10.1/x", title="Another paper", year=2019)
    assert a.fingerprint == b.fingerprint
    assert propose_mod.propose([a, b]) == []


def test_propose_sorts_strongest_first():
    ver_a = cand(doi="10.5281/zenodo.111", title="raDash software", year=2026,
                 venue="Zenodo")
    ver_b = cand(doi="10.5281/zenodo.222", title="raDash software", year=2026,
                 venue="Zenodo")
    weak_a = cand(title="Diffusion of rooftop technology", year=2020,
                  authors=json.dumps(["O Butler"]))
    weak_b = cand(CandidateSource.s2, title="Diffusion of rooftop technologies",
                  year=2021, authors=json.dumps(["O Butler"]))
    out = propose_mod.propose([weak_a, weak_b, ver_a, ver_b])
    assert len(out) == 2
    assert out[0].confidence > out[1].confidence
    assert out[0].kind == "version"


def test_a_title_match_with_no_authors_needs_the_years_to_agree():
    """Missing author data is not evidence; it must not read as corroboration."""
    a = cand(CandidateSource.folder, title="Networks and institutions", year=2021)
    b = cand(CandidateSource.cv, title="Networks and institutions", year=2021)
    p = propose_mod.compare(a, b)
    assert p is not None and p.confidence == 0.6
    assert "no author list" in p.counter_case

    c = cand(CandidateSource.folder, title="Networks and institutions", year=2015)
    assert propose_mod.compare(a, c) is None, "differing years, nothing to go on"


# --- merge -----------------------------------------------------------------

def test_citations_take_the_max_within_each_lane_separately():
    group = [
        cand(doi="10.1/x", title="P", year=2015, cited_by=340),
        cand(CandidateSource.s2, doi="10.1/x", title="P", year=2015, cited_by=348),
        cand(CandidateSource.scholar, title="P", year=2015, cited_by=402),
    ]
    m = merge_mod.merge("doi:10.1/x", group)
    assert m.citations_automated == 348, "max within the automated lane"
    assert m.citations_manual == 402, "the manual lane stays separate"
    assert m.provenance["automated"]["source"] == "s2"


def test_published_venue_wins_and_the_preprint_is_kept():
    group = [
        cand(title="P", year=2014, venue="SSRN"),
        cand(title="P", year=2015, venue="Renewable Energy"),
    ]
    m = merge_mod.merge("ttl:x", group)
    assert m.venue == "Renewable Energy"
    assert "SSRN" in m.venues_seen, "the preprint venue is kept, not discarded"
    assert m.year == 2015, "the published year is canonical"


def test_cv_title_wins_over_an_index_title():
    group = [
        cand(title="Short index title", year=2020),
        cand(CandidateSource.cv, title="The full title as you wrote it", year=2020),
    ]
    m = merge_mod.merge("ttl:x", group)
    assert m.title == "The full title as you wrote it"
    assert m.on_cv is True
    assert m.provenance["title"] == "cv"


def test_accrual_takes_the_highest_figure_per_year():
    group = [
        cand(doi="10.1/x", title="P", counts_by_year=json.dumps({"2023": 54, "2024": 10})),
        cand(CandidateSource.s2, doi="10.1/x", title="P",
             counts_by_year=json.dumps({"2023": 50, "2025": 40})),
    ]
    m = merge_mod.merge("doi:10.1/x", group)
    assert m.counts_by_year == {2023: 54, 2024: 10, 2025: 40}


def test_merge_of_nothing_is_empty_not_an_error():
    assert merge_mod.merge("ttl:x", []).title == ""


def test_a_release_series_is_detected_from_title_and_venue_not_from_the_doi():
    """Three releases are three fingerprints; merging them is a human ruling."""
    a = fingerprint(doi="10.5281/zenodo.111", title="raDash", year=2026)
    b = fingerprint(doi="10.5281/zenodo.222", title="raDash", year=2026)
    assert a != b
    ca = cand(doi="10.5281/zenodo.111", title="raDash", year=2026, venue="Zenodo")
    cb = cand(doi="10.5281/zenodo.222", title="raDash", year=2026, venue="Zenodo")
    p = propose_mod.compare(ca, cb)
    assert p.kind == "version" and p.confidence == 0.9


def test_two_different_deposits_are_not_a_release_series():
    """The bug this guards: same host, same year, different software."""
    a = cand(doi="10.5281/zenodo.111", title="HAARPi: a research pipeline",
             year=2026, venue="Zenodo")
    b = cand(doi="10.5281/zenodo.222", title="trundlr: task scheduling",
             year=2026, venue="Zenodo")
    assert propose_mod.compare(a, b) is None


# --- links -----------------------------------------------------------------

def test_links_are_built_only_from_identifiers_a_source_gave():
    from app.ledger import links

    c = cand(doi="10.1/x", source_id="W123")
    urls = {l["label"]: l["url"] for l in links.candidate_links(c)}
    assert urls["DOI"] == "https://doi.org/10.1/x"
    assert urls["openalex"] == "https://openalex.org/W123"


def test_a_candidate_with_no_identifier_gets_no_link():
    """A search URL could land you on the wrong paper, which is worse than
    no link at all when you are about to rule on it."""
    from app.ledger import links
    assert links.candidate_links(cand(title="Something", year=2020)) == []


def test_s2_links_point_at_the_paper_page():
    from app.ledger import links
    c = cand(CandidateSource.s2, source_id="abc123")
    assert links.candidate_links(c)[0]["url"].endswith("/paper/abc123")


def test_proposals_carry_the_type_and_links_of_both_sides():
    a = cand(doi="10.1/x", title="A conference item", year=2013,
             venue="Zenodo", source_id="W1", work_type="conference-paper")
    b = cand(doi="10.1/y", title="A conference item", year=2013,
             venue="Zenodo", source_id="W2", work_type="conference-paper")
    p = propose_mod.compare(a, b).as_dict()
    assert p["left_type"] == "conference-paper"
    assert p["right_type"] == "conference-paper"
    assert any(l["label"] == "openalex" for l in p["left_links"])
    assert any(l["label"] == "openalex" for l in p["right_links"])
