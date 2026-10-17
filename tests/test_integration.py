"""Regrese registru, dostupnosti, energie a ovládání proti HA 2026.10.1."""

import asyncio
from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiohttp import ClientConnectionError, ClientResponseError
from homeassistant.components.climate import HVACMode
from homeassistant.exceptions import ConfigEntryAuthFailed, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.remeha_home.climate import RemehaHomeClimateEntity
from custom_components.remeha_home.coordinator import RemehaHomeUpdateCoordinator
from custom_components.remeha_home.diagnostics import async_get_config_entry_diagnostics
from custom_components.remeha_home.number import (
    RemehaHomeActivityNumber,
    RemehaHomeDhwSetpointNumber,
    RemehaHomeHeatingCurveNumber,
)
from custom_components.remeha_home.switch import RemehaHomeDhwBoostSwitch
from custom_components.remeha_home.util import supports_reduced_setpoint, validate_value
from custom_components.remeha_home.water_heater import RemehaHomeWaterHeater


def entity(cls, ready, *args):
    """Odpojit jen zápis UI; logika entity a cloudové povely zůstávají testované."""
    coordinator, _ = ready
    item = cls(coordinator.api, coordinator, *args)
    item.async_write_ha_state = Mock()
    return item


async def test_parent_ids_survive_restart(hass, ready, api):
    c, entry = ready
    first = c.get_device_info("zone-1")["via_device_id"]
    assert c.get_device_info("water-1")["via_device_id"] == first
    assert dr.async_get(hass).async_get(first).config_entry_id == entry.entry_id
    restarted = RemehaHomeUpdateCoordinator(hass, api, entry)
    await restarted._async_update_data()
    assert restarted.get_device_info("zone-1")["via_device_id"] == first


async def test_accounts_with_same_cloud_ids_are_isolated(hass, ready, api):
    c, _ = ready
    second = MockConfigEntry(domain="remeha_home", data={}, title="Second", unique_id="second")
    second.add_to_hass(hass)
    c2 = RemehaHomeUpdateCoordinator(hass, api, second)
    await c2._async_update_data()
    assert (
        c2.get_device_info("zone-1")["via_device_id"]
        != c.get_device_info("zone-1")["via_device_id"]
    )


async def test_empty_consumption_is_unknown_not_zero(ready):
    c, _ = ready
    assert c.get_by_id("appliance-1")["consumptionData"]["heatingEnergyConsumed"] is None
    assert c.get_by_id("zone-1")["roomTemperature"] == 20


def test_real_zero_and_missing_energy_are_distinct(ready):
    c, _ = ready
    result = c._process_consumption_data({}, {"heatingEnergyConsumed": 0})
    assert result["heatingEnergyConsumed"] == 0
    assert result["hotWaterEnergyConsumed"] is None


def test_gas_conversion_requires_valid_calorific_value(ready):
    c, _ = ready
    source = {
        "producerPerformanceStatistics": {
            "producers": [
                {"energyType": "NaturalGas", "instanceWithinDevice": 1, "energyConsumptionCH": 20}
            ]
        }
    }
    invalid = c._process_consumption_data({"gasCalorificValue": 0}, deepcopy(source))
    assert invalid["producerPerformanceStatistics"]["producers"][0]["energyConsumptionCH"] is None
    valid = c._process_consumption_data({"gasCalorificValue": 10}, deepcopy(source))
    assert valid["producerPerformanceStatistics"]["producers"][0]["energyConsumptionCH"] == 2


async def test_optional_failure_keeps_main_climate_and_limits_retries(ready, api):
    c, _ = ready
    c.heating_curve_data.clear()
    api.async_get_heating_curve.side_effect = ClientConnectionError()
    c.async_set_updated_data(await c._async_update_data())
    assert c.get_heating_curve("zone-1") is None
    assert c.get_by_id("zone-1")["roomTemperature"] == 20
    count = api.async_get_heating_curve.await_count
    await c._async_update_data()
    assert api.async_get_heating_curve.await_count == count


async def test_optional_auth_error_requests_reauthentication(ready, api):
    c, _ = ready
    c.heating_curve_data.clear()
    api.async_get_heating_curve.side_effect = ClientResponseError(None, (), status=401)
    with pytest.raises(ConfigEntryAuthFailed):
        await c._async_update_data()


async def test_heat_contract_for_existing_automations(ready):
    climate = entity(RemehaHomeClimateEntity, ready, "zone-1")
    assert climate.hvac_mode == HVACMode.HEAT
    assert climate.preset_mode == "manual"
    assert HVACMode.HEAT in climate.hvac_modes
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    ready[0].api.async_set_manual.assert_awaited_once_with("zone-1", 21)


async def test_failed_climate_mode_restores_ui(ready, api):
    climate = entity(RemehaHomeClimateEntity, ready, "zone-1")
    api.async_set_off.side_effect = ClientConnectionError()
    with pytest.raises(ClientConnectionError):
        await climate.async_set_hvac_mode(HVACMode.OFF)
    assert climate.hvac_mode == HVACMode.HEAT


async def test_boost_requires_confirmed_capability_and_schedule(ready, api):
    boost = entity(RemehaHomeDhwBoostSwitch, ready, "water-1")
    ready[0].items["water-1"]["dhwZoneMode"] = "ContinuousComfort"
    with pytest.raises(ServiceValidationError):
        await boost.async_turn_on()
    api.async_set_hot_water_boost.assert_not_awaited()
    ready[0].items["water-1"]["dhwZoneMode"] = "Scheduling"
    ready[0].items["water-1"]["capabilityBoostMode"] = False
    with pytest.raises(ServiceValidationError):
        await boost.async_turn_on()
    api.async_set_hot_water_boost.assert_not_awaited()


async def test_boost_start_stop_and_failure(ready, api):
    boost = entity(RemehaHomeDhwBoostSwitch, ready, "water-1")
    api.async_set_hot_water_boost.side_effect = ClientConnectionError()
    with pytest.raises(ClientConnectionError):
        await boost.async_turn_on()
    assert not boost.is_on
    api.async_set_hot_water_boost.side_effect = None
    await boost.async_turn_on()
    assert boost.is_on
    await boost.async_turn_off()
    api.async_set_dhw_mode_schedule.assert_awaited_once_with("water-1")
    assert not boost.is_on


async def test_failed_water_setpoint_does_not_change_display(ready, api):
    water = entity(RemehaHomeWaterHeater, ready, "appliance-1", "water-1")
    api.async_set_dhw_comfort_setpoint.side_effect = ClientConnectionError()
    with pytest.raises(ClientConnectionError):
        await water.async_set_temperature(temperature=60)
    assert water.target_temperature == 55


async def test_ambiguous_schedule_setpoint_is_not_guessed(ready, api):
    water = entity(RemehaHomeWaterHeater, ready, "appliance-1", "water-1")
    ready[0].items["water-1"]["dhwCurrentActivity"] = None
    with pytest.raises(ServiceValidationError):
        await water.async_set_temperature(temperature=50)
    api.async_set_dhw_comfort_setpoint.assert_not_awaited()
    api.async_set_dhw_reduced_setpoint.assert_not_awaited()


@pytest.mark.parametrize("value", [-1, 100, float("inf"), float("nan"), True, "20"])
def test_untrusted_setpoints_are_rejected(value):
    with pytest.raises(ServiceValidationError):
        validate_value(value, 5, 30)


def test_unknown_tank_type_is_not_assumed():
    assert not supports_reduced_setpoint({})
    assert not supports_reduced_setpoint({"dhwType": "Combi"})
    assert supports_reduced_setpoint({"dhwType": "Tank"})


async def test_curve_fetches_fresh_other_parameter(ready, api):
    curve = entity(RemehaHomeHeatingCurveNumber, ready, "zone-1", "slope")
    api.async_get_heating_curve.return_value["baseSetpoint"] = 25
    await curve.async_set_native_value(1.5)
    api.async_set_heating_curve.assert_awaited_once_with("zone-1", 1.5, 25)


async def test_curve_invalid_limits_block_write(ready, api):
    curve = entity(RemehaHomeHeatingCurveNumber, ready, "zone-1", "slope")
    api.async_get_heating_curve.return_value.pop("maximumSlope")
    with pytest.raises(ServiceValidationError):
        await curve.async_set_native_value(1.5)
    api.async_set_heating_curve.assert_not_awaited()


async def test_activities_change_only_heating_target(ready, api):
    activity = entity(RemehaHomeActivityNumber, ready, "zone-1", 1, "Home")
    assert activity.native_value == 21
    await activity.async_set_native_value(22)
    api.async_set_heating_activities.assert_awaited_once_with(
        "zone-1",
        [{"activityNumber": 1, "temperature": 22}, {"activityNumber": 2, "temperature": 17}],
    )


async def test_zone_writes_are_serialized(ready, api):
    first = entity(
        RemehaHomeDhwSetpointNumber, ready, "water-1", "comfort", "comfortSetPoint", "Comfort"
    )
    second = entity(RemehaHomeDhwSetpointNumber, ready, "water-1", "eco", "reducedSetpoint", "Eco")
    started, finish = asyncio.Event(), asyncio.Event()

    async def slow(*args):
        started.set()
        await finish.wait()

    api.async_set_dhw_comfort_setpoint.side_effect = slow
    task1 = asyncio.create_task(first.async_set_native_value(60))
    await started.wait()
    task2 = asyncio.create_task(second.async_set_native_value(40))
    await asyncio.sleep(0)
    api.async_set_dhw_reduced_setpoint.assert_not_awaited()
    finish.set()
    await asyncio.gather(task1, task2)
    api.async_set_dhw_reduced_setpoint.assert_awaited_once_with("water-1", 40)


async def test_diagnostics_excludes_ids_and_credentials(hass, ready):
    c, entry = ready
    hass.data.setdefault("remeha_home", {})[entry.entry_id] = {"coordinator": c}
    result = await async_get_config_entry_diagnostics(hass, entry)
    text = str(result)
    for private in ["appliance-1", "zone-1", "Test appliance", entry.entry_id, "access_token"]:
        assert private not in text
    assert result["last_update_success"]


async def test_full_platform_setup_has_no_writes_or_via_warning(
    hass, api, enable_custom_integrations, caplog
):
    entry = MockConfigEntry(domain="remeha_home", title="Test", data={}, unique_id="full")
    entry.add_to_hass(hass)
    with (
        patch("custom_components.remeha_home.RemehaHomeAPI", return_value=api),
        # I při nedostupném GitHubu musí být hlavní integrace plně načtená.
        patch(
            "custom_components.remeha_home.upstream.RemehaUpstreamCoordinator._get_json",
            new=AsyncMock(side_effect=ClientConnectionError()),
        ),
        patch(
            "custom_components.remeha_home.config_entry_oauth2_flow.async_get_config_entry_implementation",
            new=AsyncMock(),
        ),
        patch("custom_components.remeha_home.config_entry_oauth2_flow.OAuth2Session"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    registry = er.async_get(hass)
    entities = [
        item for item in registry.entities.values() if item.config_entry_id == entry.entry_id
    ]
    assert {item.domain for item in entities} >= {
        "climate",
        "number",
        "sensor",
        "switch",
        "water_heater",
        "binary_sensor",
        "button",
    }
    assert (
        hass.data["remeha_home"][entry.entry_id]["upstream"].data["original"]["error"]
        == "network_error"
    )
    assert any(
        hass.states.get(item.entity_id).state not in ("unknown", "unavailable")
        for item in entities
        if item.domain == "climate"
    )
    for call in api.mock_calls:
        assert not call[0].startswith("async_set_")
    assert not any(
        "deprecated" in rec.message and "via_device" in rec.message for rec in caplog.records
    )
    assert await hass.config_entries.async_unload(entry.entry_id)


def test_schedule_does_not_reuse_previous_activity(ready):
    """Neurčitý současný cíl nesmí zdědit dřívější Comfort/Eco."""
    c, _ = ready
    zone = c.items["water-1"]
    assert c._derive_dhw_activity(zone) == "Comfort"
    zone["targetSetpoint"] = 50
    assert c._derive_dhw_activity(zone) is None


async def test_late_optional_data_adds_controls_without_duplicates(hass, ready):
    """Po obnově volitelného API přidat nové ovladače a zachovat jediná ID."""
    from types import SimpleNamespace

    from custom_components.remeha_home.number import async_setup_entry as setup_numbers

    c, entry = ready
    curve = dict(c.heating_curve_data["zone-1"])
    c.heating_curve_data["zone-1"] = None
    hass.data.setdefault("remeha_home", {})[entry.entry_id] = {"coordinator": c, "api": c.api}
    lifecycle = SimpleNamespace(entry_id=entry.entry_id, async_on_unload=Mock())
    added = []
    await setup_numbers(hass, lifecycle, added.extend)
    assert not any("heating_curve" in item.unique_id for item in added)
    c.heating_curve_data["zone-1"] = curve
    c.async_set_updated_data(c.data)
    assert len([item for item in added if "heating_curve" in item.unique_id]) == 2
    before = len(added)
    c.async_set_updated_data(c.data)
    assert len(added) == before
    lifecycle.async_on_unload.call_args.args[0]()


def test_sensor_timestamp_keeps_original_offset(ready):
    """API offset +02:00 se převede na UTC bez posunutí okamžiku."""
    from homeassistant.components.sensor import SensorDeviceClass, SensorEntityDescription

    from custom_components.remeha_home.sensor import RemehaHomeSensor

    c, _ = ready
    sensor = RemehaHomeSensor(
        c,
        "zone-1",
        SensorEntityDescription(key="nextSwitchTime", device_class=SensorDeviceClass.TIMESTAMP),
    )
    assert sensor.native_value.hour == 6
    assert sensor.native_value.utcoffset() == timedelta(0)
    c.items["zone-1"]["nextSwitchTime"] = "0001-01-01T00:00:00Z"
    assert sensor.native_value is None
