"""Async client for the kettle's unauthenticated local HTTP interface."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from stagg_remote.protocol import (
    KettleSettings,
    clock_payload,
    decode_settings,
    schedule_payload,
)

SETTINGS_PATH = "/api?i=0,p=0,d=0,t=3,s=1"
# Writes are only sent to firmware whose settings record layout has been verified.
SUPPORTED_FIRMWARE = ("1.2.26",)
_VERSION_RE = re.compile(r"Current version:\s*([^<\s]+)")


class KettleError(Exception):
    """Base class for kettle failures surfaced to the API."""


class KettleUnavailable(KettleError):
    """Kettle did not answer; it drops off WiFi while heating."""


class KettleNotReady(KettleError):
    """Kettle must be idle (S_Off) and on its base before arming a schedule."""


class UnsupportedFirmware(KettleError):
    """Firmware is not one whose write format has been verified."""


class KettleWriteUnconfirmed(KettleError):
    """A write was sent but readback did not confirm it. Never retried automatically."""


@dataclass(frozen=True)
class KettleStatus:
    state: str
    temp_c: float | None
    target_c: float | None

    @property
    def idle_on_base(self) -> bool:
        # The kettle reports temp NaN when lifted off the base.
        return self.state == "S_Off" and self.temp_c is not None


class KettleClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 5.0,
        confirm_timeout: float = 8.0,
        confirm_interval: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)
        self._confirm_timeout = confirm_timeout
        self._confirm_interval = confirm_interval
        # The ESP32 web server handles one request at a time; serialize everything.
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, content: bytes | None = None) -> httpx.Response:
        try:
            response = await self._http.request(method, path, content=content)
            response.raise_for_status()
        except httpx.HTTPError as err:
            raise KettleUnavailable(f"kettle unreachable: {err}") from err
        return response

    async def firmware(self) -> str:
        async with self._lock:
            page = (await self._request("GET", "/")).text
        match = _VERSION_RE.search(page)
        if not match:
            raise KettleUnavailable("could not read firmware version")
        return match.group(1)

    async def status(self) -> KettleStatus:
        async with self._lock:
            text = (await self._request("GET", "/temp")).text
        data = json.loads(text.replace("nan", "null"))
        return KettleStatus(state=data["state"], temp_c=data["temp"], target_c=data["temp_set"])

    async def settings(self) -> KettleSettings:
        async with self._lock:
            return await self._read_settings()

    async def _read_settings(self) -> KettleSettings:
        return decode_settings((await self._request("GET", SETTINGS_PATH)).content)

    async def _require_supported_firmware(self) -> None:
        version = await self.firmware()
        if version not in SUPPORTED_FIRMWARE:
            raise UnsupportedFirmware(f"firmware {version} is not verified; supported: {', '.join(SUPPORTED_FIRMWARE)}")

    async def set_schedule(self, hour: int, minute: int, temp_c: float, *, enabled: bool) -> KettleSettings:
        """Write the kettle's native schedule in one record and confirm it by readback."""
        payload = schedule_payload(hour, minute, temp_c, enabled=enabled)
        await self._require_supported_firmware()
        if enabled and not (await self.status()).idle_on_base:
            raise KettleNotReady("kettle must be idle on its base to arm a schedule")
        expected_temp = round(temp_c * 2) / 2

        def confirmed(s: KettleSettings) -> bool:
            sch = s.schedule
            return (
                sch is not None
                and sch.enabled == enabled
                and (sch.hour, sch.minute) == (hour, minute)
                and sch.temp_c == expected_temp
            )

        return await self._write(payload, confirmed)

    async def set_clock(self, hour: int, minute: int) -> KettleSettings:
        payload = clock_payload(hour, minute)
        await self._require_supported_firmware()
        following = divmod((hour * 60 + minute + 1) % 1440, 60)
        # Allow a minute rollover between the write and the readback.
        return await self._write(payload, lambda s: s.clock in ((hour, minute), following))

    async def _write(self, payload: bytes, confirmed: Callable[[KettleSettings], bool]) -> KettleSettings:
        async with self._lock:
            await self._request("POST", SETTINGS_PATH, content=payload)
            deadline = time.monotonic() + self._confirm_timeout
            while True:
                settings = await self._read_settings()
                if confirmed(settings):
                    return settings
                if time.monotonic() >= deadline:
                    raise KettleWriteUnconfirmed(f"readback did not confirm write (record {settings.raw.hex(' ')})")
                await asyncio.sleep(self._confirm_interval)
