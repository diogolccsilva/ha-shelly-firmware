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
from .rpc import DeviceIdentity, ShellyClient, mac_from_zeroconf_name
from .util import normalize_base_url

_LOGGER = logging.getLogger(__name__)


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

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a host and the address devices use to reach Home Assistant."""
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_try_create(user_input)
            # Flow results are dicts too. Only field errors lack a result type.
            if "type" in result:
                return result
            errors = result

        suggested = _suggested_base_url(self.hass)
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST, default=(user_input or {}).get(CONF_HOST, "")
                ): str,
                vol.Optional(CONF_USERNAME, default=""): str,
                vol.Optional(CONF_PASSWORD, default=""): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Required(CONF_BASE_URL, default=suggested): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_zeroconf(self, discovery_info: Any) -> ConfigFlowResult:
        """Offer Shelly devices announced on the LAN."""
        ip_address = getattr(discovery_info, "ip_address", None)
        host = str(ip_address or getattr(discovery_info, "host", ""))
        mac = mac_from_zeroconf_name(str(discovery_info.name))
        # Do not reserve the unique id yet. A discovery form that holds it
        # makes a manual setup of the same device abort with
        # already_in_progress, and these devices are often discovered while
        # the user is adding them by IP.
        if mac and mac in self._async_current_ids():
            await self.async_set_unique_id(mac, raise_on_progress=False)
            self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        self._discovered_host = host
        name = str(discovery_info.name).split(".", 1)[0]
        self.context["title_placeholders"] = {"name": name}
        return await self.async_step_discovery_confirm()

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
            if "type" in result:
                return result
            errors = result

        schema = vol.Schema(
            {
                vol.Optional(CONF_USERNAME, default=""): str,
                vol.Optional(CONF_PASSWORD, default=""): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Required(
                    CONF_BASE_URL, default=_suggested_base_url(self.hass)
                ): str,
            }
        )
        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=schema,
            description_placeholders={"host": self._discovered_host or ""},
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
                vol.Required(CONF_PASSWORD): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
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
