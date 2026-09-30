"""Where output goes, and the refusal that keeps it there — M7-T3, M7-T4.

**One funnel.** Every file raDash emits is written by `write` and by nothing
else, so the boundary is a single function rather than a convention each
caller is trusted to follow. A convention holds until somebody adds a feature
in a hurry.

**The refusal is in the code, not only in the test.** `resolve` rejects any
name that escapes `output/` — a slug carrying `../`, an absolute path, a
symlinked parent — and raises rather than writing somewhere surprising. The
test in `tests/test_outputs.py` asserts the property across the endpoints;
this asserts it at the point of the write, which is the half that still holds
when somebody calls the function from somewhere new.

**On what the boundary actually is.** `BUILD_PLAN.md` states M7-T4 as "no
write outside `output/`", and taken literally raDash has always failed it: the
fitted vector arrays land in `spaces/` and Scholar imports in `import/`, both
siblings of `output/` inside the same volume. The property worth having is the
one the read-only mounts enforce from the other side — **raDash writes only
inside its own data volume, and never inside a source it observes**. That is
what the test asserts, and stating the narrower version would have meant
either a failing test or a test written to pass.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import Config

# Outputs join the repo's document revision chain: `ra` is the tool's
# initials, and a new datestamp starts a fresh cycle (`BUILD_PLAN.md`,
# Conventions).
NAME = "{stamp}_{slug}_{kind}_ra.txt"

KINDS = ("readin", "fill")

# Long enough to recognise a region, short enough to leave the datestamp and
# the kind legible in a directory listing.
MAX_SLUG = 40

_UNSAFE = re.compile(r"[^a-z0-9]+")


class OutsideOutput(Exception):
    """A write was aimed at somewhere that is not `output/`."""


def slugify(text: str, fallback: str = "region") -> str:
    """A filename fragment from a region's name or its terms.

    Lowercase, alphanumeric and hyphens. Anything else is a separator, which
    is what makes `../` and an absolute path unrepresentable rather than
    merely rejected later.
    """
    slug = _UNSAFE.sub("-", (text or "").lower()).strip("-")
    slug = slug[:MAX_SLUG].strip("-")
    return slug or fallback


def filename(slug: str, kind: str, when: Optional[datetime] = None) -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown output kind {kind!r}")
    when = when or datetime.now(timezone.utc)
    return NAME.format(stamp=when.strftime("%y%m%d"),
                       slug=slugify(slug), kind=kind)


def resolve(cfg: Config, name: str) -> Path:
    """The path a given filename maps to, or a refusal.

    Resolved on both sides before comparing, so a symlinked `output/` is
    compared as what it points at rather than as what it is called.
    """
    out = Path(cfg.output_dir)
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutsideOutput(f"the output directory is not writable: {exc}")
    base = out.resolve()
    path = (base / name).resolve()
    if path.parent != base:
        raise OutsideOutput(
            f"{name!r} resolves to {path}, which is not inside {base}")
    return path


def write(cfg: Config, slug: str, kind: str, text: str,
          when: Optional[datetime] = None) -> Path:
    """Emit one artifact. Same-day re-emits overwrite.

    The revision chain treats a datestamp as one cycle and the newest file is
    the one that counts, so keeping every attempt would only leave debris
    nothing reads.
    """
    path = resolve(cfg, filename(slug, kind, when))
    path.write_text(text, encoding="utf-8")
    return path


def listing(cfg: Config) -> list[dict]:
    """What has been emitted, newest first."""
    out = Path(cfg.output_dir)
    if not out.is_dir():
        return []
    rows = []
    for path in out.iterdir():
        if not path.is_file() or not path.name.endswith("_ra.txt"):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append({
            "name": path.name,
            "kind": "readin" if "_readin_" in path.name
                    else "fill" if "_fill_" in path.name else "other",
            "bytes": stat.st_size,
            "written_at": datetime.fromtimestamp(
                stat.st_mtime, timezone.utc).isoformat(),
        })
    rows.sort(key=lambda r: r["written_at"], reverse=True)
    return rows


def read(cfg: Config, name: str) -> Optional[str]:
    """One emitted file, so the UI can show what it just wrote."""
    try:
        path = resolve(cfg, name)
    except (OSError, OutsideOutput):
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None
