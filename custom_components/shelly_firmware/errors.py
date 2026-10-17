"""Errors raised while talking to a Shelly device or its update catalog."""

from __future__ import annotations


class ShellyFirmwareError(Exception):
    """Base error with a message that can be shown in the UI."""


class AuthRequired(ShellyFirmwareError):
    """The device rejected the request because authentication is required."""

    def __init__(self) -> None:
        super().__init__("Shelly device requires a username and password")


class UnsupportedDevice(ShellyFirmwareError):
    """The device is not a Gen2+ RPC device this integration can update."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)


class RpcError(ShellyFirmwareError):
    """A Shelly RPC call returned an error."""

    def __init__(self, message: str, code: int | None = None) -> None:
        self.code = code
        super().__init__(message)


class CatalogError(ShellyFirmwareError):
    """The Shelly update catalog could not be read or failed verification."""


class CertificatePinError(CatalogError):
    """The update server presented a certificate other than the one we pin."""

    def __init__(self, host: str) -> None:
        super().__init__(
            f"Certificate for {host} did not match the pinned Shelly update certificate"
        )
        self.host = host


class ChecksumError(CatalogError):
    """Downloaded bytes did not match the checksum Shelly published."""

    def __init__(self) -> None:
        super().__init__(
            "Downloaded firmware did not match the checksum published by Shelly"
        )
