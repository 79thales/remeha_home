"""Local inputs and a device-timed override; no scheduled HA commands.

The cloud API owns expiry, including while Home Assistant is offline. The
community-documented endTime parameter is experimental until verified on a
particular appliance. A later command from any client is left to the device;
HA never replays an old command or attempts to infer who changed the zone.
"""

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from homeassistant.util.file import write_utf8_file_atomic

from .const import DOMAIN
from .export_override import invalidate_local_export
from .util import serialized_command, validate_value


class TemporaryOverrideJournal:
    """Uchovává nepotvrzené povely jednotlivých zón přes restart HA."""

    def __init__(self, hass, entry_id):
        self.hass = hass
        self.path = Path(hass.config.path(".storage", f"{DOMAIN}.{entry_id}.temporary_pending"))
        self.records: dict[str, tuple[float, str]] = {}
        # Zápisy různých zón nesmějí přepsat záznam druhé zóny.
        self._lock = asyncio.Lock()

    def _read(self):
        try:
            records = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        # Poškozený záznam má zastavit inicializaci. Prázdný záznam by
        # automatizaci nepravdivě oznámil, že žádný povel nečeká.
        if not isinstance(records, dict):
            raise ValueError("Invalid temporary override journal")
        parsed = {}
        for zone_id, record in records.items():
            if (
                not isinstance(record, list)
                or len(record) != 2
                or type(record[0]) not in (int, float)
                or not isinstance(record[1], str)
                or (end := dt_util.parse_datetime(record[1])) is None
                or end.tzinfo is None
            ):
                raise ValueError("Invalid temporary override record")
            parsed[zone_id] = (record[0], record[1])
        return parsed

    async def async_load(self):
        """Před prvním čtením zařízení obnoví pouze záznam, bez povelu."""
        self.records = await self.hass.async_add_executor_job(self._read)

    async def _save(self, records):
        # Atomický zápis s fsync hlásí chybu volajícímu. Při selhání ukládání
        # služba skončí před síťovým povelem a platný záznam zůstane zachován.
        await self.hass.async_add_executor_job(
            write_utf8_file_atomic, str(self.path), json.dumps(records), True
        )
        self.records = records

    async def async_mark_pending(self, zone_id, temperature, end_time):
        """Zapíše očekávanou teplotu v °C a čas konce v UTC před odesláním."""
        async with self._lock:
            await self._save({**self.records, zone_id: (temperature, end_time)})

    async def async_observe(self, appliances, read_started: datetime):
        """Vyhodnotí skutečnou odpověď cloudu, bez jakéhokoli zápisu do topení."""
        if not self.records:
            return
        async with self._lock:
            records = self.records.copy()
            for appliance in appliances:
                for zone in appliance.get("climateZones") or []:
                    zone_id = zone.get("climateZoneId")
                    if zone_id not in records:
                        continue
                    temperature, expected_end = records[zone_id]
                    mode = zone.get("zoneMode")
                    override = zone.get("temporaryOverride")
                    end_text = override.get("endTime") if isinstance(override, dict) else None
                    try:
                        reported_end = (
                            dt_util.parse_datetime(end_text) if isinstance(end_text, str) else None
                        )
                    except ValueError:
                        reported_end = None
                    end = datetime.fromisoformat(expected_end)
                    confirmed = (
                        mode == "TemporaryOverride"
                        and zone.get("setPoint") == temperature
                        and reported_end == end
                    )
                    # Opožděný program před koncem neprokazuje zánik povelu.
                    # Po termínu smí čerstvé čtení známého režimu záznam uvolnit.
                    expired = read_started >= end and mode in (
                        "Scheduling",
                        "Manual",
                        "FrostProtection",
                    )
                    if confirmed or expired:
                        del records[zone_id]
            if records != self.records:
                await self._save(records)


@serialized_command("climate_zone_id")
async def start_override(entity, temperature, duration_minutes):
    """Send one override after a fresh read, with UTC expiry in whole minutes.

    The per-zone lock serializes callers using this helper. A failed refresh,
    unknown capability, mode, range, or duration prevents a write. Existing
    climate commands and external apps may race with this request; the device
    decides their order. Never retry an uncertain write or restore a timer.
    """
    # Tlačítko historicky očekává celé minuty. Služba přijímá časový interval
    # HA a předává timedelta, aby se např. 90 sekund nezaokrouhlilo na minuty.
    native_service = isinstance(duration_minutes, timedelta)
    duration = (
        duration_minutes
        if native_service
        else timedelta(minutes=duration_minutes)
        if type(duration_minutes) is int and 1 <= duration_minutes <= 10080
        else None
    )
    coordinator = entity.coordinator
    await coordinator.async_refresh()
    zone = coordinator.get_by_id(entity.climate_zone_id) or {}
    if (
        not coordinator.last_update_success
        or zone.get("capabilityTemporaryOverrideEndTime") is not True
    ):
        raise ServiceValidationError("Timed override capability is not confirmed")
    if zone.get("zoneMode") not in ("Scheduling", "TemporaryOverride"):
        raise ServiceValidationError("Timed override requires schedule mode")
    validate_value(temperature, zone.get("setPointMin"), zone.get("setPointMax"))
    if duration is None or not timedelta(minutes=1) <= duration <= timedelta(days=7):
        raise ServiceValidationError("Duration must be between 1 minute and 7 days")
    if native_service and entity.climate_zone_id in getattr(coordinator, "temporary_pending", {}):
        raise ServiceValidationError("The previous timed override is awaiting confirmation")
    await invalidate_local_export(entity)
    end = (dt_util.utcnow() + duration).replace(microsecond=0)
    # Signal uncertainty before the network request so observers can suspend
    # conflicting automatic writes immediately. An HTTP success is not proof
    # that the device has applied the request. Lost responses stay uncertain.
    end_text = end.isoformat().replace("+00:00", "Z")
    await coordinator.temporary_override_journal.async_mark_pending(
        entity.climate_zone_id, temperature, end_text
    )
    coordinator.async_update_listeners()
    try:
        await entity.api.async_set_temporary_override(entity.climate_zone_id, temperature, end_text)
    finally:
        await coordinator.async_request_refresh()
