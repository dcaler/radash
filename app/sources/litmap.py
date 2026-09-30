"""rabbitHole's contribution maps — a second opinion on the literature.

Each project's `litReview/output/*_litmap_ra.dot` holds what rabbitHole made
of that project's reading: every paper as a node carrying a written claim,
grouped into themes, with edges between them. Not every project has one.

Two things in there that raDash cannot produce for itself.

**Claims.** "Agent-based models capture feedback between household choices
and market outcomes that equilibrium models assume away" is a statement
about what a paper *contributes*, which is denser and more discriminating
than its abstract and exists for papers whose abstract raDash never found.

**Themes.** The groups are named — "Who bears the cost of a new tariff", "Subsidies
steer which technologies mature" — where raDash's regions are labelled
by whichever terms have the most lift. More usefully still, they are an
*independent* partition of the same papers, which makes them something to
check the clustering against rather than only a source of better labels.

The file is Graphviz, generated, and stable in shape: nodes are one per line
with a citekey, a label and a fill colour; themes are `__tN__` hubs whose
saturated colour is the Tailwind 600 of its members' 100. Parsing is
deliberately shallow and tolerant — this is somebody else's output format,
and a change to it should cost raDash a panel rather than a read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Members are drawn in a pale tint of their theme's colour. The pairs are
# Tailwind's 100 and 600 of one hue, which is what lets a member be matched to
# its hub without re-deriving the layout.
THEME_TINTS = {
    "#dbeafe": "#2563eb",   # blue
    "#d1fae5": "#059669",   # emerald
    "#fef3c7": "#d97706",   # amber
    "#fee2e2": "#dc2626",   # red
    "#ede9fe": "#7c3aed",   # violet
    "#cffafe": "#0891b2",   # cyan
    "#fce7f3": "#db2777",   # pink
}

_NODE = re.compile(r'^\s*"(?P<key>[^"]+)"\s*\[(?P<attrs>.*)\];\s*$')
_EDGE = re.compile(r'^\s*"(?P<from>[^"]+)"\s*->\s*"(?P<to>[^"]+)"')
_ATTR = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
_HUB = re.compile(r"^__t\d+__$")

# refs.bib: `@article{citekey,` then fields until the closing brace.
_BIB_ENTRY = re.compile(r"@\w+\{([^,]+),")
_BIB_FIELD = re.compile(r"^\s*(\w+)\s*=\s*\{(.*?)\},?\s*$", re.MULTILINE)


@dataclass
class LitNode:
    citekey: str
    label: str = ""
    claim: str = ""
    theme: Optional[str] = None       # the theme's name, where one is found
    colour: Optional[str] = None
    doi: Optional[str] = None
    title: Optional[str] = None


@dataclass
class LitMap:
    project: str
    path: str
    nodes: list[LitNode] = field(default_factory=list)
    themes: dict = field(default_factory=dict)      # colour -> theme name
    edges: list[tuple] = field(default_factory=list)

    @property
    def by_doi(self) -> dict:
        return {n.doi: n for n in self.nodes if n.doi}

    def counts(self) -> dict:
        grouped: dict[str, int] = {}
        for n in self.nodes:
            if n.theme:
                grouped[n.theme] = grouped.get(n.theme, 0) + 1
        return {"nodes": len(self.nodes), "themes": len(self.themes),
                "edges": len(self.edges), "with_doi": len(self.by_doi),
                "per_theme": grouped}


def parse_dot(text: str, project: str = "", path: str = "") -> LitMap:
    """Read a litmap. An unrecognised line is skipped, never fatal."""
    out = LitMap(project=project, path=path)
    hubs: dict[str, str] = {}

    for line in text.splitlines():
        edge = _EDGE.match(line)
        if edge:
            out.edges.append((edge.group("from"), edge.group("to")))
            continue
        node = _NODE.match(line)
        if not node:
            continue
        key = node.group("key")
        attrs = dict(_ATTR.findall(node.group("attrs")))
        label = attrs.get("label", "").replace("\\n", " ").strip()
        colour = (attrs.get("fillcolor") or "").lower()

        if _HUB.match(key):
            hubs[colour] = label
            continue
        if key == "__hub__":
            continue

        # The first line of a node's label is "Author et al. YEAR"; the rest is
        # the claim. Splitting on the year keeps the two apart without
        # depending on how many lines rabbitHole wrapped it to.
        head, claim = _split_label(label)
        out.nodes.append(LitNode(citekey=key, label=head, claim=claim,
                                 colour=colour))

    # Members carry the tint; the hub carries the name.
    for tint, saturated in THEME_TINTS.items():
        name = hubs.get(saturated)
        if name:
            out.themes[tint] = name
    for n in out.nodes:
        n.theme = out.themes.get(n.colour or "")
    return out


def _split_label(label: str) -> tuple[str, str]:
    m = re.match(r"^(.{0,70}?\b(?:19|20)\d{2}[a-z]?)\s+(.*)$", label)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return label[:70], ""


def parse_bib(text: str) -> dict:
    """citekey -> {doi, title}, which is what joins a litmap to your library."""
    out: dict[str, dict] = {}
    chunks = text.split("\n@")
    for i, chunk in enumerate(chunks):
        chunk = chunk if i == 0 else "@" + chunk
        m = _BIB_ENTRY.search(chunk)
        if not m:
            continue
        fields = {k.lower(): v for k, v in _BIB_FIELD.findall(chunk)}
        doi = (fields.get("doi") or "").strip().lower() or None
        out[m.group(1).strip()] = {
            "doi": doi,
            "title": re.sub(r"[{}]", "", fields.get("title", "")).strip() or None,
        }
    return out


def load(project_dir: Path) -> Optional[LitMap]:
    """The newest litmap in a project, joined to its bibliography.

    Newest by filename, which carries the datestamp this repo names files by.
    A project without one returns None rather than an empty map: "no litmap"
    and "a litmap with nothing in it" are different facts.
    """
    output = project_dir / "litReview" / "output"
    if not output.is_dir():
        return None
    try:
        maps = sorted(p for p in output.iterdir()
                      if p.name.endswith(".dot") and "litmap" in p.name)
    except OSError:
        return None
    if not maps:
        return None

    try:
        litmap = parse_dot(maps[-1].read_text(encoding="utf-8", errors="replace"),
                           project=project_dir.name, path=str(maps[-1]))
    except OSError:
        return None

    bib = output / "refs.bib"
    if bib.exists():
        try:
            refs = parse_bib(bib.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            refs = {}
        for n in litmap.nodes:
            entry = refs.get(n.citekey) or {}
            n.doi = entry.get("doi")
            n.title = entry.get("title")
    return litmap


def load_all(projects_dir: Path) -> list[LitMap]:
    """Every project's litmap. Missing ones are simply absent."""
    out = []
    try:
        entries = sorted(p for p in projects_dir.iterdir() if p.is_dir())
    except OSError:
        return out
    for d in entries:
        if d.name.startswith((".", "@")):
            continue
        found = load(d)
        if found is not None:
            out.append(found)
    return out
