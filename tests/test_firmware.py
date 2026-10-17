"""Unit tests for catalog parsing, version checks, and digest auth."""

from __future__ import annotations

import hashlib
import ssl
from pathlib import Path

import pytest
from shelly_firmware import catalog, rpc, util, version
from shelly_firmware.errors import CertificatePinError, ChecksumError, RpcError

FIXTURES = Path(__file__).parent / "fixtures"

# RFC 2617 section 3.5
RFC_CHALLENGE = {
    "realm": "testrealm@host.com",
    "nonce": "dcd98b7102dd2f0e8b11d0f600bfb0c093",
    "qop": "auth",
    "opaque": "5ccc069c403ebaf9f0171e9517f40e41",
}


def test_stable_is_newer_than_older_beta() -> None:
    """Gateway case: 2.0.2 stable is newer than 2.0.1-beta1."""
    assert version.is_newer("2.0.2", "2.0.1-beta1")


def test_same_version_is_not_an_update() -> None:
    """Wall Display case: the installed stable build is not offered again."""
    assert not version.is_newer("2.8.3", "2.8.3")


def test_older_stable_is_not_offered_over_a_newer_beta() -> None:
    assert not version.is_newer("2.0.2", "2.2.0-beta1")


def test_newer_beta_is_offered_when_selected() -> None:
    assert version.is_newer("2.2.0-beta1", "2.0.1-beta1")


def test_unparseable_versions_are_not_updates() -> None:
    assert not version.is_newer("", "1.0.0")
    assert not version.is_newer("not-a-version", "also-bad")


def test_normalize_base_url_strips_path() -> None:
    assert util.normalize_base_url("http://192.168.1.10:8123/local/") == (
        "http://192.168.1.10:8123"
    )


def test_normalize_base_url_rejects_junk() -> None:
    with pytest.raises(ValueError):
        util.normalize_base_url("not a url")


def test_firmware_public_url() -> None:
    assert util.firmware_public_url("http://192.168.1.10:8123", "AABBCCDDEEFF") == (
        "http://192.168.1.10:8123/local/shelly_firmware/AABBCCDDEEFF.zip"
    )


def test_parse_release_stable() -> None:
    release = catalog.parse_release(
        {
            "stable": {
                "version": "2.0.2",
                "build_id": "20261009-104928/2.0.2-g34427f1",
                "url": "https://fwcdn.shelly.cloud/gen2-ntest/BluGwG3/"
                "f850646b37d39a03526a15006aac1280b4d9551ce5ba05af3ac997ab1cdce25f",
            }
        },
        "stable",
    )
    assert release is not None
    assert release.version == "2.0.2"
    assert catalog.firmware_sha256(release.url) == (
        "f850646b37d39a03526a15006aac1280b4d9551ce5ba05af3ac997ab1cdce25f"
    )


def test_missing_channel_is_none() -> None:
    assert (
        catalog.parse_release({"stable": {"version": "1", "url": "http://x"}}, "beta")
        is None
    )


def test_firmware_url_without_checksum_is_rejected() -> None:
    with pytest.raises(ChecksumError):
        catalog.firmware_sha256("https://fwcdn.shelly.cloud/gen2-ntest/BluGwG3/latest")


def test_parse_rpc_error() -> None:
    with pytest.raises(RpcError, match="nope"):
        rpc.parse_rpc({"id": 1, "error": {"code": -1, "message": "nope"}})


def test_parse_rpc_result() -> None:
    assert rpc.parse_rpc({"id": 1, "result": None}) is None
    assert rpc.parse_rpc({"ver": "2.8.3", "app": "WallDisplay"})["app"] == "WallDisplay"


def test_identity_rejects_generation_1() -> None:
    with pytest.raises(rpc.UnsupportedDevice):
        rpc.identity_from_status(
            {"gen": 1, "app": "SHSW-1", "ver": "1.0", "mac": "A" * 12}, None
        )


def test_mac_from_zeroconf_name() -> None:
    assert (
        rpc.mac_from_zeroconf_name("shellyblugwg3-28372f21a124._shelly._tcp.local.")
        == "28372F21A124"
    )


def test_digest_rfc2617_example() -> None:
    header = rpc.build_digest_authorization(
        username="Mufasa",
        password="Circle Of Life",
        method="GET",
        uri="/dir/index.html",
        challenge=RFC_CHALLENGE,
        cnonce="0a4f113b",
    )
    assert 'response="6629fae49393a05397450978507c4ef1"' in header
    assert "qop=auth" in header


@pytest.mark.parametrize(
    ("host", "filename"),
    [
        ("updates.shelly.cloud", "updates.shelly.cloud.pem"),
        ("fwcdn.shelly.cloud", "fwcdn.shelly.cloud.pem"),
    ],
)
def test_pinned_certificates_match_fixtures(host: str, filename: str) -> None:
    pem = (FIXTURES / filename).read_text(encoding="utf-8")
    der = ssl.PEM_cert_to_DER_cert(pem)
    catalog.assert_pinned(host, der)
    assert hashlib.sha256(der).hexdigest() == catalog.PINNED_CERTIFICATES[host]


def test_wrong_certificate_is_rejected() -> None:
    with pytest.raises(CertificatePinError):
        catalog.assert_pinned("updates.shelly.cloud", b"not a certificate")
