"""Build a minimal Zotero database in a temp file.

The suite must never need the real library present (`conftest.py`), but the
Zotero reader is almost entirely SQL against Zotero's EAV schema, so testing it
against a mock object would test nothing. This builds the tables the reader
actually touches, with the same column names and the same relationships —
including the two that are easy to get wrong: trashed items live in
`deletedItems` rather than being deleted, and annotations hang off the
*attachment*, so reaching their bibliographic parent takes two hops.
"""
import sqlite3
from pathlib import Path

ITEM_TYPES = {1: "journalArticle", 2: "attachment", 3: "annotation", 4: "note", 5: "book"}
FIELDS = {1: "title", 2: "abstractNote", 3: "DOI", 4: "date", 5: "publicationTitle"}

SCHEMA = """
CREATE TABLE version (schema TEXT PRIMARY KEY, version INT NOT NULL);
CREATE TABLE libraries (libraryID INTEGER PRIMARY KEY, type TEXT NOT NULL);
CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
CREATE TABLE items (itemID INTEGER PRIMARY KEY, itemTypeID INT, dateAdded TEXT,
                    dateModified TEXT, libraryID INT, key TEXT);
CREATE TABLE deletedItems (itemID INTEGER PRIMARY KEY, dateDeleted TEXT);
CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value TEXT);
CREATE TABLE itemData (itemID INT, fieldID INT, valueID INT);
CREATE TABLE creators (creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT);
CREATE TABLE itemCreators (itemID INT, creatorID INT, creatorTypeID INT, orderIndex INT);
CREATE TABLE tags (tagID INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE itemTags (itemID INT, tagID INT, type INT);
CREATE TABLE collections (collectionID INTEGER PRIMARY KEY, collectionName TEXT,
                          parentCollectionID INT, libraryID INT, key TEXT);
CREATE TABLE collectionItems (collectionID INT, itemID INT, orderIndex INT);
CREATE TABLE itemAttachments (itemID INTEGER PRIMARY KEY, parentItemID INT,
                              linkMode INT, contentType TEXT, path TEXT,
                              lastRead INT);
CREATE TABLE itemAnnotations (itemID INTEGER PRIMARY KEY, parentItemID INT,
                              type INT, text TEXT, comment TEXT);
CREATE TABLE itemNotes (itemID INTEGER PRIMARY KEY, parentItemID INT,
                        note TEXT, title TEXT);
"""

LONG_ABSTRACT = (
    "This paper examines the diffusion of residential solar adoption under "
    "uncertainty, using an agent-based model calibrated to household survey "
    "data. We find that peer effects dominate price effects once penetration "
    "exceeds a threshold, and that the threshold is sensitive to the spatial "
    "structure of the network rather than to its density alone."
)


def build(path: Path) -> Path:
    """Write a small but structurally faithful library to `path`."""
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)

    con.execute("INSERT INTO version VALUES ('userdata', 129)")
    con.executemany("INSERT INTO libraries VALUES (?,?)", [(1, "user"), (2, "group")])
    con.executemany("INSERT INTO itemTypes VALUES (?,?)", list(ITEM_TYPES.items()))
    con.executemany("INSERT INTO fields VALUES (?,?)", list(FIELDS.items()))

    # 1: a full article. 2: thin (title only). 3: trashed. 4: in the group
    # library. 5: a book.
    items = [
        (1, 1, "2024-01-01", "2024-02-01", 1, "AAAA1111"),
        (2, 1, "2024-01-02", "2024-02-02", 1, "BBBB2222"),
        (3, 1, "2024-01-03", "2024-02-03", 1, "CCCC3333"),
        (4, 1, "2024-01-04", "2024-02-04", 2, "DDDD4444"),
        (5, 5, "2024-01-05", "2024-02-05", 1, "EEEE5555"),
        # children of item 1
        (10, 2, "2024-01-06", "2024-02-06", 1, "ATT10000"),
        (11, 3, "2024-01-07", "2024-02-07", 1, "ANN11000"),
        (12, 3, "2024-01-08", "2024-02-08", 1, "ANN12000"),
        (13, 4, "2024-01-09", "2024-02-09", 1, "NOTE1300"),
    ]
    con.executemany("INSERT INTO items VALUES (?,?,?,?,?,?)", items)
    con.execute("INSERT INTO deletedItems VALUES (3, '2024-03-01')")

    values = {
        1: "Kindred effects in residential solar adoption",
        2: LONG_ABSTRACT,
        3: "10.1016/J.FICT.2015.01.001",
        4: "2015-03",
        5: "Renewable Energy",
        6: "A note with no abstract",
        7: "Trashed work",
        8: "Group library work",
        9: "A book about networks",
        10: "2021",
    }
    con.executemany("INSERT INTO itemDataValues VALUES (?,?)", list(values.items()))
    con.executemany("INSERT INTO itemData VALUES (?,?,?)", [
        (1, 1, 1), (1, 2, 2), (1, 3, 3), (1, 4, 4), (1, 5, 5),
        (2, 1, 6),                      # title only -> thin
        (3, 1, 7),                      # trashed
        (4, 1, 8),                      # group library
        (5, 1, 9), (5, 4, 10),
    ])

    con.executemany("INSERT INTO creators VALUES (?,?,?)",
                    [(1, "Octavia", "Butler"), (2, "A.", "Coauthor")])
    con.executemany("INSERT INTO itemCreators VALUES (?,?,?,?)",
                    [(1, 1, 1, 0), (1, 2, 1, 1), (5, 1, 1, 0)])
    con.execute("INSERT INTO tags VALUES (1, 'solar')")
    con.execute("INSERT INTO itemTags VALUES (1, 1, 0)")

    con.executemany("INSERT INTO collections VALUES (?,?,?,?,?)", [
        (1, "parableSower", None, 1, "COLL0001"),
        (2, "DRvehicle", None, 1, "COLL0002"),
        (3, "Co-Adoption Paper", None, 2, "COLL0003"),   # group library
    ])
    con.executemany("INSERT INTO collectionItems VALUES (?,?,?)", [
        (1, 1, 0), (1, 2, 1), (1, 3, 2), (2, 5, 0),
    ])

    # lastRead is Zotero 7's stamp for "opened in the reader". Null on the
    # book, which is the common case: most items carry no reading evidence.
    con.execute("INSERT INTO itemAttachments VALUES "
                "(10, 1, 1, 'application/pdf', 'x.pdf', 1778151560)")
    con.execute("INSERT INTO itemNotes VALUES (13, 5, 'A note about the book', 'Note')")
    con.executemany("INSERT INTO itemAnnotations VALUES (?,?,?,?,?)", [
        (11, 10, 1, "highlighted text", ""),
        (12, 10, 1, "more highlighted text", "a comment"),
    ])

    con.commit()
    con.close()
    return path
