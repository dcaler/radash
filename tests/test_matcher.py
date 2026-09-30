"""The join key. Pure logic, so the cases can be stated directly."""
from dataclasses import dataclass
from typing import Optional

from app.sources.matcher import Match, match, normalize, stem


@dataclass
class Col:
    name: str
    key: str = "K"
    size: int = 0


@dataclass
class Proj:
    id: int
    name: str
    priority: Optional[int] = None
    folder: Optional[str] = None


@dataclass
class Haarpi:
    name: str
    folder_name: str = ""
    folder: Optional[str] = None
    trundlr_project_id: Optional[int] = None


def test_normalize_and_stem():
    assert normalize("Schelling-Chords") == "schellingchords"
    assert stem("lathe_dev") == "lathe"
    assert stem("DigiPros_old") == "digipros"
    assert stem("codePixie_run") == "codepixie"
    assert stem("_dev") == "dev", "a bare suffix is a name, not a suffix"


def test_declared_id_beats_name_similarity():
    m = match(
        collections=[Col("wildSeed", size=40)],
        projects=[Proj(23, "wildSeed", 4)],
        haarpi_projects=[Haarpi("wildSeed", "260714_wildSeed",
                                "/p/260714_wildSeed", 23)],
    )
    assert len(m.matches) == 1
    only = m.matches[0]
    assert only.basis == "declared"
    assert only.trundlr_project_id == 23
    assert only.zotero_collection == "wildSeed"
    assert only.complete is True


def test_role_suffixes_join_to_one_project():
    m = match(
        collections=[Col("lathe", size=125)],
        projects=[Proj(13, "lathe", 2), Proj(17, "lathe_dev", 4)],
        haarpi_projects=[],
    )
    assert len(m.matches) == 1, "lathe and lathe_dev are one body of work"
    assert m.matches[0].zotero_collection == "lathe"


def test_a_second_collection_is_noted_not_dropped():
    m = match(
        collections=[Col("digipros", size=273), Col("DigiPros_old", size=141)],
        projects=[Proj(4, "DigiPros", 1)],
        haarpi_projects=[],
    )
    assert len(m.matches) == 1
    only = m.matches[0]
    assert only.item_count == 414, "both collections' items count toward the project"
    assert any("second collection" in n for n in only.notes)


def test_unmatched_are_reported_on_both_sides():
    m = match(
        collections=[Col("ToRead"), Col("parableSower")],
        projects=[Proj(20, "parableSower", 1), Proj(27, "Kindred", 2)],
        haarpi_projects=[],
    )
    assert m.unmatched_zotero == ["ToRead"]
    assert m.unmatched_trundlr == ["Kindred"]


def test_a_declared_id_that_trundlr_does_not_have_is_drift_not_a_match():
    m = match(
        collections=[],
        projects=[Proj(1, "other")],
        haarpi_projects=[Haarpi("ghost", "260101_ghost", "/p/260101_ghost", 999)],
    )
    ghost = next(x for x in m.matches if x.slug == "ghost")
    assert ghost.trundlr_project_id is None
    assert ghost.basis == "stem"
    assert any("999" in n for n in ghost.notes)


def test_summary_counts_by_basis():
    m = match(
        collections=[Col("alpha")],
        projects=[Proj(1, "alpha"), Proj(2, "beta_dev")],
        haarpi_projects=[Haarpi("alpha", "260101_alpha", "/p/a", 1)],
    )
    s = m.summary()
    assert s["matched"] == 2
    assert s["by_basis"]["declared"] == 1
