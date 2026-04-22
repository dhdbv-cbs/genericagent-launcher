"""Shared non-UI core for the GenericAgent launcher.

This module re-exports symbols from split modules under launcher_core_parts.
"""

from __future__ import annotations

from urllib.parse import urlparse

import qrcode
import requests

from launcher_core_parts import channels as _channels
from launcher_core_parts import constants as _constants
from launcher_core_parts import markup as _markup
from launcher_core_parts import model_api as _model_api
from launcher_core_parts import python_env as _python_env
from launcher_core_parts import runtime as _runtime
from launcher_core_parts import schedules as _schedules
from launcher_core_parts import sessions as _sessions
from launcher_core_parts import upstream_dependencies as _upstream_dependencies

_MODULES = (
    _constants,
    _runtime,
    _python_env,
    _schedules,
    _model_api,
    _sessions,
    _channels,
    _upstream_dependencies,
    _markup,
)

for _module in _MODULES:
    for _name in dir(_module):
        if _name.startswith("__"):
            continue
        globals()[_name] = getattr(_module, _name)

# Keep commonly accessed dependency modules available via launcher_core.
globals()["qrcode"] = qrcode
globals()["requests"] = requests
globals()["urlparse"] = urlparse

__all__ = [name for name in globals() if not name.startswith("__")]
