"""Axis poles, read straight off the term loadings — M3-T7.

No naming pass, deliberately. An SVD component is a weighted sum of thousands
of terms; the honest description of it is the terms that weigh most at each
end. Asking a model to summarise those into a phrase produces something that
reads well and asserts a structure the fit may not contain — and once a
plausible name is attached, every later reading of the map is anchored to it.
`BUILD_PLAN.md` lists that as a named risk, with the response "ship unnamed
axes; a numbered component beats a confabulated label".

So a component reports its strongest positive and negative terms and its share
of variance, and you may attach a name at the gate. Yours is kept; none is
invented.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class Axis:
    component: int
    positive: list[str] = field(default_factory=list)
    negative: list[str] = field(default_factory=list)
    explained_variance: Optional[float] = None

    def as_dict(self) -> dict:
        return {"component": self.component, "positive": self.positive,
                "negative": self.negative,
                "explained_variance": self.explained_variance}


def poles(fitted, components: int = 6, terms_per_pole: int = 8) -> list[Axis]:
    """The terms loading most strongly at each end of the first components.

    A backend with no vocabulary has no loadings and returns axes with no
    terms rather than invented ones. Nothing ships in that state today — the
    embedding backend was cut — but the branch stays, because a space you
    cannot read is a different proposition from one you can, and that should
    be visible rather than assumed away.
    """
    if fitted.loadings is None or not fitted.terms:
        return [Axis(component=i, explained_variance=_variance(fitted, i))
                for i in range(min(components, fitted.dimensions))]

    vocab = np.asarray(fitted.terms)
    out: list[Axis] = []
    for i in range(min(components, fitted.loadings.shape[0])):
        row = fitted.loadings[i]
        order = np.argsort(row)
        negative = [str(t) for t in vocab[order[:terms_per_pole]]]
        positive = [str(t) for t in vocab[order[-terms_per_pole:]][::-1]]
        out.append(Axis(component=i, positive=positive, negative=negative,
                        explained_variance=_variance(fitted, i)))
    return out


def _variance(fitted, i: int) -> Optional[float]:
    if i < len(fitted.component_variance):
        return round(float(fitted.component_variance[i]), 5)
    return None


def scree(fitted, limit: int = 40) -> list[float]:
    """Variance per component, for the strip beside the map.

    The shape of this is the honest summary of how much structure the fit
    found. A curve that flattens immediately means the first two axes are most
    of what there is; one that never flattens means a two-dimensional picture
    is a poor account of the space, and the map should be read knowing that.
    """
    return [round(float(v), 5) for v in fitted.component_variance[:limit]]
