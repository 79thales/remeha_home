"""Coordinator for fetching the Remeha Home data."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import date, timedelta
from math import isfinite

import homeassistant.util.dt as dt_util
from aiohttp import ClientError
from aiohttp.client_exceptions import ClientResponseError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RemehaHomeAPI
from .const import DOMAIN
from .util import detect_dhw_setpoint_activity

_LOGGER = logging.getLogger(__name__)

PRODUCER_TYPE_NAMES = {
    "HeatPumpAirSource": "Heat Pump (Air Source)",
    "HeatPumpGroundSource": "Heat Pump (Ground Source)",
    "HeatPumpWaterSource": "Heat Pump (Water Source)",
    "GasBoiler": "Gas Boiler",
    "OilBoiler": "Oil Boiler",
    "ElectricBoiler": "Electric Boiler",
    "CHP": "CHP",
}

PRODUCER_STATS = [
    "energyConsumptionCH",
    "energyConsumptionDHW",
    "energyConsumptionCooling",
    "energyConsumptionTotal",
    "energyProductionCH",
    "energyProductionDHW",
    "energyProductionCooling",
    "energyProductionTotal",
]

APPLIANCE_CONSUMPTION_DEFAULT_DATA = {
    "heatingEnergyConsumed": None,
    "hotWaterEnergyConsumed": None,
    "coolingEnergyConsumed": None,
    "heatingEnergyDelivered": None,
    "hotWaterEnergyDelivered": None,
    "coolingEnergyDelivered": None,
}


def get_producers(consumption: dict) -> list[dict]:
    """Return the producers (heat sources) in the consumption data of an appliance."""
    producer_stats = consumption.get("producerPerformanceStatistics")
    return (producer_stats or {}).get("producers") or []


def get_producer_id(appliance_id: str, producer: dict) -> str:
    """Return the item id of a producer."""
    return f"{appliance_id}_{producer['instanceWithinDevice']}"


class RemehaHomeUpdateCoordinator(DataUpdateCoordinator):
    """Remeha Home update coordinator."""

    def __init__(self, hass: HomeAssistant, api: RemehaHomeAPI, entry: ConfigEntry) -> None:
        """Initialize Remeha Home update coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=60),
        )
        # Jeden zámek na zónu serializuje zápisy TUV a číselných parametrů.
        # Zámek nepřežívá restart a nevytváří žádné automatické řídicí povely.
        self.command_locks: dict[str, asyncio.Lock] = {}
        self.appliance_device_ids: dict[str, str] = {}
        self.optional_data_updated = {}
        self.api = api
        self.items = {}
        self.device_info = {}
        self.technical_info = {}
        self.appliance_consumption_data = {}
        self.appliance_last_consumption_data_update = {}
        # Date of the last non-empty consumption data, to tell an empty response
        # during the day apart from the start of a new day
        self.appliance_consumption_data_date: dict[str, date] = {}
        self.appliance_producer_ids: dict[str, set[str]] = {}
        self.dhw_activity_cache = {}
        self.heating_curve_data: dict[str, dict] = {}
        self.activities_data: dict[str, list] = {}

    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        try:
            # Note: asyncio.TimeoutError and aiohttp.ClientError are already
            # handled by the data update coordinator.
            async with asyncio.timeout(30):
                data = await self.api.async_get_dashboard()
                _LOGGER.debug("Requested dashboard information: %s", data)
        except ClientResponseError as err:
            # Raising ConfigEntryAuthFailed will cancel future updates
            # and start a config flow with SOURCE_REAUTH (async_step_reauth)
            if err.status == 401:
                raise ConfigEntryAuthFailed from err

            raise UpdateFailed from err

        # Save the current time for appliance usage data updates
        now = dt_util.utcnow()
        device_registry = dr.async_get(self.hass)

        for appliance in data["appliances"]:
            appliance_id = appliance["applianceId"]
            self.items[appliance_id] = appliance

            # Request appliance technical information the first time it is discovered
            if appliance_id not in self.technical_info:
                self.technical_info[
                    appliance_id
                ] = await self.api.async_get_appliance_technical_information(appliance_id)
                _LOGGER.debug(
                    "Requested technical information for appliance %s: %s",
                    appliance_id,
                    self.technical_info[appliance_id],
                )

            # Only update appliance usage data every 15 minutes
            if (appliance_id not in self.appliance_last_consumption_data_update) or (
                now - self.appliance_last_consumption_data_update[appliance_id]
                >= timedelta(minutes=14, seconds=45)
            ):
                try:
                    consumption_data = await self.api.async_get_consumption_data_for_today(
                        appliance_id
                    )
                    _LOGGER.debug(
                        "Requested consumption data for appliance %s: %s",
                        appliance_id,
                        consumption_data,
                    )

                    # Nula je měření jen tehdy, pokud ji API skutečně vrátí.
                    # Prázdná odpověď, chybějící pole ani nový den nejsou nula.
                    # Data bez potvrzené čerstvosti se zobrazí jako neznámá;
                    # nevytváříme falešný reset denních součtů v energetice HA.
                    rows = consumption_data.get("data") or []
                    if rows:
                        consumption = self._process_consumption_data(appliance, rows[0])
                        self.appliance_consumption_data[appliance_id] = consumption
                        self.appliance_consumption_data_date[appliance_id] = now.date()
                    else:
                        self.appliance_consumption_data.pop(appliance_id, None)
                except ClientResponseError as err:
                    if err.status == 401:
                        raise ConfigEntryAuthFailed from err
                    self.appliance_consumption_data.pop(appliance_id, None)
                    _LOGGER.warning("Consumption request failed (HTTP %s)", err.status)
                except (ClientError, TimeoutError, ValueError, TypeError, KeyError):
                    # Volitelné energetické údaje nesmějí znepřístupnit termostat.
                    self.appliance_consumption_data.pop(appliance_id, None)
                    _LOGGER.warning("Consumption data unavailable")
                finally:
                    # I chybu opakujeme nejdříve za 15 minut, ne každých 60 s.
                    self.appliance_last_consumption_data_update[appliance_id] = now

            if self.appliance_consumption_data_date.get(appliance_id) != now.date():
                self.appliance_consumption_data.pop(appliance_id, None)
            appliance["consumptionData"] = self.appliance_consumption_data.get(
                appliance_id, dict(APPLIANCE_CONSUMPTION_DEFAULT_DATA)
            )

            self.device_info[appliance_id] = DeviceInfo(
                identifiers={(DOMAIN, appliance_id)},
                name=appliance["houseName"],
                manufacturer="Remeha",
                model=self.technical_info[appliance_id]["applianceName"],
            )

            # Zaregistrovat rodiče před zónami; vlastník + identifikátory drží
            # stejné ID při aktualizaci, restartu i nové instalaci. Zóny i zdroje
            # energie používají interní ID, nikoli odstraněný parametr via_device.
            parent = device_registry.async_get_or_create(
                config_entry_id=self.config_entry.entry_id,
                identifiers={(DOMAIN, appliance_id)},
                name=appliance["houseName"],
                manufacturer="Remeha",
                model=self.technical_info[appliance_id]["applianceName"],
            )
            self.appliance_device_ids[appliance_id] = parent.id

            for climate_zone in appliance["climateZones"]:
                climate_zone_id = climate_zone["climateZoneId"]
                # This assumes that all climate zones for an appliance share the same gateway
                gateways = self.technical_info[appliance_id]["internetConnectedGateways"]

                if len(gateways) > 1:
                    _LOGGER.warning(
                        "Appliance %s has more than one gateway, using technical information from the first one",
                        appliance_id,
                    )

                if len(gateways) > 0:
                    gateway_info = gateways[0]
                else:
                    _LOGGER.warning(
                        "Appliance %s has no gateways, using unknown values",
                        appliance_id,
                    )
                    gateway_info = {
                        "name": "Unknown",
                        "hardwareVersion": "Unknown",
                        "softwareVersion": "Unknown",
                    }

                self.items[climate_zone_id] = climate_zone

                # Volitelné funkce se obnovují po 15 minutách nebo po zápisu.
                # Chyba/timeout/absence podpory neshodí hlavní termostat.
                await self._async_refresh_optional(
                    climate_zone_id,
                    "curve",
                    self.heating_curve_data,
                    self.api.async_get_heating_curve,
                    now,
                    dict,
                )
                await self._async_refresh_optional(
                    climate_zone_id,
                    "activities",
                    self.activities_data,
                    self.api.async_get_activities,
                    now,
                    list,
                )

                self.device_info[climate_zone_id] = DeviceInfo(
                    identifiers={(DOMAIN, climate_zone_id)},
                    name=climate_zone["name"],
                    manufacturer="Remeha",
                    model=gateway_info["name"],
                    hw_version=gateway_info["hardwareVersion"],
                    sw_version=gateway_info["softwareVersion"],
                    via_device_id=self.appliance_device_ids[appliance_id],
                )

            for hot_water_zone in appliance["hotWaterZones"]:
                hot_water_zone_id = hot_water_zone["hotWaterZoneId"]
                hot_water_zone["dhwCurrentActivity"] = self._derive_dhw_activity(hot_water_zone)

                self.items[hot_water_zone_id] = hot_water_zone
                self.device_info[hot_water_zone_id] = DeviceInfo(
                    identifiers={(DOMAIN, hot_water_zone_id)},
                    name=hot_water_zone["name"],
                    manufacturer="Remeha",
                    model="Hot Water Zone",
                    via_device_id=self.appliance_device_ids[appliance_id],
                )

            self._update_producers(appliance_id, get_producers(appliance["consumptionData"]))
        return data

    def _update_producers(self, appliance_id: str, producers: list[dict]) -> None:
        """Register the producers (heat sources) of an appliance as devices."""
        type_counts = Counter(producer.get("producerType") for producer in producers)
        current_ids = set()

        for producer in producers:
            producer_id = get_producer_id(appliance_id, producer)
            current_ids.add(producer_id)
            self.items[producer_id] = producer

            producer_type = producer.get("producerType", "Producer")
            producer_label = PRODUCER_TYPE_NAMES.get(producer_type, producer_type)
            # Only number the producers when there are several of the same type
            if type_counts[producer_type] > 1:
                name = f"{producer_label} {producer['instanceWithinDevice']}"
            else:
                name = producer_label
            self.device_info[producer_id] = DeviceInfo(
                identifiers={(DOMAIN, producer_id)},
                name=name,
                manufacturer="Remeha",
                model=producer_label,
                via_device_id=self.appliance_device_ids[appliance_id],
            )

        # Chybějící zdroj neznamená nulovou spotřebu. Existující entitu ponecháme,
        # ale měření označíme jako neznámé; po návratu API dostane skutečná data.
        known_ids = self.appliance_producer_ids.setdefault(appliance_id, set())
        for producer_id in known_ids - current_ids:
            self.items[producer_id] = {
                **self.items[producer_id],
                **dict.fromkeys([*PRODUCER_STATS, "seasonalEfficiency"], None),
            }
        known_ids |= current_ids

    async def _async_refresh_optional(self, zone_id, key, cache, fetch, now, expected):
        """Načíst volitelný endpoint s 10s timeoutem a 15min intervalem.

        Vstup je ID zóny a čtecí metoda API, výstup dict/list nebo None v cache.
        None nepředstírá podporu funkce. HTTP 401 vyžádá nové přihlášení;
        ostatní síťové chyby povolí další pokus po intervalu. Po úspěšném zápisu
        ovladač odstraní cache, tím vynutí okamžité opětovné čtení.
        """
        stamp = self.optional_data_updated.get((zone_id, key))
        if zone_id in cache and stamp is not None and now - stamp < timedelta(minutes=15):
            return
        try:
            async with asyncio.timeout(10):
                value = await fetch(zone_id)
            if not isinstance(value, expected):
                raise ValueError("Unexpected optional response type")
            cache[zone_id] = value
        except ClientResponseError as err:
            if err.status == 401:
                raise ConfigEntryAuthFailed from err
            cache[zone_id] = None
            _LOGGER.debug("Optional %s endpoint unavailable (HTTP %s)", key, err.status)
        except (ClientError, TimeoutError, ValueError, TypeError):
            cache[zone_id] = None
            _LOGGER.debug("Optional %s data unavailable", key)
        finally:
            self.optional_data_updated[(zone_id, key)] = now

    def _process_consumption_data(self, appliance: dict, consumption: dict) -> dict:
        """Zachovat měřené nuly a ponechat chybějící energie jako None.

        Energie z API jsou kWh; plynové senzory CH a DHW mají m³. Převod
        vyžaduje kladnou konečnou výhřevnost v kWh/m³ ze stejného spotřebiče.
        Bez ní objem neodhadujeme. Sezónní účinnost zůstává hodnotou API.
        """
        consumption = {**APPLIANCE_CONSUMPTION_DEFAULT_DATA, **consumption}
        for key in APPLIANCE_CONSUMPTION_DEFAULT_DATA:
            value = consumption.get(key)
            if type(value) not in (int, float) or not isfinite(value):
                consumption[key] = None
        for producer in get_producers(consumption):
            for key in PRODUCER_STATS:
                value = producer.get(key)
                if type(value) not in (int, float) or not isfinite(value):
                    producer[key] = None
            if producer.get("energyType") == "NaturalGas":
                try:
                    calorific = float(appliance.get("gasCalorificValue"))
                except (TypeError, ValueError):
                    calorific = 0
                for key in ("energyConsumptionCH", "energyConsumptionDHW"):
                    value = producer[key]
                    producer[key] = (
                        round(value / calorific, 3)
                        if value is not None and isfinite(calorific) and calorific > 0
                        else None
                    )
        return consumption

    def get_by_id(self, item_id: str):
        """Return item with the specified item id."""
        return self.items.get(item_id)

    def get_device_info(self, item_id: str):
        """Return device info for the item with the specified id."""
        return self.device_info.get(item_id)

    def get_heating_curve(self, climate_zone_id: str) -> dict | None:
        """Return heating curve data for the specified climate zone."""
        return self.heating_curve_data.get(climate_zone_id)

    def get_activities(self, climate_zone_id: str) -> list | None:
        """Return activities data for the specified climate zone."""
        return self.activities_data.get(climate_zone_id)

    def _derive_dhw_activity(self, zone: dict) -> str | None:
        """Derive a human-friendly DHW activity label."""
        mode = zone.get("dhwZoneMode")
        if mode in ("ContinuousComfort", "Boost"):
            activity = "Comfort"
            self.dhw_activity_cache[zone.get("hotWaterZoneId")] = activity
            return activity
        if mode == "Scheduling":
            activity = detect_dhw_setpoint_activity(
                zone.get("targetSetpoint"),
                zone.get("comfortSetPoint"),
                zone.get("reducedSetpoint"),
            )
            if activity is not None:
                self.dhw_activity_cache[zone.get("hotWaterZoneId")] = activity
                return activity
            return self.dhw_activity_cache.get(zone.get("hotWaterZoneId"))
        if mode == "Off":
            # Called Eco in the Remeha Home app, the reduced setpoint applies
            activity = "Eco"
            self.dhw_activity_cache[zone.get("hotWaterZoneId")] = activity
            return activity
        return None
