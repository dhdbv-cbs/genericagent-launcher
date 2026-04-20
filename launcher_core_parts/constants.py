from __future__ import annotations

import os
import sys

REPO_URL = "https://github.com/lsdefine/GenericAgent"
APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
CONFIG_PATH = os.path.join(APP_DIR, "launcher_config.json")
WX_BOT_API = "https://ilinkai.weixin.qq.com"
WX_TOKEN_PATH = os.path.join(os.path.expanduser("~"), ".wxbot", "token.json")
TOKEN_ESTIMATE_DIVISOR = 2.5
TOKEN_USAGE_VERSION = 1

FONT_TITLE = ("Microsoft YaHei UI", 22, "bold")
FONT_SUB = ("Microsoft YaHei UI", 13)
FONT_BODY = ("Microsoft YaHei UI", 12)
FONT_BTN = ("Microsoft YaHei UI", 13, "bold")
FONT_SMALL = ("Microsoft YaHei UI", 10)
FONT_MONO = ("Consolas", 10)

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
