"""Obnova časového programu vytápění a TUV po dovolené potvrzené zařízením."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy

from aiohttp import ClientError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util


class HolidayWaterRestore:
    """Sledovat pouze termíny založené touto instancí integrace.

    Dvojice dat sváže snímek s termínem. Zápis obnovy vyžaduje potvrzenou
    změnu zóny a nové čtení po skončení dovolené. Stejně jako v Hugově
    PR #52 se úspěšná změna režimu posuzuje podle toho, zda dovolená ještě běží.
    """

    def __init__(self, coordinator, entry_id):
        self.coordinator = coordinator
        self.store = Store(coordinator.hass, 1, f"remeha_home.{entry_id}.holiday_recovery")
        self.records = {}
        self._journal_lock = asyncio.Lock()
        self._candidates = {}
        self._restoring = set()

    async def async_load(self):
        """Načíst snímek před prvním dotazem na cloud. Načtení samo nic nezapisuje."""
        data = await self.store.async_load()
        if isinstance(data, dict):
            # Formát v0.5.11 obsahoval snímek a potvrzení zón bez volby obnovy.
            self.records = {
                appliance_id: [*record, {}] if len(record) == 3 else record
                for appliance_id, record in data.items()
            }

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
                    {},
                ]
            else:
                self.records.pop(appliance["applianceId"], None)
            await self._save(previous)
            self._candidates.pop(appliance["applianceId"], None)

    @asynccontextmanager
    async def async_zone_command(self, zone_id, *, preserve_during_holiday=True):
        """Po úspěšném povelu uložit volbu obnovy pod zámkem téže zóny.

        Volající už vlastní zámek zóny. OFF, Eco a zrušení boostu za dovolené
        umožní obnovu, po dovolené má každý výslovný povel přednost. Chybný
        povel nechá dřívější rozhodnutí v platnosti. Snímek uložený před
        dovolenou zůstává použitelný i po opětovném povolení obnovy.
        """
        async with self._journal_lock:
            appliance_id, record = next(
                (
                    (key, value)
                    for key, value in self.records.items()
                    if zone_id in value[1] or zone_id in value[2]
                ),
                (None, None),
            )
            appliance = (self.coordinator.get_by_id(appliance_id) or {}) if record else {}
            report = appliance.get("holidaySchedule")
            active = isinstance(report, dict) and report.get("active") is True
        yield
        if record is None:
            return
        async with self._journal_lock:
            if self.records.get(appliance_id) is not record:
                return
            previous = deepcopy(self.records)
            record[3][zone_id] = preserve_during_holiday or not active
            await self._save(previous)

    async def observe(self, appliance):
        """Uložit pozorování a naplánovat obnovu po skončení dovolené.

        Čtení koordinátoru může vyvolat povel, který už vlastní zámek zóny.
        Obnova proto získá tento zámek v úloze na pozadí. Použije poslední
        načtený stav a po ručním povelu znovu zkontroluje uloženou volbu.
        """
        appliance_id = appliance["applianceId"]
        async with self._journal_lock:
            if appliance_id not in self.records:
                return
            previous = deepcopy(self.records)
            candidates = self._observe(appliance)
            await self._save(previous)
            if not candidates:
                self._candidates.pop(appliance_id, None)
                return
            self._candidates[appliance_id] = candidates
            for zone_id in candidates:
                key = (appliance_id, zone_id)
                if key in self._restoring:
                    continue
                self._restoring.add(key)
                self.coordinator.config_entry.async_create_background_task(
                    self.coordinator.hass,
                    self._restore(appliance_id, zone_id),
                    "remeha_home holiday recovery",
                )

    def _observe(self, appliance):
        candidates = {}
        appliance_id = appliance["applianceId"]
        term, water, heating, choices = self.records[appliance_id]
        phase = _phase(term, appliance.get("holidaySchedule"))
        if phase == "different":
            self.records.pop(appliance_id, None)
            return candidates
        api = self.coordinator.api
        for zone in appliance.get("hotWaterZones", []):
            zone_id = zone["hotWaterZoneId"]
            if zone_id not in water:
                continue
            mode = zone.get("dhwZoneMode")
            if phase == "active":
                if mode == "Off":
                    water[zone_id] = True
                elif water[zone_id] and mode is not None:
                    self._respect_external_change(zone_id, choices)
            elif phase == "ended" and water[zone_id] and mode is not None:
                candidates[zone_id] = (
                    (api.async_set_dhw_mode_schedule, ()) if mode == "Off" else None
                )
        for zone in appliance.get("climateZones", []):
            zone_id = zone["climateZoneId"]
            if zone_id not in heating:
                continue
            program, witnessed = heating[zone_id]
            mode = zone.get("zoneMode")
            if phase == "active":
                if mode == "FrostProtection":
                    heating[zone_id] = [program, True]
                elif witnessed and mode is not None:
                    self._respect_external_change(zone_id, choices)
            elif phase == "ended" and witnessed and mode is not None:
                same_program = zone.get("activeHeatingClimateTimeProgramNumber") == program
                candidates[zone_id] = (
                    (api.async_set_schedule, (program,))
                    if mode == "FrostProtection" and same_program
                    else None
                )
        return candidates

    def _respect_external_change(self, zone_id, choices):
        """Mimo vlastní probíhající povel ponechat změnu z jiného klienta v platnosti."""
        lock = self.coordinator.command_locks.get(zone_id)
        if lock is None or not lock.locked():
            choices.setdefault(zone_id, True)

    async def _restore(self, appliance_id, zone_id):
        """Zpracovat poslední potvrzený stav jednou, po uvolnění zámku zóny."""
        try:
            lock = self.coordinator.command_locks.setdefault(zone_id, asyncio.Lock())
            async with lock:
                async with self._journal_lock:
                    candidates = self._candidates.get(appliance_id, {})
                    if zone_id not in candidates:
                        return
                    _, water, heating, choices = self.records[appliance_id]
                    command = None if choices.get(zone_id) else candidates[zone_id]
                    # Po timeoutu může zařízení ještě převzít časovaný override.
                    # Jeho čerstvé potvrzení nebo expiraci vyhodnotí koordinátor.
                    if command is not None and zone_id in self.coordinator.temporary_pending:
                        return
                    previous = deepcopy(self.records)
                    choices.pop(zone_id, None)
                    water.pop(zone_id, None)
                    heating.pop(zone_id, None)
                    if not water and not heating:
                        self.records.pop(appliance_id)
                    await self._save(previous)
                    candidates.pop(zone_id)
                    if not candidates:
                        self._candidates.pop(appliance_id, None)
                if command is not None:
                    write, args = command
                    async with asyncio.timeout(10):
                        await write(zone_id, *args)
        except (ClientError, TimeoutError):
            self.coordinator.logger.warning("Nepodařilo se potvrdit návrat zóny po dovolené")
        finally:
            self._restoring.discard((appliance_id, zone_id))


def _phase(term, report):
    """Rozlišit potvrzený začátek a konec uloženého termínu v hlášení zařízení."""
    if not isinstance(report, dict):
        return None
    dates = []
    for key in ("startTime", "endTime"):
        value = report.get(key)
        if value is not None and not isinstance(value, str):
            return None
        dates.append((value or "")[:10])
    empty = all(not date or date == "0001-01-01" for date in dates)
    if not empty and dates != term:
        return "different"
    if report.get("active") is True and dates == term:
        return "active"
    if report.get("active") is False and (empty or dt_util.now().date().isoformat() >= term[1]):
        return "ended"
    return None
