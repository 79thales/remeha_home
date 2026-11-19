"""Regrese časování programu a cílení zápisů dovolené pouze na spotřebič."""

from datetime import date, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.remeha_home.api import RemehaHomeAPI
from custom_components.remeha_home.binary_sensor import RemehaHomeBinarySensor
from custom_components.remeha_home.const import APPLIANCE_BINARY_SENSOR_TYPES
from custom_components.remeha_home.schedule import week


def test_week_maps_days_and_temperatures():
    program = {
        "switchPoints": [
            {"day": 0, "time": "09:00", "activity": 2, "customValue": 19},
            {"day": 1, "time": "07:00", "activity": 2, "comfortActive": False},
        ]
    }
    heating = week(
        program,
        {},
        [{"type": "Heating", "activityNumber": 2, "name": "Home", "temperature": 21}],
        False,
    )
    assert list(heating)[0] == "monday"
    assert heating["monday"][0]["setpoint"] == 21
    assert heating["sunday"][0]["setpoint"] == 19
    assert week(program, {"dhwType": "Combi"}, [], True)["monday"][0] == {
        "time": "07:00",
        "mode": "Eco",
        "setpoint": None,
    }
    assert week(None, {}, [], True) is None


async def test_holiday_target_dates_and_cancel(ready):
    coordinator, _ = ready
    coordinator.api.async_write_holiday = AsyncMock()
    start = dt_util.now().date() + timedelta(days=1)
    end = start + timedelta(days=2)
    for desc, transform in APPLIANCE_BINARY_SENSOR_TYPES:
        sensor = RemehaHomeBinarySensor(coordinator, "appliance-1", desc, transform)
        if desc.key != "holidaySchedule.active":
            with pytest.raises(ServiceValidationError):
                await sensor.async_cancel_holiday_mode()
            continue
        with pytest.raises(ServiceValidationError):
            await sensor.async_set_holiday_mode(end, start)
        await sensor.async_set_holiday_mode(start, end)
        coordinator.api.async_write_holiday.assert_awaited_once_with("appliance-1", start, end)
        await sensor.async_cancel_holiday_mode()
    assert coordinator.api.async_write_holiday.await_count == 2
    coordinator.api.async_write_holiday.assert_awaited_with("appliance-1", None, None)


async def test_holiday_api_contract():
    api = Mock()
    api._async_api_request = AsyncMock(return_value=Mock())
    for start, end, expected in [
        (date(2030, 1, 2), date(2030, 1, 3), ("2030-01-02T00:00:00Z", "2030-01-03T00:00:00Z")),
        (None, None, ("0001-01-01T00:00:00Z", "0001-01-01T00:00:01Z")),
    ]:
        await RemehaHomeAPI.async_write_holiday(api, "one", start, end)
        api._async_api_request.assert_awaited_with(
            "POST",
            "/appliances/one/holidayschedule",
            json=dict(zip(("holidayModeStartTime", "holidayModeEndTime"), expected, strict=True)),
        )


@pytest.mark.parametrize("manual", [False, True])
async def test_holiday_water_restores_only_observed_off_zone(ready, manual):
    coordinator, _ = ready
    coordinator.api.async_set_dhw_mode_schedule = AsyncMock()
    recover = coordinator.holiday_water_restore
    start = dt_util.now().date()
    end = start + timedelta(days=1)
    zone = {"hotWaterZoneId": "scheduled", "dhwZoneMode": "Scheduling"}
    appliance = {
        "applianceId": "appliance-1",
        "holidaySchedule": {"active": False},
        "hotWaterZones": [zone, {"hotWaterZoneId": "originally-off", "dhwZoneMode": "Off"}],
    }
    recover.track(appliance, start, end)
    appliance["holidaySchedule"] = {
        "active": True,
        "startTime": start.isoformat(),
        "endTime": end.isoformat(),
    }
    zone["dhwZoneMode"] = "Off"
    await recover.observe(appliance)
    appliance["holidaySchedule"] = {"active": True}
    await recover.observe(appliance)
    coordinator.api.async_set_dhw_mode_schedule.assert_not_awaited()
    if manual:
        recover.forget_zone("scheduled")
    appliance["holidaySchedule"]["active"] = False
    await recover.observe(appliance)
    await recover.observe(appliance)
    if manual:
        coordinator.api.async_set_dhw_mode_schedule.assert_not_awaited()
    else:
        coordinator.api.async_set_dhw_mode_schedule.assert_awaited_once_with("scheduled")
