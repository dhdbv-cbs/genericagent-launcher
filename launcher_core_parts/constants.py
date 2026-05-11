from __future__ import annotations

import os
import sys

REPO_URL = "https://github.com/lsdefine/GenericAgent"
LAUNCHER_REPO_URL = "https://github.com/dhdbv-cbs/genericagent-launcher"
APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
APP_NAME = "GenericAgentLauncher"
APP_DISPLAY_NAME = "GenericAgent Launcher"
IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"
PLATFORM_SUPPORTS_INTERNAL_UPDATER = IS_WINDOWS
PLATFORM_SUPPORTS_PRIVATE_PYTHON_INSTALLER = IS_WINDOWS


def _default_local_appdata():
    if IS_MACOS:
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    root = str(os.environ.get("LOCALAPPDATA") or "").strip()
    if root:
        return root
    return os.path.join(os.path.expanduser("~"), "AppData", "Local")


def _default_data_root():
    return os.path.join(_default_local_appdata(), APP_NAME)


def _iter_dir_with_parents(path: str, *, max_depth: int = 6):
    current = os.path.abspath(str(path or "").strip())
    seen = set()
    for _ in range(max(1, int(max_depth or 1))):
        if not current:
            break
        norm = os.path.normcase(os.path.normpath(current))
        if norm in seen:
            break
        seen.add(norm)
        yield current
        parent = os.path.dirname(current)
        if not parent or parent == current:
            break
        current = parent


def _load_update_public_key():
    env_val = str(os.environ.get("GA_LAUNCHER_UPDATE_PUBLIC_KEY_PEM") or "").strip()
    if env_val:
        return env_val
    for base_dir in _iter_dir_with_parents(APP_DIR, max_depth=6):
        fp = os.path.join(base_dir, "update_public_key.pem")
        try:
            if os.path.isfile(fp):
                with open(fp, "r", encoding="utf-8") as f:
                    text = f.read().strip()
                if text:
                    return text
        except Exception:
            continue
    return ""

LOCAL_APPDATA = _default_local_appdata()
PROGRAMS_ROOT = os.path.join(LOCAL_APPDATA, "Programs", APP_NAME) if IS_WINDOWS else APP_DIR
DATA_ROOT = _default_data_root()
CONFIG_DIR = os.path.join(DATA_ROOT, "config")
CONFIG_PATH = os.path.join(CONFIG_DIR, "launcher_config.json")
LEGACY_CONFIG_PATH = os.path.join(APP_DIR, "launcher_config.json")
STATE_DIR = os.path.join(DATA_ROOT, "state")
CURRENT_STATE_PATH = os.path.join(STATE_DIR, "current.json")
UPDATES_DIR = os.path.join(DATA_ROOT, "updates")
UPDATE_JOBS_DIR = os.path.join(UPDATES_DIR, "jobs")
UPDATE_DOWNLOADS_DIR = os.path.join(UPDATES_DIR, "downloads")
UPDATE_STAGING_DIR = os.path.join(UPDATES_DIR, "staging")
UPDATE_LOG_PATH = os.path.join(UPDATES_DIR, "updater.log")
VERSIONS_DIR = os.path.join(PROGRAMS_ROOT, "app", "versions")
BOOTSTRAP_EXE_NAME = "LauncherBootstrap.exe" if IS_WINDOWS else ""
MAIN_EXE_NAME = "GenericAgentLauncher.exe" if IS_WINDOWS else "GenericAgentLauncher"
UPDATER_EXE_NAME = "Updater.exe" if IS_WINDOWS else ""
UPDATE_SIGNING_PUBLIC_KEY_PEM = _load_update_public_key()
WX_BOT_API = "https://ilinkai.weixin.qq.com"
WX_TOKEN_PATH = os.path.join(os.path.expanduser("~"), ".wxbot", "token.json")
TOKEN_ESTIMATE_DIVISOR = 2.5
TOKEN_USAGE_VERSION = 2

COLOR_ACCENT = "#4f8cff"
COLOR_ACCENT_HOVER = "#3a75e0"
COLOR_APP_BG = ("#f4f7fb", "#1c1e22")
COLOR_PANEL = ("#ffffff", "#23262c")
COLOR_SURFACE = ("#ffffff", "#1c1e22")
COLOR_SIDEBAR_BG = ("#eef2f7", "#181a1e")
COLOR_CARD = ("#ffffff", "#2a2d33")
COLOR_CARD_HOVER = ("#e8edf6", "#34383f")
COLOR_FIELD_BG = ("#ffffff", "#14161a")
COLOR_FIELD_ALT = ("#f3f6fb", "#262a31")
COLOR_ACTIVE = ("#dbe7ff", "#2d3544")
COLOR_ACTIVE_HOVER = ("#cfdcf7", "#34405a")
COLOR_TEXT = ("#1f2937", "#e8ecf2")
COLOR_TEXT_SOFT = ("#3f4957", "#cfd4dc")
COLOR_MUTED = ("#6b7280", "#8a8f99")
COLOR_DIVIDER = ("#d7deea", "#3a3f47")
COLOR_DANGER_TEXT = ("#b94a4a", "#ea7070")
COLOR_DANGER_BG = ("#dc6666", "#c24848")
COLOR_DANGER_BG_HOVER = ("#c85757", "#a13a3a")
COLOR_CODE_BG = ("#f4f7fb", "#14161a")
COLOR_CODE_TEXT = ("#253041", "#dde1e7")
