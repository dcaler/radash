"""Last-good-response store for the network sources.

Small enough to be one function pair, important enough to be its own module:
every network adapter's failure path routes through `load`, and getting that
wrong means a dead tailnet blanks the dashboard instead of ageing it.

The cache is keyed by source, not by request, because raDash reads each source
once per refresh and wants the whole payload or none of it. A per-URL HTTP
cache would be a different design for a different access pattern.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlmodel import Session, select

from app.models import SourceCache


def save(session: Optional[Session], key: str, payload: Any,
         item_count: Optional[int] = None, note: Optional[str] = None) -> None:
    """Persist a successful read. A caller with no session is a no-op."""
    if session is None:
        return
    row = session.get(SourceCache, key) or SourceCache(key=key)
    row.payload = json.dumps(payload)
    row.fetched_at = datetime.now(timezone.utc)
    row.item_count = item_count
    row.note = note
    session.add(row)
    session.commit()


def load(session: Optional[Session], key: str) -> tuple[Optional[Any], Optional[datetime]]:
    """Return `(payload, fetched_at)`, or `(None, None)` if nothing is cached.

    A corrupt payload is treated as absent rather than raised: the caller is
    already on a failure path and does not need a second exception.
    """
    if session is None:
        return None, None
    row = session.get(SourceCache, key)
    if row is None or not row.payload:
        return None, None
    try:
        payload = json.loads(row.payload)
    except (ValueError, TypeError):
        return None, None
    fetched = row.fetched_at
    if fetched is not None and fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return payload, fetched


def ages(session: Optional[Session]) -> dict[str, datetime]:
    """When each cached source was last fetched — for the staleness column."""
    if session is None:
        return {}
    out: dict[str, datetime] = {}
    for row in session.exec(select(SourceCache)).all():
        fetched = row.fetched_at
        if fetched is not None and fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        out[row.key] = fetched
    return out
