"""Source adapters: read everything, write nothing.

Every adapter in this package answers the same question — *what can raDash see
right now, and how much of it should be trusted* — and answers it the same way,
through `SourceReport`. That uniformity is what makes `/api/sources` possible:
the diagnostic does not know what a Zotero library or an OpenAlex profile is,
only that each source reports a status, a count, an age and its failures.

Two rules hold across every adapter here:

**Nothing raises.** A source that is absent, unreachable, malformed or half-read
returns a report saying so. raDash is an observatory; a telescope that refuses
to open because one mirror is dirty is worse than one that says which mirror.
Adapters therefore catch broadly and deliberately — the exception text becomes
the `note`, and the panel that needed the data degrades on its own.

**Nothing writes.** No adapter opens a source for writing, creates a file beside
one, or sends a non-idempotent request. Docker's `:ro` mounts enforce this for
the filesystem sources and `tests/test_sources_readonly.py` checks the property
directly for every handle this package opens.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.models import SourceStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class SourceReport:
    """One adapter's account of itself.

    `data` carries the payload for callers that want it; `/api/sources` ignores
    it and reports only the metadata, so the diagnostic stays cheap to render
    even when the payload is thousands of items.
    """
    key: str
    status: SourceStatus = SourceStatus.missing
    count: Optional[int] = None
    note: Optional[str] = None
    read_at: datetime = field(default_factory=utcnow)
    source_mtime: Optional[datetime] = None   # age of the data, not of the read
    elapsed_ms: Optional[int] = None
    failures: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)  # per-kind breakdown
    data: Any = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return self.status in (SourceStatus.ok, SourceStatus.degraded)

    def fail(self, message: str, status: SourceStatus = SourceStatus.error) -> "SourceReport":
        """Record a failure without discarding what was already read."""
        self.failures.append(message)
        self.status = status
        if self.note is None:
            self.note = message
        return self

    def degrade(self, message: str) -> "SourceReport":
        """Partial success: data is usable but incomplete, and says why."""
        self.failures.append(message)
        if self.status is not SourceStatus.error:
            self.status = SourceStatus.degraded
        self.note = "; ".join(self.failures[:3])
        return self

    def summary(self) -> dict:
        """The shape `/api/sources` serves. Excludes `data` by design."""
        return {
            "key": self.key,
            "status": self.status.value,
            "ok": self.ok,
            "count": self.count,
            "counts": self.counts,
            "note": self.note,
            "read_at": self.read_at.isoformat(),
            "source_mtime": self.source_mtime.isoformat() if self.source_mtime else None,
            "age_seconds": self.age_seconds,
            "elapsed_ms": self.elapsed_ms,
            "failures": self.failures,
        }

    @property
    def age_seconds(self) -> Optional[float]:
        """Seconds since the underlying data changed, where that is knowable.

        This is the number that matters for staleness — a read taken one second
        ago of a file last written in March is fresh as a *read* and six months
        stale as *data*, and only the second reading is worth showing.
        """
        if self.source_mtime is None:
            return None
        return (utcnow() - self.source_mtime).total_seconds()


class timer:
    """Context manager that stamps elapsed_ms onto a report."""

    def __init__(self, report: SourceReport):
        self.report = report

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self.report

    def __exit__(self, *exc):
        self.report.elapsed_ms = int((time.perf_counter() - self._t0) * 1000)
        return False
