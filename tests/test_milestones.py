"""Milestones: HAARPi's status.json is read as written, and its faults are named."""
import json
from pathlib import Path

from app.routers.milestones import _project
from app.sources import haarpi, milestones

MANIFEST = "name: lathe\nshort_title: lathe\ntrundlr_project_id: 13\ntrundlr_priority: 2\n"


def milestone(id_, state, track=None, **kw):
    return {"id": id_, "track": track, "state": state, "implied": False,
            "waiting_on": None, "released": None, "health": "no_target",
            "open_tasks": [], "blocked_by": [], "stale_inputs": [], **kw}


STATUS = {
    "schema": 1, "generated": "2026-10-26T18:43:45", "generated_by": "haarpi status",
    "project": {"name": "lathe", "short_title": "lathe", "trundlr_project_id": 13,
                "priority": 2},
    "trundlr": True,
    "milestones": [
        milestone("litreview", "done",
                  released={"file": "260801_lathe_litreview.docx", "date": "2026-08-01"}),
        milestone("design", "done", implied=True),
        milestone("paper.css2026.submission", "done", track="css2026",
                  released={"file": "lathe.pdf", "date": "2026-09-14"}),
        milestone("deck.longtalk", "in_flight", track="css2026", waiting_on="you",
                  target="2026-10-31", health="late"),
        milestone("experiments", "in_flight", waiting_on="tool",
                  released={"file": "260810_lathe_results.docx", "date": "2026-08-10"}),
    ],
    "tracks": [{"venue": "css2026", "outcome": "submitted",
                "milestones": ["paper.css2026.submission", "deck.longtalk"]}],
}


def project(tmp_path: Path, status=STATUS, raw=None) -> Path:
    root = tmp_path / "260601_lathe"
    (root / ".haarpi").mkdir(parents=True)
    (root / "haarpi.yaml").write_text(MANIFEST)
    if raw is not None:
        (root / ".haarpi" / "status.json").write_text(raw)
    elif status is not None:
        (root / ".haarpi" / "status.json").write_text(json.dumps(status))
    return root


def test_the_file_is_read_as_written(tmp_path):
    s = milestones.read(project(tmp_path))
    assert s.note is None
    assert [m["id"] for m in s.milestones][:2] == ["litreview", "design"]
    assert s.tracks[0]["outcome"] == "submitted"
    assert s.written_at is not None


def test_a_missing_file_says_how_to_get_one(tmp_path):
    s = milestones.read(project(tmp_path, status=None))
    assert s.payload is None and "any haarpi verb" in s.note


def test_an_unparseable_file_costs_only_that_project(tmp_path):
    s = milestones.read(project(tmp_path, raw="{not json"))
    assert s.payload is None and "unreadable" in s.note


def test_an_unknown_schema_is_read_and_flagged(tmp_path):
    s = milestones.read(project(tmp_path, status={**STATUS, "schema": 2}))
    assert s.milestones and "schema 2" in s.note


def test_the_summary_keeps_implied_apart_from_released(tmp_path):
    root = project(tmp_path)
    row = _project(haarpi._read_project(root), milestones.read(root),
                   {"id": 13, "priority": 1, "archived": False})
    assert row["done"] == 3          # litreview, design (implied), submission
    # Releases on disk: the implied one has none, and the reworked one keeps its.
    assert row["released"] == 3
    assert row["current"] == "deck.longtalk"
    assert row["health"] == "late"   # the worst milestone's
    assert row["priority"] == 1      # trundlr's live priority beats the file's copy
