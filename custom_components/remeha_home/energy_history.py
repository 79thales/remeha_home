"""Čtecí historie energie z jednotlivých zařízení, v kWh dle odhadů API.

Služba má stejný název a vstupy jako u Huga v0.10.2. Historie čte surové denní
záznamy a nijak nevstupuje do živých senzorů, registru ani statistik recorderu.
"""

from datetime import datetime, timedelta
from math import isfinite

import voluptuous as vol
from aiohttp import ClientError
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from .const import DOMAIN

TOTAL_FIELDS = {
    "appliance_heating_consumed_kwh": "heatingEnergyConsumed",
    "appliance_heating_delivered_kwh": "heatingEnergyDelivered",
    "appliance_hot_water_consumed_kwh": "hotWaterEnergyConsumed",
    "appliance_hot_water_delivered_kwh": "hotWaterEnergyDelivered",
}


def _energy(value):
    """Nulu zachovat. Chybějící či neplatné měření označit null."""
    return value if type(value) in (int, float) and isfinite(value) and value >= 0 else None


def _sum_readings(values):
    return sum(values) if values and all(v is not None for v in values) else None


def _cop(consumed, delivered):
    return delivered / consumed if consumed and delivered is not None else None


def _day(row):
    """Převést čas API na jeho den, kWh ponechat odděleně podle zdroje tepla."""
    stamp = row.get("timeStamp")
    if row.get("isDataReported") is False or not isinstance(stamp, str):
        return None
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    # Uzavřené dny jsou označeny půlnocí. Průběžný čas s offsetem je
    # okamžik měření a jeho kalendářní den závisí na místním pásmu HA.
    midnight = not any((moment.hour, moment.minute, moment.second, moment.microsecond))
    day = dt_util.as_local(moment).date() if moment.tzinfo and not midnight else moment.date()
    statistics = row.get("producerPerformanceStatistics")
    raw = statistics.get("producers") if isinstance(statistics, dict) else None
    producers = {}
    ambiguous = set()
    for item in raw if isinstance(raw, list) else ():
        if not isinstance(item, dict):
            continue
        kind, source = item.get("producerType"), item.get("energyType")
        if not isinstance(kind, str) or not kind or not isinstance(source, str) or not source:
            continue
        instance = item.get("instanceWithinDevice")
        if type(instance) is not int and not (isinstance(instance, str) and instance.isdecimal()):
            ambiguous.add((kind, source))
            continue
        identity = (kind, source, int(instance))
        if identity in producers:
            ambiguous.add((kind, source))
        producers[identity] = {
            "producer_type": kind,
            "energy_type": source,
            "heating_consumed_kwh": _energy(item.get("energyConsumptionCH")),
            "heating_delivered_kwh": _energy(item.get("energyProductionCH")),
        }
    # V hybridu může mít plyn i elektřina shodný index. Identitu tvoří celá
    # trojice a nejednoznačné zdroje nesčítáme. Registry entit se nepoužívají.
    usable = [producers[key] for key in sorted(producers) if key[:2] not in ambiguous]
    pumps = [
        p
        for p in usable
        if p["producer_type"].startswith("HeatPump") and p["energy_type"] == "Electric"
    ]
    gas = [
        p for p in usable if p["producer_type"] == "GasBoiler" and p["energy_type"] == "NaturalGas"
    ]
    pump_ambiguous = any(
        kind.startswith("HeatPump") and source == "Electric" for kind, source in ambiguous
    )
    consumed = None if pump_ambiguous else _sum_readings([p["heating_consumed_kwh"] for p in pumps])
    delivered = (
        None if pump_ambiguous else _sum_readings([p["heating_delivered_kwh"] for p in pumps])
    )
    return {
        "date": day.isoformat(),
        "producers": usable,
        **{key: _energy(row.get(field)) for key, field in TOTAL_FIELDS.items()},
        "heat_pump_heating_consumed_kwh": consumed,
        "heat_pump_heating_delivered_kwh": delivered,
        "heat_pump_heating_cop": _cop(consumed, delivered),
        "gas_heating_consumed_kwh": _sum_readings([p["heating_consumed_kwh"] for p in gas]),
    }


def combine_days(dates, responses):
    """Sloučit dávky chronologicky. Rozporný den i mezery přiznat v odpovědi."""
    records = {}
    for response in responses:
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, list):
            raise HomeAssistantError("Remeha returned an invalid energy history response")
        for item in data:
            if not isinstance(item, dict) or (row := _day(item)) is None:
                continue
            key = row["date"]
            if key in dates:
                records[key] = row if key not in records or records[key] == row else None
    missing = [key for key in dates if records.get(key) is None]
    days = [records[key] for key in dates if records.get(key) is not None]
    period = {"complete": not missing, "days_requested": len(dates), "days_reported": len(days)}
    for key in (
        *TOTAL_FIELDS,
        "heat_pump_heating_consumed_kwh",
        "heat_pump_heating_delivered_kwh",
        "gas_heating_consumed_kwh",
    ):
        period[key] = _sum_readings([day[key] for day in days])
    period["heat_pump_heating_cop"] = _cop(
        period["heat_pump_heating_consumed_kwh"], period["heat_pump_heating_delivered_kwh"]
    )
    return {
        "start_date": dates[0],
        "end_date": dates[-1],
        "days": days,
        "missing_dates": missing,
        "period": period,
        "note": "Remeha estimates. Incomplete period totals cover reported days only.",
    }


def _appliance(hass, device_id):
    """Použít aktivní config entry vlastníka konkrétního zařízení nebo předka."""
    registry = dr.async_get(hass)
    visited = set()
    while device_id and device_id not in visited and (device := registry.async_get(device_id)):
        visited.add(device_id)
        entry_data = hass.data[DOMAIN].get(device.config_entry_id)
        if entry_data is not None:
            dashboard = entry_data["coordinator"].data or {}
            for appliance in dashboard.get("appliances", []):
                appliance_id = appliance["applianceId"]
                if (DOMAIN, appliance_id) in device.identifiers:
                    return appliance_id, entry_data["api"]
        device_id = device.via_device_id
    raise ServiceValidationError("Select a device attached to an active Remeha Home appliance")


def register_energy_history(hass: HomeAssistant) -> None:
    """Zaregistrovat službu jednou. Po unload neexistuje cíl, který by šel číst."""

    async def read(call: ServiceCall):
        start, end = call.data["start_date"], call.data["end_date"]
        now = dt_util.now()
        cutoff = now.date() - timedelta(days=1 if now.hour >= 6 else 2)
        if end < start or (end - start).days >= 366 or start > cutoff:
            raise ServiceValidationError(
                "Choose at most 366 days starting on a closed calendar day"
            )
        end = min(end, cutoff)
        dates = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
        appliance_id, api = _appliance(hass, call.data["device_id"])
        responses = []
        try:
            for offset in range(0, len(dates), 31):
                batch_start = start + timedelta(days=offset)
                responses.append(
                    await api.async_get_consumption_data_for_range(
                        appliance_id, batch_start, min(end, batch_start + timedelta(days=30))
                    )
                )
        except (ClientError, TimeoutError) as exc:
            raise HomeAssistantError("Remeha energy history is temporarily unavailable") from exc
        return {"appliance_id": appliance_id, **combine_days(dates, responses)}

    hass.services.async_register(
        DOMAIN,
        "get_energy_history",
        read,
        schema=vol.Schema(
            {
                vol.Required("device_id"): str,
                vol.Required("start_date"): cv.date,
                vol.Required("end_date"): cv.date,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
