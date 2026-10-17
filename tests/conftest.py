"""Load integration modules without importing Home Assistant.

``shelly_firmware/__init__.py`` pulls in Home Assistant. Registering the package
first lets tests import ``catalog`` and ``rpc`` through their relative imports
without executing that file.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

_COMPONENT = (
    Path(__file__).resolve().parent.parent / "custom_components" / "shelly_firmware"
)
_package = types.ModuleType("shelly_firmware")
_package.__path__ = [str(_COMPONENT)]  # type: ignore[attr-defined]
_package.__package__ = "shelly_firmware"
sys.modules["shelly_firmware"] = _package
