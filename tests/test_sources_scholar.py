"""Scholar: two input shapes, and staleness as the headline fact."""
from datetime import datetime, timedelta, timezone

import pytest

from app.config import Config
from app.models import SourceStatus
from app.sources import scholar

PASTE = """The Dispossessed: an ambiguous utopia of residential solar
OE Butler, N Jemisin - Renewable Energy, 2015
Cited by 300

Kindred effects in the diffusion of solar photovoltaic panels
OE Butler - JASSS, 2022
Cited by 18

An uncited working paper about networks and institutions
OE Butler - Working paper, 2026
"""

CSV = """Title,Authors,Publication,Volume,Number,Pages,Year,Publisher
The Dispossessed: an ambiguous utopia,"Butler, OE",Renewable Energy,12,,101-120,2015,Elsevier
Kindred effects in diffusion,"Butler, OE",JASSS,31,2,,2022,
"""

# `citations.csv`, as Google Scholar actually writes it: a UTF-8 byte-order
# mark, and Authors as the first column rather than Title. Both details are
# kept rather than tidied away, because both are the bug this fixture exists
# to hold down.
REAL_CSV = (
    "\ufeffAuthors,Title,Publication,Volume,Number,Pages,Year,Publisher\n"
    '"Cherryh, C; Leckie, A; Butler, O E; ",'
    "Visualizing the Fifth Season: Peak Energy Analysis,"
    "Energy and Buildings,7,,21-30,2015,Elsevier\n"
    '"Jemisin, Nora; Butler, Octavia E.; Russ, Joanna; ",'
    "The Dispossessed: an ambiguous utopia in the adoption of residential "
    "solar PV,Renewable Energy,12,,101-120,2016,Elsevier\n"
)


def test_scholars_own_csv_export_carries_no_citation_counts():
    """Asked directly: why not just use `citations.csv`?

    Because Scholar puts a citation column in none of its exports, and the
    counts are the entire reason this lane is worked by hand. Checked against
    a real export rather than from memory: the header is Authors, Title,
    Publication, Volume, Number, Pages, Year, Publisher.
    """
    parsed = scholar.parse(REAL_CSV)
    assert parsed.shape == "csv"
    assert parsed.with_counts == 0
    assert parsed.citation_sum == 0
    # Still worth parsing. The titles, venues and years reach the ledger as
    # candidates; it is only the manual citation lane the CSV cannot move.
    assert len(parsed.entries) == 2
    assert parsed.entries[0].venue == "Energy and Buildings"
    assert parsed.entries[0].year == 2015


def test_the_byte_order_mark_does_not_eat_the_first_column():
    """Scholar writes the file UTF-8 with a BOM, and it lands inside the first
    header cell — the key becomes the marker plus `authors`, so that column
    vanishes with nothing anywhere reporting a problem.

    On the real export the first column is Authors, and authorship overlap is
    one of the signals the duplicate proposals rest on, so the cost was
    quietly weaker dedup on every Scholar-sourced work. Had Scholar ordered
    Title first, the same bug would have parsed the whole file to zero entries.
    """
    parsed = scholar.parse(REAL_CSV)
    assert len(parsed.entries) == 2
    assert all(e.authors for e in parsed.entries), "the first column survives"
    assert parsed.entries[0].authors.startswith("Cherryh")


@pytest.fixture
def import_dir(tmp_path, monkeypatch):
    d = tmp_path / "import"
    d.mkdir()
    monkeypatch.setenv("IMPORT_DIR", str(d))
    return d


def test_paste_shape_captures_counts():
    parsed = scholar.parse(PASTE)
    assert parsed.shape == "paste"
    assert len(parsed.entries) == 3
    first = parsed.entries[0]
    assert first.cited_by == 300
    assert first.year == 2015
    assert parsed.citation_sum == 318
    assert parsed.with_counts == 2


def test_an_uncaptured_count_is_none_not_zero():
    """'Not captured' and 'never cited' are different facts."""
    parsed = scholar.parse(PASTE)
    assert parsed.entries[2].cited_by is None


def test_csv_shape_parses_but_cannot_move_the_lane(import_dir):
    (import_dir / "260901_scholar.csv").write_text(CSV)
    report = scholar.read(Config())
    assert report.count == 2
    assert report.status is SourceStatus.degraded
    assert "no 'Cited by' column" in " ".join(report.failures)
    assert report.counts["with_citations"] == 0


def test_the_newest_export_wins(import_dir):
    import os, time
    old = import_dir / "260101_old.txt"
    new = import_dir / "260901_new.txt"
    old.write_text("An older export entry here\nOE Butler - Venue, 2020\nCited by 1\n")
    new.write_text(PASTE)
    os.utime(old, (1, 1))
    report = scholar.read(Config())
    assert report.data.source_file == "260901_new.txt"


def test_filename_datestamp_beats_mtime(import_dir):
    """A file copied between machines gets a fresh mtime; the stamp is the truth."""
    f = import_dir / "250101_scholar.txt"
    f.write_text(PASTE)
    report = scholar.read(Config())
    assert report.data.captured_at.year == 2025
    assert report.status is SourceStatus.stale
    assert "days old" in report.note


def test_a_recent_export_is_not_stale(import_dir):
    stamp = datetime.now(timezone.utc).strftime("%y%m%d")
    (import_dir / f"{stamp}_scholar.txt").write_text(PASTE)
    report = scholar.read(Config())
    assert report.status is SourceStatus.ok
    assert report.data.stale is False


def test_no_import_directory_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("IMPORT_DIR", str(tmp_path / "nope"))
    report = scholar.read(Config())
    assert report.status is SourceStatus.missing
    assert "nothing imported yet" in report.note


def test_the_missing_note_does_not_send_you_to_a_directory(tmp_path, monkeypatch):
    """It used to read "drop a Scholar export" into a path inside raDash's own
    Docker volume — no host side, nothing to open, and an importer already
    built for the job sitting one page away. Advice you cannot follow is worse
    than none, because you go looking."""
    monkeypatch.setenv("IMPORT_DIR", str(tmp_path / "nope"))
    empty = tmp_path / "empty"
    empty.mkdir()

    notes = [scholar.read(Config()).note]
    monkeypatch.setenv("IMPORT_DIR", str(empty))
    notes.append(scholar.read(Config()).note)

    for note in notes:
        assert str(tmp_path) not in note, "no filesystem path a human cannot reach"
        assert "importer" in note, "it names the control that fixes it"
        assert "no folder to drop a file into" in note, (
            "and says outright that there is no directory to go looking for")


def test_an_empty_export_is_an_error_not_a_silent_zero(import_dir):
    (import_dir / "260901_empty.txt").write_text("\n\n")
    report = scholar.read(Config())
    assert report.status is SourceStatus.error


# A real selection from a Google Scholar profile, kept as explicit string
# concatenation so nothing that reflows this file can quietly change the
# column gaps or the indentation — both are load-bearing.
REAL_PASTE = (
    "TITLE\n"
    "CITED BY\n"
    "YEAR\n"
    "    The Dispossessed: an ambiguous utopia of residential solar PV\n"
    "N Jemisin, OE Butler, J Russ\n"
    "Renewable Energy 12, 101-120    314    2016\n"
    "    Visualizing the Fifth Season: Peak Energy Analysis\n"
    "C Cherryh, A Leckie, OE Butler, M Wells, C Willis, SS Tepper\n"
    "Energy and Buildings 7, 21-30    42    2015\n"
    "    The Lathe of Heaven: agent heterogeneity in agent-based models\n"
    "OE Butler, N Okorafor, A McCaffrey, N Jemisin\n"
    "Journal of Artificial Societies and Social Simulation 31 (3)    17    2022\n"
    "    Xenogenesis, Sequencing and Timing of Home Energy Technology Co-Adoption\n"
    "A McCaffrey, OE Butler, T Lee, N Jemisin\n"
    "2023 APPAM Fall Research Conference        2023\n"
    "    Ancillary Justice: A Policy Toolkit\n"
    "M Piercy, M Atwood, LM Bujold, P Cadigan, OE Butler, A McCaffrey, N Jemisin, ...\n"
    "2020\n"
    "    The Word for World Is Forest: Property Values Near Utility-Scale Solar\n"
    "M Robinette-Kowal, B Chambers, M Shelley, OE Butler, J Tiptree, A Norton\n"
)


def test_the_real_profile_paste_captures_its_citation_counts():
    """The whole reason this lane is worked by hand, and it did not work.

    Copying the table off a Scholar profile produces columns — an indented
    title, an authors line, then venue, count and year in separate cells. The
    string `Cited by 300` appears nowhere in it, and the parser this replaced
    searched for exactly that phrase, so a real paste yielded no counts at all.
    """
    parsed = scholar.parse(REAL_PASTE)
    assert parsed.shape == "paste"
    assert parsed.with_counts == 3
    assert parsed.citation_sum == 314 + 42 + 17

    first = parsed.entries[0]
    assert first.title.startswith("The Dispossessed")
    assert first.cited_by == 314
    assert first.year == 2016
    assert first.venue == "Renewable Energy 12, 101-120"
    assert first.authors == "N Jemisin, OE Butler, J Russ"


def test_an_uncited_work_does_not_borrow_its_year_as_a_count():
    """An uncited work leaves the count cell empty, so the row is venue then
    year. Reading that year as a citation count is how the old parser turned
    a 2020 toolkit into 2,020 citations and summed it into the lane."""
    by_title = {e.title: e for e in scholar.parse(REAL_PASTE).entries}

    toolkit = by_title["Ancillary Justice: A Policy Toolkit"]
    assert toolkit.year == 2020
    assert toolkit.cited_by is None, "uncaptured is not zero, and never a year"

    appam = by_title["Xenogenesis, Sequencing and Timing of Home Energy "
                     "Technology Co-Adoption"]
    assert appam.year == 2023
    assert appam.cited_by is None
    assert appam.venue == "2023 APPAM Fall Research Conference"


def test_the_column_headers_are_not_works():
    """`CITED BY` came through as a publication with no year."""
    titles = [e.title for e in scholar.parse(REAL_PASTE).entries]
    assert len(titles) == 6, "six works, not six works and a header row"
    for heading in ("TITLE", "CITED BY", "YEAR"):
        assert heading not in titles


def test_a_journal_name_does_not_become_a_title():
    """The old parser broke entries on line length, so a long venue started a
    new work and the real title was swallowed."""
    titles = [e.title for e in scholar.parse(REAL_PASTE).entries]
    assert not any(t.startswith("Journal of Artificial Societies")
                   for t in titles)
    assert not any(t.startswith("OE Butler,") for t in titles), (
        "and an authors line is not a title either")


def test_flattened_column_gaps_still_yield_the_count():
    """Some clipboards collapse the tab between cells to a single space."""
    flattened = (
        "    The Dispossessed: an ambiguous utopia\n"
        "N Jemisin, OE Butler, J Russ\n"
        "Renewable Energy 12, 101-120 314 2016\n"
        "    An uncited conference paper\n"
        "OE Butler\n"
        "2023 APPAM Fall Research Conference 2023\n"
    )
    a, b = scholar.parse(flattened).entries
    assert (a.cited_by, a.year) == (314, 2016)
    assert (b.cited_by, b.year) == (None, 2023)
