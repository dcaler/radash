"""Google Scholar — the manual citation lane, and the one raDash cannot refresh.

Scholar has no API worth the name, and the automated alternatives are paid
cloud dependencies that were deliberately cut from v1 (`BUILD_PLAN.md`, scope
fences). So this lane is manual: you export or paste, roughly monthly, and
raDash reads what you dropped.

That makes **staleness the headline fact about this source**, not a footnote.
Scholar's numbers are always the largest of the three lanes and always the
oldest, and a dashboard that shows them without their age invites you to read
a number from March as today's. Every reading here therefore carries the age of
the file it came from, and the UI renders the age beside the number.

Two input shapes are accepted, because Scholar's own CSV export omits the one
column that matters:

- **CSV** (`Title,Authors,Publication,Volume,Number,Pages,Year,Publisher`) —
  bibliographic only. Parsed, and reported as `degraded` with the reason, since
  it cannot move the citation lane.
- **Paste** — the text of a Scholar profile page, where each entry carries its
  `Cited by N`. This is the useful one: about thirty seconds of copying, and it
  is the only path that produces manual citation counts.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app import settings
from app.config import Config
from app.models import SourceStatus
from app.sources import SourceReport, timer, utcnow

KEY = "scholar"

SUFFIXES = (".csv", ".txt")

# Beyond this the numbers are old enough to mislead rather than inform.
STALE_AFTER_DAYS = 60

# What to say when there is nothing to read. The previous wording named the
# import directory and told you to put a file in it, which is impossible from
# outside the container and unnecessary from inside it — the importer writes
# there for you, and creates it on the first use.
NOTHING_IMPORTED = (
    "nothing imported yet — paste or upload your Scholar profile in the "
    "importer on this page. There is no folder to drop a file into: raDash "
    "stores the import inside its own volume and creates it on first use.")

# `Cited by 42` — the column Scholar's CSV export leaves out.
CITED_BY_RE = re.compile(r"cited\s+by\s+(\d[\d,]*)", re.IGNORECASE)
# A trailing 4-digit year on an author/venue line.
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
# Filename datestamp from the repo's document-revision convention.
STAMP_RE = re.compile(r"^(\d{6})_")


@dataclass
class ScholarEntry:
    title: str
    authors: Optional[str] = None
    venue: Optional[str] = None
    year: Optional[int] = None
    cited_by: Optional[int] = None


@dataclass
class ScholarImport:
    entries: list[ScholarEntry] = field(default_factory=list)
    source_file: Optional[str] = None
    captured_at: Optional[datetime] = None
    shape: Optional[str] = None       # "csv" | "paste"

    @property
    def citation_sum(self) -> int:
        return sum(e.cited_by or 0 for e in self.entries)

    @property
    def with_counts(self) -> int:
        return sum(1 for e in self.entries if e.cited_by is not None)

    @property
    def age_days(self) -> Optional[float]:
        if self.captured_at is None:
            return None
        return (utcnow() - self.captured_at).total_seconds() / 86400

    @property
    def stale(self) -> bool:
        age = self.age_days
        return age is not None and age > settings.num("SCHOLAR_STALE_DAYS",
                                                      STALE_AFTER_DAYS)


def read(cfg: Config) -> SourceReport:
    """Read the most recent Scholar export in the import directory."""
    report = SourceReport(key=KEY)
    d = cfg.import_dir

    if not d.exists():
        report.status = SourceStatus.missing
        # Not "drop a file in {d}". That path is inside raDash's own Docker
        # volume and has no host side: the advice sent you looking for a
        # folder that cannot be opened, past an importer that was already
        # built for exactly this. The directory is created by the first
        # import and never needs to exist before one.
        report.note = NOTHING_IMPORTED
        return report

    path = _newest_export(d)
    if path is None:
        report.status = SourceStatus.missing
        report.note = NOTHING_IMPORTED
        return report

    try:
        with timer(report):
            text = path.read_text(encoding="utf-8", errors="replace")
            imported = parse(text, shape=_shape_of(path, text))
            imported.source_file = path.name
            imported.captured_at = _captured_at(path)
    except OSError as exc:
        report.fail(f"{path.name} unreadable: {exc}")
        return report

    report.data = imported
    report.count = len(imported.entries)
    report.source_mtime = imported.captured_at
    report.counts = {
        "entries": len(imported.entries),
        "with_citations": imported.with_counts,
        "citations_summed": imported.citation_sum,
    }

    if not imported.entries:
        report.fail(f"{path.name} parsed to zero entries", status=SourceStatus.error)
        return report

    report.status = SourceStatus.ok
    if imported.with_counts == 0:
        report.degrade(
            f"{path.name} is a {imported.shape} export with no 'Cited by' "
            "column — Scholar does not put citation counts in any of its "
            "exports. Its titles, venues and years still reach the ledger as "
            "candidates; the manual citation lane stays empty until you paste "
            "the profile page itself.")
    if imported.stale:
        report.status = SourceStatus.stale
        report.note = (f"{path.name} is {imported.age_days:.0f} days old "
                       f"(stale after {STALE_AFTER_DAYS})")
    return report


def parse(text: str, shape: Optional[str] = None) -> ScholarImport:
    """Parse either accepted shape. Pure, so both paths are directly testable.

    The leading BOM is stripped first. Scholar writes `citations.csv` as UTF-8
    with a byte-order mark, which lands inside the *first header cell*: the
    column key becomes `\ufeffauthors` rather than `authors`, and that column
    silently disappears. On the real export the first column is Authors, and
    authorship overlap is one of the signals the duplicate proposals rest on —
    so the cost of the BOM was weaker dedup on every Scholar-sourced work,
    with nothing anywhere reporting a problem.
    """
    text = text.lstrip("\ufeff")
    shape = shape or ("csv" if _looks_like_csv(text) else "paste")
    imported = ScholarImport(shape=shape)
    imported.entries = _parse_csv(text) if shape == "csv" else _parse_paste(text)
    return imported


def _parse_csv(text: str) -> list[ScholarEntry]:
    out: list[ScholarEntry] = []
    try:
        reader = csv.DictReader(io.StringIO(text))
    except csv.Error:
        return out
    for row in reader:
        if not row:
            continue
        low = { (k or "").strip().lower(): (v or "").strip() for k, v in row.items() }
        title = low.get("title") or ""
        if not title:
            continue
        out.append(ScholarEntry(
            title=title,
            authors=low.get("authors") or None,
            venue=low.get("publication") or None,
            year=_int(low.get("year")),
            # Present only if the export was hand-extended with the column.
            cited_by=_int(low.get("cited by") or low.get("cited_by") or low.get("citations")),
        ))
    return out


def _parse_paste(text: str) -> list[ScholarEntry]:
    """Parse pasted profile text, in either shape the page produces.

    **The columnar shape is what you actually get.** Selecting the table on a
    Scholar profile and copying it yields three header cells, then one block
    per work: an indented title, an authors line, and a line carrying the
    venue, the citation count and the year in separate columns.

        TITLE
        CITED BY
        YEAR
            The Dispossessed: an ambiguous utopia of ...
        N Jemisin, OE Butler, J Russ
        Renewable Energy 12, 101-120    314    2016

    There is no `Cited by 314` anywhere in it. The count is a column, and the
    parser this replaced searched for the phrase — so on a real paste it found
    no counts at all, mistook a year for one, and promoted an authors line and
    a header cell to titles. The manual citation lane could not be filled by
    the only route that fills it.

    The flowed shape — `Cited by 402` on its own line — is what a screen
    reader or a partial selection produces, and is still accepted.

    Either way a block without a recognisable count yields an entry with
    `cited_by` left `None` rather than zero, because "not captured" and "never
    cited" are different facts.
    """
    lines = text.splitlines()
    if _is_columnar(lines):
        return _parse_columnar(lines)
    return _parse_flowed([ln.strip() for ln in lines])


# A title cell, indented by the copy. Two or more of these and the paste
# carried its table structure rather than being flattened into prose.
_INDENTED = re.compile(r"^(?:\t| {2,})\s*\S")
# The gap between table cells: a tab, or the run of spaces a tab becomes.
_COLUMNS = re.compile(r"\t+| {2,}")
# Header cells, which are not works.
_HEADINGS = {"title", "cited by", "year", "citedby"}
# Last resort, for a paste whose column gaps were flattened to single spaces.
_TRAILING_COUNT_YEAR = re.compile(r"\s(\d{1,7})\s+((?:19|20)\d{2})\s*$")
_TRAILING_YEAR = re.compile(r"\s((?:19|20)\d{2})\s*$")


def _is_columnar(lines: list) -> bool:
    return sum(1 for ln in lines if _INDENTED.match(ln)) >= 2


def _parse_columnar(lines: list) -> list[ScholarEntry]:
    """One entry per indented title cell; the lines under it are its block.

    Indentation is the delimiter because it is the one signal that survives
    the copy intact. Guessing where an entry ends from line length is what
    made the previous parser turn a journal name into a title.
    """
    blocks: list = []
    for raw in lines:
        if not raw.strip():
            continue
        if _INDENTED.match(raw):
            blocks.append([raw.strip(), []])
        elif blocks:
            blocks[-1][1].append(raw.rstrip())
        # Anything before the first title cell is the header row.

    out: list[ScholarEntry] = []
    for title, block in blocks:
        if not title or title.lower() in _HEADINGS:
            continue
        authors = meta = None
        if len(block) == 1:
            # One line under the title is the authors, unless it parses as the
            # venue/count/year row — a work with no listed authors still has
            # its numbers.
            if _metrics(block[0])[2] is None and _metrics(block[0])[1] is None:
                authors = block[0]
            else:
                meta = block[0]
        elif block:
            authors, meta = block[0], block[-1]
        venue, cited, year = _metrics(meta) if meta else (None, None, None)
        out.append(ScholarEntry(title=title, authors=authors or None,
                                venue=venue, year=year, cited_by=cited))
    return out


def _metrics(line: str):
    """Split a venue/count/year row into its three cells.

    From the right, because only the trailing cells are reliably typed: two
    trailing integers are a count and a year, one is a year if it looks like
    one and a count otherwise. A work with no citations has an empty cell, so
    "venue then year" must not read as "venue then count".
    """
    if not line or not line.strip():
        return None, None, None
    cells = [c.strip() for c in _COLUMNS.split(line.strip()) if c.strip()]

    trailing: list = []
    while cells and len(trailing) < 2 and _int(cells[-1]) is not None:
        trailing.insert(0, _int(cells.pop()))

    if not trailing:
        # The gaps may have been flattened to single spaces on the way here.
        rest = " ".join(cells)
        both = _TRAILING_COUNT_YEAR.search(rest)
        if both:
            return (rest[:both.start()].strip() or None,
                    _int(both.group(1)), _int(both.group(2)))
        one = _TRAILING_YEAR.search(rest)
        if one:
            return rest[:one.start()].strip() or None, None, _int(one.group(1))
        return rest or None, None, None

    venue = " ".join(cells).strip() or None
    if len(trailing) == 2:
        return venue, trailing[0], trailing[1]
    value = trailing[0]
    # A lone number in the last column is the year: an uncited work leaves the
    # count cell empty, and every entry carries a year.
    if value is not None and 1900 <= value <= 2100:
        return venue, None, value
    return venue, value, None


def _parse_flowed(lines: list) -> list[ScholarEntry]:
    """The older shape, where the count reads `Cited by 402`."""
    out: list[ScholarEntry] = []
    i = 0
    while i < len(lines):
        title = lines[i]
        i += 1
        if not title or CITED_BY_RE.search(title) or title.isdigit():
            continue
        if len(title) < 8:      # headers, page furniture, stray numbers
            continue

        block: list[str] = []
        while i < len(lines) and len(block) < 3:
            nxt = lines[i]
            if not nxt:
                i += 1
                break
            # A long line that is not a count begins the next entry.
            if len(nxt) > 60 and not CITED_BY_RE.search(nxt) and block:
                break
            block.append(nxt)
            i += 1

        joined = " ".join(block)
        cited = CITED_BY_RE.search(joined)
        bare = [b for b in block if b.replace(",", "").isdigit()]
        year = YEAR_RE.search(joined)
        out.append(ScholarEntry(
            title=title,
            authors=block[0] if block else None,
            venue=block[1] if len(block) > 1 else None,
            year=_int(year.group(0)) if year else None,
            cited_by=(_int(cited.group(1)) if cited
                      else (_int(bare[0]) if bare else None)),
        ))
    return out


def _newest_export(d: Path) -> Optional[Path]:
    """Most recently modified export. Matches the repo's revision-chain rule:
    the file you touched last is the one that counts."""
    try:
        files = [p for p in d.iterdir()
                 if p.is_file() and p.suffix.lower() in SUFFIXES]
    except OSError:
        return None
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def _captured_at(path: Path) -> Optional[datetime]:
    """Prefer a `YYMMDD_` filename stamp over mtime.

    A file copied between machines gets a fresh mtime and would read as new
    data; the datestamp in the name is what the convention says the cycle is.
    """
    m = STAMP_RE.match(path.name)
    if m:
        try:
            return datetime.strptime(m.group(1), "%y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return None


def _shape_of(path: Path, text: str) -> str:
    if path.suffix.lower() == ".csv":
        return "csv"
    return "csv" if _looks_like_csv(text) else "paste"


def _looks_like_csv(text: str) -> bool:
    head = text.lstrip().splitlines()[:1]
    return bool(head) and head[0].count(",") >= 2 and "title" in head[0].lower()


def _int(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
