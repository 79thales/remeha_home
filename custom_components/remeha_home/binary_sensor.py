"""Platform for binary sensor integration."""

from __future__ import annotations

import logging
from collections.abc import Callable

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    APPLIANCE_BINARY_SENSOR_TYPES,
    CLIMATE_ZONE_BINARY_SENSOR_TYPES,
    DOMAIN,
    HOT_WATER_ZONE_BINARY_SENSOR_TYPES,
)
from .coordinator import RemehaHomeUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Remeha Home sensor entities from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = []
    for appliance in coordinator.data["appliances"]:
        # Hugo používá ID celého rozvrhu. Přejmenování unique_id ve stejném
        # záznamu ponechá entity_id, historii i odkazy karet. Při kolizi se
        # dvě odlišné identity neslučují.
        registry = er.async_get(hass)
        prefix = f"{DOMAIN}_{appliance['applianceId']}_holidaySchedule"

        def migrate(previous, target):
            old = registry.async_get_entity_id("binary_sensor", DOMAIN, previous)
            if old and registry.async_get(old).config_entry_id == entry.entry_id:
                if registry.async_get_entity_id("binary_sensor", DOMAIN, target) is None:
                    registry.async_update_entity(old, new_unique_id=target)

        migrate(prefix + ".active", prefix)
        zones = appliance["climateZones"]
        # Starý snímač byl připojen k zařízení. Přesměrovat jeho vazby
        # smíme jen tehdy, když existuje právě jedna jednoznačná cílová zóna.
        preheat_zones = [
            z for z in zones if isinstance(z.get("preHeat"), dict) and "active" in z["preHeat"]
        ]
        if len(zones) == len(preheat_zones) == 1:
            migrate(
                f"{DOMAIN}_{appliance['applianceId']}_preHeat.active",
                f"{DOMAIN}_{zones[0]['climateZoneId']}_preHeat.active",
            )
        for description, transform in APPLIANCE_BINARY_SENSOR_TYPES:
            if description.key == "preHeat.active" and preheat_zones:
                continue
            entities.append(
                RemehaHomeBinarySensor(
                    coordinator, appliance["applianceId"], description, transform
                )
            )
        for climate_zone in appliance["climateZones"]:
            climate_zone_id = climate_zone["climateZoneId"]
            zone_types = list(CLIMATE_ZONE_BINARY_SENSOR_TYPES)
            if (
                isinstance(climate_zone.get("preHeat"), dict)
                and "active" in climate_zone["preHeat"]
            ):
                zone_types.append(
                    (
                        BinarySensorEntityDescription(key="preHeat.active", name="Pre-heat active"),
                        bool,
                    )
                )
            for entity_description, transform_func in zone_types:
                entities.append(
                    RemehaHomeBinarySensor(
                        coordinator,
                        climate_zone_id,
                        entity_description,
                        transform_func,
                    )
                )

        for hot_water_zone in appliance["hotWaterZones"]:
            hot_water_zone_id = hot_water_zone["hotWaterZoneId"]
            dhw_types = list(HOT_WATER_ZONE_BINARY_SENSOR_TYPES)
            if (
                appliance.get("capabilityDhwOptimization")
                and "optimizationEnabled" in hot_water_zone
            ):
                dhw_types.append(
                    (
                        BinarySensorEntityDescription(
                            key="optimizationEnabled", name="Optimization"
                        ),
                        bool,
                    )
                )
            for entity_description, transform_func in dhw_types:
                entities.append(
                    RemehaHomeBinarySensor(
                        coordinator,
                        hot_water_zone_id,
                        entity_description,
                        transform_func,
                    )
                )

    async_add_entities(entities)


class RemehaHomeBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of a binary sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RemehaHomeUpdateCoordinator,
        item_id: str,
        entity_description: BinarySensorEntityDescription,
        transform_func: Callable[[str], bool],
    ) -> None:
        """Create a Remeha Home binary sensor entity."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self.transform_func = transform_func
        self.item_id = item_id
        identity = (
            "holidaySchedule"
            if entity_description.key == "holidaySchedule.active"
            else entity_description.key
        )
        self._attr_unique_id = "_".join([DOMAIN, self.item_id, identity])

    @property
    def _data(self):
        """Return the appliance data for this sensor."""
        return self.coordinator.get_by_id(self.item_id)

    @property
    def is_on(self) -> bool | None:
        """Return the measurement value for this sensor."""
        data = self._data
        for part in self.entity_description.key.split("."):
            # A missing nested field or null response is unknown, never a false alarm.
            if not isinstance(data, dict) or part not in data:
                return None
            data = data[part]
        if data is None:
            return None
        if (
            self.entity_description.key
            in {"applianceOnline", "waterPressureOK", "preHeat.active", "holidaySchedule.active"}
            and type(data) is not bool
        ):
            return None
        return self.transform_func(data)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this device."""
        return self.coordinator.get_device_info(self.item_id)
