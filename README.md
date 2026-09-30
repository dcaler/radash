<p align="center">
  <img src="raDash_logo.png" alt="raDash logo: a radish reading a dashboard" width="220">
</p>

# raDash

A research portfolio observatory. It reads your reference library, your project
folders and the public indexes, and reports where your reading, your writing and
the literature actually stand relative to one another.

**It is built for [HAARPi](https://github.com/dcaler/haarpi) and
[trundlr](https://github.com/dcaler/trundlr)** and reads their files and API
directly. It runs without them, but the project layer — what you are working on
now, and everything that leans on it — goes dark. See
[Built for HAARPi and trundlr](#built-for-haarpi-and-trundlr) before you install.

**It writes to nothing it observes.** Every source is mounted read-only, and the
one test that matters asserts it: raDash writes only inside its own data volume,
on every code path. The two things it emits are text files in `output/`, which
you copy from by hand — that copy-paste is the price of raDash never being able
to set work in motion off a score nobody has checked.

## What it does

**A canonical works ledger.** Your publication record assembled from sources that
disagree about it — two OpenAlex profiles, Semantic Scholar, Google Scholar, your
CV page, your own folders — with the disagreements surfaced as proposals you rule
on. A ruling is not snapshot-scoped: a refresh may replace every number and never
a decision.

**A map of what you read and what you write.** A TF-IDF/SVD space fitted on your
library, with your own works and project briefs projected *into* it rather than
helping to build it, so "where does my work sit relative to the field" is not
partly a question about itself. Regions come from HDBSCAN, which is allowed to
say *noise* — a research library is not a tidy partition.

**Four opportunity signals, scored separately and never blended.** Read but never
written; field-active but absent; thin literature with strong grounding; thin
reading. They call for different responses, so averaging them would produce a top
row nobody could act on. Every candidate carries its inputs, its evidence, and
the honest case against it.

**A frontier feed.** Each region resolves to the OpenAlex topics its own papers
carry, and recent work in those topics is projected back into your space — the
distance from the region is the check that the topic was a fair proxy.

**Two outputs.** A read-in brief, or a corpus fill list. Which one a candidate
routes to is decided by the signal that produced it: an unconfirmed signal 3 is a
gap in your collecting rather than in the field, and the honest response to a
reading gap is reading.

## The stance

Every number carries its own age and its own source. Nothing is hardcoded into a
coverage panel, because a reassuring constant would be worse than no panel —
it would be believed. Where two citation lanes disagree they are shown side by
side and never averaged: the gap between them is the honest bound on both.

Unreachable is a normal state. Sources degrade to cache with the age attached,
and `Offline` is a mode you can turn on deliberately.

## Built for HAARPi and trundlr

raDash is the observatory for one researcher's toolchain, and it reads that
toolchain's formats rather than a general standard:

- **[HAARPi](https://github.com/dcaler/haarpi)**, the research pipeline. Each
  project folder's `haarpi.yaml` supplies the brief raDash places on the map
  and the trundlr id it joins on; `.haarpi/corpus_ledger.json` lists the
  project's reading.
- **rabbitHole**, HAARPi's literature-review stage. Its
  `litReview/output/*_litmap_ra.dot` theme maps are the independent grouping
  raDash checks its own regions against.
- **[trundlr](https://github.com/dcaler/trundlr)**, the project tracker. raDash
  reads its project list over REST, `GET` only, to join each project to its
  Zotero collection and its folder.

Without them you still get the map of your library, the publication ledger,
the frontier feed and the reading-gap signals. You do not get the project
layer: no briefs on the map, no project-to-collection join, no check against
rabbitHole's themes, and a region counts as current only through your recent
publications. `haarpi.yaml` is plain YAML, so you can write one by hand per
project folder; HAARPi's repository documents the format.

## What it reads

| Source | How | If it is absent |
|---|---|---|
| Your Zotero library | the `zotero.sqlite` file, mounted read-only | no map: the library is what the space is fitted on |
| Project folders | one directory per project, each with a `haarpi.yaml` brief | your projects are not placed on the map |
| Publications folder | `1_Publication/YYYY_Venue_Slug` directories corroborating the record | the ledger rests on the indexes alone |
| OpenAlex, Semantic Scholar | public APIs, keyed on your OpenAlex author id | no index record; cached copies are shown with their age |
| Google Scholar | pasted by hand into the importer | the manual citation lane stays empty |
| Your CV page | fetched from `CV_URL` | no drift report between the page and the record |
| rabbitHole theme maps | `*_litmap_ra.dot` files in the project folders | no agreement check on the map's regions |
| trundlr | a project tracker, over REST, `GET` only | the project join matches collections to folders by name alone |

Every source is optional in the sense that raDash starts and runs without it:
each panel says which source it is waiting for rather than showing an empty
chart.

## Running it

Docker, one container, SQLite in a named volume:

```bash
cp .env.example .env     # source paths, OpenAlex author ids, contact email
docker compose up -d --build
```

Then open `http://localhost:8261`. On a fresh volume every panel says what it is
waiting for; `POST /api/refresh` — or the button — takes it from empty to a
populated dashboard. It refreshes itself weekly thereafter, measured against the
newest snapshot's age rather than against uptime, so a redeploy neither triggers
a refresh nor delays one.

The map backend is TF-IDF plus truncated SVD, in process. There is no model
server, no GPU and no API key required for it to work.

```bash
python -m pytest -q
```

## Licence

PolyForm Noncommercial 1.0.0 — see `LICENSE.md`.
