"""Ruční čtecí kontrola veřejných upstreamů; tlačítko nemění žádné topení."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .temporary import start_override


async def async_setup_entry(hass, entry, async_add_entities):
    """Připojit jednu diagnostickou kontrolu k prvnímu zařízení položky."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    known = set()

    @callback
    def add_timed_buttons():
        """Discover supported zones on each refresh; never press on startup."""
        entities = []
        for appliance in coordinator.data.get("appliances", []):
            for zone in appliance.get("climateZones", []):
                zone_id = zone["climateZoneId"]
                if zone.get("capabilityTemporaryOverrideEndTime") is True and zone_id not in known:
                    known.add(zone_id)
                    entities.append(TemporaryOverrideButton(data["api"], coordinator, zone_id))
        if entities:
            async_add_entities(entities)

    add_timed_buttons()
    entry.async_on_unload(coordinator.async_add_listener(add_timed_buttons))
    appliances = data["coordinator"].data.get("appliances", [])
    if appliances:
        async_add_entities(
            [
                RemehaUpstreamCheckButton(
                    data["upstream"],
                    entry,
                    data["coordinator"].get_device_info(appliances[0]["applianceId"]),
                )
            ]
        )


class TemporaryOverrideButton(CoordinatorEntity, ButtonEntity):
    """Send the two explicitly selected draft values to this zone once."""

    _attr_has_entity_name = True
    _attr_name = "Start temporary temperature"
    _attr_icon = "mdi:timer-play-outline"

    def __init__(self, api, coordinator, zone_id):
        super().__init__(coordinator)
        self.api, self.climate_zone_id = api, zone_id
        self._attr_unique_id = f"{DOMAIN}_{zone_id}_start_temporary_temperature"
        self._attr_device_info = coordinator.get_device_info(zone_id)

    async def async_press(self):
        """Reject missing drafts and let the shared helper validate fresh data."""
        values = getattr(self.coordinator, "temporary_drafts", {}).get(self.climate_zone_id, {})
        temperature, duration = values.get("temperature"), values.get("duration")
        if duration is None or not float(duration).is_integer():
            raise ServiceValidationError("Select a whole duration in minutes")
        await start_override(self, temperature, int(duration))


class RemehaUpstreamCheckButton(ButtonEntity):
    """Zkontrolovat GitHub i po předchozím výpadku; odstup hlídá koordinátor."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Zkontrolovat upstream"
    _attr_icon = "mdi:source-branch-check"

    def __init__(self, coordinator, entry, device_info):
        self.coordinator = coordinator
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_upstream_check"
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        """Požádat o obnovení; 5min cache a odklad GitHubu platí i pro tlačítko."""
        await self.coordinator.async_request_refresh()
