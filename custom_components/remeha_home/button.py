"""Ruční čtecí kontrola veřejných upstreamů; tlačítko nemění žádné topení."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    """Připojit jednu diagnostickou kontrolu k prvnímu zařízení položky."""
    data = hass.data[DOMAIN][entry.entry_id]
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
