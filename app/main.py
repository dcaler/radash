import hashlib
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

_APP_DIR = Path(__file__).parent  # app/


def _compute_version() -> str:
    """Fingerprint the deployed source so the version changes iff the code does.

    Hashes the contents (and relative paths) of every Python/JS/CSS/HTML file
    under app/. A rebuilt image with changed files yields a new hash — which is
    both the displayed version and the ?v= cache-bust key — while redeploying
    identical code keeps the same hash. No manual bump, git, or build arg needed.
    """
    h = hashlib.sha256()
    for p in sorted(_APP_DIR.rglob("*")):
        if p.suffix in {".py", ".js", ".css", ".html"} and "__pycache__" not in p.parts:
            h.update(p.relative_to(_APP_DIR).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:7]


_APP_VERSION = _compute_version()
_STARTED_AT = datetime.now(timezone.utc).strftime("%H:%M UTC")

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app import settings
from app.config import get_config
from app.database import apply_migrations, create_db_and_tables, init_engine
from app.routers import (frontier, ledger, map as map_router, meta,
                         outputs, planning, refresh as refresh_router, sources,
                         status)
from app.routers import settings as settings_router

STATIC_DIR = _APP_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open the database and ensure the output directory exists.

    Note what is *not* here: no source is read and nothing is fetched at
    startup. raDash boots with Zotero absent and trundlr unreachable, and
    reports that at /api/mounts rather than failing to start.
    """
    engine = init_engine(get_config().database_url)
    create_db_and_tables(engine)
    apply_migrations(engine)
    # Before anything reads configuration: an override stored in the database
    # outranks the environment, so it has to be in hand first.
    settings.load(engine)
    cfg = get_config()
    # The single writable location. Created here so a fresh volume works
    # first-run; every source path stays untouched.
    try:
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # surfaced at /api/mounts, never fatal

    # The weekly tick. Nothing is fetched at startup — it sleeps through a
    # grace period first, then ticks against the newest snapshot's age, so a
    # redeploy neither triggers a refresh nor delays one.
    from app.scheduler import Scheduler

    scheduler = Scheduler(engine)
    app.state.scheduler = scheduler
    scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()


app = FastAPI(
    title="raDash",
    description="A research portfolio observatory. Reads trundlr, HAARPi "
                "project folders and Zotero; writes to none of them.",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_INDEX_HTML = STATIC_DIR / "index.html"

# Append ?v=<version> to local static .css/.js references so browsers fetch
# fresh assets after every deploy instead of serving a stale cached copy.
_ASSET_REF_RE = re.compile(r'(href|src)="(/static/[^"]+\.(?:css|js))"')


def _versioned_index() -> str:
    html = _INDEX_HTML.read_text()
    return _ASSET_REF_RE.sub(
        lambda m: f'{m.group(1)}="{m.group(2)}?v={_APP_VERSION}"', html
    )


@app.get("/", include_in_schema=False)
def read_root():
    return HTMLResponse(
        _versioned_index(),
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/health")
def health():
    """Container healthcheck. Deliberately dependency-free."""
    return {"status": "ok"}


@app.get("/api/health")
def api_health():
    return {"status": "ok", "version": _APP_VERSION, "started_at": _STARTED_AT}


@app.get("/api/version")
def version():
    return {"version": f"{_APP_VERSION} · {_STARTED_AT}"}


app.include_router(meta.router)
app.include_router(sources.router)
app.include_router(ledger.router)
app.include_router(settings_router.router)
app.include_router(map_router.router)
app.include_router(status.router)
app.include_router(frontier.router)
app.include_router(planning.router)
app.include_router(outputs.router)
app.include_router(refresh_router.router)
