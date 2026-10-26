"""In-memory emulation of the kettle's HTTP endpoints, built from observed 1.2.26 behaviour."""

from __future__ import annotations

import struct

import httpx

ROOT_PAGE = (
    "<h1>EKG</h1>Current version: {fw}<br>\nBuild time: 17:50:37<br>\nBuild date: Oct  8 2026<br>\n"
    "Boot partition: ota_1<br>\nRunning partition: ota_1<br>\n"
)


class FakeKettle:
    def __init__(self) -> None:
        self.firmware = "1.2.26"
        self.state = "S_Off"
        self.temp: float | None = 34.3
        self.online = True
        self.ignore_writes = False
        self.record = bytearray.fromhex("f7170000bc80c0800000050d001e000001")
        self.posts: list[bytes] = []

    # --- helpers for tests -------------------------------------------------
    def set_clock(self, hour: int, minute: int) -> None:
        self.record[10], self.record[11] = minute, hour

    @property
    def schedule_enabled(self) -> bool:
        return bool(struct.unpack_from("<H", self.record)[0] & 0x8)

    def fire(self) -> None:
        """What the kettle did at the scheduled minute: disarm, adopt temp, start heating, drop WiFi."""
        mask = struct.unpack_from("<H", self.record)[0] & ~0x8
        struct.pack_into("<H", self.record, 0, mask)
        self.record[4:6] = self.record[6:8]
        self.state, self.online = "S_StartupToTempr", False

    # --- transport ---------------------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.online:
            raise httpx.ConnectError("No route to host", request=request)
        path = request.url.path
        if path == "/":
            return httpx.Response(200, text=ROOT_PAGE.format(fw=self.firmware))
        if path == "/temp":
            temp = "nan" if self.temp is None else f"{self.temp:.2f}"
            set_c = (struct.unpack_from("<H", self.record, 4)[0] & 0x7FFF) / 2
            body = f'{{"temp":{temp},"temp_set":{set_c:.2f},"state":"{self.state}","pwm":-10,"timestamp":1}}'
            return httpx.Response(200, text=body)
        if path == "/api":
            if request.method == "GET":
                return httpx.Response(200, content=bytes(self.record))
            self._apply(request.content)
            return httpx.Response(200, text="")
        return httpx.Response(404, text="This URI does not exist")

    def _apply(self, payload: bytes) -> None:
        self.posts.append(payload)
        if self.ignore_writes:
            return
        mask = struct.unpack_from("<H", payload)[0]
        current = struct.unpack_from("<H", self.record)[0]
        if mask & 0x4:
            self.record[6:10] = payload[6:10]
            current = (current & ~0x8) | (mask & 0x8)
        if mask & 0x10:
            self.record[10:12] = payload[10:12]
        struct.pack_into("<H", self.record, 0, current)
        self.record[16] = (self.record[16] + 1) % 256

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
