"""The map: where your work sits relative to itself.

Three things are kept apart here, because conflating them is how a map starts
lying:

**The text** an item is represented by (`text.py`) is a choice, not a given.
An abstract, a venue and a title say different things, and an item with no
abstract is not a point near the origin — it is an item the map cannot place.

**The backend** (`backends.py`) turns text into vectors: TF-IDF with truncated
SVD, in process, because it is seconds of CPU, reproducible from the corpus
alone, and needs no model server. An embedding backend was planned and cut —
`backends.py` records why.

**The fit** (`fit.py`) is a specific run, stamped with everything needed to
say whether it still applies: backend, model, parameters, corpus hash, row
count, time. A space whose stamp does not match the corpus in front of it is
stale by definition rather than by somebody's judgement.

What this package deliberately does not do is name things. Cluster labels are
the terms that distinguish a region; axis poles are the terms that load on a
component. Both are read off the arithmetic and shown as such, because a
plausible-sounding label invites belief in structure the fit may not contain.
"""
