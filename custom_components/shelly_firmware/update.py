"""Firmware update entity for one Shelly device."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, SHELLY_DOMAIN
from .coordinator import ShellyFirmwareCoordinator
from .errors import AuthRequired, ShellyFirmwareError
from .version import is_newer

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the firmware update entity for this device."""
    async_add_entities([ShellyFirmwareUpdate(entry.runtime_data)])


class ShellyFirmwareUpdate(CoordinatorEntity[ShellyFirmwareCoordinator], UpdateEntity):
    """Offers a Shelly firmware update in Settings → Updates."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    )
    _attr_translation_key = "firmware"

    def __init__(self, coordinator: ShellyFirmwareCoordinator) -> None:
        super().__init__(coordinator)
        status = coordinator.data
        assert status is not None
        identity = status.identity
        self._attr_unique_id = f"{identity.mac}_firmware"
        # Only push a name the device itself reports, so we don't rename a
        # device the official Shelly integration already named.
        if identity.reported_name:
            self._attr_device_info = DeviceInfo(
                identifiers={(SHELLY_DOMAIN, identity.mac), (DOMAIN, identity.mac)},
                manufacturer=MANUFACTURER,
                configuration_url=f"http://{coordinator.host}",
                name=identity.reported_name,
            )
        else:
            self._attr_device_info = DeviceInfo(
                identifiers={(SHELLY_DOMAIN, identity.mac), (DOMAIN, identity.mac)},
                manufacturer=MANUFACTURER,
                configuration_url=f"http://{coordinator.host}",
            )
        self._in_progress = False
        self._watch: asyncio.Task[None] | None = None

    @property
    def available(self) -> bool:
        """Stay available while an install is in progress and the device reboots."""
        return self._in_progress or super().available

    @property
    def installed_version(self) -> str | None:
        """Firmware version the device reported."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.identity.ver

    @property
    def latest_version(self) -> str | None:
        """Newest catalog version, or the installed one when nothing is newer."""
        status = self.coordinator.data
        if status is None:
            return None
        if status.update_available and status.release is not None:
            return status.release.version
        return status.identity.ver

    @property
    def release_summary(self) -> str | None:
        """Build id of the offered release."""
        status = self.coordinator.data
        if status is None or not status.update_available or status.release is None:
            return None
        return status.release.build_id or status.release.version

    @property
    def in_progress(self) -> bool:
        """Whether an install has been requested and has not finished."""
        return self._in_progress

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        """Compare Shelly semver, including beta suffixes."""
        return is_newer(latest_version, installed_version)

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Download the image if needed and tell the device to install it."""
        self._in_progress = True
        self.async_write_ha_state()
        try:
            target = await self.coordinator.async_start_update()
        except AuthRequired as err:
            self._in_progress = False
            self.async_write_ha_state()
            self.coordinator.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(str(err)) from err
        except (ShellyFirmwareError, OSError) as err:
            self._in_progress = False
            self.async_write_ha_state()
            raise HomeAssistantError(str(err)) from err
        except Exception:
            self._in_progress = False
            self.async_write_ha_state()
            raise

        self._watch = self.hass.async_create_task(self._watch_until_installed(target))

    async def _watch_until_installed(self, target: str) -> None:
        try:
            await self.coordinator.async_wait_for_version(target)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception(
                "Firmware install on %s did not finish at %s",
                self.coordinator.host,
                target,
            )
        else:
            await self.coordinator.async_delete_staged_firmware()
        finally:
            self._in_progress = False
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel a background install watch when the entity is removed."""
        if self._watch is not None:
            self._watch.cancel()
        await super().async_will_remove_from_hass()
