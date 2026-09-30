"""`/api/settings` — the configuration you can change without redeploying.

Only what belongs here is here. Deployment facts (the database URL, the five
source paths, the port) mirror what Docker was told and are not editable: a
field that disagreed with a bind mount would be worse than one you cannot
change. Secrets are not editable either, and are never returned.

Every value reports where it came from — your override, the environment, or
the shipped default — so a number that surprises you can be traced.
"""
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlmodel import Session

from app import settings as settings_mod
from app.database import get_db

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _payload(db: Session) -> dict:
    changed = settings_mod.changed_at(db)
    groups: dict[str, list] = {}
    for item in settings_mod.describe():
        groups.setdefault(item["group"], []).append(item)
    return {
        "groups": [{"name": name, "settings": items}
                   for name, items in groups.items()],
        "changed_at": changed.isoformat() if changed else None,
        "not_editable": {
            "paths": "Source paths mirror Docker bind mounts; editing them "
                     "here would change the value and not the mount.",
            "database_url": "Needed before there is a database to read "
                            "settings from.",
            "secrets": "API keys are read from the environment at call time "
                       "and never returned, masked or otherwise.",
        },
    }


@router.get("")
def read(db: Session = Depends(get_db)):
    return _payload(db)


@router.put("/{key}")
def write(key: str, value: Optional[str] = Body(None, embed=True),
          db: Session = Depends(get_db)):
    """Set an override. An empty value stores an empty override, which is not
    the same as clearing it — clearing hands the setting back to the
    environment, while an empty override deliberately blanks it."""
    try:
        stored = settings_mod.set_value(db, key, value)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"key": key, "value": stored, "source": settings_mod.source(key),
            "note": ("Rebuild the ledger to apply."
                     if settings_mod.BY_KEY[key].affects_ledger else None)}


@router.delete("/{key}")
def revert(key: str, db: Session = Depends(get_db)):
    """Drop the override so the environment or the default takes over again."""
    try:
        settings_mod.clear(db, key)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"key": key, "value": settings_mod.raw(key),
            "source": settings_mod.source(key)}
