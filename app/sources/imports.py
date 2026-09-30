"""Pasted imports — the machinery behind M2-T3 and M2-T8.

Two sources cannot be read automatically and never will be: Google Scholar has
no usable API, and your publication list lives in a CV written for humans. Both
arrive the same way — you paste text, raDash shows you what it extracted, and
only then does it commit.

The preview is not a nicety. Both parsers are heuristics over prose, so the
honest interface shows their output *before* it becomes the record, rather than
discovering at the gate that a title was truncated three refreshes ago.

Writes land in `import_dir`, inside raDash's own volume. That is the same class
of write as emitting a brief to `output/`: no source is touched, and the
read-only boundary is unaffected.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Imports join the repo's document revision chain: `ra` is the tool's initials,
# and a new datestamp starts a fresh cycle.
NAME = "{stamp}_{kind}_ra.txt"

MAX_BYTES = 512 * 1024      # a pasted list; anything larger is a mistake

_SAFE_KIND = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class ImportError_(ValueError):
    """Raised for input the endpoint should reject rather than store."""


def validate(text: str, kind: str) -> str:
    """Check size and shape before anything touches the filesystem."""
    if not _SAFE_KIND.match(kind):
        raise ImportError_(f"unrecognised import kind {kind!r}")
    if not text or not text.strip():
        raise ImportError_("nothing pasted")
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise ImportError_(
            f"{len(encoded)} bytes exceeds the {MAX_BYTES} byte limit — "
            "this expects a publication list, not a document")
    if "\x00" in text:
        raise ImportError_("binary content; paste text")
    return text


def save(import_dir: Path, text: str, kind: str,
         when: Optional[datetime] = None) -> Path:
    """Write the paste, creating `import_dir` on first use.

    Same-day re-imports overwrite rather than accumulate: the revision chain
    treats a datestamp as one cycle, and the newest file is what gets read, so
    keeping every attempt would only leave debris that never gets served.
    """
    validate(text, kind)
    when = when or datetime.now(timezone.utc)
    import_dir.mkdir(parents=True, exist_ok=True)
    path = import_dir / NAME.format(stamp=when.strftime("%y%m%d"), kind=kind)
    path.write_text(text, encoding="utf-8")
    return path


def latest(import_dir: Path, kind: str) -> Optional[Path]:
    """Most recent stored paste of one kind, if any."""
    if not import_dir.is_dir():
        return None
    matches = sorted(import_dir.glob(f"*_{kind}_ra.txt"))
    return matches[-1] if matches else None


def read_latest(import_dir: Path, kind: str) -> Optional[str]:
    path = latest(import_dir, kind)
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
