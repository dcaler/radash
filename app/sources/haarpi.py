"""HAARPi project folders — `haarpi.yaml` and the corpus ledger.

Each research project folder carries a `haarpi.yaml` describing what the
project is, which trundlr project it belongs to, and the pipeline stages it
moves through; some also carry `.haarpi/corpus_ledger.json`, the list of Zotero
items admitted to that project's corpus.

This adapter is deliberately the most forgiving in the package. These files are
written by other tools and edited by hand, so their schema drifts: a stage
directory named in the yaml may not exist on disk, a `trundlr_project_id` may
point at a deleted project, a ledger may be a version raDash has not seen. None
of that is an error — it is **drift**, and drift is a finding the dashboard
should show, not an exception that hides every other project.

Every project therefore parses independently. One unparseable yaml costs you
that project and a line in `failures`, never the read.

What this adapter computes beyond parsing is **last movement**: the most recent
modification time across a project's stage directories. Stalled work surfaces
by silence, and silence is only visible if something measures it.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from app.config import Config
from app.models import SourceStatus
from app.sources import SourceReport, timer, utcnow

KEY = "haarpi"

MANIFEST = "haarpi.yaml"
LEDGER = Path(".haarpi") / "corpus_ledger.json"

# Folders are `{YYMMDD}_{slug}`; the datestamp is the cycle, the slug the name.
FOLDER_RE = re.compile(r"^(?P<stamp>\d{6})_(?P<slug>.+)$")

# Directories that are never a project.
SKIP_DIRS = frozenset({"@eaDir", "old", ".git", "__pycache__", "archive"})

# Ledger schema versions this reader understands. A higher one is read anyway
# and flagged, because refusing to read forward-compatible data is worse than
# reading it with a caveat.
KNOWN_LEDGER_VERSIONS = frozenset({1})


@dataclass
class Stage:
    name: str
    dir: Optional[str] = None
    tool: Optional[str] = None
    attended: bool = False
    exists: bool = False
    file_count: int = 0
    last_modified: Optional[datetime] = None


@dataclass
class HaarpiProject:
    folder: str                      # absolute path
    folder_name: str                 # `260601_lathe`
    slug: str = ""                   # `lathe`
    stamp: Optional[str] = None      # `260601`
    name: str = ""
    short_title: Optional[str] = None
    brief: Optional[str] = None
    trundlr_project_id: Optional[int] = None
    trundlr_priority: Optional[int] = None
    stages: list[Stage] = field(default_factory=list)
    ledger_keys: list[str] = field(default_factory=list)   # Zotero item keys
    ledger_version: Optional[int] = None
    has_manifest: bool = False
    has_ledger: bool = False
    drift: list[str] = field(default_factory=list)
    # A stage declared in haarpi.yaml with no directory yet. Every project has
    # some: they are the stages it has not reached. Kept apart from drift so a
    # normal pipeline position does not read as a fault.
    pending: list[str] = field(default_factory=list)

    @property
    def is_haarpi(self) -> bool:
        """A directory is a HAARPi project when it says so.

        Without this raDash treats every folder under the projects directory
        as a HAARPi project and reports the ones that are not — a teaching
        folder, a convening, an ideas scratchpad — as *drift*. That is not
        drift, it is a folder, and counting it as a defect pushed the whole
        source to `degraded` for no reason.
        """
        return self.has_manifest

    @property
    def last_movement(self) -> Optional[datetime]:
        times = [s.last_modified for s in self.stages if s.last_modified]
        return max(times) if times else None

    @property
    def days_since_movement(self) -> Optional[float]:
        last = self.last_movement
        if last is None:
            return None
        return (utcnow() - last).total_seconds() / 86400

    @property
    def furthest_stage(self) -> Optional[str]:
        """The last stage with anything in it — a crude pipeline position."""
        reached = [s.name for s in self.stages if s.file_count]
        return reached[-1] if reached else None


@dataclass
class HaarpiCorpus:
    projects: list[HaarpiProject] = field(default_factory=list)

    @property
    def ledger_keys(self) -> set[str]:
        keys: set[str] = set()
        for p in self.projects:
            keys.update(p.ledger_keys)
        return keys


def read(cfg: Config) -> SourceReport:
    """Walk the projects directory, parsing each project in isolation."""
    report = SourceReport(key=KEY)
    root = cfg.projects_dir

    if not root.exists():
        report.status = SourceStatus.missing
        report.note = f"not mounted at {root}"
        return report

    corpus = HaarpiCorpus()
    try:
        with timer(report):
            for entry in _project_dirs(root):
                try:
                    corpus.projects.append(_read_project(entry))
                except Exception as exc:
                    report.degrade(f"{entry.name}: {type(exc).__name__}: {exc}")
    except OSError as exc:
        report.fail(f"cannot list {root}: {exc}")
        return report

    corpus.projects.sort(key=lambda p: p.folder_name)
    report.data = corpus
    # The count is HAARPi projects, not directories. A folder with no manifest
    # is somebody else's, and saying "22 projects" of which five are not
    # projects is the kind of number this dashboard exists not to produce.
    report.count = sum(1 for p in corpus.projects if p.is_haarpi)

    drifted = [p for p in corpus.projects if p.is_haarpi and p.drift]
    pending = sum(len(p.pending) for p in corpus.projects if p.is_haarpi)
    report.counts = {
        "haarpi_projects": sum(1 for p in corpus.projects if p.is_haarpi),
        "other_folders": sum(1 for p in corpus.projects if not p.is_haarpi),
        "directories_scanned": len(corpus.projects),
        "with_ledger": sum(1 for p in corpus.projects if p.has_ledger),
        "ledger_items": sum(len(p.ledger_keys) for p in corpus.projects),
        "with_trundlr_id": sum(1 for p in corpus.projects if p.trundlr_project_id),
        "stages_not_started": pending,
        "drifted": len(drifted),
    }
    for p in drifted:
        for d in p.drift:
            report.failures.append(f"{p.folder_name}: {d}")

    if report.status is not SourceStatus.degraded:
        report.status = SourceStatus.ok
    if drifted:
        report.note = (
            f"{len(drifted)} HAARPi project(s) name something in haarpi.yaml "
            "that is not on disk — usually a pipeline stage not yet begun")
        if report.status is SourceStatus.ok:
            report.status = SourceStatus.degraded
    else:
        others = report.counts["other_folders"]
        report.note = (
            f"{report.count} HAARPi projects"
            + (f"; {others} other folders ignored" if others else "")
            + (f"; {pending} pipeline stages not started yet" if pending else ""))
    return report


def _project_dirs(root: Path) -> list[Path]:
    """Immediate subdirectories that look like project folders."""
    out = []
    with os.scandir(root) as it:
        for e in it:
            if not e.is_dir(follow_symlinks=False):
                continue
            if e.name in SKIP_DIRS or e.name.startswith("."):
                continue
            out.append(Path(e.path))
    return sorted(out)


def _read_project(folder: Path) -> HaarpiProject:
    m = FOLDER_RE.match(folder.name)
    proj = HaarpiProject(
        folder=str(folder),
        folder_name=folder.name,
        slug=(m.group("slug") if m else folder.name),
        stamp=(m.group("stamp") if m else None),
    )
    proj.name = proj.slug

    manifest = folder / MANIFEST
    if manifest.exists():
        proj.has_manifest = True
        _apply_manifest(proj, manifest)

    ledger = folder / LEDGER
    if ledger.exists():
        proj.has_ledger = True
        _apply_ledger(proj, ledger)

    return proj


def _apply_manifest(proj: HaarpiProject, path: Path) -> None:
    """Parse `haarpi.yaml`. A malformed file costs the manifest, not the project."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    except (yaml.YAMLError, OSError) as exc:
        proj.drift.append(f"{MANIFEST} unparseable: {exc}")
        return
    if not isinstance(raw, dict):
        proj.drift.append(f"{MANIFEST} is {type(raw).__name__}, expected a mapping")
        return

    proj.name = str(raw.get("name") or proj.slug)
    proj.short_title = _opt_str(raw.get("short_title"))
    proj.brief = _opt_str(raw.get("brief"))
    proj.trundlr_priority = _opt_int(raw.get("trundlr_priority"))
    proj.trundlr_project_id = _opt_int(raw.get("trundlr_project_id"))
    if raw.get("trundlr_project_id") is not None and proj.trundlr_project_id is None:
        proj.drift.append(
            f"trundlr_project_id is not an integer: {raw.get('trundlr_project_id')!r}")

    stages = raw.get("stages")
    if isinstance(stages, dict):
        for name, spec in stages.items():
            proj.stages.append(_read_stage(proj, str(name), spec))
    elif stages is not None:
        proj.drift.append(f"stages is {type(stages).__name__}, expected a mapping")


def _read_stage(proj: HaarpiProject, name: str, spec) -> Stage:
    stage = Stage(name=name)
    if not isinstance(spec, dict):
        proj.drift.append(f"stage {name!r} is {type(spec).__name__}, expected a mapping")
        return stage
    stage.dir = _opt_str(spec.get("dir"))
    stage.tool = _opt_str(spec.get("tool"))
    stage.attended = bool(spec.get("attended"))
    if not stage.dir:
        return stage

    path = Path(proj.folder) / stage.dir
    stage.exists = path.is_dir()
    if not stage.exists:
        # Not begun, which is a position in the pipeline rather than a
        # problem with it.
        proj.pending.append(name)
        return stage

    try:
        newest = 0.0
        count = 0
        with os.scandir(path) as it:
            for e in it:
                if e.name.startswith("."):
                    continue
                count += 1
                try:
                    newest = max(newest, e.stat(follow_symlinks=False).st_mtime)
                except OSError:
                    continue
        stage.file_count = count
        if newest:
            stage.last_modified = datetime.fromtimestamp(newest, timezone.utc)
    except OSError as exc:
        proj.drift.append(f"stage {name!r} unreadable: {exc}")
    return stage


def _apply_ledger(proj: HaarpiProject, path: Path) -> None:
    """Parse `corpus_ledger.json`, tolerating both known and future shapes."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (ValueError, OSError) as exc:
        proj.drift.append(f"corpus_ledger.json unparseable: {exc}")
        return

    items = None
    if isinstance(raw, dict):
        proj.ledger_version = _opt_int(raw.get("version"))
        items = raw.get("items")
        if proj.ledger_version not in KNOWN_LEDGER_VERSIONS:
            proj.drift.append(
                f"corpus_ledger.json version {proj.ledger_version!r} is not one "
                f"raDash knows ({sorted(KNOWN_LEDGER_VERSIONS)}) — read anyway")
    elif isinstance(raw, list):
        items = raw  # an older, bare-list ledger

    if not isinstance(items, list):
        proj.drift.append("corpus_ledger.json has no readable item list")
        return

    for entry in items:
        if isinstance(entry, dict) and entry.get("key"):
            proj.ledger_keys.append(str(entry["key"]))
        elif isinstance(entry, str):
            proj.ledger_keys.append(entry)


def _opt_str(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _opt_int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
