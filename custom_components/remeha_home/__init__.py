"""The Remeha Home integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import RemehaHomeAPI, RemehaHomeOAuth2Implementation
from .config_flow import RemehaHomeLoginFlowHandler
from .const import DOMAIN
from .coordinator import RemehaHomeUpdateCoordinator
from .energy_history import register_energy_history
from .export_override import ExportOverrideManager
from .producer_ids import ProducerIdResolver
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


def producer_ids_storage_key(entry: ConfigEntry) -> str:
    """Return the storage key for persistent heat producer identities."""
    return f"{DOMAIN}.{entry.entry_id}.producer_ids"


def legacy_producer_ids_storage_key(entry: ConfigEntry) -> str:
    """Return the key used by early versions of the collision fix."""
    return f"{DOMAIN}_producer_ids_{entry.entry_id}"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up Remeha Home."""
    hass.data.setdefault(DOMAIN, {})
    register_energy_history(hass)

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

    producer_store = Store(hass, 1, producer_ids_storage_key(entry))
    saved_producer_ids = await producer_store.async_load()
    if saved_producer_ids is None:
        # Don't discard assignments from an already-installed preview of this PR.
        legacy_store = Store(hass, 1, legacy_producer_ids_storage_key(entry))
        saved_producer_ids = await legacy_store.async_load()
        if saved_producer_ids is not None:
            producer_store.async_delay_save(lambda: dict(saved_producer_ids), 1)
    coordinator.producer_id_resolver = ProducerIdResolver(saved_producer_ids)

    @callback
    def save_producer_ids() -> None:
        """Persist assigned producer IDs without affecting climate updates."""
        producer_store.async_delay_save(
            lambda: dict(coordinator.producer_id_resolver.assignments), 1
        )

    coordinator.on_producer_ids_changed = save_producer_ids
    await coordinator.holiday_water_restore.async_load()
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
