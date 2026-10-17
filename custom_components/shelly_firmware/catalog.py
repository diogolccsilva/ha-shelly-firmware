"""Fetch Shelly's firmware catalog and verify the downloaded image.

``updates.shelly.cloud`` and ``fwcdn.shelly.cloud`` present certificates
signed by Shelly's own Allterco CA, which is not in the public trust store.
The connection is pinned to the certificate each host presented, and the
image itself is checked against the SHA-256 embedded in the catalog URL.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .errors import CatalogError, CertificatePinError, ChecksumError

_LOGGER = logging.getLogger(__name__)

CATALOG_HOST = "updates.shelly.cloud"
CDN_HOST = "fwcdn.shelly.cloud"

# SHA-256 of the DER certificate currently presented by each host.
# Update these if Shelly rotates that certificate.
PINNED_CERTIFICATES = {
    CATALOG_HOST: "6bb3786778eb2a7ee1d5d275cecc01d53ddc1745e8002a9400468ff577efeac0",
    CDN_HOST: "baa5ba084e6bb4032eae16c079759bbdd14054f7a4ed82a032ce1c10282d1ba4",
}

_APP_NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")


@dataclass(frozen=True, slots=True)
class Release:
    """One firmware image from the Shelly update catalog."""

    channel: str
    version: str
    build_id: str | None
    url: str


def firmware_sha256(url: str) -> str:
    """Return the SHA-256 Shelly encodes as the last segment of a firmware URL."""
    name = url.rstrip("/").rsplit("/", 1)[-1]
    if not _SHA256.fullmatch(name):
        raise ChecksumError
    return name.lower()


def parse_release(payload: Any, channel: str) -> Release | None:
    """Pull the requested channel out of a catalog payload."""
    if not isinstance(payload, dict):
        raise CatalogError("Shelly update catalog was not a JSON object")
    info = payload.get(channel)
    if not info:
        return None
    if not isinstance(info, dict):
        raise CatalogError(f"Shelly catalog channel {channel} was not an object")
    version = info.get("version")
    url = info.get("url")
    if not version or not url:
        return None
    return Release(
        channel=channel,
        version=str(version),
        build_id=str(info["build_id"]) if info.get("build_id") else None,
        url=str(url),
    )


def assert_pinned(host: str, der_certificate: bytes) -> None:
    """Reject a peer certificate that is not the one pinned for ``host``."""
    expected = PINNED_CERTIFICATES.get(host.lower())
    digest = hashlib.sha256(der_certificate).hexdigest()
    if expected is None or digest != expected:
        _LOGGER.error("Certificate pin mismatch for %s (got %s)", host, digest)
        raise CertificatePinError(host)


async def fetch_release(app: str, channel: str) -> Release | None:
    """Download the catalog for ``app`` and return the requested channel."""
    if not _APP_NAME.fullmatch(app):
        raise CatalogError(f"Unsupported Shelly app name {app!r}")
    raw = await fetch_pinned(f"https://{CATALOG_HOST}/update/{app}")
    try:
        payload = json.loads(raw)
    except ValueError as err:
        raise CatalogError("Shelly update catalog was not valid JSON") from err
    return parse_release(payload, channel)


async def download_firmware(url: str) -> bytes:
    """Download a catalog image and require its bytes to match the URL hash."""
    expected = firmware_sha256(url)
    data = await fetch_pinned(url)
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        _LOGGER.error(
            "Firmware checksum mismatch (expected %s, got %s)", expected, actual
        )
        raise ChecksumError()
    if not data:
        raise CatalogError("Shelly firmware download was empty")
    return data


async def fetch_pinned(url: str) -> bytes:
    """GET ``url`` and require that connection's certificate to match the pin.

    aiohttp does not expose the peer certificate for the connection that
    served the body, so the download uses ``asyncio.open_connection`` and
    reads the certificate from that same socket.
    """
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise CatalogError("Shelly firmware URL must be https")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                parsed.hostname,
                parsed.port or 443,
                ssl=context,
                server_hostname=parsed.hostname,
            ),
            timeout=30,
        )
    except TimeoutError as err:
        raise CatalogError("Timed out contacting the Shelly update server") from err
    except OSError as err:
        raise CatalogError(
            "Couldn't reach the Shelly update server from Home Assistant"
        ) from err

    try:
        ssl_object = writer.get_extra_info("ssl_object")
        der = ssl_object.getpeercert(binary_form=True) if ssl_object else None
        if not der:
            raise CertificatePinError(parsed.hostname)
        assert_pinned(parsed.hostname, der)

        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}\r\n"
            "User-Agent: ha-shelly-firmware\r\n"
            "Accept: */*\r\n"
            "Connection: close\r\n\r\n"
        )
        writer.write(request.encode())
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(), timeout=180)
    except TimeoutError as err:
        raise CatalogError(
            "Timed out downloading from the Shelly update server"
        ) from err
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

    status, headers, body = _split_http(raw)
    if status >= 400 or status < 200:
        raise CatalogError(
            f"Shelly update server returned HTTP {status} for {parsed.hostname}"
        )
    if headers.get("transfer-encoding", "").lower() == "chunked":
        return _decode_chunks(body)
    if "content-length" in headers:
        return body[: int(headers["content-length"])]
    return body


def _split_http(raw: bytes) -> tuple[int, dict[str, str], bytes]:
    header_blob, separator, body = raw.partition(b"\r\n\r\n")
    if not separator:
        raise CatalogError("Shelly update server returned an incomplete response")
    lines = header_blob.decode("latin1").split("\r\n")
    try:
        status = int(lines[0].split()[1])
    except (IndexError, ValueError) as err:
        raise CatalogError("Shelly update server returned an invalid status") from err
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    return status, headers, body


def _decode_chunks(data: bytes) -> bytes:
    output = bytearray()
    index = 0
    while index < len(data):
        line_end = data.find(b"\r\n", index)
        if line_end < 0:
            break
        size = int(data[index:line_end].split(b";", 1)[0] or b"0", 16)
        if size == 0:
            break
        start = line_end + 2
        output.extend(data[start : start + size])
        index = start + size + 2
    return bytes(output)
