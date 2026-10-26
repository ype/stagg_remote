"""Wire format of the kettle's 17-byte settings record (``/api?i=0,p=0,d=0,t=3,s=1``).

Layout (little-endian), verified on firmware 1.2.26:

    0-1   field mask; on writes it selects which fields the kettle applies
    4-5   target temperature
    6-7   schedule temperature
    8, 9  schedule minute, hour
    10,11 clock minute, hour
    16    settings revision (incremented by the kettle on every change)

Temperatures with bit 15 set are Celsius half-degrees, otherwise Fahrenheit.
Credit: layout first documented by github.com/bramboe/stagg-ekg-plus-ha.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

RECORD_LEN = 17

MASK_TARGET = 0x0002
MASK_SCHEDULE = 0x0004
MASK_SCHEDULE_ENABLED = 0x0008
MASK_CLOCK = 0x0010

MIN_TEMP_C = 40.0
MAX_TEMP_C = 100.0


class ProtocolError(ValueError):
    """The kettle returned a record we cannot interpret."""


@dataclass(frozen=True)
class KettleSchedule:
    hour: int
    minute: int
    temp_c: float | None
    enabled: bool


@dataclass(frozen=True)
class KettleSettings:
    target_temp_c: float | None
    schedule: KettleSchedule | None
    clock: tuple[int, int] | None
    revision: int
    raw: bytes


def encode_temp(celsius: float) -> int:
    return 0x8000 | round(celsius * 2)


def decode_temp(raw: int) -> float:
    return (raw & 0x7FFF) / 2 if raw & 0x8000 else round((raw - 32) / 1.8, 1)


def _temp_or_none(raw: int) -> float | None:
    value = decode_temp(raw)
    return value if MIN_TEMP_C <= value <= MAX_TEMP_C else None


def decode_settings(raw: bytes) -> KettleSettings:
    if len(raw) != RECORD_LEN:
        raise ProtocolError(f"expected {RECORD_LEN}-byte settings record, got {len(raw)}")
    mask, _, target, sched_temp = struct.unpack_from("<HHHH", raw)

    schedule = None
    if mask & MASK_SCHEDULE and raw[9] < 24 and raw[8] < 60:
        schedule = KettleSchedule(
            hour=raw[9],
            minute=raw[8],
            temp_c=_temp_or_none(sched_temp),
            enabled=bool(mask & MASK_SCHEDULE_ENABLED),
        )
    clock = (raw[11], raw[10]) if mask & MASK_CLOCK and raw[11] < 24 and raw[10] < 60 else None
    return KettleSettings(
        target_temp_c=_temp_or_none(target) if mask & MASK_TARGET else None,
        schedule=schedule,
        clock=clock,
        revision=raw[16],
        raw=raw,
    )


def _check_time(hour: int, minute: int) -> None:
    if not (isinstance(hour, int) and isinstance(minute, int) and 0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError("invalid time of day")


def schedule_payload(hour: int, minute: int, temp_c: float, *, enabled: bool) -> bytes:
    """Write schedule time + temperature, arming or disarming it in the same record."""
    _check_time(hour, minute)
    if not (math.isfinite(temp_c) and MIN_TEMP_C <= temp_c <= MAX_TEMP_C):
        raise ValueError(f"temperature must be {MIN_TEMP_C:g}-{MAX_TEMP_C:g} °C")
    raw = bytearray(RECORD_LEN)
    struct.pack_into("<H", raw, 0, MASK_SCHEDULE | (MASK_SCHEDULE_ENABLED if enabled else 0))
    struct.pack_into("<H", raw, 6, encode_temp(temp_c))
    raw[8], raw[9] = minute, hour
    return bytes(raw)


def clock_payload(hour: int, minute: int) -> bytes:
    _check_time(hour, minute)
    raw = bytearray(RECORD_LEN)
    struct.pack_into("<H", raw, 0, MASK_CLOCK)
    raw[10], raw[11] = minute, hour
    return bytes(raw)
