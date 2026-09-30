"""The map: what text an item is represented by, and what a fit records."""
import numpy as np
import pytest

from app.mapping import axes as axes_mod
from app.mapping import cluster as cluster_mod
from app.mapping import text as text_mod
from app.mapping.backends import LsaBackend, get_backend


def doc(ref, title="", abstract="", venue=None, year=None, kind="corpus"):
    return text_mod.Document(ref=ref, kind=kind, title=title, abstract=abstract,
                             venue=venue, year=year)


LONG = ("Residential solar adoption diffuses through peer networks rather than "
        "through price signals alone, and the threshold at which it does so "
        "depends on the spatial structure of the network. " * 2)

TOPICS = ("rooftop photovoltaic households rebate",
          "medieval manorial tenure land records",
          "agent based simulation calibration heterogeneity")


def _varied(n):
    """Documents that actually differ — a corpus of near-identical strings has
    no vocabulary left after pruning, which is a property of the fixture."""
    return [f"{TOPICS[i % len(TOPICS)]} study number {i} {LONG[:120]}"
            for i in range(n)]


# --- text assembly ---------------------------------------------------------

def test_text_is_title_then_venue_then_abstract():
    d = doc("k", title="Kindred effects", venue="Energy Policy", abstract="Body text.")
    assert d.text.startswith("Kindred effects")
    assert "Energy Policy" in d.text
    assert d.text.endswith("Body text.")


def test_publisher_boilerplate_is_stripped():
    """Repeated across hundreds of items it becomes a term the fit believes in."""
    d = doc("k", title="A paper", abstract="© 2020 Elsevier Ltd. All rights reserved. Real content here.")
    assert "elsevier" not in d.text.lower()
    assert "all rights reserved" not in d.text.lower()
    assert "Real content here." in d.text


def test_a_thin_item_is_held_back_not_placed_at_the_origin():
    corpus = text_mod.assemble([doc("thin", title="Short"),
                                doc("full", title="Full", abstract=LONG)])
    assert [d.ref for d in corpus.documents] == ["full"]
    assert corpus.skipped_thin == 1


def test_thin_items_can_be_included_deliberately():
    corpus = text_mod.assemble([doc("thin", title="Short")], include_thin=True)
    assert len(corpus.documents) == 1


def test_openalex_supplies_an_abstract_zotero_lacks():
    class Item:
        key, title, doi, year = "k", "A paper", "10.1/x", 2020
        abstract = ""
        reading = "collected"
        fields = {"publicationTitle": "Energy Policy"}

    class Lib:
        items = [Item()]

    docs = text_mod.from_zotero(Lib(), enrichment={"10.1/x": LONG})
    assert docs[0].enriched is True
    assert LONG[:40] in docs[0].text


def test_the_corpus_hash_ignores_order_and_notices_content():
    a, b = doc("1", abstract=LONG), doc("2", abstract=LONG + " extra")
    assert text_mod.corpus_hash([a, b]) == text_mod.corpus_hash([b, a])
    assert text_mod.corpus_hash([a]) != text_mod.corpus_hash([b])


# --- backend ---------------------------------------------------------------

def test_lsa_is_the_backend():
    assert get_backend().name == "lsa"


def test_an_unknown_backend_is_refused_rather_than_guessed():
    """The embedding backend was cut; asking for one should say so, not
    silently hand back LSA and leave you believing you got embeddings."""
    with pytest.raises(ValueError, match="TF-IDF"):
        get_backend("ollama")


def test_a_fit_reports_what_made_it():
    texts = _varied(40)
    fitted = LsaBackend(min_df=2).fit_transform(texts, dimensions=5)
    assert fitted.backend == "lsa"
    assert fitted.model.startswith("tfidf+svd:")
    assert fitted.vectors.shape[0] == 40
    assert 0 < fitted.explained_variance <= 1, "clamped: summing ratios drifts"
    assert fitted.terms and fitted.loadings is not None


def test_transform_places_new_text_without_refitting():
    texts = _varied(40)
    backend = LsaBackend(min_df=2)
    fitted = backend.fit_transform(texts, dimensions=5)
    placed = backend.transform(["A new document about peer networks."])
    assert placed.shape == (1, fitted.dimensions)


def test_transform_before_fit_is_an_error_not_a_guess():
    with pytest.raises(RuntimeError):
        LsaBackend().transform(["anything"])


# --- axes ------------------------------------------------------------------

def test_axes_report_terms_and_never_invent_a_name():
    texts = [f"solar adoption households networks {i}" for i in range(25)] + \
            [f"medieval land tenure manorial records {i}" for i in range(25)]
    fitted = LsaBackend(min_df=2).fit_transform(texts, dimensions=4)
    poles = axes_mod.poles(fitted, components=2)
    assert poles[0].positive and poles[0].negative
    assert all(not hasattr(a, "name") or getattr(a, "name", None) is None
               for a in poles)
    assert axes_mod.scree(fitted)


def test_a_backend_without_a_vocabulary_reports_no_terms():
    """An embedding space cannot be read, and says so rather than confabulating."""
    from app.mapping.backends import Fitted
    fitted = Fitted(vectors=np.zeros((10, 3)), backend="none", model="m",
                    component_variance=[0.1, 0.05, 0.01])
    poles = axes_mod.poles(fitted, components=3)
    assert all(a.positive == [] and a.negative == [] for a in poles)


# --- clusters --------------------------------------------------------------

def test_clustering_leaves_noise_as_noise():
    """Separated by direction, not magnitude: vectors are L2-normalised before
    clustering, so length carries no information by the time HDBSCAN sees it."""
    # Signal lives past component 0, as it does in a real fit: c0 is the mean
    # direction and is excluded from clustering.
    rng = np.random.default_rng(0)
    blob_a = np.array([0.5, 1.0, 0, 0]) + rng.normal(0, 0.02, size=(30, 4))
    blob_b = np.array([0.5, 0, 1.0, 0]) + rng.normal(0, 0.02, size=(30, 4))
    stray = np.array([[0.5, 0.0, 0.0, 1.0]])
    clusters = cluster_mod.fit(np.vstack([blob_a, blob_b, stray]),
                               dimensions=4)
    assert len(clusters) == 2
    assert sum(c.size for c in clusters) == 60, \
        "the item pointing somewhere else is not forced into a region"


def test_length_alone_does_not_split_a_region():
    """The measured difference on the real corpus: unnormalised, document
    *length* competes with document *topic* for the algorithm's attention, and
    one subject at two scales reads as two subjects.

    Asserted as a contrast, because that is the claim: the same points split
    on length before normalising and do not after.
    """
    from sklearn.cluster import HDBSCAN

    rng = np.random.default_rng(1)
    direction = np.array([0.3, 1.0, 0.2, 0])
    short = direction * 0.2 + rng.normal(0, 0.01, size=(20, 4))
    long_ = direction * 9.0 + rng.normal(0, 0.01, size=(20, 4))
    points = np.vstack([short, long_])

    raw = HDBSCAN(min_cluster_size=8).fit_predict(points)
    assert len({int(v) for v in raw if v >= 0}) == 2, \
        "on raw vectors, scale alone invents a second region"

    clusters = cluster_mod.fit(points, dimensions=4, min_cluster_size=8)
    assert len(clusters) < 2, "normalised, the split disappears"


def test_labels_favour_distinguishing_terms_over_frequent_ones():
    texts = ([ "energy model policy solar rooftop households" ] * 12 +
             [ "energy model policy medieval tenure manorial" ] * 12)
    clusters = [cluster_mod.Cluster(cluster=0, members=list(range(12))),
                cluster_mod.Cluster(cluster=1, members=list(range(12, 24)))]
    cluster_mod.label_clusters(clusters, texts)
    for c in clusters:
        assert "energy" not in c.terms, "a term in every document separates nothing"
    assert any("solar" in c.terms or "rooftop" in c.terms for c in clusters)


def test_the_exemplar_is_the_most_cited_member():
    clusters = [cluster_mod.Cluster(cluster=0, members=[0, 1, 2])]
    cluster_mod.choose_exemplars(clusters, [3.0, 91.0, 12.0])
    assert clusters[0].exemplar_row == 1


def test_a_region_that_keeps_its_members_keeps_its_identity():
    """Cluster numbers are an artefact of the run; identity must not be."""
    previous = {"r00000001": ["a", "b", "c", "d"]}
    c = cluster_mod.Cluster(cluster=7, members=[0, 1, 2],
                            refs=["a", "b", "c"])
    cluster_mod.assign_lineage([c], previous)
    assert c.lineage == "r00000001", "renumbered, not renamed"


def test_a_genuinely_new_region_gets_a_new_identity():
    previous = {"r00000001": ["a", "b", "c", "d"]}
    c = cluster_mod.Cluster(cluster=0, members=[0, 1], refs=["x", "y"])
    cluster_mod.assign_lineage([c], previous)
    assert c.lineage != "r00000001"


def test_one_previous_region_is_inherited_once():
    """A region that splits must not produce two claimants to one name."""
    previous = {"r00000001": ["a", "b", "c", "d"]}
    big = cluster_mod.Cluster(cluster=0, members=[0, 1, 2], refs=["a", "b", "c"])
    small = cluster_mod.Cluster(cluster=1, members=[3, 4], refs=["d", "z"])
    cluster_mod.assign_lineage([big, small], previous)
    assert big.lineage == "r00000001"
    assert small.lineage != "r00000001"


# --- the benchmark ---------------------------------------------------------

def test_the_machine_endpoint_reports_what_it_has(client):
    body = client.get("/api/map/machine").json()
    assert body["cpu_count"] >= 1
    assert len(body["load_average"]) == 3


def test_the_benchmark_says_so_when_there_is_no_corpus(client):
    """Sources are absent in the test container, which is the fresh-deploy
    state: it must explain rather than divide by zero."""
    resp = client.get("/api/map/benchmark")
    assert resp.status_code == 503
    assert "corpus" in resp.json()["detail"]


def test_the_benchmark_refuses_to_become_expensive(client):
    """A benchmark that takes minutes is the problem it exists to detect."""
    assert client.get("/api/map/benchmark?sample=5000").status_code == 422
    assert client.get("/api/map/benchmark?sample=10").status_code == 422


def test_any_component_pair_can_be_drawn_without_refitting(client, tmp_path, monkeypatch):
    """A map is a projection, and which one you want depends on what you are
    looking for. The full vectors are kept beside the database precisely so
    the choice costs a read rather than a refit."""
    import numpy as np
    from sqlmodel import Session
    from app.database import _engine
    from app.models import MapPoint, MapSpace

    vectors = np.arange(60, dtype=float).reshape(10, 6)
    path = tmp_path / "space.npz"
    np.savez_compressed(path, vectors=vectors)

    with Session(_engine) as s:
        space = MapSpace(is_current=True, backend="lsa", model="m",
                         corpus_hash="h", row_count=10, dimensions=6,
                         vectors_path=str(path))
        s.add(space)
        s.commit()
        s.refresh(space)
        for row in range(10):
            s.add(MapPoint(space_id=space.id, kind="corpus", ref=f"r{row}",
                           label=f"doc {row}", row=row,
                           x=float(vectors[row][1]), y=float(vectors[row][2])))
        s.commit()

    default = client.get("/api/map").json()
    assert default["display"]["x"] == 1 and default["display"]["y"] == 2
    assert default["display"]["reprojected"] is False

    chosen = client.get("/api/map?x=4&y=5").json()
    assert chosen["display"] == {"x": 4, "y": 5, "reprojected": True,
                                 "available": 6}
    assert chosen["points"][0]["x"] == vectors[0][4]
    assert chosen["points"][0]["y"] == vectors[0][5]


def test_a_component_the_fit_does_not_have_falls_back(client, tmp_path):
    """Asking for component 900 of a 6-component fit is a mistake, not a
    reason to serve an empty map."""
    import numpy as np
    from sqlmodel import Session
    from app.database import _engine
    from app.models import MapPoint, MapSpace

    path = tmp_path / "s.npz"
    np.savez_compressed(path, vectors=np.zeros((4, 6)))
    with Session(_engine) as s:
        space = MapSpace(is_current=True, backend="lsa", model="m",
                         corpus_hash="h", row_count=4, dimensions=6,
                         vectors_path=str(path))
        s.add(space)
        s.commit()
        s.refresh(space)
        s.add(MapPoint(space_id=space.id, kind="corpus", ref="r", label="d", row=0))
        s.commit()

    body = client.get("/api/map?x=900&y=901").json()
    assert body["display"]["x"] == 1 and body["display"]["reprojected"] is False


# --- text repair -----------------------------------------------------------

def test_typographic_ligatures_are_decomposed():
    """This library holds 1,210 of them. Each is one codepoint, so "firms" and
    "ﬁrms" are two terms to the vectoriser, and a label pattern anchored at
    a-z reads from the second character — which is where "tness", "icult" and
    "ne-tuning" came from."""
    out = text_mod.clean("ﬁrms found di�".replace("�", "ﬀerent")
                         + " ﬁtness and diﬃcult work")
    assert "firms" in out and "different" in out
    assert "fitness" in out and "difficult" in out
    assert not any(c in out for c in "ﬀﬁﬂﬃﬄ")


def test_apparatus_sections_are_cut_from_the_text():
    """An acknowledgement names funders, a JEL line is a classification code.
    Both were labelling regions of the map."""
    out = text_mod.clean(
        "We study innovation. JEL classification: O31, L26. Keywords: creativity")
    assert out == "We study innovation."
    assert "o31" not in out.lower() and "keywords" not in out.lower()

    out = text_mod.clean("Results were clear. Acknowledgements: funded by the NSF.")
    assert out == "Results were clear."


def test_a_classification_code_is_not_stripped_from_running_text():
    """The pattern for a JEL code is also the pattern for vitamin B12."""
    out = text_mod.clean("Vitamin B12 deficiency across the O31 region of Iowa.")
    assert "B12" in out and "O31" in out


def test_words_broken_across_a_line_are_rejoined():
    assert text_mod.clean("low- income households") == "low-income households"


def test_a_suspended_compound_is_left_alone():
    """"small- and medium-sized" is correct English with a deliberate dangling
    hyphen. Rejoining it produced "small-and", which became a cluster label —
    a repair inventing the artifact it was added to remove."""
    out = text_mod.clean("small- and medium-sized enterprises")
    assert out == "small- and medium-sized enterprises"
    assert "small-and" not in out


def test_your_own_work_lands_in_the_regions_the_corpus_defined(session, tmp_path, monkeypatch):
    """Without this the areas table reads "read 99, written 0" for every row,
    and the comparison it exists to make is unavailable."""
    import numpy as np
    from app.mapping import cluster as cm

    rng = np.random.default_rng(3)
    a = np.array([0.5, 1.0, 0, 0]) + rng.normal(0, 0.02, size=(20, 4))
    b = np.array([0.5, 0, 1.0, 0]) + rng.normal(0, 0.02, size=(20, 4))
    corpus = np.vstack([a, b])
    clusters = cm.fit(corpus, dimensions=4)
    assert len(clusters) == 2

    # One work beside the first region, one pointing nowhere near either.
    placed = np.vstack([np.array([0.5, 1.0, 0.01, 0]),
                        np.array([0.5, 0, 0, 1.0])])
    vectors = np.vstack([corpus, placed])
    assigned = cm.assign_nearest(clusters, vectors, [40, 41], dimensions=4)

    assert assigned.get(40) == clusters[0].cluster, "a work near a region joins it"
    assert 41 not in assigned, (
        "a work outside every region's own spread stays unassigned rather than "
        "being filed under whichever centre happens to be nearest")


def test_the_reading_list_ranks_only_what_has_no_reading_evidence(client, tmp_path):
    """Candidates are items collected with no evidence of reading -- weaker
    than "unread", because reading elsewhere leaves no trace. Every row has to
    carry the signals that put it there."""
    import numpy as np
    from sqlmodel import Session
    from app.database import _engine
    from app.models import MapCluster, MapPoint, MapSpace

    path = tmp_path / "s.npz"
    np.savez_compressed(path, vectors=np.zeros((4, 6)))
    with Session(_engine) as s:
        space = MapSpace(is_current=True, backend="lsa", model="m",
                         corpus_hash="h", row_count=4, dimensions=6,
                         vectors_path=str(path))
        s.add(space)
        s.commit()
        s.refresh(space)
        s.add(MapCluster(space_id=space.id, cluster=0, terms='["solar"]', size=2))
        s.add(MapPoint(space_id=space.id, kind="work", ref="w", label="My paper",
                       row=0, cluster=0))
        s.add(MapPoint(space_id=space.id, kind="corpus", ref="unread",
                       label="Never opened", row=1, cluster=0,
                       reading="collected", cited_by=40, year=2026))
        s.add(MapPoint(space_id=space.id, kind="corpus", ref="done",
                       label="Already read", row=2, cluster=0,
                       reading="marked", cited_by=900, year=2026))
        s.commit()

    body = client.get("/api/status/reading").json()
    refs = [c["ref"] for c in body["candidates"]]
    assert "unread" in refs
    assert "done" not in refs, "a marked item is not offered again"

    row = next(c for c in body["candidates"] if c["ref"] == "unread")
    signals = {r["signal"] for r in row["reasons"]}
    assert "near_your_work" in signals and "recent" in signals
    # A reason either adds to the score or damps it, and says which: a
    # damper reported as "weight 0" is a ranking you cannot decompose.
    assert all(r["effect"] in ("adds", "damps") for r in row["reasons"])
    assert any(r["effect"] == "adds" and r["weight"] > 0 for r in row["reasons"])
    assert body["collected_unread"] == 1 and body["with_reading_evidence"] == 1


# --- frontier --------------------------------------------------------------

def test_a_region_resolves_to_the_topics_its_own_papers_carry():
    """Not to a keyword search over its label: "toolkits, scratch, replicating"
    is a good name for a human and a poor query."""
    from app.sources import frontier as frontier_mod

    enrichment = {
        "10.1/a": {"topics": [{"id": "T1", "name": "Solar adoption"},
                              {"id": "T2", "name": "Energy policy"}]},
        "10.1/b": {"topics": [{"id": "T1", "name": "Solar adoption"}]},
        "10.1/c": {"topics": [{"id": "T3", "name": "Something else"}]},
    }
    topics = frontier_mod.topics_for_region(["10.1/a", "10.1/b", "10.1/c"],
                                            enrichment)
    assert topics[0].topic_id == "T1"
    assert topics[0].members == 2
    assert 0.6 < topics[0].share < 0.7


def test_a_topic_too_rare_to_describe_a_region_is_not_queried():
    """One paper in ninety carrying a topic says nothing about the region, and
    querying it would return a neighbouring field's output."""
    from app.sources import frontier as frontier_mod

    enrichment = {f"10.1/{i}": {"topics": []} for i in range(20)}
    enrichment["10.1/0"] = {"topics": [{"id": "T9", "name": "Rare"}]}
    assert frontier_mod.topics_for_region(list(enrichment), enrichment) == []


def test_the_frontier_reads_without_touching_the_network(client):
    body = client.get("/api/frontier").json()
    assert body["regions"] == []
    assert "no space" in body["note"]


def test_gathering_an_empty_space_explains_rather_than_crashes(client, tmp_path):
    """No points at all is the same shape of problem as no DOIs: there is
    nothing to resolve a region by, and the endpoint says which."""
    import numpy as np
    from sqlmodel import Session
    from app.database import _engine
    from app.models import MapSpace

    path = tmp_path / "s.npz"
    np.savez_compressed(path, vectors=np.zeros((2, 4)))
    with Session(_engine) as s:
        s.add(MapSpace(is_current=True, backend="lsa", model="m",
                       corpus_hash="h", row_count=2, dimensions=4,
                       vectors_path=str(path)))
        s.commit()

    resp = client.post("/api/frontier/refresh")
    assert resp.status_code == 503
    assert "no DOIs on its points" in resp.json()["detail"]


def test_a_space_without_dois_says_which_thing_is_stale(client, tmp_path):
    """A space fitted by an earlier build carries no DOIs on its points, and
    no amount of fetching fixes that. The error named the cache, which sent
    you to the wrong page."""
    import numpy as np
    from sqlmodel import Session
    from app.database import _engine
    from app.models import MapPoint, MapSpace

    path = tmp_path / "s.npz"
    np.savez_compressed(path, vectors=np.zeros((2, 4)))
    with Session(_engine) as s:
        space = MapSpace(is_current=True, backend="lsa", model="m",
                         corpus_hash="h", row_count=2, dimensions=4,
                         vectors_path=str(path))
        s.add(space)
        s.commit()
        s.refresh(space)
        s.add(MapPoint(space_id=space.id, kind="corpus", ref="a",
                       label="No DOI", row=0, cluster=0))
        s.commit()

    resp = client.post("/api/frontier/refresh")
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "no DOIs on its points" in detail
    assert "refit the map" in detail


def test_a_dormant_region_is_damped_not_dropped(client, tmp_path):
    """A region you collected heavily and then left should stop being
    recommended, without raDash deciding for you that you will never return."""
    import numpy as np
    from sqlmodel import Session
    from app.database import _engine
    from app.models import MapCluster, MapPoint, MapSpace

    path = tmp_path / "s.npz"
    np.savez_compressed(path, vectors=np.zeros((6, 6)))
    with Session(_engine) as s:
        space = MapSpace(is_current=True, backend="lsa", model="m",
                         corpus_hash="h", row_count=6, dimensions=6,
                         vectors_path=str(path))
        s.add(space)
        s.commit()
        s.refresh(space)
        for cluster, terms in ((0, '["live"]'), (1, '["retired"]')):
            s.add(MapCluster(space_id=space.id, cluster=cluster, terms=terms,
                             size=3, lineage=f"r{cluster}"))
        # A live region: a current project brief sits in it.
        s.add(MapPoint(space_id=space.id, kind="project", ref="p",
                       label="a brief", row=0, cluster=0))
        s.add(MapPoint(space_id=space.id, kind="corpus", ref="u1",
                       label="Unread, live region", row=1, cluster=0,
                       reading="collected", year=2026))
        # A retired region: your only work there is old, and no brief.
        s.add(MapPoint(space_id=space.id, kind="work", ref="w",
                       label="An old paper", row=2, cluster=1, year=2013))
        s.add(MapPoint(space_id=space.id, kind="corpus", ref="u2",
                       label="Unread, retired region", row=3, cluster=1,
                       reading="collected", year=2026))
        s.commit()

    rows = {c["label"]: c for c in
            client.get("/api/status/reading?limit=50").json()["candidates"]}
    live = rows["Unread, live region"]
    retired = rows["Unread, retired region"]

    assert live["score"] > retired["score"], "live work outranks a retired area"
    assert any(r["signal"] == "live_project" and r["effect"] == "adds"
               for r in live["reasons"])
    assert any(r["signal"] == "dormant" and r["effect"] == "damps"
               for r in retired["reasons"])
    assert retired["score"] > 0, "damped, not dropped — you may come back"


# --- rabbitHole litmaps ----------------------------------------------------

DOT = '''// figure: litmap
digraph litmap {
  "__hub__" [label="Contribution map"];
  "__t0__" [fillcolor="#2563eb", label="Evolutionary\\nmacroeconomics"];
  "__t1__" [fillcolor="#059669", label="Distributional\\nequity"];
  "aOne2019" [label="Author et al. 2019\\nEvolutionary models handle\\nnon-linearities better.", fillcolor="#dbeafe"];
  "bTwo2020" [label="Bravo et al. 2020\\nBorder adjustments shift\\nburdens to exporters.", fillcolor="#d1fae5"];
  "aOne2019" -> "bTwo2020" [color="#47556955"];
}'''

BIB = '''@article{aOne2019,
\ttitle = {A {Paper} About Models},
\tdoi = {10.1/one},
\tyear = {2019},
}

@article{bTwo2020,
\ttitle = {Another Paper},
\tdoi = {10.1/TWO},
\tyear = {2020},
}'''


def test_a_litmap_yields_named_themes_and_written_claims():
    """The themes are named and the claims are written — neither is something
    raDash can produce for itself from term frequencies."""
    from app.sources import litmap as litmap_mod

    m = litmap_mod.parse_dot(DOT, project="p")
    assert m.counts()["nodes"] == 2, "hubs are not papers"
    assert m.counts()["edges"] == 1
    assert {t for t in m.themes.values()} == {
        "Evolutionary macroeconomics", "Distributional equity"}

    first = next(n for n in m.nodes if n.citekey == "aOne2019")
    assert first.label == "Author et al. 2019", "the citation head"
    assert first.claim.startswith("Evolutionary models handle")
    assert first.theme == "Evolutionary macroeconomics", (
        "a member is matched to its hub by the tint of its fill colour")


def test_the_bibliography_is_what_joins_a_litmap_to_your_library():
    from app.sources import litmap as litmap_mod

    refs = litmap_mod.parse_bib(BIB)
    assert refs["aOne2019"]["doi"] == "10.1/one"
    assert refs["bTwo2020"]["doi"] == "10.1/two", "DOIs are matched lowercased"
    assert refs["aOne2019"]["title"] == "A Paper About Models", "braces stripped"


def test_a_project_without_a_litmap_is_absent_not_empty(tmp_path):
    """"No litmap" and "a litmap with nothing in it" are different facts."""
    from app.sources import litmap as litmap_mod

    (tmp_path / "litReview" / "output").mkdir(parents=True)
    assert litmap_mod.load(tmp_path) is None
    assert litmap_mod.load(tmp_path / "nonexistent") is None


def test_agreement_needs_a_space_and_says_so(client):
    body = client.get("/api/map/agreement").json()
    assert body["projects"] == []
    assert "no space" in body["note"]


def test_the_reading_list_leans_the_way_your_work_has_been_moving(client, tmp_path):
    """Ordered with the drift, not against it.

    Corpus mass says where you have been. The drift says where you are going,
    and the list is for what to read *next* — so a paper lying ahead on the
    line from your older work to your recent work outranks an otherwise
    identical one sitting behind it. It is the arrow drawn on the Landscape,
    measured on the same two components, so the ranking and the picture agree.
    """
    import numpy as np
    from sqlmodel import Session
    from app.database import _engine
    from app.models import MapCluster, MapPoint, MapSpace

    path = tmp_path / "s.npz"
    np.savez_compressed(path, vectors=np.zeros((8, 6)))
    with Session(_engine) as s:
        space = MapSpace(is_current=True, backend="lsa", model="m",
                         corpus_hash="h", row_count=8, dimensions=6,
                         vectors_path=str(path))
        s.add(space)
        s.commit()
        s.refresh(space)
        s.add(MapCluster(space_id=space.id, cluster=0, terms='["solar"]', size=4))
        # Older work at the origin, recent work out along x: the drift runs +x.
        for i, year in enumerate((2012, 2013)):
            s.add(MapPoint(space_id=space.id, kind="work", ref=f"old{i}",
                           label=f"Old {year}", row=i, cluster=0,
                           x=0.0, y=0.0, year=year))
        for i, year in enumerate((2024, 2025)):
            s.add(MapPoint(space_id=space.id, kind="work", ref=f"new{i}",
                           label=f"New {year}", row=2 + i, cluster=0,
                           x=1.0, y=0.0, year=year))
        # Two unread papers, identical but for where they sit on that line.
        s.add(MapPoint(space_id=space.id, kind="corpus", ref="ahead",
                       label="Ahead of you", row=4, cluster=0,
                       x=2.0, y=0.0, reading="collected", cited_by=10,
                       year=2026))
        s.add(MapPoint(space_id=space.id, kind="corpus", ref="behind",
                       label="Behind you", row=5, cluster=0,
                       x=-1.0, y=0.0, reading="collected", cited_by=10,
                       year=2026))
        s.commit()

    body = client.get("/api/status/reading?limit=50").json()
    rows = {c["ref"]: c for c in body["candidates"]}

    assert body["drift"] is not None, "the ranking says what ordered it"
    assert body["drift"]["older"] == 2 and body["drift"]["recent"] == 2
    assert rows["ahead"]["score"] > rows["behind"]["score"]

    reasons = {r["signal"]: r for r in rows["ahead"]["reasons"]}
    assert "with_your_drift" in reasons
    assert reasons["with_your_drift"]["effect"] == "adds"
    assert "drift-lengths" in reasons["with_your_drift"]["detail"]

    # Behind you is not penalised, only unrewarded: a paper on the far side of
    # where you started is not evidence against itself.
    assert not any(r["signal"] == "with_your_drift"
                   for r in rows["behind"]["reasons"])
    assert rows["behind"]["score"] > 0


def test_without_enough_published_history_there_is_no_drift_term(client, tmp_path):
    """Four placed works spanning more than six years, or the list says it is
    leaning on where you already are instead."""
    import numpy as np
    from sqlmodel import Session
    from app.database import _engine
    from app.models import MapCluster, MapPoint, MapSpace

    path = tmp_path / "s.npz"
    np.savez_compressed(path, vectors=np.zeros((3, 6)))
    with Session(_engine) as s:
        space = MapSpace(is_current=True, backend="lsa", model="m",
                         corpus_hash="h", row_count=3, dimensions=6,
                         vectors_path=str(path))
        s.add(space)
        s.commit()
        s.refresh(space)
        s.add(MapCluster(space_id=space.id, cluster=0, terms='["solar"]', size=2))
        s.add(MapPoint(space_id=space.id, kind="work", ref="w", label="Only one",
                       row=0, cluster=0, x=0.0, y=0.0, year=2025))
        s.add(MapPoint(space_id=space.id, kind="corpus", ref="unread",
                       label="Never opened", row=1, cluster=0, x=1.0, y=0.0,
                       reading="collected", cited_by=40, year=2026))
        s.commit()

    body = client.get("/api/status/reading").json()
    assert body["drift"] is None
    assert not any(r["signal"] == "with_your_drift"
                   for c in body["candidates"] for r in c["reasons"])
