"""Ověření rozhraní první fáze na HA s výhradně simulovanými daty zařízení."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from test_integration import entity
from test_temporary import draft

from custom_components.remeha_home.climate import RemehaHomeClimateEntity
from custom_components.remeha_home.energy_history import combine_days, register_energy_history
from custom_components.remeha_home.temporary import start_override


@pytest.mark.parametrize(
    ("reported_mode", "override_active"),
    [
        ("Scheduling", False),
        ("TemporaryOverride", True),
        ("Manual", False),
        ("FrostProtection", False),
        (None, None),
    ],
)
async def test_zone_mode_attribute_keeps_override_guards(ready, reported_mode, override_active):
    """Hugovo jméno rozliší oba režimy auto i bez času konce override."""
    coordinator, _ = ready
    climate = entity(RemehaHomeClimateEntity, ready, "zone-1")
    raw = coordinator.get_by_id("zone-1")
    raw["zoneMode"] = reported_mode
    raw.pop("temporaryOverride", None)
    attributes = climate.extra_state_attributes
    assert attributes["zone_mode"] == reported_mode
    assert "remeha_zone_mode" not in attributes
    assert attributes["override_end_time"] is None
    assert attributes["temporary_override_active"] is override_active
    assert attributes["temporary_override_command_pending"] is False


@pytest.mark.parametrize("reported_mode", ["Scheduling", "TemporaryOverride", "FrostProtection"])
@pytest.mark.parametrize(
    ("raw_end", "parsed_end"),
    [
        ("2026-11-25T21:00:00Z", datetime(2026, 11, 25, 21, tzinfo=UTC)),
        ("2026-11-25T21:00:00", datetime(2026, 11, 25, 21, tzinfo=UTC)),
        ("0001-01-01T00:00:00Z", None),
    ],
)
async def test_override_end_matches_hugo_independently_of_mode(
    ready, reported_mode, raw_end, parsed_end
):
    """Zveřejnit platný čas i při opožděném poli za cloudovým režimem."""
    coordinator, _ = ready
    climate = entity(RemehaHomeClimateEntity, ready, "zone-1")
    coordinator.get_by_id("zone-1").update(
        zoneMode=reported_mode, temporaryOverride={"endTime": raw_end}
    )

    attributes = climate.extra_state_attributes
    assert attributes["zone_mode"] == reported_mode
    assert attributes["override_end_time"] == (
        dt_util.as_local(parsed_end).isoformat() if parsed_end else None
    )
    assert attributes["temporary_override_end_time"] == (
        raw_end if reported_mode == "TemporaryOverride" else None
    )


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


async def test_native_seconds_and_pending_guard(temporary_journal):
    """90 s se nekrátí na minutu a nejistý povel využívá stávající ochranu."""
    target = draft(
        {
            "zoneMode": "Scheduling",
            "capabilityTemporaryOverrideEndTime": True,
            "setPointMin": 5,
            "setPointMax": 30,
        },
        temporary_journal,
    )
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


async def test_holiday_identity_migration_preserves_entity_id(hass, ready):
    """Po aktualizaci a opakovaném načtení drží HA původní řádek registru."""
    from homeassistant.helpers import entity_registry as er

    from custom_components.remeha_home import binary_sensor

    coordinator, entry = ready
    hass.data["remeha_home"] = {entry.entry_id: {"coordinator": coordinator}}
    registry = er.async_get(hass)
    prefix = "remeha_home_appliance-1_holidaySchedule"
    original = registry.async_get_or_create(
        "binary_sensor", "remeha_home", prefix + ".active", config_entry=entry
    )
    for _ in range(2):
        entities = []
        with patch.object(binary_sensor.entity_platform, "async_get_current_platform"):
            await binary_sensor.async_setup_entry(hass, entry, entities.extend)
        assert sum(entity.unique_id == prefix for entity in entities) == 1
        assert registry.async_get(original.entity_id).unique_id == prefix
        assert registry.async_get(original.entity_id).id == original.id


async def test_preheat_preserves_other_zones(ready):
    """Nové čtení těsně před POST určí hodnotu druhé zóny, neúplné čtení blokuje zápis."""
    from unittest.mock import AsyncMock

    from homeassistant.exceptions import ServiceValidationError

    from custom_components.remeha_home.switch import PreHeatSwitch

    coordinator, _ = ready
    api = AsyncMock()
    zones = [
        {"climateZoneId": "a", "preHeat": {"enabled": False}},
        {"climateZoneId": "b", "preHeat": {"enabled": True}},
    ]
    api.async_get_dashboard.return_value = {
        "appliances": [{"applianceId": "one", "capabilityPreHeat": True, "climateZones": zones}]
    }
    switch = PreHeatSwitch(api, coordinator, "one", "a")
    await switch.async_turn_on()
    api.async_set_pre_heat.assert_awaited_once_with("one", {"a": True, "b": True})
    zones[1].pop("preHeat")
    with pytest.raises(ServiceValidationError):
        await switch.async_turn_on()
    assert api.async_set_pre_heat.await_count == 1
