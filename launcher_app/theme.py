"""Fluent / Win11 design system for the GenericAgent launcher.

Pure PySide6. Supports runtime theme switching between dark and light
palettes through the mutable `C` dict and `set_theme()` helper.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import byref, c_int

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget

from launcher_app import core as lz


def _lz(value, index: int, fallback: str) -> str:
    if isinstance(value, (tuple, list)) and len(value) > index:
        return str(value[index])
    if isinstance(value, str) and value:
        return value
    return fallback


def _lz_light(value, fallback: str) -> str:
    return _lz(value, 0, fallback)


def _lz_dark(value, fallback: str) -> str:
    return _lz(value, 1, fallback)


_DARK_PALETTE: dict = {
    "bg": _lz_dark(lz.COLOR_APP_BG, "#1c1e22"),
    "panel": _lz_dark(lz.COLOR_PANEL, "#23262c"),
    "surface": _lz_dark(lz.COLOR_SURFACE, "#1c1e22"),
    "sidebar_bg": _lz_dark(lz.COLOR_SIDEBAR_BG, "#181a1e"),
    "card": _lz_dark(lz.COLOR_CARD, "#2a2d33"),
    "card_hover": _lz_dark(lz.COLOR_CARD_HOVER, "#34383f"),
    "field_bg": _lz_dark(lz.COLOR_FIELD_BG, "#14161a"),
    "field_alt": _lz_dark(lz.COLOR_FIELD_ALT, "#262a31"),
    "border": _lz_dark(lz.COLOR_DIVIDER, "#3a3f47"),
    "active": _lz_dark(lz.COLOR_ACTIVE, "#2d3544"),
    "active_hover": _lz_dark(lz.COLOR_ACTIVE_HOVER, "#34405a"),
    "code_bg": _lz_dark(lz.COLOR_CODE_BG, "#14161a"),
    "text": _lz_dark(lz.COLOR_TEXT, "#e8ecf2"),
    "text_soft": _lz_dark(lz.COLOR_TEXT_SOFT, "#cfd4dc"),
    "muted": _lz_dark(lz.COLOR_MUTED, "#8a8f99"),
    "code_text": _lz_dark(lz.COLOR_CODE_TEXT, "#dde1e7"),
    "accent": _lz_dark(lz.COLOR_ACCENT, "#4f8cff"),
    "accent_hover": _lz_dark(lz.COLOR_ACCENT_HOVER, "#3a75e0"),
    "danger": _lz_dark(lz.COLOR_DANGER_BG, "#c24848"),
    "danger_hover": _lz_dark(lz.COLOR_DANGER_BG_HOVER, "#a13a3a"),
    "danger_text": _lz_dark(lz.COLOR_DANGER_TEXT, "#ea7070"),

    "layer1": _lz_dark(lz.COLOR_PANEL, "#23262c"),
    "layer2": _lz_dark(lz.COLOR_CARD, "#2a2d33"),
    "layer3": _lz_dark(lz.COLOR_CARD_HOVER, "#34383f"),
    "mica_fallback": _lz_dark(lz.COLOR_APP_BG, "#1c1e22"),
    "bg_subtle": "#1a1c20",

    "stroke_default": "rgba(255,255,255,0.06)",
    "stroke_hover": "rgba(255,255,255,0.12)",
    "stroke_focus": _lz_dark(lz.COLOR_ACCENT, "#4f8cff"),
    "stroke_divider": "rgba(255,255,255,0.05)",

    "accent_pressed": "#2d5db8",
    "accent_disabled": "rgba(79,140,255,0.35)",
    "accent_soft_bg": "rgba(79,140,255,0.12)",
    "accent_soft_bg_hover": "rgba(79,140,255,0.20)",
    "accent_text": "#7db0ff",

    "success": "#6ccf8f",
    "success_soft": "rgba(108,207,143,0.14)",
    "warning": "#f2c661",
    "warning_soft": "rgba(242,198,97,0.14)",
    "error": "#ea7070",
    "error_soft": "rgba(234,112,112,0.14)",

    "shadow_rgba_1": "rgba(0,0,0,0.20)",
    "shadow_rgba_2": "rgba(0,0,0,0.30)",
    "shadow_rgba_3": "rgba(0,0,0,0.45)",

    "user_row_bg": "rgba(255,255,255,0.03)",
    "avatar_bg": "rgba(255,255,255,0.04)",
    "avatar_stroke": "rgba(255,255,255,0.10)",
    "user_avatar_color": "#c8c8d0",
    "bot_avatar_color": "#9eb4d0",

    "selection_bg": "rgba(79,140,255,0.40)",
    "selection_fg": "white",
    "scrollbar_thumb": "rgba(148,163,184,0.28)",
    "scrollbar_thumb_hover": "rgba(148,163,184,0.50)",
    "scrollbar_thumb_pressed": "rgba(148,163,184,0.70)",

    "is_dark": True,
}


_LIGHT_PALETTE: dict = {
    "bg": _lz_light(lz.COLOR_APP_BG, "#f3f5f9"),
    "panel": _lz_light(lz.COLOR_PANEL, "#ffffff"),
    "surface": _lz_light(lz.COLOR_SURFACE, "#ffffff"),
    "sidebar_bg": _lz_light(lz.COLOR_SIDEBAR_BG, "#f7f8fb"),
    "card": _lz_light(lz.COLOR_CARD, "#ffffff"),
    "card_hover": _lz_light(lz.COLOR_CARD_HOVER, "#f0f3f9"),
    "field_bg": _lz_light(lz.COLOR_FIELD_BG, "#ffffff"),
    "field_alt": _lz_light(lz.COLOR_FIELD_ALT, "#f3f5f9"),
    "border": _lz_light(lz.COLOR_DIVIDER, "#d7deea"),
    "active": _lz_light(lz.COLOR_ACTIVE, "#dbe7ff"),
    "active_hover": _lz_light(lz.COLOR_ACTIVE_HOVER, "#cfdcf7"),
    "code_bg": _lz_light(lz.COLOR_CODE_BG, "#f4f7fb"),
    "text": _lz_light(lz.COLOR_TEXT, "#1a1f2b"),
    "text_soft": _lz_light(lz.COLOR_TEXT_SOFT, "#3f4957"),
    "muted": _lz_light(lz.COLOR_MUTED, "#6b7280"),
    "code_text": _lz_light(lz.COLOR_CODE_TEXT, "#253041"),
    "accent": _lz_light(lz.COLOR_ACCENT, "#4f8cff"),
    "accent_hover": _lz_light(lz.COLOR_ACCENT_HOVER, "#3a75e0"),
    "danger": _lz_light(lz.COLOR_DANGER_BG, "#dc6666"),
    "danger_hover": _lz_light(lz.COLOR_DANGER_BG_HOVER, "#c85757"),
    "danger_text": _lz_light(lz.COLOR_DANGER_TEXT, "#b94a4a"),

    "layer1": "#ffffff",
    "layer2": "#f6f8fc",
    "layer3": "#eef1f7",
    "mica_fallback": "#f3f5f9",
    "bg_subtle": "#ecf0f6",

    "stroke_default": "rgba(0,0,0,0.06)",
    "stroke_hover": "rgba(0,0,0,0.12)",
    "stroke_focus": _lz_light(lz.COLOR_ACCENT, "#4f8cff"),
    "stroke_divider": "rgba(0,0,0,0.06)",

    "accent_pressed": "#2d5db8",
    "accent_disabled": "rgba(79,140,255,0.35)",
    "accent_soft_bg": "rgba(79,140,255,0.10)",
    "accent_soft_bg_hover": "rgba(79,140,255,0.18)",
    "accent_text": "#1e4ea8",

    "success": "#3ea35f",
    "success_soft": "rgba(62,163,95,0.14)",
    "warning": "#c78a1a",
    "warning_soft": "rgba(199,138,26,0.14)",
    "error": "#c24848",
    "error_soft": "rgba(194,72,72,0.12)",

    "shadow_rgba_1": "rgba(15,23,42,0.08)",
    "shadow_rgba_2": "rgba(15,23,42,0.14)",
    "shadow_rgba_3": "rgba(15,23,42,0.22)",

    "user_row_bg": "rgba(15,23,42,0.035)",
    "avatar_bg": "rgba(15,23,42,0.04)",
    "avatar_stroke": "rgba(15,23,42,0.10)",
    "user_avatar_color": "#4f5766",
    "bot_avatar_color": "#2d5db8",

    "selection_bg": "rgba(79,140,255,0.25)",
    "selection_fg": "#1a1f2b",
    "scrollbar_thumb": "rgba(15,23,42,0.18)",
    "scrollbar_thumb_hover": "rgba(15,23,42,0.32)",
    "scrollbar_thumb_pressed": "rgba(15,23,42,0.48)",

    "is_dark": False,
}


C: dict = {}
C.update(_LIGHT_PALETTE)


F: dict = {
    "font_family": '"Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif',
    "font_family_mono": '"Cascadia Mono", "Cascadia Code", Consolas, "Courier New", monospace',

    "font_caption": 12,
    "font_body": 14,
    "font_body_strong": 14,
    "font_subtitle": 16,
    "font_title": 20,
    "font_display": 28,

    "radius_xs": 4,
    "radius_sm": 6,
    "radius_md": 8,
    "radius_lg": 12,
    "radius_xl": 16,

    "spacing_xs": 4,
    "spacing_sm": 8,
    "spacing_md": 12,
    "spacing_lg": 16,
    "spacing_xl": 24,

    "button_h": 32,
    "input_h": 32,
    "topbar_h": 56,
}


def set_theme(mode: str) -> str:
    normalized = "light" if str(mode or "").strip().lower() == "light" else "dark"
    src = _LIGHT_PALETTE if normalized == "light" else _DARK_PALETTE
    C.clear()
    C.update(src)
    return normalized


def current_mode() -> str:
    return "light" if not C.get("is_dark", True) else "dark"


def build_qss() -> str:
    text = C["text"]
    text_soft = C["text_soft"]
    muted = C["muted"]
    accent = C["accent"]
    accent_pressed = C["accent_pressed"]
    accent_disabled = C["accent_disabled"]
    accent_soft = C["accent_soft_bg"]
    accent_soft_hover = C["accent_soft_bg_hover"]
    stroke = C["stroke_default"]
    stroke_hover = C["stroke_hover"]
    stroke_divider = C["stroke_divider"]
    focus = C["stroke_focus"]
    font = F["font_family"]
    fs = F["font_body"]
    r_md = F["radius_md"]
    r_lg = F["radius_lg"]
    field_bg = C["field_bg"]
    layer1 = C["layer1"]
    layer2 = C["layer2"]
    layer3 = C["layer3"]
    mica_fb = C["mica_fallback"]
    sidebar_bg = C["sidebar_bg"]
    danger = C["danger"]
    danger_hover = C["danger_hover"]
    danger_soft_border = "rgba(234,112,112,0.35)" if C.get("is_dark") else "rgba(194,72,72,0.30)"
    danger_text = C["danger_text"]
    success = C["success"]
    success_soft = C["success_soft"]
    warning = C["warning"]
    warning_soft = C["warning_soft"]
    error = C["error"]
    error_soft = C["error_soft"]
    selection_bg = C["selection_bg"]
    selection_fg = C["selection_fg"]
    sb_thumb = C["scrollbar_thumb"]
    sb_thumb_hover = C["scrollbar_thumb_hover"]
    sb_thumb_pressed = C["scrollbar_thumb_pressed"]
    bg = C["bg"]

    return f"""
    QWidget {{
        color: {text};
        font-family: {font};
        font-size: {fs}px;
    }}
    QMainWindow, QDialog {{
        background: {mica_fb};
    }}
    QToolTip {{
        background: {layer2};
        color: {text};
        border: 1px solid {stroke_hover};
        border-radius: 4px;
        padding: 4px 8px;
    }}

    QPushButton {{
        background: {layer2};
        color: {text};
        border: 1px solid {stroke};
        border-radius: {r_md}px;
        padding: 6px 14px;
        font-size: {fs}px;
    }}
    QPushButton:hover {{
        background: {layer3};
        border-color: {stroke_hover};
    }}
    QPushButton:pressed {{
        background: {layer1};
        border-color: {stroke};
    }}
    QPushButton:disabled {{
        background: {layer1};
        color: {muted};
        border-color: {stroke};
    }}
    QPushButton:focus {{ outline: none; }}

    QLineEdit, QTextEdit, QPlainTextEdit {{
        background: {field_bg};
        color: {text};
        border: 1px solid {stroke};
        border-radius: {r_md}px;
        padding: 7px 10px;
        selection-background-color: {selection_bg};
        selection-color: {selection_fg};
    }}
    QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover {{
        border-color: {stroke_hover};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
        border-color: {focus};
    }}
    QLineEdit:disabled, QTextEdit:disabled {{
        color: {muted};
    }}

    QComboBox {{
        background: {field_bg};
        color: {text};
        border: 1px solid {stroke};
        border-radius: {r_md}px;
        padding: 6px 10px;
        min-height: 20px;
    }}
    QComboBox:hover {{ border-color: {stroke_hover}; }}
    QComboBox:focus {{ border-color: {focus}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background: {layer1};
        color: {text};
        border: 1px solid {stroke_hover};
        border-radius: {r_md}px;
        padding: 4px;
        outline: 0;
        selection-background-color: {accent_soft};
        selection-color: {text};
    }}

    QListWidget {{
        background: transparent;
        border: none;
        outline: none;
        padding: 4px;
    }}
    QListWidget::item {{
        background: transparent;
        border: 1px solid transparent;
        border-left: 2px solid transparent;
        border-radius: {r_md}px;
        padding: 9px 12px 9px 10px;
        margin: 2px 6px;
        color: {text_soft};
    }}
    QListWidget::item:hover {{
        background: {accent_soft};
        color: {text};
    }}
    QListWidget::item:selected {{
        background: {accent_soft_hover};
        color: {text};
        border-left: 2px solid {accent};
    }}

    QCheckBox {{ color: {text}; spacing: 8px; }}
    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border: 1px solid {stroke_hover};
        border-radius: 4px;
        background: {field_bg};
    }}
    QCheckBox::indicator:hover {{ border-color: {accent}; }}
    QCheckBox::indicator:checked {{
        background: {accent};
        border-color: {accent};
    }}
    QCheckBox::indicator:disabled {{
        border-color: {stroke};
        background: {layer1};
    }}

    QScrollBar:vertical {{
        width: 10px;
        background: transparent;
        border: none;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {sb_thumb};
        border-radius: 4px;
        min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {sb_thumb_hover}; }}
    QScrollBar::handle:vertical:pressed {{ background: {sb_thumb_pressed}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0; background: none;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
    QScrollBar:horizontal {{
        height: 10px;
        background: transparent;
        border: none;
        margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {sb_thumb};
        border-radius: 4px;
        min-width: 28px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {sb_thumb_hover}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0; background: none;
    }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}

    QMenu {{
        background: {layer1};
        color: {text};
        border: 1px solid {stroke_hover};
        border-radius: {r_md}px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 7px 16px;
        border-radius: 4px;
    }}
    QMenu::item:selected {{ background: {accent_soft}; }}
    QMenu::separator {{
        height: 1px;
        background: {stroke};
        margin: 4px 6px;
    }}

    QProgressBar {{
        background: {layer2};
        border: none;
        border-radius: 4px;
        text-align: center;
        color: {text_soft};
        font-size: 12px;
        min-height: 8px;
        max-height: 8px;
    }}
    QProgressBar::chunk {{
        background: {accent};
        border-radius: 4px;
    }}

    QSplitter::handle {{ background: {stroke_divider}; }}
    QSplitter::handle:horizontal {{ width: 1px; }}
    QSplitter::handle:vertical {{ height: 1px; }}

    /* ====== App-specific roles via objectName ====== */
    QFrame#chatSidebar {{
        background: {sidebar_bg};
        border: none;
        border-right: 1px solid {stroke_divider};
    }}
    QFrame#chatMain {{
        background: {bg};
        border: none;
    }}
    QFrame#chatHead {{
        background: {layer1};
        border: none;
        border-bottom: 1px solid {stroke_divider};
    }}
    QFrame#chatComposer {{
        background: {layer1};
        border: none;
        border-radius: {r_lg}px;
    }}
    QFrame#panelCard {{
        background: {layer1};
        border: 1px solid {stroke};
        border-radius: {r_lg}px;
    }}
    QFrame#cardInset {{
        background: {layer2};
        border: none;
        border-radius: {r_md}px;
    }}
    QFrame#settingsTopbar, QFrame#setupTopbar {{
        background: {layer1};
        border: none;
        border-bottom: 1px solid {stroke_divider};
    }}
    QFrame#settingsNav {{
        background: {sidebar_bg};
        border: none;
        border-right: 1px solid {stroke_divider};
    }}
    QFrame#settingsBody {{
        background: {bg};
        border: none;
    }}
    QFrame#recentCard, QFrame#statusCard {{
        background: {accent_soft};
        border: none;
        border-radius: {r_lg}px;
    }}
    QFrame#turnFold {{
        background: {layer2};
        border: none;
        border-radius: {r_md}px;
    }}
    QPushButton#turnFoldHeader {{
        background: transparent;
        color: {text_soft};
        border: 1px solid transparent;
        border-radius: 6px;
        text-align: left;
        padding: 7px 10px;
        font-size: 12px;
        font-weight: 600;
    }}
    QPushButton#turnFoldHeader:hover {{
        background: {layer3};
        color: {text};
    }}
    QFrame#optionCard {{
        background: {layer1};
        border: 1px solid {stroke};
        border-radius: {r_lg}px;
    }}
    QFrame#optionCard:hover {{
        background: {layer2};
        border-color: {accent};
    }}
    QFrame#userBubble {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {accent}, stop:1 {accent_pressed});
        border: none;
        border-radius: {r_lg}px;
    }}
    QLabel#userBubbleText {{
        color: white;
        background: transparent;
        border: none;
        font-size: 14px;
    }}
    QPushButton#sendBtn {{
        background: {accent};
        color: white;
        border: 1px solid {accent};
        border-radius: {r_md}px;
        padding: 6px 18px;
        font-size: 14px;
        font-weight: 600;
    }}
    QPushButton#sendBtn:hover {{
        background: {C['accent_hover']};
        border-color: {C['accent_hover']};
    }}
    QPushButton#sendBtn:pressed {{
        background: {accent_pressed};
        border-color: {accent_pressed};
    }}
    QPushButton#sendBtn:disabled {{
        background: {accent_disabled};
        border-color: {accent_disabled};
        color: rgba(255,255,255,0.70);
    }}
    QPushButton#stopBtn {{
        background: transparent;
        color: {danger_text};
        border: 1px solid transparent;
        border-radius: {r_md}px;
        padding: 6px 14px;
        font-size: 13px;
        font-weight: 500;
    }}
    QPushButton#stopBtn:hover {{
        background: {error_soft};
        border-color: {danger_soft_border};
    }}
    QPushButton#stopBtn:disabled {{
        color: {muted};
        background: transparent;
        border-color: transparent;
    }}
    QLabel#titleDisplay {{
        color: {text};
        font-size: {F['font_display']}px;
        font-weight: 600;
        background: transparent;
    }}
    QLabel#titleSubtitle {{
        color: {muted};
        font-size: {F['font_body']}px;
        background: transparent;
    }}
    QLabel#cardTitle {{
        color: {text};
        font-size: {F['font_subtitle']}px;
        font-weight: 600;
        background: transparent;
    }}
    QLabel#cardDesc {{
        color: {muted};
        font-size: 13px;
        background: transparent;
    }}
    QLabel#sectionLabel {{
        color: {muted};
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 1px;
        background: transparent;
    }}
    QLabel#accentLabel {{
        color: {C['accent_text']};
        font-size: 11px;
        font-weight: 600;
        background: transparent;
    }}
    QLabel#bodyText {{
        color: {text};
        font-size: 13px;
        background: transparent;
    }}
    QLabel#softText {{
        color: {text_soft};
        font-size: 13px;
        background: transparent;
    }}
    QLabel#softTextSmall {{
        color: {text_soft};
        font-size: 12px;
        background: transparent;
    }}
    QLabel#mutedText {{
        color: {muted};
        font-size: 12px;
        background: transparent;
    }}
    QLabel#optionIcon {{
        font-size: 26px;
        background: transparent;
    }}
    QLabel#optionArrow {{
        color: {muted};
        font-size: 22px;
        font-weight: 600;
        background: transparent;
    }}
    QLabel#sidebarLogo {{
        background: {accent_soft};
        color: {C['accent_text']};
        border-radius: 10px;
        font-size: 22px;
        font-weight: 600;
    }}
    QWidget#userMsgRow {{
        background: {C['user_row_bg']};
    }}
    QWidget#botMsgRow {{
        background: transparent;
    }}
    QLabel#msgAvatar {{
        background: {C['avatar_bg']};
        border: 1px solid {C['avatar_stroke']};
        border-radius: 15px;
    }}
    QLabel#msgRoleLabel {{
        color: {text_soft};
        font-size: 12px;
        font-weight: 700;
        background: transparent;
    }}
    QLabel#userMsgText {{
        background: transparent;
        color: {text};
        padding: 2px 0;
        font-size: 14px;
    }}
    QTextBrowser#botMsgBrowser {{
        background: transparent;
        color: {text};
        border: none;
        padding: 0;
        font-size: 14px;
    }}
    QPushButton#msgActionBtn {{
        background: transparent;
        border: none;
        border-radius: 4px;
        padding: 3px;
    }}
    QPushButton#msgActionBtn:hover {{
        background: {layer2};
    }}
    QFrame#msgSeparator {{
        background: {C['stroke_divider']};
        border: none;
    }}
    QPushButton#infoBtn {{
        background: transparent;
        border: 1px solid {stroke};
        border-radius: 13px;
        padding: 0;
    }}
    QPushButton#infoBtn:hover {{
        background: {layer2};
        border-color: {stroke_hover};
    }}
    QLabel#optionTitle {{
        color: {text};
        font-size: {F['font_body']}px;
        font-weight: 600;
        background: transparent;
    }}
    QLabel#optionDesc {{
        color: {muted};
        font-size: 12px;
        background: transparent;
    }}
    QLabel#pathValue {{
        background: {layer2};
        color: {text};
        border: none;
        border-radius: {r_md}px;
        padding: 7px 10px;
        font-size: 13px;
    }}
    QFrame#depRowOk {{
        background: {success_soft};
        border: none;
        border-radius: {r_md}px;
    }}
    QFrame#depRowWarn {{
        background: {warning_soft};
        border: none;
        border-radius: {r_md}px;
    }}
    QFrame#depRowError {{
        background: {error_soft};
        border: none;
        border-radius: {r_md}px;
    }}
    QLabel#depMark[severity="ok"] {{ color: {success}; font-size: 13px; font-weight: 700; background: transparent; }}
    QLabel#depMark[severity="warn"] {{ color: {warning}; font-size: 13px; font-weight: 700; background: transparent; }}
    QLabel#depMark[severity="error"] {{ color: {error}; font-size: 13px; font-weight: 700; background: transparent; }}
    QLabel#depName {{ color: {text}; font-size: 13px; font-weight: 600; background: transparent; }}
    QLabel#depDetail {{ color: {text_soft}; font-size: 12px; background: transparent; }}
    QLabel#tokenTree {{
        color: {muted};
        font-family: Consolas, 'Segoe UI', monospace;
        font-size: 11px;
        background: transparent;
    }}
    """


FLUENT_QSS: str = build_qss()


_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_SYSTEMBACKDROP_TYPE = 38
_DWMSBT_MAINWINDOW = 2


def apply_mica(window: QWidget, *, dark: bool | None = None) -> bool:
    if sys.platform != "win32":
        return False
    if dark is None:
        dark = bool(C.get("is_dark", True))
    try:
        hwnd = int(window.winId())
        dwm = ctypes.windll.dwmapi
        dark_val = c_int(1 if dark else 0)
        dwm.DwmSetWindowAttribute(
            hwnd,
            _DWMWA_USE_IMMERSIVE_DARK_MODE,
            byref(dark_val),
            ctypes.sizeof(dark_val),
        )
        backdrop_val = c_int(_DWMSBT_MAINWINDOW)
        res = dwm.DwmSetWindowAttribute(
            hwnd,
            _DWMWA_SYSTEMBACKDROP_TYPE,
            byref(backdrop_val),
            ctypes.sizeof(backdrop_val),
        )
        return res == 0
    except Exception:
        return False


def apply_fluent_shadow(widget: QWidget, level: int = 1) -> QGraphicsDropShadowEffect:
    effect = QGraphicsDropShadowEffect(widget)
    if level <= 1:
        effect.setBlurRadius(12)
        effect.setOffset(0, 2)
        effect.setColor(QColor(0, 0, 0, 50))
    elif level == 2:
        effect.setBlurRadius(24)
        effect.setOffset(0, 4)
        effect.setColor(QColor(0, 0, 0, 70))
    else:
        effect.setBlurRadius(40)
        effect.setOffset(0, 8)
        effect.setColor(QColor(0, 0, 0, 100))
    widget.setGraphicsEffect(effect)
    return effect
