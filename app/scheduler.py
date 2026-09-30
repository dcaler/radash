"""The weekly tick — M8-T1.

An asyncio task rather than cron, because raDash is one container and a second
scheduling system would be a second thing to deploy, configure and forget.

**It ticks against the data, not against uptime.** Sleeping a week from
process start sounds right and is wrong in both directions: a container that
restarts more often than weekly would never refresh, and one that restarts
rarely would refresh at whatever hour it last happened to boot. So the loop
wakes hourly, asks how old the newest snapshot is, and refreshes when that
exceeds the interval. The clock is the snapshot's own age, which survives a
redeploy.

**It never runs at startup.** A cold container with an empty volume has
nothing to compare against and every source to fetch; doing that automatically
in the first second of a deploy is how a redeploy becomes an outage. The first
cycle is either yours to press or an hour away.

Failures are logged and slept off. A scheduler that dies on one bad week is
worse than no scheduler, because nothing says it stopped.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session

from app import refresh as refresh_mod
from app import settings
from app.config import get_config

log = logging.getLogger("radash.scheduler")

# How often the loop wakes to ask whether a cycle is owed. Not the refresh
# interval — that is `REFRESH_DAYS`, measured against the newest snapshot.
TICK_SECONDS = 3600

# Nothing is fetched in the first minute of a container's life. A redeploy
# should be boring.
STARTUP_GRACE_SECONDS = 60


class Scheduler:
    """Owns the loop, and reports what it last did."""

    def __init__(self, engine):
        self.engine = engine
        self.task: Optional[asyncio.Task] = None
        self.last_run: Optional[datetime] = None
        self.last_summary: Optional[dict] = None
        self.running = False

    def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self.task is None:
            return
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass
        self.task = None

    async def _loop(self) -> None:
        await asyncio.sleep(STARTUP_GRACE_SECONDS)
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:              # never dies of one bad week
                log.warning("scheduled refresh failed: %s", exc)
            await asyncio.sleep(TICK_SECONDS)

    async def _tick(self) -> None:
        if settings.num("REFRESH_DAYS", refresh_mod.REFRESH_DAYS) <= 0:
            return
        with Session(self.engine) as db:
            if not refresh_mod.is_due(db):
                return
        # The cycle is synchronous and takes seconds to low minutes: run it
        # off the event loop so a weekly refresh cannot stall a page load.
        self.running = True
        try:
            summary = await asyncio.to_thread(self._run)
            self.last_run = datetime.now(timezone.utc)
            self.last_summary = summary
            log.info("weekly refresh: %s", summary.get("failed") or "clean")
        finally:
            self.running = False

    def _run(self) -> dict:
        with Session(self.engine) as db:
            return refresh_mod.cycle(get_config(), db, trigger="weekly").summary()

    def status(self) -> dict:
        every = settings.num("REFRESH_DAYS", refresh_mod.REFRESH_DAYS)
        with Session(self.engine) as db:
            age = refresh_mod.days_since_last(db)
            due = refresh_mod.is_due(db)
        return {
            "enabled": every > 0,
            "every_days": every,
            "running": self.running,
            "due": due,
            "snapshot_age_days": round(age, 2) if age is not None else None,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "last_summary": self.last_summary,
            "note": ("The interval is measured against the newest snapshot, "
                     "not against how long this container has been up, so a "
                     "redeploy neither triggers a refresh nor delays one."),
        }
