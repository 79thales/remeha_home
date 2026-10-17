"""Platform for sensor integration."""

from __future__ import annotations

import logging

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    APPLIANCE_SENSOR_TYPES,
    CLIMATE_ZONE_SENSOR_TYPES,
    DOMAIN,
    ELECTRIC_PRODUCER_SENSOR_TYPES,
    GAS_PRODUCER_SENSOR_TYPES,
    HOT_WATER_ZONE_SENSOR_TYPES,
)
from .coordinator import (
    RemehaHomeUpdateCoordinator,
    get_producer_id,
    get_producers,
)
from .upstream import SOURCES

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Remeha Home sensor entities from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = []
    for appliance in coordinator.data["appliances"]:
        appliance_id = appliance["applianceId"]
        for entity_description in APPLIANCE_SENSOR_TYPES:
            entities.append(RemehaHomeSensor(coordinator, appliance_id, entity_description))

        for climate_zone in appliance["climateZones"]:
            climate_zone_id = climate_zone["climateZoneId"]
            for entity_description in CLIMATE_ZONE_SENSOR_TYPES:
                entities.append(RemehaHomeSensor(coordinator, climate_zone_id, entity_description))

        for hot_water_zone in appliance["hotWaterZones"]:
            hot_water_zone_id = hot_water_zone["hotWaterZoneId"]
            for entity_description in HOT_WATER_ZONE_SENSOR_TYPES:
                entities.append(
                    RemehaHomeSensor(coordinator, hot_water_zone_id, entity_description)
                )
    async_add_entities(entities)

    # Samostatná data GitHubu se nečtou přes cloudový koordinátor topení.
    # Jen dvě diagnostické entity na config entry, připojené k prvnímu zařízení.
    upstream = hass.data[DOMAIN][entry.entry_id].get("upstream")
    if upstream is not None and coordinator.data["appliances"]:
        parent = coordinator.data["appliances"][0]["applianceId"]
        async_add_entities(
            [
                RemehaUpstreamSensor(upstream, entry, source, coordinator.get_device_info(parent))
                for source in SOURCES
            ]
        )

    known_producer_ids: set[str] = set()

    @callback
    def async_add_producer_sensors() -> None:
        """Add the sensors of producers that were not seen before.

        Right after midnight the API doesn't list the producers until the data for
        the new day is reported, so they can appear after the setup.
        """
        new_entities = []
        for appliance in coordinator.data["appliances"]:
            for producer in get_producers(appliance["consumptionData"]):
                producer_id = get_producer_id(appliance["applianceId"], producer)
                if producer_id in known_producer_ids:
                    continue
                if producer.get("energyType") == "NaturalGas":
                    entity_descriptions = GAS_PRODUCER_SENSOR_TYPES
                elif producer.get("energyType") == "Electric":
                    entity_descriptions = ELECTRIC_PRODUCER_SENSOR_TYPES
                else:
                    continue
                # Neznámý typ si neoznačíme za hotový: při pozdějším potvrzení
                # jednotek se senzory mohou doplnit bez restartu integrace.
                known_producer_ids.add(producer_id)
                new_entities.extend(
                    RemehaHomeSensor(coordinator, producer_id, entity_description)
                    for entity_description in entity_descriptions
                )

        if new_entities:
            async_add_entities(new_entities)

    async_add_producer_sensors()
    entry.async_on_unload(coordinator.async_add_listener(async_add_producer_sensors))


class RemehaHomeSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RemehaHomeUpdateCoordinator,
        item_id: str,
        entity_description: SensorEntityDescription,
    ) -> None:
        """Create a Remeha Home sensor entity."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self.item_id = item_id
        self._attr_unique_id = "_".join([DOMAIN, self.item_id, entity_description.key])

    @property
    def _data(self):
        """Return the appliance data for this sensor."""
        return self.coordinator.get_by_id(self.item_id)

    @property
    def native_value(self):
        """Return the measurement value for this sensor."""
        data = self._data
        for part in self.entity_description.key.split("."):
            # If the key is missing for some reason, don't crash, instead return None
            if not isinstance(data, dict) or part not in data:
                _LOGGER.debug("Key not found in data: %s", self.entity_description.key)
                return None
            data = data[part]
        value = data

        if self.entity_description.device_class == SensorDeviceClass.TIMESTAMP:
            # Zachovat offset API. Naivní čas interpretujeme v pásmu HA;
            # sentinel roku 1, prázdná a neplatná hodnota znamenají neznámý čas.
            parsed = dt_util.parse_datetime(value) if isinstance(value, str) else None
            if parsed is None or parsed.year <= 1:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
            return dt_util.as_utc(parsed)

        return value

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this device."""
        return self.coordinator.get_device_info(self.item_id)


class RemehaUpstreamSensor(CoordinatorEntity, SensorEntity):
    """Počet dosud nepřevzatých commitů; chybná kontrola se nezobrazí jako 0.

    Jednotka commity není energie ani kumulativní měření. Atributy uchovávají
    přesný základ, odkaz, čas kontroly a poslední úspěch pro rozlišení výpadku.
    Stav každého zdroje je nezávislý na druhém zdroji a na dostupnosti Remeha.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:source-commit"
    _attr_native_unit_of_measurement = "commits"

    def __init__(self, coordinator, entry, source, device_info):
        super().__init__(coordinator)
        self.source = source
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_upstream_{source.key}"
        self._attr_name = "Nové commity · " + (
            "původní integrace" if source.key == "original" else "komunitní fork"
        )
        self._attr_device_info = device_info

    @property
    def available(self):
        """Nula platí jen při úspěšném srovnání stejné historie."""
        result = (self.coordinator.data or {}).get(self.source.key)
        return super().available and result is not None and result.get("error") is None

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self.source.key, {}).get("commits")

    @property
    def extra_state_attributes(self):
        return (self.coordinator.data or {}).get(self.source.key, {})
