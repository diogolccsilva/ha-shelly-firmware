"""Constants for the Shelly Local Firmware integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "shelly_firmware"

# Shared with the official Shelly integration so firmware entities attach to
# the device the user already has, instead of creating a second copy.
SHELLY_DOMAIN = "shelly"

CONF_HOST = "host"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_MAC = "mac"
CONF_BASE_URL = "base_url"
CONF_CHANNEL = "channel"
CONF_SERVE_LOCALLY = "serve_locally"

CHANNEL_STABLE = "stable"
CHANNEL_BETA = "beta"
CHANNELS = [CHANNEL_STABLE, CHANNEL_BETA]

UPDATE_INTERVAL = timedelta(hours=1)
# Wall Display packages are about 30 MB. Give the device time to download
# the file from Home Assistant and reboot onto the new version.
UPDATE_TIMEOUT = timedelta(minutes=15)

MANUFACTURER = "Shelly"

# Friendly titles when the device itself has no configured name.
APP_NAMES = {
    "WallDisplay": "Shelly Wall Display",
    "WallDisplayV2": "Shelly Wall Display XL",
    "BluGwG3": "Shelly BLU Gateway Gen3",
}
