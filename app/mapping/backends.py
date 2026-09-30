"""Turning text into vectors — M3-T1.

One interface, one implementation, and a decision recorded rather than left
open.

**TF-IDF with truncated SVD is the backend.** It fits this corpus in seconds,
needs no model server, and is reproducible from the corpus alone — re-run it
next year and you get the same space, where an embedding model can be retired
or revised underneath you and leave last year's map quietly incomparable with
this year's.

**An embedding backend was planned and has been cut.** Not on the timings, on
the architecture: raDash deploys to a NAS and the only GPU on the network is a
different machine that sleeps. LSA can refit from the corpus alone, but an
embedding space *cannot place a new paper without the model server*, and every
weekly refresh brings new items. That makes the dependency permanent and
per-refresh rather than a one-off job whose vectors you cache — the wrong
shape for a dashboard whose premise is degrading gracefully when things are
unreachable. The M1-T9 spike had already removed the reason to want it: the
plan's trigger for revisiting embeddings was a projected corpus fit over six
hours, and the measurement was eight seconds.

The `Backend` protocol stays, so the door is open if the corpus outgrows this
or the deployment target changes. The interface is deliberately narrow:
`fit_transform` to build a space, `transform` to place something new into an
existing one. Anything that cannot do both does not belong here.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

import numpy as np


@dataclass
class Fitted:
    """A fitted space and everything needed to describe it."""
    vectors: "np.ndarray"                       # rows × dimensions
    backend: str
    model: str
    params: dict = field(default_factory=dict)
    explained_variance: Optional[float] = None
    component_variance: list[float] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)      # vocabulary, if any
    loadings: Optional["np.ndarray"] = None             # components × terms
    seconds: float = 0.0

    @property
    def dimensions(self) -> int:
        return int(self.vectors.shape[1]) if self.vectors.size else 0


class Backend(Protocol):
    name: str

    def fit_transform(self, texts: list[str], dimensions: int) -> Fitted: ...

    def transform(self, texts: list[str]) -> "np.ndarray": ...


class LsaBackend:
    """TF-IDF over 1–2 grams, then truncated SVD.

    `min_df=3` drops terms appearing in fewer than three documents, which is
    most of the vocabulary and nearly all of the noise: a term used once
    cannot relate two items. `max_df=0.5` drops terms appearing in over half
    the corpus, which in a single author's library means the words that
    describe their whole field and therefore separate nothing within it.
    `sublinear_tf` stops a term repeated twenty times in one abstract counting
    twenty times as much as one used once.
    """

    name = "lsa"

    def __init__(self, min_df: int = 3, max_df: float = 0.5,
                 ngram_range: tuple = (1, 2), random_state: int = 0):
        self.params = {"min_df": min_df, "max_df": max_df,
                       "ngram_range": list(ngram_range),
                       "random_state": random_state, "sublinear_tf": True}
        self._vectoriser = None
        self._svd = None

    def fit_transform(self, texts: list[str], dimensions: int = 100) -> Fitted:
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer

        started = time.perf_counter()
        matrix = None
        # `max_df` drops terms shared by more than half the corpus, which on a
        # small or very homogeneous set can be *every* term. Relaxing beats
        # raising: a space built from a wider vocabulary is still a space, and
        # the alternative is no map at all for a narrow library.
        for max_df, min_df in ((self.params["max_df"], self.params["min_df"]),
                               (0.9, self.params["min_df"]), (1.0, 1)):
            self._vectoriser = TfidfVectorizer(
                stop_words="english", min_df=min_df, max_df=max_df,
                ngram_range=tuple(self.params["ngram_range"]),
                sublinear_tf=True)
            try:
                matrix = self._vectoriser.fit_transform(texts)
            except ValueError:
                continue
            if matrix.shape[1] >= 2:
                self.params = {**self.params, "max_df": max_df, "min_df": min_df}
                break
        if matrix is None or matrix.shape[1] < 2:
            raise ValueError(
                "the corpus yields no usable vocabulary; every term is either "
                "too rare or shared by every document")

        # More components than documents or terms is not a meaningful request.
        k = max(2, min(dimensions, matrix.shape[0] - 1, matrix.shape[1] - 1))
        self._svd = TruncatedSVD(n_components=k,
                                 random_state=self.params["random_state"])
        vectors = self._svd.fit_transform(matrix)

        ratios = [float(v) for v in self._svd.explained_variance_ratio_]
        # Summing floats drifts: on one machine these total 0.9999999999999998
        # and on another 1.0000000000000007. Neither is a different answer, and
        # a figure over 1 is not one to report.
        total_variance = min(1.0, float(sum(ratios)))
        return Fitted(
            vectors=vectors, backend=self.name,
            model=f"tfidf+svd:{k}", params={**self.params, "dimensions": k},
            explained_variance=total_variance,
            component_variance=ratios,
            terms=list(self._vectoriser.get_feature_names_out()),
            loadings=self._svd.components_,
            seconds=time.perf_counter() - started,
        )

    def transform(self, texts: list[str]) -> "np.ndarray":
        if self._vectoriser is None or self._svd is None:
            raise RuntimeError("fit_transform must be called before transform")
        return self._svd.transform(self._vectoriser.transform(texts))


def get_backend(name: Optional[str] = None) -> Backend:
    """The backend. One, for now — see the module docstring for why."""
    chosen = (name or "lsa").strip().lower()
    if chosen != "lsa":
        raise ValueError(
            f"unknown map backend {chosen!r}; raDash builds its space with "
            "TF-IDF and truncated SVD, in process")
    return LsaBackend()
