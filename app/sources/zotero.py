"""Zotero reader — the corpus, read from a live database without touching it.

Zotero's SQLite file is open and being written by the Zotero client whenever it
runs. raDash opens it with `immutable=1`, which tells SQLite the file will not
change under it: no locking, no WAL recovery, no journal writes, and therefore
no way for this process to block the client or corrupt the file. The cost of
that promise is that it is a lie — the file *does* change — so a read taken
mid-write can see a torn page and raise `DatabaseError`. That is expected
rather than exceptional, so reads retry briefly and, if the file is still
unquiet, report `degraded` instead of failing (`DEPLOY.md` §4).

Scope: the **personal library** (`libraries.type = 'user'`) is the corpus.
Group libraries are collaborations with their own naming conventions and are
reported but not merged, because the collection→project join in `matcher.py`
only holds for the personal library's names.

Deleted items — anything in `deletedItems`, Zotero's trash — are excluded
everywhere, including from collection membership and attachment counts.
"""
from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from app import settings
from app import settings
from app.config import Config
from app.models import SourceStatus
from app.sources import SourceReport, timer

KEY = "zotero"

# Fields lifted out of Zotero's EAV tables. Everything raDash maps or dedups on
# lives here; the rest of Zotero's ~100 fields stay unread.
WANTED_FIELDS = (
    "title", "abstractNote", "DOI", "date", "publicationTitle", "url",
    "extra", "bookTitle", "proceedingsTitle", "repository", "archiveID",
    "shortTitle", "language",
)

# Item types that are bibliography, not machinery. `attachment`, `note` and
# `annotation` are real rows in `items` but are children, counted separately.
CHILD_TYPES = frozenset({"attachment", "note", "annotation"})

# Below this many characters an abstract cannot carry a document's position in
# the map. Counted, reported, and left in place — the fix is collection, not
# imputation (`DESIGN.md` coverage honesty).
THIN_TEXT_CHARS = 200

_RETRIES = 3
_RETRY_SLEEP = 0.25

_YEAR_RE = re.compile(r"\b(\d{4})\b")


@dataclass
class ZoteroItem:
    item_id: int
    key: str
    library_id: int
    item_type: str
    date_added: Optional[str] = None
    date_modified: Optional[str] = None
    fields: dict[str, str] = field(default_factory=dict)
    creators: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    collections: list[str] = field(default_factory=list)   # collection keys
    attachment_count: int = 0
    pdf_count: int = 0
    annotation_count: int = 0
    note_count: int = 0
    last_read: Optional[datetime] = None
    # Casefolded names of the collections this item belongs to, so a "read"
    # collection can be recognised without a second lookup per item.
    _collection_names: set = field(default_factory=set)

    @property
    def title(self) -> str:
        return self.fields.get("title", "")

    @property
    def abstract(self) -> str:
        return self.fields.get("abstractNote", "")

    @property
    def doi(self) -> str:
        return (self.fields.get("DOI") or "").strip().lower()

    @property
    def year(self) -> Optional[int]:
        """Zotero dates are free text ('2015-03', 'March 2015', 'in press').

        A year is any standalone four-digit token in a plausible range. The
        word boundaries matter: they keep '12345' from reading as 1234.
        """
        m = _YEAR_RE.search(self.fields.get("date", "") or "")
        if not m:
            return None
        y = int(m.group(1))
        return y if 1800 < y < 2200 else None

    @property
    def text(self) -> str:
        """The text that represents this item on the map."""
        return f"{self.title}\n\n{self.abstract}".strip()

    @property
    def marked_read(self) -> bool:
        """Whether you have marked this item as read, in Zotero.

        Two ways, because they suit different moments. A **tag** is a property
        of the item and keeps your collections clean. A **collection** is
        easier for a backfill — select two hundred items and drag once — at
        the cost of one more name in the namespace your project join uses.
        Either, both, or neither; raDash reads whatever is set.

        The tag is the one signal that can be trusted, because it is the only
        one you set deliberately. Everything else here is inference from
        traces, and the traces are thin: Zotero began stamping `lastRead` in
        May 2026, and reading in any other application — Preview, a tablet,
        paper — leaves none at all.
        """
        tag = settings.text("READ_TAG", "read").strip().casefold()
        if tag and any((t or "").strip().casefold() == tag for t in self.tags):
            return True
        collection = settings.text("READ_COLLECTION", "").strip().casefold()
        return bool(collection and collection in self._collection_names)

    @property
    def reading(self) -> str:
        """How far this item got, on the evidence Zotero keeps.

        A library is not a reading list. rabbitHole collects faster than anyone
        reads, and on this library 2,765 items have a stored PDF while 138 have
        ever been opened — so treating "in the corpus" as "read" overstates
        what has been absorbed by a factor of twenty.

        **Only a deliberate mark counts.** An earlier version graded four
        states from traces — annotated, noted, opened — and the traces are too
        thin to carry it: Zotero began stamping `lastRead` in May 2026, and
        reading in Preview, on a tablet or on paper leaves nothing at all. A
        number assembled from those would be wrong in a new direction rather
        than right.

        So: you said so, or raDash does not know. Annotations and notes are
        still counted and shown, as evidence you might act on — they are a
        good shortlist for what to mark — but they do not make the claim
        themselves.
        """
        return "marked" if self.marked_read else "collected"

    @property
    def read(self) -> bool:
        return self.reading != "collected"

    @property
    def thin(self) -> bool:
        return len(self.text) < settings.num("THIN_TEXT_CHARS", THIN_TEXT_CHARS)


@dataclass
class ZoteroCollection:
    collection_id: int
    key: str
    name: str
    library_id: int
    parent_id: Optional[int] = None
    item_keys: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.item_keys)


@dataclass
class ZoteroLibrary:
    """Everything raDash reads from Zotero, already joined."""
    items: list[ZoteroItem] = field(default_factory=list)
    collections: list[ZoteroCollection] = field(default_factory=list)
    library_id: int = 1
    group_libraries: dict[int, int] = field(default_factory=dict)  # libraryID -> item count
    schema_version: Optional[int] = None

    def by_key(self) -> dict[str, ZoteroItem]:
        return {i.key: i for i in self.items}

    def collection_names(self) -> list[str]:
        return [c.name for c in self.collections]

    @property
    def thin_count(self) -> int:
        return sum(1 for i in self.items if i.thin)


def _connect(path: Path) -> sqlite3.Connection:
    """Open read-only and immutable.

    `immutable=1` implies read-only and additionally forbids SQLite from taking
    locks or writing a journal — the property that makes reading a live library
    safe for the *client*. `mode=ro` is redundant alongside it but is kept so
    the intent survives anyone who edits this URI later.
    """
    uri = f"file:{path}?immutable=1&mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=5.0)
    con.row_factory = sqlite3.Row
    return con


def _query(con: sqlite3.Connection, sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
    """Run one query, retrying through the torn reads a live file produces."""
    last: Optional[Exception] = None
    for attempt in range(_RETRIES):
        try:
            return con.execute(sql, tuple(params)).fetchall()
        except sqlite3.DatabaseError as exc:  # malformed page, moved content
            last = exc
            if attempt < _RETRIES - 1:
                time.sleep(_RETRY_SLEEP)
    raise last  # type: ignore[misc]


def read(cfg: Config, library_id: Optional[int] = None) -> SourceReport:
    """Read the personal library: items, collections, attachments, annotations.

    Never raises. A missing file, an unreadable file and a half-read file are
    three different reports, not three exceptions.
    """
    report = SourceReport(key=KEY)
    path = cfg.zotero_sqlite

    if not path.exists():
        report.status = SourceStatus.missing
        report.note = f"not mounted at {path}"
        return report

    try:
        report.source_mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        pass

    con = None
    try:
        with timer(report):
            con = _connect(path)
            lib = ZoteroLibrary()

            try:
                row = _query(con, "SELECT version FROM version WHERE schema='userdata'")
                lib.schema_version = row[0]["version"] if row else None
            except sqlite3.DatabaseError as exc:
                report.degrade(f"schema version unreadable: {exc}")

            lib.library_id = library_id or _personal_library_id(con)
            lib.group_libraries = _group_counts(con, lib.library_id)

            items = _read_items(con, lib.library_id, report)
            _attach_fields(con, items, report)
            _attach_creators(con, items, report)
            _attach_tags(con, items, report)
            collections = _read_collections(con, lib.library_id, items, report)
            _attach_children(con, items, report)

            lib.items = [i for i in items.values() if i.item_type not in CHILD_TYPES]
            lib.collections = collections
            report.data = lib

        report.count = len(lib.items)
        report.counts = {
            "items": len(lib.items),
            "collections": len(lib.collections),
            "with_abstract": sum(1 for i in lib.items if i.abstract),
            "thin_text": lib.thin_count,
            "with_pdf": sum(1 for i in lib.items if i.pdf_count),
            "annotations": sum(i.annotation_count for i in lib.items),
            "read": sum(1 for i in lib.items if i.read),
            # Kept as evidence rather than as a verdict: these are a shortlist
            # of what to mark, not a count of what has been read.
            "annotated": sum(1 for i in lib.items if i.annotation_count),
            "noted": sum(1 for i in lib.items if i.note_count),
            "opened_in_zotero": sum(1 for i in lib.items if i.last_read),
            "collected_only": sum(1 for i in lib.items
                                  if i.reading == "collected"),
            "with_doi": sum(1 for i in lib.items if i.doi),
            "group_library_items": sum(lib.group_libraries.values()),
        }
        if report.status is SourceStatus.missing:
            report.status = SourceStatus.ok
        if lib.thin_count and not report.note:
            report.note = (f"{lib.thin_count} of {len(lib.items)} items carry less "
                           f"than {THIN_TEXT_CHARS} characters of text")
    except sqlite3.DatabaseError as exc:
        report.fail(f"zotero.sqlite unreadable ({exc}) — retried {_RETRIES}x; "
                    "likely a write in progress")
    except Exception as exc:  # an adapter never takes the app down
        report.fail(f"{type(exc).__name__}: {exc}")
    finally:
        if con is not None:
            con.close()

    return report


def _personal_library_id(con: sqlite3.Connection) -> int:
    rows = _query(con, "SELECT libraryID FROM libraries WHERE type='user' ORDER BY libraryID")
    return rows[0]["libraryID"] if rows else 1


def _group_counts(con: sqlite3.Connection, personal_id: int) -> dict[int, int]:
    """Group libraries are visible but excluded — reported so that is explicit."""
    rows = _query(con, """
        SELECT libraryID, COUNT(*) AS n FROM items
        WHERE libraryID != ? AND itemID NOT IN (SELECT itemID FROM deletedItems)
        GROUP BY libraryID
    """, (personal_id,))
    return {r["libraryID"]: r["n"] for r in rows}


def _read_items(con, library_id: int, report: SourceReport) -> dict[int, ZoteroItem]:
    rows = _query(con, """
        SELECT i.itemID, i.key, i.libraryID, i.dateAdded, i.dateModified, t.typeName
        FROM items i JOIN itemTypes t ON t.itemTypeID = i.itemTypeID
        WHERE i.libraryID = ?
          AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
    """, (library_id,))
    return {
        r["itemID"]: ZoteroItem(
            item_id=r["itemID"], key=r["key"], library_id=r["libraryID"],
            item_type=r["typeName"], date_added=r["dateAdded"],
            date_modified=r["dateModified"],
        )
        for r in rows
    }


def _attach_fields(con, items: dict[int, ZoteroItem], report: SourceReport) -> None:
    """Pivot Zotero's EAV field storage onto the items, for wanted fields only."""
    placeholders = ",".join("?" * len(WANTED_FIELDS))
    try:
        rows = _query(con, f"""
            SELECT d.itemID, f.fieldName, v.value
            FROM itemData d
            JOIN fields f ON f.fieldID = d.fieldID
            JOIN itemDataValues v ON v.valueID = d.valueID
            WHERE f.fieldName IN ({placeholders})
        """, WANTED_FIELDS)
    except sqlite3.DatabaseError as exc:
        report.degrade(f"item fields unreadable: {exc}")
        return
    for r in rows:
        item = items.get(r["itemID"])
        if item is not None and r["value"] is not None:
            item.fields[r["fieldName"]] = str(r["value"])


def _attach_creators(con, items, report) -> None:
    try:
        rows = _query(con, """
            SELECT ic.itemID, c.lastName, c.firstName
            FROM itemCreators ic JOIN creators c ON c.creatorID = ic.creatorID
            ORDER BY ic.itemID, ic.orderIndex
        """)
    except sqlite3.DatabaseError as exc:
        report.degrade(f"creators unreadable: {exc}")
        return
    for r in rows:
        item = items.get(r["itemID"])
        if item is not None:
            name = " ".join(x for x in (r["firstName"], r["lastName"]) if x)
            if name:
                item.creators.append(name)


def _attach_tags(con, items, report) -> None:
    try:
        rows = _query(con, """
            SELECT it.itemID, t.name FROM itemTags it JOIN tags t ON t.tagID = it.tagID
        """)
    except sqlite3.DatabaseError as exc:
        report.degrade(f"tags unreadable: {exc}")
        return
    for r in rows:
        item = items.get(r["itemID"])
        if item is not None:
            item.tags.append(r["name"])


def _read_collections(con, library_id: int, items, report) -> list[ZoteroCollection]:
    try:
        crows = _query(con, """
            SELECT collectionID, key, collectionName, parentCollectionID, libraryID
            FROM collections WHERE libraryID = ?
        """, (library_id,))
        mrows = _query(con, """
            SELECT ci.collectionID, ci.itemID FROM collectionItems ci
            WHERE ci.itemID NOT IN (SELECT itemID FROM deletedItems)
        """)
    except sqlite3.DatabaseError as exc:
        report.degrade(f"collections unreadable: {exc}")
        return []

    cols = {
        r["collectionID"]: ZoteroCollection(
            collection_id=r["collectionID"], key=r["key"], name=r["collectionName"],
            library_id=r["libraryID"], parent_id=r["parentCollectionID"],
        )
        for r in crows
    }
    for r in mrows:
        col = cols.get(r["collectionID"])
        item = items.get(r["itemID"])
        if col is not None and item is not None:
            col.item_keys.append(item.key)
            item.collections.append(col.key)
            item._collection_names.add((col.name or "").strip().casefold())
    return list(cols.values())


def _attach_children(con, items, report) -> None:
    """Attachments and annotations, rolled up onto their parent items.

    Annotation counts are a reading signal, not a bibliographic one: an item
    with highlights was read, an item with a stored PDF and no annotations was
    filed. The map does not use this, but the coverage panel does.
    """
    try:
        arows = _query(con, """
            SELECT parentItemID, contentType, lastRead FROM itemAttachments
            WHERE parentItemID IS NOT NULL
              AND itemID NOT IN (SELECT itemID FROM deletedItems)
        """)
    except sqlite3.DatabaseError as exc:
        report.degrade(f"attachments unreadable: {exc}")
        arows = []
    for r in arows:
        item = items.get(r["parentItemID"])
        if item is None:
            continue
        item.attachment_count += 1
        if (r["contentType"] or "") == "application/pdf":
            item.pdf_count += 1
        # Zotero 7 stamps an attachment when its reader opens it. Seconds since
        # the epoch, and the most direct evidence of reading the schema holds.
        if r["lastRead"]:
            try:
                when = datetime.fromtimestamp(int(r["lastRead"]), timezone.utc)
            except (TypeError, ValueError, OSError):
                when = None
            if when and (item.last_read is None or when > item.last_read):
                item.last_read = when

    # Annotations hang off the *attachment*, so they need a second hop to reach
    # the bibliographic parent.
    try:
        nrows = _query(con, """
            SELECT at.parentItemID AS grandparent, COUNT(*) AS n
            FROM itemAnnotations an
            JOIN itemAttachments at ON at.itemID = an.parentItemID
            WHERE an.itemID NOT IN (SELECT itemID FROM deletedItems)
              AND at.parentItemID IS NOT NULL
            GROUP BY at.parentItemID
        """)
    except sqlite3.DatabaseError as exc:
        report.degrade(f"annotations unreadable: {exc}")
        nrows = []
    for r in nrows:
        item = items.get(r["grandparent"])
        if item is not None:
            item.annotation_count = r["n"]

    # A note you wrote about an item is reading evidence of its own.
    try:
        note_rows = _query(con, """
            SELECT parentItemID, COUNT(*) AS n FROM itemNotes
            WHERE parentItemID IS NOT NULL
              AND itemID NOT IN (SELECT itemID FROM deletedItems)
            GROUP BY parentItemID
        """)
    except sqlite3.DatabaseError as exc:
        report.degrade(f"notes unreadable: {exc}")
        note_rows = []
    for r in note_rows:
        item = items.get(r["parentItemID"])
        if item is not None:
            item.note_count = r["n"]
