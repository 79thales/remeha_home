"""Experimental export overlay for an existing device-timed user override.

Cloud state is read before each write, but the API has no compare-and-swap:
an external command between that read and the POST can still be overwritten.
The device's handling of repeated endTime commands has not been verified.
Never start or restore an overlay automatically during setup or restart.
"""

import asyncio
from copy import deepcopy
from math import isfinite

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .util import serialized_command, validate_value


class ExportOverrideManager:
    """Persist user baseline and command phase per zone before any cloud write.

    A journal in HA storage contains the user's setpoint U in °C, its UTC end
    T, the export setpoint and a phase. A lock protects the shared journal from
    simultaneous saves across zones. Uncertain writes stay pending indefinitely
    and are never retried automatically. Storage failure prevents cloud writes.
    """

    def __init__(self, hass, entry_id):
        self.store = Store(hass, 1, f"{DOMAIN}.export_override.{entry_id}")
        self.records = {}
        self.lock = asyncio.Lock()

    async def async_load(self):
        data = await self.store.async_load()
        if data is not None:
            if not isinstance(data, dict) or any(not isinstance(v, dict) for v in data.values()):
                raise ServiceValidationError("Invalid export override journal")
            self.records = data

    async def save(self, zone_id, record):
        """Durably save a snapshot; do not change memory when saving fails."""
        async with self.lock:
            data = deepcopy(self.records)
            if record is None:
                data.pop(zone_id, None)
            else:
                data[zone_id] = record
            await self.store.async_save(data)
            self.records = data


def _end_time(zone):
    """Return a timezone-aware UTC-compatible future timestamp, or fail closed."""
    override = zone.get("temporaryOverride")
    raw = override.get("endTime") if isinstance(override, dict) else None
    end = dt_util.parse_datetime(raw) if isinstance(raw, str) else None
    if end is None or end.utcoffset() is None or end <= dt_util.utcnow():
        raise ServiceValidationError("No confirmed future override end time")
    return end


def _matches(zone, temperature, end):
    """Match the observable fields only; they cannot identify a cloud client."""
    try:
        return (zone.get("zoneMode") == "TemporaryOverride"
                and zone.get("setPoint") == temperature and _end_time(zone) == end)
    except ServiceValidationError:
        return False


async def _fresh(entity):
    await entity.coordinator.async_refresh()
    if not entity.coordinator.last_update_success:
        raise ServiceValidationError("Cloud state is unavailable")
    zone = entity.coordinator.get_by_id(entity.climate_zone_id) or {}
    if zone.get("capabilityTemporaryOverrideEndTime") is not True:
        raise ServiceValidationError("Timed override capability is not confirmed")
    return zone


def _manager(entity):
    manager = getattr(entity.coordinator, "export_override_manager", None)
    if not isinstance(manager, ExportOverrideManager):
        raise ServiceValidationError("Export override journal is unavailable")
    return manager


def _validate_guard(entity, guard_entity_id, guard_valid_until, measurement_entity_id, measurement_max_age_seconds):
    """Require an active binary guard and a future, timezone-aware deadline.

    The caller owns the guard's threshold and window logic. The
    guard is evaluated synchronously so no deliberate yield occurs between the
    final check and submitting the cloud coroutine. It cannot make a network
    write atomic with respect to subsequent guard or external-client changes.
    """
    if not isinstance(guard_entity_id, str) or not guard_entity_id.startswith("binary_sensor."):
        raise ServiceValidationError("A binary sensor guard is required")
    state = entity.hass.states.get(guard_entity_id)
    if state is None or state.state != "on":
        raise ServiceValidationError("Export guard is not on")
    end = dt_util.parse_datetime(guard_valid_until) if isinstance(guard_valid_until, str) else None
    if end is None or end.utcoffset() is None or end <= dt_util.utcnow():
        raise ServiceValidationError("Export guard deadline is invalid or expired")
    if (not isinstance(measurement_entity_id, str)
            or not measurement_entity_id.startswith("sensor.")
            or type(measurement_max_age_seconds) not in (int, float)
            or not isfinite(measurement_max_age_seconds)
            or measurement_max_age_seconds <= 0):
        raise ServiceValidationError("A measurement sensor and positive maximum age are required")
    measurement = entity.hass.states.get(measurement_entity_id)
    try:
        value = float(measurement.state)
        reported = measurement.last_reported
        age = (dt_util.utcnow() - reported).total_seconds()
    except (AttributeError, TypeError, ValueError):
        raise ServiceValidationError("Measurement or reporting timestamp is unavailable") from None
    if not isfinite(value) or not 0 <= age <= measurement_max_age_seconds:
        raise ServiceValidationError("Measurement is invalid or stale")


async def invalidate_local_export(entity):
    """Give an explicit local climate command priority before it is sent.

    Retaining the record for review prevents an old baseline being restored
    after that command, even if it selects the same observable temperature.
    """
    manager = getattr(entity.coordinator, "export_override_manager", None)
    record = manager.records.get(entity.climate_zone_id) if isinstance(manager, ExportOverrideManager) else None
    if record and record.get("phase") != "intervened":
        await manager.save(entity.climate_zone_id, {**record, "phase": "intervened"})
        entity.async_write_ha_state()


@serialized_command("climate_zone_id")
async def reconcile_export(entity):
    """Confirm a pending command by reading only; never send or retry a POST."""
    manager = _manager(entity)
    zone_id = entity.climate_zone_id
    record = manager.records.get(zone_id)
    if not record or record.get("phase") not in ("start_pending", "restore_pending"):
        raise ServiceValidationError("No pending export command to check")
    end = dt_util.parse_datetime(record.get("end_time", ""))
    if end is None or end.utcoffset() is None or end <= dt_util.utcnow():
        raise ServiceValidationError("Original override end has passed or is unknown")
    zone = await _fresh(entity)
    temperature = record.get("export_temperature") if record["phase"] == "start_pending" else record.get("user_temperature")
    if not _matches(zone, temperature, end):
        raise ServiceValidationError("Cloud data do not confirm the pending command")
    await manager.save(zone_id, {**record, "phase": "active"} if record["phase"] == "start_pending" else None)
    entity.async_write_ha_state()


@serialized_command("climate_zone_id")
async def discard_export_record(entity):
    """Forget a journal record explicitly, without reading or controlling heat.

    This permanently gives up automatic restoration of the saved user value;
    it is intended for manual recovery after a cloud mismatch or expiry.
    """
    manager = _manager(entity)
    if entity.climate_zone_id not in manager.records:
        raise ServiceValidationError("No export override record to discard")
    await manager.save(entity.climate_zone_id, None)
    entity.async_write_ha_state()


@serialized_command("climate_zone_id")
async def start_export(
    entity, temperature, guard_entity_id, guard_valid_until,
    measurement_entity_id, measurement_max_age_seconds,
):
    """Raise an existing override to max(user, export) without changing T.

    This is explicit, not a response to an export sensor. A pre-existing record,
    unconfirmed command, unknown limits or stale cloud state blocks the write.
    If the requested target is no higher than U, no journal or command is made.
    """
    manager = _manager(entity)
    zone_id = entity.climate_zone_id
    _validate_guard(entity, guard_entity_id, guard_valid_until, measurement_entity_id, measurement_max_age_seconds)
    if zone_id in manager.records or zone_id in getattr(entity.coordinator, "temporary_pending", {}):
        raise ServiceValidationError("Previous override command requires review")
    zone = await _fresh(entity)
    if zone.get("zoneMode") != "TemporaryOverride":
        raise ServiceValidationError("A confirmed timed user override is required")
    user = zone.get("setPoint")
    validate_value(user, zone.get("setPointMin"), zone.get("setPointMax"))
    validate_value(temperature, zone.get("setPointMin"), zone.get("setPointMax"))
    end = _end_time(zone)
    if temperature <= user:
        return
    end_text = end.isoformat().replace("+00:00", "Z")
    record = {"user_temperature": user, "end_time": end_text,
              "export_temperature": temperature, "phase": "start_pending"}
    await manager.save(zone_id, record)
    entity.async_write_ha_state()
    try:
        _validate_guard(entity, guard_entity_id, guard_valid_until, measurement_entity_id, measurement_max_age_seconds)
        if end <= dt_util.utcnow():
            raise ServiceValidationError("Original override expired before export command")
    except ServiceValidationError:
        # No cloud request was made. If deleting the record fails, leave the
        # pending journal in place and propagate the storage failure instead.
        await manager.save(zone_id, None)
        entity.async_write_ha_state()
        raise
    await entity.api.async_set_temporary_override(zone_id, temperature, end_text)
    zone = await _fresh(entity)
    if not _matches(zone, temperature, end):
        raise ServiceValidationError("Export override has not been confirmed by cloud")
    await manager.save(zone_id, {**record, "phase": "active"})
    entity.async_write_ha_state()


@serialized_command("climate_zone_id")
async def restore_export(entity):
    """Restore U with unchanged T only while the cloud still matches our overlay.

    This must be explicitly called; neither restart nor export loss calls it.
    A mismatch, expired T, or uncertain prior request fails closed. A persisted
    pending phase is never retried. The API lacks CAS, so external interventions
    can race the POST even though all integration commands share a local lock.
    """
    manager = _manager(entity)
    zone_id = entity.climate_zone_id
    record = manager.records.get(zone_id)
    if not record or record.get("phase") != "active":
        raise ServiceValidationError("No confirmed export override to restore")
    end = dt_util.parse_datetime(record.get("end_time", ""))
    if end is None or end.utcoffset() is None or end <= dt_util.utcnow():
        raise ServiceValidationError("Original override has expired or its end is unknown")
    zone = await _fresh(entity)
    if not _matches(zone, record.get("export_temperature"), end):
        raise ServiceValidationError("Cloud state changed; restoration is blocked")
    user = record.get("user_temperature")
    validate_value(user, zone.get("setPointMin"), zone.get("setPointMax"))
    await manager.save(zone_id, {**record, "phase": "restore_pending"})
    entity.async_write_ha_state()
    if end <= dt_util.utcnow():
        raise ServiceValidationError("Original override expired before restoration")
    await entity.api.async_set_temporary_override(zone_id, user, record["end_time"])
    zone = await _fresh(entity)
    if not _matches(zone, user, end):
        raise ServiceValidationError("User override restoration has not been confirmed")
    await manager.save(zone_id, None)
    entity.async_write_ha_state()
