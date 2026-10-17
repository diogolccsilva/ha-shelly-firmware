"""Config flow for Shelly Local Firmware."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CHANNEL_STABLE,
    CHANNELS,
    CONF_BASE_URL,
    CONF_CHANNEL,
    CONF_MAC,
    CONF_PASSWORD,
    CONF_SERVE_LOCALLY,
    CONF_USERNAME,
    DOMAIN,
)
from .errors import AuthRequired, RpcError, ShellyFirmwareError, UnsupportedDevice
from .rpc import (
    DeviceIdentity,
    ShellyClient,
    discovery_generation,
    parse_dhcp,
    parse_zeroconf,
)
from .util import normalize_base_url

_LOGGER = logging.getLogger(__name__)

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _suggested_base_url(hass: Any) -> str:
    try:
        return get_url(hass, prefer_external=False, allow_cloud=False)
    except NoURLAvailableError:
        return "http://homeassistant.local:8123"


class ShellyFirmwareConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up one Gen2+ Shelly device."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_host: str | None = None
        self._discovered_name = ""
        self._needs_auth = False
        self._pending_host = ""
        self._pending_base_url = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a host and the address devices use to reach Home Assistant."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._pending_host = str(user_input[CONF_HOST]).strip()
            self._pending_base_url = str(user_input[CONF_BASE_URL])
            result = await self._async_try_create(
                {
                    CONF_HOST: self._pending_host,
                    CONF_USERNAME: "",
                    CONF_PASSWORD: "",
                    CONF_BASE_URL: self._pending_base_url,
                }
            )
            if result == {"base": "invalid_auth"}:
                return await self.async_step_credentials()
            if "type" in result:
                return result
            errors = result

        suggested = _suggested_base_url(self.hass)
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST, default=(user_input or {}).get(CONF_HOST, "")
                ): str,
                vol.Required(CONF_BASE_URL, default=suggested): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a password after the device rejected an anonymous login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_try_create(
                {
                    CONF_HOST: self._pending_host,
                    CONF_USERNAME: user_input.get(CONF_USERNAME, ""),
                    CONF_PASSWORD: user_input.get(CONF_PASSWORD, ""),
                    CONF_BASE_URL: self._pending_base_url,
                }
            )
            if "type" in result:
                return result
            errors = result

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME, default="admin"): str,
                vol.Required(CONF_PASSWORD): _PASSWORD,
            }
        )
        return self.async_show_form(
            step_id="credentials", data_schema=schema, errors=errors
        )

    async def async_step_zeroconf(self, discovery_info: Any) -> ConfigFlowResult:
        """Offer a Shelly announced on the LAN as a discovered integration."""
        try:
            host, mac, title, ipv6 = parse_zeroconf(discovery_info)
            generation = discovery_generation(discovery_info)
        except Exception:
            _LOGGER.exception("Couldn't read a Shelly discovery announcement")
            return self.async_abort(reason="cannot_connect")
        if ipv6:
            _LOGGER.debug("Ignoring IPv6 Shelly announcement for %s", title or host)
            return self.async_abort(reason="ipv6_not_supported")
        if generation == "1":
            return self.async_abort(reason="unsupported")
        return await self._async_offer_device(host, mac, title)

    async def async_step_dhcp(self, discovery_info: Any) -> ConfigFlowResult:
        """Offer a Shelly seen in DHCP when mDNS did not reach Home Assistant."""
        try:
            host, mac, title = parse_dhcp(discovery_info)
        except Exception:
            _LOGGER.exception("Couldn't read a Shelly DHCP announcement")
            return self.async_abort(reason="cannot_connect")
        return await self._async_offer_device(host, mac, title)

    async def _async_offer_device(
        self, host: str, mac: str | None, title: str
    ) -> ConfigFlowResult:
        """Probe a discovered device and show one confirmation card."""
        if not host:
            return self.async_abort(reason="cannot_connect")
        self._discovered_host = host
        self._discovered_name = title or host
        self._needs_auth = False
        if mac:
            reserved = await self._async_reserve(mac, host)
            if reserved is not None:
                return reserved
        try:
            identity = await _probe(self.hass, host, "", "")
        except AuthRequired:
            self._needs_auth = True
        except UnsupportedDevice:
            return self.async_abort(reason="unsupported")
        except ShellyFirmwareError as err:
            _LOGGER.debug("Discovery probe of %s failed: %s", host, err)
        else:
            self._discovered_name = identity.name
            reserved = await self._async_reserve(identity.mac, host)
            if reserved is not None:
                return reserved
        self.context.update(
            {
                "title_placeholders": {"name": self._discovered_name},
                "configuration_url": f"http://{host}",
            }
        )
        _LOGGER.info(
            "Offering firmware setup for %s at %s", self._discovered_name, host
        )
        return await self.async_step_discovery_confirm()

    async def _async_reserve(self, mac: str, host: str) -> ConfigFlowResult | None:
        """Claim a device, or abort when it is already set up or already offered.

        Uninitialized flows are ignored. A previous discovery that crashed
        before showing a form must not hide the card forever.
        """
        await self.async_set_unique_id(mac, raise_on_progress=False)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        for flow in self.hass.config_entries.flow.async_progress_by_handler(
            DOMAIN, include_uninitialized=False
        ):
            if flow["flow_id"] == self.flow_id:
                continue
            if (flow.get("context") or {}).get("unique_id") == mac:
                return self.async_abort(reason="already_in_progress")
        return None

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered device and the Home Assistant address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_try_create(
                {
                    CONF_HOST: self._discovered_host or "",
                    CONF_USERNAME: user_input.get(CONF_USERNAME, ""),
                    CONF_PASSWORD: user_input.get(CONF_PASSWORD, ""),
                    CONF_BASE_URL: user_input[CONF_BASE_URL],
                }
            )
            if result == {"base": "invalid_auth"}:
                self._needs_auth = True
            if "type" in result:
                return result
            errors = result

        fields: dict[Any, Any] = {}
        if self._needs_auth:
            fields[vol.Required(CONF_USERNAME, default="admin")] = str
            fields[vol.Required(CONF_PASSWORD)] = _PASSWORD
        fields[vol.Required(CONF_BASE_URL, default=_suggested_base_url(self.hass))] = (
            str
        )
        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=vol.Schema(fields),
            description_placeholders={
                "name": self._discovered_name or self._discovered_host or "",
                "host": self._discovered_host or "",
            },
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start a reauthentication after the device rejected the password."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for credentials again."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            data = dict(entry.data)
            data[CONF_USERNAME] = user_input[CONF_USERNAME]
            data[CONF_PASSWORD] = user_input[CONF_PASSWORD]
            try:
                await _probe(
                    self.hass,
                    data[CONF_HOST],
                    data[CONF_USERNAME],
                    data[CONF_PASSWORD],
                )
            except AuthRequired:
                errors["base"] = "invalid_auth"
            except ShellyFirmwareError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(entry, data_updates=data)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_USERNAME, default=entry.data.get(CONF_USERNAME, "")
                ): str,
                vol.Required(CONF_PASSWORD): _PASSWORD,
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=schema, errors=errors
        )

    async def _async_try_create(
        self, user_input: dict[str, Any]
    ) -> ConfigFlowResult | dict[str, str]:
        host = str(user_input[CONF_HOST]).strip()
        username = str(user_input.get(CONF_USERNAME) or "")
        password = str(user_input.get(CONF_PASSWORD) or "")
        try:
            base_url = normalize_base_url(str(user_input[CONF_BASE_URL]))
        except ValueError:
            return {"base": "invalid_base_url"}

        try:
            identity = await _probe(self.hass, host, username, password)
        except AuthRequired:
            return {"base": "invalid_auth"}
        except UnsupportedDevice:
            return {"base": "unsupported"}
        except (RpcError, aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("Probe of %s failed: %s", host, err)
            return {"base": "cannot_connect"}
        except ShellyFirmwareError as err:
            _LOGGER.debug("Probe of %s failed: %s", host, err)
            return {"base": "cannot_connect"}

        in_progress = [
            (flow.get("context") or {}).get("unique_id")
            for flow in self.hass.config_entries.flow.async_progress_by_handler(
                DOMAIN, include_uninitialized=True
            )
            if flow["flow_id"] != self.flow_id
        ]
        _LOGGER.info(
            "Adding %s (%s); other in-progress firmware flows: %s",
            identity.name,
            identity.mac,
            in_progress,
        )
        await self.async_set_unique_id(identity.mac, raise_on_progress=False)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        return self.async_create_entry(
            title=identity.name,
            data={
                CONF_HOST: host,
                CONF_USERNAME: username,
                CONF_PASSWORD: password,
                CONF_MAC: identity.mac,
            },
            options={
                CONF_SERVE_LOCALLY: True,
                CONF_CHANNEL: CHANNEL_STABLE,
                CONF_BASE_URL: base_url,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return ShellyFirmwareOptionsFlow()


class ShellyFirmwareOptionsFlow(OptionsFlow):
    """Change how updates are delivered after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit channel, local staging, and the Home Assistant address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                user_input = {
                    **user_input,
                    CONF_BASE_URL: normalize_base_url(str(user_input[CONF_BASE_URL])),
                }
            except ValueError:
                errors["base"] = "invalid_base_url"
            else:
                return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SERVE_LOCALLY,
                    default=current.get(CONF_SERVE_LOCALLY, True),
                ): bool,
                vol.Required(
                    CONF_CHANNEL, default=current.get(CONF_CHANNEL, CHANNEL_STABLE)
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=CHANNELS,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_BASE_URL,
                    default=current.get(CONF_BASE_URL, _suggested_base_url(self.hass)),
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)


async def _probe(hass: Any, host: str, username: str, password: str) -> DeviceIdentity:
    client = ShellyClient(async_get_clientsession(hass), host, username, password)
    return await client.probe()
