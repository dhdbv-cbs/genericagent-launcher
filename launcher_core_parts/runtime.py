from __future__ import annotations

import json
import os
import subprocess
import sys

from .constants import APP_DIR, CONFIG_PATH


def _bridge_script_path():
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        # runtime.py lives under launcher_core_parts/, while bridge.py stays at
        # the project root next to launcher.py.
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "bridge.py")


def _python_creationflags():
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Config] save failed: {e}")


def _resolve_config_path(path):
    raw = str(path or "").strip()
    if not raw:
        return ""
    if os.path.isabs(raw):
        return os.path.normpath(raw)
    return os.path.normpath(os.path.join(APP_DIR, raw))


def _make_config_relative_path(path):
    raw = str(path or "").strip()
    if not raw:
        return ""
    abs_path = os.path.abspath(raw)
    try:
        rel = os.path.relpath(abs_path, APP_DIR)
    except Exception:
        return abs_path
    if rel.startswith(".."):
        return abs_path
    return rel


def is_valid_agent_dir(path):
    return bool(
        path
        and os.path.isdir(path)
        and os.path.isfile(os.path.join(path, "launch.pyw"))
        and os.path.isfile(os.path.join(path, "agentmain.py"))
    )


def _ensure_mykey_file(agent_dir):
    py_path = os.path.join(agent_dir, "mykey.py")
    json_path = os.path.join(agent_dir, "mykey.json")
    if os.path.isfile(py_path) or os.path.isfile(json_path):
        return {"ok": True, "created": False, "path": py_path if os.path.isfile(py_path) else json_path}
    try:
        with open(py_path, "w", encoding="utf-8") as dst:
            dst.write(
                "# mykey.py\n"
                "# 已由 GenericAgent 启动器自动创建。\n"
                "# 请在启动器的「设置 -> API」中填写渠道配置。\n"
            )
        return {"ok": True, "created": True, "path": py_path}
    except Exception as e:
        return {"ok": False, "created": False, "path": py_path, "error": str(e)}
