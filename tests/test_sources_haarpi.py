"""Project folders: tolerant parsing, and drift reported rather than raised."""
import json
from pathlib import Path

import pytest

from app.config import Config
from app.models import SourceStatus
from app.sources import haarpi

MANIFEST = """
name: lathe
short_title: lathe
brief: A project about something.
trundlr_project_id: 13
trundlr_priority: 2
stages:
  litreview:
    dir: litReview
    tool: rabbithole
    attended: false
  paper:
    dir: paper
    tool: raconteur
    attended: true
"""


def make_project(root: Path, folder: str, manifest: str = MANIFEST,
                 stage_dirs=("litReview",), ledger=None) -> Path:
    d = root / folder
    d.mkdir(parents=True)
    if manifest is not None:
        (d / "haarpi.yaml").write_text(manifest)
    for s in stage_dirs:
        (d / s).mkdir()
        (d / s / "a.md").write_text("x")
    if ledger is not None:
        (d / ".haarpi").mkdir()
        (d / ".haarpi" / "corpus_ledger.json").write_text(json.dumps(ledger))
    return d


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJECTS_DIR", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    return Config()


def test_absent_directory_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJECTS_DIR", str(tmp_path / "nope"))
    assert haarpi.read(Config()).status is SourceStatus.missing


def test_reads_manifest_and_ledger(cfg, tmp_path):
    make_project(tmp_path / "projects", "260601_lathe",
                 ledger={"version": 1, "items": [{"key": "AAAA1111"}, {"key": "BBBB2222"}]})
    report = haarpi.read(cfg)
    p = report.data.projects[0]
    assert p.slug == "lathe"
    assert p.stamp == "260601"
    assert p.trundlr_project_id == 13
    assert p.ledger_keys == ["AAAA1111", "BBBB2222"]
    assert report.counts["ledger_items"] == 2
    assert report.counts["haarpi_projects"] == 1


def test_a_stage_not_yet_begun_is_a_position_not_a_fault(cfg, tmp_path):
    """Every project has stages it has not reached. Counting them as drift put
    twenty-one of them behind a `degraded` badge on the real library."""
    make_project(tmp_path / "projects", "260601_lathe", stage_dirs=("litReview",))
    report = haarpi.read(cfg)
    p = report.data.projects[0]
    assert p.has_manifest
    assert "paper" in p.pending, "the stage with no directory is pending"
    assert p.drift == [], "and it is not drift"
    assert report.status is SourceStatus.ok
    assert report.counts["stages_not_started"] == 1
    assert report.count == 1


def test_last_movement_comes_from_stage_contents(cfg, tmp_path):
    make_project(tmp_path / "projects", "260601_lathe")
    p = haarpi.read(cfg).data.projects[0]
    assert p.last_movement is not None
    assert p.days_since_movement < 1
    assert p.furthest_stage == "litreview"  # the yaml key, not the dir


def test_one_unparseable_manifest_does_not_cost_the_others(cfg, tmp_path):
    root = tmp_path / "projects"
    make_project(root, "260601_lathe")
    make_project(root, "260602_broken", manifest="name: [unclosed\n  bad: :")
    report = haarpi.read(cfg)
    assert report.count == 2
    broken = next(p for p in report.data.projects if p.slug == "broken")
    assert any("unparseable" in d for d in broken.drift)
    assert report.data.projects[0].trundlr_project_id == 13


def test_an_unknown_ledger_version_is_read_anyway_and_flagged(cfg, tmp_path):
    make_project(tmp_path / "projects", "260601_lathe",
                 ledger={"version": 99, "items": [{"key": "ZZZZ9999"}]})
    p = haarpi.read(cfg).data.projects[0]
    assert p.ledger_keys == ["ZZZZ9999"], "forward-compatible data is still read"
    assert any("99" in d for d in p.drift)


def test_skip_dirs_are_not_projects(cfg, tmp_path):
    root = tmp_path / "projects"
    make_project(root, "260601_lathe")
    (root / "@eaDir").mkdir()
    (root / "old").mkdir()
    (root / ".hidden").mkdir()
    assert haarpi.read(cfg).count == 1


def test_a_folder_without_a_manifest_is_not_a_haarpi_project(cfg, tmp_path):
    """It is somebody else's folder. Reporting it as a project with drift made
    a teaching directory and a convening into defects, and pushed the whole
    source to degraded."""
    root = tmp_path / "projects"
    make_project(root, "260601_lathe")
    make_project(root, "250330_Proj_Teaching", manifest=None, stage_dirs=())
    report = haarpi.read(cfg)
    assert report.count == 1, "one HAARPi project, not two directories"
    assert report.counts["other_folders"] == 1
    assert report.counts["directories_scanned"] == 2
    assert report.status is SourceStatus.ok
    assert "other folders ignored" in report.note
