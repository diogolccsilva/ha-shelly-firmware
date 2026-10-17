"""Shelly firmware version comparison.

The default AwesomeVersion strategy raises on Shelly's ``2.0.1-beta1`` style
versions, which would make an update entity look available forever. Compare
the first semver token instead, same as the official Shelly integration.
"""

from __future__ import annotations

from awesomeversion import AwesomeVersion, AwesomeVersionStrategy
from awesomeversion.exceptions import (
    AwesomeVersionCompareException,
    AwesomeVersionStrategyException,
)


def is_newer(latest: str | None, installed: str | None) -> bool:
    """Return True when ``latest`` is a newer semver than ``installed``.

    Equal versions and unparseable strings are not treated as an update, so a
    beta device is never offered an older stable release.
    """
    if not latest or not installed or latest == installed:
        return False
    try:
        return _semver(latest) > _semver(installed)
    except (AwesomeVersionCompareException, AwesomeVersionStrategyException):
        return False


def _semver(value: str) -> AwesomeVersion:
    return AwesomeVersion(
        value,
        find_first_match=True,
        ensure_strategy=[AwesomeVersionStrategy.SEMVER],
    )
