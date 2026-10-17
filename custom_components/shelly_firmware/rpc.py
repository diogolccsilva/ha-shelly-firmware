"""Minimal Gen2+ Shelly RPC client over local HTTP."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from .const import APP_NAMES
from .errors import AuthRequired, RpcError, UnsupportedDevice

_LOGGER = logging.getLogger(__name__)

_AUTH_PARAM = re.compile(r'(\w+)=(?:"([^"]*)"|([^,\s]+))')


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    """Identity and firmware version reported by a Shelly device."""

    name: str
    reported_name: str | None
    mac: str
    model: str
    app: str
    gen: int
    ver: str
    fw_id: str


def parse_rpc(payload: Any) -> Any:
    """Unwrap a JSON-RPC response, or return a bare Gen2 result object."""
    if not isinstance(payload, dict):
        raise RpcError("Unexpected response from the Shelly device")
    error = payload.get("error")
    if error:
        if isinstance(error, dict):
            message = str(error.get("message") or error)
            code = error.get("code")
            raise RpcError(message, code if isinstance(code, int) else None)
        raise RpcError(str(error))
    if "result" in payload:
        return payload["result"]
    return payload


def parse_www_authenticate(header: str) -> dict[str, str]:
    """Parse a ``WWW-Authenticate: Digest ...`` challenge."""
    if header.lower().startswith("digest"):
        header = header[6:]
    found: dict[str, str] = {}
    for match in _AUTH_PARAM.finditer(header):
        found[match.group(1).lower()] = match.group(2) or match.group(3) or ""
    return found


def build_digest_authorization(
    *,
    username: str,
    password: str,
    method: str,
    uri: str,
    challenge: dict[str, str],
    cnonce: str,
    nc: str = "00000001",
) -> str:
    """Build a Digest Authorization header value for one request."""
    realm = challenge.get("realm", "")
    nonce = challenge.get("nonce", "")
    qop = challenge.get("qop", "")
    if "," in qop:
        options = [part.strip() for part in qop.split(",")]
        qop = "auth" if "auth" in options else options[0]
    algorithm = challenge.get("algorithm") or "MD5"
    ha1 = hashlib.md5(f"{username}:{realm}:{password}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    if qop:
        response = hashlib.md5(
            f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}".encode()
        ).hexdigest()
    else:
        response = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()

    parts = [
        f'username="{username}"',
        f'realm="{realm}"',
        f'nonce="{nonce}"',
        f'uri="{uri}"',
        f'response="{response}"',
        f"algorithm={algorithm}",
    ]
    if qop:
        parts.extend((f"qop={qop}", f"nc={nc}", f'cnonce="{cnonce}"'))
    if opaque := challenge.get("opaque"):
        parts.append(f'opaque="{opaque}"')
    return "Digest " + ", ".join(parts)


def _request_uri(url: str) -> str:
    split = urlsplit(url)
    uri = split.path or "/"
    if split.query:
        uri = f"{uri}?{split.query}"
    return uri


def _normalize_mac(value: str) -> str:
    mac = value.replace(":", "").upper()
    if len(mac) != 12 or any(char not in "0123456789ABCDEF" for char in mac):
        raise RpcError("Device did not report a MAC address")
    return mac


def mac_from_zeroconf_name(name: str) -> str | None:
    """Extract a MAC from ``shellyblugwg3-28372f21a124._shelly._tcp.local.``."""
    suffix = name.split(".", 1)[0].rsplit("-", 1)[-1].upper()
    if len(suffix) == 12 and all(char in "0123456789ABCDEF" for char in suffix):
        return suffix
    return None


def identity_from_status(
    info: dict[str, Any], configured_name: str | None
) -> DeviceIdentity:
    """Build a ``DeviceIdentity`` from ``Shelly.GetDeviceInfo``."""
    try:
        gen = int(info.get("gen") or 0)
    except (TypeError, ValueError) as err:
        raise UnsupportedDevice("Device did not report a generation") from err
    app = str(info.get("app") or "")
    if gen < 2 or not app:
        raise UnsupportedDevice(
            "Only Gen2 or newer Shelly devices that speak RPC can be updated"
        )
    ver = str(info.get("ver") or "")
    if not ver:
        raise RpcError("Device did not report a firmware version")
    reported = configured_name or (str(info["name"]) if info.get("name") else None)
    title = reported or APP_NAMES.get(app) or app
    return DeviceIdentity(
        name=title,
        reported_name=reported,
        mac=_normalize_mac(str(info.get("mac") or "")),
        model=str(info.get("model") or app),
        app=app,
        gen=gen,
        ver=ver,
        fw_id=str(info.get("fw_id") or ""),
    )


class ShellyClient:
    """HTTP RPC client for one Shelly device."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        username: str = "",
        password: str = "",
    ) -> None:
        self._session = session
        self.host = host.strip().removeprefix("http://").removeprefix("https://")
        self.host = self.host.rstrip("/")
        self._username = username
        self._password = password

    async def probe(self) -> DeviceIdentity:
        """Read device identity, including a configured name when present."""
        info = await self.call("Shelly.GetDeviceInfo", timeout=10)
        if not isinstance(info, dict):
            raise RpcError("Shelly.GetDeviceInfo returned an unexpected payload")
        configured: str | None = None
        try:
            sys_config = await self.call("Sys.GetConfig", timeout=10)
        except (RpcError, AuthRequired):
            sys_config = None
        if isinstance(sys_config, dict):
            device = sys_config.get("device")
            if isinstance(device, dict) and device.get("name"):
                configured = str(device["name"])
        return identity_from_status(info, configured)

    async def update_from_url(self, url: str) -> Any:
        """Tell the device to install firmware from ``url``."""
        return await self.call("Shelly.Update", {"url": url}, timeout=60)

    async def update_channel(self, channel: str) -> Any:
        """Tell the device to install the stable or beta catalog image itself."""
        return await self.call("Shelly.Update", {"stage": channel}, timeout=60)

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = 20,
    ) -> Any:
        """Call one RPC method and return its result."""
        payload: dict[str, Any] = {"id": 1, "method": method}
        if params is not None:
            payload["params"] = params
        url = f"http://{self.host}/rpc"
        body = await self._post(url, payload, timeout, authorization=None)
        return parse_rpc(body)

    async def _post(
        self,
        url: str,
        payload: dict[str, Any],
        timeout: float,
        authorization: str | None,
    ) -> Any:
        status, headers, body = await self._send(url, payload, timeout, authorization)
        if status == 401 and authorization is None and self._username:
            challenge = parse_www_authenticate(headers.get("WWW-Authenticate", ""))
            if "nonce" not in challenge:
                raise AuthRequired()
            authorization = build_digest_authorization(
                username=self._username,
                password=self._password,
                method="POST",
                uri=_request_uri(url),
                challenge=challenge,
                cnonce=secrets.token_hex(8),
            )
            status, headers, body = await self._send(
                url, payload, timeout, authorization
            )
        if status == 401:
            raise AuthRequired()
        if status >= 400:
            detail = body if isinstance(body, str) else str(body)
            raise RpcError(f"Shelly device returned HTTP {status}: {detail[:200]}")
        if not isinstance(body, dict):
            raise RpcError("Shelly device returned a response that was not JSON")
        return body

    async def _send(
        self,
        url: str,
        payload: dict[str, Any],
        timeout: float,
        authorization: str | None,
    ) -> tuple[int, dict[str, str], Any]:
        headers = {"Authorization": authorization} if authorization else None
        try:
            async with self._session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                status = response.status
                header_map = {key: value for key, value in response.headers.items()}
                text = await response.text()
        except TimeoutError as err:
            raise RpcError(f"Timed out talking to {self.host}") from err
        except aiohttp.ClientError as err:
            raise RpcError(f"Couldn't reach the Shelly device at {self.host}") from err

        parsed: Any = text
        if "json" in header_map.get("Content-Type", "").lower() or text.startswith("{"):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = text
        _LOGGER.debug("RPC %s -> HTTP %s", payload.get("method"), status)
        return status, header_map, parsed
