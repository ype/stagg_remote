"""HTTP API + single-page UI. LAN only: the kettle itself has no authentication."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from importlib import resources
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from stagg_remote.config import Settings
from stagg_remote.kettle import (
    KettleClient,
    KettleError,
    KettleNotReady,
    KettleUnavailable,
    KettleWriteUnconfirmed,
    UnsupportedFirmware,
)
from stagg_remote.service import PlanStore, WakePlan, WakeService

log = logging.getLogger(__name__)


def _clock_drift_minutes(kettle_clock: tuple[int, int] | None, now: datetime) -> int | None:
    if kettle_clock is None:
        return None
    diff = (kettle_clock[0] * 60 + kettle_clock[1]) - (now.hour * 60 + now.minute)
    return (diff + 720) % 1440 - 720


def _http_error(err: KettleError) -> HTTPException:
    code = {
        KettleUnavailable: 503,
        KettleNotReady: 409,
        UnsupportedFirmware: 409,
        KettleWriteUnconfirmed: 502,
    }.get(type(err), 500)
    return HTTPException(status_code=code, detail=str(err))


def create_app(settings: Settings | None = None, client: KettleClient | None = None) -> FastAPI:
    cfg = settings or Settings()  # type: ignore[call-arg]  # values come from the environment
    kettle = client or KettleClient(cfg.kettle_url, timeout=cfg.http_timeout_seconds)
    store = PlanStore(cfg.state_file)
    service = WakeService(kettle, store)

    async def reconcile_loop() -> None:
        while True:
            await service.reconcile()
            await asyncio.sleep(cfg.reconcile_interval_seconds)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(reconcile_loop())
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await kettle.aclose()

    app = FastAPI(title="Stagg Remote", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        now = datetime.now()
        state = store.load()
        body: dict[str, Any] = {
            "server_time": now.strftime("%H:%M"),
            "plan": state.plan.model_dump() if state.plan else None,
            "armed": state.armed,
            "pending_disarm": state.pending_disarm,
            "last_error": service.last_error,
            "reachable": False,
        }
        try:
            live = await kettle.status()
            ks = await kettle.settings()
        except KettleUnavailable:
            return body  # normal while the kettle is heating: it drops off WiFi
        body.update(
            reachable=True,
            state=live.state,
            temp_c=live.temp_c,
            target_c=live.target_c,
            on_base=live.temp_c is not None,
            kettle_clock=f"{ks.clock[0]:02d}:{ks.clock[1]:02d}" if ks.clock else None,
            clock_drift_minutes=_clock_drift_minutes(ks.clock, now),
            kettle_schedule=(
                {
                    "time": f"{ks.schedule.hour:02d}:{ks.schedule.minute:02d}",
                    "temp_c": ks.schedule.temp_c,
                    "enabled": ks.schedule.enabled,
                }
                if ks.schedule
                else None
            ),
        )
        return body

    @app.put("/api/plan")
    async def put_plan(plan: WakePlan) -> dict[str, Any]:
        plan.active = True
        try:
            result = await service.apply(plan)
        except KettleError as err:
            raise _http_error(err) from err
        return {"kettle": result.kettle, "detail": result.detail, "plan": plan.model_dump()}

    @app.delete("/api/plan")
    async def delete_plan() -> dict[str, Any]:
        try:
            result = await service.cancel()
        except KettleError as err:
            raise _http_error(err) from err
        return {"kettle": result.kettle, "detail": result.detail}

    @app.post("/api/clock/sync")
    async def sync_clock() -> dict[str, Any]:
        now = datetime.now()
        try:
            ks = await kettle.set_clock(now.hour, now.minute)
        except KettleError as err:
            raise _http_error(err) from err
        return {"kettle_clock": f"{ks.clock[0]:02d}:{ks.clock[1]:02d}" if ks.clock else None}

    index_html = resources.files("stagg_remote").joinpath("static/index.html").read_text()

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return index_html

    return app
