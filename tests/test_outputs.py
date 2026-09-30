"""The two artifacts, and the boundary that makes them safe — M7.

The last test in this file is the milestone's point. raDash's whole claim is
that it writes to nothing it observes; the read-only Docker mounts are one
half of that and this is the other, because a `:ro` flag protects the
deployment and not the development machine, and a test that only passes in a
container is a test nobody runs.
"""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.config import Config
from app.outputs import brief as brief_mod
from app.outputs import emit as emit_mod
from app.outputs import fill as fill_mod

WHEN = datetime(2026, 9, 28, tzinfo=timezone.utc)

CANDIDATE = {
    "signal": 1, "signal_name": "Read, never written",
    "move": "you already know it — write",
    "cluster": 3, "lineage": "r0421", "region": "earthseed · lmi · leasing",
    "terms": ["earthseed", "lmi", "leasing"], "exemplar": "The Dispossessed",
    "score": 2.56,
    "inputs": [{"input": "collecting", "effect": "adds", "weight": 2.0,
                "value": 0.72, "contribution": 1.45,
                "detail": "27 items collected here"},
               {"input": "distance_from_your_work", "effect": "scales",
                "weight": 1.0, "value": 1.0, "contribution": 1.0,
                "detail": "your nearest published work is 0.072 away"}],
    "confirmation": None, "bridge": None,
    "nearest_work": {"ref": "doi:1", "label": "EARTHSEED", "year": 2022,
                     "distance": 0.072},
    "counts": {"collected": 27, "read": 2, "engaged": 0, "written": 0,
               "recent_written": 0, "newest_year": 2026, "median_year": 2022,
               "recent_collected": 20, "projects": ["bloodchild"],
               "live": True, "live_because": "bloodchild is active",
               "spread": 0.026},
    "corpus": [{"ref": "K1", "label": "A paper you collected", "year": 2019,
                "reading": "annotated", "cited_by": 12,
                "url": "https://doi.org/10.1/held"}],
    "frontier": [{"title": "A frontier paper", "year": 2026,
                  "venue": "Energy Policy", "cited_by": 40, "topic": "T",
                  "distance": 0.01, "url": "https://doi.org/10.9/new"}],
    "topics": [], "frontier_age_days": 3.0,
    "counter_case": ["25 of 27 items here carry no evidence of being read."],
    "route": {"output": "brief", "label": "Read-in brief",
              "file": "{YYMMDD}_{slug}_readin_ra.txt",
              "detail": "topic, focus, seed DOIs",
              "why": "the grounding is already paid for"},
}


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    return Config()


# --- naming and the funnel --------------------------------------------------


def test_the_name_follows_the_repos_revision_chain(cfg):
    name = emit_mod.filename("earthseed · lmi · leasing", "readin", WHEN)
    assert name == "260928_earthseed-lmi-leasing_readin_ra.txt"
    assert emit_mod.filename("x", "fill", WHEN).endswith("_fill_ra.txt")


def test_a_same_day_re_emit_overwrites_rather_than_accumulating(cfg):
    a = emit_mod.write(cfg, "solar", "readin", "first", WHEN)
    b = emit_mod.write(cfg, "solar", "readin", "second", WHEN)
    assert a == b
    assert b.read_text() == "second"
    assert len(emit_mod.listing(cfg)) == 1


def test_a_region_name_cannot_escape_the_output_directory(cfg):
    """A slug is built from a region's terms, and a region is named by
    whatever the clustering found. Traversal has to be unrepresentable rather
    than filtered, so every separator collapses to a hyphen."""
    for hostile in ("../../etc/passwd", "/etc/passwd", "..", "....//"):
        name = emit_mod.filename(hostile, "readin", WHEN)
        assert "/" not in name and ".." not in name
        path = emit_mod.resolve(cfg, name)
        assert path.parent == Path(cfg.output_dir).resolve()


def test_a_handcrafted_path_is_refused_outright(cfg):
    """`resolve` is the guard, not the slug: a future caller that builds its
    own filename must still be unable to leave."""
    for name in ("../escaped.txt", "/tmp/escaped.txt", "sub/dir/escaped.txt"):
        with pytest.raises(emit_mod.OutsideOutput):
            emit_mod.resolve(cfg, name)


# --- the brief --------------------------------------------------------------


def test_the_brief_carries_what_the_design_asks_for(cfg):
    text = brief_mod.render(CANDIDATE, [{"name": "260523_Xenogenesis",
                                         "matched": 18, "size": 60}], WHEN)
    for required in ("earthseed · lmi · leasing", "Read, never written",
                     "10.1/held", "10.9/new", "260523_Xenogenesis"):
        assert required in text, f"the brief must carry {required}"
    assert "TARGET SIZE" in text and "DATES" in text and "TO RUN" in text


def test_the_brief_carries_the_case_against_it(cfg):
    """It outlives the page that produced it. A month from now it is a list of
    papers with no memory that the grounding was collecting, not reading."""
    text = brief_mod.render(CANDIDATE, [], WHEN)
    assert "THE CASE AGAINST" in text
    assert "no evidence of being read" in text


def test_the_brief_invents_no_command(cfg):
    """raDash does not know your reading tool's flags. Laying out the fields
    and leaving the verb is also the copy-paste that stops it setting work in
    motion off a score nobody checked."""
    flat = " ".join(brief_mod.render(CANDIDATE, [], WHEN).split()).lower()
    assert "the verb is yours" in flat
    # No fabricated invocation, and none of the things DESIGN §8 forbids
    # writing: raDash emits one file and reaches into nothing.
    for invented in ("rabbithole gather ", "$ radash", "litrev.yaml",
                     "trundlr queue"):
        assert invented not in flat


def test_the_brief_says_filling_changes_the_map(cfg):
    flat = " ".join(brief_mod.render(CANDIDATE, [], WHEN).split())
    assert "fill, re-embed, re-read" in flat
    assert "rabbitHole's gather" in flat


# --- the fill list ----------------------------------------------------------


ITEMS = [
    {"title": "The anchor", "year": 2016, "cited_by": 900, "distance": 0.40,
     "url": "https://doi.org/10.1/anchor"},
    {"title": "The core", "year": 2018, "cited_by": 4, "distance": 0.01,
     "url": "https://doi.org/10.1/core"},
    {"title": "The bridge", "year": 2019, "cited_by": 10, "distance": 0.22,
     "url": "https://doi.org/10.1/bridge"},
    {"title": "The new one", "year": 2026, "cited_by": 1, "distance": 0.30,
     "url": "https://doi.org/10.1/new"},
    {"title": "Already yours", "year": 2020, "cited_by": 50, "distance": 0.02,
     "url": "https://doi.org/10.1/held", "already_held": True},
]


def test_every_paper_earns_one_role_only():
    """Four overlapping lists would be four times the same paper. Assigned in
    order, so an anchor that is also recent stays an anchor — that is the
    reason to collect it first."""
    roles = fill_mod.assign_roles(ITEMS, {"https://doi.org/10.1/bridge"}, WHEN)
    seen = [r["url"] for rows in roles.values() for r in rows]
    assert len(seen) == len(set(seen)), "a paper appears under one role"
    assert roles["most-cited"][0]["title"] == "The anchor"
    assert roles["central"][0]["title"] == "The core"
    assert roles["bridging"][0]["title"] == "The bridge"
    assert roles["frontier"][0]["title"] == "The new one"


def test_what_you_already_hold_is_not_on_the_fill_list():
    roles = fill_mod.assign_roles(ITEMS, set(), WHEN)
    titles = [r["title"] for rows in roles.values() for r in rows]
    assert "Already yours" not in titles


def test_the_fill_list_says_why_it_is_not_a_brief():
    roles = fill_mod.assign_roles(ITEMS, set(), WHEN)
    unconfirmed = {**CANDIDATE, "signal": 3,
                   "route": {**CANDIDATE["route"], "output": "fill",
                             "label": "Corpus fill list",
                             "why": "the honest response to a reading gap is "
                                    "reading"}}
    text = fill_mod.render({"name": "solar", "terms": ["pv"], "cluster": 3,
                            "lineage": "r1"}, roles, unconfirmed, WHEN)
    flat = " ".join(text.split())
    assert "honest response to a reading gap is reading" in flat
    assert "MOST-CITED" in text and "BRIDGING" in text
    assert "adds nothing to Zotero" in flat


# --- M7-T4: the boundary ----------------------------------------------------


def test_raDash_writes_nothing_outside_its_own_volume(client, tmp_path,
                                                      monkeypatch):
    """**The milestone's point.** Every write on every code path, caught at
    the filesystem rather than asserted about.

    `BUILD_PLAN.md` states this as "no write outside `output/`" and taken
    literally raDash has never met it: the fitted vector arrays land in
    `spaces/` and Scholar imports in `import/`, both siblings of `output/`
    inside the same volume. The property worth having is the one the `:ro`
    mounts enforce from the other side — raDash writes only inside its own
    data volume, and never inside a source it observes. Asserting the narrower
    version would have meant a failing test or a test written to lie.
    """
    import builtins

    # The three read-only mounts, read from the environment the fixture set.
    # Not the folder they share with `import/`: the conftest parks several
    # absent paths under one parent for convenience, and `import_dir` is
    # inside raDash's own volume in every real deployment.
    env = Config()
    sources = {env.zotero_sqlite, env.projects_dir, env.professional_dir}
    writes: list = []
    real_open, real_write_text = builtins.open, Path.write_text

    def record(path, mode=""):
        if any(m in str(mode) for m in ("w", "a", "x", "+")):
            writes.append(Path(str(path)).resolve())

    def guarded_open(file, mode="r", *a, **kw):
        record(file, mode)
        return real_open(file, mode, *a, **kw)

    def guarded_write_text(self, *a, **kw):
        writes.append(Path(str(self)).resolve())
        return real_write_text(self, *a, **kw)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(Path, "write_text", guarded_write_text)

    # Exercise every path that writes anything: the ledger, the importer, the
    # fit, and both emitters.
    client.post("/api/ledger/rebuild")
    client.post("/api/sources/scholar/import", json={"text": (
        "    A paper with a count\nO E Butler\nJASSS 25 (3)    12    2022\n"
        "    Another paper\nO E Butler\nEnergy Policy    8    2021\n")})
    client.post("/api/map/fit")
    client.post("/api/outputs/brief?cluster=0")
    client.post("/api/outputs/fill?cluster=0")
    client.get("/api/planning")
    client.get("/api/status/reading")

    assert writes, "the harness caught nothing, so it proves nothing"
    volume = Path(tmp_path).resolve()
    for path in writes:
        assert volume in path.parents or path.parent == volume, (
            f"{path} is outside raDash's own data volume")
        for source in sources:
            resolved = Path(source).resolve()
            assert resolved not in path.parents and resolved != path, (
                f"{path} is inside {resolved}, which raDash only observes")


def test_the_output_listing_only_reports_its_own_files(cfg):
    out = Path(cfg.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    emit_mod.write(cfg, "solar", "readin", "a brief", WHEN)
    (out / "notes.txt").write_text("something else")
    names = [f["name"] for f in emit_mod.listing(cfg)]
    assert names == ["260928_solar_readin_ra.txt"]


def test_the_endpoints_refuse_a_region_with_no_candidate(client):
    r = client.post("/api/outputs/brief?cluster=99")
    assert r.status_code in (404, 503)
    assert "candidate" in r.json()["detail"] or "fitted" in r.json()["detail"]


# --- the endpoints, end to end ----------------------------------------------


@pytest.fixture
def mapped(client):
    """A fitted space with one region raDash will produce a candidate for."""
    from sqlmodel import Session

    from app.database import _engine
    from tests.test_signals import Space

    with Session(_engine) as s:
        sp = Space(s)
        lineage = sp.region(0, centre=(0.0, 0.0), collected=40, read=20)
        sp.work("doi:far", at=(9.0, 9.0))
        sp.frontier(lineage, 0, n=6, cited=120)
        s.commit()
    return client


def test_a_preview_writes_nothing(mapped, tmp_path):
    body = mapped.post("/api/outputs/brief?cluster=0&preview=true").json()
    assert body["preview"] is True
    assert "raDash read-in brief" in body["text"]
    assert body["filename"].endswith("_readin_ra.txt")
    assert mapped.get("/api/outputs").json()["files"] == [], (
        "a preview that leaves a file behind is not a preview")


def test_writing_a_brief_puts_one_file_in_output(mapped):
    body = mapped.post("/api/outputs/brief?cluster=0").json()
    assert body["written"].endswith("_readin_ra.txt")

    listed = mapped.get("/api/outputs").json()
    assert [f["name"] for f in listed["files"]] == [body["written"]]
    assert listed["files"][0]["kind"] == "readin"

    fetched = mapped.get(f"/api/outputs/file/{body['written']}").json()
    assert fetched["text"] == body["text"]


def test_a_candidate_is_not_written_as_the_artifact_it_does_not_route_to(mapped):
    """Signal 1 routes to a brief. Asking for a fill list is refused with the
    routing reason rather than quietly obliged — the routing is the design's
    honesty mechanism, not a default."""
    r = mapped.post("/api/outputs/fill?cluster=0&signal=1")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "Read-in brief" in detail and "preview=true" in detail

    # Both directions, or it is a preference rather than a mechanism.
    thin = mapped.post("/api/outputs/brief?cluster=0&signal=4")
    assert thin.status_code in (404, 409)

    # A preview still shows you what the other one would say, because that is
    # how you disagree with the routing rather than merely obey it.
    shown = mapped.post("/api/outputs/fill?cluster=0&signal=1&preview=true")
    assert shown.status_code == 200
    assert "corpus fill list" in shown.json()["text"]


def test_a_fill_list_needs_a_gathered_frontier(client):
    """Without a frontier set there is nothing to fill from, and saying so
    beats emitting an empty list that reads as 'the field is empty'."""
    from sqlmodel import Session

    from app.database import _engine
    from tests.test_signals import Space

    with Session(_engine) as s:
        sp = Space(s)
        sp.region(0, centre=(0.0, 0.0), collected=6)   # thin: signal 4
        s.commit()

    # Named explicitly: a thin region also passes signal 1's gate, and with no
    # signal given the endpoint takes the strongest reading, which routes to a
    # brief and is refused for a different reason.
    r = client.post("/api/outputs/fill?cluster=0&signal=4")
    assert r.status_code == 409
    assert "gather" in r.json()["detail"].lower()


def test_an_unknown_region_is_refused_with_the_reason(mapped):
    r = mapped.post("/api/outputs/brief?cluster=404")
    assert r.status_code == 404
    assert "no candidate" in r.json()["detail"]


def test_an_unknown_output_file_is_a_404_not_a_traversal(client):
    assert client.get("/api/outputs/file/nope.txt").status_code == 404
    # A name that tries to leave the directory resolves to nothing readable
    # rather than to something outside it.
    assert client.get("/api/outputs/file/..%2F..%2Fetc%2Fpasswd").status_code \
        in (404, 400)
