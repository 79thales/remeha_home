"""Regrese časování programu a cílení zápisů dovolené pouze na spotřebič."""

import asyncio
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import ClientConnectionError
from homeassistant.components.climate import HVACMode
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from test_integration import entity

from custom_components.remeha_home.api import RemehaHomeAPI
from custom_components.remeha_home.binary_sensor import RemehaHomeBinarySensor
from custom_components.remeha_home.climate import RemehaHomeClimateEntity
from custom_components.remeha_home.const import APPLIANCE_BINARY_SENSOR_TYPES
from custom_components.remeha_home.holiday_water import HolidayWaterRestore
from custom_components.remeha_home.schedule import week
from custom_components.remeha_home.switch import RemehaHomeDhwBoostSwitch
from custom_components.remeha_home.water_heater import RemehaHomeWaterHeater


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
@pytest.mark.parametrize("restart", [False, True])
async def test_holiday_water_restores_only_observed_off_zone(hass, ready, manual, restart):
    """Ověřit obnovu, perzistenci ručního zásahu a ochranu proti druhému povelu."""
    coordinator, entry = ready
    coordinator.api.async_set_dhw_mode_schedule = AsyncMock()
    coordinator.api.async_set_schedule = AsyncMock()
    recover = coordinator.holiday_water_restore
    start = dt_util.now().date()
    end = start + timedelta(days=1)
    zone = {"hotWaterZoneId": "scheduled", "dhwZoneMode": "Scheduling"}
    heating = {
        "climateZoneId": "heating",
        "zoneMode": "Scheduling",
        "activeHeatingClimateTimeProgramNumber": 1,
    }
    appliance = {
        "climateZones": [heating],
        "applianceId": "appliance-1",
        "holidaySchedule": {"active": False},
        "hotWaterZones": [zone, {"hotWaterZoneId": "originally-off", "dhwZoneMode": "Off"}],
    }
    await recover.track(appliance, start, end)
    appliance["holidaySchedule"] = {
        "active": True,
        "startTime": start.isoformat(),
        "endTime": end.isoformat(),
    }
    zone["dhwZoneMode"] = "Off"
    heating["zoneMode"] = "FrostProtection"
    await recover.observe(appliance)
    appliance["holidaySchedule"] = {"active": True}
    await recover.observe(appliance)
    coordinator.api.async_set_dhw_mode_schedule.assert_not_awaited()
    if manual:
        async with recover.async_zone_command("scheduled"):
            pass
        async with recover.async_zone_command("heating"):
            pass
    if restart:
        recover = HolidayWaterRestore(coordinator, entry.entry_id)
        await recover.async_load()
    appliance["holidaySchedule"]["active"] = False
    await recover.observe(appliance)
    await hass.async_block_till_done(wait_background_tasks=True)
    if restart:
        recover = HolidayWaterRestore(coordinator, entry.entry_id)
        await recover.async_load()
    await recover.observe(appliance)
    if manual:
        coordinator.api.async_set_dhw_mode_schedule.assert_not_awaited()
        coordinator.api.async_set_schedule.assert_not_awaited()
    else:
        coordinator.api.async_set_dhw_mode_schedule.assert_awaited_once_with("scheduled")
        coordinator.api.async_set_schedule.assert_awaited_once_with("heating", 1)


@pytest.fixture
async def active_holiday(ready):
    """Zařízení během uloženého termínu potvrdí protimrazový režim a vypnutí TUV."""
    coordinator, _ = ready
    restore = coordinator.holiday_water_restore
    appliance = coordinator.get_by_id("appliance-1")
    heating = coordinator.get_by_id("zone-1")
    water = coordinator.get_by_id("water-1")
    heating["zoneMode"] = "Scheduling"
    start = dt_util.now().date()
    end = start + timedelta(days=1)
    await restore.track(appliance, start, end)
    appliance["holidaySchedule"] = {
        "active": True,
        "startTime": start.isoformat(),
        "endTime": end.isoformat(),
    }
    heating["zoneMode"] = "FrostProtection"
    water["dhwZoneMode"] = "Off"
    await restore.observe(appliance)
    return appliance, heating, water


@pytest.mark.parametrize(
    ("action", "api_name", "fails", "skip_heating", "skip_water"),
    [
        ("off", "async_set_off", False, False, False),
        ("anti_frost", "async_set_off", False, False, False),
        ("eco", "async_set_dhw_mode_eco", False, False, False),
        ("manual", "async_set_manual", False, True, False),
        ("performance", "async_set_dhw_mode_comfort", False, False, True),
        ("manual", "async_set_manual", True, False, False),
        ("performance", "async_set_dhw_mode_comfort", True, False, False),
        ("off_after", "async_set_off", False, True, False),
    ],
)
async def test_holiday_zone_command_survives_restart(
    hass, ready, api, active_holiday, action, api_name, fails, skip_heating, skip_water
):
    """Volbu přebírá pouze dotčená zóna a uchová ji po načtení uložených dat."""
    coordinator, entry = ready
    appliance, heating, water = active_holiday
    after = {"active": False}
    api_command = getattr(api, api_name)
    if fails:
        api_command.side_effect = ClientConnectionError("rejected")

    async def send():
        if action in {"eco", "performance"}:
            dhw = entity(RemehaHomeWaterHeater, ready, "appliance-1", "water-1")
            await dhw.async_set_operation_mode(action)
        else:
            climate = entity(RemehaHomeClimateEntity, ready, "zone-1")
            if action in {"off", "off_after"}:
                await climate.async_set_hvac_mode(HVACMode.OFF)
            else:
                await climate.async_set_preset_mode(action)

    if action == "off_after":
        appliance["holidaySchedule"] = after
    if fails:
        with pytest.raises(ClientConnectionError, match="rejected"):
            await send()
    else:
        await send()
    assert api_command.await_count == 1

    restored = HolidayWaterRestore(coordinator, entry.entry_id)
    await restored.async_load()
    appliance["holidaySchedule"] = after
    # Zařízení může při konci ještě hlásit dovolenkový režim po přijatém povelu.
    heating["zoneMode"] = "FrostProtection"
    water["dhwZoneMode"] = "Off"
    await restored.observe(appliance)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert api.async_set_schedule.await_count == (0 if skip_heating else 1)
    assert api.async_set_dhw_mode_schedule.await_count == (0 if skip_water else 1)
    assert not restored.records


@pytest.mark.parametrize("cancel_boost", ["no", "accepted", "rejected"])
async def test_holiday_boost_cancel_restores_eligibility(
    hass, ready, api, active_holiday, cancel_boost
):
    """Boost má přednost. Zrušení boostu vrátí obnovu i přes mezilehlý stav Schedule."""
    coordinator, entry = ready
    appliance, _, water = active_holiday
    restore = coordinator.holiday_water_restore
    water["dhwZoneMode"] = "Scheduling"
    boost = entity(RemehaHomeDhwBoostSwitch, ready, "water-1")
    await boost.async_turn_on()
    api.async_set_hot_water_boost.assert_awaited_once_with("water-1")
    await restore.observe(appliance)
    if cancel_boost == "accepted":
        await boost.async_turn_off()
        api.async_set_dhw_mode_schedule.assert_awaited_once_with("water-1")
        await restore.observe(appliance)
    elif cancel_boost == "rejected":
        api.async_set_dhw_mode_schedule.side_effect = ClientConnectionError("rejected")
        with pytest.raises(ClientConnectionError, match="rejected"):
            await boost.async_turn_off()
        assert boost.is_on
        api.async_set_dhw_mode_schedule.side_effect = None

    api.async_set_dhw_mode_schedule.reset_mock()
    restarted = HolidayWaterRestore(coordinator, entry.entry_id)
    await restarted.async_load()
    water["dhwZoneMode"] = "Off"
    appliance["holidaySchedule"] = {"active": False}
    await restarted.observe(appliance)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert api.async_set_dhw_mode_schedule.await_count == int(cancel_boost == "accepted")


@pytest.mark.parametrize("fails", [False, True])
async def test_timed_override_during_holiday(hass, ready, api, active_holiday, fails):
    """Po dobu nejistého povelu se zachová samostatná evidence časovaného override."""
    coordinator, _ = ready
    appliance, heating, _ = active_holiday
    coordinator.async_refresh = AsyncMock()
    heating["capabilityTemporaryOverrideEndTime"] = True
    heating["zoneMode"] = "Scheduling"
    climate = entity(RemehaHomeClimateEntity, ready, "zone-1")
    if fails:
        api.async_set_temporary_override.side_effect = ClientConnectionError("rejected")
        with pytest.raises(ClientConnectionError, match="rejected"):
            await climate.async_set_temporary_override(21.5, timedelta(hours=1))
    else:
        await climate.async_set_temporary_override(21.5, timedelta(hours=1))
    assert api.async_set_temporary_override.await_count == 1
    assert "zone-1" in coordinator.temporary_pending

    heating["zoneMode"] = "FrostProtection"
    appliance["holidaySchedule"] = {"active": False}
    await coordinator.holiday_water_restore.observe(appliance)
    await hass.async_block_till_done(wait_background_tasks=True)
    api.async_set_schedule.assert_not_awaited()

    # Čerstvé hlášení po vypršení ověří, že nejistý povel už nemůže být aktivní.
    _, end = coordinator.temporary_pending["zone-1"]
    await coordinator.temporary_override_journal.async_observe(
        [appliance], datetime.fromisoformat(end)
    )
    assert "zone-1" not in coordinator.temporary_pending
    await coordinator.holiday_water_restore.observe(appliance)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert api.async_set_schedule.await_count == int(fails)


@pytest.mark.parametrize("fails", [False, True])
async def test_holiday_end_during_zone_write(hass, ready, api, active_holiday, fails):
    """Obnova počká na výsledek ručního povelu a čtení zařízení nečeká na zámek."""
    coordinator, _ = ready
    appliance, heating, _ = active_holiday
    restore = coordinator.holiday_water_restore
    climate = entity(RemehaHomeClimateEntity, ready, "zone-1")
    entered, release = asyncio.Event(), asyncio.Event()

    async def manual(*_):
        entered.set()
        await release.wait()
        if fails:
            raise ClientConnectionError("rejected")

    api.async_set_manual.side_effect = manual
    command = asyncio.create_task(climate.async_set_preset_mode("manual"))
    await entered.wait()
    try:
        heating["zoneMode"] = "FrostProtection"
        appliance["holidaySchedule"] = {"active": False}
        await asyncio.wait_for(restore.observe(appliance), timeout=1)
        api.async_set_schedule.assert_not_awaited()
    finally:
        release.set()
    if fails:
        with pytest.raises(ClientConnectionError, match="rejected"):
            await command
    else:
        await command
    await hass.async_block_till_done(wait_background_tasks=True)
    assert api.async_set_schedule.await_count == int(fails)


async def test_holiday_loads_previous_release_journal(hass, ready, api, active_holiday):
    """Dovolená založená ve v0.5.11 pokračuje po aktualizaci i v novém formátu."""
    coordinator, entry = ready
    appliance, _, _ = active_holiday
    restored = HolidayWaterRestore(coordinator, entry.entry_id)
    await restored.store.async_save(
        {key: value[:3] for key, value in coordinator.holiday_water_restore.records.items()}
    )
    await restored.async_load()
    appliance["holidaySchedule"] = {"active": False}
    await restored.observe(appliance)
    await hass.async_block_till_done(wait_background_tasks=True)
    api.async_set_schedule.assert_awaited_once_with("zone-1", 1)
    api.async_set_dhw_mode_schedule.assert_awaited_once_with("water-1")
