"""Regions of the space, and what distinguishes them — M3-T6, M3-T8.

HDBSCAN rather than k-means, for one reason that matters here: it does not
require a number of clusters, and it is allowed to say *noise*. A research
library is not a tidy partition — it has dense areas, sparse areas, and items
that genuinely sit between things. k-means would assign every one of them to a
region and report a clean structure that is partly invented.

Labels are the terms that *separate* a region from the rest of the corpus, not
the terms most common inside it: "model", "policy" and "energy" are frequent
everywhere in this library and distinguish nothing.

Lineage (M3-T8) exists because cluster numbers are meaningless across refits.
HDBSCAN may return the same region as cluster 4 this week and cluster 1 next,
and without a stable identity every refit would read as wholesale upheaval.
Regions are matched to the previous fit by membership overlap, so a region
that keeps most of its items keeps its identity — and the name you gave it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from app import settings

# Below this, a "region" is a handful of items that happen to sit together and
# naming it would overstate what is there.
MIN_CLUSTER_SIZE = 8

# How many components clustering sees. Measured on the real corpus: 100 leaves
# two-thirds of items between regions, 10 collapses the library into two.
CLUSTER_DIMS = 40

# How much of a region's membership must carry over for it to be the same
# region. Below half, calling it the same thing is a claim, not a match.
LINEAGE_OVERLAP = 0.5

# A label term must be at least this much more common inside a region than
# across the corpus. At 1.0 a term is merely present, not characteristic.
MIN_TERM_LIFT = 1.25

_WORD = re.compile(r"[a-z][a-z0-9\-]{2,}")


@dataclass
class Cluster:
    cluster: int
    members: list[int] = field(default_factory=list)      # row indices
    refs: list[str] = field(default_factory=list)         # stable item ids
    terms: list[str] = field(default_factory=list)
    exemplar_row: Optional[int] = None
    centroid: Optional["np.ndarray"] = None
    lineage: Optional[str] = None

    @property
    def size(self) -> int:
        return len(self.members)


def clustering_space(vectors: "np.ndarray",
                     dimensions: Optional[int] = None) -> "np.ndarray":
    """The sub-space the clustering actually ran in, normalised.

    Shared rather than repeated, because three callers now need to measure
    distance in the same geometry the regions were found in — the fit, the
    placement of your own work, and the signals that ask how far a region sits
    from your nearest publication. Two different notions of "the clustering
    space" would put a region at two different distances from the same paper.

    Component 0 is dropped for the reason `fit` gives: it points along the
    average document and separates nothing.
    """
    from sklearn.preprocessing import normalize

    dimensions = int(dimensions or settings.num("CLUSTER_DIMS", CLUSTER_DIMS))
    width = max(3, min(dimensions + 1, vectors.shape[1]))
    return normalize(vectors[:, 1:width])


def fit(vectors: "np.ndarray", min_cluster_size: Optional[int] = None,
        dimensions: Optional[int] = None) -> list[Cluster]:
    """Cluster the fitted space. Noise stays noise.

    Two choices here change the answer more than the algorithm does, and both
    were measured on the real corpus rather than assumed.

    **Vectors are L2-normalised first.** LSA components are a cosine geometry,
    and HDBSCAN measures euclidean distance; without normalising, document
    *length* competes with document *topic* for the algorithm's attention. On
    1,737 documents at 100 dimensions this moved the result from 8 regions
    with 88% of items unassigned to 22 regions with 66% — the same corpus,
    the same algorithm, a different question asked of it.

    **Dimensionality trades granularity against coverage.** At 100 dimensions
    distances concentrate and most items fall between regions; at 10 the space
    collapses into two continents and nothing is unassigned. Neither is wrong,
    and the useful setting depends on whether you want a few large areas or
    many small ones — which is a judgement about your own work, so it is a
    setting rather than a constant.
    """
    from sklearn.cluster import HDBSCAN

    min_cluster_size = int(min_cluster_size
                           or settings.num("MIN_CLUSTER_SIZE", MIN_CLUSTER_SIZE))

    if len(vectors) < min_cluster_size * 2:
        return []
    # Component 0 is dropped here as it is on the map. The decomposition does
    # not centre the data and TF-IDF is non-negative, so the leading component
    # points along the average document: every item scores positive on it and
    # it separates nothing. Including it gives the clustering a dimension that
    # measures how typical a text is.
    #
    # True centring is the textbook fix and is not affordable here: it would
    # densify a 1,735 x 11,337 sparse matrix on a two-core NAS. Dropping the
    # mean direction is the cheap equivalent of subtracting the mean, and the
    # rest of the space is unchanged.
    space = clustering_space(vectors, dimensions)
    labels = HDBSCAN(min_cluster_size=min_cluster_size).fit_predict(space)

    clusters: dict[int, Cluster] = {}
    for row, label in enumerate(labels):
        label = int(label)
        if label < 0:
            continue          # noise: an item between regions, left there
        clusters.setdefault(label, Cluster(cluster=label)).members.append(row)
    for c in clusters.values():
        c.centroid = vectors[c.members].mean(axis=0)
    return [clusters[k] for k in sorted(clusters)]


def label_clusters(clusters: list[Cluster], texts: list[str],
                   terms_per_cluster: int = 6) -> None:
    """Attach the terms that distinguish each region from the rest.

    Distinguishing, not frequent: a term common inside a region *and*
    everywhere else says nothing about the region. The score is the region's
    share of the term against the corpus's share, so a word that is ordinary
    overall and heavy here rises to the top.
    """
    if not clusters:
        return
    total = max(len(texts), 1)
    corpus_df: dict[str, int] = {}
    per_doc: list[set[str]] = []
    for text in texts:
        words = set(_WORD.findall(text.lower()))
        per_doc.append(words)
        for w in words:
            corpus_df[w] = corpus_df.get(w, 0) + 1

    for c in clusters:
        inside: dict[str, int] = {}
        for row in c.members:
            for w in per_doc[row]:
                inside[w] = inside.get(w, 0) + 1
        scored = []
        for w, n in inside.items():
            if n < 2 or corpus_df.get(w, 0) < 3:
                continue
            here = n / max(c.size, 1)
            everywhere = corpus_df[w] / total
            lift = here / max(everywhere, 1e-9)
            # No lift means the term is as common outside the region as in it.
            # "energy" in an energy library describes everything and separates
            # nothing, and listing it pads a label with noise.
            if lift < MIN_TERM_LIFT:
                continue
            scored.append((lift, here, w))
        scored.sort(reverse=True)
        c.terms = [w for _, _, w in scored[:terms_per_cluster]]


def choose_exemplars(clusters: list[Cluster], weights: list[float]) -> None:
    """Pick the most-cited item in each region as its exemplar.

    Most cited rather than most central: the centre of a region is a synthetic
    point nothing sits on, while the paper everyone in the field has read is
    the one that tells you what the region actually is.
    """
    for c in clusters:
        if not c.members:
            continue
        c.exemplar_row = max(
            c.members, key=lambda r: weights[r] if r < len(weights) else 0.0)


def assign_lineage(clusters: list[Cluster],
                   previous: Optional[dict] = None) -> None:
    """Carry region identity across refits by membership overlap.

    `previous` maps a lineage id to the refs it contained last time. A region
    inheriting at least half of a previous region's membership *is* that
    region; anything else is new and gets a fresh id. Each previous region can
    be inherited once, so a split does not produce two claimants to one name.
    """
    previous = previous or {}
    taken: set[str] = set()
    # Strongest claims first, so a split region's larger half inherits.
    ordered = sorted(clusters, key=lambda c: -c.size)
    for c in ordered:
        members = set(c.refs)
        best, best_score = None, 0.0
        for lineage, old in previous.items():
            if lineage in taken or not old:
                continue
            overlap = len(members & set(old)) / max(len(set(old)), 1)
            if overlap > best_score:
                best, best_score = lineage, overlap
        if best is not None and best_score >= LINEAGE_OVERLAP:
            taken.add(best)
            c.lineage = best
        else:
            c.lineage = f"r{abs(hash(frozenset(members))) % 10 ** 8:08d}"


def assign_nearest(clusters: list[Cluster], vectors: "np.ndarray",
                   rows: list[int], dimensions: Optional[int] = None) -> dict:
    """Place already-fitted items into the regions the corpus defined.

    Your own works and project briefs are transformed into the space rather
    than clustered with it — the corpus defines the regions, and a handful of
    your papers should not be able to create one. But they still have to land
    *somewhere* for "read 99, written 0" to stop being every row of the areas
    table.

    Nearest centroid, with a radius: a work is assigned to a region only if it
    falls within that region's own spread, measured as the furthest its
    members sit from their centre. Outside every radius it stays unassigned,
    which is the honest answer for a paper that genuinely sits between fields.
    """
    if not clusters or not len(rows):
        return {}
    # Must match `fit` above — two different notions of the clustering space
    # would put a work in a region it does not sit in.
    space = clustering_space(vectors, dimensions)

    centres, radii = [], []
    for c in clusters:
        members = space[c.members]
        centre = members.mean(axis=0)
        centres.append(centre)
        radii.append(float(np.linalg.norm(members - centre, axis=1).max()))
    centres = np.vstack(centres)

    out: dict[int, int] = {}
    for row in rows:
        if row >= space.shape[0]:
            continue
        distances = np.linalg.norm(centres - space[row], axis=1)
        nearest = int(distances.argmin())
        if distances[nearest] <= radii[nearest]:
            out[row] = clusters[nearest].cluster
    return out
