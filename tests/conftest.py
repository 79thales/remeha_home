"""Syntetická data pro skutečné HA testy; neobsahují data žádné domácnosti."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.remeha_home.api import RemehaHomeAPI
from custom_components.remeha_home.coordinator import RemehaHomeUpdateCoordinator


@pytest.fixture
def api():
    """Napodobit pouze cloud; registry, entity a koordinátor jsou skutečné HA."""
    client = AsyncMock(spec=RemehaHomeAPI)
    client.async_get_dashboard.side_effect = lambda: deepcopy(
        {
            "appliances": [
                {
                    "applianceId": "appliance-1",
                    "houseName": "Test appliance",
                    "applianceType": "HeatPump",
                    "waterPressure": 1.2,
                    "activeThermalMode": "Heating",
                    "outdoorTemperatureInformation": {"applianceOutdoorTemperature": 10},
                    "climateZones": [
                        {
                            "climateZoneId": "zone-1",
                            "name": "Test zone",
                            "zoneMode": "Manual",
                            "roomTemperature": 20,
                            "setPoint": 21,
                            "setPointMin": 5,
                            "setPointMax": 30,
                            "activeComfortDemand": "Idle",
                            "activeHeatingClimateTimeProgramNumber": 1,
                            "firePlaceModeActive": False,
                            "nextSetpoint": 19,
                            "currentScheduleSetPoint": 20,
                            "nextSwitchTime": "2026-10-20T08:00:00+02:00",
                        }
                    ],
                    "hotWaterZones": [
                        {
                            "hotWaterZoneId": "water-1",
                            "name": "Test water",
                            "dhwType": "Tank",
                            "dhwZoneMode": "Scheduling",
                            "dhwStatus": "Idle",
                            "dhwTemperature": 50,
                            "comfortSetPoint": 55,
                            "reducedSetpoint": 45,
                            "targetSetpoint": 55,
                            "capabilityBoostMode": True,
                            "setPointRanges": {
                                "comfortSetpointMin": 35,
                                "comfortSetpointMax": 70,
                                "reducedSetpointMin": 10,
                                "reducedSetpointMax": 60,
                            },
                        }
                    ],
                }
            ]
        }
    )
    client.async_get_appliance_technical_information.return_value = {
        "applianceName": "Test model",
        "internetConnectedGateways": [
            {"name": "Gateway", "hardwareVersion": "1", "softwareVersion": "2"}
        ],
    }
    client.async_get_consumption_data_for_today.return_value = {"data": []}
    client.async_get_heating_curve.return_value = {
        "slope": 1.2,
        "baseSetpoint": 20,
        "minimumSlope": 0.1,
        "maximumSlope": 4,
        "incrementSlope": 0.1,
        "minimumBaseSetpoint": 10,
        "maximumBaseSetpoint": 40,
        "incrementBaseSetpoint": 1,
    }
    client.async_get_activities.return_value = [
        {
            "type": "Cooling",
            "activityNumber": 1,
            "name": "Cooling",
            "temperature": 25,
            "setPointMin": 15,
            "setPointMax": 30,
            "increment": 0.5,
        },
        {
            "type": "Heating",
            "activityNumber": 1,
            "name": "Home",
            "temperature": 21,
            "setPointMin": 5,
            "setPointMax": 30,
            "increment": 0.5,
        },
        {
            "type": "Heating",
            "activityNumber": 2,
            "name": "Away",
            "temperature": 17,
            "setPointMin": 5,
            "setPointMax": 30,
            "increment": 0.5,
        },
    ]
    return client


@pytest.fixture
async def ready(hass, api, enable_custom_integrations):
    """Načíst koordinátor pod skutečnou konfigurační položkou HA."""
    entry = MockConfigEntry(domain="remeha_home", title="Test", data={}, unique_id="test")
    entry.add_to_hass(hass)
    coordinator = RemehaHomeUpdateCoordinator(hass, api, entry)
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    coordinator.async_request_refresh = AsyncMock()
    return coordinator, entry
