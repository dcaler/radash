"""The four opportunity signals, and the candidates they assemble.

The tests that matter here are the ones about *refusal*: that signal 1 does
not fire where you have already published, that signal 3 is never confirmed by
a frontier nobody gathered, that a bridge is between two active regions rather
than beside them, and that no candidate reaches the page as a bare number.
Each of those is a way the page could quietly flatter you, and a score that
flatters you is worse than no score at all.
"""
import json
from datetime import datetime, timedelta, timezone

from app.models import (FrontierItem, MapCluster, MapPoint, MapPointKind,
                        MapSpace, RegionTopic)
from app.signals import assemble as assemble_mod
from app.signals import regions as regions_mod
from app.signals import signals as signals_mod

YEAR = datetime.now(timezone.utc).year


class Space:
    """A fitted space, built by hand.

    No vectors are written, so the signals fall back to the display
    coordinates — which is exactly the degraded path a deployed instance takes
    when the array beside the database cannot be read, and worth exercising
    rather than mocking around.
    """

    def __init__(self, session):
        self.session = session
        self.row = 0
        self.space = MapSpace(is_current=True, backend="lsa", corpus_hash="h",
                              row_count=0, dimensions=2)
        session.add(self.space)
        session.commit()
        session.refresh(self.space)

    def _point(self, kind, ref, label, x, y, **kw):
        p = MapPoint(space_id=self.space.id, kind=kind, ref=ref, label=label,
                     x=x, y=y, row=self.row, **kw)
        self.row += 1
        self.session.add(p)
        return p

    def region(self, cluster, centre=(0.0, 0.0), collected=10, read=0,
               year=None, terms=None, lineage=None, cited=3):
        lineage = lineage or f"r{cluster}"
        cx, cy = centre
        for i in range(collected):
            self._point(
                MapPointKind.corpus, f"c{cluster}-{i}", f"paper {cluster}-{i}",
                cx + (i % 5) * 0.01, cy + (i % 3) * 0.01,
                year=year if year is not None else YEAR,
                cluster=cluster, cited_by=cited,
                reading="annotated" if i < read else "collected",
                doi=f"10.1/{cluster}-{i}")
        self.session.add(MapCluster(
            space_id=self.space.id, cluster=cluster, lineage=lineage,
            terms=json.dumps(terms or [f"term{cluster}", "other"]),
            size=collected, exemplar_label=f"paper {cluster}-0",
            centroid_x=cx, centroid_y=cy))
        return lineage

    def work(self, ref, at, year=None, cluster=None):
        self._point(MapPointKind.work, ref, ref, at[0], at[1],
                    year=year or YEAR, cluster=cluster)

    def brief(self, ref, at, cluster=None):
        self._point(MapPointKind.project, ref, ref, at[0], at[1],
                    cluster=cluster)

    def frontier(self, lineage, cluster, n=6, year=None, cited=40,
                 off_target=False, age_days=1):
        fetched = datetime.now(timezone.utc) - timedelta(days=age_days)
        for i in range(n):
            self.session.add(FrontierItem(
                lineage=lineage, cluster=cluster, openalex_id=f"W{cluster}{i}",
                doi=f"10.9/{cluster}-{i}", title=f"frontier {cluster}-{i}",
                year=year if year is not None else YEAR, cited_by=cited,
                topic_id="T1", topic_name="A topic", fetched_at=fetched,
                distance_to_region=0.01, off_target=off_target))
        self.session.add(RegionTopic(
            lineage=lineage, cluster=cluster, topic_id="T1",
            topic_name="A topic", share=0.4, members=n))

    def done(self):
        self.session.commit()
        return regions_mod.gather(self.session)


def scored(session, sp):
    regions, context = sp.done()
    rows, unassessable = signals_mod.score_all(regions)
    limits = signals_mod.thresholds()
    scale = signals_mod.scale_of(regions)
    return (assemble_mod.candidates(rows, scale, limits), unassessable,
            context)


def only(candidates, signal):
    return [c for c in candidates if c["signal"] == signal]


# --- 1 · read, never written -----------------------------------------------


def test_signal_one_fires_where_you_have_read_and_not_written(session):
    sp = Space(session)
    sp.region(0, centre=(0.0, 0.0), collected=40, read=20)
    sp.work("doi:far", at=(5.0, 5.0))
    rows, _, _ = scored(session, sp)

    one = only(rows, 1)
    assert [c["cluster"] for c in one] == [0]
    assert one[0]["score"] > 0
    names = [i["input"] for i in one[0]["inputs"]]
    assert "collecting" in names and "reading_evidence" in names
    # The distance is a factor, not a term: DESIGN §4 states the ranking as
    # reading investment *times* distance from your nearest publication.
    factor = [i for i in one[0]["inputs"] if i["effect"] == "scales"]
    assert len(factor) == 1 and factor[0]["input"] == "distance_from_your_work"


def test_signal_one_does_not_fire_where_you_have_already_published(session):
    sp = Space(session)
    sp.region(0, centre=(0.0, 0.0), collected=40, read=20)
    sp.work("doi:here", at=(0.01, 0.01), cluster=0)
    rows, _, _ = scored(session, sp)
    assert only(rows, 1) == []


def test_a_region_next_to_your_work_outranks_nothing(session):
    """Distance scales the score, so a region your work sits beside scores
    below an identical region on the other side of the map."""
    sp = Space(session)
    sp.region(0, centre=(0.0, 0.0), collected=40, read=20)
    sp.region(1, centre=(5.0, 5.0), collected=40, read=20)
    sp.work("doi:x", at=(0.02, 0.02))     # in neither region, beside region 0
    rows, _, _ = scored(session, sp)

    by_cluster = {c["cluster"]: c["score"] for c in only(rows, 1)}
    assert by_cluster[1] > by_cluster[0]


# --- 2 · field-active, you're absent ----------------------------------------


def test_signal_two_scores_a_thin_region_the_field_is_busy_in(session):
    sp = Space(session)
    lineage = sp.region(0, centre=(0.0, 0.0), collected=6)
    sp.frontier(lineage, 0, n=8, cited=120)
    rows, unassessable, _ = scored(session, sp)

    two = only(rows, 2)
    assert [c["cluster"] for c in two] == [0]
    assert unassessable == []
    assert two[0]["frontier"], "the papers behind the score travel with it"


def test_a_region_the_frontier_was_never_asked_about_is_not_scored_zero(session):
    """The gatherer visits live regions first, so silence is very often a
    statement about the budget rather than about the field."""
    sp = Space(session)
    sp.region(0, centre=(0.0, 0.0), collected=6)
    rows, unassessable, _ = scored(session, sp)

    assert only(rows, 2) == []
    assert [u["cluster"] for u in unassessable] == [0]
    assert "never" in unassessable[0]["reason"] or "gathered" in unassessable[0]["reason"]


# --- 3 · thin literature, strong grounding ----------------------------------


def _quiet_region(sp, cluster=0, centre=(0.0, 0.0)):
    return sp.region(cluster, centre=centre, collected=40, read=25,
                     year=YEAR - 8)


def test_signal_three_is_confirmed_when_the_frontier_is_quiet_too(session):
    sp = Space(session)
    lineage = _quiet_region(sp)
    sp.frontier(lineage, 0, n=4, year=YEAR - 9)      # nothing recent
    rows, _, _ = scored(session, sp)

    three = only(rows, 3)
    assert len(three) == 1
    assert three[0]["confirmation"]["confirmed"] is True
    assert three[0]["route"]["output"] == "brief"


def test_an_unconfirmed_signal_three_routes_to_fill_not_brief(session):
    """The milestone's exit criterion, asserted. A hole the frontier can fill
    is a hole in your collecting, and the honest response is reading."""
    sp = Space(session)
    lineage = _quiet_region(sp)
    sp.frontier(lineage, 0, n=8, year=YEAR)          # the field is busy
    rows, _, _ = scored(session, sp)

    three = only(rows, 3)
    assert three[0]["confirmation"]["confirmed"] is False
    assert three[0]["route"]["output"] == "fill"
    assert "reading gap" in three[0]["confirmation"]["detail"]
    assert any("reading gap" in line for line in three[0]["counter_case"])


def test_a_region_nobody_gathered_is_never_confirmed(session):
    """Silence from a source that was never queried is not agreement."""
    sp = Space(session)
    _quiet_region(sp)
    rows, _, _ = scored(session, sp)

    three = only(rows, 3)
    assert three[0]["confirmation"]["confirmed"] is False
    assert three[0]["confirmation"]["basis"] == "unchecked"
    assert three[0]["route"]["output"] == "fill"


def test_signal_three_ignores_a_region_whose_literature_is_current(session):
    sp = Space(session)
    sp.region(0, centre=(0.0, 0.0), collected=40, read=25, year=YEAR)
    rows, _, _ = scored(session, sp)
    assert only(rows, 3) == []


# --- 4 · thin reading -------------------------------------------------------


def test_signal_four_applies_no_activity_filter_and_is_ranked_last(session):
    sp = Space(session)
    sp.region(0, centre=(0.0, 0.0), collected=6)
    sp.region(1, centre=(3.0, 3.0), collected=60, read=30)
    rows, _, _ = scored(session, sp)

    four = only(rows, 4)
    assert [c["cluster"] for c in four] == [0]
    assert rows[-1]["signal"] == 4, "signal 4 sorts behind the other three"
    assert four[0]["route"]["output"] == "fill"
    assert any("nobody" in line for line in four[0]["counter_case"])


# --- 5 · the bridge attribute ------------------------------------------------


def test_a_bridge_is_between_two_active_regions(session):
    sp = Space(session)
    sp.region(0, centre=(-1.0, 0.0), collected=12)
    sp.region(1, centre=(1.0, 0.0), collected=12)
    sp.region(2, centre=(0.0, 0.0), collected=12)     # between the two
    sp.work("doi:a", at=(-1.0, 0.0), cluster=0)
    sp.brief("parableSower", at=(1.0, 0.0), cluster=1)
    regions, _ = sp.done()

    by_cluster = {r.cluster: r for r in regions}
    assert by_cluster[2].bridge is not None
    assert {b["cluster"] for b in by_cluster[2].bridge["between"]} == {0, 1}


def test_a_region_beside_two_active_ones_is_not_a_bridge(session):
    """Near two things is not between them, and calling it a bridge would
    mark half the map."""
    sp = Space(session)
    sp.region(0, centre=(1.0, 0.0), collected=12)
    sp.region(1, centre=(2.0, 0.0), collected=12)
    sp.region(2, centre=(0.0, 0.0), collected=12)     # off to one side
    sp.work("doi:a", at=(1.0, 0.0), cluster=0)
    sp.work("doi:b", at=(2.0, 0.0), cluster=1)
    regions, _ = sp.done()

    assert {r.cluster: r for r in regions}[2].bridge is None


# --- 6 · assembly ------------------------------------------------------------


def test_no_candidate_is_a_bare_number(session):
    """DESIGN §7: signal type, score with the inputs broken out, the corpus
    items behind it, the nearest work, and the honest counter-case."""
    sp = Space(session)
    lineage = sp.region(0, centre=(0.0, 0.0), collected=40, read=6,
                        year=YEAR - 8)
    sp.region(1, centre=(4.0, 4.0), collected=6)
    sp.frontier(lineage, 0, n=5, year=YEAR)
    sp.work("doi:x", at=(9.0, 9.0))
    rows, _, _ = scored(session, sp)

    assert rows, "the fixture produces candidates at all"
    for c in rows:
        assert c["signal_name"] and c["move"]
        assert c["inputs"], "a score with no inputs is a bare number"
        assert all(i["detail"] for i in c["inputs"])
        assert c["counter_case"], "every candidate carries the case against it"
        assert c["route"]["output"] in ("brief", "fill")
        assert c["route"]["why"]


def test_the_counter_case_names_collecting_that_is_not_reading(session):
    sp = Space(session)
    sp.region(0, centre=(0.0, 0.0), collected=40, read=1)
    sp.work("doi:x", at=(9.0, 9.0))
    rows, _, _ = scored(session, sp)

    one = only(rows, 1)[0]
    assert any("no evidence of being read" in line
               for line in one["counter_case"])


def test_off_target_frontier_papers_are_counted_against_not_shown_as_evidence(session):
    sp = Space(session)
    lineage = sp.region(0, centre=(0.0, 0.0), collected=6)
    sp.frontier(lineage, 0, n=4, cited=100)
    sp.frontier(lineage, 0, n=3, cited=100, off_target=True)
    rows, _, _ = scored(session, sp)

    two = only(rows, 2)[0]
    assert len(two["frontier"]) == 4, "only on-target papers are evidence"
    assert any("landed outside" in line for line in two["counter_case"])


def test_scores_reproduce_across_reads(session):
    sp = Space(session)
    lineage = sp.region(0, centre=(0.0, 0.0), collected=40, read=10,
                        year=YEAR - 8)
    sp.frontier(lineage, 0, n=3, year=YEAR - 9)
    sp.work("doi:x", at=(9.0, 9.0))
    first, _, _ = scored(session, sp)
    second, _, _ = scored(session, sp)
    assert ([(c["signal"], c["cluster"], c["score"]) for c in first]
            == [(c["signal"], c["cluster"], c["score"]) for c in second])


# --- the endpoint ------------------------------------------------------------


def test_planning_says_what_is_missing_before_a_fit(client):
    body = client.get("/api/planning").json()
    assert body["signals"] == []
    assert "fitted" in body["note"]


def test_planning_returns_four_sections_and_no_total(client):
    from app.database import _engine
    from sqlmodel import Session

    with Session(_engine) as s:
        sp = Space(s)
        lineage = sp.region(0, centre=(0.0, 0.0), collected=40, read=20,
                            year=YEAR - 8)
        sp.region(1, centre=(4.0, 4.0), collected=6)
        sp.frontier(lineage, 0, n=5, year=YEAR)
        sp.work("doi:x", at=(9.0, 9.0))
        s.commit()

    body = client.get("/api/planning").json()
    assert [s["signal"] for s in body["signals"]] == [1, 2, 3, 4]
    assert body["as_of"] is not None, "the panel dates itself"
    assert "total" not in body and "combined" not in body
    assert body["thresholds"]["dense_region"] >= 1
    assert body["weights"], "the weights are on the page, not buried in code"
