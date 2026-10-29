"""The Remeha Home integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import RemehaHomeAPI, RemehaHomeOAuth2Implementation
from .config_flow import RemehaHomeLoginFlowHandler
from .const import DOMAIN
from .coordinator import RemehaHomeUpdateCoordinator
from .export_override import ExportOverrideManager
from .upstream import RemehaUpstreamCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.WATER_HEATER,
]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up Remeha Home."""
    hass.data.setdefault(DOMAIN, {})

    RemehaHomeLoginFlowHandler.async_register_implementation(
        hass,
        RemehaHomeOAuth2Implementation(async_get_clientsession(hass)),
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Remeha Home from a config entry."""
    implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
        hass, entry
    )

    oauth_session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)
    api = RemehaHomeAPI(oauth_session)
    # Vlastník registru musí být znám už při prvním načtení dat.
    coordinator = RemehaHomeUpdateCoordinator(hass, api, entry)

    await coordinator.async_config_entry_first_refresh()

    # Load only the journal; setup must never replay a pending heating command.
    coordinator.export_override_manager = ExportOverrideManager(hass, entry.entry_id)
    await coordinator.export_override_manager.async_load()

    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "upstream": RemehaUpstreamCoordinator(hass, entry),
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # GitHub je volitelná diagnostika. Start a 60s cyklus topení na něj nečekají;
    # config entry při unload/restartu zruší případnou rozpracovanou kontrolu.
    entry.async_create_background_task(
        hass,
        hass.data[DOMAIN][entry.entry_id]["upstream"].async_refresh(),
        "remeha_home initial upstream check",
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
