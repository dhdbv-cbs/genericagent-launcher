"""Shared non-UI core for the GenericAgent launcher.

This module re-exports symbols from split modules under launcher_core_parts.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from urllib.parse import urlparse

import qrcode


def _repair_requests_stack():
    py = str(sys.executable or "").strip()
    if not py:
        return False, "当前解释器路径为空"
    cmd = [
        py,
        "-m",
        "pip",
        "install",
        "--user",
        "--ignore-installed",
        "requests>=2.31",
        "simplejson>=3.19.3",
        "charset-normalizer>=3.3",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    except Exception as e:
        return False, str(e)
    if r.returncode == 0:
        return True, ""
    detail = ((r.stderr or "") + "\n" + (r.stdout or "")).strip()
    if detail:
        lines = [line.strip() for line in detail.splitlines() if line.strip()]
        detail = "\n".join(lines[-20:]) if lines else detail
    return False, detail or f"pip exit {r.returncode}"


def _import_requests_with_repair():
    def _purge_broken_modules():
        for name in list(sys.modules.keys()):
            key = str(name or "").strip()
            if key == "simplejson" or key.startswith("simplejson."):
                sys.modules.pop(name, None)
            elif key == "requests" or key.startswith("requests."):
                sys.modules.pop(name, None)

    try:
        return importlib.import_module("requests")
    except Exception as first_error:
        _purge_broken_modules()
        ok, _detail = _repair_requests_stack()
        if ok:
            importlib.invalidate_caches()
            _purge_broken_modules()
            try:
                return importlib.import_module("requests")
            except Exception:
                pass
        raise first_error


requests = _import_requests_with_repair()

from launcher_core_parts import channels as _channels
from launcher_core_parts import constants as _constants
from launcher_core_parts import markup as _markup
from launcher_core_parts import model_api as _model_api
from launcher_core_parts import python_env as _python_env
from launcher_core_parts import runtime as _runtime
from launcher_core_parts import schedules as _schedules
from launcher_core_parts import sessions as _sessions
from launcher_core_parts import update_manager as _update_manager
from launcher_core_parts import upstream_dependencies as _upstream_dependencies

_MODULES = (
    _constants,
    _runtime,
    _update_manager,
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
