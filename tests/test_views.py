"""Structural checks over the view files.

Every failure these catch happened, repeatedly, in one session: a helper was
defined and never called, a table row grew a cell its header did not, a click
handler was bound to a class the template never emitted. All three are silent
— the page renders, the syntax is valid, the backend tests pass, and a control
you were told exists simply is not there.

They are deliberately static. The container has no node, and a check that only
runs on a developer machine is a check that stops running. The real render is
exercised separately, and skips where node is absent.
"""
import json
import re
import shutil
import subprocess
import textwrap

import pytest

from app.main import STATIC_DIR

VIEWS = sorted((STATIC_DIR / "js" / "views").glob("*.js"))

STATUS_PAYLOAD = {
            "build": {"version": "abc1234", "started_at": "09:00 UTC"},
            "changes": {"as_of": None, "age_days": None, "source": "s",
                        "available": False, "note": "first snapshot"},
            "headline": {"as_of": None, "age_days": None, "source": "s",
                         "works": 1, "publications": 1,
                         "citations_automated": 1, "citations_manual": 0,
                         "lane_gap": None, "span": [2015, 2026],
                         "unconfirmed": 0},
            "accrual": {"as_of": None, "age_days": None, "source": "s",
                        "works": []},
            "momentum": {"as_of": None, "age_days": None, "source": "s",
                         "window_years": 2, "works": []},
            "areas": {"as_of": None, "age_days": None, "source": "s",
                      "regions": [], "unclustered": 0, "note": "none"},
            "drift": {"as_of": None, "age_days": None, "source": "s",
                      "public": {}, "position": None, "note": ""},
            "coverage": {"as_of": None, "age_days": None, "source": "s",
                         "sources": [], "fitted_documents": 0,
                         "unclustered": 0, "unclustered_share": None,
                         "regions": 0, "unconfirmed_works": 0,
                         "rulings_made": 0, "excluded": 0,
                         "unclaimed_publicly": 0, "public_cv_age_days": None,
                         "manual_lane_empty": True},
        }

READING_PAYLOAD = {"as_of": None, "age_days": None, "source": "s",
                   "candidates": [], "collected_unread": 0,
                   "with_reading_evidence": 0, "weights": {}, "note": ""}

DEFINED = re.compile(r"^\s{2,}(?:async\s+)?function\s+(\w+)", re.MULTILINE)


def source(path):
    return path.read_text()


@pytest.mark.parametrize("path", VIEWS, ids=lambda p: p.name)
def test_every_helper_a_view_defines_is_used(path):
    """`rowActions` was defined, wired to handlers, and never called — so the
    works table shipped without its action column and nothing complained."""
    text = source(path)
    for name in DEFINED.findall(text):
        # Any mention beyond the definition counts: a helper is as often
        # passed to .map() as it is called outright.
        uses = len(re.findall(rf"\b{re.escape(name)}\b", text))
        assert uses > 1, (
            f"{path.name} defines {name}() and never mentions it again — "
            "either it is dead, or the call site was meant to exist")


@pytest.mark.parametrize("path", VIEWS, ids=lambda p: p.name)
def test_table_rows_match_their_headers(path):
    """The works table grew a cell without a column, so the header ran out
    before the row did and the control fell off the end of the page."""
    text = source(path)
    defined = set(DEFINED.findall(text))
    # Pair each header with the row builder named in the tbody beside it,
    # rather than with every row in the file: a panel may hold several tables.
    # The builder is whichever identifier in that expression is a function this
    # view defines -- `.map(w => workRow(w, cats))` names `map` first.
    pattern = re.compile(r"<thead>(?P<head>.*?)</thead>.*?<tbody>(?P<body>.*?)</tbody>",
                         re.S)
    for m in pattern.finditer(text):
        columns = len(re.findall(r"<th[ >]", m.group("head")))
        # The builder may be called (`workRow(w, cats)`) or merely named
        # (`.map(sourceRow)`), and some tables build their rows inline with no
        # named function at all. Handle all three rather than skipping.
        names = [n for n in re.findall(r"\b(\w+)\b", m.group("body"))
                 if n in defined]
        if names:
            body = re.search(rf"function {re.escape(names[0])}\(.*?\n  \}}",
                             text, re.S)
            assert body is not None, f"{path.name}: cannot read {names[0]}()"
            region, fn = body.group(0), names[0] + "()"
        else:
            region, fn = m.group("body"), "the inline row"
            # `<tbody>${rows}</tbody>`: the row markup was built into a
            # variable further up. Follow it rather than counting an empty
            # interpolation as zero cells.
            bare = re.fullmatch(r"\s*\$\{(\w+)\}\s*", region)
            if bare:
                # The nearest definition *before* this table, not the first in
                # the file: two functions may each build a local `bars`, and
                # taking the wrong one compares a table against someone else's
                # rows. This bit me on the dashboard.
                name = bare.group(1)
                defs = [d for d in re.finditer(
                    rf"(?:const|let|var)\s+{re.escape(name)}\s*=(.*?);\n",
                    text, re.S) if d.start() < m.start()]
                assert defs, f"{path.name}: cannot find where {name} is built"
                assigned = defs[-1]
                region, fn = assigned.group(1), f"rows built into {name}"
                # One more hop: `const body = rows.map(agreementRow)...` puts
                # the cells in the function, not in the assignment.
                if "<td" not in region:
                    via = [n for n in re.findall(r"\b(\w+)\b", region)
                           if n in defined]
                    if via:
                        target = re.search(
                            rf"function {re.escape(via[0])}\(.*?\n  \}}",
                            text, re.S)
                        if target:
                            region = target.group(0)
                            fn = f"{via[0]}() via {name}"
        cells = len(re.findall(r"<td[ >]", region))
        assert cells == columns, (
            f"{path.name}: {fn} emits {cells} cells against {columns} "
            "headers — a control will fall off the end of the row")


@pytest.mark.parametrize("path", VIEWS, ids=lambda p: p.name)
def test_handlers_target_markup_the_view_emits(path):
    """Click handlers were bound to `.drop-btn` while no template produced
    one, so the page carried listeners for elements that never existed."""
    text = source(path)
    for selector in set(re.findall(r"querySelectorAll?\(['\"]\.([\w-]+)['\"]\)", text)):
        assert f'class="{selector}"' in text or f"class=\"action {selector}\"" in text \
            or re.search(rf'class="[^"]*\b{re.escape(selector)}\b', text), (
            f"{path.name} binds to .{selector} but emits no such element")


def test_the_ledger_renders_its_controls():
    """A real render, where node is available. Asserts the things that were
    claimed and absent: links, categories, and a way to remove a work."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed (it is not in the container image)")

    work = {
        "fingerprint": "doi:10.1/m", "title": "A work you do not recognise",
        "year": 2019, "venue": None, "venues_seen": [], "doi": "10.1/m",
        "url": "https://doi.org/10.1/m", "type": "article",
        "category": "publication", "category_source": "inferred",
        "on_cv": False, "on_site": False, "confirmed": False,
        "citations": {"automated": 3, "manual": None, "provenance": {}},
        "counts_by_year": {}, "sources": ["openalex"],
        "links": [{"label": "openalex", "url": "https://openalex.org/W42"}],
    }
    drift = {"checked": True, "matched": 1, "claim_age_days": 40,
             "cv_file": "cv.pdf", "unindexed_other": 0, "unindexed": [],
             "unclaimed": [{"fingerprint": "doi:10.1/m", "title": "Unclaimed",
                            "year": 2019, "category": "publication",
                            "doi": "10.1/m", "url": "https://doi.org/10.1/m",
                            "links": []}]}
    ledger = {"snapshot": {"id": 1, "created_at": "x", "label": "m"},
              "works": [work], "totals": {"works": 1},
              "categories": ["publication", "conference"], "by_category": {},
              "drift": drift, "stale_settings": False}

    harness = textwrap.dedent("""
        const fs = require('fs');
        const LEDGER = %s;
        const STATUS = %s;
        const stub = () => ({ innerHTML: '', dataset: { endpoint: '/x' },
          addEventListener() {}, insertAdjacentHTML() {},
          closest: stub, querySelector: stub, querySelectorAll: () => [] });
        global.window = global;   // panels.js publishes onto it
        global.escHtml = (s) => String(s ?? '');
        global.__views = {};
        global.registerView = (p, fn) => { global.__views[p] = fn; };
        global.api = { get: async (p) => (p.startsWith('/status') ? STATUS
                        : p === '/ledger' ? LEDGER
                        : { proposals: [] }), post: async () => ({}) };
        // With `node -e`, argv is [node, ...args]: the script is not argv[1].
        for (const f of process.argv.slice(1)) eval(fs.readFileSync(f, 'utf8'));
        (async () => {
          const app = stub();
          await global.__views['/ledger'](app);
          process.stdout.write(app.innerHTML);
        })();
    """) % (json.dumps(ledger), json.dumps(STATUS_PAYLOAD))

    result = subprocess.run(
        [node, "-e", harness,
         str(STATIC_DIR / "js/panels.js"),
         str(STATIC_DIR / "js/views/ledger.js"),
         str(STATIC_DIR / "js/views/sources.js")],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    html = result.stdout

    assert 'href="https://doi.org/10.1/m"' in html, "a work must be openable"
    assert "https://openalex.org/W42" in html, "and list its other addresses"
    assert 'class="cat-select"' in html, "every work needs a category control"
    assert html.count('class="action drop-btn"') >= 2, (
        "a Not mine button on the work and on the unclaimed drift row")
    assert "What is it?" in html, "the actions column needs its header"


def test_the_map_renders_its_space():
    """The scatter, the legend, the region table and the tooltip layer.

    The legend and table are not decoration: light-mode aqua sits below 3:1
    against the chart surface, so identity has to be carried by something
    other than colour.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed (it is not in the container image)")

    data = {
        "space": {"id": 1, "backend": "lsa", "model": "tfidf+svd:100",
                  "created_at": "2026-09-20T19:00:00Z", "corpus_hash": "abc123",
                  "rows": 1737, "dimensions": 100, "explained_variance": 0.199,
                  "fit_seconds": 4.2, "params": {}},
        "points": [
            {"kind": "corpus", "ref": "A", "label": "A read paper", "year": 2019,
             "x": 0.1, "y": 0.2, "cluster": 0, "citations": None},
            {"kind": "work", "ref": "doi:1", "label": "An early paper",
             "year": 2013, "x": -0.3, "y": 0.4, "cluster": None, "citations": 8},
            {"kind": "work", "ref": "doi:2", "label": "A much cited paper",
             "year": 2015, "x": -0.2, "y": 0.3, "cluster": None, "citations": 348},
            {"kind": "work", "ref": "doi:3", "label": "A recent paper",
             "year": 2024, "x": 0.4, "y": -0.1, "cluster": None, "citations": 10},
            {"kind": "work", "ref": "doi:4", "label": "A new paper",
             "year": 2025, "x": 0.3, "y": -0.2, "cluster": None, "citations": 0},
            {"kind": "project", "ref": "parableSower", "label": "parableSower",
             "year": None, "x": 0.5, "y": -0.2, "cluster": 1, "citations": None},
        ],
        "clusters": [{"cluster": 0, "lineage": "r1", "name": None,
                      "terms": ["solar", "rooftop"], "size": 40,
                      "exemplar": "A much cited paper", "exemplar_ref": "A",
                      "x": 0.1, "y": 0.2}],
        "axes": [
            {"component": 0, "name": None, "confirmed": False,
             "positive": ["typical"], "negative": ["atypical"],
             "explained_variance": 0.002},
            {"component": 1, "name": None, "confirmed": False,
             "positive": ["solar", "adoption"], "negative": ["tenure"],
             "explained_variance": 0.08},
            {"component": 2, "name": None, "confirmed": False,
             "positive": ["agent"], "negative": ["recycling"],
             "explained_variance": 0.06},
        ],
        "unclustered": 12,
        "display": {"x": 1, "y": 2, "reprojected": False, "available": 16},
        "previous": [],
    }
    harness = textwrap.dedent("""
        const fs = require('fs');
        const DATA = %s;
        const STATUS = %s;
        const READING = %s;
        const stub = () => ({ innerHTML: '', dataset: {}, hidden: true,
          style: {}, addEventListener() {}, insertAdjacentHTML() {},
          closest: stub, getBoundingClientRect: () => ({}),
          get parentElement() { return stub(); },
          querySelector: stub, querySelectorAll: () => [] });
        global.window = global;   // panels.js publishes onto it
        global.escHtml = (s) => String(s ?? '');
        global.__views = {};
        global.registerView = (p, fn) => { global.__views[p] = fn; };
        global.api = { get: async (p) => (p.startsWith('/status/reading')
          ? READING : p.startsWith('/status') ? STATUS : DATA),
          post: async () => ({}) };
        for (const f of process.argv.slice(1)) eval(fs.readFileSync(f, 'utf8'));
        (async () => {
          const app = stub();
          await global.__views['/map'](app);
          process.stdout.write(app.innerHTML);
        })();
    """) % (json.dumps(data), json.dumps(STATUS_PAYLOAD),
            json.dumps(READING_PAYLOAD))

    result = subprocess.run([node, "-e", harness,
                             str(STATIC_DIR / "js/panels.js"),
                             str(STATIC_DIR / "js/views/map.js")],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    html = result.stdout

    assert html.count('class="pt ') == 6, "every point is drawn"
    assert 'class="pt pt-work"' in html and 'class="pt pt-project"' in html
    assert "viz-legend" in html and "Written" in html, "identity is not colour alone"
    assert "A much cited paper" in html, "the region table carries the exemplar"
    assert "viz-tip" in html, "hover layer is present"
    assert "abc123" in html, "the reproducibility stamp is shown"
    flat = " ".join(html.split())
    assert "The terms below are not names" in flat, "axes are offered unnamed"
    assert "Naming is yours" in flat
    # The primary action must be reachable without scrolling past the whole
    # page: it was below the scatter, the scree, the regions and the axes.
    assert html.count('id="fit-btn"') == 1, "one refit control, not two"
    assert html.index('id="fit-btn"') < html.index("The space"), (
        "the refit control comes before the chart, not after everything")
    # An axis a reader cannot orient is a line with dots on it.
    assert 'class="pole"' in html, "each axis end is labelled on the axis"
    assert html.count('class="pole"') == 4, "both ends of both axes"
    assert "solar" in html and "tenure" in html, "the poles carry their terms"
    assert "further that way than" in html, "direction, not category"
    assert 'id="pick-x"' in html and 'id="pick-y"' in html, "the pair is chosen"
    # Component 0 is the average-document direction: every document scores
    # positive on it, so it is never the projection anyone wants.
    assert 'value="0"' not in html, "component 0 is not offered as an axis"
    assert 'value="1"' in html and 'value="2"' in html

    # Area carries citations, not radius, and an uncited work stays clickable.
    radii = sorted(float(r) for r in re.findall(
        r'class="pt pt-work"[^>]*r="([\d.]+)"', html))
    assert radii[0] == 4.0, "an uncited work keeps the floor size"
    assert radii[-1] == 15.0, "the most cited work hits the cap"
    assert radii[-1] / radii[-2] < 348 / 10, "area, not radius — the outlier is not exaggerated"

    # What to read next moved to Frontier: half its rows are papers you do
    # not hold, which is a question about the literature rather than about the
    # shape of your own library.
    assert "What to read next" not in html
    assert 'class="drift"' in html, "the drift arrow is drawn"
    assert "drift-head" in html, "and has an arrowhead"
    assert "arrow is your own drift" in html, "and says what it means"


def test_the_status_dashboard_leads_with_change_and_dates_every_panel():
    """M4's exit criterion, asserted: every panel renders from cache and shows
    its own age. Difference leads, because the cadence is weekly and the only
    question a weekly glance answers is what is not the same as last week."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed (it is not in the container image)")

    block = lambda **kw: {"as_of": "2026-09-20T12:00:00+00:00", "age_days": 2.0,
                          "source": "test", **kw}
    data = {
        "changes": block(available=True, since="2026-09-13T12:00:00+00:00",
                         since_days=7.0, works_added=["A new paper"],
                         works_removed=[], citations_before=500,
                         citations_now=529, citations_delta=29,
                         works_moved=[{"title": "A paper", "was": 340,
                                       "now": 348, "delta": 8}]),
        "headline": block(works=30, publications=14, citations_automated=529,
                          citations_manual=0, lane_gap=None, span=[1984, 2026],
                          unconfirmed=30),
        "accrual": block(works=[{"fingerprint": "f", "title": "A paper",
                                 "year": 2015, "category": "publication",
                                 "total": 348,
                                 "counts": {"2023": 54, "2024": 40, "2025": 15}}]),
        "momentum": block(window_years=2, works=[
            {"fingerprint": "f", "title": "A paper", "year": 2015, "total": 348,
             "recent": 55, "share": 0.158, "age": 11}]),
        "areas": block(regions=[{"cluster": 0, "lineage": "r", "name": None,
                                 "terms": ["solar", "leasing"], "collected": 99,
                                 "read": 4, "written": 8, "exemplar": "A paper"},
                                {"cluster": 1, "lineage": "r2", "name": None,
                                 "terms": ["tonal"], "collected": 54, "read": 0,
                                 "written": 0, "exemplar": "A music paper"}],
                       unclustered=1181),
        "drift": block(public={"checked": True, "matched": 13,
                               "unclaimed": [{"title": "x"}],
                               "claim_age_days": 40},
                       position={"published": {"x": 0.1, "y": 0.2, "n": 30},
                                 "in_progress": {"x": 0.2, "y": 0.1, "n": 17},
                                 "displacement": 0.074},
                       note="units note"),
        "coverage": block(sources=[{"key": "zotero", "status": "ok",
                                    "items": 3035, "age_days": 0.1,
                                    "last_clean": 0.1}],
                          fitted_documents=1735, unclustered=1181,
                          unclustered_share=0.603, regions=26,
                          unconfirmed_works=30, rulings_made=11, excluded=5,
                          unclaimed_publicly=2, public_cv_age_days=40,
                          manual_lane_empty=True),
    }
    reading = {
        "as_of": "2026-09-21T09:00:00+00:00", "age_days": 0.2,
        "source": "map, Zotero and OpenAlex",
        "collected_unread": 2877, "with_reading_evidence": 158,
        "weights": {}, "note": "mark what you have read",
        "candidates": [{
            "ref": "K", "label": "A paper you collected", "year": 2026,
            "cited_by": 4, "cluster": 0, "region": "solar, leasing",
            "score": 4.2,
            "reasons": [{"signal": "near_your_work", "weight": 3.0,
                         "detail": "8 of your works sit in this region"},
                        {"signal": "recent", "weight": 1.0,
                         "detail": "published 2026"}]}],
    }
    harness = textwrap.dedent("""
        const fs = require('fs');
        const DATA = %s;
        const READING = %s;
        const stub = () => ({ innerHTML: '', dataset: {}, hidden: true,
          style: {}, textContent: '', addEventListener() {},
          insertAdjacentHTML() {}, closest: stub,
          getBoundingClientRect: () => ({}),
          querySelector: stub, querySelectorAll: () => [] });
        global.window = global;   // panels.js publishes onto it
        global.escHtml = (s) => String(s ?? '');
        global.__views = {};
        global.registerView = (p, fn) => { global.__views[p] = fn; };
        global.api = { get: async (p) => (p.startsWith('/status/reading')
          ? READING : DATA), post: async () => ({}) };
        for (const f of process.argv.slice(1)) eval(fs.readFileSync(f, 'utf8'));
        (async () => {
          const app = stub();
          await global.__views['/status'](app);
          process.stdout.write(app.innerHTML);
        })();
    """) % (json.dumps(data), json.dumps(reading))

    result = subprocess.run([node, "-e", harness,
                             str(STATIC_DIR / "js/panels.js"),
                             str(STATIC_DIR / "js/views/status.js")],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    html = result.stdout

    # Change leads: it appears before the level it is a change in.
    assert html.index("Changes since") < html.index("Portfolio")
    assert "+29" in html, "the delta is the headline of the lead panel"

    # Every panel dates itself.
    assert html.count("days old") >= 4, "each panel carries its own age"

    # Accrual, momentum, areas and the reading list moved to the pages they
    # describe: a status page answering four questions was how it grew to
    # seven panels.
    assert "Citation accrual" not in html
    assert "What to read next" not in html
    assert "Active areas" not in html

    # Coverage is counted, and says so.
    assert "counted from current state" in html
    assert "1181" in html and "60%" in html
    assert "empty — nothing pasted" in html


def test_the_planning_page_never_shows_a_bare_number():
    """M6-T7's exit, asserted on the rendered page.

    Four sections and no total; every candidate's score taken apart into its
    inputs; the evidence behind it; the counter-case present and open; and the
    route stated rather than implied. The last of those is the one that would
    fail silently — a routed action that renders as a badge nobody reads is a
    planning page that ranks and does not route.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed (it is not in the container image)")

    def candidate(signal, cluster, score, **kw):
        row = {
            "signal": signal, "signal_name": f"Signal {signal}",
            "move": "do the thing", "cluster": cluster, "lineage": f"r{cluster}",
            "region": f"region {cluster}", "terms": ["solar", "leasing"],
            "exemplar": "A much cited paper", "score": score,
            "inputs": [{"input": "collecting", "effect": "adds", "weight": 2.0,
                        "value": 0.8, "contribution": 1.6,
                        "detail": "99 items collected here"},
                       {"input": "distance_from_your_work", "effect": "scales",
                        "weight": 1.0, "value": 0.5, "contribution": 0.5,
                        "detail": "your nearest work is 0.41 away"}],
            "confirmation": None, "bridge": None,
            "nearest_work": {"ref": "doi:1", "label": "An early paper",
                             "year": 2015, "distance": 0.41},
            "counts": {"collected": 99, "read": 4, "engaged": 2, "written": 0,
                       "recent_written": 0, "newest_year": 2019,
                       "median_year": 2014, "recent_collected": 3,
                       "projects": [], "live": False,
                       "live_because": None, "spread": 0.12},
            "corpus": [{"ref": "K", "label": "A paper you read", "year": 2019,
                        "reading": "annotated", "cited_by": 12,
                        "url": "https://doi.org/10.1/x"}],
            "frontier": [{"title": "A frontier paper", "year": 2026,
                          "venue": "Energy Policy", "cited_by": 40,
                          "topic": "A topic", "distance": 0.01,
                          "url": "https://doi.org/10.9/y"}],
            "topics": [{"id": "T1", "name": "A topic", "share": 0.4,
                        "members": 8}],
            "frontier_age_days": 3.0,
            "counter_case": ["95 of 99 items here carry no evidence of being read."],
            "route": {"output": "brief", "label": "Read-in brief",
                      "file": "{YYMMDD}_{slug}_readin_ra.txt",
                      "detail": "topic, focus lines, seed DOIs",
                      "why": "the grounding is already paid for"},
        }
        row.update(kw)
        return row

    unconfirmed = candidate(
        3, 2, 3.1,
        confirmation={"confirmed": False, "basis": "the frontier says otherwise",
                      "detail": "this is a reading gap, not an opportunity"},
        bridge={"between": [{"cluster": 0, "label": "region 0", "distance": 0.2},
                            {"cluster": 1, "label": "region 1", "distance": 0.3}],
                "detail": "sits between region 0 and region 1"},
        counter_case=["the frontier says otherwise"],
        route={"output": "fill", "label": "Corpus fill list",
               "file": "{YYMMDD}_{slug}_fill_ra.txt",
               "detail": "the frontier minus your library",
               "why": "the honest response to a reading gap is reading"})

    data = {
        "as_of": "2026-09-21T09:00:00+00:00", "age_days": 1.0,
        "source": "the map and the frontier feed",
        "signals": [
            {"signal": 1, "name": "Read, never written", "move": "write",
             "note": "corpus-dense regions far from every ledger work",
             "found": 1, "candidates": [candidate(1, 0, 4.2)]},
            {"signal": 2, "name": "Field-active, you're absent",
             "move": "read in", "note": "busy in the frontier feed",
             "found": 0, "candidates": []},
            {"signal": 3, "name": "Thin literature, strong grounding",
             "move": "a real hole", "note": "confirmed against the frontier",
             "found": 1, "candidates": [unconfirmed]},
            {"signal": 4, "name": "Thin reading", "move": "scout",
             "note": "no activity filter", "found": 0, "candidates": []},
        ],
        "unassessable": [{"signal": 2, "cluster": 7, "region": "region 7",
                          "reason": "no frontier set has been gathered"}],
        "weights": {"collecting": 2.0},
        "thresholds": {"dense_region": 25, "thin_region": 15, "quiet_years": 3},
        "space": {"id": 1, "rows": 1737, "dimensions": 100,
                  "corpus_hash": "abc123"},
        "geometry": "the fitted space, 40 components", "exact_geometry": True,
        "regions": 26, "frontier_queried": 12, "works_placed": 30,
        "briefs_placed": 17, "unclustered": 1181, "largest_region": 99,
        "note": "four signals, never blended",
    }

    harness = textwrap.dedent("""
        const fs = require('fs');
        const DATA = %s;
        const stub = () => ({ innerHTML: '', dataset: {}, hidden: true,
          style: {}, textContent: '', addEventListener() {},
          insertAdjacentHTML() {}, closest: stub,
          getBoundingClientRect: () => ({}),
          get parentElement() { return stub(); },
          querySelector: stub, querySelectorAll: () => [] });
        global.window = global;   // panels.js publishes onto it
        global.escHtml = (s) => String(s ?? '');
        global.__views = {};
        global.registerView = (p, fn) => { global.__views[p] = fn; };
        global.api = { get: async () => DATA, post: async () => ({}) };
        for (const f of process.argv.slice(1)) eval(fs.readFileSync(f, 'utf8'));
        (async () => {
          const app = stub();
          await global.__views['/planning'](app);
          process.stdout.write(app.innerHTML);
        })();
    """) % json.dumps(data)

    result = subprocess.run([node, "-e", harness,
                             str(STATIC_DIR / "js/panels.js"),
                             str(STATIC_DIR / "js/views/planning.js")],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    html = result.stdout
    flat = " ".join(html.split())

    # Four sections, in order, and nothing that adds them up.
    for name in ("Read, never written", "Field-active", "Thin literature",
                 "Thin reading"):
        assert name in html, f"{name} has no section"
    assert html.index("Read, never written") < html.index("Thin reading"), (
        "signal 4 is ranked last, deliberately")

    # The score is taken apart, not merely printed.
    assert "Why this score" in html
    assert "99 items collected here" in html, "each input carries its detail"
    assert "×0.50" in flat, "a scaling term reads as a factor, not as a sum"
    assert "+1.60" in flat, "an adding term reads as a contribution"

    # The evidence behind it, on both sides.
    assert "A paper you read" in html and "A frontier paper" in html
    assert "An early paper" in html, "the nearest work of yours is named"

    # The counter-case is present and open by default.
    assert 'class="counter" open' in html
    assert "no evidence of being read" in html

    # Signal 3's split, and the routing that hangs off it.
    assert "unconfirmed" in html and "reading gap" in html
    assert "Corpus fill list" in html and "Read-in brief" in html
    assert html.count('class="action route-btn"') == 2, (
        "every candidate says where it routes")
    # The button is live now that M7 exists, and it carries what the writer
    # needs. A routed action that cannot be pressed is a label.
    assert 'data-kind="brief"' in html and 'data-kind="fill"' in html
    assert 'data-cluster=' in html and 'data-signal=' in html
    assert "disabled" not in html.split('route-btn')[1][:120], (
        "the writers landed in M7; the button is no longer waiting on them")
    assert 'class="route-preview"' in html, (
        "and nothing is written before you have seen it")
    assert "bridge" in html, "the bridge attribute is shown"

    # A region the frontier was never asked about is listed, not scored zero.
    assert "Not assessed" in html and "region 7" in html
    assert 'class="action gather-btn"' in html, (
        "and the page offers the one thing that would resolve it")


def test_the_scholar_importer_offers_a_file_and_says_where_the_file_goes():
    """The source panel used to answer an empty Scholar lane with *drop an
    export in /app/data/import* — a path inside raDash's own Docker volume,
    with no host side and nothing to open, while the importer that writes
    there sat on another page.

    So the control has to be on the page that reports the problem, it has to
    take a file and not only a paste, and it has to say outright that there is
    no folder to put anything in.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed (it is not in the container image)")

    data = {
        "sources": [{"key": "scholar", "status": "missing", "count": None,
                     "age_seconds": None, "counts": {}, "failures": [],
                     "note": ("nothing imported yet — paste or upload your "
                              "Scholar profile in the importer on this page.")}],
        "refreshed": False, "ok": True, "failing": [], "waiting": ["scholar"],
        "stale": [], "network_sources": ["trundlr", "openalex", "s2", "website"],
    }
    harness = textwrap.dedent("""
        const fs = require('fs');
        const DATA = %s;
        const stub = () => ({ innerHTML: '', dataset: {}, hidden: true,
          style: {}, textContent: '', addEventListener() {},
          insertAdjacentHTML() {}, closest: stub,
          getBoundingClientRect: () => ({}),
          get parentElement() { return stub(); },
          querySelector: stub, querySelectorAll: () => [] });
        global.window = global;
        global.escHtml = (s) => String(s ?? '');
        global.__views = {};
        global.registerView = (p, fn) => { global.__views[p] = fn; };
        global.api = { get: async () => DATA, post: async () => DATA };
        for (const f of process.argv.slice(1)) eval(fs.readFileSync(f, 'utf8'));
        (async () => {
          const app = stub();
          await global.__views['/sources'](app);
          process.stdout.write(app.innerHTML);
        })();
    """) % json.dumps(data)

    result = subprocess.run([node, "-e", harness,
                             str(STATIC_DIR / "js/panels.js"),
                             str(STATIC_DIR / "js/views/sources.js")],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    html = result.stdout
    flat = " ".join(html.split())

    # The control is on the page that reports the empty lane.
    assert 'id="scholar-upload"' in html, "a file can be uploaded, not only pasted"
    assert 'type="file"' in html and 'accept=".txt,.csv' in html
    assert 'id="scholar-preview"' in html and 'id="scholar-commit"' in html
    assert 'class="import-text"' in html, "and the box takes a dropped file"

    # Specific instructions, not "paste your profile".
    assert "scholar.google.com" in html, "it names where to start"
    assert "Show more" in flat, "and the step everyone forgets"
    assert "CSV export" in flat, "and the export that cannot move the lane"
    # A worked example of the real columnar shape, not the `Cited by 402`
    # phrasing the parser used to look for and no real paste contains.
    assert "CITED BY" in html and "Renewable Energy 12, 101-120" in html
    assert 'class="sample"' in html

    # The sentence that answers the complaint.
    assert "no folder on your machine to put anything in" in flat


def test_the_ledger_and_sources_show_the_same_importer():
    """One implementation, two pages. Two copies of these instructions would
    drift, and the one you happened to open would be the stale one."""
    panels = (STATIC_DIR / "js" / "panels.js").read_text()
    assert "SCHOLAR_HELP" in panels and "function importPanel" in panels
    for name in ("ledger.js", "sources.js"):
        view = (STATIC_DIR / "js" / "views" / name).read_text()
        assert "P.importPanel(" in view, f"{name} builds its own import panel"
        assert "SCHOLAR_HELP" in view, f"{name} carries its own instructions"


def test_what_to_read_next_lives_on_frontier_and_says_what_ordered_it():
    """It belongs beside the feed that half fills it, not beside the map.

    And a list reordered by the drift has to show the drift: the heaviest term
    in the score is invisible on the row unless the panel explains it, and a
    ranking the reader cannot see the basis of is one they can only take on
    trust.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed (it is not in the container image)")

    frontier = {
        "regions": [{
            "cluster": 0, "lineage": "r0", "name": None,
            "terms": ["solar", "leasing"], "size": 40,
            "topics": [{"id": "T1", "name": "A topic", "share": 0.4,
                        "members": 8}],
            "fetched_at": "2026-09-21T09:00:00Z", "age_days": 3.0,
            "items": [{"openalex_id": "W1", "doi": "10.9/a",
                       "title": "A frontier paper", "year": 2026,
                       "venue": "Energy Policy", "cited_by": 40,
                       "topic": "A topic", "already_held": False,
                       "distance": 0.01, "off_target": False,
                       "url": "https://doi.org/10.9/a"}],
        }],
        "never_fetched": 0, "note": "each set is dated",
    }
    reading = {
        "as_of": "2026-09-21T09:00:00+00:00", "age_days": 0.2,
        "source": "map, Zotero, OpenAlex and the frontier feed",
        "collected_unread": 2877, "with_reading_evidence": 158,
        "weights": {}, "note": "mark what you have read",
        "drift": {"cut": 2019, "older": 12, "recent": 18, "length": 0.0731},
        "candidates": [{
            "ref": "W1", "label": "A paper ahead of you", "year": 2026,
            "cited_by": 40, "cluster": 0, "region": "solar, leasing",
            "score": 6.1, "frontier": True, "url": "https://doi.org/10.9/a",
            "reasons": [{"signal": "with_your_drift", "effect": "adds",
                         "weight": 2.4,
                         "detail": "sits 1.60 drift-lengths along the "
                                   "direction your work has moved since 2019"},
                        {"signal": "frontier", "effect": "adds", "weight": 1.2,
                         "detail": "not in your library"}]}],
    }
    harness = textwrap.dedent("""
        const fs = require('fs');
        const FRONTIER = %s;
        const READING = %s;
        const stub = () => ({ innerHTML: '', dataset: {}, hidden: true,
          style: {}, textContent: '', addEventListener() {},
          insertAdjacentHTML() {}, closest: stub,
          getBoundingClientRect: () => ({}),
          get parentElement() { return stub(); },
          querySelector: stub, querySelectorAll: () => [] });
        global.window = global;
        global.escHtml = (s) => String(s ?? '');
        global.__views = {};
        global.registerView = (p, fn) => { global.__views[p] = fn; };
        global.api = { get: async (p) => (p.startsWith('/status/reading')
          ? READING : FRONTIER), post: async () => ({}) };
        for (const f of process.argv.slice(1)) eval(fs.readFileSync(f, 'utf8'));
        (async () => {
          const app = stub();
          await global.__views['/frontier'](app);
          process.stdout.write(app.innerHTML);
        })();
    """) % (json.dumps(frontier), json.dumps(reading))

    result = subprocess.run([node, "-e", harness,
                             str(STATIC_DIR / "js/panels.js"),
                             str(STATIC_DIR / "js/views/frontier.js")],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    html = result.stdout
    flat = " ".join(html.split())

    assert "What to read next" in html, "the list is on this page now"
    # And it leads: it is the question the page answers, the per-region sets
    # below it are the working.
    assert html.index("What to read next") < html.index("Gather")

    assert "Ordered with your drift" in flat
    assert "2019" in html and "12" in html and "18" in html, (
        "the split, and how many works sit on each side of it")
    assert "drift-lengths" in flat, "and the row says how far along it sits"
    assert "A paper ahead of you" in html


def test_the_frontier_page_still_offers_the_list_before_any_fit():
    """No space means no regions, and it used to mean an empty page. The
    reading list is the page's headline now, so it renders either way."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed (it is not in the container image)")

    reading = {"as_of": None, "age_days": None, "source": "s",
               "candidates": [], "collected_unread": 0,
               "with_reading_evidence": 0, "weights": {}, "drift": None,
               "note": "nothing to suggest yet"}
    harness = textwrap.dedent("""
        const fs = require('fs');
        const READING = %s;
        const stub = () => ({ innerHTML: '', dataset: {}, hidden: true,
          style: {}, textContent: '', addEventListener() {},
          insertAdjacentHTML() {}, closest: stub,
          getBoundingClientRect: () => ({}),
          get parentElement() { return stub(); },
          querySelector: stub, querySelectorAll: () => [] });
        global.window = global;
        global.escHtml = (s) => String(s ?? '');
        global.__views = {};
        global.registerView = (p, fn) => { global.__views[p] = fn; };
        global.api = { get: async (p) => (p.startsWith('/status/reading')
          ? READING : { regions: [], note: 'no space has been fitted' }),
          post: async () => ({}) };
        for (const f of process.argv.slice(1)) eval(fs.readFileSync(f, 'utf8'));
        (async () => {
          const app = stub();
          await global.__views['/frontier'](app);
          process.stdout.write(app.innerHTML);
        })();
    """) % json.dumps(reading)

    result = subprocess.run([node, "-e", harness,
                             str(STATIC_DIR / "js/panels.js"),
                             str(STATIC_DIR / "js/views/frontier.js")],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    html = result.stdout
    assert "no space has been fitted" in html
    assert "What to read next" in html
    # An empty list explains nothing about its order, because it has none.
    assert "nothing to suggest yet" in html
    assert "Ordered with your drift" not in html
