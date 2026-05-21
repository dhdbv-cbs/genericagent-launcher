"""Fluent / Win11 design system for the GenericAgent launcher.

Pure PySide6. Supports runtime theme switching between dark and light
palettes through the mutable `C` dict and `set_theme()` helper.
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import byref, c_int

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QToolTip, QWidget

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
    "bg": "#17181b",
    "panel": "#1d1f23",
    "surface": "#1a1c20",
    "sidebar_bg": "#15171a",
    "card": "#202329",
    "card_hover": "#262a31",
    "field_bg": "#121418",
    "field_alt": "#1c2027",
    "border": "#343943",
    "active": "#252a33",
    "active_hover": "#2b323d",
    "code_bg": "#121418",
    "text": "#e6e8ee",
    "text_soft": "#c7ccd5",
    "muted": "#8b919c",
    "code_text": "#dde2ea",
    "accent": "#90a2b5",
    "accent_hover": "#7d90a4",
    "danger": "#b85f5c",
    "danger_hover": "#a75250",
    "danger_text": "#e08a86",

    "layer1": "#1d1f23",
    "layer2": "#23262d",
    "layer3": "#2b2f37",
    "mica_fallback": "#17181b",
    "bg_subtle": "#14161a",

    "stroke_default": "rgba(255,255,255,0.08)",
    "stroke_hover": "rgba(255,255,255,0.14)",
    "stroke_focus": "#90a2b5",
    "stroke_divider": "rgba(255,255,255,0.06)",

    "accent_pressed": "#67798c",
    "accent_disabled": "rgba(144,162,181,0.28)",
    "accent_soft_bg": "rgba(144,162,181,0.12)",
    "accent_soft_bg_hover": "rgba(144,162,181,0.18)",
    "accent_text": "#b2c0cf",

    "success": "#6ccf8f",
    "success_soft": "rgba(108,207,143,0.14)",
    "warning": "#f2c661",
    "warning_soft": "rgba(242,198,97,0.14)",
    "error": "#ea7070",
    "error_soft": "rgba(234,112,112,0.14)",

    "shadow_rgba_1": "rgba(0,0,0,0.20)",
    "shadow_rgba_2": "rgba(0,0,0,0.30)",
    "shadow_rgba_3": "rgba(0,0,0,0.45)",

    "user_row_bg": "rgba(255,255,255,0.02)",
    "avatar_bg": "rgba(255,255,255,0.03)",
    "avatar_stroke": "rgba(255,255,255,0.08)",
    "user_avatar_color": "#d1d5dc",
    "bot_avatar_color": "#a6b6c7",

    "selection_bg": "rgba(144,162,181,0.34)",
    "selection_fg": "white",
    "scrollbar_thumb": "rgba(148,163,184,0.28)",
    "scrollbar_thumb_hover": "rgba(148,163,184,0.50)",
    "scrollbar_thumb_pressed": "rgba(148,163,184,0.70)",

    "is_dark": True,
}


_LIGHT_PALETTE: dict = {
    "bg": "#f4f2ee",
    "panel": "#fcfbf8",
    "surface": "#fbfaf7",
    "sidebar_bg": "#efede8",
    "card": "#fcfbf8",
    "card_hover": "#f2efe9",
    "field_bg": "#ffffff",
    "field_alt": "#f1eeea",
    "border": "#d8d2c9",
    "active": "#ebe6de",
    "active_hover": "#e2ddd5",
    "code_bg": "#f5f2ed",
    "text": "#20242b",
    "text_soft": "#4e5561",
    "muted": "#757b85",
    "code_text": "#2d3440",
    "accent": "#5f7288",
    "accent_hover": "#536479",
    "danger": "#c96b67",
    "danger_hover": "#b55d5a",
    "danger_text": "#9f4d49",

    "layer1": "#fcfbf8",
    "layer2": "#f3f0eb",
    "layer3": "#ece8e1",
    "mica_fallback": "#f4f2ee",
    "bg_subtle": "#ece8e1",

    "stroke_default": "rgba(36,41,47,0.08)",
    "stroke_hover": "rgba(36,41,47,0.14)",
    "stroke_focus": "#5f7288",
    "stroke_divider": "rgba(36,41,47,0.08)",

    "accent_pressed": "#46576a",
    "accent_disabled": "rgba(95,114,136,0.30)",
    "accent_soft_bg": "rgba(95,114,136,0.10)",
    "accent_soft_bg_hover": "rgba(95,114,136,0.16)",
    "accent_text": "#516273",

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
    "avatar_bg": "rgba(15,23,42,0.035)",
    "avatar_stroke": "rgba(15,23,42,0.08)",
    "user_avatar_color": "#4f5766",
    "bot_avatar_color": "#617487",

    "selection_bg": "rgba(95,114,136,0.22)",
    "selection_fg": "#1a1f2b",
    "scrollbar_thumb": "rgba(15,23,42,0.18)",
    "scrollbar_thumb_hover": "rgba(15,23,42,0.32)",
    "scrollbar_thumb_pressed": "rgba(15,23,42,0.48)",

    "is_dark": False,
}


C: dict = {}
C.update(_LIGHT_PALETTE)


DEFAULT_THEME_VISUAL_PRESET = "graphite"
_FALLBACK_THEME_VISUAL_PRESET = "paper"

_VISUAL_PRESET_OPTIONS: tuple[tuple[str, str], ...] = (
    ("graphite", "石墨"),
    ("paper", "静纸"),
    ("mist", "雾钛"),
)

_LEGACY_BG_PRESET_TO_VISUAL: dict[str, str] = {
    "default": DEFAULT_THEME_VISUAL_PRESET,
    "warm": _FALLBACK_THEME_VISUAL_PRESET,
    "mist": "mist",
    "graphite": "graphite",
}

_MIST_LIGHT_PALETTE: dict = {
    "bg": "#eef2f6",
    "panel": "#f8fafc",
    "surface": "#f6f8fb",
    "sidebar_bg": "#e8edf3",
    "card": "#fafbfd",
    "card_hover": "#eff3f7",
    "field_bg": "#ffffff",
    "field_alt": "#edf2f7",
    "border": "#d3dae4",
    "active": "#dfe7f0",
    "active_hover": "#d6dee8",
    "code_bg": "#eef3f8",
    "text": "#1e2631",
    "text_soft": "#4b5968",
    "muted": "#718092",
    "code_text": "#2a3747",
    "accent": "#607a92",
    "accent_hover": "#536b80",
    "layer1": "#f8fafc",
    "layer2": "#eef3f7",
    "layer3": "#e6ebf1",
    "mica_fallback": "#eef2f6",
    "bg_subtle": "#e6ebf1",
    "stroke_default": "rgba(30,38,49,0.08)",
    "stroke_hover": "rgba(30,38,49,0.14)",
    "stroke_focus": "#607a92",
    "stroke_divider": "rgba(30,38,49,0.08)",
    "accent_pressed": "#475b6d",
    "accent_disabled": "rgba(96,122,146,0.30)",
    "accent_soft_bg": "rgba(96,122,146,0.11)",
    "accent_soft_bg_hover": "rgba(96,122,146,0.17)",
    "accent_text": "#4b6277",
    "user_row_bg": "rgba(20,32,48,0.032)",
    "avatar_bg": "rgba(20,32,48,0.036)",
    "avatar_stroke": "rgba(20,32,48,0.08)",
    "user_avatar_color": "#4f5d6c",
    "bot_avatar_color": "#647d95",
    "selection_bg": "rgba(96,122,146,0.21)",
    "selection_fg": "#17202b",
    "scrollbar_thumb": "rgba(30,38,49,0.16)",
    "scrollbar_thumb_hover": "rgba(30,38,49,0.28)",
    "scrollbar_thumb_pressed": "rgba(30,38,49,0.42)",
}

_MIST_DARK_PALETTE: dict = {
    "bg": "#161a21",
    "panel": "#1b2028",
    "surface": "#181d25",
    "sidebar_bg": "#14181f",
    "card": "#202631",
    "card_hover": "#262d39",
    "field_bg": "#12161d",
    "field_alt": "#1b2330",
    "border": "#36404d",
    "active": "#24303d",
    "active_hover": "#2a3644",
    "code_bg": "#121821",
    "text": "#e6ebf2",
    "text_soft": "#c3ccd8",
    "muted": "#8d97a5",
    "code_text": "#dce5ef",
    "accent": "#8ca3bb",
    "accent_hover": "#7d93aa",
    "layer1": "#1b2028",
    "layer2": "#222935",
    "layer3": "#29313e",
    "mica_fallback": "#161a21",
    "bg_subtle": "#141922",
    "stroke_default": "rgba(255,255,255,0.08)",
    "stroke_hover": "rgba(255,255,255,0.15)",
    "stroke_focus": "#8ca3bb",
    "stroke_divider": "rgba(255,255,255,0.07)",
    "accent_pressed": "#687d92",
    "accent_disabled": "rgba(140,163,187,0.30)",
    "accent_soft_bg": "rgba(140,163,187,0.14)",
    "accent_soft_bg_hover": "rgba(140,163,187,0.20)",
    "accent_text": "#aec0d2",
    "user_row_bg": "rgba(255,255,255,0.025)",
    "avatar_bg": "rgba(255,255,255,0.030)",
    "avatar_stroke": "rgba(255,255,255,0.08)",
    "user_avatar_color": "#d0d7e0",
    "bot_avatar_color": "#a6bbd0",
    "selection_bg": "rgba(140,163,187,0.34)",
    "selection_fg": "#ffffff",
    "scrollbar_thumb": "rgba(140,163,187,0.28)",
    "scrollbar_thumb_hover": "rgba(140,163,187,0.44)",
    "scrollbar_thumb_pressed": "rgba(140,163,187,0.62)",
}

_GRAPHITE_LIGHT_PALETTE: dict = {
    "bg": "#eceff2",
    "panel": "#f7f8fa",
    "surface": "#f5f6f8",
    "sidebar_bg": "#e3e6ea",
    "card": "#fafbfc",
    "card_hover": "#edf0f3",
    "field_bg": "#ffffff",
    "field_alt": "#eceff3",
    "border": "#ccd2d9",
    "active": "#dde1e7",
    "active_hover": "#d3d8df",
    "code_bg": "#edf0f4",
    "text": "#1d232b",
    "text_soft": "#4b5562",
    "muted": "#727b87",
    "code_text": "#2b333e",
    "accent": "#5b6877",
    "accent_hover": "#505c6b",
    "layer1": "#f7f8fa",
    "layer2": "#edf0f3",
    "layer3": "#e6eaee",
    "mica_fallback": "#eceff2",
    "bg_subtle": "#e6eaee",
    "stroke_default": "rgba(29,35,43,0.08)",
    "stroke_hover": "rgba(29,35,43,0.14)",
    "stroke_focus": "#5b6877",
    "stroke_divider": "rgba(29,35,43,0.08)",
    "accent_pressed": "#444f5c",
    "accent_disabled": "rgba(91,104,119,0.30)",
    "accent_soft_bg": "rgba(91,104,119,0.10)",
    "accent_soft_bg_hover": "rgba(91,104,119,0.16)",
    "accent_text": "#495563",
    "user_row_bg": "rgba(15,23,42,0.030)",
    "avatar_bg": "rgba(15,23,42,0.032)",
    "avatar_stroke": "rgba(15,23,42,0.08)",
    "user_avatar_color": "#4d5561",
    "bot_avatar_color": "#647180",
    "selection_bg": "rgba(91,104,119,0.20)",
    "selection_fg": "#161c24",
    "scrollbar_thumb": "rgba(29,35,43,0.18)",
    "scrollbar_thumb_hover": "rgba(29,35,43,0.30)",
    "scrollbar_thumb_pressed": "rgba(29,35,43,0.46)",
}

_GRAPHITE_DARK_PALETTE: dict = {
    "bg": "#121418",
    "panel": "#171a1f",
    "surface": "#15181d",
    "sidebar_bg": "#0f1216",
    "card": "#1b1f24",
    "card_hover": "#22272d",
    "field_bg": "#0d1014",
    "field_alt": "#171c23",
    "border": "#313840",
    "active": "#20252d",
    "active_hover": "#272d36",
    "code_bg": "#0f1318",
    "text": "#e5e8ec",
    "text_soft": "#c4cad3",
    "muted": "#87909b",
    "code_text": "#d9dee6",
    "accent": "#8f9cab",
    "accent_hover": "#7f8b99",
    "layer1": "#171a1f",
    "layer2": "#1d2127",
    "layer3": "#252a31",
    "mica_fallback": "#121418",
    "bg_subtle": "#101317",
    "stroke_default": "rgba(255,255,255,0.08)",
    "stroke_hover": "rgba(255,255,255,0.14)",
    "stroke_focus": "#8f9cab",
    "stroke_divider": "rgba(255,255,255,0.06)",
    "accent_pressed": "#697481",
    "accent_disabled": "rgba(143,156,171,0.28)",
    "accent_soft_bg": "rgba(143,156,171,0.12)",
    "accent_soft_bg_hover": "rgba(143,156,171,0.18)",
    "accent_text": "#b0bcc8",
    "user_row_bg": "rgba(255,255,255,0.020)",
    "avatar_bg": "rgba(255,255,255,0.028)",
    "avatar_stroke": "rgba(255,255,255,0.08)",
    "user_avatar_color": "#cfd4dc",
    "bot_avatar_color": "#aab6c3",
    "selection_bg": "rgba(143,156,171,0.32)",
    "selection_fg": "#ffffff",
    "scrollbar_thumb": "rgba(143,156,171,0.26)",
    "scrollbar_thumb_hover": "rgba(143,156,171,0.42)",
    "scrollbar_thumb_pressed": "rgba(143,156,171,0.60)",
}

_VISUAL_PRESETS: dict[str, dict[str, dict]] = {
    "paper": {"light": {}, "dark": {}},
    "mist": {"light": _MIST_LIGHT_PALETTE, "dark": _MIST_DARK_PALETTE},
    "graphite": {"light": _GRAPHITE_LIGHT_PALETTE, "dark": _GRAPHITE_DARK_PALETTE},
}


def _default_ui_font_family(platform_name: str | None = None) -> str:
    name = str(platform_name or sys.platform or "").strip().lower()
    if name == "darwin":
        return '"PingFang SC", "Helvetica Neue", "Arial Unicode MS", sans-serif'
    if name.startswith("win"):
        return '"Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif'
    return '"Noto Sans CJK SC", "Noto Sans", "Source Han Sans SC", "DejaVu Sans", sans-serif'


def _default_mono_font_family(platform_name: str | None = None) -> str:
    name = str(platform_name or sys.platform or "").strip().lower()
    if name == "darwin":
        return '"SF Mono", Menlo, Monaco, "PingFang SC", monospace'
    if name.startswith("win"):
        return '"Cascadia Mono", "Cascadia Code", Consolas, "Courier New", monospace'
    return '"Noto Sans Mono CJK SC", "Noto Sans Mono", "DejaVu Sans Mono", monospace'


def preferred_theme_font_families(platform_name: str | None = None) -> list[str]:
    name = str(platform_name or sys.platform or "").strip().lower()
    if name == "darwin":
        ordered = [
            "PingFang SC",
            "Helvetica Neue",
            "Arial Unicode MS",
            "Segoe UI Variable Text",
            "Segoe UI",
            "Microsoft YaHei UI",
            "Microsoft YaHei",
            "Noto Sans CJK SC",
            "Source Han Sans SC",
        ]
    elif name.startswith("win"):
        ordered = [
            "Segoe UI Variable Text",
            "Segoe UI",
            "Microsoft YaHei UI",
            "Microsoft YaHei",
            "PingFang SC",
            "Noto Sans CJK SC",
            "Source Han Sans SC",
        ]
    else:
        ordered = [
            "Noto Sans CJK SC",
            "Noto Sans",
            "Source Han Sans SC",
            "DejaVu Sans",
            "PingFang SC",
            "Segoe UI",
            "Microsoft YaHei UI",
            "Microsoft YaHei",
        ]
    seen = set()
    result = []
    for item in ordered:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


F: dict = {
    "font_family": _default_ui_font_family(),
    "font_family_mono": _default_mono_font_family(),

    "font_caption": 12,
    "font_body": 14,
    "font_body_strong": 14,
    "font_subtitle": 16,
    "font_title": 20,
    "font_display": 28,

    "radius_xs": 4,
    "radius_sm": 8,
    "radius_md": 10,
    "radius_lg": 14,
    "radius_xl": 18,

    "spacing_xs": 4,
    "spacing_sm": 8,
    "spacing_md": 12,
    "spacing_lg": 16,
    "spacing_xl": 24,

    "button_h": 34,
    "input_h": 34,
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


_VISUAL_PREFS: dict = {
    "font_family": "",
    "font_weight": 400,
    "font_size": 14,
    "bg_blur": 18,
    "visual_preset": DEFAULT_THEME_VISUAL_PRESET,
    "bg_preset": "default",
    "bg_color": "",
    "bg_image": "",
    "bg_image_mode": "center",
    "use_bg_image": False,
}


def _normalize_font_weight(value) -> int:
    raw = str(value or "").strip().lower()
    mapping = {
        "400": 400,
        "normal": 400,
        "regular": 400,
        "500": 500,
        "medium": 500,
        "600": 600,
        "semibold": 600,
        "semi-bold": 600,
        "700": 700,
        "bold": 700,
    }
    if raw in mapping:
        return mapping[raw]
    try:
        parsed = int(raw)
    except Exception:
        return 400
    return parsed if parsed in (400, 500, 600, 700) else 400


def _normalize_font_size(value) -> int:
    try:
        parsed = int(float(value))
    except Exception:
        parsed = int(F["font_body"] or 14)
    return max(11, min(24, parsed))


def theme_visual_preset_options() -> tuple[tuple[str, str], ...]:
    return _VISUAL_PRESET_OPTIONS


def normalize_theme_visual_preset(value) -> str:
    raw = str(value or "").strip().lower()
    if raw in {item[0] for item in _VISUAL_PRESET_OPTIONS}:
        return raw
    return DEFAULT_THEME_VISUAL_PRESET


def resolve_theme_visual_preset(cfg: dict | None) -> str:
    data = cfg if isinstance(cfg, dict) else {}
    raw = str(data.get("theme_visual_preset") or "").strip()
    if raw:
        return normalize_theme_visual_preset(raw)
    legacy_raw = str(data.get("theme_bg_preset") or "").strip().lower()
    return _LEGACY_BG_PRESET_TO_VISUAL.get(legacy_raw, DEFAULT_THEME_VISUAL_PRESET)


def normalize_theme_background_mode(value) -> str:
    raw = str(value or "").strip().lower()
    if raw == "image":
        return "image"
    return "default"


def _normalize_bg_blur(value) -> int:
    try:
        parsed = int(float(value))
    except Exception:
        parsed = 18
    return max(0, min(100, parsed))


def _normalize_bg_image_mode(value) -> str:
    raw = str(value or "").strip().lower()
    if raw in ("center", "stretch", "tile"):
        return raw
    return "center"


def _format_font_family(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return F["font_family"]
    if "," in raw or raw.startswith('"') or raw.startswith("'"):
        return raw
    escaped = raw.replace('"', '\\"')
    return f'"{escaped}", {F["font_family"]}'


def _visual_palette_for_preset(preset: str) -> dict:
    preset_map = _VISUAL_PRESETS.get(normalize_theme_visual_preset(preset)) or {}
    mode = current_mode()
    palette = preset_map.get(mode) or {}
    return dict(palette)


def _apply_visual_preset(preset: str) -> str:
    normalized = normalize_theme_visual_preset(preset)
    overlay = _visual_palette_for_preset(normalized)
    if overlay:
        C.update(overlay)
    return normalized


def configure_visual_preferences(cfg: dict | None) -> None:
    data = cfg if isinstance(cfg, dict) else {}
    font_family = str(data.get("theme_font_family") or "").strip()
    font_weight = _normalize_font_weight(data.get("theme_font_weight"))
    font_size = _normalize_font_size(data.get("theme_font_size"))
    bg_blur = _normalize_bg_blur(data.get("theme_bg_fade", data.get("theme_bg_blur")))
    visual_preset = resolve_theme_visual_preset(data)
    bg_preset = normalize_theme_background_mode(data.get("theme_bg_preset"))
    bg_image_mode = _normalize_bg_image_mode(data.get("theme_bg_image_mode"))
    raw_image = str(data.get("theme_bg_image") or "").strip()
    resolved_image = lz._resolve_config_path(raw_image) if raw_image else ""
    has_image = bool(resolved_image and os.path.isfile(resolved_image))
    visual_preset = _apply_visual_preset(visual_preset)
    use_bg_image = bool(bg_preset == "image" and has_image)
    bg_color = str(C.get("bg") or "")

    _VISUAL_PREFS["font_family"] = font_family
    _VISUAL_PREFS["font_weight"] = font_weight
    _VISUAL_PREFS["font_size"] = font_size
    _VISUAL_PREFS["bg_blur"] = bg_blur
    _VISUAL_PREFS["visual_preset"] = visual_preset
    _VISUAL_PREFS["bg_preset"] = bg_preset
    _VISUAL_PREFS["bg_color"] = bg_color
    _VISUAL_PREFS["bg_image"] = resolved_image
    _VISUAL_PREFS["bg_image_mode"] = bg_image_mode
    _VISUAL_PREFS["use_bg_image"] = use_bg_image


def has_background_image() -> bool:
    return bool(_VISUAL_PREFS.get("use_bg_image"))


def app_surface_background() -> str:
    return str(_VISUAL_PREFS.get("bg_color") or C["bg"])


def font_body_size() -> int:
    return _normalize_font_size(_VISUAL_PREFS.get("font_size"))


def chat_surface_background() -> str:
    if has_background_image():
        return "transparent"
    return app_surface_background()


def build_tooltip_palette() -> QPalette:
    bg = QColor(str(C.get("layer2") or C.get("field_bg") or "#ffffff"))
    text = QColor(str(C.get("text") or "#111111"))
    pal = QPalette(QToolTip.palette())
    for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
        pal.setColor(group, QPalette.ToolTipBase, bg)
        pal.setColor(group, QPalette.ToolTipText, text)
        # Some Windows/Qt paths still consult generic window/text roles for tooltips.
        pal.setColor(group, QPalette.Window, bg)
        pal.setColor(group, QPalette.WindowText, text)
        pal.setColor(group, QPalette.Base, bg)
        pal.setColor(group, QPalette.Text, text)
        pal.setColor(group, QPalette.ButtonText, text)
        pal.setColor(group, QPalette.HighlightedText, text)
    return pal


def apply_tooltip_palette(app: QApplication | None = None) -> QPalette:
    pal = build_tooltip_palette()
    try:
        QToolTip.setPalette(pal)
    except Exception:
        pass
    inst = app if app is not None else QApplication.instance()
    if inst is not None:
        try:
            app_pal = QPalette(inst.palette())
            for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
                app_pal.setColor(group, QPalette.ToolTipBase, pal.color(group, QPalette.ToolTipBase))
                app_pal.setColor(group, QPalette.ToolTipText, pal.color(group, QPalette.ToolTipText))
            inst.setPalette(app_pal)
        except Exception:
            pass
    return pal


def _alpha_color(color_text: str, alpha: int) -> str:
    c = QColor(str(color_text or ""))
    if not c.isValid():
        return color_text
    return f"rgba({c.red()},{c.green()},{c.blue()},{max(0, min(255, int(alpha)))})"


def _build_background_override_qss() -> str:
    if not has_background_image():
        return ""
    image_path = str(_VISUAL_PREFS.get("bg_image") or "").strip()
    if not image_path:
        return ""
    # Qt style sheet on Windows is more stable with plain absolute path
    # (e.g. D:/xx/yy.png) than file:/// URI in border/background image.
    qss_url = image_path.replace("\\", "/").replace('"', '\\"')
    base_bg = app_surface_background()
    blur_value = int(_VISUAL_PREFS.get("bg_blur") or 0)
    overlay_alpha = max(24, min(96, 28 + int(blur_value * 1.6)))
    overlay_soft_alpha = max(20, min(98, overlay_alpha - 8))
    overlay_hard_alpha = max(28, min(98, overlay_alpha + 10))
    overlay_main = _alpha_color(C["panel"], overlay_alpha)
    overlay_soft = _alpha_color(C["layer2"], overlay_soft_alpha)
    overlay_nav = _alpha_color(C["sidebar_bg"], overlay_hard_alpha)
    mode = str(_VISUAL_PREFS.get("bg_image_mode") or "center").lower()
    if mode == "stretch":
        rule = f'border-image: url("{qss_url}") 0 0 0 0 stretch stretch;'
    elif mode == "tile":
        rule = (
            f'background-image: url("{qss_url}"); '
            "background-repeat: repeat-xy; "
            "background-position: top left;"
        )
    else:
        rule = (
            f'background-image: url("{qss_url}"); '
            "background-repeat: no-repeat; "
            "background-position: center center;"
        )
    return (
        "\n"
        "QMainWindow {\n"
        f"    background-color: {base_bg};\n"
        f"    {rule}\n"
        "}\n"
        "QStackedWidget, QStackedWidget > QWidget {\n"
        "    background: transparent;\n"
        "}\n"
        "QFrame#chatMain, QFrame#settingsBody {\n"
        "    background: transparent;\n"
        "}\n"
        "QWidget#chatMsgRoot, QWidget#floatingMsgRoot {\n"
        "    background: transparent;\n"
        "}\n"
        "QScrollArea#chatScroll, QScrollArea#floatingChatScroll {\n"
        "    background: transparent;\n"
        "}\n"
        "QFrame#chatSidebar, QFrame#settingsNav {\n"
        f"    background: {overlay_nav};\n"
        "}\n"
        "QFrame#chatHead, QFrame#chatComposer, QFrame#settingsTopbar, QFrame#setupTopbar,\n"
        "QFrame#panelCard, QFrame#cardInset, QFrame#floatingPanel, QFrame#floatingComposer, QFrame#floatingPanelHead {\n"
        f"    background: {overlay_main};\n"
        "}\n"
        "QFrame#recentCard, QFrame#statusCard {\n"
        f"    background: {overlay_soft};\n"
        "}\n"
    )


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
    font = _format_font_family(str(_VISUAL_PREFS.get("font_family") or ""))
    font_mono = str(F.get("font_family_mono") or "monospace")
    font_weight = int(_VISUAL_PREFS.get("font_weight") or 400)
    fs = font_body_size()
    fs_compact = max(10, fs - 3)
    fs_caption = max(10, fs - 2)
    fs_small = max(11, fs - 1)
    fs_subtitle = fs + 2
    fs_display = fs + 14
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
    bg = app_surface_background()
    chat_bg = chat_surface_background()

    base_qss = f"""
    QWidget {{
        color: {text};
        font-family: {font};
        font-weight: {font_weight};
        font-size: {fs}px;
    }}
    QMainWindow, QDialog {{
        background: {mica_fb};
    }}
    QToolTip {{
        background: {layer2};
        color: {text};
        border: 1px solid {stroke_hover};
        border-radius: 8px;
        padding: 5px 9px;
    }}

    QPushButton {{
        background: {field_bg};
        color: {text};
        border: 1px solid {stroke};
        border-radius: {r_md}px;
        padding: 7px 14px;
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
        padding: 6px 28px 6px 10px;
        min-height: 20px;
    }}
    QComboBox:hover {{ border-color: {stroke_hover}; }}
    QComboBox:focus {{ border-color: {focus}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox::down-arrow {{
        image: none;
        width: 0px;
        height: 0px;
        border-left: 5px solid transparent;
        border-right: 5px solid transparent;
        border-top: 6px solid {muted};
        margin-right: 8px;
    }}
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
        border-radius: {r_md}px;
        padding: 9px 12px;
        margin: 2px 6px;
        color: {text_soft};
    }}
    QListWidget::item:hover {{
        background: {layer2};
        color: {text};
    }}
    QListWidget::item:selected {{
        background: {accent_soft};
        color: {text};
        border: 1px solid {stroke_hover};
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
        border: 1px solid {stroke};
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
    QWidget#chatMsgRoot, QWidget#floatingMsgRoot {{
        background: {chat_bg};
        border: none;
    }}
    QScrollArea#chatScroll, QScrollArea#floatingChatScroll {{
        background: {chat_bg};
        border: none;
    }}
    QFrame#recentCard, QFrame#statusCard {{
        background: {layer2};
        border: 1px solid {stroke};
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
        border-color: {stroke_hover};
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
        font-size: {fs_display}px;
        font-weight: 600;
        background: transparent;
    }}
    QLabel#titleSubtitle {{
        color: {muted};
        font-size: {fs}px;
        background: transparent;
    }}
    QLabel#cardTitle {{
        color: {text};
        font-size: {fs_subtitle}px;
        font-weight: 600;
        background: transparent;
    }}
    QLabel#cardDesc {{
        color: {muted};
        font-size: {fs_small}px;
        background: transparent;
    }}
    QLabel#sectionLabel {{
        color: {muted};
        font-size: {fs_compact}px;
        font-weight: 600;
        letter-spacing: 1px;
        background: transparent;
    }}
    QLabel#accentLabel {{
        color: {C['accent_text']};
        font-size: {fs_compact}px;
        font-weight: 600;
        background: transparent;
    }}
    QLabel#bodyText {{
        color: {text};
        font-size: {fs_small}px;
        background: transparent;
    }}
    QLabel#softText {{
        color: {text_soft};
        font-size: {fs_small}px;
        background: transparent;
    }}
    QLabel#softTextSmall {{
        color: {text_soft};
        font-size: {fs_caption}px;
        background: transparent;
    }}
    QLabel#mutedText {{
        color: {muted};
        font-size: {fs_caption}px;
        background: transparent;
    }}
    QLabel#optionIcon {{
        background: {layer2};
        border: 1px solid {stroke};
        border-radius: 12px;
        padding: 6px;
    }}
    QLabel#optionArrow {{
        color: {text_soft};
        background: transparent;
    }}
    QLabel#sidebarLogo {{
        background: {field_bg};
        color: {C['accent_text']};
        border: 1px solid {stroke};
        border-radius: 12px;
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
    QLabel#msgAvatar[avatarVariant="custom"] {{
        background: transparent;
        border: 1px solid transparent;
    }}
    QLabel#msgRoleLabel {{
        color: {text_soft};
        font-size: {fs_caption}px;
        font-weight: 700;
        background: transparent;
    }}
    QLabel#userMsgText {{
        background: transparent;
        color: {text};
        padding: 2px 0;
        font-size: {fs}px;
    }}
    QTextBrowser#botMsgBrowser {{
        background: transparent;
        color: {text};
        border: none;
        padding: 0;
        font-size: {fs}px;
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
        font-size: {fs}px;
        font-weight: 600;
        background: transparent;
    }}
    QLabel#optionDesc {{
        color: {muted};
        font-size: {fs_caption}px;
        background: transparent;
    }}
    QLabel#pathValue {{
        background: {layer2};
        color: {text};
        border: none;
        border-radius: {r_md}px;
        padding: 7px 10px;
        font-size: {fs_small}px;
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
    QLabel#depMark[severity="ok"] {{ color: {success}; font-size: {fs_small}px; font-weight: 700; background: transparent; }}
    QLabel#depMark[severity="warn"] {{ color: {warning}; font-size: {fs_small}px; font-weight: 700; background: transparent; }}
    QLabel#depMark[severity="error"] {{ color: {error}; font-size: {fs_small}px; font-weight: 700; background: transparent; }}
    QLabel#depName {{ color: {text}; font-size: {fs_small}px; font-weight: 600; background: transparent; }}
    QLabel#depDetail {{ color: {text_soft}; font-size: {fs_caption}px; background: transparent; }}
    QLabel#tokenTree {{
        color: {muted};
        font-family: {font_mono};
        font-size: {fs_compact}px;
        background: transparent;
    }}
    """
    return base_qss + _build_background_override_qss()


FLUENT_QSS: str = build_qss()


_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_BORDER_COLOR = 34
_DWMWA_CAPTION_COLOR = 35
_DWMWA_TEXT_COLOR = 36
_DWMWA_SYSTEMBACKDROP_TYPE = 38
_DWMSBT_MAINWINDOW = 2


def _to_win_colorref(color_text: str, fallback: str) -> int | None:
    color = QColor(str(color_text or fallback or ""))
    if not color.isValid():
        color = QColor(str(fallback or ""))
    if not color.isValid():
        return None
    return int(color.red()) | (int(color.green()) << 8) | (int(color.blue()) << 16)


def apply_mica(window: QWidget, *, dark: bool | None = None) -> bool:
    if sys.platform != "win32":
        return False
    if dark is None:
        dark = bool(C.get("is_dark", True))
    try:
        hwnd = int(window.winId())
        dwm = ctypes.windll.dwmapi
        success = True
        dark_val = c_int(1 if dark else 0)
        success = (
            dwm.DwmSetWindowAttribute(
                hwnd,
                _DWMWA_USE_IMMERSIVE_DARK_MODE,
                byref(dark_val),
                ctypes.sizeof(dark_val),
            )
            == 0
        ) and success
        caption_color = _to_win_colorref(str(C.get("panel") or C.get("bg") or ""), str(C.get("bg") or "#ffffff"))
        if caption_color is not None:
            caption_val = c_int(caption_color)
            success = (
                dwm.DwmSetWindowAttribute(
                    hwnd,
                    _DWMWA_CAPTION_COLOR,
                    byref(caption_val),
                    ctypes.sizeof(caption_val),
                )
                == 0
            ) and success
        text_color = _to_win_colorref(str(C.get("text") or ""), "#111111" if not dark else "#f5f5f5")
        if text_color is not None:
            text_val = c_int(text_color)
            success = (
                dwm.DwmSetWindowAttribute(
                    hwnd,
                    _DWMWA_TEXT_COLOR,
                    byref(text_val),
                    ctypes.sizeof(text_val),
                )
                == 0
            ) and success
        border_color = _to_win_colorref(str(C.get("border") or C.get("panel") or ""), str(C.get("panel") or "#ffffff"))
        if border_color is not None:
            border_val = c_int(border_color)
            success = (
                dwm.DwmSetWindowAttribute(
                    hwnd,
                    _DWMWA_BORDER_COLOR,
                    byref(border_val),
                    ctypes.sizeof(border_val),
                )
                == 0
            ) and success
        backdrop_val = c_int(_DWMSBT_MAINWINDOW)
        success = (
            dwm.DwmSetWindowAttribute(
                hwnd,
                _DWMWA_SYSTEMBACKDROP_TYPE,
                byref(backdrop_val),
                ctypes.sizeof(backdrop_val),
            )
            == 0
        ) and success
        return success
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
