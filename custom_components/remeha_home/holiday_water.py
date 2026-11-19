"""Obnova časového programu TUV po dovolené potvrzené zařízením."""

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
        if zones:
            self.records[appliance["applianceId"]] = ((start.isoformat(), end.isoformat()), zones)

    def forget_zone(self, zone_id):
        """Výslovný zápis TUV z HA má přednost i při nastavení stejného režimu."""
        for _, zones in self.records.values():
            zones.pop(zone_id, None)

    async def observe(self, appliance):
        """Použít čerstvý stav z cloudu a obnovit jen prokazatelně vypnuté zóny."""
        appliance_id = appliance["applianceId"]
        record = self.records.get(appliance_id)
        report = appliance.get("holidaySchedule")
        if not record or not isinstance(report, dict):
            return
        term, zones = record
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
            # Volající zápisu TUV může právě čekat na tento refresh. Konflikt
            # proto ponecháme ručnímu ovladači a nikdy tu nečekáme na jeho zámek.
            zones.pop(zone_id)
            lock = self.coordinator.command_locks.setdefault(zone_id, asyncio.Lock())
            if lock.locked():
                continue
            if mode == "Off":
                try:
                    async with lock, asyncio.timeout(10):
                        await self.coordinator.api.async_set_dhw_mode_schedule(zone_id)
                except (ClientError, TimeoutError):
                    # Po nejednoznačném výsledku povel automaticky neopakujeme.
                    self.coordinator.logger.warning("Nepodařilo se potvrdit návrat TUV po dovolené")
        if not zones:
            self.records.pop(appliance_id, None)
