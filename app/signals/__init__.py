"""Component 3 — the four opportunity signals, scored separately.

Not one blended number (`DESIGN.md` §4). Four signals, each independently
scored and independently inspectable, because they call for different
responses: something you have read and never written is a thing to write,
while something the field is busy with and you have not touched is a thing to
read into first. Averaging them would produce a ranking whose top row nobody
could act on.

The three modules split along the same line the milestone does.

- `regions` gathers the facts — what sits in each region, how much of it you
  have read, what you have published near it, and what the frontier returned.
  No judgement, only counting, so a score can always be traced to a count.
- `signals` turns those facts into four scores, each a weighted sum of named
  inputs with its own gate. A score nobody can decompose is a ranking nobody
  can argue with, so every input travels with its value and its contribution.
- `assemble` builds the candidate a person reads: the evidence behind the
  score, the nearest thing you have written, the bridge attribute, the honest
  counter-case, and where the candidate routes.

Nothing here is stored. Every number is recomputed from the current space and
the current frontier, which is what makes "candidates reproduce" true by
construction rather than by a cache that has to be kept honest.
"""
