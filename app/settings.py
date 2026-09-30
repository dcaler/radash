"""Settings you can change without redeploying.

Three kinds of configuration live in this application and only one of them
belongs here.

**Deployment facts** — the database URL, the five source paths, the port —
mirror what Docker was told at start. A page that let you edit a bind-mount
path would change the field and not the mount, so the app would report one
thing and read another. Those stay in the environment, and this module
refuses to manage them.

**Secrets** stay in the environment too. `S2_API_KEY` is read at call time and
never echoed, not even masked; storing it here would put it in every backup of
the data volume.

**Everything else** is a judgement — who you are across the indexes, where
your public claim lives, how much text is too little to map, how eager the
dedup engine should be. Those were either environment variables requiring a
redeploy or, worse, constants written into source files. They are editable
here, and the environment remains the default they start from.

Precedence is environment first as the bootstrap, then your override. Both are
always visible, so a surprising number can be traced to whoever set it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlmodel import Session, select

from app.models import Setting

CHANGED_AT = "_settings_changed_at"   # internal; not an editable setting


@dataclass(frozen=True)
class SettingDef:
    key: str
    label: str
    help: str
    group: str
    kind: str                     # "text" | "number" | "list"
    env: Optional[str] = None     # environment variable it falls back to
    default: Optional[str] = None
    affects_ledger: bool = False  # changing it makes the current ledger stale
    validate: Optional[Callable[[str], str]] = None


def _url(v: str) -> str:
    v = v.strip()
    if v and not v.startswith(("http://", "https://")):
        raise ValueError("must start with http:// or https://")
    return v


def _page_url(v: str) -> str:
    """A URL to a page, not to a file.

    Pointing at the CV PDF is the obvious mistake -- it is the thing actually
    called "my CV" -- and it failed three steps later, at the next rebuild, as
    "no public claim to compare against". Refuse it here and say why.
    """
    v = _url(v)
    if v.split("?")[0].lower().endswith((".pdf", ".doc", ".docx")):
        raise ValueError(
            "that is the CV file; raDash reads the HTML page that lists your "
            "publications. Use the page on your site that links this file.")
    return v


def _ratio(v: str) -> str:
    f = float(v)
    if not 0.0 < f <= 1.0:
        raise ValueError("must be between 0 and 1")
    return str(f)


def _toggle(v: str) -> str:
    """A flag, spelt the way a person would spell it."""
    v = v.strip().lower()
    if v in ("on", "true", "yes", "1"):
        return "on"
    if v in ("", "off", "false", "no", "0"):
        return "off"
    raise ValueError("must be on or off")


def _whole_int(v: str) -> str:
    """Zero allowed, negative not. Zero is how a schedule is turned off."""
    n = int(v)
    if n < 0:
        raise ValueError("must be 0 or more")
    return str(n)


def _positive_int(v: str) -> str:
    n = int(v)
    if n < 1:
        raise ValueError("must be 1 or more")
    return str(n)


def _author_ids(v: str) -> str:
    ids = [p.strip() for p in v.split(",") if p.strip()]
    for i in ids:
        if not i.startswith("A") or not i[1:].isdigit():
            raise ValueError(f"{i!r} is not an OpenAlex author id (A followed by digits)")
    return ",".join(ids)


REGISTRY: tuple[SettingDef, ...] = (
    SettingDef("OPENALEX_AUTHOR_IDS", "OpenAlex author ids",
               "Comma-separated. More than one is supported for a record split "
               "across duplicate profiles; the first is treated as the one you "
               "have vouched for, and work seen only on the others is proposed "
               "for adoption rather than absorbed.",
               "Identity", "list", env="OPENALEX_AUTHOR_IDS",
               affects_ledger=True, validate=_author_ids),
    SettingDef("CV_URL", "Public CV page",
               "The HTML page on your site that lists your publications -- "
               "not the PDF it links to. raDash reads it as a source of "
               "claims, not of truth, and reports where it diverges from what "
               "the indexes hold. Leave empty to skip the comparison.",
               "Identity", "text", env="CV_URL",
               affects_ledger=True, validate=_page_url),
    SettingDef("CONTACT_EMAIL", "Contact email",
               "Sent to OpenAlex and Semantic Scholar, which give better "
               "service to identified callers. Optional.",
               "Identity", "text", env="CONTACT_EMAIL"),
    SettingDef("TRUNDLR_URL", "trundlr API",
               "Where the project ledger lives. Unreachable is a normal state: "
               "raDash serves the last good response and labels its age.",
               "Services", "text", env="TRUNDLR_URL", validate=_url),
    SettingDef("THIN_TEXT_CHARS", "Thin-text threshold",
               "Below this many characters of title plus abstract, an item "
               "cannot carry a position on the map. Raising it makes the "
               "coverage panel stricter about what it counts as usable.",
               "Judgements", "number", default="200",
               affects_ledger=True, validate=_positive_int),
    SettingDef("TITLE_RATIO", "Duplicate: title match",
               "How similar two titles must be before a duplicate is proposed. "
               "Lowering it lengthens your gate queue and catches more real "
               "duplicates; raising it does the reverse.",
               "Judgements", "number", default="0.92",
               affects_ledger=True, validate=_ratio),
    SettingDef("MATCH_RATIO", "CV match threshold",
               "How similar a CV line and an indexed title must be to count as "
               "the same work. Too high and your own publications read as "
               "unclaimed; too low and a paper is absolved by the wrong line.",
               "Judgements", "number", default="0.78",
               affects_ledger=True, validate=_ratio),
    SettingDef("READ_TAG", "\"I have read this\" tag",
               "A Zotero tag you apply to items you have actually read. This "
               "is the only reliable signal there is: Zotero began recording "
               "when a PDF was opened in May 2026, and reading anywhere else "
               "-- Preview, a tablet, paper -- leaves no trace at all. Tag in "
               "Zotero and raDash will see it; it stays yours, travels with "
               "the item, and can be applied to a hundred items at once.",
               "Identity", "text", default="read"),
    SettingDef("READ_COLLECTION", "\"I have read this\" collection",
               "A Zotero collection whose members count as read. An "
               "alternative to the tag, and easier for marking a backlog: "
               "select many items and drag once. Note that it adds a name to "
               "the namespace your project join uses, so raDash excludes it "
               "from that comparison. Leave empty to use only the tag.",
               "Identity", "text", default="Read"),
    SettingDef("CLUSTER_DIMS", "Clustering dimensions",
               "How many components the clustering sees. Fewer gives a few "
               "large areas with almost everything assigned; more gives many "
               "small ones with most items left between regions. Neither is "
               "wrong — it decides whether the map shows continents or towns.",
               "Judgements", "number", default="40",
               affects_ledger=True, validate=_positive_int),
    SettingDef("MIN_CLUSTER_SIZE", "Smallest region",
               "Fewer items than this and a cluster is a handful of papers "
               "that happen to sit together; naming it would overstate what "
               "is there.",
               "Judgements", "number", default="8",
               affects_ledger=True, validate=_positive_int),
    SettingDef("DENSE_REGION", "Densely read region",
               "At or above this many collected items, a region counts as "
               "one you know — the grounding signal 3 rests on. Too low and "
               "a handful of papers becomes a claim to expertise; too high "
               "and the signal never fires on a library this size.",
               "Judgements", "number", default="25", validate=_positive_int),
    SettingDef("THIN_REGION", "Thinly read region",
               "At or below this many collected items, a region counts as "
               "one you have barely touched. It gates signals 2 and 4, which "
               "are the two readings of an area you have not explored.",
               "Judgements", "number", default="15", validate=_positive_int),
    SettingDef("QUIET_YEARS", "Region goes quiet after",
               "Years since the newest thing you hold in a region before its "
               "literature counts as having gone quiet. Measured on "
               "publication years in your library, so it says when the "
               "papers were written, not when you collected them.",
               "Judgements", "number", default="3", validate=_positive_int),
    SettingDef("OFFLINE", "Offline",
               "Stop raDash opening a socket at all. Every network source "
               "serves its cache with the age attached, and a gather or a "
               "refresh reports that it was not attempted rather than "
               "failing slowly. This is the normal mode on a train and the "
               "honest mode when the tailnet is down: stale-and-labelled "
               "beats absent.",
               "Services", "toggle", default="off", validate=_toggle),
    SettingDef("REFRESH_DAYS", "Refresh every",
               "Days before the weekly cycle considers the picture stale and "
               "runs itself. Measured against the newest snapshot rather than "
               "against how long the container has been up, so a redeploy "
               "neither triggers a refresh nor delays one. Set 0 to leave "
               "refreshing entirely to you.",
               "Services", "number", default="7", validate=_whole_int),
    SettingDef("RETAIN_SNAPSHOTS", "Snapshots kept",
               "How many snapshots and fitted spaces survive a refresh. Both "
               "are derived state and both are large — a space carries a row "
               "per document and a vector array beside the database. Your "
               "rulings are not snapshot-scoped and are never pruned.",
               "Services", "number", default="12", validate=_positive_int),
    SettingDef("SCHOLAR_STALE_DAYS", "Scholar goes stale after",
               "Days before a pasted Google Scholar import stops being "
               "presented as current. It is the oldest lane by construction.",
               "Judgements", "number", default="60", validate=_positive_int),
    SettingDef("CV_STALE_DAYS", "Public CV goes stale after",
               "Days before your CV page is flagged as behind the record.",
               "Judgements", "number", default="120", validate=_positive_int),
)

BY_KEY = {d.key: d for d in REGISTRY}

# Held in the process so every read is cheap; reloaded at startup and rewritten
# on every change. raDash runs as one container, so there is no second process
# to fall out of step with.
_overrides: dict[str, str] = {}


def load(engine) -> None:
    """Populate the in-process overrides from the database."""
    from sqlmodel import Session as _Session
    _overrides.clear()
    try:
        with _Session(engine) as session:
            for row in session.exec(select(Setting)).all():
                if row.value is not None:
                    _overrides[row.key] = row.value
    except Exception:
        pass   # a database that cannot be read yet leaves the environment in charge


def raw(key: str) -> Optional[str]:
    """The effective value, override first, then environment, then default."""
    if key in _overrides:
        return _overrides[key]
    d = BY_KEY.get(key)
    if d is None:
        return None
    if d.env:
        env = os.getenv(d.env)
        if env not in (None, ""):
            return env
    return d.default


def source(key: str) -> str:
    if key in _overrides:
        return "you"
    d = BY_KEY.get(key)
    if d and d.env and os.getenv(d.env):
        return "environment"
    return "default"


def text(key: str, fallback: str = "") -> str:
    v = raw(key)
    return fallback if v is None else v


def flag(key: str, fallback: bool = False) -> bool:
    """A toggle, read the same way everywhere.

    Anything unparseable falls back to what the code shipped with, for the
    same reason `num` does: a setting nobody can read should degrade to the
    behaviour that was tested, not to the behaviour that happens to be falsy.
    """
    v = raw(key)
    if v is None:
        return fallback
    return v.strip().lower() in ("on", "true", "yes", "1")


def num(key: str, fallback: float) -> float:
    """A numeric judgement, with the caller's constant as the floor of trust.

    Call sites keep their constant as the argument, so a setting that is
    missing or unparseable degrades to the value the code shipped with rather
    than to zero.
    """
    v = raw(key)
    if v is None:
        return fallback
    try:
        return float(v)
    except (TypeError, ValueError):
        return fallback


def set_value(session: Session, key: str, value: Optional[str]) -> str:
    """Store an override. Returns the stored value; raises ValueError if invalid."""
    d = BY_KEY.get(key)
    if d is None:
        raise ValueError(f"{key!r} is not an editable setting")
    cleaned = (value or "").strip()
    if cleaned and d.validate:
        cleaned = d.validate(cleaned)
    row = session.get(Setting, key) or Setting(key=key)
    row.value = cleaned
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    _touch(session, d)
    session.commit()
    _overrides[key] = cleaned
    return cleaned


def clear(session: Session, key: str) -> None:
    """Drop an override, so the environment or default takes over again."""
    d = BY_KEY.get(key)
    if d is None:
        raise ValueError(f"{key!r} is not an editable setting")
    row = session.get(Setting, key)
    if row is not None:
        session.delete(row)
    _touch(session, d)
    session.commit()
    _overrides.pop(key, None)


def _touch(session: Session, d: SettingDef) -> None:
    """Record when a ledger-affecting setting last moved.

    Changing a threshold silently changes how many duplicates are proposed and
    how much text counts as thin, without touching a single source. The ledger
    compares this against its snapshot time and says it needs rebuilding.
    """
    if not d.affects_ledger:
        return
    row = session.get(Setting, CHANGED_AT) or Setting(key=CHANGED_AT)
    row.value = datetime.now(timezone.utc).isoformat()
    session.add(row)


def changed_at(session: Session) -> Optional[datetime]:
    row = session.get(Setting, CHANGED_AT)
    if row is None or not row.value:
        return None
    try:
        return datetime.fromisoformat(row.value)
    except ValueError:
        return None


def describe() -> list[dict]:
    """Every editable setting, its value, and where that value came from."""
    return [
        {
            "key": d.key, "label": d.label, "help": d.help, "group": d.group,
            "kind": d.kind, "value": raw(d.key), "source": source(d.key),
            "default": d.default,
            "environment": os.getenv(d.env) if d.env else None,
            "affects_ledger": d.affects_ledger,
        }
        for d in REGISTRY
    ]
