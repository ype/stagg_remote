"""Protocol tests pinned to records captured from a real kettle (firmware 1.2.26)."""

import pytest

from stagg_remote.protocol import (
    RECORD_LEN,
    ProtocolError,
    clock_payload,
    decode_settings,
    decode_temp,
    encode_temp,
    schedule_payload,
)

# Captured 2026-10-26: idle, target 94 °C, stored schedule 00:00 @ 96 °C disarmed, clock 13:05.
IDLE = bytes.fromhex("f7170000bc80c0800000050d001e000001")
# Captured after arming 13:13 @ 50 °C via a single POST with mask 0x000C.
ARMED = bytes.fromhex("ff170000bc8064800d0d0b0d001e000004")
# Captured right after the schedule fired: disarmed, target taken from the schedule.
FIRED = bytes.fromhex("f71700006480c08000000d0d001e000008")


def test_captures_are_record_length() -> None:
    assert {len(IDLE), len(ARMED), len(FIRED)} == {RECORD_LEN}


@pytest.mark.parametrize(("celsius", "raw"), [(94.0, 0x80BC), (50.0, 0x8064), (96.0, 0x80C0), (92.5, 0x80B9)])
def test_temperature_round_trip(celsius: float, raw: int) -> None:
    assert encode_temp(celsius) == raw
    assert decode_temp(raw) == celsius


def test_decode_fahrenheit_encoding() -> None:
    assert decode_temp(212) == 100.0


def test_decode_idle_record() -> None:
    s = decode_settings(IDLE)
    assert s.target_temp_c == 94.0
    assert s.clock == (13, 5)
    assert s.schedule is not None
    assert (s.schedule.hour, s.schedule.minute, s.schedule.temp_c) == (0, 0, 96.0)
    assert s.schedule.enabled is False
    assert s.revision == 1


def test_decode_armed_record() -> None:
    s = decode_settings(ARMED)
    assert s.schedule is not None
    assert (s.schedule.hour, s.schedule.minute, s.schedule.temp_c, s.schedule.enabled) == (13, 13, 50.0, True)
    assert s.clock == (13, 11)


def test_decode_fired_record_disarms_and_adopts_schedule_temp() -> None:
    s = decode_settings(FIRED)
    assert s.schedule is not None and s.schedule.enabled is False
    assert s.target_temp_c == 50.0


def test_decode_rejects_wrong_length() -> None:
    with pytest.raises(ProtocolError):
        decode_settings(IDLE[:-1])


def test_schedule_payload_armed_matches_wire_format() -> None:
    payload = schedule_payload(13, 13, 50.0, enabled=True)
    assert payload.hex() == "0c000000000064800d0d00000000000000"


def test_schedule_payload_disarmed_omits_enable_bit() -> None:
    payload = schedule_payload(6, 30, 96.0, enabled=False)
    assert payload[:2] == b"\x04\x00"
    assert (payload[8], payload[9]) == (30, 6)


@pytest.mark.parametrize(
    ("hour", "minute", "temp"), [(24, 0, 90), (0, 60, 90), (6, 0, 39.5), (6, 0, 100.5), (6, 0, float("nan"))]
)
def test_schedule_payload_validates(hour: int, minute: int, temp: float) -> None:
    with pytest.raises(ValueError):
        schedule_payload(hour, minute, temp, enabled=True)


def test_clock_payload() -> None:
    assert clock_payload(7, 45).hex() == "100000000000000000002d070000000000"
