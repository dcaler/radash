"""`/api/map` — fitting the space, and finding out what this machine can do.

The benchmark exists because the deploy target is a NAS and the development
machine is not. Every timing I have for this fit was measured somewhere else,
on hardware that is not the one that has to run it weekly, and sizing the
defaults to the wrong machine is how a refresh quietly becomes a thing that
times out.

So: fit a bounded sample where the container actually runs, report what it
cost, and extrapolate. The sample is capped and the endpoint refuses to grow
past it, because a "benchmark" that takes four minutes is the problem it was
meant to detect.
"""
import os
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.config import get_config
from app.database import get_db
from app.mapping import text as text_mod
from app.mapping.backends import LsaBackend
from app.sources import collect as collect_mod
from app.sources import zotero as zotero_src

router = APIRouter(prefix="/api/map", tags=["map"])

# Past this the benchmark stops being cheap, which defeats the point of it.
MAX_SAMPLE = 400
DEFAULT_SAMPLE = 200


@router.get("/machine")
def machine():
    """What the container has to work with.

    Cheap, and worth having beside the timings: a number without the machine
    that produced it cannot be compared with a number from anywhere else.
    """
    try:
        affinity = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        affinity = None
    return {
        "cpu_count": os.cpu_count(),
        "cpus_available": affinity,
        "load_average": [round(v, 2) for v in os.getloadavg()],
    }


@router.get("/benchmark")
def benchmark(sample: int = Query(DEFAULT_SAMPLE, ge=20, le=MAX_SAMPLE),
              dimensions: int = Query(50, ge=2, le=200),
              db: Session = Depends(get_db)):
    """Fit a sample of the real corpus here, and say what a full fit would cost.

    Extrapolation is linear in documents for the vectoriser and worse than
    linear for the decomposition, so the projection is reported as a range
    with its own caveat rather than as a figure to plan against.
    """
    cfg = get_config()
    report = zotero_src.read(cfg)
    if report.data is None:
        raise HTTPException(
            503, f"the corpus is not readable: {report.note or report.status.value}")

    started = time.perf_counter()
    docs = text_mod.from_zotero(report.data)
    corpus = text_mod.assemble(docs)
    assemble_seconds = time.perf_counter() - started

    total = len(corpus.documents)
    if total < 20:
        raise HTTPException(
            503, f"only {total} documents carry enough text to fit anything")

    # Evenly spaced rather than the first N: a library sorted by anything at
    # all would otherwise be sampled from one corner of itself.
    step = max(1, total // min(sample, total))
    texts = corpus.texts[::step][:sample]

    # Warm up first. scikit-learn imports lazily and costs about fifteen
    # seconds on a cold worker, which a naive first reading attributes to the
    # fit: the first run of this endpoint reported 16s where the real cost was
    # 0.26s, and then projected a four-hundred-second corpus fit from it.
    warm_started = time.perf_counter()
    LsaBackend(min_df=1).fit_transform(texts[:20], dimensions=2)
    warm_seconds = time.perf_counter() - warm_started

    fit_started = time.perf_counter()
    fitted = LsaBackend().fit_transform(texts, dimensions=dimensions)
    fit_seconds = time.perf_counter() - fit_started

    cluster_started = time.perf_counter()
    from app.mapping import cluster as cluster_mod
    clusters = cluster_mod.fit(fitted.vectors)
    cluster_seconds = time.perf_counter() - cluster_started

    ratio = total / max(len(texts), 1)
    return {
        "machine": machine(),
        "corpus": {"usable": total, "skipped_thin": corpus.skipped_thin,
                   "sampled": len(texts)},
        "seconds": {
            "import_and_warmup": round(warm_seconds, 2),
            "read_and_assemble": round(assemble_seconds, 2),
            "fit": round(fit_seconds, 2),
            "cluster": round(cluster_seconds, 2),
        },
        "sample_fit": {
            "dimensions": fitted.dimensions,
            "terms": len(fitted.terms),
            "explained_variance": round(fitted.explained_variance or 0, 4),
            "clusters": len(clusters),
        },
        "projection": {
            "documents": total,
            "linear_seconds": round((fit_seconds + cluster_seconds) * ratio, 1),
            "superlinear_seconds": round(
                (fit_seconds + cluster_seconds) * ratio ** 1.5, 1),
            "caveat": ("The vectoriser scales about linearly in documents and "
                       "the decomposition worse, so the true cost sits between "
                       "these. Read them as an order of magnitude, not a plan."),
        },
        "note": ("Measured where the container runs; a timing from anywhere "
                 "else is about a different machine. `import_and_warmup` is "
                 "paid once per worker, not per fit — it is reported "
                 "separately rather than folded into the projection."),
    }


def _gather(cfg, db: Session, refresh: bool = False):
    """Everything the map is built from: what you read, wrote, and are doing."""
    from sqlmodel import select as _select

    from app.models import Snapshot, Work

    sources = collect_mod.collect(cfg, db, refresh=refresh)

    zot = sources.reports.get("zotero")
    oa = sources.reports.get("openalex")
    hp = sources.reports.get("haarpi")

    # OpenAlex abstracts fill what Zotero lacks, matched on DOI.
    enrichment, weights = {}, {}
    if oa is not None and oa.data is not None:
        for w in oa.data.works:
            if w.doi and w.abstract:
                enrichment[w.doi] = w.abstract
            if w.doi:
                weights[w.doi] = max(weights.get(w.doi, 0), w.cited_by_count)

    # Enrich from OpenAlex before assembling. Zotero records what a reference
    # manager was handed, which for a great many items is a title and a venue;
    # OpenAlex carries the abstract for a little over half of those same DOIs.
    # Without this the fit calls a third of the library thin and loses it over
    # a field one source happens to be missing.
    enrichment_stats = {}
    corpus_citations: dict = {}
    if zot is not None and zot.data is not None:
        missing = [i.doi for i in zot.data.items if i.doi and not i.abstract]
        if missing:
            from app.sources import openalex as openalex_src
            fetched, enrichment_stats = openalex_src.enrich_dois(missing, cfg, db)
            for doi, row in fetched.items():
                if not isinstance(row, dict):
                    continue
                if row.get("abstract"):
                    enrichment.setdefault(doi, row["abstract"])
                if row.get("cited_by") is not None:
                    corpus_citations[doi] = row["cited_by"]

    corpus_docs = (text_mod.from_zotero(zot.data, enrichment)
                   if zot is not None and zot.data is not None else [])
    # How much the field has taken up each item, for ranking a reading list.
    for d in corpus_docs:
        doi = (getattr(d, "doi", "") or "")
        if not doi:
            continue
        d.cited_by = corpus_citations.get(doi)

    # Citation weight per Zotero item, for choosing a region's exemplar.
    by_ref = {}
    if zot is not None and zot.data is not None:
        for item in zot.data.items:
            if item.doi and item.doi in weights:
                by_ref[item.key] = weights[item.doi]

    snapshot = db.exec(_select(Snapshot).where(Snapshot.is_current)).first()
    works = (db.exec(_select(Work).where(Work.snapshot_id == snapshot.id)).all()
             if snapshot else [])
    work_docs = text_mod.from_works(works, enrichment)

    project_docs = (text_mod.from_projects(hp.data.projects)
                    if hp is not None and hp.data is not None else [])

    return corpus_docs, work_docs + project_docs, by_ref, enrichment_stats


@router.post("/fit")
def fit_space(refresh: bool = False, dimensions: int = Query(100, ge=10, le=300),
              db: Session = Depends(get_db)):
    """Build a space from the corpus and place your work into it.

    Synchronous, because it is measured: about five seconds on the deploy
    target, plus a one-off import on a cold worker. If the corpus grows by an
    order of magnitude this becomes a background job; it is not one yet
    because pretending a five-second call needs a job queue is its own cost.
    """
    from app.mapping import fit as fit_mod

    cfg = get_config()
    corpus_docs, placed_docs, weights, enrichment = _gather(
        cfg, db, refresh=refresh)
    if not corpus_docs:
        raise HTTPException(503, "the corpus is not readable; nothing to fit")
    result = fit_mod.build(cfg, db, corpus_docs, placed_docs,
                           dimensions=dimensions, weights=weights)
    if result.space_id is None:
        raise HTTPException(503, result.note or "the fit produced nothing")
    return {**result.summary(), "enrichment": enrichment}


@router.get("")
def read_map(x: Optional[int] = Query(None, ge=0),
             y: Optional[int] = Query(None, ge=0),
             db: Session = Depends(get_db)):
    """The current space: its stamp, its regions, its axes, and its points.

    `x` and `y` choose which components are drawn. Any pair the fit produced
    is available without refitting, because the full vectors are kept beside
    the database and only the default pair is written into the rows — a map is
    a projection, and which projection you want depends on what you are
    looking for. Component 0 is offered like any other and is a poor choice:
    every document scores positive on it.
    """
    import json as _json

    from sqlmodel import select as _select

    from app.mapping import fit as fit_mod
    from app.models import MapAxis, MapCluster, MapPoint, MapSpace

    space = fit_mod.current(db)
    if space is None:
        return {"space": None,
                "note": "no space has been fitted — POST /api/map/fit"}

    points = db.exec(_select(MapPoint)
                     .where(MapPoint.space_id == space.id)).all()

    # Re-project onto the requested pair, from the stored array. A space whose
    # vectors could not be written keeps the pair baked into its rows, and says
    # which it is rather than silently ignoring the request.
    dx = fit_mod.DISPLAY_X if x is None else x
    dy = fit_mod.DISPLAY_Y if y is None else y
    coords, projected = {}, False
    if (dx, dy) != (fit_mod.DISPLAY_X, fit_mod.DISPLAY_Y) or x is not None:
        vectors = fit_mod.load_vectors(space)
        if vectors is not None and max(dx, dy) < vectors.shape[1]:
            coords = {p.id: (float(vectors[p.row][dx]), float(vectors[p.row][dy]))
                      for p in points if p.row < vectors.shape[0]}
            projected = True
        else:
            dx, dy = fit_mod.DISPLAY_X, fit_mod.DISPLAY_Y

    def _xy(p):
        return coords.get(p.id, (p.x, p.y))

    # Citation counts for your own work, so the map can size them. They live on
    # the ledger, not on the point: a refit must not silently freeze last
    # month's numbers into the picture.
    from app.models import Snapshot, Work
    snapshot = db.exec(_select(Snapshot).where(Snapshot.is_current)).first()
    citations = {}
    if snapshot is not None:
        for w in db.exec(_select(Work)
                         .where(Work.snapshot_id == snapshot.id)).all():
            citations[w.fingerprint] = w.citations_automated

    # Region centroids follow the projection: a centre computed on one pair of
    # components is not the centre on another.
    centres = {}
    if projected:
        for p in points:
            if p.cluster is None:
                continue
            cx, cy = _xy(p)
            acc = centres.setdefault(p.cluster, [0.0, 0.0, 0])
            acc[0] += cx
            acc[1] += cy
            acc[2] += 1
    clusters = db.exec(_select(MapCluster)
                       .where(MapCluster.space_id == space.id)).all()
    axes = db.exec(_select(MapAxis).where(MapAxis.space_id == space.id)
                   .order_by(MapAxis.component)).all()

    def _terms(raw):
        try:
            return _json.loads(raw) if raw else []
        except (ValueError, TypeError):
            return []

    return {
        # The stamp, so a number on this page can always be traced to a fit.
        "space": {
            "id": space.id, "backend": space.backend, "model": space.model,
            "created_at": space.created_at.isoformat(),
            "corpus_hash": space.corpus_hash, "rows": space.row_count,
            "dimensions": space.dimensions,
            "explained_variance": space.explained_variance,
            "fit_seconds": space.fit_seconds,
            "params": _terms(space.params) or space.params,
        },
        "points": [
            {"kind": p.kind.value, "ref": p.ref, "label": p.label,
             "year": p.year, "x": round(_xy(p)[0], 4), "y": round(_xy(p)[1], 4),
             "cluster": p.cluster, "citations": citations.get(p.ref)}
            for p in points
        ],
        "clusters": [
            {"cluster": c.cluster, "lineage": c.lineage, "name": c.label,
             "terms": _terms(c.terms), "size": c.size,
             "exemplar": c.exemplar_label, "exemplar_ref": c.exemplar_ref,
             "x": round(centres[c.cluster][0] / centres[c.cluster][2], 4)
                  if c.cluster in centres else round(c.centroid_x, 4),
             "y": round(centres[c.cluster][1] / centres[c.cluster][2], 4)
                  if c.cluster in centres else round(c.centroid_y, 4)}
            for c in sorted(clusters, key=lambda c: -c.size)
        ],
        "axes": [
            {"component": a.component, "name": a.name, "confirmed": a.confirmed,
             "positive": _terms(a.positive_terms),
             "negative": _terms(a.negative_terms),
             "explained_variance": a.explained_variance}
            for a in axes
        ],
        "unclustered": sum(1 for p in points
                           if p.kind.value == "corpus" and p.cluster is None),
        "display": {"x": dx, "y": dy, "reprojected": projected,
                    "available": space.dimensions},
        # Previous fits are retained rather than replaced, so a refit that
        # goes wrong stays comparable against the one before it.
        "previous": [
            {"id": sp.id, "created_at": sp.created_at.isoformat(),
             "rows": sp.row_count, "dimensions": sp.dimensions,
             "explained_variance": sp.explained_variance,
             "corpus_hash": sp.corpus_hash}
            for sp in db.exec(_select(MapSpace)
                              .where(MapSpace.id != space.id)
                              .order_by(MapSpace.id.desc())).all()[:5]
        ],
    }


@router.post("/axis/{component}")
def name_axis(component: int, name: Optional[str] = None,
              db: Session = Depends(get_db)):
    """Attach your name to a component, or confirm it has none worth giving.

    raDash never invents one. A component is a weighted sum of thousands of
    terms, and a plausible label anchors every later reading of the map to a
    structure the fit may not contain.
    """
    from sqlmodel import select as _select

    from app.mapping import fit as fit_mod
    from app.models import MapAxis

    space = fit_mod.current(db)
    if space is None:
        raise HTTPException(404, "no space has been fitted")
    axis = db.exec(_select(MapAxis).where(MapAxis.space_id == space.id)
                   .where(MapAxis.component == component)).first()
    if axis is None:
        raise HTTPException(404, f"no component {component} in this space")
    axis.name = (name or "").strip() or None
    axis.confirmed = True
    db.add(axis)
    db.commit()
    return {"component": component, "name": axis.name, "confirmed": True}


@router.post("/cluster/{cluster}")
def name_cluster(cluster: int, name: Optional[str] = None,
                 db: Session = Depends(get_db)):
    """Name a region. The name travels with its lineage, not its number."""
    from sqlmodel import select as _select

    from app.mapping import fit as fit_mod
    from app.models import MapCluster

    space = fit_mod.current(db)
    if space is None:
        raise HTTPException(404, "no space has been fitted")
    row = db.exec(_select(MapCluster).where(MapCluster.space_id == space.id)
                  .where(MapCluster.cluster == cluster)).first()
    if row is None:
        raise HTTPException(404, f"no region {cluster} in this space")
    row.label = (name or "").strip() or None
    db.add(row)
    db.commit()
    return {"cluster": cluster, "lineage": row.lineage, "name": row.label}


@router.get("/agreement")
def agreement(db: Session = Depends(get_db)):
    """Compare raDash's regions with rabbitHole's themes, where both exist.

    This is the closest thing to an external check the map has. rabbitHole
    grouped the same papers independently and gave each group a written name,
    so the question "do these regions correspond to anything" gets an answer
    with a number on it rather than only an eyeball at a gate.

    Read the two figures differently. **Agreement** is adjusted for chance:
    zero means the two partitions are unrelated, one means identical, and
    anything above about 0.2 on data this messy means real correspondence.
    **Concentration** is the plainer question — of a theme's papers, what share
    landed in its single most common region. A theme scattered evenly across
    six regions is one raDash has not found, whatever the index says.

    A disagreement is not automatically raDash being wrong. rabbitHole groups
    one project's reading by argument; raDash clusters the whole library by
    vocabulary. They are different questions, and the interesting case is a
    theme that holds together in one and dissolves in the other.
    """
    import json as _json

    from sklearn.metrics import adjusted_rand_score
    from sqlmodel import select as _select

    from app.mapping import fit as fit_mod

    from app.models import MapCluster, MapPoint
    from app.sources import litmap as litmap_mod

    cfg = get_config()
    space = fit_mod.current(db)
    if space is None:
        return {"projects": [], "note": "no space has been fitted"}

    points = db.exec(_select(MapPoint).where(MapPoint.space_id == space.id)).all()
    by_doi = {p.doi: p for p in points if p.doi and p.kind.value == "corpus"}
    regions = {c.cluster: _json.loads(c.terms or "[]")
               for c in db.exec(_select(MapCluster)
                                .where(MapCluster.space_id == space.id)).all()}

    out = []
    for lm in litmap_mod.load_all(cfg.projects_dir):
        pairs = [(n.theme, by_doi[n.doi].cluster)
                 for n in lm.nodes
                 if n.theme and n.doi and n.doi in by_doi
                 and by_doi[n.doi].cluster is not None]
        matched = len([n for n in lm.nodes if n.doi and n.doi in by_doi])
        if len(pairs) < 8:
            out.append({
                "project": lm.project, "nodes": len(lm.nodes),
                "in_your_library": matched, "compared": len(pairs),
                "note": ("too few of these papers are placed on the map to "
                         "compare — most are unread, thin, or not in Zotero"),
            })
            continue

        themes = [t for t, _ in pairs]
        clusters = [c for _, c in pairs]
        index = float(adjusted_rand_score(themes, clusters))

        per_theme = {}
        for theme in sorted(set(themes)):
            here = [c for t, c in pairs if t == theme]
            counts: dict = {}
            for c in here:
                counts[c] = counts.get(c, 0) + 1
            best, n = max(counts.items(), key=lambda kv: kv[1])
            per_theme[theme] = {
                "papers": len(here), "regions": len(counts),
                "concentration": round(n / len(here), 3),
                "main_region": best,
                "main_region_terms": regions.get(best, [])[:3],
            }

        out.append({
            "project": lm.project, "nodes": len(lm.nodes),
            "in_your_library": matched, "compared": len(pairs),
            "themes": len(set(themes)), "regions_touched": len(set(clusters)),
            "agreement": round(index, 3),
            "mean_concentration": round(
                sum(t["concentration"] for t in per_theme.values())
                / len(per_theme), 3),
            "per_theme": per_theme,
        })

    out.sort(key=lambda r: -(r.get("compared") or 0))
    return {
        "projects": out,
        "note": ("Agreement is adjusted for chance: 0 is unrelated, 1 is "
                 "identical. Concentration is the share of a theme's papers "
                 "landing in its most common region. Disagreement is not "
                 "automatically raDash being wrong — rabbitHole groups one "
                 "project's reading by argument, raDash clusters the whole "
                 "library by vocabulary."),
    }
