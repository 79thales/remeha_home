"""Regression contracts for narrowly adapted Hugo fixes; no real cloud writes.

Source: a2bc341 (native temperature members), 01f308b (log privacy) and
57f96bb (account identity). Both API interfaces must expose the same Celsius
values and manual OFF must keep its absent target across all adaptations.
"""

import importlib.util
import logging
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.water_heater import WaterHeaterEntity

from custom_components.remeha_home.api import RemehaHomeAPI
from custom_components.remeha_home.config_flow import _account_id
from custom_components.remeha_home.coordinator import get_producers


@pytest.mark.parametrize("native", [False, True])
def test_native_interface_selection_and_off(monkeypatch, ready, native):
    """Emulate interface capability, retaining the real HA entity base and data.

    This exercises selection and Celsius values; it does not claim a full
    HA 2026.11 runtime integration test when only 2026.10 is installed.
    """
    coordinator, _ = ready
    for base, module, cls in [
        (ClimateEntity, "climate", "RemehaHomeClimateEntity"),
        (WaterHeaterEntity, "water_heater", "RemehaHomeWaterHeater"),
    ]:
        if native:
            monkeypatch.setattr(
                base, "native_temperature_unit", property(lambda self: "°C"), raising=False
            )
        else:
            monkeypatch.delattr(base, "native_temperature_unit", raising=False)
        name = "custom_components.remeha_home._test_" + module
        path = Path(__file__).parents[1] / "custom_components" / "remeha_home" / (module + ".py")
        spec = importlib.util.spec_from_file_location(name, path)
        loaded = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loaded)
        args = ("zone-1",) if module == "climate" else ("appliance-1", "water-1")
        entity = getattr(loaded, cls)(coordinator.api, coordinator, *args)
        key = "native_target_temperature" if native else "target_temperature"
        assert getattr(entity, key) == (21 if module == "climate" else 55)
        if module == "climate":
            coordinator.items["zone-1"]["zoneMode"] = "FrostProtection"
            assert getattr(entity, key) is None
            coordinator.items["zone-1"]["zoneMode"] = "Manual"


async def test_coordinator_debug_omits_payload(hass, api, ready, caplog):
    """Healthy refresh logs endpoint health without household payload markers."""
    coordinator, _ = ready
    api.async_get_dashboard.return_value = None
    original = api.async_get_dashboard.side_effect

    def response():
        data = original()
        data["appliances"][0]["houseName"] = "PRIVATE_HOUSE_SENTINEL"
        data["appliances"][0]["climateZones"][0]["name"] = "PRIVATE_ROOM_SENTINEL"
        return data

    api.async_get_dashboard.side_effect = response
    caplog.set_level(logging.DEBUG, logger="custom_components.remeha_home")
    await coordinator._async_update_data()
    assert "PRIVATE_HOUSE_SENTINEL" not in caplog.text
    assert "PRIVATE_ROOM_SENTINEL" not in caplog.text


async def test_api_does_not_log_raw_responses(caplog):
    """Dashboard, technical and consumption reads must not dump response JSON."""
    api = RemehaHomeAPI()
    response = Mock()
    response.json = AsyncMock(return_value={"private": "PRIVATE_API_SENTINEL"})
    api._async_api_request = AsyncMock(return_value=response)
    caplog.set_level(logging.DEBUG, logger="custom_components.remeha_home")
    await api.async_get_dashboard()
    await api.async_get_appliance_technical_information("synthetic")
    await api.async_get_consumption_data_for_today("synthetic")
    assert "PRIVATE_API_SENTINEL" not in caplog.text


@pytest.mark.parametrize(
    "token", [{}, {"access_token": None}, {"access_token": "invalid"}, {"access_token": "a.b.c"}]
)
def test_malformed_account_metadata_does_not_crash(token):
    assert _account_id(token) is None


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        {"producerPerformanceStatistics": []},
        {
            "producerPerformanceStatistics": {
                "producers": [None, {}, {"instanceWithinDevice": True}]
            }
        },
    ],
)
def test_incomplete_producers_never_register_phantom_devices(data):
    assert get_producers(data) == []


def test_hybrid_collision_does_not_mix_legacy_statistics(ready):
    c, _ = ready
    electric = {
        "instanceWithinDevice": 1,
        "producerType": "HeatPumpAirSource",
        "energyType": "Electric",
        "energyConsumptionCH": 5,
    }
    gas = {
        **electric,
        "instanceWithinDevice": "1",
        "producerType": "GasBoiler",
        "energyType": "NaturalGas",
        "energyConsumptionCH": 99,
    }
    c._update_producers("appliance-1", [electric])
    legacy = c.get_by_id("appliance-1_1")
    wrapped = {"producerPerformanceStatistics": {"producers": [gas, electric]}}
    assert get_producers(wrapped) == []
    c._update_producers("appliance-1", get_producers(wrapped))
    assert c.get_by_id("appliance-1_1")["energyConsumptionCH"] is None
    assert legacy["energyConsumptionCH"] == 5
    c._update_producers(
        "appliance-1", get_producers({"producerPerformanceStatistics": {"producers": [electric]}})
    )
    assert c.get_by_id("appliance-1_1")["energyConsumptionCH"] == 5
