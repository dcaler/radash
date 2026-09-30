"""Building a space, and stamping it — M3-T4, M3-T5.

**The corpus defines the space; your work is placed into it.** The fit runs on
what you have *read*, and your publications and project briefs are then
transformed into that space rather than helping to build it. This is the
decision that makes the drift panel mean anything: if your own twenty works
helped define the axes, then asking where they sit relative to the field is
partly asking where they sit relative to themselves, and a change in your
output would move the map underneath the question.

**Every space is stamped.** Backend, model, parameters, corpus hash, row
count, dimensions, time taken. A space whose corpus hash does not match the
corpus in front of it is stale as a matter of fact, not of judgement — which
is what lets the map say "this was fitted on a library you have since changed"
instead of quietly answering from last month's structure.

Vectors are written beside the database rather than into it. A hundred
dimensions across a few thousand documents is a large array and a poor row;
the database keeps the display coordinates and the cluster assignments, which
is all the panels read.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
from sqlmodel import Session, select

from app.config import Config
from app.mapping import axes as axes_mod
from app.mapping import cluster as cluster_mod
from app.mapping import text as text_mod
from app.mapping.backends import Backend, get_backend
from app.models import (
    MapAxis, MapCluster, MapPoint, MapPointKind, MapSpace,
)

DEFAULT_DIMENSIONS = 100

# Which components the map is drawn on, and why not the first.
#
# TruncatedSVD does not centre the data, and a TF-IDF matrix is non-negative,
# so the leading component points along the *mean document* direction: every
# document in this corpus scores positive on it, mean 0.154 against a standard
# deviation of 0.041. It has the largest singular value and almost no variance,
# because it separates nothing — it measures how typical a document is, not
# what it is about. Plotting it puts "ordinariness" on the x-axis of a map
# meant to show subject.
#
# Components are still reported from zero, since the axes panel should show
# what the fit contains; only the drawing skips it.
DISPLAY_X = 1
DISPLAY_Y = 2

# How many components are described well enough to be offered as an axis.
DISPLAY_COMPONENTS = 16

# Where the vector arrays live: inside raDash's own writable volume, beside
# the database, never near a source.
VECTORS_DIR = "spaces"


@dataclass
class FitResult:
    space_id: Optional[int] = None
    corpus_counts: dict = field(default_factory=dict)
    clusters: int = 0
    noise: int = 0
    axes: list = field(default_factory=list)
    scree: list = field(default_factory=list)
    explained_variance: Optional[float] = None
    seconds: float = 0.0
    note: Optional[str] = None

    def summary(self) -> dict:
        return {
            "space_id": self.space_id,
            "corpus": self.corpus_counts,
            "clusters": self.clusters,
            "unclustered": self.noise,
            "explained_variance": self.explained_variance,
            "scree": self.scree,
            "axes": [a.as_dict() for a in self.axes],
            "seconds": round(self.seconds, 2),
            "note": self.note,
        }


def build(cfg: Config, session: Session, corpus_docs: list,
          placed_docs: Optional[list] = None,
          dimensions: int = DEFAULT_DIMENSIONS,
          backend: Optional[Backend] = None,
          weights: Optional[dict] = None) -> FitResult:
    """Fit a space on `corpus_docs` and place `placed_docs` into it."""
    result = FitResult()
    placed_docs = placed_docs or []

    corpus = text_mod.assemble(corpus_docs)
    result.corpus_counts = corpus.counts()
    if len(corpus.documents) < 20:
        result.note = (f"only {len(corpus.documents)} documents carry enough "
                       "text to fit a space; nothing was built")
        return result

    engine = backend or get_backend()
    fitted = engine.fit_transform(corpus.texts, dimensions=dimensions)
    result.seconds = fitted.seconds
    result.explained_variance = fitted.explained_variance

    space = MapSpace(
        backend=fitted.backend, model=fitted.model,
        params=json.dumps(fitted.params),
        corpus_hash=corpus.hash, row_count=len(corpus.documents),
        dimensions=fitted.dimensions,
        explained_variance=fitted.explained_variance,
        fit_seconds=round(fitted.seconds, 3),
    )
    session.add(space)
    session.commit()
    session.refresh(space)
    result.space_id = space.id

    # Your own work and your project briefs enter the space without shaping it.
    placed = text_mod.assemble(placed_docs, include_thin=True)
    placed_vectors = (engine.transform(placed.texts)
                      if placed.documents else np.empty((0, fitted.dimensions)))

    vectors = (np.vstack([fitted.vectors, placed_vectors])
               if len(placed_vectors) else fitted.vectors)
    documents = corpus.documents + placed.documents

    space.vectors_path = _save_vectors(cfg, space.id, vectors)
    # The fitted vectoriser, so text that arrives later — a frontier paper, a
    # new project brief — can enter this space without refitting it. A refit
    # would place it in a *different* space and call the coordinates
    # comparable, which they would not be.
    _save_model(cfg, space.id, engine)

    clusters = cluster_mod.fit(fitted.vectors)
    for c in clusters:
        c.refs = [corpus.documents[r].ref for r in c.members]
    cluster_mod.label_clusters(clusters, corpus.texts)
    cluster_mod.choose_exemplars(
        clusters, [float((weights or {}).get(d.ref, 0)) for d in corpus.documents])
    cluster_mod.assign_lineage(clusters, _previous_lineage(session, space.id))

    by_row = {row: c.cluster for c in clusters for row in c.members}
    # Your own work lands in the regions the corpus defined, where it falls
    # inside one. Without this the areas table reads "read 99, written 0" for
    # every row, and the comparison it exists to make is unavailable.
    placed_rows = list(range(len(corpus.documents), len(documents)))
    by_row.update(cluster_mod.assign_nearest(clusters, vectors, placed_rows))
    result.clusters = len(clusters)
    result.noise = len(corpus.documents) - sum(c.size for c in clusters)

    for row, doc in enumerate(documents):
        session.add(MapPoint(
            space_id=space.id, kind=MapPointKind(doc.kind), ref=doc.ref,
            label=doc.label, year=doc.year,
            x=float(vectors[row][DISPLAY_X]), y=float(vectors[row][DISPLAY_Y]),
            row=row, cluster=by_row.get(row),
            reading=doc.reading, cited_by=doc.cited_by, doi=doc.doi,
        ))

    for c in clusters:
        exemplar = corpus.documents[c.exemplar_row] if c.exemplar_row is not None else None
        session.add(MapCluster(
            space_id=space.id, cluster=c.cluster, lineage=c.lineage,
            terms=json.dumps(c.terms), size=c.size,
            exemplar_ref=exemplar.ref if exemplar else None,
            exemplar_label=exemplar.label if exemplar else None,
            centroid_x=float(c.centroid[DISPLAY_X]) if c.centroid is not None else 0.0,
            centroid_y=float(c.centroid[DISPLAY_Y]) if c.centroid is not None else 0.0,
        ))

    # Enough components to choose a projection from, not just to read the
    # default one. Each is cheap: a handful of terms off the loadings.
    result.axes = axes_mod.poles(fitted, components=DISPLAY_COMPONENTS)
    result.scree = axes_mod.scree(fitted)
    names = _previous_axis_names(session, space.id)
    for axis in result.axes:
        session.add(MapAxis(
            space_id=space.id, component=axis.component,
            positive_terms=json.dumps(axis.positive),
            negative_terms=json.dumps(axis.negative),
            explained_variance=axis.explained_variance,
            name=names.get(axis.component),
        ))

    for old in session.exec(select(MapSpace).where(MapSpace.is_current)).all():
        old.is_current = False
        session.add(old)
    space.is_current = True
    session.add(space)
    session.commit()
    return result


def _save_vectors(cfg: Config, space_id: int, vectors: "np.ndarray") -> Optional[str]:
    """Write the array beside the database. A failure costs the array, not the fit."""
    try:
        directory = Path(cfg.output_dir).parent / VECTORS_DIR
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"space_{space_id}.npz"
        np.savez_compressed(path, vectors=vectors)
        return str(path)
    except OSError:
        return None


def _model_path(cfg: Config, space_id: int) -> Path:
    return Path(cfg.output_dir).parent / VECTORS_DIR / f"space_{space_id}.model"


def _save_model(cfg: Config, space_id: int, engine) -> None:
    """Persist the fitted backend. A failure costs projection, not the fit."""
    try:
        import joblib

        path = _model_path(cfg, space_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(engine, path)
    except Exception:
        pass


def load_model(cfg: Config, space: MapSpace):
    """The backend that built this space, if it can still be loaded.

    Returns None rather than raising when the file is missing or was written
    by a different scikit-learn. A space you cannot add to is still a space
    you can read, and the caller says so instead of failing.
    """
    try:
        import joblib

        path = _model_path(cfg, space.id)
        if not path.exists():
            return None
        return joblib.load(path)
    except Exception:
        return None


def load_vectors(space: MapSpace) -> Optional["np.ndarray"]:
    if not space.vectors_path:
        return None
    try:
        with np.load(space.vectors_path) as data:
            return data["vectors"]
    except (OSError, KeyError, ValueError):
        return None


def _previous_lineage(session: Session, current_id: int) -> dict:
    """Region memberships from the last space, for lineage matching."""
    previous = session.exec(
        select(MapSpace).where(MapSpace.id != current_id)
        .order_by(MapSpace.id.desc())).first()
    if previous is None:
        return {}
    out: dict[str, list[str]] = {}
    for c in session.exec(select(MapCluster)
                          .where(MapCluster.space_id == previous.id)).all():
        refs = session.exec(
            select(MapPoint).where(MapPoint.space_id == previous.id)
            .where(MapPoint.cluster == c.cluster)).all()
        if c.lineage:
            out[c.lineage] = [p.ref for p in refs]
    return out


def _previous_axis_names(session: Session, current_id: int) -> dict:
    """Axis names you gave at a previous gate, carried forward.

    A refit renumbers nothing about the components, so a name attached to
    component 2 still describes component 2 — but the terms may have moved
    under it, which is why the gate re-confirms rather than assuming.
    """
    previous = session.exec(
        select(MapSpace).where(MapSpace.id != current_id)
        .order_by(MapSpace.id.desc())).first()
    if previous is None:
        return {}
    return {a.component: a.name for a in session.exec(
        select(MapAxis).where(MapAxis.space_id == previous.id)).all() if a.name}


def current(session: Session) -> Optional[MapSpace]:
    return session.exec(select(MapSpace).where(MapSpace.is_current)).first()


def is_stale(space: Optional[MapSpace], corpus_hash: str) -> bool:
    """A space fitted on a corpus that has since changed is stale as a fact."""
    return bool(space and corpus_hash and space.corpus_hash != corpus_hash)
