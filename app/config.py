"""Runtime configuration, read from the environment.

Every path here is a *source* raDash reads and never writes. The container
mounts each one read-only, so the read-only guarantee is enforced by Docker
rather than asserted by this module — see `docker-compose.yml` and
`tests/test_mounts_readonly.py`.

Paths are reported rather than required: a missing source degrades the
dashboard panel that needs it and is surfaced at `/api/mounts`, never raised
at startup. raDash starts with nothing mounted and says so.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from app import settings


def _env_path(name: str, default: str) -> Path:
    return Path(os.getenv(name, default))


@dataclass(frozen=True)
class Mount:
    """A read-only source location."""
    key: str
    path: Path
    kind: str          # "file" | "dir"
    what: str          # human-readable description

    @property
    def exists(self) -> bool:
        return self.path.exists()

    @property
    def writable(self) -> bool:
        """True if this process could write here — which would be a defect.

        Checked with os.access rather than by attempting a write, so calling it
        is always side-effect free.
        """
        target = self.path if self.path.exists() else self.path.parent
        return bool(target.exists() and os.access(target, os.W_OK))


@dataclass(frozen=True)
class Config:
    database_url: str = field(default_factory=lambda: os.getenv(
        "DATABASE_URL", "sqlite:///radash.db"))
    zotero_sqlite: Path = field(default_factory=lambda: _env_path(
        "ZOTERO_SQLITE", "/data/zotero/zotero.sqlite"))
    projects_dir: Path = field(default_factory=lambda: _env_path(
        "PROJECTS_DIR", "/data/projects"))
    professional_dir: Path = field(default_factory=lambda: _env_path(
        "PROFESSIONAL_DIR", "/data/professional"))
    output_dir: Path = field(default_factory=lambda: _env_path(
        "OUTPUT_DIR", "/app/data/output"))
    # Where you drop a Google Scholar export for raDash to read. Inside the
    # writable volume rather than beside a source, because it is your file, not
    # one of the systems raDash observes — it is read here and never written.
    import_dir: Path = field(default_factory=lambda: _env_path(
        "IMPORT_DIR", "/app/data/import"))
    # These three read through `settings`, so an override set in the UI wins
    # over the environment without a redeploy. The paths above deliberately do
    # not: they mirror Docker bind mounts, and a value that disagreed with the
    # mount would be worse than one you cannot edit.
    trundlr_url: str = field(default_factory=lambda: settings.text(
        "TRUNDLR_URL", os.getenv("TRUNDLR_URL", "http://localhost:8251")))
    contact_email: str = field(default_factory=lambda: settings.text(
        "CONTACT_EMAIL", os.getenv("CONTACT_EMAIL", "")))
    # Your public CV page. Not a source of truth -- a source of *claims*, whose
    # divergence from the ledger is the thing worth reporting.
    cv_url: str = field(default_factory=lambda: settings.text(
        "CV_URL", os.getenv("CV_URL", "")))

    @property
    def openalex_author_ids(self) -> list[str]:
        raw = settings.text("OPENALEX_AUTHOR_IDS",
                            os.getenv("OPENALEX_AUTHOR_IDS", ""))
        return [a.strip() for a in raw.split(",") if a.strip()]

    def mounts(self) -> list[Mount]:
        """Every read-only source, for reporting and for the read-only test."""
        return [
            Mount("zotero", self.zotero_sqlite, "file",
                  "Zotero library (sqlite, opened immutable)"),
            Mount("projects", self.projects_dir, "dir",
                  "research project folders (haarpi.yaml, stage dirs)"),
            Mount("professional", self.professional_dir, "dir",
                  "professional record (publications, CV)"),
        ]


def get_config() -> Config:
    """Read configuration fresh from the environment.

    Not cached, so tests can monkeypatch env vars without reaching into module
    state.
    """
    return Config()
