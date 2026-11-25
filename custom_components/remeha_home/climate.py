"""Platform for Remeha Home climate integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_HALVES, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import RemehaHomeAPI
from .const import DOMAIN
from .coordinator import RemehaHomeUpdateCoordinator
from .export_override import (
    discard_export_record,
    invalidate_local_export,
    reconcile_export,
    restore_export,
    start_export,
)
from .temporary import start_override
from .util import serialized_command

_LOGGER = logging.getLogger(__name__)


def manual_hvac_mode(can_cool: bool) -> HVACMode:
    """Stejně jako Hugo vystavit jediný ruční režim podle možností zóny."""
    return HVACMode.HEAT_COOL if can_cool else HVACMode.HEAT


def get_remeha_mode_to_hvac_mode(can_cool: bool) -> dict:
    """Převést režim zařízení na nativní stav Home Assistantu."""
    return {
        "Scheduling": HVACMode.AUTO,
        "TemporaryOverride": HVACMode.AUTO,
        "Manual": manual_hvac_mode(can_cool),
        "FrostProtection": HVACMode.OFF,
    }


def get_hvac_mode_to_remeha_mode(can_cool: bool) -> dict:
    """Přiřadit nabízené režimy k povelům zařízení."""
    return {
        HVACMode.AUTO: "Scheduling",
        manual_hvac_mode(can_cool): "Manual",
        HVACMode.OFF: "FrostProtection",
    }


REMEHA_STATUS_TO_HVAC_ACTION = {
    "ProducingHeat": HVACAction.HEATING,
    "RequestingHeat": HVACAction.HEATING,
    "ProducingCold": HVACAction.COOLING,
    "RequestingCold": HVACAction.COOLING,
    "Idle": HVACAction.IDLE,
}

PRESET_INDEX_TO_PRESET_MODE = {
    1: "clock_program_1",
    2: "clock_program_2",
    3: "clock_program_3",
}

PRESET_MODE_TO_PRESET_INDEX = {
    "clock_program_1": 1,
    "clock_program_2": 2,
    "clock_program_3": 3,
}


def map_remeha_status_to_hvac_action(status: str | None) -> HVACAction | None:
    """Map the raw comfort demand status to a Home Assistant HVAC action."""
    if status is None:
        return None

    if action := REMEHA_STATUS_TO_HVAC_ACTION.get(status):
        return action

    if "Heat" in status:
        return HVACAction.HEATING

    _LOGGER.debug("Unknown comfort demand status received: %s", status)
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Remeha Home climate entity from a config entry."""
    api = hass.data[DOMAIN][entry.entry_id]["api"]
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = []
    for appliance in coordinator.data["appliances"]:
        for climate_zone in appliance["climateZones"]:
            climate_zone_id = climate_zone["climateZoneId"]
            entities.append(RemehaHomeClimateEntity(api, coordinator, climate_zone_id))

    async_add_entities(entities)
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "set_temporary_override",
        {
            vol.Required("temperature"): vol.Coerce(float),
            vol.Required("duration"): vol.All(
                cv.time_period, vol.Range(min=timedelta(minutes=1), max=timedelta(days=7))
            ),
        },
        "async_set_temporary_override",
    )
    platform.async_register_entity_service(
        "start_export_override",
        {
            vol.Required("temperature"): vol.Coerce(float),
            vol.Required("guard_entity_id"): str,
            vol.Required("guard_valid_until"): str,
            vol.Required("measurement_entity_id"): str,
            vol.Required("measurement_max_age_seconds"): vol.Coerce(float),
        },
        "async_start_export_override",
    )
    platform.async_register_entity_service(
        "restore_export_override", {}, "async_restore_export_override"
    )
    platform.async_register_entity_service(
        "reconcile_export_override", {}, "async_reconcile_export_override"
    )
    platform.async_register_entity_service(
        "discard_export_override_record", {}, "async_discard_export_override_record"
    )


class RemehaHomeClimateEntity(CoordinatorEntity, ClimateEntity):
    """Climate entity representing a Remeha Home climate zone."""

    _enable_turn_on_off_backwards_compatibility = False
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )
    # Adapted from Hugo a2bc341: HA 2026.11 uses native units; 2026.10
    # retains the legacy accessors. Values stay in Celsius in both versions.
    if hasattr(ClimateEntity, "native_temperature_unit"):
        _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    else:
        _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_HALVES
    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "remeha_home"

    def __init__(
        self,
        api: RemehaHomeAPI,
        coordinator: RemehaHomeUpdateCoordinator,
        climate_zone_id: str,
    ) -> None:
        """Create a Remeha Home climate entity."""
        super().__init__(coordinator)
        self.api = api
        self.coordinator: RemehaHomeUpdateCoordinator = coordinator
        self.climate_zone_id = climate_zone_id
        self._has_cooling_activity: bool | None = None

        self._attr_unique_id = "_".join([DOMAIN, self.climate_zone_id])

    @property
    def _data(self) -> dict:
        """Return the climate zone information from the coordinator."""
        return self.coordinator.get_by_id(self.climate_zone_id)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this device."""
        return self.coordinator.get_device_info(self.climate_zone_id)

    def _get_appliance_data(self) -> dict | None:
        """Get the appliance data for this climate zone."""
        for appliance in self.coordinator.data.get("appliances", []):
            for climate_zone in appliance.get("climateZones", []):
                if climate_zone.get("climateZoneId") == self.climate_zone_id:
                    return appliance
        return None

    def _can_cool(self) -> bool:
        """Použít Hugovo pořadí dat o chlazení pro příslušnou zónu.

        Přednost má příznak zóny před příznakem zařízení. Aktivita Cooling
        chlazení potvrzuje i při záporném příznaku. Při obnově aktivit použij
        poslední výsledek a před prvním úspěšným načtením Hugův předpoklad
        pro tepelné čerpadlo. Tato volba sama žádný povel neposílá.
        """
        appliance = self._get_appliance_data() or {}
        capability = self._data.get("capabilityCooling")
        if capability is None:
            capability = appliance.get("capabilityCooling")
        if capability:
            return True
        activities = self.coordinator.get_activities(self.climate_zone_id)
        if activities is not None:
            self._has_cooling_activity = any(
                activity.get("type") == "Cooling" for activity in activities
            )
        if self._has_cooling_activity is not None:
            return self._has_cooling_activity
        return appliance.get("applianceType") == "HeatPump"

    def _room_temperature(self) -> float | None:
        """Return the current temperature."""
        return self._data["roomTemperature"]

    def _setpoint(self) -> float | None:
        """Return the target temperature."""
        if self.hvac_mode == HVACMode.OFF:
            return None
        return self._data["setPoint"]

    # Select accessors by HA capability rather than a guessed release number.
    if hasattr(ClimateEntity, "native_temperature_unit"):
        native_current_temperature = property(_room_temperature)
        native_target_temperature = property(_setpoint)
    else:
        current_temperature = property(_room_temperature)
        target_temperature = property(_setpoint)

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        return self._data["setPointMin"]

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        return self._data["setPointMax"]

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return hvac target hvac state."""
        mode = self._data["zoneMode"]
        return get_remeha_mode_to_hvac_mode(self._can_cool()).get(mode)

    @property
    def extra_state_attributes(self) -> dict:
        """Zveřejnit společné Hugovo rozhraní a atributy starších klientů.

        Cloudový režim a konec v místním ISO formátu jsou nezávislé údaje.
        Po přepnutí může krátce zůstat starý konec. Vlastnictví override
        ani potvrzení místního povelu z nich nelze samostatně odvodit.
        """
        data = self._data or {}
        mode = data.get("zoneMode")
        override = data.get("temporaryOverride")
        raw_end = override.get("endTime") if isinstance(override, dict) else None
        try:
            end = dt_util.parse_datetime(raw_end) if isinstance(raw_end, str) else None
        except ValueError:
            end = None
        if end is not None and end.tzinfo is None:
            end = end.replace(tzinfo=dt_util.UTC)
        manager = getattr(self.coordinator, "export_override_manager", None)
        record = manager.records.get(self.climate_zone_id) if manager else None
        return {
            # Režim z API pod Hugovým názvem rozliší Scheduling a
            # TemporaryOverride i tehdy, když oba v Home Assistantu hlásí auto.
            "zone_mode": mode,
            # Stejně jako Hugo zveřejníme platný čas i vedle Scheduling.
            # Společný skript jej použije jen v nahlášeném TemporaryOverride.
            # Starší atribut níže si ponechává původní text API.
            "override_end_time": dt_util.as_local(end).isoformat()
            if end is not None and end.year > 1
            else None,
            "temporary_override_active": (mode == "TemporaryOverride")
            if mode in ("TemporaryOverride", "Scheduling", "Manual", "FrostProtection")
            else None,
            "temporary_override_end_time": override.get("endTime")
            if isinstance(override, dict) and mode == "TemporaryOverride"
            else None,
            "temporary_override_end_time_supported": data.get("capabilityTemporaryOverrideEndTime")
            if isinstance(data.get("capabilityTemporaryOverrideEndTime"), bool)
            else None,
            "temporary_override_command_pending": self.climate_zone_id
            in getattr(self.coordinator, "temporary_pending", set()),
            "export_override_phase": record.get("phase") if record else None,
            "export_override_user_temperature": record.get("user_temperature") if record else None,
            "export_override_end_time": record.get("end_time") if record else None,
        }

    async def async_set_temporary_override(self, temperature: float, duration: timedelta) -> None:
        """Použít stejný zámek, čerstvé čtení a potvrzování jako časované tlačítko."""
        await start_override(self, temperature, duration)

    async def async_start_export_override(
        self,
        temperature: float,
        guard_entity_id: str,
        guard_valid_until: str,
        measurement_entity_id: str,
        measurement_max_age_seconds: float,
    ) -> None:
        """Explicitly start the experimental export overlay for this zone."""
        await start_export(
            self,
            temperature,
            guard_entity_id,
            guard_valid_until,
            measurement_entity_id,
            measurement_max_age_seconds,
        )

    async def async_restore_export_override(self) -> None:
        """Explicitly attempt restoration; never called automatically."""
        await restore_export(self)

    async def async_reconcile_export_override(self) -> None:
        """Check a pending request against cloud data without changing heating."""
        await reconcile_export(self)

    async def async_discard_export_override_record(self) -> None:
        """Forget the saved baseline without sending a heating command."""
        await discard_export_record(self)

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return the list of available operation modes."""
        return [HVACMode.OFF, HVACMode.AUTO, manual_hvac_mode(self._can_cool())]

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return hvac action."""
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF

        return map_remeha_status_to_hvac_action(self._data.get("activeComfortDemand"))

    @property
    def preset_mode(self) -> str | None:
        """Return the preset mode."""
        if self.hvac_mode == HVACMode.OFF:
            return "anti_frost"
        if self.hvac_mode in (HVACMode.HEAT, HVACMode.HEAT_COOL):
            return "manual"
        return PRESET_INDEX_TO_PRESET_MODE[self._data["activeHeatingClimateTimeProgramNumber"]]

    @property
    def preset_modes(self) -> list[str]:
        """Return the list of available presets."""
        return [*PRESET_INDEX_TO_PRESET_MODE.values(), "manual", "anti_frost"]

    @serialized_command("climate_zone_id")
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature, optionally together with a new operation mode."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if self.climate_zone_id in getattr(self.coordinator, "temporary_pending", set()):
            raise ServiceValidationError("A timed override is awaiting cloud confirmation")
        await invalidate_local_export(self)
        requested_hvac_mode = kwargs.get(ATTR_HVAC_MODE)

        if temperature is None:
            if requested_hvac_mode is not None:
                # This method already owns the same non-reentrant zone lock.
                await self.async_set_hvac_mode.__wrapped__(self, requested_hvac_mode)
            return

        if requested_hvac_mode is not None and requested_hvac_mode not in self.hvac_modes:
            raise ServiceValidationError("Unsupported HVAC mode")
        current_hvac_mode = self.hvac_mode
        hvac_mode = requested_hvac_mode or current_hvac_mode
        _LOGGER.debug("Setting temperature to %f in mode %s", temperature, hvac_mode)

        if hvac_mode == HVACMode.OFF:
            # The setpoint can't be stored while the zone is in frost protection,
            # so fail loudly instead of silently dropping it
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="set_temperature_while_off",
            )

        async with self.coordinator.holiday_water_restore.async_zone_command(self.climate_zone_id):
            if hvac_mode == HVACMode.AUTO:
                if current_hvac_mode != HVACMode.AUTO:
                    await self.api.async_set_schedule(
                        self.climate_zone_id,
                        self._data["activeHeatingClimateTimeProgramNumber"],
                    )
                await self.api.async_set_temporary_override(self.climate_zone_id, temperature)
            elif hvac_mode in (HVACMode.HEAT, HVACMode.HEAT_COOL):
                # Switches the zone to manual mode with the new setpoint in one request
                await self.api.async_set_manual(self.climate_zone_id, temperature)
            else:
                raise NotImplementedError()

        await self.coordinator.async_request_refresh()

    @serialized_command("climate_zone_id")
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new operation mode."""
        mapped_mode = get_hvac_mode_to_remeha_mode(self._can_cool()).get(hvac_mode)
        if mapped_mode is None:
            raise ServiceValidationError("Unsupported HVAC mode")
        await invalidate_local_export(self)
        if hvac_mode not in self.hvac_modes:
            raise ServiceValidationError("Unsupported HVAC mode")
        _LOGGER.debug("Setting operation mode to %s", hvac_mode)

        async with self.coordinator.holiday_water_restore.async_zone_command(
            self.climate_zone_id, preserve_during_holiday=hvac_mode != HVACMode.OFF
        ):
            # Temporarily override the coordinator state until the next poll
            zone_data = self._data
            previous_zone_mode = zone_data["zoneMode"]
            zone_data["zoneMode"] = mapped_mode
            self.async_write_ha_state()

            try:
                if hvac_mode == HVACMode.AUTO:
                    await self.api.async_set_schedule(
                        self.climate_zone_id,
                        zone_data["activeHeatingClimateTimeProgramNumber"],
                    )
                elif hvac_mode in (HVACMode.HEAT, HVACMode.HEAT_COOL):
                    await self.api.async_set_manual(self.climate_zone_id, zone_data["setPoint"])
                elif hvac_mode == HVACMode.OFF:
                    await self.api.async_set_off(self.climate_zone_id)
                else:
                    raise NotImplementedError()
            except Exception:
                # Revert the temporary override, the zone did not change
                zone_data["zoneMode"] = previous_zone_mode
                self.async_write_ha_state()
                raise

        await self.coordinator.async_request_refresh()

    @serialized_command("climate_zone_id")
    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        # Stejně jako Hugo obsloužit presety ručního režimu a protimrazové
        # ochrany stejným povelem jako režimy termostatu. Zámek už je držen.
        if preset_mode == "manual":
            await self.async_set_hvac_mode.__wrapped__(self, manual_hvac_mode(self._can_cool()))
            return
        if preset_mode == "anti_frost":
            await self.async_set_hvac_mode.__wrapped__(self, HVACMode.OFF)
            return
        await invalidate_local_export(self)
        _LOGGER.debug("Setting preset mode to %s", preset_mode)

        if preset_mode not in PRESET_MODE_TO_PRESET_INDEX:
            _LOGGER.error("Trying to set unknown preset mode %s", preset_mode)
            return

        async with self.coordinator.holiday_water_restore.async_zone_command(self.climate_zone_id):
            target_preset = PRESET_MODE_TO_PRESET_INDEX[preset_mode]
            previous_hvac_mode = self.hvac_mode

            zone_data = self._data
            previous_zone_mode = zone_data["zoneMode"]
            previous_preset = zone_data["activeHeatingClimateTimeProgramNumber"]
            zone_data["zoneMode"] = "Scheduling"
            zone_data["activeHeatingClimateTimeProgramNumber"] = target_preset
            self.async_write_ha_state()

            try:
                # Switch the selected heating time program
                await self.api.async_activate_heating_time_program(
                    self.climate_zone_id, target_preset
                )
                # Automatically make sure the mode is set to schedule
                if previous_hvac_mode != HVACMode.AUTO:
                    await self.api.async_set_schedule(self.climate_zone_id, target_preset)
            except Exception:
                # Revert the temporary override. If only the schedule request failed,
                # the next poll picks up the program that was already activated.
                zone_data["zoneMode"] = previous_zone_mode
                zone_data["activeHeatingClimateTimeProgramNumber"] = previous_preset
                self.async_write_ha_state()
                raise

        await self.coordinator.async_request_refresh()
