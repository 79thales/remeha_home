"""Ověření rozhraní první fáze na HA s výhradně simulovanými daty zařízení."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from homeassistant.exceptions import ServiceValidationError
from test_temporary import draft

from custom_components.remeha_home.energy_history import combine_days, register_energy_history
from custom_components.remeha_home.temporary import start_override


def test_hybrid_history_and_duplicate_days():
    """Stejný index zdroje nevytváří dvojí součet a sporný den se vyřadí."""
    first = {
        "timeStamp": "2026-01-01T00:00:00Z",
        "heatingEnergyConsumed": 13,
        "producerPerformanceStatistics": {
            "producers": [
                {
                    "producerType": "HeatPumpAirSource",
                    "energyType": "Electric",
                    "instanceWithinDevice": 1,
                    "energyConsumptionCH": 3,
                    "energyProductionCH": 9,
                },
                {
                    "producerType": "GasBoiler",
                    "energyType": "NaturalGas",
                    "instanceWithinDevice": "1",
                    "energyConsumptionCH": 10,
                },
            ]
        },
    }
    second = {**first, "timeStamp": "2026-01-02T00:00:00Z"}
    result = combine_days(
        ["2026-01-01", "2026-01-02", "2026-01-03"],
        [
            {"data": [first, second]},
            {"data": [{**second, "heatingEnergyConsumed": 15}, second]},
        ],
    )
    assert result["missing_dates"] == ["2026-01-02", "2026-01-03"]
    assert result["period"]["complete"] is False
    assert result["period"]["appliance_heating_consumed_kwh"] == 13
    assert result["period"]["heat_pump_heating_consumed_kwh"] == 3
    assert result["period"]["gas_heating_consumed_kwh"] == 10
    assert result["period"]["heat_pump_heating_cop"] == 3


async def test_history_service_batches_and_response(hass, api):
    """Skutečná služba HA přijme ISO data a čte pouze potvrzené ID spotřebiče."""
    register_energy_history(hass)
    api.async_get_consumption_data_for_range.return_value = {"data": []}
    with (
        patch(
            "custom_components.remeha_home.energy_history._appliance",
            return_value=("appliance-1", api),
        ) as resolve,
        patch(
            "custom_components.remeha_home.energy_history.dt_util.now",
            return_value=datetime(2026, 3, 1, 5, 59, tzinfo=UTC),
        ),
    ):
        response = await hass.services.async_call(
            "remeha_home",
            "get_energy_history",
            {
                "device_id": "synthetic",
                "start_date": "2026-01-01",
                "end_date": "2026-03-01",
            },
            blocking=True,
            return_response=True,
        )
    resolve.assert_called_once_with(hass, "synthetic")
    assert response["end_date"] == "2026-02-27"
    assert response["period"]["days_requested"] == 58
    assert [c.args for c in api.async_get_consumption_data_for_range.await_args_list] == [
        ("appliance-1", date(2026, 1, 1), date(2026, 1, 31)),
        ("appliance-1", date(2026, 2, 1), date(2026, 2, 27)),
    ]


async def test_native_seconds_and_pending_guard():
    """90 s se nekrátí na minutu a nejistý povel využívá stávající ochranu."""
    target = draft(
        {
            "zoneMode": "Scheduling",
            "capabilityTemporaryOverrideEndTime": True,
            "setPointMin": 5,
            "setPointMax": 30,
        }
    )
    target.coordinator.temporary_pending = {}
    with patch(
        "custom_components.remeha_home.temporary.dt_util.utcnow",
        return_value=datetime(2026, 1, 1, tzinfo=UTC),
    ):
        await start_override(target, 21.5, timedelta(seconds=90))
    with pytest.raises(ServiceValidationError, match="awaiting confirmation"):
        await start_override(target, 22, timedelta(hours=1))
    target.api.async_set_temporary_override.assert_awaited_once_with(
        "synthetic-zone", 21.5, "2026-01-01T00:01:30Z"
    )
