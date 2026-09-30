"""The public CV page: a source of claims, and the drift from the record."""
from datetime import datetime, timezone

import pytest

from app.config import Config
from app.ledger import drift as drift_mod
from app.models import SourceStatus, WorkCategory
from app.sources import website

PAGE = """
<html><body>
<p>Octavia E. Butler</p>
<p><a href="/s/260812_CV_OctaviaButler.pdf">Download CV</a></p>
<p>PEER REVIEWED PUBLICATIONS</p>
<p>1. Hopkinson, N., Vinge, J. D., <b>Butler, O. E.</b>, Le Guin, U.
   Kindred determinants or Wild Seed drivers? The case of U.S. patternist policy
   adoption. <i>Resources, Conservation &amp; Recycling.</i> 2025.</p>
<p>2. Moon, E., <b>Butler, O. E.</b>, Jemisin, N. EARTHSEED pathways - a bottom-up modelling
   framework to guide sustainable growth. <i>Journal of Building Performance
   Simulation.</i> 2024.</p>
<p>RESEARCH PIPELINE - DRAFTS AVAILABLE UPON REQUEST</p>
<p>1. Novik, N., <b>Butler, O.E.</b>, Lackey, M. Dawn: gatekeeping in sustainable
   materials streams. (Under Revision, Journal of Cleaner Production).</p>
<p>CONFERENCE ACTIVITY</p>
<p>1. <b>Butler, O. E.</b> Parable of the Talents: Predictive Modeling of Technology
   Diffusion. 2019 APPAM Fall Research Conference. 2019.</p>
<p>AWARDS</p>
<p>1. Some award, 2021.</p>
</body></html>
"""


class FakeWork:
    def __init__(self, fp, title, year=None, venue=None, category="publication",
                 doi=None, links=None):
        self.fingerprint, self.title = fp, title
        self.year, self.venue, self.category = year, venue, category
        self.doi, self.links = doi, links or []


def items(works):
    return [(w.fingerprint, w.title, w.year, w.venue, w.category, w.doi, w.links)
            for w in works]


def test_unset_cv_url_is_missing_not_an_error():
    report = website.read(Config())
    assert report.status is SourceStatus.missing
    assert "CV_URL" in report.note


def test_sections_map_to_the_authors_own_categories():
    claim = website.parse(PAGE)
    cats = {e.title[:20]: e.category for e in claim.entries}
    assert claim.sections["Peer Reviewed Publication"] == 2
    assert all(c is WorkCategory.publication
               for t, c in cats.items() if t.startswith(("Kindred", "EARTHSEED")))
    assert any(c is WorkCategory.conference for c in cats.values())


def test_the_research_pipeline_is_not_counted_as_published():
    """A paper under revision is not a work the ledger should carry."""
    claim = website.parse(PAGE)
    pipeline = [e for e in claim.entries if e.section == "Research Pipeline"]
    assert pipeline and all(not e.published for e in pipeline)


def test_a_title_containing_a_capitalised_abbreviation_survives():
    """"The case of U.S. patternist policy" must not be truncated at "U.S."."""
    claim = website.parse(PAGE)
    first = claim.entries[0]
    assert first.title.startswith("Kindred determinants or Wild Seed drivers?")
    assert "patternist policy" in first.title


def test_a_paper_title_is_not_mistaken_for_a_section_heading():
    """"EARTHSEED pathways" contains an all-caps run; sections end at all-caps
    *lines*, not at any capitals found in running text."""
    assert website.parse(PAGE).sections["Peer Reviewed Publication"] == 2


def test_the_claim_is_dated_from_the_cv_file_it_links():
    claim = website.parse(PAGE)
    assert claim.cv_file == "260812_CV_OctaviaButler.pdf"
    assert claim.as_of == datetime(2026, 8, 12, tzinfo=timezone.utc)


def test_drift_reports_work_the_record_has_and_the_page_does_not():
    claim = website.parse(PAGE)
    works = [FakeWork("doi:1", "Kindred determinants or Wild Seed drivers? "
                               "The case of U.S. patternist policy adoption", 2025),
             FakeWork("doi:2", "A paper that never reached the CV", 2023,
                      doi="10.1/unclaimed",
                      links=[{"label": "openalex",
                              "url": "https://openalex.org/W9"}])]
    matched, drift = drift_mod.match(items(works), claim)
    assert "doi:1" in matched, "the claimed paper is recognised"
    assert [u["title"] for u in drift.unclaimed] == ["A paper that never reached the CV"]


def test_an_unclaimed_work_carries_somewhere_to_go_and_look():
    """The unclaimed list is by construction the rows you will not recognise,
    so a title you cannot open is not something you can rule on."""
    claim = website.parse(PAGE)
    works = [FakeWork("doi:2", "A paper that never reached the CV", 2023,
                      doi="10.1/unclaimed",
                      links=[{"label": "openalex",
                              "url": "https://openalex.org/W9"}])]
    _, drift = drift_mod.match(items(works), claim)
    row = drift.unclaimed[0]
    assert row["url"] == "https://doi.org/10.1/unclaimed"
    assert row["links"][0]["url"] == "https://openalex.org/W9"


def test_one_cv_line_cannot_absolve_two_works():
    claim = website.parse(PAGE)
    title = ("Kindred determinants or Wild Seed drivers? The case of U.S. "
             "patternist policy adoption")
    works = [FakeWork("doi:1", title, 2025), FakeWork("doi:2", title, 2024)]
    matched, drift = drift_mod.match(items(works), claim)
    assert len(matched) == 1
    assert len(drift.unclaimed) == 1


def test_talks_are_counted_not_enumerated_as_missing_from_the_record():
    """No index carries a conference session, so listing them as unindexed
    would bury the finding that matters."""
    claim = website.parse(PAGE)
    _, drift = drift_mod.match([], claim)
    assert all(u["category"] == "publication" for u in drift.unindexed)
    assert drift.unindexed_other >= 1


def test_no_claim_means_no_drift_rather_than_an_empty_accusation():
    matched, drift = drift_mod.match(
        items([FakeWork("doi:1", "Anything", 2020)]), None)
    assert matched == {} and drift.checked is False
    assert drift.unclaimed == []


def test_a_pdf_url_is_reported_as_the_wrong_kind_of_link(monkeypatch):
    """The failure this replaced: a PDF parsed as markup, found nothing, and
    the ledger then reported that no CV page was configured at all."""
    import httpx

    class Resp:
        status_code = 200
        headers = {"content-type": "application/pdf"}
        text = "%PDF-1.4 ..."

        def raise_for_status(self):
            return None

    monkeypatch.setenv("CV_URL", "https://example.com/260812_CV_Name.pdf")
    monkeypatch.setattr(httpx.Client, "get", lambda self, url, **kw: Resp())
    report = website.read(Config())
    assert report.status is SourceStatus.error
    assert "HTML page" in report.note
    assert "application/pdf" in report.note


def test_a_page_with_no_recognisable_sections_says_what_it_looked_for(monkeypatch):
    import httpx

    class Resp:
        status_code = 200
        headers = {"content-type": "text/html"}
        text = "<html><body><p>Nothing useful here.</p></body></html>"

        def raise_for_status(self):
            return None

    monkeypatch.setenv("CV_URL", "https://example.com/page-cv")
    monkeypatch.setattr(httpx.Client, "get", lambda self, url, **kw: Resp())
    report = website.read(Config())
    assert report.status is SourceStatus.degraded
    assert "PEER REVIEWED PUBLICATIONS" in report.note
