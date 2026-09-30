"""The weekly cycle, what it prunes, and refusing to touch the network — M8.

Two properties carry the milestone. Retention must never reach a ruling: a
refresh may replace every number and must replace no decision, which is the
reason `WorkDecision` is not snapshot-scoped in the first place. And offline
must be a *mode*, not a best effort — one adapter left unguarded is not a mode
at all, so the test asserts no socket is opened rather than asserting each
adapter behaves.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import select

from app import refresh as refresh_mod
from app import settings
from app.config import Config
from app.models import (LedgerRuling, MapAxis, MapCluster, MapPoint, MapSpace,
                        Snapshot, Work, WorkClassification, WorkDecision)


def _snapshots(session, n, current_last=True):
    made = []
    for i in range(n):
        snap = Snapshot(created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
                        + timedelta(days=i), label="weekly")
        session.add(snap)
        session.commit()
        session.refresh(snap)
        session.add(Work(snapshot_id=snap.id, fingerprint=f"doi:{i}",
                         title=f"work {i}"))
        made.append(snap)
    if current_last and made:
        made[-1].is_current = True
        session.add(made[-1])
    session.commit()
    return made


def _spaces(session, n):
    made = []
    for i in range(n):
        space = MapSpace(corpus_hash=f"h{i}", row_count=1, dimensions=2)
        session.add(space)
        session.commit()
        session.refresh(space)
        session.add(MapPoint(space_id=space.id, kind="corpus", ref=f"r{i}",
                             row=0))
        session.add(MapCluster(space_id=space.id, cluster=0, lineage=f"l{i}"))
        session.add(MapAxis(space_id=space.id, component=1))
        made.append(space)
    made[-1].is_current = True
    session.add(made[-1])
    session.commit()
    return made


# --- retention --------------------------------------------------------------


def test_retention_keeps_the_last_twelve(session, tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    _snapshots(session, 20)
    pruned = refresh_mod.prune(Config(), session, keep=12)

    assert pruned["snapshots"] == 8
    left = session.exec(select(Snapshot)).all()
    assert len(left) == 12
    assert len(session.exec(select(Work)).all()) == 12, (
        "a snapshot's works go with it — they are derived state")


def test_retention_never_reaches_a_ruling(session, tmp_path, monkeypatch):
    """The reason `WorkDecision` is not snapshot-scoped. A refresh may replace
    every number and must replace no decision, and a tidy-up in September
    losing a ruling you made in March would be the same bug wearing a
    housekeeping hat."""
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    _snapshots(session, 20)
    session.add(WorkDecision(fingerprint="doi:0",
                             ruling=LedgerRuling.excluded,
                             rationale="not mine"))
    session.add(WorkClassification(fingerprint="doi:1", category="preprint"))
    session.commit()

    refresh_mod.prune(Config(), session, keep=12)

    rulings = session.exec(select(WorkDecision)).all()
    assert len(rulings) == 1 and rulings[0].fingerprint == "doi:0"
    assert len(session.exec(select(WorkClassification)).all()) == 1


def test_retention_never_drops_below_two_spaces(session, tmp_path, monkeypatch):
    """Lineage matching reads the previous space to decide whether a region is
    the same region. Leave one and every refit reads as wholesale upheaval."""
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    _spaces(session, 6)
    refresh_mod.prune(Config(), session, keep=1)
    assert len(session.exec(select(MapSpace)).all()) == 2


def test_pruning_a_space_takes_its_rows_and_its_array(session, tmp_path,
                                                      monkeypatch):
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    spaces = _spaces(session, 5)
    vectors = tmp_path / "spaces"
    vectors.mkdir()
    doomed = vectors / f"space_{spaces[0].id}.npz"
    doomed.write_bytes(b"not really an array")

    refresh_mod.prune(Config(), session, keep=2)

    assert len(session.exec(select(MapSpace)).all()) == 2
    assert not session.exec(
        select(MapPoint).where(MapPoint.space_id == spaces[0].id)).all()
    assert not doomed.exists(), "the array beside the database goes too"


def test_the_current_snapshot_survives_any_retention(session, tmp_path,
                                                     monkeypatch):
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    made = _snapshots(session, 5)
    refresh_mod.prune(Config(), session, keep=2)
    assert session.get(Snapshot, made[-1].id) is not None


# --- when a cycle is owed ---------------------------------------------------


def test_a_cycle_is_owed_when_there_is_nothing_at_all(session):
    assert refresh_mod.is_due(session) is True
    assert refresh_mod.days_since_last(session) is None


def test_a_fresh_snapshot_owes_nothing(session):
    _snapshots(session, 1)
    session.exec(select(Snapshot)).first().created_at = datetime.now(timezone.utc)
    session.commit()
    assert refresh_mod.is_due(session) is False


def test_an_old_snapshot_is_due(session):
    _snapshots(session, 1)
    snap = session.exec(select(Snapshot)).first()
    snap.created_at = datetime.now(timezone.utc) - timedelta(days=9)
    session.add(snap)
    session.commit()
    assert refresh_mod.is_due(session) is True
    assert refresh_mod.days_since_last(session) > 8


def test_zero_days_leaves_refreshing_entirely_to_you(session, monkeypatch):
    monkeypatch.setitem(settings._overrides, "REFRESH_DAYS", "0")
    assert refresh_mod.is_due(session) is False


# --- the cycle --------------------------------------------------------------


def test_a_failing_step_costs_its_panel_not_the_snapshot(client, monkeypatch):
    """`DESIGN.md`'s whole stance: a frontier that could not be gathered
    should cost you the frontier panel, not the refresh."""
    def boom(*a, **kw):
        raise RuntimeError("the tailnet is down")

    monkeypatch.setattr(refresh_mod, "_frontier", boom)
    body = client.post("/api/refresh").json()

    assert body["ok"] is False
    assert body["failed"] == ["frontier"]
    steps = {s["step"]: s for s in body["steps"]}
    assert steps["ledger"]["ok"] is True, "the ledger still ran"
    assert "the tailnet is down" in steps["frontier"]["error"]


def test_the_cycle_reports_every_step_and_what_it_pruned(client):
    body = client.post("/api/refresh?fit=false&gather=false").json()
    assert [s["step"] for s in body["steps"]] == ["sources", "ledger"]
    assert "snapshots" in body["pruned"] and "spaces" in body["pruned"]
    assert body["trigger"] == "manual"


def test_the_refresh_status_says_whether_one_is_owed(client):
    body = client.get("/api/refresh").json()
    assert body["enabled"] is True
    assert body["due"] is True, "an empty volume owes a cycle"
    assert body["running"] is False


# --- offline ----------------------------------------------------------------


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setitem(settings._overrides, "OFFLINE", "on")


def test_the_toggle_reads_the_way_a_person_writes_it(monkeypatch):
    for on in ("on", "ON", "true", "yes", "1"):
        monkeypatch.setitem(settings._overrides, "OFFLINE", on)
        assert settings.flag("OFFLINE") is True
    for off in ("off", "false", "no", "0", ""):
        monkeypatch.setitem(settings._overrides, "OFFLINE", off)
        assert settings.flag("OFFLINE") is False


def test_offline_opens_no_socket_at_all(client, offline, monkeypatch):
    """A mode, not a best effort. Asserted by making any client raise rather
    than by trusting each adapter, because one adapter left unguarded is not
    a mode."""
    import httpx

    def forbidden(*a, **kw):
        raise AssertionError("offline, and something opened a socket")

    monkeypatch.setattr(httpx, "Client", forbidden)

    body = client.post("/api/sources/refresh").json()
    assert body["offline"] is True
    assert body["refreshed"] is False, "a refresh was asked for and downgraded"
    # Every network source still reports, from cache, with its age.
    keys = {s["key"] for s in body["sources"]}
    assert {"trundlr", "openalex", "s2", "website"} <= keys

    client.post("/api/refresh?fit=false&gather=false")
    client.get("/api/status")
    client.get("/api/planning")


def test_offline_refuses_to_gather_rather_than_failing_slowly(client, offline):
    r = client.post("/api/frontier/refresh")
    assert r.status_code == 503
    assert "offline" in r.json()["detail"].lower()
    assert "Settings" in r.json()["detail"], "and says how to turn it off"


def test_the_cv_page_says_it_was_not_attempted(client, offline):
    body = client.post("/api/sources/refresh").json()
    website = next(s for s in body["sources"] if s["key"] == "website")
    assert "offline" in (website["note"] or "")


# --- the scheduler ----------------------------------------------------------


@pytest.mark.anyio
async def test_the_scheduler_never_fetches_at_startup(engine, monkeypatch):
    """A cold container has an empty volume and every source to fetch. Doing
    that automatically in the first second of a deploy is how a redeploy
    becomes an outage."""
    import asyncio

    from app.scheduler import Scheduler

    ran = []
    monkeypatch.setattr("app.scheduler.STARTUP_GRACE_SECONDS", 0.05)
    monkeypatch.setattr("app.scheduler.TICK_SECONDS", 0.05)

    sched = Scheduler(engine)
    monkeypatch.setattr(sched, "_run", lambda: ran.append(1) or {"ok": True})

    sched.start()
    await asyncio.sleep(0)          # the task starts, and sleeps the grace
    assert ran == [], "nothing is fetched before the grace period is out"
    await asyncio.sleep(0.2)        # past the grace, and a cycle is owed
    await sched.stop()
    assert ran, "and then it ticks"


@pytest.mark.anyio
async def test_the_scheduler_leaves_a_fresh_snapshot_alone(engine, session,
                                                           monkeypatch):
    import asyncio

    from app.scheduler import Scheduler

    _snapshots(session, 1)
    snap = session.exec(select(Snapshot)).first()
    snap.created_at = datetime.now(timezone.utc)
    session.add(snap)
    session.commit()

    ran = []
    monkeypatch.setattr("app.scheduler.STARTUP_GRACE_SECONDS", 0.01)
    monkeypatch.setattr("app.scheduler.TICK_SECONDS", 0.01)
    sched = Scheduler(engine)
    monkeypatch.setattr(sched, "_run", lambda: ran.append(1) or {})

    sched.start()
    await asyncio.sleep(0.1)
    await sched.stop()
    assert ran == [], "a week old is the bar, not a tick"


@pytest.mark.anyio
async def test_one_bad_week_does_not_kill_the_loop(engine, monkeypatch):
    """A scheduler that dies on a failure is worse than none, because nothing
    says it stopped."""
    import asyncio

    from app.scheduler import Scheduler

    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("the tailnet is down")

    monkeypatch.setattr("app.scheduler.STARTUP_GRACE_SECONDS", 0.01)
    monkeypatch.setattr("app.scheduler.TICK_SECONDS", 0.01)
    sched = Scheduler(engine)
    monkeypatch.setattr(sched, "_run", boom)

    sched.start()
    await asyncio.sleep(0.12)
    await sched.stop()
    assert len(calls) > 1, "it tried again the following tick"


def test_the_scheduler_reports_what_it_is_waiting_for(engine, session):
    from app.scheduler import Scheduler

    status = Scheduler(engine).status()
    assert status["enabled"] is True
    assert status["every_days"] == 7
    assert status["due"] is True
    assert status["snapshot_age_days"] is None
    assert status["last_run"] is None
    assert "newest snapshot" in status["note"]
