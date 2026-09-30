"""Your public CV page — not a truth source, a *claim* source.

Every other source here reports what the world recorded about your work. This
one reports what **you told the world**, which is a different fact and a more
interesting one, because the two drift apart and nobody notices. A paper
published in March that is still missing from your site in September is not a
data problem; it is a thing you would want to be told.

So this adapter deliberately does not try to be current. It reads the page as
it stands, dates that reading from the CV file the page links, and lets the
ledger compare the claim against the record. The staleness is the signal.

It also carries something no index has: **your own categories.** The page
separates peer-reviewed publications from other publications, conference
activity and invited talks. That is you classifying your own output, and it
beats any inference from an OpenAlex `type` — so a site-derived category is
treated as authoritative, below only an explicit override.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx

from app import settings
from app.config import Config
from app.models import SourceStatus, WorkCategory
from app.sources import SourceReport, timer, utcnow

KEY = "website"
TIMEOUT = 20.0

# Beyond this the public claim is far enough behind to be worth saying so.
STALE_AFTER_DAYS = 120

# CV headings → what the author is calling that group of work. Matched as a
# prefix against an all-caps heading, longest first so "OTHER PUBLICATIONS"
# does not fall into "PUBLICATIONS".
SECTIONS: tuple[tuple[str, WorkCategory, bool], ...] = (
    # (heading prefix, category, counts as published)
    ("PEER REVIEWED PUBLICATION", WorkCategory.publication, True),
    ("OTHER PUBLICATION", WorkCategory.publication, True),
    ("RESEARCH PIPELINE", WorkCategory.other, False),      # not out yet
    ("CONFERENCE ACTIVITY", WorkCategory.conference, True),
    ("INVITED TALK", WorkCategory.conference, True),
)

_TAG = re.compile(r"(?s)<[^>]+>")
_BLOCK_END = re.compile(r"(?is)</(p|div|li|tr|h[1-6]|br)\s*>|<br\s*/?>")
_SCRIPT = re.compile(r"(?is)<(script|style).*?</\1>")
_ENTRY = re.compile(r"(?m)(?:^|\s)(\d{1,2})\.\s+")
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")
_DOI = re.compile(r"\b10\.\d{4,9}/[-._;()/:a-z0-9]+", re.IGNORECASE)
# The linked CV file carries the revision datestamp: 260812_CV_Name.pdf
_CV_FILE = re.compile(r'href="([^"]*?(\d{6})_[^"]*\.pdf)"', re.IGNORECASE)
# A heading is a whole line in capitals. Testing whole lines rather than
# searching flattened text matters: a paper titled "EARTHSEED pathways -- a
# bottom-up modelling framework" contains a nine-letter all-caps run, and
# searching for one truncated the publications section at the second entry.
_HEADING_LINE = re.compile(r"^[A-Z][A-Z0-9 &–\-'/,.()]{3,70}$")


@dataclass
class SiteEntry:
    raw: str
    title: str
    year: Optional[int]
    doi: Optional[str]
    section: str
    category: WorkCategory
    published: bool


@dataclass
class SiteClaim:
    entries: list[SiteEntry] = field(default_factory=list)
    url: str = ""
    cv_file: Optional[str] = None
    as_of: Optional[datetime] = None     # revision date of the linked CV
    sections: dict[str, int] = field(default_factory=dict)

    @property
    def published(self) -> list[SiteEntry]:
        return [e for e in self.entries if e.published]

    @property
    def age_days(self) -> Optional[float]:
        if self.as_of is None:
            return None
        return (utcnow() - self.as_of).total_seconds() / 86400

    @property
    def stale(self) -> bool:
        age = self.age_days
        return age is not None and age > settings.num("CV_STALE_DAYS",
                                                      STALE_AFTER_DAYS)


def read(cfg: Config) -> SourceReport:
    """Fetch and parse the public CV page. Never raises."""
    report = SourceReport(key=KEY)
    url = (cfg.cv_url or "").strip()
    if not url:
        report.status = SourceStatus.missing
        report.note = "CV_URL is unset — no public claim to compare against"
        return report

    try:
        with timer(report):
            with httpx.Client(timeout=TIMEOUT, follow_redirects=True,
                              headers=_headers(cfg)) as client:
                resp = client.get(url)
                resp.raise_for_status()
                kind = (resp.headers.get("content-type") or "").split(";")[0].strip()
                # A link to the CV *file* is the obvious thing to paste, and it
                # is not what this reads. Saying which is far more useful than
                # parsing a PDF as though it were markup and finding nothing.
                if kind and "html" not in kind:
                    report.status = SourceStatus.error
                    report.note = (
                        f"{url} is {kind}, and raDash reads the HTML page. "
                        "Point CV_URL at the CV page on your site — the one "
                        "that lists your publications — rather than at the "
                        "PDF it links.")
                    report.failures.append(f"content-type {kind}")
                    return report
                claim = parse(resp.text, url=url)
    except Exception as exc:
        report.fail(f"{url}: {type(exc).__name__}: {exc}")
        return report

    report.data = claim
    report.count = len(claim.entries)
    report.source_mtime = claim.as_of
    report.counts = {
        "entries": len(claim.entries),
        "published": len(claim.published),
        **{f"section_{k}": v for k, v in claim.sections.items()},
    }
    report.status = SourceStatus.ok
    if not claim.entries:
        report.fail(
            f"{url} was read but no publication entries were found. raDash "
            "looks for all-capitals headings such as PEER REVIEWED "
            "PUBLICATIONS followed by a numbered list.",
            status=SourceStatus.degraded)
    elif claim.stale:
        report.status = SourceStatus.stale
        report.note = (f"the linked CV is {claim.age_days:.0f} days old "
                       f"({claim.cv_file}) — your public claim may be behind")
    return report


def parse(page: str, url: str = "") -> SiteClaim:
    """Extract the claim. Tolerant: an unrecognised page yields no entries."""
    claim = SiteClaim(url=url)

    m = _CV_FILE.search(page)
    if m:
        claim.cv_file = m.group(1).rsplit("/", 1)[-1]
        try:
            claim.as_of = datetime.strptime(m.group(2), "%y%m%d").replace(
                tzinfo=timezone.utc)
        except ValueError:
            claim.as_of = None

    lines = _lines(page)
    for heading, category, published in SECTIONS:
        body = _section(lines, heading)
        if not body:
            continue
        entries = _entries(body)
        claim.sections[heading.title()] = len(entries)
        for raw in entries:
            claim.entries.append(_entry(raw, heading, category, published))
    return claim


def _lines(page: str) -> list[str]:
    """Visible text as lines, broken only at block boundaries.

    Inline tags become spaces, because one CV entry is split across several of
    them -- a bolded author name, an italic venue -- and breaking there would
    tear every entry into fragments. Block ends become newlines, which is what
    makes a heading identifiable as a line of its own.
    """
    s = _SCRIPT.sub(" ", page)
    s = _BLOCK_END.sub("\n", s)
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    s = s.replace("\xa0", " ")
    out = []
    for raw in s.splitlines():
        line = re.sub(r"[ \t]+", " ", raw).strip()
        if line:
            out.append(line)
    return out


def _is_heading(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return bool(letters) and line == line.upper() and _HEADING_LINE.match(line)


def _section(lines: list[str], heading: str) -> str:
    """Everything between one heading line and the next, joined into one blob."""
    start = None
    for i, line in enumerate(lines):
        if _is_heading(line) and line.upper().startswith(heading):
            start = i + 1
            break
    if start is None:
        return ""
    body: list[str] = []
    for line in lines[start:]:
        if _is_heading(line):
            break
        body.append(line)
    return " ".join(body)


def _entries(body: str) -> list[str]:
    """Split a numbered list into its items."""
    parts = _ENTRY.split(body)
    out: list[str] = []
    # split() yields [pre, num, text, num, text, ...]
    for i in range(1, len(parts) - 1, 2):
        chunk = " ".join(parts[i + 1].split()).strip()
        if len(chunk) > 30:
            out.append(chunk)
    return out


def _entry(raw: str, heading: str, category: WorkCategory,
           published: bool) -> SiteEntry:
    year_m = _YEAR.search(raw)
    doi_m = _DOI.search(raw)
    return SiteEntry(
        raw=raw, title=_title(raw),
        year=int(year_m.group(1)) if year_m else None,
        doi=doi_m.group(0).rstrip(".,;") if doi_m else None,
        section=heading.title(), category=category, published=published,
    )


def _title(raw: str) -> str:
    """The title within `Authors. Title. Venue. Year.`

    Sentence-splitting alone cannot do this. Split on every period and the
    title breaks at "U.S."; refuse to split after a capital and the author
    initials stop being boundaries, so the whole citation reads as one
    sentence. So the author block is removed explicitly first.

    An author block is a run ending in an initial -- `K.`, `D. C.` -- followed
    by the start of the title. The same shape appears later in a venue like
    "J. Cleaner Production", which is why only matches in the opening portion
    of the entry count: author lists come first, venues come last.

    It is still a guess, and it is shown to you rather than trusted silently.
    """
    body = _strip_authors(raw)
    parts = [p.strip() for p in re.split(r"(?<![A-Z])\.\s+", body) if p.strip()]
    parts = [p for p in parts if len(p) > 12]
    if not parts:
        return body[:300].rstrip(" .") or raw[:200]
    return parts[0].rstrip(" .")[:300]


# An initial, then optional further initials, then the start of the title.
_AUTHOR_TAIL = re.compile(r"[A-Z]\.\s*,?\s*(?=[A-Z])")
# How far into an entry an author block may plausibly still be running.
_AUTHOR_ZONE = 0.6


def _strip_authors(raw: str) -> str:
    cutoff = len(raw) * _AUTHOR_ZONE
    end = None
    for m in _AUTHOR_TAIL.finditer(raw):
        if m.end() > cutoff:
            break
        end = m.end()
    return raw[end:].strip() if end else raw


def _headers(cfg: Config) -> dict[str, str]:
    who = cfg.contact_email or "anonymous"
    return {"User-Agent": f"raDash/1.0 (reading its owner's CV page; {who})"}
