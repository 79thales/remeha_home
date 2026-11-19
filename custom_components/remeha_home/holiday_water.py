"""Obnova časového programu vytápění a TUV po dovolené potvrzené zařízením."""

import asyncio

from aiohttp import ClientError
from homeassistant.util import dt as dt_util


class HolidayWaterRestore:
    """Sledovat pouze termíny založené touto instancí integrace.

    Platí pro původní Scheduling. Záznam je v paměti a při restartu zaniká,
    aby se neodeslal povel založený na stavu před delším výpadkem.
    """

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.records = {}

    def track(self, appliance, start, end):
        zones = {
            zone["hotWaterZoneId"]: False
            for zone in appliance.get("hotWaterZones", [])
            if zone.get("dhwZoneMode") == "Scheduling"
        }
        heating = {
            zone["climateZoneId"]: (zone["activeHeatingClimateTimeProgramNumber"], False)
            for zone in appliance.get("climateZones", [])
            if zone.get("zoneMode") == "Scheduling"
            and type(zone.get("activeHeatingClimateTimeProgramNumber")) is int
            and 1 <= zone["activeHeatingClimateTimeProgramNumber"] <= 3
        }
        if zones or heating:
            self.records[appliance["applianceId"]] = (
                (start.isoformat(), end.isoformat()),
                zones,
                heating,
            )

    def forget_zone(self, zone_id):
        """Výslovný zápis TUV z HA má přednost i při nastavení stejného režimu."""
        for _, water, heating in self.records.values():
            water.pop(zone_id, None)
            heating.pop(zone_id, None)

    async def observe(self, appliance):
        """Použít čerstvý stav z cloudu a obnovit jen prokazatelně vypnuté zóny."""
        appliance_id = appliance["applianceId"]
        record = self.records.get(appliance_id)
        report = appliance.get("holidaySchedule")
        if not record or not isinstance(report, dict):
            return
        term, zones, heating = record
        dates = tuple((report.get(key) or "")[:10] for key in ("startTime", "endTime"))
        empty = all(not date or date == "0001-01-01" for date in dates)
        if not empty and dates != term:
            self.records.pop(appliance_id, None)
            return
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
                await self._restore(zone_id, self.coordinator.api.async_set_dhw_mode_schedule)
        for zone in appliance.get("climateZones", []):
            zone_id = zone["climateZoneId"]
            if zone_id not in heating:
                continue
            program, witnessed = heating[zone_id]
            mode = zone.get("zoneMode")
            if active is True and dates == term:
                if mode == "FrostProtection":
                    heating[zone_id] = program, True
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
                await self._restore(zone_id, self.coordinator.api.async_set_schedule, program)
        if not zones and not heating:
            self.records.pop(appliance_id, None)

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
