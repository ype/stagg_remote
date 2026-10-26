from pathlib import Path

import pytest

from stagg_remote.kettle import KettleClient, UnsupportedFirmware
from stagg_remote.service import PlanStore, WakePlan, WakeService
from tests.fake_kettle import FakeKettle


@pytest.fixture
def kettle() -> FakeKettle:
    k = FakeKettle()
    k.set_clock(22, 0)
    return k


@pytest.fixture
def store(tmp_path: Path) -> PlanStore:
    return PlanStore(tmp_path / "state.json")


@pytest.fixture
def service(kettle: FakeKettle, store: PlanStore) -> WakeService:
    client = KettleClient("http://kettle", transport=kettle.transport(), confirm_timeout=0.2, confirm_interval=0.05)
    return WakeService(client, store)


DAILY = WakePlan(hour=6, minute=30, temp_c=96.0, repeat="daily")
ONCE = WakePlan(hour=6, minute=30, temp_c=96.0, repeat="once")


async def test_apply_arms_kettle_and_persists(service: WakeService, kettle: FakeKettle, store: PlanStore) -> None:
    result = await service.apply(DAILY)
    assert result.kettle == "armed"
    assert kettle.schedule_enabled
    assert store.load().plan == DAILY
    assert store.load().armed


async def test_apply_while_offline_is_pending(service: WakeService, kettle: FakeKettle, store: PlanStore) -> None:
    kettle.online = False
    result = await service.apply(DAILY)
    assert result.kettle == "pending"
    assert store.load().plan == DAILY and not store.load().armed
    kettle.online = True
    await service.reconcile()
    assert kettle.schedule_enabled and store.load().armed


async def test_apply_while_heating_is_pending(service: WakeService, kettle: FakeKettle) -> None:
    kettle.state = "S_Hold"
    assert (await service.apply(DAILY)).kettle == "pending"
    assert not kettle.schedule_enabled


async def test_apply_unsupported_firmware_raises(service: WakeService, kettle: FakeKettle) -> None:
    kettle.firmware = "9.9.9"
    with pytest.raises(UnsupportedFirmware):
        await service.apply(DAILY)


async def test_daily_rearms_after_firing(service: WakeService, kettle: FakeKettle) -> None:
    await service.apply(DAILY)
    kettle.set_clock(6, 30)
    kettle.fire()
    await service.reconcile()  # offline while heating: nothing happens
    kettle.online, kettle.state = True, "S_Off"
    kettle.set_clock(7, 15)
    await service.reconcile()
    assert kettle.schedule_enabled


async def test_daily_does_not_rearm_inside_guard_window(service: WakeService, kettle: FakeKettle) -> None:
    await service.apply(DAILY)
    kettle.set_clock(6, 30)
    kettle.fire()
    kettle.online, kettle.state = True, "S_Off"  # e.g. lifted and replaced right away
    kettle.set_clock(6, 31)
    await service.reconcile()
    assert not kettle.schedule_enabled
    kettle.set_clock(6, 33)
    await service.reconcile()
    assert kettle.schedule_enabled


async def test_once_completes_after_firing(service: WakeService, kettle: FakeKettle, store: PlanStore) -> None:
    await service.apply(ONCE)
    kettle.fire()
    kettle.online, kettle.state = True, "S_Off"
    kettle.set_clock(7, 0)
    await service.reconcile()
    assert not kettle.schedule_enabled
    state = store.load()
    assert state.plan is not None and not state.plan.active


async def test_cancel_disarms(service: WakeService, kettle: FakeKettle, store: PlanStore) -> None:
    await service.apply(DAILY)
    result = await service.cancel()
    assert result.kettle == "disarmed"
    assert not kettle.schedule_enabled
    assert store.load().plan is not None and not store.load().plan.active  # type: ignore[union-attr]


async def test_cancel_while_offline_disarms_later(service: WakeService, kettle: FakeKettle) -> None:
    await service.apply(DAILY)
    kettle.online = False
    assert (await service.cancel()).kettle == "pending"
    kettle.online = True
    await service.reconcile()
    assert not kettle.schedule_enabled


async def test_reconcile_leaves_kettle_alone_without_plan(service: WakeService, kettle: FakeKettle) -> None:
    await service.reconcile()
    assert kettle.posts == []


async def test_reconcile_records_unreachable(service: WakeService, kettle: FakeKettle) -> None:
    await service.apply(DAILY)
    kettle.online = False
    await service.reconcile()
    assert service.last_error is not None


def test_plan_validation() -> None:
    with pytest.raises(ValueError):
        WakePlan(hour=6, minute=30, temp_c=101, repeat="daily")
    with pytest.raises(ValueError):
        WakePlan(hour=25, minute=30, temp_c=90, repeat="daily")
