"""Shelly Local Firmware: catalog updates for devices that cannot reach Shelly."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_MAC
from .coordinator import ShellyFirmwareCoordinator
from .util import remove_file, staged_firmware_path

PLATFORMS: list[Platform] = [Platform.UPDATE]

type ShellyFirmwareConfigEntry = ConfigEntry[ShellyFirmwareCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: ShellyFirmwareConfigEntry
) -> bool:
    """Set up one Shelly device."""
    coordinator = ShellyFirmwareCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ShellyFirmwareConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete a staged firmware file when the device is removed."""
    mac = entry.data.get(CONF_MAC) or entry.unique_id
    if not mac:
        return
    path = staged_firmware_path(hass.config.path(), str(mac))
    await hass.async_add_executor_job(remove_file, path)


async def _async_reload_entry(
    hass: HomeAssistant, entry: ShellyFirmwareConfigEntry
) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
