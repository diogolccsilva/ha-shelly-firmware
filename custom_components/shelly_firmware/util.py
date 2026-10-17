"""Small helpers with no Home Assistant imports."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


def normalize_base_url(value: str) -> str:
    """Return ``scheme://host[:port]`` or raise ``ValueError``.

    Devices download firmware from this origin. A path, query, or trailing
    slash is dropped so the integration controls the ``/local/`` path.
    """
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(
            "Home Assistant address must look like http://192.168.1.10:8123"
        )
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return f"{parsed.scheme}://{host}"


def firmware_public_url(base_url: str, mac: str) -> str:
    """URL a Shelly device uses to download its staged firmware."""
    return f"{normalize_base_url(base_url)}/local/shelly_firmware/{mac}.zip"


def staged_firmware_path(config_path: str, mac: str) -> Path:
    """Absolute path of the staged zip inside Home Assistant's www folder."""
    return Path(config_path) / "www" / "shelly_firmware" / f"{mac}.zip"


def write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` so readers never see a half-written zip."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_bytes(data)
    temporary.replace(path)


def remove_file(path: Path) -> None:
    """Delete a staged zip if it is still there."""
    path.unlink(missing_ok=True)
