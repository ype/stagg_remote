from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stagg_remote.app import _clock_drift_minutes, create_app
from stagg_remote.config import Settings
from stagg_remote.kettle import KettleClient
from tests.fake_kettle import FakeKettle


@pytest.fixture
def kettle() -> FakeKettle:
    return FakeKettle()


@pytest.fixture
def api(kettle: FakeKettle, tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(kettle_url="http://kettle", state_file=tmp_path / "state.json", reconcile_interval_seconds=3600)
    client = KettleClient("http://kettle", transport=kettle.transport(), confirm_timeout=0.2, confirm_interval=0.05)
    with TestClient(create_app(settings, client)) as tc:
        yield tc


PLAN = {"hour": 6, "minute": 30, "temp_c": 96, "repeat": "daily"}


def test_index_served(api: TestClient) -> None:
    r = api.get("/")
    assert r.status_code == 200 and "Kettle wake-up" in r.text


def test_status_reachable(api: TestClient) -> None:
    body = api.get("/api/status").json()
    assert body["reachable"] is True
    assert body["state"] == "S_Off"
    assert body["kettle_schedule"] == {"time": "00:00", "temp_c": 96.0, "enabled": False}


def test_status_offline(api: TestClient, kettle: FakeKettle) -> None:
    kettle.online = False
    body = api.get("/api/status").json()
    assert body["reachable"] is False and body["plan"] is None


def test_put_plan_arms(api: TestClient, kettle: FakeKettle) -> None:
    r = api.put("/api/plan", json=PLAN)
    assert r.status_code == 200 and r.json()["kettle"] == "armed"
    assert kettle.schedule_enabled
    status = api.get("/api/status").json()
    assert status["armed"] and status["kettle_schedule"]["time"] == "06:30"


def test_put_plan_offline_is_pending(api: TestClient, kettle: FakeKettle) -> None:
    kettle.online = False
    r = api.put("/api/plan", json=PLAN)
    assert r.status_code == 200 and r.json()["kettle"] == "pending"


@pytest.mark.parametrize(
    "bad", [{**PLAN, "temp_c": 120}, {**PLAN, "hour": 24}, {**PLAN, "repeat": "weekly"}, {**PLAN, "temp_c": 95.3}]
)
def test_put_plan_validation(api: TestClient, bad: dict[str, object]) -> None:
    assert api.put("/api/plan", json=bad).status_code == 422


def test_unsupported_firmware_is_conflict(api: TestClient, kettle: FakeKettle) -> None:
    kettle.firmware = "1.3.0"
    assert api.put("/api/plan", json=PLAN).status_code == 409


def test_delete_plan(api: TestClient, kettle: FakeKettle) -> None:
    api.put("/api/plan", json=PLAN)
    r = api.delete("/api/plan")
    assert r.json()["kettle"] == "disarmed"
    assert not kettle.schedule_enabled


def test_clock_sync(api: TestClient) -> None:
    r = api.post("/api/clock/sync")
    now = datetime.now()
    assert r.status_code == 200
    assert r.json()["kettle_clock"] in {f"{t:%H:%M}" for t in (now - timedelta(minutes=1), now)}


@pytest.mark.parametrize(
    ("kettle_clock", "now", "drift"),
    [((13, 5), (13, 5), 0), ((13, 7), (13, 5), 2), ((0, 1), (23, 59), 2), ((14, 5), (13, 5), 60)],
)
def test_clock_drift(kettle_clock: tuple[int, int], now: tuple[int, int], drift: int) -> None:
    assert _clock_drift_minutes(kettle_clock, datetime(2026, 10, 26, *now)) == drift
