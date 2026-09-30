"""Zotero reader: what it includes, what it excludes, and how it degrades."""
import sqlite3

import pytest

from app.config import Config
from app.models import SourceStatus
from app.sources import zotero
from tests.zotero_fixture import build


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    db = build(tmp_path / "zotero.sqlite")
    monkeypatch.setenv("ZOTERO_SQLITE", str(db))
    return Config()


def test_missing_library_is_missing_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOTERO_SQLITE", str(tmp_path / "nope.sqlite"))
    report = zotero.read(Config())
    assert report.status is SourceStatus.missing
    assert not report.failures
    assert "not mounted" in report.note


def test_reads_the_personal_library_only(cfg):
    lib = zotero.read(cfg).data
    keys = {i.key for i in lib.items}
    assert "AAAA1111" in keys
    assert "DDDD4444" not in keys, "group-library items must not be merged in"
    assert lib.group_libraries == {2: 1}


def test_trashed_items_are_excluded_everywhere(cfg):
    report = zotero.read(cfg)
    lib = report.data
    assert "CCCC3333" not in {i.key for i in lib.items}
    parable_sower = next(c for c in lib.collections if c.name == "parableSower")
    assert "CCCC3333" not in parable_sower.item_keys
    assert parable_sower.size == 2


def test_child_items_are_not_bibliographic_items(cfg):
    lib = zotero.read(cfg).data
    assert {i.item_type for i in lib.items} == {"journalArticle", "book"}


def test_fields_creators_tags_and_collections_are_joined(cfg):
    lib = zotero.read(cfg).data
    item = lib.by_key()["AAAA1111"]
    assert item.title.startswith("Kindred effects")
    assert item.doi == "10.1016/j.fict.2015.01.001", "DOI is lowercased for matching"
    assert item.year == 2015
    assert item.fields["publicationTitle"] == "Renewable Energy"
    assert item.creators == ["Octavia Butler", "A. Coauthor"]
    assert item.tags == ["solar"]
    assert "COLL0001" in item.collections


def test_annotations_reach_the_bibliographic_parent(cfg):
    """Annotations hang off the attachment, so the rollup needs two hops."""
    lib = zotero.read(cfg).data
    item = lib.by_key()["AAAA1111"]
    assert item.attachment_count == 1
    assert item.pdf_count == 1
    assert item.annotation_count == 2


def test_thin_text_is_counted_not_hidden(cfg):
    report = zotero.read(cfg)
    lib = report.data
    assert lib.by_key()["BBBB2222"].thin is True
    assert lib.by_key()["AAAA1111"].thin is False
    assert report.counts["thin_text"] == 2   # the title-only article and the book


def test_report_counts_match_the_library(cfg):
    report = zotero.read(cfg)
    assert report.status is SourceStatus.ok
    assert report.count == 3   # two articles and a book
    assert report.counts["with_abstract"] == 1
    assert report.counts["with_doi"] == 1
    assert report.counts["collections"] == 2
    assert report.counts["annotations"] == 2
    # Nothing is marked in the fixture, so nothing is read — evidence is
    # counted separately, as a shortlist of what to mark.
    assert report.counts["read"] == 0
    assert report.counts["annotated"] == 1 and report.counts["noted"] == 1
    assert report.counts["collected_only"] == 3
    assert report.elapsed_ms is not None


def test_free_text_dates_that_have_no_year(cfg, tmp_path):
    item = zotero.ZoteroItem(item_id=1, key="K", library_id=1, item_type="journalArticle")
    for raw, expected in [("2015-03", 2015), ("March 2015", 2015), ("2015/04/01", 2015),
                          ("in press", None), ("", None), ("n.d.", None), ("12345", None)]:
        item.fields["date"] = raw
        assert item.year == expected, raw


def test_a_corrupt_database_reports_rather_than_raises(tmp_path, monkeypatch):
    bad = tmp_path / "zotero.sqlite"
    bad.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)   # header, then garbage
    monkeypatch.setenv("ZOTERO_SQLITE", str(bad))
    report = zotero.read(Config())
    assert report.status is SourceStatus.error
    assert report.failures
    assert report.data is None


def test_evidence_alone_does_not_make_an_item_read(cfg):
    """An earlier version graded four states from traces. The traces are too
    thin to carry it: lastRead begins in May 2026 and reading in Preview or on
    paper leaves nothing, so a count built from them is wrong in a new
    direction rather than right. Only a deliberate mark counts."""
    lib = zotero.read(cfg).data
    by_key = lib.by_key()
    annotated = by_key["AAAA1111"]
    assert annotated.annotation_count == 2 and annotated.last_read is not None
    assert annotated.reading == "collected", "evidence is not a claim"
    assert annotated.read is False
    assert by_key["EEEE5555"].note_count == 1
    assert by_key["BBBB2222"].reading == "collected"


def test_a_read_tag_marks_an_item_as_read(cfg, session, monkeypatch):
    """The only signal that can be trusted, because it is the only one you set
    deliberately. Everything else is inference from traces that begin in 2026."""
    from app import settings as settings_mod
    settings_mod._overrides.clear()
    monkeypatch.setenv("READ_TAG", "read")
    lib = zotero.read(cfg).data
    assert lib.by_key()["BBBB2222"].reading == "collected"

    # The fixture tags item 1 'solar'; rename the wanted tag to match it.
    settings_mod._overrides["READ_TAG"] = "solar"
    lib = zotero.read(cfg).data
    assert lib.by_key()["AAAA1111"].reading == "marked", (
        "the tag is what makes it read")
    settings_mod._overrides.clear()


def test_a_read_collection_marks_its_members(cfg, session):
    """Easier for a backfill: select two hundred items and drag once."""
    from app import settings as settings_mod
    settings_mod._overrides.clear()
    settings_mod._overrides["READ_COLLECTION"] = "parablesower"
    lib = zotero.read(cfg).data
    # The fixture puts AAAA1111 and BBBB2222 in the parableSower collection.
    assert lib.by_key()["BBBB2222"].reading == "marked"
    assert lib.by_key()["EEEE5555"].reading == "collected", "not a member"
    settings_mod._overrides.clear()


def test_the_read_collection_is_kept_out_of_the_project_join(client, monkeypatch):
    """It is not a research project, and reporting it as "reading with no
    project behind it" is noise in the panel that finds exactly that."""
    from app import settings as settings_mod
    settings_mod._overrides.clear()
    settings_mod._overrides["READ_COLLECTION"] = "read"
    body = client.get("/api/sources/join").json()
    names = {m.get("zotero_collection") for m in body.get("matches", [])}
    assert "read" not in names
    settings_mod._overrides.clear()
