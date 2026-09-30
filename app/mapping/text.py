"""What text represents an item — M3-T2.

The map only ever sees this string, so the choice is the most consequential
one in the milestone and the least visible.

`title + venue + abstract`, in that order. The venue earns its place: it is
short, it is present when an abstract is not, and it carries real signal about
where work sits — *Energy Policy* and *JASSS* are different neighbourhoods
whatever the abstract says. It is one field among many and is not repeated, so
it colours a position rather than dominating it.

**Thin items are not silently placed.** 43% of this corpus carries under 200
characters of usable text. An item with a title and nothing else has a
position the arithmetic will happily compute and the map should not show as
equal in confidence to a full abstract. They are assembled, marked, and the
caller decides — `include_thin=False` is the default for fitting, because a
space fitted mostly on titles is a space about title style.

**OpenAlex enrichment comes first.** Where Zotero has no abstract and OpenAlex
has one for the same DOI, the OpenAlex text is used: it is the same work, and
the alternative is discarding an item over a field one source happens to be
missing.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Optional

from app import settings
from app.sources.zotero import THIN_TEXT_CHARS

# Collapse the whitespace that arrives with scraped and pasted abstracts.
_WS = re.compile(r"\s+")

# Publisher boilerplate that carries no topical signal and, repeated across
# hundreds of items, becomes a term the fit treats as meaningful.
BOILERPLATE = (
    "all rights reserved", "elsevier ltd", "published by elsevier",
    "this article is protected by copyright", "springer nature",
    "copyright ©", "©", "abstract:", "abstract ",
)

# Sections that are apparatus rather than content. An acknowledgement names
# funders and colleagues; a JEL line is a classification code. Both were
# clustering: "acknowledgements, funded, thomson, funders" was a region of the
# map, and "o31" — a JEL code for innovation — was labelling another. Rare
# tokens carry high inverse document frequency, so a handful of stray ones
# define a region, exactly as a handful of Spanish documents defined an axis.
_SECTION_STARTS = (
    "acknowledgement", "acknowledgment", "jel classification", "jel codes",
    "jel:", "keywords:", "key words:", "declaration of competing interest",
    "conflict of interest", "funding:", "data availability",
)
_SECTION_RE = re.compile(
    r"(?is)\b(?:" + "|".join(re.escape(x) for x in _SECTION_STARTS) + r").*$")

# A word broken across a line and never rejoined: "small- scale" is not the
# token "small-", and the fragment is rare enough to become a cluster label.
# Rejoined *with* the hyphen: every fragment observed in this corpus is a
# compound prefix — low-, early-, micro-, non-, small-, high- — so "small-
# scale" is "small-scale", not "smallscale". A genuinely split word like
# "adop- tion" comes out hyphenated instead, which costs one token rather
# than inventing a wrong one.
# ...except a suspended compound: "small- and medium-sized" is correct English
# in which the hyphen dangles on purpose. Rejoining it produced "small-and",
# which promptly became a cluster label — a repair inventing the artifact it
# was added to remove.
_HYPHEN_BREAK = re.compile(r"(\w)-\s+(?!(?:and|or|to)\b)(\w)")

# Typographic ligatures arrive intact from PDFs: this library holds 1,210 of
# them — 871 "ﬁ", 195 "ﬀ", 120 "ﬂ", 24 "ﬃ" — in words like "ﬁrms" and
# "diﬀerent". Each is a single codepoint, so "ﬁrms" and "firms" are two
# different terms to the vectoriser, splitting the weight of a word between
# two spellings. Worse for the labels: a term pattern anchored at a-z matches
# from the *second* character, which is where "tness", "icult" and
# "ne-tuning" came from — they are "ﬁtness", "diﬃcult" and "ﬁne-tuning" read
# past their first letter.
#
# NFKC decomposes every one of them to its component letters, which is both
# the complete fix and a smaller one than the table of guesses it replaced.
def _unligature(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


# Where extraction dropped the ligature instead of preserving it, leaving a
# space. Rare — two instances in this corpus — and listed rather than guessed,
# because inferring the missing letters would invent vocabulary.
_SPACED_LIGATURES = (
    (re.compile(r"(?i)\bspeci c\b"), "specific"),
    (re.compile(r"(?i)\bsigni cant(ly)?\b"), r"significant\1"),
    (re.compile(r"(?i)\bbene t(s)?\b"), r"benefit\1"),
    (re.compile(r"(?i)\bcon dence\b"), "confidence"),
    (re.compile(r"(?i)\be ciency\b"), "efficiency"),
    (re.compile(r"(?i)\bidenti ed\b"), "identified"),
    (re.compile(r"(?i)\bclassi cation\b"), "classification"),
)


@dataclass
class Document:
    """One item, as the map will see it."""
    ref: str
    kind: str                      # "corpus" | "work" | "project"
    title: str = ""
    venue: Optional[str] = None
    abstract: str = ""
    year: Optional[int] = None
    language: Optional[str] = None
    enriched: bool = False         # abstract came from OpenAlex, not Zotero
    # Collected is not read. On this library 2,765 items have a stored PDF and
    # 158 carry evidence of having been opened, so conflating the two
    # overstates what has been absorbed by a factor of twenty.
    reading: str = "collected"     # collected | opened | noted | annotated | marked
    doi: Optional[str] = None
    cited_by: Optional[int] = None

    @property
    def text(self) -> str:
        parts = [self.title.strip(), (self.venue or "").strip(),
                 self.abstract.strip()]
        return clean(" \n ".join(p for p in parts if p))

    @property
    def thin(self) -> bool:
        return len(self.text) < settings.num("THIN_TEXT_CHARS", THIN_TEXT_CHARS)

    @property
    def label(self) -> str:
        year = f" ({self.year})" if self.year else ""
        return f"{self.title[:90]}{year}"


# Function words are the cheapest language signal there is: they are frequent,
# short, and almost never shared across these languages.
_STOPWORDS = {
    "en": set("the of and to in a is that for with on as are by this be from at"
              " it we our".split()),
    "other": set("de la el en y los las del que por para con una un se al sus"
                 " decisiones sociales der die das und den ist mit von des les"
                 " est une dans pour que qui ou e da do dos das nao".split()),
}

_WORDS = re.compile(r"[a-zà-öø-ÿ]+")

# Below this a text has too few function words to judge, and an item is kept:
# a wrong exclusion costs a document, a wrong inclusion costs some weight.
_MIN_WORDS_TO_JUDGE = 15


def is_english(doc: "Document") -> bool:
    """Whether an item should join an English-language fit.

    Worth doing for a handful of items, because of how TF-IDF weights them:
    a Spanish paper's vocabulary appears almost nowhere else in the corpus, so
    every one of its terms carries near-maximal inverse document frequency, and
    a few such documents can define an entire component. On this library the
    strongest axis was separating Spanish from English rather than one field
    from another — four documents' worth of signal occupying the axis the map
    is read along.

    Zotero's `language` field decides where it is set. It is blank on about
    half the library, so the fallback counts function words, and anything with
    too few to judge is kept.
    """
    declared = (doc.language or "").strip().lower()
    if declared:
        return declared.startswith("en")
    words = _WORDS.findall(doc.text.lower())
    if len(words) < _MIN_WORDS_TO_JUDGE:
        return True
    english = sum(w in _STOPWORDS["en"] for w in words)
    other = sum(w in _STOPWORDS["other"] for w in words)
    return english >= other


def clean(text: str) -> str:
    """Repair the text, then strip what is apparatus rather than content.

    Order matters. Hyphen breaks are rejoined before anything else, because a
    word split across a line is invisible to every rule below it. Sections are
    cut before tokens, so an "Acknowledgements" heading takes its paragraph
    with it rather than leaving the names behind.
    """
    out = _unligature(text or "")
    out = _HYPHEN_BREAK.sub(r"\1-\2", out)
    for pattern, replacement in _SPACED_LIGATURES:
        out = pattern.sub(replacement, out)
    out = _WS.sub(" ", out).strip()

    # Everything from an apparatus heading to the end. Abstracts put these
    # last, and a mid-sentence false positive would cost the tail of one
    # abstract rather than corrupt the corpus.
    # Only the JEL *section* goes, never a bare code: the pattern for one is
    # letter-then-two-digits, which is also B12, and stripping a vitamin from a
    # nutrition abstract to catch a classification code is a bad trade.
    out = _SECTION_RE.sub(" ", out)

    lowered = out.lower()
    for phrase in BOILERPLATE:
        idx = lowered.find(phrase)
        while idx != -1:
            out = (out[:idx] + " " + out[idx + len(phrase):]).strip()
            lowered = out.lower()
            idx = lowered.find(phrase)
    return _WS.sub(" ", out).strip()


def from_zotero(library, enrichment: Optional[dict] = None) -> list[Document]:
    """The corpus: what you have read.

    `enrichment` maps a normalised DOI to an abstract from OpenAlex, used only
    where Zotero has none.
    """
    enrichment = enrichment or {}
    out: list[Document] = []
    for item in getattr(library, "items", []):
        abstract = item.abstract or ""
        enriched = False
        if not abstract and item.doi and item.doi in enrichment:
            abstract = enrichment[item.doi] or ""
            enriched = bool(abstract)
        out.append(Document(
            ref=item.key, kind="corpus", title=item.title,
            venue=item.fields.get("publicationTitle"), abstract=abstract,
            year=item.year, language=item.fields.get("language"),
            enriched=enriched, reading=item.reading, doi=item.doi or None,
        ))
    return out


def from_works(works, abstracts: Optional[dict] = None) -> list[Document]:
    """What you have written, projected into the same space."""
    abstracts = abstracts or {}
    out: list[Document] = []
    for w in works:
        out.append(Document(
            ref=w.fingerprint, kind="work", title=w.title or "",
            venue=w.venue, abstract=abstracts.get((w.doi or "").lower(), ""),
            year=w.year,
        ))
    return out


def from_projects(projects) -> list[Document]:
    """What you are doing: the brief from each `haarpi.yaml`."""
    out: list[Document] = []
    for p in projects:
        brief = (p.brief or "").strip()
        if not brief:
            continue
        out.append(Document(ref=p.slug, kind="project",
                            title=p.name or p.slug, abstract=brief))
    return out


def corpus_hash(docs: Iterable[Document]) -> str:
    """Fingerprint the exact text a space was fitted on.

    Order-independent, so a reordered read of the same corpus does not read as
    a different one, while a single changed abstract does.
    """
    h = hashlib.sha256()
    for line in sorted(f"{d.kind}:{d.ref}:{d.text}" for d in docs):
        h.update(line.encode("utf-8", "replace"))
        h.update(b"\n")
    return h.hexdigest()[:16]


@dataclass
class Corpus:
    documents: list[Document] = field(default_factory=list)
    skipped_thin: int = 0
    skipped_language: int = 0

    @property
    def texts(self) -> list[str]:
        return [d.text for d in self.documents]

    @property
    def hash(self) -> str:
        return corpus_hash(self.documents)

    def counts(self) -> dict:
        kinds: dict[str, int] = {}
        for d in self.documents:
            kinds[d.kind] = kinds.get(d.kind, 0) + 1
        return {"documents": len(self.documents), "skipped_thin": self.skipped_thin,
                "skipped_language": self.skipped_language,
                "enriched": sum(1 for d in self.documents if d.enriched), **kinds}


def assemble(*groups: Iterable[Document], include_thin: bool = False,
             english_only: bool = True) -> Corpus:
    """Combine document groups, holding back what the fit should not see."""
    corpus = Corpus()
    for group in groups:
        for d in group:
            if not d.text:
                corpus.skipped_thin += 1
                continue
            if d.thin and not include_thin:
                corpus.skipped_thin += 1
                continue
            if english_only and d.kind == "corpus" and not is_english(d):
                corpus.skipped_language += 1
                continue
            corpus.documents.append(d)
    return corpus
