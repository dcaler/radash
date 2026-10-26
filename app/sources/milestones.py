"""HAARPi milestones — each project's `.haarpi/status.json`, read as written.

HAARPi writes this file after every `haarpi` verb: the full list of
milestones a project can reach (its stages, the paper's shared rungs, and a
track per venue with its rungs and its deck), each with its state, its
release, whose move it is, its target and the open trundlr tasks working
toward it. The schema is HAARPi's (`packages/haarpi/DESIGN_status_json.md`);
raDash owns only how it is shown.

So nothing here derives a milestone. An earlier version of this module did,
re-implementing HAARPi's release rules from file names, and it disagreed with
`haarpi status` in four places. A second derivation of the same facts is a
second answer, and the dashboard would have had to pick one.

What this module does own is how the file can be wrong. It is written by
another tool on another machine, so it can be missing (a project no verb has
run in since HAARPi learned to write it), unparseable, or a schema raDash has
not seen. Each of those costs that project's milestones and says why, never
the page. Its age is taken from the file's modification time rather than its
`generated` field, which is a naive timestamp in whatever timezone the runner
keeps.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

STATUS = Path(".haarpi") / "status.json"

# Schemas this reader understands. A higher one is read anyway and flagged,
# as the corpus ledger is: refusing forward-compatible data is worse than
# reading it with a caveat.
KNOWN_SCHEMAS = frozenset({1})


@dataclass
class StatusFile:
    payload: Optional[dict] = None
    written_at: Optional[datetime] = None
    note: Optional[str] = None

    @property
    def milestones(self) -> list[dict]:
        rows = (self.payload or {}).get("milestones")
        return [m for m in rows if isinstance(m, dict) and m.get("id")] \
            if isinstance(rows, list) else []

    @property
    def tracks(self) -> list[dict]:
        rows = (self.payload or {}).get("tracks")
        return [t for t in rows if isinstance(t, dict)] if isinstance(rows, list) else []


def read(folder) -> StatusFile:
    """One project's status file, or the reason there is none to show."""
    path = Path(folder) / STATUS
    if not path.exists():
        return StatusFile(note="no .haarpi/status.json yet — any haarpi verb run "
                               "in the project writes one")
    try:
        written = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as exc:
        return StatusFile(note=f"status.json unreadable: {type(exc).__name__}: {exc}")
    if not isinstance(raw, dict) or not isinstance(raw.get("milestones"), list):
        return StatusFile(written_at=written,
                          note="status.json has no milestone list")

    out = StatusFile(payload=raw, written_at=written)
    if raw.get("schema") not in KNOWN_SCHEMAS:
        out.note = (f"status.json schema {raw.get('schema')!r} is not one raDash "
                    f"knows ({sorted(KNOWN_SCHEMAS)}) — read anyway")
    return out
