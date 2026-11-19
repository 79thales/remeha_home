"""Obnova časového programu vytápění a TUV po dovolené potvrzené zařízením."""

import asyncio
from copy import deepcopy

from aiohttp import ClientError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util


class HolidayWaterRestore:
    """Sledovat pouze termíny založené touto instancí integrace.

    Podobně jako Hugo ukládá původní režimy do úložiště HA. Dvojice dat
    sváže snímek s termínem. Zápis obnovení vyžaduje dříve potvrzenou změnu
    zóny a nové čtení po skončení dovolené.
    """

    def __init__(self, coordinator, entry_id):
        self.coordinator = coordinator
        self.store = Store(coordinator.hass, 1, f"remeha_home.{entry_id}.holiday_recovery")
        self.records = {}
        self._journal_lock = asyncio.Lock()

    async def async_load(self):
        """Načíst snímek před prvním dotazem na cloud. Načtení samo nic nezapisuje."""
        data = await self.store.async_load()
        if isinstance(data, dict):
            self.records = data

    async def _save(self, previous):
        if previous == self.records:
            return
        try:
            await self.store.async_save(self.records)
        except BaseException:
            self.records = previous
            raise

    async def track(self, appliance, start, end):
        zones = {
            zone["hotWaterZoneId"]: False
            for zone in appliance.get("hotWaterZones", [])
            if zone.get("dhwZoneMode") == "Scheduling"
        }
        heating = {
            zone["climateZoneId"]: [zone["activeHeatingClimateTimeProgramNumber"], False]
            for zone in appliance.get("climateZones", [])
            if zone.get("zoneMode") == "Scheduling"
            and type(zone.get("activeHeatingClimateTimeProgramNumber")) is int
            and 1 <= zone["activeHeatingClimateTimeProgramNumber"] <= 3
        }
        async with self._journal_lock:
            previous = deepcopy(self.records)
            if zones or heating:
                self.records[appliance["applianceId"]] = [
                    [start.isoformat(), end.isoformat()],
                    zones,
                    heating,
                ]
            else:
                self.records.pop(appliance["applianceId"], None)
            await self._save(previous)

    async def forget_zone(self, zone_id):
        """Ruční zásah se uloží před povelem, aby ho pozdější obnova nepřepsala."""
        async with self._journal_lock:
            previous = deepcopy(self.records)
            for _, water, heating in self.records.values():
                water.pop(zone_id, None)
                heating.pop(zone_id, None)
            await self._save(previous)

    async def observe(self, appliance):
        """Po čerstvém čtení uložit změny a teprve pak odeslat jednorázovou obnovu."""
        async with self._journal_lock:
            previous = deepcopy(self.records)
            requests = self._observe(appliance)
            await self._save(previous)
            for zone, command, args in requests:
                await self._restore(zone, command, *args)

    def _observe(self, appliance):
        requests = []

        appliance_id = appliance["applianceId"]
        record = self.records.get(appliance_id)
        report = appliance.get("holidaySchedule")
        if not record or not isinstance(report, dict):
            return requests
        term, zones, heating = record
        dates = []
        for key in ("startTime", "endTime"):
            value = report.get(key)
            if value is not None and not isinstance(value, str):
                return requests
            dates.append((value or "")[:10])
        empty = all(not date or date == "0001-01-01" for date in dates)
        if not empty and dates != term:
            self.records.pop(appliance_id, None)
            return requests
        active = report.get("active")
        ended = active is False and (empty or dt_util.now().date().isoformat() >= term[1])
        for zone in appliance.get("hotWaterZones", []):
            zone_id = zone["hotWaterZoneId"]
            if zone_id not in zones:
                continue
            mode = zone.get("dhwZoneMode")
            if active is True and dates == term:
                if mode == "Off":
                    zones[zone_id] = True
                elif zones[zone_id] and mode is not None:
                    zones.pop(zone_id)
                continue
            if not ended or not zones[zone_id] or mode is None:
                continue
            zones.pop(zone_id)
            if mode == "Off":
                requests.append((zone_id, self.coordinator.api.async_set_dhw_mode_schedule, ()))
        for zone in appliance.get("climateZones", []):
            zone_id = zone["climateZoneId"]
            if zone_id not in heating:
                continue
            program, witnessed = heating[zone_id]
            mode = zone.get("zoneMode")
            if active is True and dates == term:
                if mode == "FrostProtection":
                    heating[zone_id] = [program, True]
                elif witnessed and mode is not None:
                    heating.pop(zone_id)
                continue
            if not ended or not witnessed or mode is None:
                continue
            heating.pop(zone_id)
            if (
                mode == "FrostProtection"
                and zone.get("activeHeatingClimateTimeProgramNumber") == program
            ):
                requests.append((zone_id, self.coordinator.api.async_set_schedule, (program,)))
        if not zones and not heating:
            self.records.pop(appliance_id, None)
        return requests

    async def _restore(self, zone_id, command, *args):
        """Nesmí čekat na ruční zápis, který sám čeká na tento refresh."""
        lock = self.coordinator.command_locks.setdefault(zone_id, asyncio.Lock())
        if lock.locked():
            return
        try:
            async with lock, asyncio.timeout(10):
                await command(zone_id, *args)
        except (ClientError, TimeoutError):
            self.coordinator.logger.warning("Nepodařilo se potvrdit návrat zóny po dovolené")
