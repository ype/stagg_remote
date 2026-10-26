import pytest

from stagg_remote.kettle import (
    KettleClient,
    KettleNotReady,
    KettleUnavailable,
    KettleWriteUnconfirmed,
    UnsupportedFirmware,
)
from tests.fake_kettle import FakeKettle


@pytest.fixture
def kettle() -> FakeKettle:
    return FakeKettle()


@pytest.fixture
def client(kettle: FakeKettle) -> KettleClient:
    return KettleClient("http://kettle", transport=kettle.transport(), confirm_timeout=0.3, confirm_interval=0.05)


async def test_firmware(client: KettleClient) -> None:
    assert await client.firmware() == "1.2.26"


async def test_status(client: KettleClient) -> None:
    status = await client.status()
    assert status.state == "S_Off"
    assert status.temp_c == 34.3
    assert status.target_c == 94.0
    assert status.idle_on_base


async def test_status_lifted_kettle_reports_nan(client: KettleClient, kettle: FakeKettle) -> None:
    kettle.temp = None
    status = await client.status()
    assert status.temp_c is None
    assert not status.idle_on_base


async def test_unreachable_raises(client: KettleClient, kettle: FakeKettle) -> None:
    kettle.online = False
    with pytest.raises(KettleUnavailable):
        await client.status()


async def test_arm_schedule_single_write_verified(client: KettleClient, kettle: FakeKettle) -> None:
    settings = await client.set_schedule(6, 30, 96.0, enabled=True)
    assert settings.schedule is not None
    assert (settings.schedule.hour, settings.schedule.minute, settings.schedule.temp_c) == (6, 30, 96.0)
    assert settings.schedule.enabled
    assert len(kettle.posts) == 1


async def test_disarm(client: KettleClient, kettle: FakeKettle) -> None:
    await client.set_schedule(6, 30, 96.0, enabled=True)
    settings = await client.set_schedule(6, 30, 96.0, enabled=False)
    assert settings.schedule is not None and not settings.schedule.enabled
    assert not kettle.schedule_enabled


async def test_arm_refused_unless_idle(client: KettleClient, kettle: FakeKettle) -> None:
    kettle.state = "S_Heat"
    with pytest.raises(KettleNotReady):
        await client.set_schedule(6, 30, 96.0, enabled=True)
    assert kettle.posts == []


async def test_arm_refused_when_lifted(client: KettleClient, kettle: FakeKettle) -> None:
    kettle.temp = None
    with pytest.raises(KettleNotReady):
        await client.set_schedule(6, 30, 96.0, enabled=True)


async def test_disarm_allowed_while_lifted(client: KettleClient, kettle: FakeKettle) -> None:
    kettle.temp = None
    await client.set_schedule(6, 30, 96.0, enabled=False)


async def test_writes_refused_on_unknown_firmware(client: KettleClient, kettle: FakeKettle) -> None:
    kettle.firmware = "1.2.30"
    with pytest.raises(UnsupportedFirmware):
        await client.set_schedule(6, 30, 96.0, enabled=True)
    assert kettle.posts == []


async def test_unconfirmed_write_is_not_retried(client: KettleClient, kettle: FakeKettle) -> None:
    kettle.ignore_writes = True
    with pytest.raises(KettleWriteUnconfirmed):
        await client.set_schedule(6, 30, 96.0, enabled=True)
    assert len(kettle.posts) == 1


async def test_set_clock(client: KettleClient, kettle: FakeKettle) -> None:
    settings = await client.set_clock(7, 45)
    assert settings.clock == (7, 45)
