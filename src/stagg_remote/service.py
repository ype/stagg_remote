"""Keeps the kettle's native schedule in line with the user's wake plan.

The kettle runs its own schedule, so nothing here has to be online at wake time.
Firmware 1.2.26 exposes no once/daily flag over the settings record, so every arm
is effectively "once"; daily plans are re-armed by ``reconcile`` after each firing,
once the kettle is back on WiFi and idle.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from stagg_remote.kettle import KettleClient, KettleNotReady, KettleUnavailable
from stagg_remote.protocol import MAX_TEMP_C, MIN_TEMP_C, KettleSettings

log = logging.getLogger(__name__)

# Never re-arm within this many minutes after the wake time: arming a schedule for the
# current kettle minute can make it fire again immediately.
REARM_GUARD_MINUTES = 3


class WakePlan(BaseModel):
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)
    temp_c: float = Field(ge=MIN_TEMP_C, le=MAX_TEMP_C, multiple_of=0.5)
    repeat: Literal["once", "daily"]
    active: bool = True


class StoredState(BaseModel):
    plan: WakePlan | None = None
    armed: bool = False  # we confirmed the kettle schedule is armed for this plan
    pending_disarm: bool = False  # plan cancelled while the kettle was unreachable


class PlanStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> StoredState:
        if not self._path.exists():
            return StoredState()
        return StoredState.model_validate_json(self._path.read_text())

    def check_writable(self) -> None:
        """Fail at startup rather than on the first save (e.g. a root-owned bind mount)."""
        directory = self._path.parent
        directory.mkdir(parents=True, exist_ok=True)
        if not os.access(directory, os.W_OK):
            raise PermissionError(f"state directory {directory} is not writable by uid {os.getuid()}")

    def save(self, state: StoredState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(state.model_dump_json(indent=2))
        tmp.replace(self._path)


@dataclass(frozen=True)
class ApplyResult:
    kettle: Literal["armed", "disarmed", "pending"]
    detail: str | None = None


class WakeService:
    def __init__(self, client: KettleClient, store: PlanStore) -> None:
        self._client = client
        self._store = store
        self.last_error: str | None = None

    async def apply(self, plan: WakePlan) -> ApplyResult:
        state = StoredState(plan=plan)
        self._store.save(state)
        try:
            await self._arm(plan)
        except (KettleUnavailable, KettleNotReady) as err:
            self.last_error = str(err)
            return ApplyResult("pending", f"{err}; will arm automatically when the kettle is available")
        state.armed = True
        self._store.save(state)
        return ApplyResult("armed")

    async def cancel(self) -> ApplyResult:
        state = self._store.load()
        if state.plan is not None:
            state.plan.active = False
        state.armed = False
        state.pending_disarm = True
        self._store.save(state)
        try:
            await self._disarm(state.plan)
        except KettleUnavailable as err:
            self.last_error = str(err)
            return ApplyResult("pending", f"{err}; will disarm when the kettle is back online")
        state.pending_disarm = False
        self._store.save(state)
        return ApplyResult("disarmed")

    async def reconcile(self) -> None:
        """Bring the kettle in line with the stored plan. Safe to call repeatedly."""
        state = self._store.load()
        if state.plan is None and not state.pending_disarm:
            return
        try:
            settings = await self._client.settings()
            if state.pending_disarm:
                await self._disarm(state.plan)
                state.pending_disarm = False
                self._store.save(state)
            elif state.plan is not None and state.plan.active:
                await self._reconcile_active(state, state.plan, settings)
            self.last_error = None
        except (KettleUnavailable, KettleNotReady) as err:
            self.last_error = str(err)
        except Exception as err:  # keep the background loop alive, but surface the problem
            log.exception("reconcile failed")
            self.last_error = str(err)

    async def _reconcile_active(self, state: StoredState, plan: WakePlan, settings: KettleSettings) -> None:
        sch = settings.schedule
        if (
            sch is not None
            and sch.enabled
            and (sch.hour, sch.minute, sch.temp_c) == (plan.hour, plan.minute, plan.temp_c)
        ):
            if not state.armed:
                state.armed = True
                self._store.save(state)
            return
        if state.armed and plan.repeat == "once":
            # Our armed schedule is gone: the kettle fired it (or it was cleared on the kettle).
            plan.active = False
            state.armed = False
            self._store.save(state)
            log.info("one-off wake plan completed")
            return
        if self._in_guard_window(plan, settings):
            return
        await self._arm(plan)
        state.armed = True
        self._store.save(state)
        log.info("armed kettle schedule for %02d:%02d", plan.hour, plan.minute)

    @staticmethod
    def _in_guard_window(plan: WakePlan, settings: KettleSettings) -> bool:
        if settings.clock is None:
            return True  # without the kettle clock we cannot rule out an immediate re-fire
        now = settings.clock[0] * 60 + settings.clock[1]
        wake = plan.hour * 60 + plan.minute
        return (now - wake) % 1440 < REARM_GUARD_MINUTES

    async def _arm(self, plan: WakePlan) -> None:
        await self._client.set_schedule(plan.hour, plan.minute, plan.temp_c, enabled=True)

    async def _disarm(self, plan: WakePlan | None) -> None:
        settings = await self._client.settings()
        sch = settings.schedule
        if sch is None or not sch.enabled:
            return
        # Rewrite the kettle's current schedule values with the enable bit cleared.
        temp = sch.temp_c if sch.temp_c is not None else (plan.temp_c if plan else 95.0)
        await self._client.set_schedule(sch.hour, sch.minute, temp, enabled=False)
