"""Diagnostics: what raDash can see, and what it is allowed to do to it.

`/api/mounts` is the skeleton's substantive endpoint. It answers the question
M0 exists to settle: are the sources visible, and are they read-only? A mount
that is writable is reported as a defect rather than a convenience, because
raDash's whole boundary is that it writes to nothing it observes.
"""
from fastapi import APIRouter

from app.config import get_config

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/mounts")
def mounts():
    """Report every read-only source: present, and correctly not writable.

    `ok` is true only when a mount exists *and* is not writable. A missing
    mount is not an error here — raDash runs with sources absent and degrades
    the panels that need them — but a writable one is.
    """
    cfg = get_config()
    out = []
    for m in cfg.mounts():
        exists, writable = m.exists, m.writable
        if not exists:
            state = "missing"
        elif writable:
            state = "writable"   # defect
        else:
            state = "read-only"
        out.append({
            "key": m.key,
            "path": str(m.path),
            "kind": m.kind,
            "what": m.what,
            "exists": exists,
            "writable": writable,
            "state": state,
            "ok": exists and not writable,
        })
    return {
        "mounts": out,
        "writable_sources": [m["key"] for m in out if m["writable"]],
        "output_dir": str(cfg.output_dir),
    }


@router.get("/config")
def config():
    """Non-secret configuration, for the diagnostics panel.

    Deliberately excludes API keys: raDash reads ZOTERO_API_KEY, S2_API_KEY and
    similar from the environment and never echoes them, not even masked.
    """
    cfg = get_config()
    return {
        "trundlr_url": cfg.trundlr_url,
        "openalex_author_ids": cfg.openalex_author_ids,
        "contact_email": cfg.contact_email,
        # The URL itself, not a secret: knowing whether it is set is the whole
        # question when the ledger reports no public claim to compare against.
        "cv_url": cfg.cv_url or None,
        "database_url": cfg.database_url.split("://")[0] + "://…",
    }
