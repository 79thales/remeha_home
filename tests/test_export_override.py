"""Synthetic tests for journaled export overlays; never contact an appliance."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.remeha_home.export_override import (
    ExportOverrideManager,
    invalidate_local_export,
    reconcile_export,
    restore_export,
)
from custom_components.remeha_home.export_override import (
    start_export as _start_export,
)


async def start_export(entity, temperature):
    """Supply valid synthetic guard inputs for the ordinary scenarios."""
    deadline = (dt_util.utcnow() + timedelta(minutes=10)).isoformat()
    await _start_export(entity, temperature, "binary_sensor.guard", deadline, "sensor.export", 60)


@pytest.fixture
def scenario():
    """A cloud with a timed user override; POST updates only the mocked state."""
    end = (dt_util.utcnow() + timedelta(hours=3)).replace(microsecond=0).isoformat()
    zone = {
        "zoneMode": "TemporaryOverride",
        "setPoint": 20.0,
        "setPointMin": 5.0,
        "setPointMax": 30.0,
        "capabilityTemporaryOverrideEndTime": True,
        "temporaryOverride": {"endTime": end},
    }
    manager = object.__new__(ExportOverrideManager)
    manager.records = {}

    async def save(zone_id, record):
        if record is None:
            manager.records.pop(zone_id, None)
        else:
            manager.records[zone_id] = deepcopy(record)

    manager.save = AsyncMock(side_effect=save)
    coordinator = Mock()
    coordinator.command_locks = {}
    coordinator.temporary_pending = {}
    coordinator.export_override_manager = manager
    coordinator.last_update_success = True
    coordinator.async_refresh = AsyncMock()
    coordinator.get_by_id.return_value = zone
    api = Mock()

    async def post(_zone_id, temperature, end_time):
        zone["setPoint"] = temperature
        zone["temporaryOverride"]["endTime"] = end_time

    api.async_set_temporary_override = AsyncMock(side_effect=post)
    entity = Mock(coordinator=coordinator, api=api, climate_zone_id="zone-1")
    states = {
        "binary_sensor.guard": Mock(state="on"),
        "sensor.export": Mock(state="1000", last_reported=dt_util.utcnow()),
    }
    entity.hass.states.get.side_effect = states.get
    return entity, zone, manager


async def test_export_preserves_user_and_end_and_restores_only_on_request(scenario):
    entity, zone, manager = scenario
    original_end = dt_util.parse_datetime(zone["temporaryOverride"]["endTime"])
    await start_export(entity, 22.0)
    assert manager.records["zone-1"]["user_temperature"] == 20.0
    assert manager.records["zone-1"]["phase"] == "active"
    assert dt_util.parse_datetime(zone["temporaryOverride"]["endTime"]) == original_end
    await restore_export(entity)
    assert zone["setPoint"] == 20.0
    assert dt_util.parse_datetime(zone["temporaryOverride"]["endTime"]) == original_end
    assert manager.records == {}


async def test_export_never_lowers_user_temperature(scenario):
    entity, _, manager = scenario
    await start_export(entity, 19.0)
    entity.api.async_set_temporary_override.assert_not_awaited()
    assert manager.records == {}


async def test_manual_change_blocks_restore(scenario):
    entity, zone, _ = scenario
    await start_export(entity, 22.0)
    zone["setPoint"] = 21.0
    with pytest.raises(ServiceValidationError):
        await restore_export(entity)
    assert entity.api.async_set_temporary_override.await_count == 1


async def test_timeout_is_journaled_and_never_retried(scenario):
    entity, _, manager = scenario
    entity.api.async_set_temporary_override.side_effect = TimeoutError
    with pytest.raises(TimeoutError):
        await start_export(entity, 22.0)
    assert manager.records["zone-1"]["phase"] == "start_pending"
    with pytest.raises(ServiceValidationError):
        await restore_export(entity)
    with pytest.raises(ServiceValidationError):
        await reconcile_export(entity)
    assert entity.api.async_set_temporary_override.await_count == 1


async def test_journal_failure_prevents_cloud_write(scenario):
    entity, _, manager = scenario
    manager.save.side_effect = OSError("storage unavailable")
    with pytest.raises(OSError):
        await start_export(entity, 22.0)
    entity.api.async_set_temporary_override.assert_not_awaited()


async def test_expired_or_unknown_end_never_starts(scenario):
    entity, zone, _ = scenario
    zone["temporaryOverride"]["endTime"] = "2000-01-01T00:00:00Z"
    with pytest.raises(ServiceValidationError):
        await start_export(entity, 22.0)
    entity.api.async_set_temporary_override.assert_not_awaited()


async def test_local_intervention_blocks_same_value_restore(scenario):
    entity, _, manager = scenario
    await start_export(entity, 22.0)
    await invalidate_local_export(entity)
    assert manager.records["zone-1"]["phase"] == "intervened"
    with pytest.raises(ServiceValidationError):
        await restore_export(entity)
    assert entity.api.async_set_temporary_override.await_count == 1


async def test_guard_falling_during_journal_save_prevents_write_and_removes_record(scenario):
    entity, _, manager = scenario
    original_save = manager.save.side_effect

    async def save_then_drop(zone_id, record):
        await original_save(zone_id, record)
        if record is not None:
            entity.hass.states.get("binary_sensor.guard").state = "off"

    manager.save.side_effect = save_then_drop
    with pytest.raises(ServiceValidationError):
        await start_export(entity, 22)
    entity.api.async_set_temporary_override.assert_not_awaited()
    assert manager.records == {}


async def test_failed_cleanup_retains_pending_journal_without_cloud_write(scenario):
    entity, _, manager = scenario
    original_save = manager.save.side_effect

    async def save_then_fail_cleanup(zone_id, record):
        if record is None:
            raise OSError("storage unavailable")
        await original_save(zone_id, record)
        entity.hass.states.get("binary_sensor.guard").state = "off"

    manager.save.side_effect = save_then_fail_cleanup
    with pytest.raises(OSError):
        await start_export(entity, 22)
    entity.api.async_set_temporary_override.assert_not_awaited()
    assert manager.records["zone-1"]["phase"] == "start_pending"


@pytest.mark.parametrize("value", ["unknown", "unavailable", "nan", "inf"])
async def test_invalid_measurement_never_writes(scenario, value):
    entity, _, manager = scenario
    entity.hass.states.get("sensor.export").state = value
    with pytest.raises(ServiceValidationError):
        await start_export(entity, 22)
    entity.api.async_set_temporary_override.assert_not_awaited()
    assert manager.records == {}


async def test_stale_measurement_and_expired_guard_never_write(scenario):
    entity, _, _ = scenario
    entity.hass.states.get("sensor.export").last_reported = dt_util.utcnow() - timedelta(minutes=2)
    with pytest.raises(ServiceValidationError):
        await start_export(entity, 22)
    deadline = (dt_util.utcnow() - timedelta(seconds=1)).isoformat()
    with pytest.raises(ServiceValidationError):
        await _start_export(entity, 22, "binary_sensor.guard", deadline, "sensor.export", 60)
    entity.api.async_set_temporary_override.assert_not_awaited()


async def test_restart_does_not_itself_replay_command(scenario):
    entity, _, manager = scenario
    await start_export(entity, 22.0)
    saved = deepcopy(manager.records)
    manager.records = saved
    assert entity.api.async_set_temporary_override.await_count == 1
    assert manager.records["zone-1"]["phase"] == "active"
