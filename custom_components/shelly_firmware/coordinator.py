"""Polling coordinator: catalog check, local staging, and install."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .catalog import Release, download_firmware, fetch_release
from .const import (
    CHANNEL_STABLE,
    CONF_BASE_URL,
    CONF_CHANNEL,
    CONF_HOST,
    CONF_MAC,
    CONF_PASSWORD,
    CONF_SERVE_LOCALLY,
    CONF_USERNAME,
    UPDATE_INTERVAL,
    UPDATE_TIMEOUT,
)
from .errors import AuthRequired, CatalogError, ShellyFirmwareError
from .rpc import DeviceIdentity, ShellyClient
from .util import (
    firmware_public_url,
    remove_file,
    staged_firmware_path,
    write_atomic,
)
from .version import is_newer

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FirmwareStatus:
    """Installed firmware plus the newest catalog release, if it is newer."""

    identity: DeviceIdentity
    release: Release | None

    @property
    def update_available(self) -> bool:
        """Whether the catalog release should be offered."""
        return self.release is not None and is_newer(
            self.release.version, self.identity.ver
        )


class ShellyFirmwareCoordinator(DataUpdateCoordinator[FirmwareStatus]):
    """Tracks one Shelly device against Shelly's firmware catalog."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.config_entry = entry
        super().__init__(
            hass,
            _LOGGER,
            name=f"Shelly firmware {entry.data[CONF_HOST]}",
            update_interval=UPDATE_INTERVAL,
        )

    @property
    def host(self) -> str:
        """Device host configured for this entry."""
        return str(self.config_entry.data[CONF_HOST])

    def client(self) -> ShellyClient:
        """Build a client from the current entry data."""
        data = self.config_entry.data
        return ShellyClient(
            async_get_clientsession(self.hass),
            self.host,
            str(data.get(CONF_USERNAME) or ""),
            str(data.get(CONF_PASSWORD) or ""),
        )

    async def _async_update_data(self) -> FirmwareStatus:
        try:
            identity = await self.client().probe()
            release = await fetch_release(
                identity.app,
                str(self.config_entry.options.get(CONF_CHANNEL, CHANNEL_STABLE)),
            )
        except AuthRequired:
            await self.config_entry.async_start_reauth(self.hass)
            raise UpdateFailed(f"Authentication failed for {self.host}") from None
        except ShellyFirmwareError as err:
            raise UpdateFailed(str(err)) from err

        if release is None or not is_newer(release.version, identity.ver):
            await self.async_delete_staged_firmware()
        return FirmwareStatus(identity=identity, release=release)

    async def async_start_update(self) -> str:
        """Stage the image if needed and tell the device to install it.

        Returns the version the device should be running afterwards.
        """
        status = self.data
        if status is None or status.release is None or not status.update_available:
            raise CatalogError("No firmware update is available")

        release = status.release
        client = self.client()
        if self.config_entry.options.get(CONF_SERVE_LOCALLY, True):
            base_url = str(self.config_entry.options.get(CONF_BASE_URL) or "")
            try:
                public_url = firmware_public_url(base_url, status.identity.mac)
            except ValueError as err:
                raise CatalogError(str(err)) from err
            image = await download_firmware(release.url)
            path = self._staged_path(status.identity.mac)
            await self.hass.async_add_executor_job(write_atomic, path, image)
            _LOGGER.info(
                "Staged %s firmware %s (%s bytes) at %s",
                status.identity.name,
                release.version,
                len(image),
                public_url,
            )
            result = await client.update_from_url(public_url)
        else:
            _LOGGER.info(
                "Asking %s to install %s %s from Shelly",
                status.identity.name,
                release.channel,
                release.version,
            )
            result = await client.update_channel(release.channel)

        _LOGGER.info(
            "Shelly.Update accepted for %s -> %s: %s",
            status.identity.name,
            release.version,
            result,
        )
        return release.version

    async def async_wait_for_version(self, version: str) -> None:
        """Poll until the device reports ``version`` or the timeout elapses."""
        deadline = asyncio.get_running_loop().time() + UPDATE_TIMEOUT.total_seconds()
        client = self.client()
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(5)
            try:
                identity = await client.probe()
            except ShellyFirmwareError:
                continue
            if not is_newer(version, identity.ver):
                _LOGGER.info("%s is now on firmware %s", identity.name, identity.ver)
                return
        raise TimeoutError(
            f"{self.host} did not report firmware {version} within "
            f"{int(UPDATE_TIMEOUT.total_seconds() // 60)} minutes"
        )

    async def async_delete_staged_firmware(self) -> None:
        """Remove the zip from ``/config/www`` once the device no longer needs it."""
        mac = self.config_entry.data.get(CONF_MAC) or self.config_entry.unique_id
        if not mac:
            return
        path = self._staged_path(str(mac))
        await self.hass.async_add_executor_job(remove_file, path)

    def _staged_path(self, mac: str) -> Path:
        return staged_firmware_path(self.hass.config.path(), mac)
