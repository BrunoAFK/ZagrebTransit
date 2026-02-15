"""Zagreb Transit integration."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    PLATFORMS,
    SERVICE_ADD_WATCH,
    SERVICE_DUPLICATE_WATCH,
    SERVICE_FORCE_SELECT_FEED,
    SERVICE_REMOVE_WATCH,
    SERVICE_REBUILD_INDEXES,
    SERVICE_REFRESH_REALTIME,
    SERVICE_REFRESH_STATIC,
    SERVICE_UPDATE_WATCH,
    SERVICE_VALIDATE_ACTIVE_FEED,
    WATCH_TYPE_DEPARTURE,
    WATCH_TYPES,
)
from .coordinator import ZagrebTransitCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_FORCE_SCHEMA = vol.Schema({vol.Required("version"): cv.string})
SERVICE_ADD_WATCH_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Required("watch_type", default=WATCH_TYPE_DEPARTURE): vol.In(WATCH_TYPES),
        vol.Optional("enabled", default=True): cv.boolean,
        vol.Optional("config", default={}): dict,
    }
)
SERVICE_UPDATE_WATCH_SCHEMA = vol.Schema(
    {
        vol.Required("watch_id"): cv.string,
        vol.Optional("name"): cv.string,
        vol.Optional("enabled"): cv.boolean,
        vol.Optional("config"): dict,
    }
)
SERVICE_REMOVE_WATCH_SCHEMA = vol.Schema({vol.Required("watch_id"): cv.string})
SERVICE_DUPLICATE_WATCH_SCHEMA = vol.Schema(
    {
        vol.Required("watch_id"): cv.string,
        vol.Optional("name_suffix", default=" Copy"): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up Zagreb Transit domain services."""

    async def _with_coordinator(call: ServiceCall, handler_name: str, **kwargs):
        entries = hass.data.get(DOMAIN, {})
        if not entries:
            _LOGGER.warning("No %s entries loaded", DOMAIN)
            return None
        coordinator = next(iter(entries.values()))
        data = dict(call.data)
        data.update(kwargs)
        return await getattr(coordinator, handler_name)(**data)

    async def handle_refresh_static(call: ServiceCall) -> None:
        # Manual refresh service should always bypass refresh interval guard.
        await _with_coordinator(call, "async_refresh_static", force=True)

    async def handle_refresh_realtime(call: ServiceCall) -> None:
        # Manual refresh service should always bypass refresh interval guard.
        await _with_coordinator(call, "async_refresh_realtime", force=True)

    async def handle_rebuild_indexes(call: ServiceCall) -> None:
        await _with_coordinator(call, "async_rebuild_indexes")

    async def handle_validate(call: ServiceCall) -> None:
        await _with_coordinator(call, "async_validate_active_feed")

    async def handle_force(call: ServiceCall) -> None:
        await _with_coordinator(call, "async_force_select_feed")

    async def handle_add_watch(call: ServiceCall) -> None:
        await _with_coordinator(call, "async_add_watch")

    async def handle_update_watch(call: ServiceCall) -> None:
        await _with_coordinator(call, "async_update_watch")

    async def handle_remove_watch(call: ServiceCall) -> None:
        await _with_coordinator(call, "async_remove_watch")

    async def handle_duplicate_watch(call: ServiceCall) -> None:
        await _with_coordinator(call, "async_duplicate_watch")

    hass.services.async_register(DOMAIN, SERVICE_REFRESH_STATIC, handle_refresh_static)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_REALTIME, handle_refresh_realtime)
    hass.services.async_register(DOMAIN, SERVICE_REBUILD_INDEXES, handle_rebuild_indexes)
    hass.services.async_register(DOMAIN, SERVICE_VALIDATE_ACTIVE_FEED, handle_validate)
    hass.services.async_register(
        DOMAIN,
        SERVICE_FORCE_SELECT_FEED,
        handle_force,
        schema=SERVICE_FORCE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_WATCH,
        handle_add_watch,
        schema=SERVICE_ADD_WATCH_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_WATCH,
        handle_update_watch,
        schema=SERVICE_UPDATE_WATCH_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_WATCH,
        handle_remove_watch,
        schema=SERVICE_REMOVE_WATCH_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DUPLICATE_WATCH,
        handle_duplicate_watch,
        schema=SERVICE_DUPLICATE_WATCH_SCHEMA,
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up config entry."""
    coordinator = ZagrebTransitCoordinator(hass, entry)
    await coordinator.async_initialize()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)

    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_REFRESH_STATIC)
        hass.services.async_remove(DOMAIN, SERVICE_REFRESH_REALTIME)
        hass.services.async_remove(DOMAIN, SERVICE_REBUILD_INDEXES)
        hass.services.async_remove(DOMAIN, SERVICE_VALIDATE_ACTIVE_FEED)
        hass.services.async_remove(DOMAIN, SERVICE_FORCE_SELECT_FEED)
        hass.services.async_remove(DOMAIN, SERVICE_ADD_WATCH)
        hass.services.async_remove(DOMAIN, SERVICE_UPDATE_WATCH)
        hass.services.async_remove(DOMAIN, SERVICE_REMOVE_WATCH)
        hass.services.async_remove(DOMAIN, SERVICE_DUPLICATE_WATCH)
        hass.data.pop(DOMAIN, None)

    return unload_ok
