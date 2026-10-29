"""Local inputs and a device-timed override; no scheduled HA commands.

The cloud API owns expiry, including while Home Assistant is offline. The
community-documented endTime parameter is experimental until verified on a
particular appliance. A later command from any client is left to the device;
HA never replays an old command or attempts to infer who changed the zone.
"""

from datetime import timedelta

from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from .export_override import invalidate_local_export
from .util import serialized_command, validate_value


@serialized_command("climate_zone_id")
async def start_override(entity, temperature, duration_minutes):
    """Send one override after a fresh read, with UTC expiry in whole minutes.

    The per-zone lock serializes callers using this helper. A failed refresh,
    unknown capability, mode, range, or duration prevents a write. Existing
    climate commands and external apps may race with this request; the device
    decides their order. Never retry an uncertain write or restore a timer.
    """
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
    if type(duration_minutes) is not int or not 1 <= duration_minutes <= 10080:
        raise ServiceValidationError("Duration must be between 1 and 10080 minutes")
    await invalidate_local_export(entity)
    end = (dt_util.utcnow() + timedelta(minutes=duration_minutes)).replace(microsecond=0)
    # Signal uncertainty before the network request so observers can suspend
    # conflicting automatic writes immediately. An HTTP success is not proof
    # that the device has applied the request. Lost responses stay uncertain.
    pending = coordinator.__dict__.setdefault("temporary_pending", {})
    end_text = end.isoformat().replace("+00:00", "Z")
    pending[entity.climate_zone_id] = (temperature, end_text)
    coordinator.async_update_listeners()
    try:
        await entity.api.async_set_temporary_override(entity.climate_zone_id, temperature, end_text)
    finally:
        await coordinator.async_request_refresh()
