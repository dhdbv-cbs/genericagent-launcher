from __future__ import annotations

import codecs
import hashlib
import fnmatch
import locale
import logging
import os
import queue
import re
import shlex
import threading
import tempfile
import tarfile
import time
from urllib.parse import urlparse

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFontDatabase, QImage, QPainter, QPalette, QPen, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from launcher_core_parts.upstream_dependencies import (
    LAUNCHER_BOOTSTRAP_DEPENDENCIES,
    resolve_remote_fallback_requirement_specs,
    resolve_upstream_dependency_manifest,
)
from launcher_app import core as lz
from launcher_app.theme import (
    C,
    F,
    normalize_theme_background_mode,
    preferred_theme_font_families,
    resolve_theme_visual_preset,
    theme_visual_preset_options,
)

from . import common as chat_common
from .common import (
    invalidate_runtime_bound_state,
    is_auto_remote_agent_dir,
    normalize_remote_agent_dir,
    normalize_ssh_error_text,
    remote_agent_dir_default,
    remote_device_agent_dir,
    strip_auto_docker_name_suffix,
)

_SCROLLBAR_STYLE = """
QScrollBar:vertical { width: 10px; background: transparent; border: none; margin: 2px; }
QScrollBar::handle:vertical {
    background: rgba(148,163,184,0.28); border-radius: 4px; min-height: 28px;
}
QScrollBar::handle:vertical:hover { background: rgba(148,163,184,0.50); }
QScrollBar::handle:vertical:pressed { background: rgba(148,163,184,0.70); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; background: none; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
"""

_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\a]*(?:\a|\x1b\\))")
_VPS_PROMPT_TITLE_RESIDUE_RE = re.compile(r"(^|\n)0;[^\n]*?(?=(?:[A-Za-z0-9_.-]+@[^:\s]+:[^\n]*?[#$]))")
_VPS_SHELL_NOISE_RE = re.compile(r"(?:^|\n)(?:\x07+|\x08+|\x0c+)+")
_VPS_DUPLICATED_PROMPT_RE = re.compile(
    r"(?P<userhost>[A-Za-z0-9_.-]+@[^:\s]+): (?P<titlecwd>[^\n#$]*?)(?P=userhost):(?P<promptcwd>[^\n#$]*?)(?P<suffix>[#$])"
)
_VPS_PROMPT_TOKEN_RE = re.compile(r"[A-Za-z0-9_.-]+@[^:\s]+:[^\n]*?[#$] ?")
_SSH_DISCONNECT_HINTS = (
    "10054",
    "远程主机强迫关闭了一个现有的连接",
    "forcibly closed by the remote host",
    "connection reset by peer",
    "software caused connection abort",
    "socket is closed",
    "socket closed",
    "transport is closed",
    "transport closed",
    "eof during negotiation",
    "channel closed",
)


def _looks_like_ssh_disconnect(detail: str) -> bool:
    text = str(detail or "").strip().lower()
    if not text:
        return False
    return any(hint in text for hint in _SSH_DISCONNECT_HINTS)


def _friendly_ssh_disconnect_reason(detail: str, *, context: str = "SSH") -> str:
    text = str(detail or "").strip()
    lower = text.lower()
    if (not text) or ("10054" in lower) or ("reset by peer" in lower) or ("forcibly closed by the remote host" in lower):
        return f"{context} 连接已被远端重置，请重新连接。"
    if ("socket is closed" in lower) or ("transport closed" in lower) or ("transport is closed" in lower) or ("channel closed" in lower):
        return f"{context} 连接已关闭，请重新连接。"
    return f"{context} 连接已断开，请重新连接。"


class _ThemeCropPreview(QWidget):
    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self._pixmap = pixmap
        self._zoom_percent = 100
        self._offset = QPointF(0.0, 0.0)
        self._dragging = False
        self._last_pos = QPointF(0.0, 0.0)
        self.setMinimumSize(520, 320)
        self.setCursor(Qt.OpenHandCursor)

    def _fit_scale(self) -> float:
        pw = float(max(1, self._pixmap.width()))
        ph = float(max(1, self._pixmap.height()))
        ww = float(max(1, self.width()))
        wh = float(max(1, self.height()))
        # Start from full-image visible ("contain"), then user can zoom in/out.
        return min(ww / pw, wh / ph)

    def _scale(self) -> float:
        return self._fit_scale() * max(1.0, float(self._zoom_percent) / 100.0)

    def _scaled_size(self):
        scale = self._scale()
        return float(self._pixmap.width()) * scale, float(self._pixmap.height()) * scale

    def _top_left(self) -> QPointF:
        sw, sh = self._scaled_size()
        x = (self.width() - sw) / 2.0 + self._offset.x()
        y = (self.height() - sh) / 2.0 + self._offset.y()
        return QPointF(x, y)

    def _clamp_offset(self):
        sw, sh = self._scaled_size()
        max_dx = max(0.0, (sw - self.width()) / 2.0)
        max_dy = max(0.0, (sh - self.height()) / 2.0)
        x = min(max(self._offset.x(), -max_dx), max_dx)
        y = min(max(self._offset.y(), -max_dy), max_dy)
        self._offset = QPointF(x, y)

    def set_zoom_percent(self, value: int):
        self._zoom_percent = max(20, min(500, int(value or 100)))
        self._clamp_offset()
        self.update()

    def crop_norm(self):
        scale = self._scale()
        top_left = self._top_left()
        src_w = float(max(1, self._pixmap.width()))
        src_h = float(max(1, self._pixmap.height()))
        x = max(0.0, min(src_w, (-top_left.x()) / scale))
        y = max(0.0, min(src_h, (-top_left.y()) / scale))
        w = max(1.0, min(src_w - x, float(self.width()) / scale))
        h = max(1.0, min(src_h - y, float(self.height()) / scale))
        return {
            "x": x / src_w,
            "y": y / src_h,
            "w": w / src_w,
            "h": h / src_h,
        }

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        self._dragging = True
        self._last_pos = event.position()
        self.setCursor(Qt.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._dragging:
            return super().mouseMoveEvent(event)
        pos = event.position()
        delta = pos - self._last_pos
        self._last_pos = pos
        self._offset = QPointF(self._offset.x() + delta.x(), self._offset.y() + delta.y())
        self._clamp_offset()
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), Qt.black)
        top_left = self._top_left()
        sw, sh = self._scaled_size()
        target = QRectF(top_left.x(), top_left.y(), sw, sh)
        source = QRectF(0, 0, self._pixmap.width(), self._pixmap.height())
        painter.drawPixmap(target, self._pixmap, source)
        pen = QPen(Qt.white)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawRect(self.rect().adjusted(1, 1, -2, -2))


class _ThemeCropDialog(QDialog):
    def __init__(self, image_path: str, target_size: QSize, parent=None):
        super().__init__(parent)
        self._target_size = QSize(max(320, int(target_size.width())), max(240, int(target_size.height())))
        self._crop_norm = None
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            raise ValueError("无法读取图片，请更换一张文件。")

        self.setWindowTitle("裁切背景图片")
        self.setModal(True)
        self.resize(980, 760)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        info = QLabel(
            f"输出尺寸：{self._target_size.width()} x {self._target_size.height()}（目标显示尺寸）\n"
            "拖动图片调整位置，滑动缩放后点击“确定裁切”。100% 为完整显示原图。"
        )
        info.setWordWrap(True)
        info.setObjectName("mutedText")
        root.addWidget(info)

        ratio = float(self._target_size.width()) / float(max(1, self._target_size.height()))
        max_w, max_h = 920, 560
        width = max_w
        height = int(width / ratio)
        if height > max_h:
            height = max_h
            width = int(height * ratio)
        # Keep the preview ratio strictly aligned with target output ratio.
        # For tall targets (like floating window), forcing a large min width
        # would distort the preview ratio and produce visible mismatch.
        width = max(260, width)
        height = max(240, int(width / ratio))
        if height > max_h:
            height = max_h
            width = max(220, int(height * ratio))

        self.preview = _ThemeCropPreview(pixmap, self)
        self.preview.setFixedSize(width, height)
        root.addWidget(self.preview, 0, Qt.AlignCenter)

        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(8)
        zoom_title = QLabel("缩放")
        zoom_title.setObjectName("bodyText")
        zoom_row.addWidget(zoom_title, 0)
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(20, 500)
        self.zoom_slider.setValue(100)
        self.zoom_slider.valueChanged.connect(self._on_zoom_changed)
        zoom_row.addWidget(self.zoom_slider, 1)
        self.zoom_value = QLabel("100%")
        self.zoom_value.setObjectName("softTextSmall")
        self.zoom_value.setFixedWidth(56)
        zoom_row.addWidget(self.zoom_value, 0)
        root.addLayout(zoom_row)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn, 0)
        ok_btn = QPushButton("确定裁切")
        ok_btn.clicked.connect(self._accept_crop)
        buttons.addWidget(ok_btn, 0)
        root.addLayout(buttons)

    def _on_zoom_changed(self, value):
        zoom = max(20, int(value or 100))
        self.preview.set_zoom_percent(zoom)
        self.zoom_value.setText(f"{zoom}%")

    def _accept_crop(self):
        self._crop_norm = self.preview.crop_norm()
        self.accept()

    def crop_norm(self):
        return dict(self._crop_norm or {})


class _VpsTerminalCommandEdit(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._history_prev = None
        self._history_next = None

    def set_history_handlers(self, prev_handler, next_handler):
        self._history_prev = prev_handler
        self._history_next = next_handler

    def keyPressEvent(self, event):
        if event is not None and event.modifiers() == Qt.NoModifier:
            if event.key() == Qt.Key_Up and callable(self._history_prev):
                self._history_prev()
                event.accept()
                return
            if event.key() == Qt.Key_Down and callable(self._history_next):
                self._history_next()
                event.accept()
                return
        super().keyPressEvent(event)


class _StablePopupComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        try:
            app = QApplication.instance()
            if app is not None:
                app.setEffectEnabled(Qt.UI_AnimateCombo, False)
        except Exception:
            pass
        self.setMaxVisibleItems(8)
        view = QListView(self)
        view.setObjectName("stablePopupComboView")
        view.setFrameShape(QFrame.NoFrame)
        view.setUniformItemSizes(True)
        view.setMouseTracking(True)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setView(view)

    def _repair_popup_geometry(self):
        return

    def wheelEvent(self, event):
        try:
            view = self.view()
            if view is not None and view.isVisible():
                super().wheelEvent(event)
                return
        except Exception:
            pass
        if event is not None:
            event.ignore()


class SettingsPanelMixin:
    _SETTINGS_LIVE_RELOAD_CATEGORIES = frozenset({"channels", "schedule", "personal", "usage"})
    _SETTINGS_LIVE_RELOAD_MIN_INTERVAL_SECONDS = 4.0
    _SETTINGS_SWITCH_RELOAD_DELAY_MS = 24

    def _settings_nav_icon_spec(self, key: str):
        mapping = {
            "api": chat_common._SVG_KEY,
            "channels": chat_common._SVG_MESSAGE,
            "vps": chat_common._SVG_SERVER,
            "schedule": chat_common._SVG_CLOCK,
            "sop": chat_common._SVG_SPARKLE,
            "personal": chat_common._SVG_PUZZLE,
            "theme": chat_common._SVG_SWATCH,
            "usage": chat_common._SVG_RECEIPT,
            "about": chat_common._SVG_INFO,
        }
        return mapping.get(str(key or "").strip().lower(), chat_common._SVG_INFO)

    def _apply_settings_nav_button_icon(self, key: str, button, *, selected: bool = False) -> None:
        if button is None:
            return
        chat_common.set_button_svg_icon(
            button,
            f"settings_nav_{str(key or '').strip().lower() or 'item'}",
            self._settings_nav_icon_spec(key),
            color="text" if selected else "text_soft",
            size=16,
        )

    def _api_add_menu_specs(self):
        return [
            ("claude_native", f"添加 {lz.SIMPLE_FORMAT_LABEL.get('claude_native', 'Claude 原生')}"),
            ("oai_chat", f"添加 {lz.SIMPLE_FORMAT_LABEL.get('oai_chat', 'Chat Completions')}"),
            ("oai_responses", f"添加 {lz.SIMPLE_FORMAT_LABEL.get('oai_responses', 'Responses')}"),
            ("mixin", f"添加 {lz.SIMPLE_FORMAT_LABEL.get('mixin', 'Mixin 故障转移')}"),
        ]

    def _bind_api_add_button_menu(self, button):
        if button is None:
            return None
        menu = QMenu(button)
        chat_common.apply_menu_popup_theme(menu)
        for format_key, label in self._api_add_menu_specs():
            action = menu.addAction(str(label))
            action.triggered.connect(lambda _checked=False, key=format_key: self._qt_api_add_channel(key))
        button.setMenu(menu)
        return menu

    def _settings_category_needs_live_reload(self, key: str) -> bool:
        return str(key or "").strip().lower() in self._SETTINGS_LIVE_RELOAD_CATEGORIES

    def _settings_category_refreshes_target_combo(self, key: str) -> bool:
        category = str(key or "").strip().lower()
        return category == "personal" or self._settings_category_uses_target_switch(category)

    def _mark_settings_category_reloaded(self, key: str) -> None:
        category = str(key or "").strip().lower()
        if not self._settings_category_needs_live_reload(category):
            return
        stamps = getattr(self, "_settings_live_reload_stamps", None)
        if not isinstance(stamps, dict):
            stamps = {}
            self._settings_live_reload_stamps = stamps
        stamps[category] = float(time.time())

    def _settings_category_allows_live_reload(self, key: str) -> bool:
        category = str(key or "").strip().lower()
        if not self._settings_category_needs_live_reload(category):
            return False
        if category in {"channels", "schedule"}:
            target_ctx_getter = getattr(self, "_settings_target_context", None)
            target_ctx = target_ctx_getter() if callable(target_ctx_getter) else {"is_remote": False}
            return bool((target_ctx or {}).get("is_remote"))
        if category in {"personal", "usage"}:
            target_getter = getattr(self, "_settings_data_target_context", None)
            target = target_getter() if callable(target_getter) else {"is_remote": False}
            return bool((target or {}).get("is_remote"))
        return True

    def _settings_category_should_force_live_reload(self, key: str) -> bool:
        category = str(key or "").strip().lower()
        if not self._settings_category_needs_live_reload(category):
            return False
        if not self._settings_category_allows_live_reload(category):
            return False
        loaded = getattr(self, "_settings_loaded_categories", None)
        if not isinstance(loaded, set) or category not in loaded:
            return False
        stamps = getattr(self, "_settings_live_reload_stamps", None)
        if not isinstance(stamps, dict):
            return False
        last = float(stamps.get(category, 0.0) or 0.0)
        min_interval = float(getattr(self, "_SETTINGS_LIVE_RELOAD_MIN_INTERVAL_SECONDS", 1.2) or 1.2)
        return (float(time.time()) - last) >= max(0.0, min_interval)

    def _strip_auto_docker_name_suffix(self, value: str) -> str:
        return strip_auto_docker_name_suffix(value)

    def _vps_profile_uses_docker_takeover(self, raw, *, takeover_cfg=None) -> bool:
        item = raw if isinstance(raw, dict) else {}
        legacy_remote_mode = str(item.get("remote_mode") or "").strip().lower() == "docker_container"
        legacy_agent_mode = str(item.get("agent_mode") or "").strip().lower() == "docker"
        return bool(
            str(
                item.get("docker_takeover_container")
                or item.get("takeover_docker_container")
                or item.get("takeover_container")
                or ""
            ).strip()
            or str(item.get("docker_takeover_agent_dir") or item.get("takeover_docker_agent_dir") or "").strip()
            or legacy_remote_mode
            or legacy_agent_mode
        )

    def _vps_profile_name_needs_cleanup(self, raw, *, takeover_cfg=None) -> bool:
        item = raw if isinstance(raw, dict) else {}
        if not self._vps_profile_uses_docker_takeover(item, takeover_cfg=takeover_cfg):
            return False
        raw_name = str(item.get("name") or "").strip()
        if not raw_name:
            return False
        return self._strip_auto_docker_name_suffix(raw_name) != raw_name

    def _settings_should_reload_on_switch(self, key: str) -> bool:
        category = str(key or "").strip().lower()
        if self._settings_category_should_force_live_reload(category):
            return True
        loaded = getattr(self, "_settings_loaded_categories", None)
        if not isinstance(loaded, set):
            return True
        return category not in loaded

    def _normalize_qss_color(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return text
        m = re.fullmatch(
            r"rgba\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*([0-9]*\.?[0-9]+)\s*\)",
            text,
            flags=re.IGNORECASE,
        )
        if not m:
            return text
        r = max(0, min(255, int(m.group(1))))
        g = max(0, min(255, int(m.group(2))))
        b = max(0, min(255, int(m.group(3))))
        alpha_raw = float(m.group(4))
        if alpha_raw <= 1.0:
            alpha = int(round(max(0.0, min(1.0, alpha_raw)) * 255.0))
        else:
            alpha = int(round(max(0.0, min(255.0, alpha_raw))))
        return f"rgba({r},{g},{b},{alpha})"

    def _theme_combo_style(self):
        styler = getattr(self, "_api_combo_style", None)
        if callable(styler):
            return styler()
        field_bg = self._normalize_qss_color(str(C.get("field_bg") or "#ffffff"))
        text = self._normalize_qss_color(str(C.get("text") or "#1a1f2b"))
        border = self._normalize_qss_color(str(C.get("stroke_default") or "#c7cfdd"))
        border_hover = self._normalize_qss_color(str(C.get("stroke_hover") or "#9aa6bc"))
        selection_bg = self._normalize_qss_color(str(C.get("accent_soft_bg") or "#dbe7ff"))
        arrow = self._normalize_qss_color(str(C.get("muted") or "#6b7280"))
        return (
            f"QComboBox {{ background: {field_bg}; color: {text}; "
            f"border: 1px solid {border}; border-radius: 8px; padding: 6px 28px 6px 10px; min-height: 20px; }}"
            f"QComboBox:hover {{ border-color: {border_hover}; }}"
            "QComboBox::drop-down { border: none; width: 22px; }"
            "QComboBox::down-arrow { image: none; width: 0px; height: 0px; border-left: 5px solid transparent; "
            f"border-right: 5px solid transparent; border-top: 6px solid {arrow}; margin-right: 8px; }}"
            f"QComboBox QAbstractItemView {{ background: {field_bg}; color: {text}; border: 1px solid {border}; "
            f"border-radius: 8px; padding: 4px; selection-background-color: {selection_bg}; selection-color: {text}; outline: 0; }}"
        )

    def _apply_theme_combo_style(self, combo):
        if combo is None:
            return
        try:
            if str(combo.objectName() or "").strip() == "settingsTargetCombo":
                self._ensure_combo_popup_view(combo)
            combo.setStyleSheet(self._theme_combo_style())
        except Exception:
            pass

    def _ensure_combo_popup_view(self, combo):
        if combo is None:
            return None
        view = None
        try:
            view = combo.view()
        except Exception:
            view = None
        replace = view is None
        if view is not None:
            try:
                replace = str(view.itemDelegate().metaObject().className() or "").strip() == "QComboMenuDelegate"
            except Exception:
                replace = False
        if replace:
            try:
                view = QListView(combo)
                view.setFrameShape(QFrame.NoFrame)
                view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                combo.setView(view)
            except Exception:
                try:
                    view = combo.view()
                except Exception:
                    view = None
        if view is None:
            return None
        popup_bg = self._normalize_qss_color(str(C.get("layer1") or C.get("field_bg") or "#ffffff"))
        popup_text = self._normalize_qss_color(str(C.get("text") or "#1f2937"))
        popup_border = self._normalize_qss_color(str(C.get("stroke_hover") or C.get("stroke_default") or "#c7cfdd"))
        popup_select = self._normalize_qss_color(str(C.get("accent_soft_bg") or "#dbe7ff"))
        popup_style = (
            f"QListView {{ background: {popup_bg}; color: {popup_text}; border: 1px solid {popup_border}; "
            "outline: 0; padding: 4px; }"
            f"QListView::item {{ background: transparent; color: {popup_text}; min-height: 24px; padding: 6px 10px; }}"
            f"QListView::item:selected {{ background: {popup_select}; color: {popup_text}; }}"
            f"QListView::item:hover {{ background: {popup_select}; color: {popup_text}; }}"
        )
        try:
            pal = view.palette()
            pal.setColor(QPalette.Base, QColor(popup_bg))
            pal.setColor(QPalette.Text, QColor(popup_text))
            pal.setColor(QPalette.ButtonText, QColor(popup_text))
            pal.setColor(QPalette.HighlightedText, QColor(popup_text))
            view.setPalette(pal)
        except Exception:
            pass
        try:
            view.setStyleSheet(popup_style)
        except Exception:
            pass
        try:
            vp = view.viewport()
            if vp is not None:
                vp.setStyleSheet(f"background: {popup_bg}; color: {popup_text};")
        except Exception:
            pass
        return view

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        top_wrap = QFrame()
        top_wrap.setObjectName("settingsTopbar")
        top = QHBoxLayout(top_wrap)
        top.setContentsMargins(24, 14, 24, 14)
        top.setSpacing(10)
        back_btn = QPushButton("返回聊天")
        back_btn.setStyleSheet(self._sidebar_button_style(subtle=True))
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.clicked.connect(self._show_chat_page)
        chat_common.set_button_svg_icon(back_btn, "settings_back", chat_common._SVG_CHEVRON_LEFT, color="text_soft", size=16)
        self._settings_top_back_btn = back_btn
        top.addWidget(back_btn, 0)
        title = QLabel("设置")
        title.setObjectName("cardTitle")
        top.addWidget(title, 0)
        top.addStretch(1)
        layout.addWidget(top_wrap)

        body = QFrame()
        body.setObjectName("settingsBody")
        body_row = QHBoxLayout(body)
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(0)
        layout.addWidget(body, 1)

        nav = QFrame()
        nav.setObjectName("settingsNav")
        nav.setFixedWidth(220)
        nav_col = QVBoxLayout(nav)
        nav_col.setContentsMargins(10, 16, 10, 16)
        nav_col.setSpacing(2)
        nav_label = QLabel("分类")
        nav_label.setObjectName("sectionLabel")
        nav_col.addWidget(nav_label)
        body_row.addWidget(nav, 0)

        content_wrap = QFrame()
        content_wrap.setStyleSheet("background: transparent;")
        content_col = QVBoxLayout(content_wrap)
        content_col.setContentsMargins(24, 20, 24, 20)
        content_col.setSpacing(12)
        self.settings_status_label = QLabel("")
        self.settings_status_label.setWordWrap(True)
        self.settings_status_label.setObjectName("mutedText")
        self.settings_status_label.setFocusPolicy(Qt.StrongFocus)
        content_col.addWidget(self.settings_status_label)
        self._settings_target_section = QFrame()
        target_section = QVBoxLayout(self._settings_target_section)
        target_section.setContentsMargins(0, 0, 0, 0)
        target_section.setSpacing(6)
        target_row = QHBoxLayout()
        target_row.setSpacing(8)
        target_label = QLabel("配置目标设备")
        target_label.setObjectName("softTextSmall")
        target_row.addWidget(target_label, 0)
        self.settings_target_combo = _StablePopupComboBox()
        self.settings_target_combo.setObjectName("settingsTargetCombo")
        self._apply_theme_combo_style(self.settings_target_combo)
        self.settings_target_combo.currentIndexChanged.connect(self._on_settings_target_changed)
        target_row.addWidget(self.settings_target_combo, 1)
        self.settings_target_refresh_btn = QPushButton("刷新设备")
        self.settings_target_refresh_btn.setStyleSheet(self._action_button_style())
        self.settings_target_refresh_btn.clicked.connect(lambda _=False: self._refresh_settings_target_combo(force=True))
        chat_common.set_button_svg_icon(self.settings_target_refresh_btn, "settings_target_refresh", chat_common._SVG_REFRESH, color="text_soft", size=16)
        target_row.addWidget(self.settings_target_refresh_btn, 0)
        target_section.addLayout(target_row)
        self.settings_target_notice = QLabel("API 与通讯渠道配置会写入当前选中设备。")
        self.settings_target_notice.setWordWrap(True)
        self.settings_target_notice.setObjectName("mutedText")
        target_section.addWidget(self.settings_target_notice)
        content_col.addWidget(self._settings_target_section)
        self.settings_stack = QStackedWidget()
        content_col.addWidget(self.settings_stack, 1)
        body_row.addWidget(content_wrap, 1)

        self._settings_nav_buttons = {}
        self._settings_pages = {}
        self._settings_loaded_categories = set()
        categories = [
            ("api", "API"),
            ("channels", "通讯渠道"),
            ("vps", "VPS 管理"),
            ("schedule", "定时任务"),
            ("sop", "SOP"),
            ("personal", "个性设置"),
            ("theme", "主题设置"),
            ("usage", "使用日志"),
            ("about", "关于"),
        ]

        def make_page():
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }" + _SCROLLBAR_STYLE)
            inner = QWidget()
            scroll.setWidget(inner)
            inner_layout = QVBoxLayout(inner)
            inner_layout.setContentsMargins(0, 0, 0, 0)
            inner_layout.setSpacing(12)
            return scroll, inner_layout

        for key, label_text in categories:
            btn = QPushButton(label_text)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(self._sidebar_button_style(subtle=True))
            btn.clicked.connect(lambda _=False, k=key: self._show_settings_category(k))
            self._apply_settings_nav_button_icon(key, btn, selected=False)
            nav_col.addWidget(btn)
            self._settings_nav_buttons[key] = btn

            page_widget, page_layout = make_page()
            self.settings_stack.addWidget(page_widget)
            self._settings_pages[key] = {"widget": page_widget, "layout": page_layout}

        nav_col.addStretch(1)

        api_layout = self._settings_pages["api"]["layout"]
        api_layout.addWidget(
            self._settings_intro(
                "API 配置",
                "这里直接维护 GenericAgent 的 mykey.py。保存后可以按需只保存，或保存并重启内核。",
            )
        )
        api_card = self._panel_card()
        api_box = QVBoxLayout(api_card)
        api_box.setContentsMargins(20, 18, 20, 18)
        api_box.setSpacing(10)
        api_title = QLabel("配置卡片")
        api_title.setObjectName("cardTitle")
        api_box.addWidget(api_title)
        api_desc = QLabel("下方卡片会写回当前目录内的 mykey.py。")
        api_desc.setObjectName("cardDesc")
        api_box.addWidget(api_desc)
        api_toolbar = QHBoxLayout()
        api_toolbar.setSpacing(8)
        api_add_btn = QPushButton("添加 API 卡片")
        api_add_btn.setStyleSheet(self._action_button_style(primary=True))
        chat_common.set_button_svg_icon(api_add_btn, "settings_api_add", chat_common._SVG_PLUS, color="selection_fg", size=16)
        self._settings_api_add_menu = self._bind_api_add_button_menu(api_add_btn)
        api_toolbar.addWidget(api_add_btn, 0)
        self.settings_api_add_btn = api_add_btn
        api_save_btn = QPushButton("仅保存")
        api_save_btn.setStyleSheet(self._action_button_style())
        api_save_btn.clicked.connect(lambda: self._qt_api_save(restart=False))
        api_toolbar.addWidget(api_save_btn, 0)
        self.settings_api_save_btn = api_save_btn
        api_restart_btn = QPushButton("保存并重启内核")
        api_restart_btn.setStyleSheet(self._action_button_style())
        api_restart_btn.clicked.connect(lambda: self._qt_api_save(restart=True))
        api_toolbar.addWidget(api_restart_btn, 0)
        self.settings_api_restart_btn = api_restart_btn
        api_raw_btn = QPushButton("直接编辑文件")
        api_raw_btn.setStyleSheet(self._action_button_style())
        api_raw_btn.clicked.connect(self._open_raw_mykey_editor)
        api_toolbar.addWidget(api_raw_btn, 0)
        self.settings_api_raw_btn = api_raw_btn
        api_toolbar.addStretch(1)
        api_box.addLayout(api_toolbar)
        self.settings_api_notice = QLabel("")
        self.settings_api_notice.setWordWrap(True)
        self.settings_api_notice.setObjectName("mutedText")
        api_box.addWidget(self.settings_api_notice)
        self.settings_api_list = QWidget()
        self.settings_api_list_layout = QVBoxLayout(self.settings_api_list)
        self.settings_api_list_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_api_list_layout.setSpacing(10)
        api_box.addWidget(self.settings_api_list)
        api_layout.addWidget(api_card)
        api_layout.addStretch(1)

        ch_layout = self._settings_pages["channels"]["layout"]
        ch_layout.addWidget(
            self._settings_intro(
                "通讯渠道",
                "这里接的是 GenericAgent 原项目的渠道脚本。它们各自启动独立进程，不和当前聊天主区共用上下文。",
            )
        )
        channels_card = self._panel_card()
        channels_box = QVBoxLayout(channels_card)
        channels_box.setContentsMargins(20, 18, 20, 18)
        channels_box.setSpacing(10)
        channels_title = QLabel("渠道配置")
        channels_title.setObjectName("cardTitle")
        channels_box.addWidget(channels_title)
        channels_desc = QLabel("可在这里维护字段、查看运行状态，并直接启动或停止由启动器托管的渠道进程。")
        channels_desc.setWordWrap(True)
        channels_desc.setObjectName("cardDesc")
        channels_box.addWidget(channels_desc)
        channel_toolbar = QHBoxLayout()
        channel_toolbar.setSpacing(8)
        channel_save_btn = QPushButton("保存通讯配置")
        channel_save_btn.setStyleSheet(self._action_button_style(primary=True))
        channel_save_btn.clicked.connect(lambda: self._qt_channels_save(silent=False))
        channel_toolbar.addWidget(channel_save_btn, 0)
        self.settings_channels_save_btn = channel_save_btn
        channel_refresh_btn = QPushButton("刷新状态")
        channel_refresh_btn.setStyleSheet(self._action_button_style())
        channel_refresh_btn.clicked.connect(self._request_channel_status_refresh)
        chat_common.set_button_svg_icon(channel_refresh_btn, "settings_channels_refresh", chat_common._SVG_REFRESH, color="text_soft", size=16)
        channel_toolbar.addWidget(channel_refresh_btn, 0)
        self.settings_channels_refresh_btn = channel_refresh_btn
        channel_stop_btn = QPushButton("停止全部")
        channel_stop_btn.setStyleSheet(self._action_button_style())
        channel_stop_btn.clicked.connect(self._stop_all_managed_channels)
        channel_toolbar.addWidget(channel_stop_btn, 0)
        self.settings_channels_stop_all_btn = channel_stop_btn
        channel_toolbar.addStretch(1)
        channels_box.addLayout(channel_toolbar)
        self.settings_channels_notice = QLabel("")
        self.settings_channels_notice.setWordWrap(True)
        self.settings_channels_notice.setObjectName("mutedText")
        channels_box.addWidget(self.settings_channels_notice)
        self.settings_channels_list = QWidget()
        self.settings_channels_list_layout = QVBoxLayout(self.settings_channels_list)
        self.settings_channels_list_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_channels_list_layout.setSpacing(10)
        channels_box.addWidget(self.settings_channels_list)
        ch_layout.addWidget(channels_card)
        ch_layout.addStretch(1)

        vps_layout = self._settings_pages["vps"]["layout"]
        vps_layout.addWidget(
            self._settings_intro(
                "VPS 管理",
                "这里统一管理多台远端服务器。切换当前目标后，连接测试、终端和部署都会作用到当前选中的服务器。",
            )
        )
        vps_profile_card = self._panel_card()
        vps_profile_box = QVBoxLayout(vps_profile_card)
        vps_profile_box.setContentsMargins(20, 18, 20, 18)
        vps_profile_box.setSpacing(10)
        vps_profile_title = QLabel("服务器列表")
        vps_profile_title.setObjectName("cardTitle")
        vps_profile_box.addWidget(vps_profile_title)
        vps_profile_desc = QLabel("支持新建、重命名、删除和切换服务器配置。侧边栏里的“其他设备”也会同步读取这里的服务器资料。")
        vps_profile_desc.setWordWrap(True)
        vps_profile_desc.setObjectName("cardDesc")
        vps_profile_box.addWidget(vps_profile_desc)
        vps_profile_row = QHBoxLayout()
        vps_profile_row.setSpacing(8)
        vps_profile_label = QLabel("当前服务器")
        vps_profile_label.setMinimumWidth(92)
        vps_profile_label.setObjectName("bodyText")
        vps_profile_row.addWidget(vps_profile_label, 0)
        self.settings_vps_profile_light = QLabel()
        self.settings_vps_profile_light.setMinimumWidth(20)
        self.settings_vps_profile_light.setAlignment(Qt.AlignCenter)
        self.settings_vps_profile_light.setObjectName("softTextSmall")
        self.settings_vps_profile_light.setToolTip("服务器健康状态")
        chat_common.set_label_svg_icon(self.settings_vps_profile_light, "settings_vps_status", chat_common._SVG_DOT, color="#94a3b8", size=12)
        vps_profile_row.addWidget(self.settings_vps_profile_light, 0)
        self.settings_vps_profile_combo = _StablePopupComboBox()
        self._apply_theme_combo_style(self.settings_vps_profile_combo)
        self.settings_vps_profile_combo.currentIndexChanged.connect(self._on_vps_profile_combo_changed)
        vps_profile_row.addWidget(self.settings_vps_profile_combo, 1)
        self.settings_vps_profile_state_label = QLabel("")
        self.settings_vps_profile_state_label.setObjectName("softTextSmall")
        self.settings_vps_profile_state_label.setMinimumWidth(150)
        self.settings_vps_profile_state_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        vps_profile_row.addWidget(self.settings_vps_profile_state_label, 0)
        self.settings_vps_profile_new_btn = QPushButton("新建")
        self.settings_vps_profile_new_btn.setStyleSheet(self._action_button_style())
        self.settings_vps_profile_new_btn.clicked.connect(self._create_vps_profile)
        vps_profile_row.addWidget(self.settings_vps_profile_new_btn, 0)
        self.settings_vps_profile_rename_btn = QPushButton("重命名")
        self.settings_vps_profile_rename_btn.setStyleSheet(self._action_button_style())
        self.settings_vps_profile_rename_btn.clicked.connect(self._rename_vps_profile)
        vps_profile_row.addWidget(self.settings_vps_profile_rename_btn, 0)
        self.settings_vps_profile_delete_btn = QPushButton("删除")
        self.settings_vps_profile_delete_btn.setStyleSheet(self._action_button_style())
        self.settings_vps_profile_delete_btn.clicked.connect(self._delete_vps_profile)
        vps_profile_row.addWidget(self.settings_vps_profile_delete_btn, 0)
        vps_profile_box.addLayout(vps_profile_row)
        self.settings_vps_profile_notice = QLabel("")
        self.settings_vps_profile_notice.setWordWrap(True)
        self.settings_vps_profile_notice.setObjectName("mutedText")
        vps_profile_box.addWidget(self.settings_vps_profile_notice)
        vps_layout.addWidget(vps_profile_card)

        vps_card = self._panel_card()
        vps_box = QVBoxLayout(vps_card)
        vps_box.setContentsMargins(20, 18, 20, 18)
        vps_box.setSpacing(10)
        vps_title = QLabel("连接配置")
        vps_title.setObjectName("cardTitle")
        vps_box.addWidget(vps_title)
        vps_desc = QLabel("建议优先使用 SSH 私钥；密码为可选项，可用于密码认证或解密受保护私钥。")
        vps_desc.setWordWrap(True)
        vps_desc.setObjectName("cardDesc")
        vps_box.addWidget(vps_desc)

        host_row = QHBoxLayout()
        host_row.setSpacing(8)
        host_label = QLabel("服务器地址")
        host_label.setMinimumWidth(92)
        host_label.setObjectName("bodyText")
        host_row.addWidget(host_label, 0)
        self.settings_vps_host_edit = QLineEdit()
        self.settings_vps_host_edit.setPlaceholderText("例如 192.168.1.10 或 vps.example.com")
        self._fluent_input(self.settings_vps_host_edit)
        host_row.addWidget(self.settings_vps_host_edit, 1)
        vps_box.addLayout(host_row)

        user_row = QHBoxLayout()
        user_row.setSpacing(8)
        user_label = QLabel("用户名")
        user_label.setMinimumWidth(92)
        user_label.setObjectName("bodyText")
        user_row.addWidget(user_label, 0)
        self.settings_vps_username_edit = QLineEdit()
        self.settings_vps_username_edit.setPlaceholderText("例如 root 或 ubuntu")
        self._fluent_input(self.settings_vps_username_edit)
        self.settings_vps_username_edit.textChanged.connect(self._refresh_vps_remote_dir_placeholder)
        user_row.addWidget(self.settings_vps_username_edit, 1)
        vps_box.addLayout(user_row)

        port_row = QHBoxLayout()
        port_row.setSpacing(8)
        port_label = QLabel("端口")
        port_label.setMinimumWidth(92)
        port_label.setObjectName("bodyText")
        port_row.addWidget(port_label, 0)
        self.settings_vps_port_spin = chat_common.NoWheelSpinBox()
        self.settings_vps_port_spin.setRange(1, 65535)
        self.settings_vps_port_spin.setValue(22)
        self.settings_vps_port_spin.setFixedWidth(140)
        port_row.addWidget(self.settings_vps_port_spin, 0)
        port_row.addStretch(1)
        vps_box.addLayout(port_row)

        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        key_label = QLabel("SSH 私钥")
        key_label.setMinimumWidth(92)
        key_label.setObjectName("bodyText")
        key_row.addWidget(key_label, 0)
        self.settings_vps_key_path_edit = QLineEdit()
        self.settings_vps_key_path_edit.setPlaceholderText("可选：id_rsa / id_ed25519 / *.pem")
        self._fluent_input(self.settings_vps_key_path_edit)
        key_row.addWidget(self.settings_vps_key_path_edit, 1)
        vps_key_browse_btn = QPushButton("浏览")
        vps_key_browse_btn.setStyleSheet(self._action_button_style())
        vps_key_browse_btn.clicked.connect(self._browse_vps_ssh_key)
        key_row.addWidget(vps_key_browse_btn, 0)
        vps_box.addLayout(key_row)

        pwd_row = QHBoxLayout()
        pwd_row.setSpacing(8)
        pwd_label = QLabel("密码（可选）")
        pwd_label.setMinimumWidth(92)
        pwd_label.setObjectName("bodyText")
        pwd_row.addWidget(pwd_label, 0)
        self.settings_vps_password_edit = QLineEdit()
        self.settings_vps_password_edit.setEchoMode(QLineEdit.Password)
        self.settings_vps_password_edit.setPlaceholderText("可选：用于密码登录或解密私钥")
        self._fluent_input(self.settings_vps_password_edit)
        pwd_row.addWidget(self.settings_vps_password_edit, 1)
        vps_box.addLayout(pwd_row)

        self.settings_vps_notice = QLabel("")
        self.settings_vps_notice.setWordWrap(True)
        self.settings_vps_notice.setObjectName("mutedText")
        vps_box.addWidget(self.settings_vps_notice)

        vps_toolbar = QHBoxLayout()
        vps_toolbar.setSpacing(8)
        self.settings_vps_save_btn = QPushButton("保存 VPS 配置")
        self.settings_vps_save_btn.setStyleSheet(self._action_button_style())
        self.settings_vps_save_btn.clicked.connect(self._save_vps_connection)
        vps_toolbar.addWidget(self.settings_vps_save_btn, 0)
        self.settings_vps_install_dep_btn = QPushButton("安装 SSH 依赖")
        self.settings_vps_install_dep_btn.setStyleSheet(self._action_button_style())
        self.settings_vps_install_dep_btn.clicked.connect(self._install_vps_dependencies)
        vps_toolbar.addWidget(self.settings_vps_install_dep_btn, 0)
        self.settings_vps_test_btn = QPushButton("测试连接")
        self.settings_vps_test_btn.setStyleSheet(self._action_button_style(primary=True))
        self.settings_vps_test_btn.clicked.connect(self._test_vps_connection)
        vps_toolbar.addWidget(self.settings_vps_test_btn, 0)
        vps_toolbar.addStretch(1)
        vps_box.addLayout(vps_toolbar)

        vps_layout.addWidget(vps_card)

        terminal_card = self._panel_card()
        terminal_box = QVBoxLayout(terminal_card)
        terminal_box.setContentsMargins(20, 18, 20, 18)
        terminal_box.setSpacing(10)
        terminal_title = QLabel("远程终端")
        terminal_title.setObjectName("cardTitle")
        terminal_box.addWidget(terminal_title)
        terminal_desc = QLabel("连接后可直接执行命令。正文只保留远端 shell 的真实输出，不再混入启动器自己的提示。")
        terminal_desc.setWordWrap(True)
        terminal_desc.setObjectName("cardDesc")
        terminal_box.addWidget(terminal_desc)
        self.settings_vps_terminal_meta = QLabel("")
        self.settings_vps_terminal_meta.setWordWrap(True)
        self.settings_vps_terminal_meta.setMinimumHeight(40)
        terminal_box.addWidget(self.settings_vps_terminal_meta)
        self.settings_vps_terminal_output = QPlainTextEdit()
        self.settings_vps_terminal_output.setReadOnly(True)
        self.settings_vps_terminal_output.setMinimumHeight(220)
        self.settings_vps_terminal_output.setPlaceholderText("尚未连接远程终端。")
        self.settings_vps_terminal_output.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.settings_vps_terminal_output.setMaximumBlockCount(4000)
        self.settings_vps_terminal_output.setStyleSheet(
            "QPlainTextEdit {"
            f" font-family: {F['font_family_mono']};"
            " font-size: 12px;"
            " background: #0f172a;"
            " color: #e2e8f0;"
            " border: 1px solid rgba(148,163,184,0.25);"
            " border-radius: 12px;"
            " padding: 10px;"
            " selection-background-color: rgba(59,130,246,0.35);"
            "}"
        )
        try:
            palette = self.settings_vps_terminal_output.palette()
            palette.setColor(QPalette.Base, QColor("#0f172a"))
            palette.setColor(QPalette.Text, QColor("#e2e8f0"))
            palette.setColor(QPalette.PlaceholderText, QColor("#94a3b8"))
            self.settings_vps_terminal_output.setPalette(palette)
        except Exception:
            pass
        try:
            viewport = self.settings_vps_terminal_output.viewport()
            if viewport is not None:
                viewport.setStyleSheet("background: #0f172a; color: #e2e8f0;")
        except Exception:
            pass
        try:
            self.settings_vps_terminal_output.document().setDocumentMargin(8)
        except Exception:
            pass
        terminal_box.addWidget(self.settings_vps_terminal_output, 1)
        terminal_cmd_row = QHBoxLayout()
        terminal_cmd_row.setSpacing(8)
        terminal_prompt = QLabel(">")
        terminal_prompt.setObjectName("bodyText")
        terminal_prompt.setFixedWidth(16)
        terminal_cmd_row.addWidget(terminal_prompt, 0)
        self.settings_vps_terminal_input = _VpsTerminalCommandEdit()
        self.settings_vps_terminal_input.setMinimumHeight(36)
        self.settings_vps_terminal_input.setPlaceholderText("输入命令后回车执行，↑/↓ 取历史命令")
        self.settings_vps_terminal_input.set_history_handlers(
            lambda: self._navigate_vps_terminal_history(-1),
            lambda: self._navigate_vps_terminal_history(1),
        )
        self._fluent_input(self.settings_vps_terminal_input)
        self.settings_vps_terminal_input.returnPressed.connect(self._send_vps_terminal_command)
        terminal_cmd_row.addWidget(self.settings_vps_terminal_input, 1)
        self.settings_vps_terminal_send_btn = QPushButton("执行")
        self.settings_vps_terminal_send_btn.setStyleSheet(self._action_button_style())
        self.settings_vps_terminal_send_btn.clicked.connect(self._send_vps_terminal_command)
        terminal_cmd_row.addWidget(self.settings_vps_terminal_send_btn, 0)
        self.settings_vps_terminal_clear_btn = QPushButton("清空")
        self.settings_vps_terminal_clear_btn.setStyleSheet(self._action_button_style(kind="subtle"))
        self.settings_vps_terminal_clear_btn.clicked.connect(self._clear_vps_terminal_output)
        terminal_cmd_row.addWidget(self.settings_vps_terminal_clear_btn, 0)
        terminal_box.addLayout(terminal_cmd_row)
        terminal_toolbar = QHBoxLayout()
        terminal_toolbar.setSpacing(8)
        self.settings_vps_terminal_connect_btn = QPushButton("连接终端")
        self.settings_vps_terminal_connect_btn.setStyleSheet(self._action_button_style(primary=True))
        self.settings_vps_terminal_connect_btn.clicked.connect(self._connect_vps_terminal)
        terminal_toolbar.addWidget(self.settings_vps_terminal_connect_btn, 0)
        self.settings_vps_terminal_disconnect_btn = QPushButton("断开终端")
        self.settings_vps_terminal_disconnect_btn.setStyleSheet(self._action_button_style())
        self.settings_vps_terminal_disconnect_btn.clicked.connect(self._disconnect_vps_terminal)
        terminal_toolbar.addWidget(self.settings_vps_terminal_disconnect_btn, 0)
        terminal_toolbar.addStretch(1)
        terminal_box.addLayout(terminal_toolbar)
        vps_layout.addWidget(terminal_card)

        deploy_card = self._panel_card()
        deploy_box = QVBoxLayout(deploy_card)
        deploy_box.setContentsMargins(20, 18, 20, 18)
        deploy_box.setSpacing(10)
        deploy_title = QLabel("一键直接部署")
        deploy_title.setObjectName("cardTitle")
        deploy_box.addWidget(deploy_title)
        deploy_desc = QLabel("可选择上传本地 agant 项目，或在服务器拉取原始 agant 仓库，然后直接在远端目录准备可通过 SSH 使用的运行环境。")
        deploy_desc.setWordWrap(True)
        deploy_desc.setObjectName("cardDesc")
        deploy_box.addWidget(deploy_desc)

        source_row = QHBoxLayout()
        source_row.setSpacing(8)
        source_label = QLabel("部署来源")
        source_label.setMinimumWidth(92)
        source_label.setObjectName("bodyText")
        source_row.addWidget(source_label, 0)
        self.settings_vps_deploy_source_combo = _StablePopupComboBox()
        self.settings_vps_deploy_source_combo.addItem("上传本地 agant 项目", "upload")
        self.settings_vps_deploy_source_combo.addItem("服务器拉取原始 agant", "git")
        self._apply_theme_combo_style(self.settings_vps_deploy_source_combo)
        self.settings_vps_deploy_source_combo.currentIndexChanged.connect(self._on_vps_deploy_source_changed)
        source_row.addWidget(self.settings_vps_deploy_source_combo, 1)
        deploy_box.addLayout(source_row)

        local_row = QHBoxLayout()
        local_row.setSpacing(8)
        local_label = QLabel("本地目录")
        local_label.setMinimumWidth(92)
        local_label.setObjectName("bodyText")
        local_row.addWidget(local_label, 0)
        self.settings_vps_local_agent_dir_edit = QLineEdit()
        self.settings_vps_local_agent_dir_edit.setPlaceholderText("选择本地 agant 项目目录")
        self._fluent_input(self.settings_vps_local_agent_dir_edit)
        local_row.addWidget(self.settings_vps_local_agent_dir_edit, 1)
        self.settings_vps_local_agent_browse_btn = QPushButton("浏览")
        self.settings_vps_local_agent_browse_btn.setStyleSheet(self._action_button_style())
        self.settings_vps_local_agent_browse_btn.clicked.connect(self._browse_vps_local_agent_dir)
        local_row.addWidget(self.settings_vps_local_agent_browse_btn, 0)
        deploy_box.addLayout(local_row)

        repo_row = QHBoxLayout()
        repo_row.setSpacing(8)
        repo_label = QLabel("仓库地址")
        repo_label.setMinimumWidth(92)
        repo_label.setObjectName("bodyText")
        repo_row.addWidget(repo_label, 0)
        self.settings_vps_repo_url_edit = QLineEdit()
        self.settings_vps_repo_url_edit.setPlaceholderText("例如 https://github.com/.../GenericAgent.git")
        self._fluent_input(self.settings_vps_repo_url_edit)
        repo_row.addWidget(self.settings_vps_repo_url_edit, 1)
        deploy_box.addLayout(repo_row)

        remote_row = QHBoxLayout()
        remote_row.setSpacing(8)
        remote_label = QLabel("远端目录")
        remote_label.setMinimumWidth(92)
        remote_label.setObjectName("bodyText")
        remote_row.addWidget(remote_label, 0)
        self.settings_vps_remote_dir_edit = QLineEdit()
        self.settings_vps_remote_dir_edit.setPlaceholderText(remote_agent_dir_default(""))
        self._fluent_input(self.settings_vps_remote_dir_edit)
        remote_row.addWidget(self.settings_vps_remote_dir_edit, 1)
        deploy_box.addLayout(remote_row)
        self._refresh_vps_remote_dir_placeholder()

        install_mode_row = QHBoxLayout()
        install_mode_row.setSpacing(8)
        install_mode_label = QLabel("依赖策略")
        install_mode_label.setMinimumWidth(92)
        install_mode_label.setObjectName("bodyText")
        install_mode_row.addWidget(install_mode_label, 0)
        self.settings_vps_dep_install_mode_combo = _StablePopupComboBox()
        self.settings_vps_dep_install_mode_combo.addItem("内置源（推荐，清华）", "offline")
        self.settings_vps_dep_install_mode_combo.addItem("国际源（PyPI）", "global")
        self.settings_vps_dep_install_mode_combo.addItem("自定义源", "mirror")
        self._apply_theme_combo_style(self.settings_vps_dep_install_mode_combo)
        self.settings_vps_dep_install_mode_combo.currentIndexChanged.connect(self._on_vps_dep_install_mode_changed)
        install_mode_row.addWidget(self.settings_vps_dep_install_mode_combo, 1)
        deploy_box.addLayout(install_mode_row)

        mirror_row = QHBoxLayout()
        mirror_row.setSpacing(8)
        mirror_label = QLabel("镜像源")
        mirror_label.setMinimumWidth(92)
        mirror_label.setObjectName("bodyText")
        mirror_row.addWidget(mirror_label, 0)
        self.settings_vps_pip_mirror_edit = QLineEdit()
        self.settings_vps_pip_mirror_edit.setPlaceholderText("仅“自定义源”生效，例如 https://pypi.org/simple")
        self._fluent_input(self.settings_vps_pip_mirror_edit)
        mirror_row.addWidget(self.settings_vps_pip_mirror_edit, 1)
        deploy_box.addLayout(mirror_row)

        exclude_row = QHBoxLayout()
        exclude_row.setSpacing(8)
        exclude_label = QLabel("排除规则")
        exclude_label.setMinimumWidth(92)
        exclude_label.setObjectName("bodyText")
        exclude_row.addWidget(exclude_label, 0)
        self.settings_vps_upload_excludes_edit = QLineEdit()
        self.settings_vps_upload_excludes_edit.setPlaceholderText("逗号分隔，例如 .git,.venv,temp,tests,__pycache__,node_modules")
        self._fluent_input(self.settings_vps_upload_excludes_edit)
        exclude_row.addWidget(self.settings_vps_upload_excludes_edit, 1)
        deploy_box.addLayout(exclude_row)

        self.settings_vps_deploy_notice = QLabel("")
        self.settings_vps_deploy_notice.setWordWrap(True)
        self.settings_vps_deploy_notice.setObjectName("mutedText")
        deploy_box.addWidget(self.settings_vps_deploy_notice)

        deploy_toolbar = QHBoxLayout()
        deploy_toolbar.setSpacing(8)
        self.settings_vps_deploy_btn = QPushButton("直接部署")
        self.settings_vps_deploy_btn.setStyleSheet(self._action_button_style(primary=True))
        self.settings_vps_deploy_btn.clicked.connect(self._deploy_vps_agent_direct)
        deploy_toolbar.addWidget(self.settings_vps_deploy_btn, 0)
        deploy_toolbar.addStretch(1)
        deploy_box.addLayout(deploy_toolbar)
        vps_layout.addWidget(deploy_card)

        takeover_card = self._panel_card()
        takeover_box = QVBoxLayout(takeover_card)
        takeover_box.setContentsMargins(20, 18, 20, 18)
        takeover_box.setSpacing(10)
        takeover_title = QLabel("接管 agant")
        takeover_title.setObjectName("cardTitle")
        takeover_box.addWidget(takeover_title)
        takeover_desc = QLabel("复用当前 VPS 的 SSH 配置，校验远端 agant 路径，并把它注册为启动器可用的远程设备。")
        takeover_desc.setWordWrap(True)
        takeover_desc.setObjectName("cardDesc")
        takeover_box.addWidget(takeover_desc)

        takeover_dir_row = QHBoxLayout()
        takeover_dir_row.setSpacing(8)
        takeover_dir_label = QLabel("agant 路径")
        takeover_dir_label.setMinimumWidth(92)
        takeover_dir_label.setObjectName("bodyText")
        takeover_dir_row.addWidget(takeover_dir_label, 0)
        self.settings_vps_takeover_agent_dir_edit = QLineEdit()
        self.settings_vps_takeover_agent_dir_edit.setPlaceholderText("例如 /root/agant 或 /srv/agant")
        self._fluent_input(self.settings_vps_takeover_agent_dir_edit)
        takeover_dir_row.addWidget(self.settings_vps_takeover_agent_dir_edit, 1)
        takeover_box.addLayout(takeover_dir_row)

        self.settings_vps_takeover_notice = QLabel("")
        self.settings_vps_takeover_notice.setWordWrap(True)
        self.settings_vps_takeover_notice.setObjectName("mutedText")
        takeover_box.addWidget(self.settings_vps_takeover_notice)

        takeover_toolbar = QHBoxLayout()
        takeover_toolbar.setSpacing(8)
        self.settings_vps_takeover_btn = QPushButton("接管并注册 agant")
        self.settings_vps_takeover_btn.setStyleSheet(self._action_button_style(primary=True))
        self.settings_vps_takeover_btn.clicked.connect(self._takeover_vps_agent)
        takeover_toolbar.addWidget(self.settings_vps_takeover_btn, 0)
        takeover_toolbar.addStretch(1)
        takeover_box.addLayout(takeover_toolbar)
        vps_layout.addWidget(takeover_card)

        vps_layout.addStretch(1)

        sch_layout = self._settings_pages["schedule"]["layout"]
        sch_layout.addWidget(
            self._settings_intro(
                "定时任务",
                "这里直接读写上游 sche_tasks 任务，支持新建、编辑、启用，并联动调度器状态。",
            )
        )
        schedule_card = self._panel_card()
        schedule_box = QVBoxLayout(schedule_card)
        schedule_box.setContentsMargins(20, 18, 20, 18)
        schedule_box.setSpacing(10)
        schedule_title = QLabel("上游任务识别")
        schedule_title.setObjectName("cardTitle")
        schedule_box.addWidget(schedule_title)
        schedule_desc = QLabel("AI 在上游新建任务后，点一次刷新就会出现在这里；每张卡片都可以直接编辑。")
        schedule_desc.setWordWrap(True)
        schedule_desc.setObjectName("cardDesc")
        schedule_box.addWidget(schedule_desc)
        schedule_toolbar = QHBoxLayout()
        schedule_toolbar.setSpacing(8)
        schedule_add_btn = QPushButton("新建任务")
        schedule_add_btn.setStyleSheet(self._action_button_style())
        schedule_add_btn.clicked.connect(self._schedule_add_task_card)
        schedule_toolbar.addWidget(schedule_add_btn, 0)
        schedule_refresh_btn = QPushButton("刷新任务")
        schedule_refresh_btn.setStyleSheet(self._action_button_style(primary=True))
        schedule_refresh_btn.clicked.connect(self._reload_schedule_panel)
        schedule_toolbar.addWidget(schedule_refresh_btn, 0)
        schedule_toolbar.addStretch(1)
        schedule_box.addLayout(schedule_toolbar)
        self.settings_schedule_notice = QLabel("")
        self.settings_schedule_notice.setWordWrap(True)
        self.settings_schedule_notice.setObjectName("mutedText")
        schedule_box.addWidget(self.settings_schedule_notice)
        self.settings_schedule_list = QWidget()
        self.settings_schedule_list_layout = QVBoxLayout(self.settings_schedule_list)
        self.settings_schedule_list_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_schedule_list_layout.setSpacing(10)
        schedule_box.addWidget(self.settings_schedule_list)
        sch_layout.addWidget(schedule_card)
        sch_layout.addStretch(1)

        sop_layout = self._settings_pages["sop"]["layout"]
        sop_layout.addWidget(
            self._settings_intro(
                "SOP 文档",
                "这里直接查看和编辑当前 agant `memory/` 下的 SOP / Skill 文档，交互保持和现有设置页一致的轻量风格。",
            )
        )
        sop_card = self._panel_card()
        sop_box = QVBoxLayout(sop_card)
        sop_box.setContentsMargins(20, 18, 20, 18)
        sop_box.setSpacing(10)
        sop_title = QLabel("文档编辑器")
        sop_title.setObjectName("cardTitle")
        sop_box.addWidget(sop_title)
        sop_desc = QLabel("这里只显示上游约定的 `*_sop.md` 与 `SKILL.md`，避免把整块文件系统直接暴露进设置页。")
        sop_desc.setWordWrap(True)
        sop_desc.setObjectName("cardDesc")
        sop_box.addWidget(sop_desc)
        self.settings_sop_notice = QLabel("")
        self.settings_sop_notice.setWordWrap(True)
        self.settings_sop_notice.setObjectName("mutedText")
        sop_box.addWidget(self.settings_sop_notice)

        sop_doc_row = QHBoxLayout()
        sop_doc_row.setSpacing(8)
        sop_doc_label = QLabel("当前文档")
        sop_doc_label.setMinimumWidth(92)
        sop_doc_label.setObjectName("bodyText")
        sop_doc_row.addWidget(sop_doc_label, 0)
        self.settings_sop_doc_combo = _StablePopupComboBox()
        self._apply_theme_combo_style(self.settings_sop_doc_combo)
        self.settings_sop_doc_combo.currentIndexChanged.connect(self._load_selected_sop_document)
        sop_doc_row.addWidget(self.settings_sop_doc_combo, 1)
        self.settings_sop_reload_btn = QPushButton("重新读取")
        self.settings_sop_reload_btn.setStyleSheet(self._action_button_style())
        self.settings_sop_reload_btn.clicked.connect(self._reload_sop_panel)
        sop_doc_row.addWidget(self.settings_sop_reload_btn, 0)
        self.settings_sop_save_btn = QPushButton("保存文档")
        self.settings_sop_save_btn.setStyleSheet(self._action_button_style(primary=True))
        self.settings_sop_save_btn.clicked.connect(self._save_selected_sop_document)
        sop_doc_row.addWidget(self.settings_sop_save_btn, 0)
        sop_box.addLayout(sop_doc_row)

        self.settings_sop_summary_label = QLabel("")
        self.settings_sop_summary_label.setObjectName("softTextSmall")
        self.settings_sop_summary_label.setWordWrap(True)
        sop_box.addWidget(self.settings_sop_summary_label)

        sop_path_row = QHBoxLayout()
        sop_path_row.setSpacing(8)
        sop_path_label = QLabel("文档路径")
        sop_path_label.setMinimumWidth(92)
        sop_path_label.setObjectName("bodyText")
        sop_path_row.addWidget(sop_path_label, 0)
        self.settings_sop_path_value = QLineEdit()
        self.settings_sop_path_value.setReadOnly(True)
        self.settings_sop_path_value.setPlaceholderText("当前还没有可用的 SOP 文档。")
        self._fluent_input(self.settings_sop_path_value)
        sop_path_row.addWidget(self.settings_sop_path_value, 1)
        sop_box.addLayout(sop_path_row)

        self.settings_sop_editor = QPlainTextEdit()
        self.settings_sop_editor.setPlaceholderText("选择一个 SOP / Skill 文档后即可在这里查看和编辑。")
        self.settings_sop_editor.setMinimumHeight(360)
        sop_box.addWidget(self.settings_sop_editor)
        sop_layout.addWidget(sop_card)
        sop_layout.addStretch(1)

        personal_layout = self._settings_pages["personal"]["layout"]
        personal_layout.addWidget(
            self._settings_intro(
                "个性设置",
                "这一页仍然是同一个个性设置页。只有“会话上限”这块会跟随目标设备切换；提醒设置始终是启动器本机设置。",
            )
        )
        personal_card = self._panel_card()
        personal_box = QVBoxLayout(personal_card)
        personal_box.setContentsMargins(20, 18, 20, 18)
        personal_box.setSpacing(10)
        personal_title = QLabel("会话上限")
        personal_title.setObjectName("cardTitle")
        personal_box.addWidget(personal_title)
        personal_desc = QLabel("这里只有会话上限会跟随目标设备切换。数值表示该渠道保留在侧边栏中的活跃会话上限；填 0 表示关闭该渠道的自动清理，默认值是 10。")
        personal_desc.setWordWrap(True)
        personal_desc.setObjectName("cardDesc")
        personal_box.addWidget(personal_desc)
        personal_target_row = QHBoxLayout()
        personal_target_row.setSpacing(8)
        personal_target_label = QLabel("会话上限目标设备")
        personal_target_label.setObjectName("softTextSmall")
        personal_target_row.addWidget(personal_target_label, 0)
        self.settings_personal_target_combo = _StablePopupComboBox()
        self.settings_personal_target_combo.setObjectName("settingsPersonalTargetCombo")
        self._apply_theme_combo_style(self.settings_personal_target_combo)
        self.settings_personal_target_combo.currentIndexChanged.connect(
            lambda index, combo=self.settings_personal_target_combo: self._on_settings_target_changed(index, combo=combo)
        )
        personal_target_row.addWidget(self.settings_personal_target_combo, 1)
        self.settings_personal_target_refresh_btn = QPushButton("刷新设备")
        self.settings_personal_target_refresh_btn.setStyleSheet(self._action_button_style())
        self.settings_personal_target_refresh_btn.clicked.connect(lambda _=False: self._refresh_settings_target_combo(force=True))
        chat_common.set_button_svg_icon(
            self.settings_personal_target_refresh_btn,
            "settings_personal_target_refresh",
            chat_common._SVG_REFRESH,
            color="text_soft",
            size=16,
        )
        personal_target_row.addWidget(self.settings_personal_target_refresh_btn, 0)
        personal_box.addLayout(personal_target_row)
        self.settings_personal_scope_hint = QLabel("")
        self.settings_personal_scope_hint.setWordWrap(True)
        self.settings_personal_scope_hint.setObjectName("mutedText")
        personal_box.addWidget(self.settings_personal_scope_hint)
        personal_toolbar = QHBoxLayout()
        personal_toolbar.setSpacing(8)
        personal_save_btn = QPushButton("保存并立即执行")
        personal_save_btn.setStyleSheet(self._action_button_style(primary=True))
        personal_save_btn.clicked.connect(self._save_archive_settings)
        personal_toolbar.addWidget(personal_save_btn, 0)
        personal_refresh_btn = QPushButton("刷新统计")
        personal_refresh_btn.setStyleSheet(self._action_button_style())
        personal_refresh_btn.clicked.connect(self._reload_personal_panel)
        personal_toolbar.addWidget(personal_refresh_btn, 0)
        personal_toolbar.addStretch(1)
        personal_box.addLayout(personal_toolbar)
        self.settings_personal_notice = QLabel("")
        self.settings_personal_notice.setWordWrap(True)
        self.settings_personal_notice.setObjectName("mutedText")
        personal_box.addWidget(self.settings_personal_notice)
        self.settings_personal_list = QWidget()
        self.settings_personal_list_layout = QVBoxLayout(self.settings_personal_list)
        self.settings_personal_list_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_personal_list_layout.setSpacing(10)
        personal_box.addWidget(self.settings_personal_list)
        personal_layout.addWidget(personal_card)

        lan_card = self._panel_card()
        lan_box = QVBoxLayout(lan_card)
        lan_box.setContentsMargins(20, 18, 20, 18)
        lan_box.setSpacing(10)
        lan_title = QLabel("局域网 Web 接口")
        lan_title.setObjectName("cardTitle")
        lan_box.addWidget(lan_title)
        lan_desc = QLabel("对接上游 GenericAgent 的 Streamlit 前端。启用并绑定局域网后，同一局域网内的设备可以通过浏览器访问；上游前端没有额外鉴权，请只在可信网络中开启。")
        lan_desc.setWordWrap(True)
        lan_desc.setObjectName("cardDesc")
        lan_box.addWidget(lan_desc)
        self.settings_lan_status = QLabel("")
        self.settings_lan_status.setWordWrap(True)
        self.settings_lan_status.setObjectName("mutedText")
        lan_box.addWidget(self.settings_lan_status)
        self.settings_lan_enabled = QCheckBox("启用局域网 Web 接口")
        self.settings_lan_enabled.toggled.connect(self._refresh_lan_interface_controls_for_enabled)
        lan_box.addWidget(self.settings_lan_enabled)
        self.settings_lan_bind_all = QCheckBox("允许同一局域网设备访问（绑定 0.0.0.0）")
        lan_box.addWidget(self.settings_lan_bind_all)
        self.settings_lan_autostart = QCheckBox("启动器启动后自动开启")
        lan_box.addWidget(self.settings_lan_autostart)
        lan_row = QHBoxLayout()
        lan_row.setSpacing(8)
        lan_port_label = QLabel("端口")
        lan_port_label.setObjectName("softTextSmall")
        lan_row.addWidget(lan_port_label, 0)
        self.settings_lan_port_spin = chat_common.NoWheelSpinBox()
        self.settings_lan_port_spin.setRange(1024, 65535)
        self.settings_lan_port_spin.setValue(8501)
        self.settings_lan_port_spin.setStyleSheet(
            f"QSpinBox {{ background: {C['field_bg']}; color: {C['text']}; border: 1px solid {C['stroke_default']}; border-radius: {F['radius_md']}px; padding: 8px 10px; min-width: 96px; }}"
            f"QSpinBox::up-button, QSpinBox::down-button {{ width: 20px; border: none; background: transparent; }}"
        )
        lan_row.addWidget(self.settings_lan_port_spin, 0)
        lan_frontend_label = QLabel("前端")
        lan_frontend_label.setObjectName("softTextSmall")
        lan_row.addWidget(lan_frontend_label, 0)
        self.settings_lan_frontend_combo = _StablePopupComboBox()
        self.settings_lan_frontend_combo.setObjectName("settingsLanFrontendCombo")
        self.settings_lan_frontend_combo.addItem("默认 Streamlit（stapp.py）", "frontends/stapp.py")
        self.settings_lan_frontend_combo.addItem("备用 Streamlit（stapp2.py）", "frontends/stapp2.py")
        self._apply_theme_combo_style(self.settings_lan_frontend_combo)
        lan_row.addWidget(self.settings_lan_frontend_combo, 1)
        lan_box.addLayout(lan_row)
        lan_toolbar = QHBoxLayout()
        lan_toolbar.setSpacing(8)
        self.settings_lan_save_btn = QPushButton("保存并应用")
        self.settings_lan_save_btn.setStyleSheet(self._action_button_style(primary=True))
        self.settings_lan_save_btn.clicked.connect(self._save_lan_interface_settings)
        lan_toolbar.addWidget(self.settings_lan_save_btn, 0)
        self.settings_lan_start_btn = QPushButton("启动接口")
        self.settings_lan_start_btn.setStyleSheet(self._action_button_style())
        self.settings_lan_start_btn.clicked.connect(lambda _=False: self._start_lan_interface_from_settings())
        lan_toolbar.addWidget(self.settings_lan_start_btn, 0)
        self.settings_lan_stop_btn = QPushButton("停止接口")
        self.settings_lan_stop_btn.setStyleSheet(self._action_button_style())
        self.settings_lan_stop_btn.clicked.connect(lambda _=False: self._stop_lan_interface_process(refresh=True))
        lan_toolbar.addWidget(self.settings_lan_stop_btn, 0)
        self.settings_lan_open_btn = QPushButton("打开本机地址")
        self.settings_lan_open_btn.setStyleSheet(self._action_button_style())
        self.settings_lan_open_btn.clicked.connect(self._open_lan_interface_local_url)
        lan_toolbar.addWidget(self.settings_lan_open_btn, 0)
        self.settings_lan_log_btn = QPushButton("打开日志")
        self.settings_lan_log_btn.setStyleSheet(self._action_button_style())
        self.settings_lan_log_btn.clicked.connect(self._open_lan_interface_log)
        lan_toolbar.addWidget(self.settings_lan_log_btn, 0)
        lan_toolbar.addStretch(1)
        lan_box.addLayout(lan_toolbar)
        personal_layout.addWidget(lan_card)

        notify_card = self._panel_card()
        notify_box = QVBoxLayout(notify_card)
        notify_box.setContentsMargins(20, 18, 20, 18)
        notify_box.setSpacing(10)
        notify_title = QLabel("回复提醒")
        notify_title.setObjectName("cardTitle")
        notify_box.addWidget(notify_title)
        notify_desc = QLabel("分别控制 AI 回复完成后的提示音和完成提示消息。勾选后表示关闭该提醒。")
        notify_desc.setWordWrap(True)
        notify_desc.setObjectName("cardDesc")
        notify_box.addWidget(notify_desc)
        self.settings_disable_reply_sound = QCheckBox("关闭提示音")
        notify_box.addWidget(self.settings_disable_reply_sound)
        self.settings_disable_reply_message = QCheckBox("关闭提示消息")
        notify_box.addWidget(self.settings_disable_reply_message)
        notify_toolbar = QHBoxLayout()
        notify_toolbar.setSpacing(8)
        notify_save_btn = QPushButton("保存提醒设置")
        notify_save_btn.setStyleSheet(self._action_button_style())
        notify_save_btn.clicked.connect(self._save_personal_preferences)
        notify_toolbar.addWidget(notify_save_btn, 0)
        notify_toolbar.addStretch(1)
        notify_box.addLayout(notify_toolbar)
        personal_layout.addWidget(notify_card)
        personal_layout.addStretch(1)

        theme_layout = self._settings_pages["theme"]["layout"]
        theme_layout.addWidget(
            self._settings_intro(
                "主题设置",
                "这里可以单独设置界面字体、字重、主体预设和背景模式。主体预设会整套影响主界面、侧边栏、卡片和按钮层级，背景支持自定义图片，并可设置居中、拉伸或平铺。",
            )
        )
        theme_card = self._panel_card()
        theme_box = QVBoxLayout(theme_card)
        theme_box.setContentsMargins(20, 18, 20, 18)
        theme_box.setSpacing(10)
        theme_title = QLabel("视觉偏好")
        theme_title.setObjectName("cardTitle")
        theme_box.addWidget(theme_title)
        theme_desc = QLabel("保存后会立即应用；同时写入 launcher_config.json，重启后保持不变。")
        theme_desc.setWordWrap(True)
        theme_desc.setObjectName("cardDesc")
        theme_box.addWidget(theme_desc)

        self.settings_theme_notice = QLabel("")
        self.settings_theme_notice.setWordWrap(True)
        self.settings_theme_notice.setObjectName("mutedText")
        theme_box.addWidget(self.settings_theme_notice)

        self.settings_theme_auto_jump_latest = QCheckBox("发送或回复时自动跳到最新消息")
        theme_box.addWidget(self.settings_theme_auto_jump_latest)
        auto_jump_desc = QLabel("默认开启。关闭后，会保留你当前查看的位置，不再因为发送消息或 AI 流式回复而强制跳转。")
        auto_jump_desc.setWordWrap(True)
        auto_jump_desc.setObjectName("softTextSmall")
        theme_box.addWidget(auto_jump_desc)

        font_row = QHBoxLayout()
        font_row.setSpacing(8)
        font_label = QLabel("字体")
        font_label.setMinimumWidth(92)
        font_label.setObjectName("bodyText")
        font_row.addWidget(font_label, 0)
        self.settings_theme_font_combo = _StablePopupComboBox()
        self.settings_theme_font_combo.setMinimumWidth(320)
        self._apply_theme_combo_style(self.settings_theme_font_combo)
        font_row.addWidget(self.settings_theme_font_combo, 1)
        theme_box.addLayout(font_row)

        weight_row = QHBoxLayout()
        weight_row.setSpacing(8)
        weight_label = QLabel("字重")
        weight_label.setMinimumWidth(92)
        weight_label.setObjectName("bodyText")
        weight_row.addWidget(weight_label, 0)
        self.settings_theme_weight_combo = _StablePopupComboBox()
        self.settings_theme_weight_combo.addItem("常规 (400)", "400")
        self.settings_theme_weight_combo.addItem("中等 (500)", "500")
        self.settings_theme_weight_combo.addItem("半粗 (600)", "600")
        self.settings_theme_weight_combo.addItem("粗体 (700)", "700")
        self._apply_theme_combo_style(self.settings_theme_weight_combo)
        weight_row.addWidget(self.settings_theme_weight_combo, 1)
        theme_box.addLayout(weight_row)

        size_row = QHBoxLayout()
        size_row.setSpacing(8)
        size_label = QLabel("字号")
        size_label.setMinimumWidth(92)
        size_label.setObjectName("bodyText")
        size_row.addWidget(size_label, 0)
        self.settings_theme_size_combo = _StablePopupComboBox()
        self.settings_theme_size_combo.addItem("11 (紧凑)", "11")
        self.settings_theme_size_combo.addItem("12 (偏小)", "12")
        self.settings_theme_size_combo.addItem("13", "13")
        self.settings_theme_size_combo.addItem("14 (默认)", "14")
        self.settings_theme_size_combo.addItem("15", "15")
        self.settings_theme_size_combo.addItem("16", "16")
        self.settings_theme_size_combo.addItem("18", "18")
        self.settings_theme_size_combo.addItem("20", "20")
        self._apply_theme_combo_style(self.settings_theme_size_combo)
        size_row.addWidget(self.settings_theme_size_combo, 1)
        theme_box.addLayout(size_row)

        visual_row = QHBoxLayout()
        visual_row.setSpacing(8)
        visual_label = QLabel("主体预设")
        visual_label.setMinimumWidth(92)
        visual_label.setObjectName("bodyText")
        visual_row.addWidget(visual_label, 0)
        self.settings_theme_visual_combo = _StablePopupComboBox()
        for preset_key, preset_label in theme_visual_preset_options():
            self.settings_theme_visual_combo.addItem(str(preset_label), str(preset_key))
        self._apply_theme_combo_style(self.settings_theme_visual_combo)
        visual_row.addWidget(self.settings_theme_visual_combo, 1)
        theme_box.addLayout(visual_row)

        bg_row = QHBoxLayout()
        bg_row.setSpacing(8)
        bg_label = QLabel("背景模式")
        bg_label.setMinimumWidth(92)
        bg_label.setObjectName("bodyText")
        bg_row.addWidget(bg_label, 0)
        self.settings_theme_bg_combo = _StablePopupComboBox()
        self.settings_theme_bg_combo.addItem("跟随主体色面", "default")
        self.settings_theme_bg_combo.addItem("图片背景", "image")
        self._apply_theme_combo_style(self.settings_theme_bg_combo)
        bg_row.addWidget(self.settings_theme_bg_combo, 1)
        theme_box.addLayout(bg_row)

        bg_img_row = QHBoxLayout()
        bg_img_row.setSpacing(8)
        bg_img_label = QLabel("背景图片")
        bg_img_label.setMinimumWidth(92)
        bg_img_label.setObjectName("bodyText")
        bg_img_row.addWidget(bg_img_label, 0)
        self.settings_theme_bg_image_path = QLineEdit()
        self.settings_theme_bg_image_path.setReadOnly(True)
        self.settings_theme_bg_image_path.setPlaceholderText("未选择图片")
        self._fluent_input(self.settings_theme_bg_image_path)
        bg_img_row.addWidget(self.settings_theme_bg_image_path, 1)
        bg_img_choose_btn = QPushButton("选择图片")
        bg_img_choose_btn.setStyleSheet(self._action_button_style())
        bg_img_choose_btn.clicked.connect(self._choose_theme_background_image)
        bg_img_row.addWidget(bg_img_choose_btn, 0)
        bg_img_clear_btn = QPushButton("清除")
        bg_img_clear_btn.setStyleSheet(self._action_button_style(kind="subtle"))
        bg_img_clear_btn.clicked.connect(self._clear_theme_background_image)
        bg_img_row.addWidget(bg_img_clear_btn, 0)
        theme_box.addLayout(bg_img_row)

        bg_mode_row = QHBoxLayout()
        bg_mode_row.setSpacing(8)
        bg_mode_label = QLabel("图片显示")
        bg_mode_label.setMinimumWidth(92)
        bg_mode_label.setObjectName("bodyText")
        bg_mode_row.addWidget(bg_mode_label, 0)
        self.settings_theme_bg_mode_combo = _StablePopupComboBox()
        self.settings_theme_bg_mode_combo.addItem("居中", "center")
        self.settings_theme_bg_mode_combo.addItem("拉伸", "stretch")
        self.settings_theme_bg_mode_combo.addItem("平铺", "tile")
        self._apply_theme_combo_style(self.settings_theme_bg_mode_combo)
        bg_mode_row.addWidget(self.settings_theme_bg_mode_combo, 1)
        theme_box.addLayout(bg_mode_row)

        fade_row = QHBoxLayout()
        fade_row.setSpacing(8)
        fade_label = QLabel("淡化值")
        fade_label.setMinimumWidth(92)
        fade_label.setObjectName("bodyText")
        fade_row.addWidget(fade_label, 0)
        self.settings_theme_fade_slider = QSlider(Qt.Horizontal)
        self.settings_theme_fade_slider.setRange(0, 100)
        self.settings_theme_fade_slider.setValue(0)
        self.settings_theme_fade_slider.valueChanged.connect(self._on_theme_fade_changed)
        fade_row.addWidget(self.settings_theme_fade_slider, 1)
        self.settings_theme_fade_value = QLabel("0")
        self.settings_theme_fade_value.setObjectName("softTextSmall")
        self.settings_theme_fade_value.setFixedWidth(42)
        fade_row.addWidget(self.settings_theme_fade_value, 0)
        theme_box.addLayout(fade_row)

        avatar_sep = QFrame()
        avatar_sep.setObjectName("divider")
        avatar_sep.setFixedHeight(1)
        theme_box.addWidget(avatar_sep)

        avatar_title = QLabel("聊天头像")
        avatar_title.setObjectName("cardTitle")
        theme_box.addWidget(avatar_title)
        avatar_desc = QLabel("默认使用内敛的线性 SVG 头像；也可以分别为你和 AI 选择本地图片，保存后会立即刷新现有聊天消息。")
        avatar_desc.setWordWrap(True)
        avatar_desc.setObjectName("cardDesc")
        theme_box.addWidget(avatar_desc)

        user_avatar_row = QHBoxLayout()
        user_avatar_row.setSpacing(8)
        user_avatar_label = QLabel("我的头像")
        user_avatar_label.setMinimumWidth(92)
        user_avatar_label.setObjectName("bodyText")
        user_avatar_row.addWidget(user_avatar_label, 0)
        self.settings_theme_user_avatar_path = QLineEdit()
        self.settings_theme_user_avatar_path.setReadOnly(True)
        self.settings_theme_user_avatar_path.setPlaceholderText("默认极简头像")
        self._fluent_input(self.settings_theme_user_avatar_path)
        user_avatar_row.addWidget(self.settings_theme_user_avatar_path, 1)
        user_avatar_choose_btn = QPushButton("选择图片")
        user_avatar_choose_btn.setStyleSheet(self._action_button_style())
        user_avatar_choose_btn.clicked.connect(lambda: self._choose_theme_chat_avatar("user"))
        user_avatar_row.addWidget(user_avatar_choose_btn, 0)
        user_avatar_clear_btn = QPushButton("清除")
        user_avatar_clear_btn.setStyleSheet(self._action_button_style(kind="subtle"))
        user_avatar_clear_btn.clicked.connect(lambda: self._clear_theme_chat_avatar("user"))
        user_avatar_row.addWidget(user_avatar_clear_btn, 0)
        theme_box.addLayout(user_avatar_row)

        ai_avatar_row = QHBoxLayout()
        ai_avatar_row.setSpacing(8)
        ai_avatar_label = QLabel("AI 头像")
        ai_avatar_label.setMinimumWidth(92)
        ai_avatar_label.setObjectName("bodyText")
        ai_avatar_row.addWidget(ai_avatar_label, 0)
        self.settings_theme_ai_avatar_path = QLineEdit()
        self.settings_theme_ai_avatar_path.setReadOnly(True)
        self.settings_theme_ai_avatar_path.setPlaceholderText("默认极简头像")
        self._fluent_input(self.settings_theme_ai_avatar_path)
        ai_avatar_row.addWidget(self.settings_theme_ai_avatar_path, 1)
        ai_avatar_choose_btn = QPushButton("选择图片")
        ai_avatar_choose_btn.setStyleSheet(self._action_button_style())
        ai_avatar_choose_btn.clicked.connect(lambda: self._choose_theme_chat_avatar("assistant"))
        ai_avatar_row.addWidget(ai_avatar_choose_btn, 0)
        ai_avatar_clear_btn = QPushButton("清除")
        ai_avatar_clear_btn.setStyleSheet(self._action_button_style(kind="subtle"))
        ai_avatar_clear_btn.clicked.connect(lambda: self._clear_theme_chat_avatar("assistant"))
        ai_avatar_row.addWidget(ai_avatar_clear_btn, 0)
        theme_box.addLayout(ai_avatar_row)

        theme_toolbar = QHBoxLayout()
        theme_toolbar.setSpacing(8)
        theme_save_btn = QPushButton("保存主题设置")
        theme_save_btn.setStyleSheet(self._action_button_style(primary=True))
        theme_save_btn.clicked.connect(self._save_theme_preferences)
        chat_common.set_button_svg_icon(theme_save_btn, "settings_theme_save", chat_common._SVG_SWATCH, color="selection_fg", size=16)
        theme_toolbar.addWidget(theme_save_btn, 0)
        theme_toolbar.addStretch(1)
        theme_box.addLayout(theme_toolbar)

        floating_sep = QFrame()
        floating_sep.setObjectName("divider")
        floating_sep.setFixedHeight(1)
        theme_box.addWidget(floating_sep)

        floating_title = QLabel("悬浮窗背景")
        floating_title.setObjectName("cardTitle")
        theme_box.addWidget(floating_title)
        floating_desc = QLabel("可为悬浮窗单独配置背景图，不影响主界面背景。默认“跟随主背景”。")
        floating_desc.setWordWrap(True)
        floating_desc.setObjectName("cardDesc")
        theme_box.addWidget(floating_desc)

        floating_bg_row = QHBoxLayout()
        floating_bg_row.setSpacing(8)
        floating_bg_label = QLabel("悬浮窗预设")
        floating_bg_label.setMinimumWidth(92)
        floating_bg_label.setObjectName("bodyText")
        floating_bg_row.addWidget(floating_bg_label, 0)
        self.settings_theme_floating_bg_combo = _StablePopupComboBox()
        self.settings_theme_floating_bg_combo.addItem("跟随主背景", "follow")
        self.settings_theme_floating_bg_combo.addItem("图片背景", "image")
        self._apply_theme_combo_style(self.settings_theme_floating_bg_combo)
        floating_bg_row.addWidget(self.settings_theme_floating_bg_combo, 1)
        theme_box.addLayout(floating_bg_row)

        floating_img_row = QHBoxLayout()
        floating_img_row.setSpacing(8)
        floating_img_label = QLabel("悬浮窗图片")
        floating_img_label.setMinimumWidth(92)
        floating_img_label.setObjectName("bodyText")
        floating_img_row.addWidget(floating_img_label, 0)
        self.settings_theme_floating_bg_image_path = QLineEdit()
        self.settings_theme_floating_bg_image_path.setReadOnly(True)
        self.settings_theme_floating_bg_image_path.setPlaceholderText("未选择图片")
        self._fluent_input(self.settings_theme_floating_bg_image_path)
        floating_img_row.addWidget(self.settings_theme_floating_bg_image_path, 1)
        floating_img_choose_btn = QPushButton("选择图片")
        floating_img_choose_btn.setStyleSheet(self._action_button_style())
        floating_img_choose_btn.clicked.connect(self._choose_theme_floating_background_image)
        floating_img_row.addWidget(floating_img_choose_btn, 0)
        floating_img_clear_btn = QPushButton("清除")
        floating_img_clear_btn.setStyleSheet(self._action_button_style(kind="subtle"))
        floating_img_clear_btn.clicked.connect(self._clear_theme_floating_background_image)
        floating_img_row.addWidget(floating_img_clear_btn, 0)
        theme_box.addLayout(floating_img_row)

        floating_mode_row = QHBoxLayout()
        floating_mode_row.setSpacing(8)
        floating_mode_label = QLabel("悬浮窗显示")
        floating_mode_label.setMinimumWidth(92)
        floating_mode_label.setObjectName("bodyText")
        floating_mode_row.addWidget(floating_mode_label, 0)
        self.settings_theme_floating_bg_mode_combo = _StablePopupComboBox()
        self.settings_theme_floating_bg_mode_combo.addItem("居中", "center")
        self.settings_theme_floating_bg_mode_combo.addItem("拉伸", "stretch")
        self.settings_theme_floating_bg_mode_combo.addItem("平铺", "tile")
        self._apply_theme_combo_style(self.settings_theme_floating_bg_mode_combo)
        floating_mode_row.addWidget(self.settings_theme_floating_bg_mode_combo, 1)
        theme_box.addLayout(floating_mode_row)

        floating_fade_row = QHBoxLayout()
        floating_fade_row.setSpacing(8)
        floating_fade_label = QLabel("悬浮窗淡化")
        floating_fade_label.setMinimumWidth(92)
        floating_fade_label.setObjectName("bodyText")
        floating_fade_row.addWidget(floating_fade_label, 0)
        self.settings_theme_floating_fade_slider = QSlider(Qt.Horizontal)
        self.settings_theme_floating_fade_slider.setRange(0, 100)
        self.settings_theme_floating_fade_slider.setValue(0)
        self.settings_theme_floating_fade_slider.valueChanged.connect(self._on_theme_floating_fade_changed)
        floating_fade_row.addWidget(self.settings_theme_floating_fade_slider, 1)
        self.settings_theme_floating_fade_value = QLabel("0")
        self.settings_theme_floating_fade_value.setObjectName("softTextSmall")
        self.settings_theme_floating_fade_value.setFixedWidth(42)
        floating_fade_row.addWidget(self.settings_theme_floating_fade_value, 0)
        theme_box.addLayout(floating_fade_row)

        theme_layout.addWidget(theme_card)
        theme_layout.addStretch(1)

        usage_layout = self._settings_pages["usage"]["layout"]
        usage_layout.addWidget(
            self._settings_intro(
                "使用日志",
                "这里汇总本地会话里的 token / 费用 / 模型 / 渠道 / 会话活动日志，并补充 Langfuse 追踪配置状态。标注说明：真实 = 直接读取模型接口返回的 usage；估算 = 按字符数 / 2.5 回推；混合 = 同一统计范围里两者都有。",
            )
        )
        usage_card = self._panel_card()
        usage_box = QVBoxLayout(usage_card)
        usage_box.setContentsMargins(16, 14, 16, 14)
        usage_box.setSpacing(8)
        usage_title = QLabel("日志总览")
        usage_title.setObjectName("cardTitle")
        usage_box.addWidget(usage_title)
        usage_desc = QLabel("这里显示的是启动器可见的本地日志和按设备/API 卡片保存的计价规则；如果上游启用了 Langfuse，也会额外展示追踪配置和接线状态。")
        usage_desc.setWordWrap(True)
        usage_desc.setObjectName("cardDesc")
        usage_box.addWidget(usage_desc)
        self.settings_usage_notice = QLabel("")
        self.settings_usage_notice.setWordWrap(True)
        self.settings_usage_notice.setObjectName("mutedText")
        usage_box.addWidget(self.settings_usage_notice)
        self.settings_usage_list = QWidget()
        self.settings_usage_list_layout = QVBoxLayout(self.settings_usage_list)
        self.settings_usage_list_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_usage_list_layout.setSpacing(10)
        usage_box.addWidget(self.settings_usage_list)
        usage_layout.addWidget(usage_card)
        usage_layout.addStretch(1)

        about_layout = self._settings_pages["about"]["layout"]
        about_layout.addWidget(
            self._settings_intro(
                "关于启动器",
                "这是一个面向 GenericAgent 的桌面启动器，目标是把下载、配置、启动和日常聊天入口收拢到一个更直接的界面里。",
            )
        )
        about_card = self._panel_card()
        about_box = QVBoxLayout(about_card)
        about_box.setContentsMargins(20, 18, 20, 18)
        about_box.setSpacing(10)
        about_title = QLabel("基础信息")
        about_title.setObjectName("cardTitle")
        about_box.addWidget(about_title)
        self.settings_about_list = QWidget()
        self.settings_about_list_layout = QVBoxLayout(self.settings_about_list)
        self.settings_about_list_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_about_list_layout.setSpacing(10)
        about_box.addWidget(self.settings_about_list)
        about_layout.addWidget(about_card)
        about_layout.addStretch(1)

        self._show_settings_category("api", reload=False)
        return page

    def _settings_remote_devices_for_config(self):
        getter = getattr(self, "_remote_devices", None)
        if callable(getter):
            try:
                rows = [dict(item) for item in (getter() or []) if isinstance(item, dict)]
                if rows:
                    return rows
            except Exception:
                pass
        cfg = self._normalize_vps_connection_cfg(self.cfg.get("vps_connection"))
        host = str(cfg.get("host") or "").strip()
        username = str(cfg.get("username") or "").strip()
        if not host or not username:
            return []
        deploy = self._normalize_vps_deploy_cfg(self.cfg.get("vps_deploy"), username=username)
        digest = hashlib.sha1(f"{username}@{host}:{int(cfg.get('port') or 22)}".encode("utf-8", errors="ignore")).hexdigest()[:10]
        return [
            {
                "id": f"remote_{digest}",
                "name": "默认服务器",
                "host": host,
                "username": username,
                "port": int(cfg.get("port") or 22),
                "ssh_key_path": str(cfg.get("ssh_key_path") or "").strip(),
                "password": str(cfg.get("password") or "").strip(),
                "agent_dir": normalize_remote_agent_dir(deploy.get("remote_dir"), username=username),
                "python_cmd": "python3",
                "auto_ssh": True,
            }
        ]

    def _settings_remote_device_by_id(self, device_id: str):
        did = str(device_id or "").strip()
        if not did:
            return None
        for item in self._settings_remote_devices_for_config():
            if str(item.get("id") or "").strip() == did:
                return item
        return None

    def _normalize_settings_target(self, raw):
        item = raw if isinstance(raw, dict) else {}
        scope = str(item.get("scope") or "local").strip().lower()
        device_id = str(item.get("device_id") or "local").strip()
        if scope not in ("local", "remote"):
            scope = "local"
        if scope == "remote":
            if not device_id:
                scope = "local"
                device_id = "local"
            elif not self._settings_remote_device_by_id(device_id):
                scope = "local"
                device_id = "local"
        else:
            device_id = "local"
        return {"scope": scope, "device_id": device_id}

    def _settings_target_context(self):
        current = self._normalize_settings_target(
            {
                "scope": getattr(self, "_settings_target_scope", "local"),
                "device_id": getattr(self, "_settings_target_device_id", "local"),
            }
        )
        scope = current["scope"]
        device_id = current["device_id"]
        if scope == "remote":
            dev = self._settings_remote_device_by_id(device_id)
            if not isinstance(dev, dict):
                scope = "local"
                device_id = "local"
                dev = None
        else:
            dev = None
        label = "本机"
        if scope == "remote":
            label = str((dev or {}).get("name") or (dev or {}).get("host") or "远程设备").strip() or "远程设备"
        return {
            "scope": scope,
            "device_id": device_id,
            "device": dev,
            "is_remote": scope == "remote",
            "label": label,
        }

    def _settings_target_remote_agent_dir(self, device) -> str:
        dev = device if isinstance(device, dict) else {}
        return remote_device_agent_dir(dev, username=dev.get("username"))

    def _settings_target_remote_stage_dir(self, device) -> str:
        dev = device if isinstance(device, dict) else {}
        raw = str(dev.get("id") or dev.get("host") or "remote-device").strip() or "remote-device"
        safe = "".join(ch for ch in raw if (ch.isalnum() or ch in ("_", "-")))
        if not safe:
            safe = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"/tmp/genericagent_launcher_remote/{safe}/settings_target"

    def _settings_target_display_path(self, file_name: str):
        ctx = self._settings_target_context()
        if not bool(ctx.get("is_remote")):
            return os.path.join(self.agent_dir, str(file_name or "").strip())
        dev = ctx.get("device") or {}
        remote_dir = self._settings_target_remote_agent_dir(dev).rstrip("/")
        return remote_dir + "/" + str(file_name or "").strip()

    def _settings_target_supports_remote(self):
        return bool(self._settings_remote_devices_for_config())

    def _settings_category_scope_mode(self, key: str):
        category = str(key or "").strip().lower()
        if category in ("api", "channels", "schedule", "sop", "usage"):
            return "target"
        return "local"

    def _settings_sop_normalize_relpath(self, relpath: str) -> str:
        text = str(relpath or "").strip().replace("\\", "/")
        if not text or text.startswith("/"):
            return ""
        normalized = os.path.normpath(text).replace("\\", "/")
        if normalized in (".", "..") or normalized.startswith("../"):
            return ""
        if not normalized.startswith("memory/"):
            return ""
        name = os.path.basename(normalized)
        if name == "SKILL.md" or name.endswith("_sop.md"):
            return normalized
        return ""

    def _settings_sop_label(self, relpath: str) -> str:
        normalized = self._settings_sop_normalize_relpath(relpath)
        if not normalized:
            return ""
        return normalized[len("memory/"):] if normalized.startswith("memory/") else normalized

    def _settings_target_list_sop_documents(self):
        ctx = self._settings_target_context()
        docs: list[dict[str, str]] = []
        if not bool(ctx.get("is_remote")):
            if not lz.is_valid_agent_dir(self.agent_dir):
                return docs, "请先选择有效的 GenericAgent 目录。"
            memory_dir = os.path.join(self.agent_dir, "memory")
            if not os.path.isdir(memory_dir):
                return docs, "当前目录下未找到 `memory/`，暂时没有可用的 SOP 文档。"
            seen = set()
            for root_dir, dir_names, file_names in os.walk(memory_dir):
                dir_names[:] = sorted(dir_names)
                for name in sorted(file_names):
                    if name != "SKILL.md" and (not name.endswith("_sop.md")):
                        continue
                    abs_path = os.path.join(root_dir, name)
                    relpath = os.path.relpath(abs_path, self.agent_dir).replace("\\", "/")
                    normalized = self._settings_sop_normalize_relpath(relpath)
                    if (not normalized) or normalized in seen:
                        continue
                    seen.add(normalized)
                    docs.append({"relpath": normalized, "label": self._settings_sop_label(normalized)})
            docs.sort(key=lambda item: str(item.get("label") or "").lower())
            return docs, ""

        dev = ctx.get("device") or {}
        client, err = self._settings_target_open_remote_client(dev, timeout=10)
        if client is None:
            return docs, err
        try:
            remote_root = self._settings_target_remote_agent_dir(dev).rstrip("/")
            remote_memory_dir = remote_root + "/memory"
            cmd = (
                f"if [ ! -d {shlex.quote(remote_memory_dir)} ]; then exit 7; fi; "
                f"find {shlex.quote(remote_memory_dir)} -type f \\( -name '*_sop.md' -o -name 'SKILL.md' \\) -print | LC_ALL=C sort"
            )
            rc, out, err_text = self._vps_exec_remote(client, cmd, timeout=20)
            if rc != 0:
                if int(rc) == 7:
                    return docs, f"{self._settings_target_display_path('memory')} 下未找到可用的 SOP 文档。"
                detail = str(err_text or out or "").strip()
                return docs, detail or "远端 SOP 文档列表读取失败。"
            prefix = remote_root.rstrip("/") + "/"
            seen = set()
            for line in str(out or "").splitlines():
                raw_path = str(line or "").strip()
                if (not raw_path) or (not raw_path.startswith(prefix)):
                    continue
                relpath = raw_path[len(prefix):].replace("\\", "/")
                normalized = self._settings_sop_normalize_relpath(relpath)
                if (not normalized) or normalized in seen:
                    continue
                seen.add(normalized)
                docs.append({"relpath": normalized, "label": self._settings_sop_label(normalized)})
            docs.sort(key=lambda item: str(item.get("label") or "").lower())
            return docs, ""
        except Exception as e:
            return docs, str(e)
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _settings_target_read_sop_text(self, relpath: str):
        normalized = self._settings_sop_normalize_relpath(relpath)
        if not normalized:
            return False, "", self._settings_target_display_path("memory"), "当前选择的 SOP 文档路径无效。"
        ctx = self._settings_target_context()
        if not bool(ctx.get("is_remote")):
            if not lz.is_valid_agent_dir(self.agent_dir):
                return False, "", self._settings_target_display_path(normalized), "请先选择有效的 GenericAgent 目录。"
            local_path = os.path.join(self.agent_dir, normalized.replace("/", os.sep))
            try:
                with open(local_path, "r", encoding="utf-8", errors="replace") as f:
                    return True, f.read(), local_path, ""
            except Exception as e:
                return False, "", local_path, str(e)

        dev = ctx.get("device") or {}
        client, err = self._settings_target_open_remote_client(dev, timeout=10)
        if client is None:
            return False, "", self._settings_target_display_path(normalized), err
        try:
            remote_root = self._settings_target_remote_agent_dir(dev).rstrip("/")
            remote_fp = remote_root + "/" + normalized
            sftp = client.open_sftp()
            try:
                with sftp.open(remote_fp, "rb") as fp:
                    raw = fp.read()
            finally:
                try:
                    sftp.close()
                except Exception:
                    pass
            text = raw.decode("utf-8", errors="replace") if raw else ""
            return True, text, self._settings_target_display_path(normalized), ""
        except Exception as e:
            return False, "", self._settings_target_display_path(normalized), str(e)
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _settings_target_write_sop_text(self, relpath: str, text: str):
        normalized = self._settings_sop_normalize_relpath(relpath)
        if not normalized:
            return False, self._settings_target_display_path("memory"), "当前选择的 SOP 文档路径无效。"
        body = str(text or "")
        ctx = self._settings_target_context()
        if not bool(ctx.get("is_remote")):
            if not lz.is_valid_agent_dir(self.agent_dir):
                return False, self._settings_target_display_path(normalized), "请先选择有效的 GenericAgent 目录。"
            local_path = os.path.join(self.agent_dir, normalized.replace("/", os.sep))
            try:
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                with open(local_path, "w", encoding="utf-8") as f:
                    f.write(body)
                return True, local_path, ""
            except Exception as e:
                return False, local_path, str(e)

        dev = ctx.get("device") or {}
        client, err = self._settings_target_open_remote_client(dev, timeout=12)
        if client is None:
            return False, self._settings_target_display_path(normalized), err
        try:
            remote_root = self._settings_target_remote_agent_dir(dev).rstrip("/")
            remote_fp = remote_root + "/" + normalized
            parent_dir = remote_fp.rsplit("/", 1)[0]
            rc, _out, mkdir_err = self._vps_exec_remote(client, f"mkdir -p {shlex.quote(parent_dir)}", timeout=20)
            if rc != 0:
                base_err = "远端 SOP 文档目录准备失败。"
                return False, self._settings_target_display_path(normalized), str(mkdir_err or base_err).strip() or base_err
            tmp_name = f".launcher_sop.tmp.{int(time.time() * 1000)}"
            write_fp = parent_dir.rstrip("/") + "/" + tmp_name
            sftp = client.open_sftp()
            try:
                with sftp.open(write_fp, "wb") as fp:
                    fp.write(body.encode("utf-8"))
            finally:
                try:
                    sftp.close()
                except Exception:
                    pass
            mv_cmd = f"mv -f {shlex.quote(write_fp)} {shlex.quote(remote_fp)}"
            rc, _out, mv_err = self._vps_exec_remote(client, mv_cmd, timeout=20)
            if rc != 0:
                base_err = "写入远端 SOP 文档失败。"
                return False, self._settings_target_display_path(normalized), str(mv_err or base_err).strip() or base_err
            return True, self._settings_target_display_path(normalized), ""
        except Exception as e:
            return False, self._settings_target_display_path(normalized), str(e)
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _current_settings_sop_relpath(self) -> str:
        combo = getattr(self, "settings_sop_doc_combo", None)
        if combo is None:
            return str(getattr(self, "_settings_sop_selected_relpath", "") or "").strip()
        try:
            value = combo.currentData()
        except Exception:
            value = None
        if value in (None, ""):
            try:
                index = int(combo.currentIndex())
            except Exception:
                index = -1
            if index >= 0:
                try:
                    value = combo.itemData(index)
                except Exception:
                    value = None
        return self._settings_sop_normalize_relpath(str(value or getattr(self, "_settings_sop_selected_relpath", "") or "").strip())

    def _sync_settings_sop_doc_combo(self, docs, preferred_relpath: str = "") -> str:
        combo = getattr(self, "settings_sop_doc_combo", None)
        if combo is None:
            return ""
        items = list(docs or [])
        relpaths = [self._settings_sop_normalize_relpath(item.get("relpath")) for item in items if isinstance(item, dict)]
        relpaths = [item for item in relpaths if item]
        target_relpath = self._settings_sop_normalize_relpath(preferred_relpath)
        if target_relpath not in relpaths:
            target_relpath = relpaths[0] if relpaths else ""
        combo.blockSignals(True)
        try:
            combo.clear()
            for item in items:
                relpath = self._settings_sop_normalize_relpath(item.get("relpath"))
                if not relpath:
                    continue
                combo.addItem(str(item.get("label") or self._settings_sop_label(relpath)), relpath)
            if target_relpath:
                index = combo.findData(target_relpath)
                combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            combo.blockSignals(False)
        return target_relpath

    def _load_selected_sop_document(self, *_args):
        relpath = self._current_settings_sop_relpath()
        editor = getattr(self, "settings_sop_editor", None)
        notice = getattr(self, "settings_sop_notice", None)
        path_edit = getattr(self, "settings_sop_path_value", None)
        save_btn = getattr(self, "settings_sop_save_btn", None)
        reload_btn = getattr(self, "settings_sop_reload_btn", None)
        if not relpath:
            if editor is not None:
                editor.clear()
                try:
                    editor.document().setModified(False)
                except Exception:
                    pass
                editor.setReadOnly(True)
            if path_edit is not None:
                path_edit.clear()
            if notice is not None:
                notice.setText("当前目标下还没有可用的 SOP / Skill 文档。")
            if save_btn is not None:
                save_btn.setEnabled(False)
            if reload_btn is not None:
                reload_btn.setEnabled(False)
            self._settings_sop_selected_relpath = ""
            return
        ok, text, display_path, err = self._settings_target_read_sop_text(relpath)
        if path_edit is not None:
            path_edit.setText(str(display_path or ""))
        if not ok:
            if editor is not None:
                editor.clear()
                try:
                    editor.document().setModified(False)
                except Exception:
                    pass
                editor.setReadOnly(True)
            if notice is not None:
                notice.setText(str(err or "读取 SOP 文档失败。"))
            if save_btn is not None:
                save_btn.setEnabled(False)
            if reload_btn is not None:
                reload_btn.setEnabled(True)
            return
        if editor is not None:
            editor.setReadOnly(False)
            editor.setPlainText(str(text or ""))
            try:
                editor.document().setModified(False)
            except Exception:
                pass
        if notice is not None:
            notice.setText(f"已载入：{self._settings_sop_label(relpath) or relpath}")
        if save_btn is not None:
            save_btn.setEnabled(True)
        if reload_btn is not None:
            reload_btn.setEnabled(True)
        self._settings_sop_selected_relpath = relpath

    def _save_selected_sop_document(self):
        relpath = self._current_settings_sop_relpath()
        editor = getattr(self, "settings_sop_editor", None)
        notice = getattr(self, "settings_sop_notice", None)
        if (not relpath) or editor is None:
            if notice is not None:
                notice.setText("请先选择一个可编辑的 SOP 文档。")
            return
        ok, display_path, err = self._settings_target_write_sop_text(relpath, editor.toPlainText())
        if not ok:
            message = str(err or "保存 SOP 文档失败。")
            if notice is not None:
                notice.setText(message)
            setter = getattr(self, "_set_status", None)
            if callable(setter):
                try:
                    setter(message)
                except Exception:
                    pass
            return
        try:
            editor.document().setModified(False)
        except Exception:
            pass
        if notice is not None:
            notice.setText(f"已保存：{display_path}")
        setter = getattr(self, "_set_status", None)
        if callable(setter):
            try:
                setter("SOP 文档已保存。")
            except Exception:
                pass

    def _reload_sop_panel(self):
        summary = getattr(self, "settings_sop_summary_label", None)
        notice = getattr(self, "settings_sop_notice", None)
        current_relpath = self._current_settings_sop_relpath()
        docs, err = self._settings_target_list_sop_documents()
        selected_relpath = self._sync_settings_sop_doc_combo(docs, current_relpath)
        if summary is not None:
            if docs:
                summary.setText(f"当前共发现 {len(docs)} 份 SOP / Skill 文档。")
            else:
                summary.setText("当前未发现可编辑的 SOP / Skill 文档。")
        if not docs:
            self._load_selected_sop_document()
            if notice is not None and err:
                notice.setText(str(err or ""))
            return
        if notice is not None and err:
            notice.setText(str(err or ""))
        self._settings_sop_selected_relpath = selected_relpath
        self._load_selected_sop_document()

    def _settings_category_uses_target_switch(self, key: str):
        return self._settings_category_scope_mode(key) == "target"

    def _refresh_settings_target_visibility(self, key: str | None = None):
        section = getattr(self, "_settings_target_section", None)
        if section is None:
            return
        category = str(key or getattr(self, "_current_settings_category", "api") or "api").strip().lower()
        visible = bool(self._settings_category_uses_target_switch(category))
        if bool(getattr(self, "_settings_target_section_visible", True)) == visible:
            return
        self._settings_target_section_visible = visible
        section.setVisible(visible)

    def _settings_page_is_visible(self) -> bool:
        pages = getattr(self, "pages", None)
        settings_page = getattr(self, "_settings_page", None)
        if pages is None or settings_page is None:
            return False
        try:
            return pages.currentWidget() is settings_page
        except Exception:
            return False

    def _settings_category_is_visible(self, key: str) -> bool:
        category = str(key or "").strip().lower()
        if not category or not self._settings_page_is_visible():
            return False
        if str(getattr(self, "_current_settings_category", "") or "").strip().lower() != category:
            return False
        stack = getattr(self, "settings_stack", None)
        pages = getattr(self, "_settings_pages", None) or {}
        page_info = pages.get(category) if isinstance(pages, dict) else None
        widget = page_info.get("widget") if isinstance(page_info, dict) else None
        if stack is None or widget is None:
            return True
        try:
            return stack.currentWidget() is widget
        except Exception:
            return True

    def _refresh_settings_category_runtime_state(self, key: str) -> None:
        category = str(key or "").strip().lower()
        if category == "channels":
            refresher = getattr(self, "_refresh_channels_runtime_status_labels", None)
            if callable(refresher):
                try:
                    refresher(force=True)
                except Exception:
                    pass

    def _apply_settings_nav_selection(self, key: str) -> None:
        category = str(key or "").strip().lower()
        buttons = getattr(self, "_settings_nav_buttons", None) or {}
        if not isinstance(buttons, dict):
            return
        previous = str(getattr(self, "_active_settings_nav_key", "") or "").strip().lower()
        if previous == category:
            return
        if previous:
            prev_btn = buttons.get(previous)
            if prev_btn is not None:
                try:
                    prev_btn.setStyleSheet(self._sidebar_button_style(subtle=True))
                    self._apply_settings_nav_button_icon(previous, prev_btn, selected=False)
                except Exception:
                    pass
        btn = buttons.get(category)
        if btn is not None:
            try:
                btn.setStyleSheet(self._sidebar_button_style(selected=True))
                self._apply_settings_nav_button_icon(category, btn, selected=True)
            except Exception:
                pass
        self._active_settings_nav_key = category

    def _settings_switch_generation(self) -> int:
        try:
            return int(getattr(self, "_settings_switch_token", 0) or 0)
        except Exception:
            return 0

    def _bump_settings_switch_generation(self) -> int:
        token = self._settings_switch_generation() + 1
        self._settings_switch_token = token
        return token

    def _schedule_settings_category_activation(self, key: str, *, reload_required: bool, force_reload: bool) -> None:
        category = str(key or "").strip().lower()
        token = self._bump_settings_switch_generation()
        delay_ms = max(0, int(getattr(self, "_SETTINGS_SWITCH_RELOAD_DELAY_MS", 24) or 24))

        def run():
            if int(getattr(self, "_settings_switch_token", 0) or 0) != token:
                return
            if str(getattr(self, "_current_settings_category", "") or "").strip().lower() != category:
                return
            if reload_required:
                self._settings_reload(categories=[category], force=force_reload)
            else:
                self._refresh_settings_status_label(category)
                self._refresh_settings_category_runtime_state(category)

        try:
            QTimer.singleShot(delay_ms, self, run)
        except Exception:
            try:
                QTimer.singleShot(delay_ms, run)
            except Exception:
                run()

    def _refresh_settings_status_label(self, key: str | None = None):
        label = getattr(self, "settings_status_label", None)
        if label is None:
            return
        if not hasattr(self, "_settings_target_scope"):
            target_cfg = self._normalize_settings_target(self.cfg.get("settings_target"))
            self._settings_target_scope = target_cfg["scope"]
            self._settings_target_device_id = target_cfg["device_id"]
        current_key = str(key or getattr(self, "_current_settings_category", "api") or "api").strip().lower()
        scope_mode = self._settings_category_scope_mode(current_key)
        if current_key != "personal" and scope_mode != "target":
            self.settings_status_label.setText("当前页为启动器本机设置，不需要切换目标设备。")
            return
        target_ctx = self._settings_target_context()
        local_valid = lz.is_valid_agent_dir(self.agent_dir)
        if current_key == "personal":
            label.setText(
                "个性设置页包含本机设置；只有“会话上限”卡片内的目标设备会跟随切换，回复提醒始终是启动器本机设置。"
                if local_valid or bool(target_ctx.get("is_remote")) else
                "还没有可用的 GenericAgent 目录，先在上面选择目录；会话上限目标可在卡片内单独选择。"
            )
        elif scope_mode == "target":
            valid = local_valid or bool(target_ctx.get("is_remote"))
            label.setText(
                f"当前页支持多设备切换。设置目标：{target_ctx.get('label')}。"
                if valid else
                "还没有可用的 GenericAgent 目录，先在上面选择目录。"
            )
        else:
            label.setText("当前页为启动器本机设置，不需要切换目标设备。")

    def _settings_target_generation(self) -> int:
        try:
            return int(getattr(self, "_settings_target_change_token", 0) or 0)
        except Exception:
            return 0

    def _bump_settings_target_generation(self) -> int:
        token = self._settings_target_generation() + 1
        self._settings_target_change_token = token
        return token

    def _refresh_settings_target_notice(self):
        notice = getattr(self, "settings_target_notice", None)
        if notice is None:
            return
        category = str(getattr(self, "_current_settings_category", "api") or "api").strip().lower()
        current = self._normalize_settings_target(
            {
                "scope": getattr(self, "_settings_target_scope", "local"),
                "device_id": getattr(self, "_settings_target_device_id", "local"),
            }
        )
        if category == "sop":
            if current["scope"] == "remote":
                path_text = self._settings_target_display_path("memory")
                notice.setText(f"当前目标：远程设备（SSH 宿主机）。SOP 文档会读取并写回 `{path_text}`。")
            else:
                notice.setText("当前目标：本机目录。SOP 文档会读取并写回当前目录下的 `memory/`。")
            return
        if current["scope"] == "remote":
            path_text = self._settings_target_display_path("mykey.py")
            notice.setText(f"当前目标：远程设备（SSH 宿主机）。API/渠道配置会写入 `{path_text}`。")
        else:
            notice.setText("当前目标：本机目录。API/渠道配置会写入当前目录下的 `mykey.py`。")

    def _settings_target_combo_entries(self):
        entries = [("本机（当前目录）", {"scope": "local", "device_id": "local"})]
        for dev in self._settings_remote_devices_for_config():
            did = str(dev.get("id") or "").strip()
            if not did:
                continue
            name = str(dev.get("name") or dev.get("host") or did).strip() or did
            host = str(dev.get("host") or "").strip()
            entries.append((f"{name}（SSH）", {"scope": "remote", "device_id": did, "host": host}))
        return entries

    def _sync_personal_target_combo(self, entries, target_index: int, signature, *, force: bool = False):
        combo = getattr(self, "settings_personal_target_combo", None)
        if combo is None:
            return
        try:
            target_index = max(0, min(int(target_index or 0), len(entries) - 1))
        except Exception:
            target_index = 0
        current_signature = getattr(self, "_settings_personal_target_combo_signature", None)
        if (not force) and current_signature == signature and combo.count() == len(entries):
            if combo.currentIndex() != target_index:
                combo.blockSignals(True)
                combo.setCurrentIndex(target_index)
                combo.blockSignals(False)
            return
        self._dismiss_combo_popup(combo)
        combo.blockSignals(True)
        combo.clear()
        for label, data in entries:
            combo.addItem(label, data)
        combo.setCurrentIndex(target_index)
        combo.blockSignals(False)
        self._settings_personal_target_combo_signature = signature

    def _defocus_settings_target_combo(self, *, fallback=None):
        combo = getattr(self, "settings_target_combo", None)
        if combo is None:
            return
        try:
            combo.clearFocus()
        except Exception:
            pass
        target = fallback
        if target is None:
            try:
                target = self.settings_stack.currentWidget() if getattr(self, "settings_stack", None) is not None else None
            except Exception:
                target = None
        if target is None:
            target = getattr(self, "settings_status_label", None)
        if target is None:
            return
        try:
            if target.focusPolicy() == Qt.NoFocus:
                target = getattr(self, "settings_status_label", None)
        except Exception:
            pass
        if target is None:
            return
        try:
            if target.focusPolicy() == Qt.NoFocus:
                target.setFocusPolicy(Qt.StrongFocus)
        except Exception:
            pass
        try:
            target.setFocus(Qt.OtherFocusReason)
        except Exception:
            pass

    def _refresh_settings_target_combo(self, *, force: bool = False):
        combo = getattr(self, "settings_target_combo", None)
        if combo is None:
            return
        previous = self._normalize_settings_target(
            {
                "scope": getattr(self, "_settings_target_scope", "local"),
                "device_id": getattr(self, "_settings_target_device_id", "local"),
            }
        )
        entries = self._settings_target_combo_entries()
        signature = tuple(
            (
                str(label or ""),
                str((data or {}).get("scope") or ""),
                str((data or {}).get("device_id") or ""),
                str((data or {}).get("host") or ""),
            )
            for label, data in entries
        )
        target_index = 0
        for idx, (_label, data) in enumerate(entries):
            if (
                str((data or {}).get("scope") or "local").strip().lower() == previous["scope"]
                and str((data or {}).get("device_id") or "local").strip() == previous["device_id"]
            ):
                target_index = idx
                break
        current_signature = getattr(self, "_settings_target_combo_signature", None)
        if (not force) and current_signature == signature and combo.count() == len(entries):
            if combo.currentIndex() != target_index:
                combo.blockSignals(True)
                combo.setCurrentIndex(target_index)
                combo.blockSignals(False)
            self._sync_personal_target_combo(entries, target_index, signature, force=force)
            current_data = combo.itemData(target_index) if isinstance(combo.itemData(target_index), dict) else {}
            current = self._normalize_settings_target(current_data)
            changed = self._apply_settings_target_selection(
                current,
                persist=True,
                reload_current=False,
                request_probe=False,
            )
            if not changed:
                self._refresh_settings_target_notice()
                self._refresh_settings_target_visibility()
            return
        self._dismiss_combo_popup(combo)
        combo.blockSignals(True)
        combo.clear()
        for label, data in entries:
            combo.addItem(label, data)
        combo.setCurrentIndex(target_index)
        combo.blockSignals(False)
        data = combo.itemData(target_index) if isinstance(combo.itemData(target_index), dict) else {}
        current = self._normalize_settings_target(data)
        self._settings_target_combo_signature = signature
        self._sync_personal_target_combo(entries, target_index, signature, force=force)
        changed = self._apply_settings_target_selection(
            current,
            persist=True,
            reload_current=False,
            request_probe=False,
        )
        if not changed:
            self._refresh_settings_target_notice()
            self._refresh_settings_target_visibility()

    def _apply_settings_target_selection(self, target, *, persist=True, reload_current=True, request_probe=True, defocus=False):
        normalized = self._normalize_settings_target(target)
        if (
            str(getattr(self, "_settings_target_scope", "local")) == normalized["scope"]
            and str(getattr(self, "_settings_target_device_id", "local")) == normalized["device_id"]
        ):
            return False
        self._settings_target_scope = normalized["scope"]
        self._settings_target_device_id = normalized["device_id"]
        # 等价于旧的逐字段失效逻辑：
        # self._qt_api_remote_loading = False
        # self._qt_channel_remote_loading = False
        # self._settings_personal_remote_sync_running = False
        # self._settings_usage_remote_sync_running = False
        invalidate_runtime_bound_state(self, bump_settings_target=True, clear_remote_sync_queues=False)
        if defocus:
            defocus_fn = getattr(self, "_defocus_settings_target_combo", None)
            if callable(defocus_fn):
                try:
                    defocus_fn()
                except Exception:
                    pass
        if persist:
            self.cfg["settings_target"] = dict(normalized)
            lz.save_config(self.cfg)
        loaded = getattr(self, "_settings_loaded_categories", None)
        if isinstance(loaded, set):
            loaded.discard("api")
            loaded.discard("channels")
            loaded.discard("personal")
            loaded.discard("usage")
            loaded.discard("schedule")
        self._refresh_settings_target_notice()
        self._refresh_settings_target_visibility()
        if reload_current:
            current_category = str(getattr(self, "_current_settings_category", "") or "").strip().lower()
            if self._settings_category_uses_target_switch(current_category) or current_category == "personal":
                self._settings_reload(categories=[self._current_settings_category], force=True)
        if request_probe:
            probe = getattr(self, "_request_server_connection_probe", None)
            if callable(probe):
                try:
                    probe(force=True)
                except Exception:
                    pass
        return True

    def _on_settings_target_changed(self, index: int, combo=None):
        combo = combo or getattr(self, "settings_target_combo", None)
        if combo is None:
            return
        data = combo.itemData(index) if isinstance(combo.itemData(index), dict) else {}
        target = self._normalize_settings_target(data)
        self._dismiss_combo_popup(combo)
        try:
            QTimer.singleShot(0, lambda c=combo: self._dismiss_combo_popup(c))
        except Exception:
            pass
        self._apply_settings_target_selection(target, persist=True, reload_current=True, request_probe=True, defocus=True)

    def _settings_target_open_remote_client(self, device, *, timeout=10):
        payload = {}
        item = device if isinstance(device, dict) else {}
        checker = getattr(self, "_remote_device_auto_ssh_enabled", None)
        if callable(checker):
            try:
                if not bool(checker(item)):
                    return None, "该远程设备已关闭自动 SSH，请先在“其他设备”中打开开关。"
            except Exception:
                pass
        key_rel = str(item.get("ssh_key_path") or "").strip()
        key_abs = lz._resolve_config_path(key_rel) if key_rel else ""
        if key_rel and (not key_abs or not os.path.isfile(key_abs)):
            return None, "SSH 私钥路径无效，请先修正设备配置。"
        payload["host"] = str(item.get("host") or "").strip()
        payload["username"] = str(item.get("username") or "").strip()
        payload["port"] = int(item.get("port") or 22)
        payload["password"] = str(item.get("password") or "").strip()
        payload["key_abs"] = key_abs
        if not payload["host"] or not payload["username"]:
            return None, "远程设备缺少 host/username。"
        if (not payload["password"]) and (not payload["key_abs"]):
            return None, "远程设备至少需要 SSH 私钥或密码。"
        client, err_msg, detail, missing = self._open_vps_ssh_client(payload, timeout=timeout)
        if client is None:
            msg = err_msg or "SSH 连接失败。"
            if missing:
                msg = "缺少 paramiko，无法连接远程设备。"
            if detail:
                msg = f"{msg}\n{detail}"
            return None, msg
        return client, ""

    def _settings_target_ensure_remote_mykey(self, client, device):
        dev = device if isinstance(device, dict) else {}
        remote_dir = self._settings_target_remote_agent_dir(dev)
        q_dir = shlex.quote(remote_dir)
        inner = (
            "set -e; "
            f"mkdir -p {q_dir}; "
            f"cd {q_dir}; "
            "if [ ! -f mykey.py ]; then "
            "if [ -f mykey_template.py ]; then cp mykey_template.py mykey.py; "
            "else printf '%s\\n' '# mykey.py' > mykey.py; fi; "
            "fi"
        )
        cmd = inner
        rc, _out, err = self._vps_exec_remote(client, cmd, timeout=30)
        if rc != 0:
            base_err = "远程初始化 mykey.py 失败。"
            return False, str(err or base_err).strip() or base_err
        return True, ""

    def _settings_target_read_mykey_text(self):
        ctx = self._settings_target_context()
        if not bool(ctx.get("is_remote")):
            agent_dir = self.agent_dir
            py_path = os.path.join(agent_dir, "mykey.py")
            json_path = os.path.join(agent_dir, "mykey.json")
            tpl_path = os.path.join(agent_dir, "mykey_template.py")
            source_path = py_path if os.path.isfile(py_path) else (json_path if os.path.isfile(json_path) else py_path)
            if os.path.isdir(agent_dir) and source_path == py_path and not os.path.isfile(py_path):
                if os.path.isfile(tpl_path):
                    try:
                        with open(tpl_path, "r", encoding="utf-8") as src, open(py_path, "w", encoding="utf-8") as dst:
                            dst.write(src.read())
                    except Exception:
                        pass
                else:
                    try:
                        with open(py_path, "w", encoding="utf-8") as dst:
                            dst.write("# mykey.py\n")
                    except Exception:
                        pass
            with open(source_path, "r", encoding="utf-8", errors="replace") as f:
                return True, f.read(), source_path, ""
        dev = ctx.get("device") or {}
        client, err = self._settings_target_open_remote_client(dev, timeout=10)
        if client is None:
            return False, "", self._settings_target_display_path("mykey.py"), err
        try:
            ok, detail = self._settings_target_ensure_remote_mykey(client, dev)
            if not ok:
                return False, "", self._settings_target_display_path("mykey.py"), detail
            remote_dir = self._settings_target_remote_agent_dir(dev)
            remote_fp = remote_dir.rstrip("/") + "/mykey.py"
            sftp = client.open_sftp()
            try:
                with sftp.open(remote_fp, "rb") as fp:
                    raw = fp.read()
            finally:
                try:
                    sftp.close()
                except Exception:
                    pass
            text = raw.decode("utf-8", errors="replace") if raw else "# mykey.py\n"
            return True, text, self._settings_target_display_path("mykey.py"), ""
        except Exception as e:
            return False, "", self._settings_target_display_path("mykey.py"), str(e)
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _settings_target_write_mykey_text(self, text: str):
        body = str(text or "")
        ctx = self._settings_target_context()
        if not bool(ctx.get("is_remote")):
            py_path = os.path.join(self.agent_dir, "mykey.py")
            with open(py_path, "w", encoding="utf-8") as f:
                f.write(body)
            return True, py_path, ""
        dev = ctx.get("device") or {}
        client, err = self._settings_target_open_remote_client(dev, timeout=12)
        if client is None:
            return False, self._settings_target_display_path("mykey.py"), err
        try:
            ok, detail = self._settings_target_ensure_remote_mykey(client, dev)
            if not ok:
                return False, self._settings_target_display_path("mykey.py"), detail
            remote_dir = self._settings_target_remote_agent_dir(dev)
            remote_fp = remote_dir.rstrip("/") + "/mykey.py"
            tmp_name = f"mykey.py.tmp.{int(time.time() * 1000)}"
            write_fp = remote_dir.rstrip("/") + "/" + tmp_name
            sftp = client.open_sftp()
            try:
                with sftp.open(write_fp, "wb") as fp:
                    fp.write(body.encode("utf-8"))
            finally:
                try:
                    sftp.close()
                except Exception:
                    pass
            mv_cmd = f"mv -f {shlex.quote(write_fp)} {shlex.quote(remote_fp)}"
            rc, _out, mv_err = self._vps_exec_remote(client, mv_cmd, timeout=20)
            if rc != 0:
                base_err = "写入远端 mykey.py 失败。"
                return False, self._settings_target_display_path("mykey.py"), str(mv_err or base_err).strip() or base_err
            return True, self._settings_target_display_path("mykey.py"), ""
        except Exception as e:
            return False, self._settings_target_display_path("mykey.py"), str(e)
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _settings_parse_mykey_text(self, text: str, source_path: str = ""):
        tmp_fp = ""
        try:
            suffix = os.path.splitext(str(source_path or "").strip())[1].lower() or ".py"
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=suffix, delete=False) as tmp:
                tmp.write(str(text or ""))
                tmp_fp = tmp.name
            parsed = lz.parse_mykey_source(tmp_fp)
            return parsed if isinstance(parsed, dict) else {"error": "解析返回为空", "configs": [], "extras": {}, "passthrough": []}
        finally:
            if tmp_fp:
                try:
                    os.remove(tmp_fp)
                except Exception:
                    pass

    def _show_settings_category(self, key: str, *, reload: bool = True):
        if not hasattr(self, "settings_stack"):
            return
        page_info = (getattr(self, "_settings_pages", None) or {}).get(key)
        if not page_info:
            return
        category = str(key or "").strip().lower()
        self._current_settings_category = category
        stack = self.settings_stack
        target_widget = page_info["widget"]
        current_widget = None
        try:
            current_widget = stack.currentWidget()
        except Exception:
            current_widget = None
        if current_widget is not target_widget:
            stack.setCurrentWidget(target_widget)
        self._refresh_settings_target_visibility(category)
        self._apply_settings_nav_selection(category)
        if reload:
            should_reload = self._settings_should_reload_on_switch(category)
            force_reload = self._settings_category_should_force_live_reload(category)
            self._schedule_settings_category_activation(
                category,
                reload_required=should_reload,
                force_reload=force_reload,
            )

    def _load_mykey_source(self):
        ok, text, display_path, err = self._settings_target_read_mykey_text()
        if not ok:
            return display_path, {
                "error": err or "读取配置失败",
                "configs": [],
                "extras": {},
                "passthrough": [],
                "load_failed": True,
            }
        parsed = self._settings_parse_mykey_text(text, display_path)
        return display_path, parsed

    def _settings_reload(self, *, categories=None, force=False):
        current_key = str(getattr(self, "_current_settings_category", "api") or "api").strip().lower()
        requested = list(categories) if categories is not None else [str(getattr(self, "_current_settings_category", "api") or "api")]
        if any(self._settings_category_refreshes_target_combo(key) for key in requested if str(key or "").strip()):
            self._refresh_settings_target_combo(force=force)
        self._refresh_settings_status_label(current_key)
        if not requested:
            return
        loaded = getattr(self, "_settings_loaded_categories", None)
        if not isinstance(loaded, set):
            loaded = set()
            self._settings_loaded_categories = loaded
        reloaders = {
            "api": self._reload_api_editor_state,
            "channels": self._reload_channels_editor_state,
            "vps": self._reload_vps_panel,
            "schedule": self._reload_schedule_panel,
            "sop": self._reload_sop_panel,
            "personal": self._reload_personal_panel,
            "theme": self._reload_theme_panel,
            "usage": self._reload_usage_panel,
            "about": self._reload_about_panel,
        }
        for key in requested:
            text = str(key or "").strip().lower()
            fn = reloaders.get(text)
            if not callable(fn):
                continue
            if (not force) and text in loaded:
                continue
            fn()
            loaded.add(text)
            self._mark_settings_category_reloaded(text)

    def _dismiss_combo_popup(self, combo):
        if combo is None:
            return
        try:
            combo.hidePopup()
        except Exception:
            pass

    def _select_combo_data(self, combo, value, default_index: int = 0):
        if combo is None:
            return
        self._dismiss_combo_popup(combo)
        target = str(value or "")
        for idx in range(combo.count()):
            if str(combo.itemData(idx) or "") == target:
                combo.setCurrentIndex(idx)
                return
        combo.setCurrentIndex(max(0, int(default_index or 0)))

    def _make_vps_profile_id(self, seed: str = ""):
        raw = str(seed or "").strip() or f"profile-{time.time_ns()}"
        digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"vps_{digest}"

    def _settings_normalize_remote_auto_ssh_value(self, value, *, default=True):
        if isinstance(value, bool):
            return value
        if value is None:
            return bool(default)
        text = str(value).strip().lower()
        if not text:
            return bool(default)
        if text in ("0", "false", "no", "off", "disable", "disabled", "关", "关闭", "否"):
            return False
        if text in ("1", "true", "yes", "on", "enable", "enabled", "开", "开启", "是"):
            return True
        return bool(value)

    def _normalize_vps_profile(self, raw):
        item = raw if isinstance(raw, dict) else {}
        conn = self._normalize_vps_connection_cfg(item)
        deploy = self._normalize_vps_deploy_cfg(item, username=conn.get("username"))
        takeover = self._normalize_vps_takeover_cfg(item)
        profile_id = str(item.get("id") or "").strip()
        if not profile_id:
            host = str(conn.get("host") or "").strip()
            username = str(conn.get("username") or "").strip()
            port = int(conn.get("port") or 22)
            seed = f"{username}@{host}:{port}" if host and username else f"profile-{time.time_ns()}"
            profile_id = self._make_vps_profile_id(seed)
        raw_name = str(item.get("name") or "").strip()
        name = self._strip_auto_docker_name_suffix(raw_name) if self._vps_profile_uses_docker_takeover(item, takeover_cfg=takeover) else raw_name
        if not name:
            name = str(conn.get("host") or "未命名服务器").strip() or "未命名服务器"
        python_cmd = str(item.get("python_cmd") or "python3").strip() or "python3"
        try:
            last_test_at = float(item.get("last_test_at") or 0)
        except Exception:
            last_test_at = 0.0
        try:
            last_deploy_at = float(item.get("last_deploy_at") or 0)
        except Exception:
            last_deploy_at = 0.0
        try:
            last_takeover_at = float(item.get("last_takeover_at") or 0)
        except Exception:
            last_takeover_at = 0.0
        return {
            "id": profile_id,
            "name": name,
            "python_cmd": python_cmd,
            "last_test_status": str(item.get("last_test_status") or "").strip().lower(),
            "last_test_message": str(item.get("last_test_message") or "").strip(),
            "last_test_detail": str(item.get("last_test_detail") or "").strip(),
            "last_test_at": last_test_at if last_test_at > 0 else 0.0,
            "last_deploy_status": str(item.get("last_deploy_status") or "").strip().lower(),
            "last_deploy_message": str(item.get("last_deploy_message") or "").strip(),
            "last_deploy_detail": str(item.get("last_deploy_detail") or "").strip(),
            "last_deploy_at": last_deploy_at if last_deploy_at > 0 else 0.0,
            "last_takeover_status": str(item.get("last_takeover_status") or "").strip().lower(),
            "last_takeover_message": str(item.get("last_takeover_message") or "").strip(),
            "last_takeover_detail": str(item.get("last_takeover_detail") or "").strip(),
            "last_takeover_at": last_takeover_at if last_takeover_at > 0 else 0.0,
            "auto_ssh": self._settings_normalize_remote_auto_ssh_value(item.get("auto_ssh", True), default=True),
            **conn,
            **deploy,
            **takeover,
        }

    def _vps_profile_to_remote_device(self, raw):
        profile = self._normalize_vps_profile(raw)
        host = str(profile.get("host") or "").strip()
        username = str(profile.get("username") or "").strip()
        if not host or not username:
            return None
        remote_mode = str(profile.get("remote_mode") or "ssh").strip().lower()
        agent_dir = normalize_remote_agent_dir(profile.get("remote_dir"), username=username)
        python_cmd = str(profile.get("python_cmd") or "python3").strip() or "python3"
        device_name = str(profile.get("name") or "").strip() or host
        takeover_mode = str(profile.get("takeover_mode") or "").strip().lower()
        takeover_agent_dir = str(profile.get("takeover_agent_dir") or "").strip()
        takeover_python_cmd = str(profile.get("takeover_python_cmd") or "").strip() or "python3"
        if remote_mode == "docker_container":
            remote_mode = "ssh"
        if takeover_mode == "path" and takeover_agent_dir:
            agent_dir = takeover_agent_dir
            python_cmd = takeover_python_cmd
        return {
            "id": str(profile.get("id") or "").strip(),
            "name": device_name,
            "host": host,
            "username": username,
            "port": int(profile.get("port") or 22),
            "ssh_key_path": str(profile.get("ssh_key_path") or "").strip(),
            "password": str(profile.get("password") or "").strip(),
            "agent_dir": agent_dir,
            "agent_mode": "host",
            "python_cmd": python_cmd,
            "auto_ssh": self._settings_normalize_remote_auto_ssh_value(profile.get("auto_ssh", True), default=True),
            "remote_mode": "ssh",
            "takeover_mode": "path" if remote_mode == "ssh" and takeover_mode == "path" and takeover_agent_dir else "",
            "takeover_agent_dir": takeover_agent_dir if remote_mode == "ssh" and takeover_mode == "path" else "",
            "takeover_python_cmd": takeover_python_cmd if remote_mode == "ssh" and takeover_mode == "path" else "",
            "docker_container": "",
            "docker_agent_dir": "",
        }

    def _legacy_vps_profile_from_config(self):
        conn = self._normalize_vps_connection_cfg(self.cfg.get("vps_connection"))
        deploy = self._normalize_vps_deploy_cfg(self.cfg.get("vps_deploy"), username=conn.get("username"))
        takeover = self._normalize_vps_takeover_cfg(self.cfg.get("vps_takeover"))
        meaningful = any(
            [
                str(conn.get("host") or "").strip(),
                str(conn.get("username") or "").strip(),
                str(conn.get("ssh_key_path") or "").strip(),
                str(conn.get("password") or "").strip(),
                str(deploy.get("local_agent_dir") or "").strip(),
                str(deploy.get("repo_url") or "").strip(),
                str(deploy.get("remote_dir") or "").strip(),
                str(takeover.get("takeover_agent_dir") or "").strip(),
            ]
        )
        if not meaningful:
            return None
        seed_host = str(conn.get("host") or "").strip()
        seed_user = str(conn.get("username") or "").strip()
        seed_port = int(conn.get("port") or 22)
        return self._normalize_vps_profile(
            {
                "id": self._make_vps_profile_id(
                    f"{seed_user}@{seed_host}:{seed_port}" if seed_host or seed_user else "legacy"
                ),
                "name": "默认服务器",
                **conn,
                **deploy,
                **takeover,
            }
        )

    def _vps_profiles(self):
        merged = {}
        order = []
        writeback_required = False

        def upsert(raw):
            if not isinstance(raw, dict):
                return
            profile = self._normalize_vps_profile(raw)
            pid = str(profile.get("id") or "").strip()
            if not pid:
                return
            current = merged.get(pid)
            if current is None:
                merged[pid] = profile
                order.append(pid)
                return
            updated = dict(current)
            for key, value in profile.items():
                if key == "id":
                    continue
                if key in (
                    "name",
                    "host",
                    "username",
                    "ssh_key_path",
                    "password",
                    "remote_dir",
                    "python_cmd",
                ):
                    if str(value or "").strip():
                        updated[key] = value
                    continue
                if key in (
                    "takeover_mode",
                    "takeover_agent_dir",
                    "takeover_python_cmd",
                    "docker_takeover_container",
                    "docker_takeover_agent_dir",
                    "docker_takeover_python_cmd",
                    "remote_mode",
                ):
                    updated[key] = value
                    continue
                if key == "port":
                    try:
                        updated[key] = int(value or updated.get(key) or 22)
                    except Exception:
                        pass
                    continue
                if value not in (None, ""):
                    updated[key] = value
            merged[pid] = self._normalize_vps_profile(updated)

        raw_profiles = self.cfg.get("vps_profiles")
        if isinstance(raw_profiles, list):
            for raw in raw_profiles:
                takeover_cfg = self._normalize_vps_takeover_cfg(raw)
                if self._vps_profile_name_needs_cleanup(raw, takeover_cfg=takeover_cfg):
                    writeback_required = True
                upsert(raw)
        raw_devices = self.cfg.get("remote_devices")
        if isinstance(raw_devices, list):
            for raw in raw_devices:
                if not isinstance(raw, dict):
                    continue
                remote_dir = remote_device_agent_dir(raw, username=raw.get("username"))
                raw_takeover_dir = str(raw.get("takeover_agent_dir") or "").strip()
                takeover_agent_dir = (
                    normalize_remote_agent_dir(raw_takeover_dir, username=raw.get("username"))
                    if raw_takeover_dir
                    else str(remote_dir or "").strip()
                )
                item = {
                    "id": str(raw.get("id") or "").strip()
                    or self._make_vps_profile_id(f"{raw.get('username') or ''}@{raw.get('host') or ''}:{int(raw.get('port') or 22)}"),
                    "name": str(raw.get("name") or "").strip(),
                    "host": str(raw.get("host") or "").strip(),
                    "username": str(raw.get("username") or "").strip(),
                    "port": int(raw.get("port") or 22),
                    "ssh_key_path": str(raw.get("ssh_key_path") or "").strip(),
                    "password": str(raw.get("password") or "").strip(),
                    "remote_dir": remote_dir,
                    "python_cmd": str(raw.get("python_cmd") or "python3").strip() or "python3",
                    "auto_ssh": self._settings_normalize_remote_auto_ssh_value(raw.get("auto_ssh", True), default=True),
                    "remote_mode": "ssh",
                    "takeover_mode": "path" if takeover_agent_dir else "",
                    "takeover_agent_dir": takeover_agent_dir if takeover_agent_dir else "",
                    "takeover_python_cmd": str(raw.get("takeover_python_cmd") or raw.get("python_cmd") or "python3").strip() or "python3",
                    "docker_takeover_container": "",
                    "docker_takeover_agent_dir": "",
                    "docker_takeover_python_cmd": "python3",
                }
                if str(raw.get("remote_mode") or "").strip().lower() != "ssh":
                    writeback_required = True
                if str(raw.get("docker_container") or "").strip() or str(raw.get("docker_agent_dir") or "").strip():
                    writeback_required = True
                if self._vps_profile_name_needs_cleanup(raw):
                    writeback_required = True
                upsert(item)
        legacy = self._legacy_vps_profile_from_config()
        if legacy and not merged:
            upsert(legacy)
        rows = [dict(merged[pid]) for pid in order if pid in merged]
        if rows and writeback_required and not bool(getattr(self, "_vps_profiles_cleanup_in_progress", False)):
            try:
                self._vps_profiles_cleanup_in_progress = True
                saved = self._save_vps_profiles(rows, selected_id=str(self.cfg.get("vps_current_profile_id") or "").strip())
            finally:
                self._vps_profiles_cleanup_in_progress = False
            return [dict(item) for item in (saved or rows)]
        return rows

    def _current_vps_profile_id(self):
        current = str(self.cfg.get("vps_current_profile_id") or "").strip()
        rows = self._vps_profiles()
        valid_ids = {str(item.get("id") or "").strip() for item in rows}
        if current and current in valid_ids:
            return current
        if rows:
            return str(rows[0].get("id") or "").strip()
        return ""

    def _set_current_vps_profile_id(self, profile_id: str):
        self.cfg["vps_current_profile_id"] = str(profile_id or "").strip()

    def _current_vps_profile(self):
        pid = self._current_vps_profile_id()
        if not pid:
            return None
        for item in self._vps_profiles():
            if str(item.get("id") or "").strip() == pid:
                return dict(item)
        return None

    def _vps_profile_display_name(self, raw):
        item = raw if isinstance(raw, dict) else {}
        name = str(item.get("name") or "").strip()
        host = str(item.get("host") or "").strip()
        if name and host and name != host:
            return f"{name}（{host}）"
        return name or host or "未命名服务器"

    def _vps_profile_status_badges(self, raw):
        item = raw if isinstance(raw, dict) else {}
        badges = []
        test_status = str(item.get("last_test_status") or "").strip().lower()
        if test_status == "success":
            badges.append("连通通过")
        elif test_status == "fail":
            badges.append("连通失败")
        deploy_status = str(item.get("last_deploy_status") or "").strip().lower()
        if deploy_status == "success":
            badges.append("部署通过")
        elif deploy_status == "fail":
            badges.append("部署失败")
        takeover_status = str(item.get("last_takeover_status") or "").strip().lower()
        if takeover_status == "success":
            badges.append("已接管")
        elif takeover_status == "fail":
            badges.append("接管失败")
        return badges

    def _vps_profile_combo_label(self, raw):
        base = self._vps_profile_display_name(raw)
        badges = self._vps_profile_status_badges(raw)
        if badges:
            return base + "  [" + " / ".join(badges) + "]"
        return base

    def _vps_profile_health(self, raw):
        item = raw if isinstance(raw, dict) else {}
        host = str(item.get("host") or "").strip()
        username = str(item.get("username") or "").strip()
        if not host or not username:
            return "idle", "这台服务器的连接信息还没填完整。"
        deploy_status = str(item.get("last_deploy_status") or "").strip().lower()
        test_status = str(item.get("last_test_status") or "").strip().lower()
        takeover_status = str(item.get("last_takeover_status") or "").strip().lower()
        if takeover_status == "fail":
            detail = str(item.get("last_takeover_message") or "最近一次接管失败。").strip()
            return "error", detail
        if deploy_status == "fail":
            detail = str(item.get("last_deploy_message") or "最近一次部署失败。").strip()
            return "error", detail
        if test_status == "fail":
            detail = str(item.get("last_test_message") or "最近一次连接测试失败。").strip()
            return "error", detail
        if takeover_status == "success":
            detail = str(item.get("last_takeover_message") or "最近一次接管成功。").strip()
            return "ok", detail
        if deploy_status == "success":
            detail = str(item.get("last_deploy_message") or "最近一次部署成功。").strip()
            return "ok", detail
        if test_status == "success":
            detail = str(item.get("last_test_message") or "最近一次连接测试成功。").strip()
            return "ok", detail
        return "pending", "这台服务器还没有做过连接测试或部署。"

    def _format_vps_profile_runtime_summary(self, raw):
        item = raw if isinstance(raw, dict) else {}
        rows = []
        test_status = str(item.get("last_test_status") or "").strip().lower()
        test_msg = str(item.get("last_test_message") or "").strip()
        test_at = float(item.get("last_test_at") or 0)
        if test_status and test_msg:
            stamp = time.strftime("%m-%d %H:%M", time.localtime(test_at)) if test_at > 0 else ""
            prefix = "最近连接成功" if test_status == "success" else "最近连接失败"
            rows.append(f"{prefix}：{test_msg}" + (f"（{stamp}）" if stamp else ""))
        deploy_status = str(item.get("last_deploy_status") or "").strip().lower()
        deploy_msg = str(item.get("last_deploy_message") or "").strip()
        deploy_at = float(item.get("last_deploy_at") or 0)
        if deploy_status and deploy_msg:
            stamp = time.strftime("%m-%d %H:%M", time.localtime(deploy_at)) if deploy_at > 0 else ""
            prefix = "最近部署成功" if deploy_status == "success" else "最近部署失败"
            rows.append(f"{prefix}：{deploy_msg}" + (f"（{stamp}）" if stamp else ""))
        takeover_status = str(item.get("last_takeover_status") or "").strip().lower()
        takeover_msg = str(item.get("last_takeover_message") or "").strip()
        takeover_at = float(item.get("last_takeover_at") or 0)
        if takeover_status and takeover_msg:
            stamp = time.strftime("%m-%d %H:%M", time.localtime(takeover_at)) if takeover_at > 0 else ""
            prefix = "最近接管成功" if takeover_status == "success" else "最近接管失败"
            rows.append(f"{prefix}：{takeover_msg}" + (f"（{stamp}）" if stamp else ""))
        return "\n".join(rows)

    def _update_vps_profile_runtime_summary(self, *, kind: str, ok: bool, message: str, detail: str = "", profile_id: str = ""):
        pid = str(profile_id or self._current_vps_profile_id() or "").strip()
        if not pid:
            return
        kind_text = str(kind or "").strip().lower()
        if kind_text == "deploy":
            kind_key = "deploy"
        elif kind_text == "takeover":
            kind_key = "takeover"
        else:
            kind_key = "test"
        rows = self._vps_profiles()
        changed = False
        for item in rows:
            if str(item.get("id") or "").strip() != pid:
                continue
            item[f"last_{kind_key}_status"] = "success" if ok else "fail"
            item[f"last_{kind_key}_message"] = str(message or "").strip()
            item[f"last_{kind_key}_detail"] = str(detail or "").strip()
            item[f"last_{kind_key}_at"] = float(time.time())
            changed = True
            break
        if not changed:
            return
        self._save_vps_profiles(rows, selected_id=pid)

    def _save_vps_profiles(self, rows, *, selected_id: str = ""):
        payload = []
        seen = set()
        for raw in rows or []:
            if not isinstance(raw, dict):
                continue
            profile = self._normalize_vps_profile(raw)
            pid = str(profile.get("id") or "").strip()
            if not pid or pid in seen:
                continue
            seen.add(pid)
            payload.append(profile)
        self.cfg["vps_profiles"] = payload
        valid_ids = {str(item.get("id") or "").strip() for item in payload}
        current_id = str(selected_id or self.cfg.get("vps_current_profile_id") or "").strip()
        if current_id and current_id not in valid_ids:
            current_id = ""
        if not current_id and payload:
            current_id = str(payload[0].get("id") or "").strip()
        self._set_current_vps_profile_id(current_id)
        remote_rows = []
        for item in payload:
            device = self._vps_profile_to_remote_device(item)
            if device:
                remote_rows.append(device)
        self.cfg["remote_devices"] = remote_rows
        current = None
        for item in payload:
            if str(item.get("id") or "").strip() == current_id:
                current = item
                break
        if current is not None:
            self.cfg["vps_connection"] = self._normalize_vps_connection_cfg(current)
            self.cfg["vps_deploy"] = self._normalize_vps_deploy_cfg(current)
            self.cfg["vps_takeover"] = self._normalize_vps_takeover_cfg(current)
        else:
            self.cfg["vps_connection"] = self._normalize_vps_connection_cfg({})
            self.cfg["vps_deploy"] = self._normalize_vps_deploy_cfg({})
            self.cfg["vps_takeover"] = self._normalize_vps_takeover_cfg({})
        lz.save_config(self.cfg)
        return payload

    def _collect_current_vps_profile_form_data(self, profile_id: str = "", name: str = "", python_cmd: str = ""):
        current = self._current_vps_profile() or {}
        payload = {
            "id": str(profile_id or current.get("id") or self._make_vps_profile_id()).strip(),
            "name": str(name or current.get("name") or "").strip() or str(current.get("host") or "未命名服务器").strip() or "未命名服务器",
            "python_cmd": str(python_cmd or current.get("python_cmd") or "python3").strip() or "python3",
            "last_test_status": str(current.get("last_test_status") or "").strip().lower(),
            "last_test_message": str(current.get("last_test_message") or "").strip(),
            "last_test_detail": str(current.get("last_test_detail") or "").strip(),
            "last_test_at": float(current.get("last_test_at") or 0) if str(current.get("last_test_at") or "").strip() else 0.0,
            "last_deploy_status": str(current.get("last_deploy_status") or "").strip().lower(),
            "last_deploy_message": str(current.get("last_deploy_message") or "").strip(),
            "last_deploy_detail": str(current.get("last_deploy_detail") or "").strip(),
            "last_deploy_at": float(current.get("last_deploy_at") or 0) if str(current.get("last_deploy_at") or "").strip() else 0.0,
            "last_takeover_status": str(current.get("last_takeover_status") or "").strip().lower(),
            "last_takeover_message": str(current.get("last_takeover_message") or "").strip(),
            "last_takeover_detail": str(current.get("last_takeover_detail") or "").strip(),
            "last_takeover_at": float(current.get("last_takeover_at") or 0) if str(current.get("last_takeover_at") or "").strip() else 0.0,
            "auto_ssh": self._settings_normalize_remote_auto_ssh_value(current.get("auto_ssh", True), default=True),
        }
        payload.update(self._collect_vps_form_data())
        payload.update(self._collect_vps_deploy_form_data())
        payload.update(self._collect_vps_takeover_form_data(preserve_legacy=True, current_profile=current))
        host = str(payload.get("host") or "").strip()
        if name:
            payload["name"] = str(name or "").strip()
        elif str(current.get("name") or "").strip():
            payload["name"] = str(current.get("name") or "").strip()
        elif host:
            payload["name"] = host
        return self._normalize_vps_profile(payload)

    def _persist_current_vps_profile_from_form(self, *, validate_pair: bool = False, silent: bool = True):
        current = self._current_vps_profile() or {}
        profile_id = str(getattr(self, "_vps_form_profile_id", "") or current.get("id") or "").strip()
        if not profile_id:
            return None
        payload = self._collect_current_vps_profile_form_data(
            profile_id=profile_id,
            name=str(current.get("name") or "").strip(),
            python_cmd=str(current.get("python_cmd") or "python3").strip() or "python3",
        )
        has_host = bool(str(payload.get("host") or "").strip())
        has_user = bool(str(payload.get("username") or "").strip())
        if validate_pair and has_host != has_user:
            text = "服务器地址和用户名需要同时填写。"
            if not silent:
                QMessageBox.warning(self, "保存失败", text)
            self._set_status(text)
            return False
        rows = self._vps_profiles()
        replaced = False
        for idx, item in enumerate(rows):
            if str(item.get("id") or "").strip() == profile_id:
                rows[idx] = payload
                replaced = True
                break
        if not replaced:
            rows.append(payload)
        self._save_vps_profiles(rows, selected_id=profile_id)
        self._vps_form_profile_id = profile_id
        return payload

    def _normalize_vps_connection_cfg(self, raw):
        item = raw if isinstance(raw, dict) else {}
        host = str(item.get("host") or item.get("server") or "").strip()
        username = str(item.get("username") or item.get("user") or "").strip()
        key_path = str(item.get("ssh_key_path") or item.get("key_path") or "").strip()
        password = str(item.get("password") or "").strip()
        try:
            port = int(item.get("port") or 22)
        except Exception:
            port = 22
        port = max(1, min(65535, port))
        return {
            "host": host,
            "username": username,
            "port": port,
            "ssh_key_path": key_path,
            "password": password,
        }

    def _collect_vps_form_data(self):
        host_edit = getattr(self, "settings_vps_host_edit", None)
        username_edit = getattr(self, "settings_vps_username_edit", None)
        port_spin = getattr(self, "settings_vps_port_spin", None)
        key_edit = getattr(self, "settings_vps_key_path_edit", None)
        password_edit = getattr(self, "settings_vps_password_edit", None)
        host = host_edit.text().strip() if host_edit is not None else ""
        username = username_edit.text().strip() if username_edit is not None else ""
        key_raw = key_edit.text().strip() if key_edit is not None else ""
        password = password_edit.text().strip() if password_edit is not None else ""
        try:
            port = int(port_spin.value()) if port_spin is not None else 22
        except Exception:
            port = 22
        key_path = lz._make_config_relative_path(key_raw) if key_raw else ""
        return self._normalize_vps_connection_cfg(
            {
                "host": host,
                "username": username,
                "port": port,
                "ssh_key_path": key_path,
                "password": password,
            }
        )

    def _normalize_vps_deploy_cfg(self, raw, *, username: str = ""):
        item = raw if isinstance(raw, dict) else {}
        source = str(item.get("source") or "upload").strip().lower()
        if source not in ("upload", "git"):
            source = "upload"
        dep_mode = str(item.get("dep_install_mode") or "offline").strip().lower()
        if dep_mode == "online":
            dep_mode = "global"
        if dep_mode not in ("offline", "global", "mirror"):
            dep_mode = "offline"
        local_dir = str(item.get("local_agent_dir") or "").strip()
        repo_url = str(item.get("repo_url") or str(getattr(lz, "REPO_URL", "") or "")).strip()
        remote_user = str(username or item.get("username") or item.get("user") or "").strip()
        remote_dir = normalize_remote_agent_dir(item.get("remote_dir") or item.get("agent_dir"), username=remote_user)
        pip_mirror_url = str(item.get("pip_mirror_url") or "").strip()
        upload_excludes = str(
            item.get("upload_excludes")
            or ".git,.venv,venv,temp,tests,__pycache__,.pytest_cache,node_modules,.idea,.vscode,*.log"
        ).strip()
        return {
            "source": source,
            "dep_install_mode": dep_mode,
            "local_agent_dir": local_dir,
            "repo_url": repo_url,
            "remote_dir": remote_dir,
            "pip_mirror_url": pip_mirror_url,
            "upload_excludes": upload_excludes,
        }

    def _normalize_vps_takeover_cfg(self, raw):
        item = raw if isinstance(raw, dict) else {}
        agent_dir = str(item.get("takeover_agent_dir") or item.get("docker_takeover_agent_dir") or "").strip()
        python_cmd = str(item.get("takeover_python_cmd") or item.get("docker_takeover_python_cmd") or item.get("python_cmd") or "").strip() or "python3"
        remote_mode = "ssh"
        takeover_mode = "path" if agent_dir else ""
        return {
            "remote_mode": remote_mode,
            "takeover_mode": takeover_mode,
            "takeover_agent_dir": agent_dir,
            "takeover_python_cmd": python_cmd,
            "docker_takeover_container": "",
            "docker_takeover_agent_dir": "",
            "docker_takeover_python_cmd": "python3",
        }

    def _vps_dep_install_mode_label(self, mode: str):
        key = str(mode or "offline").strip().lower()
        if key == "online":
            key = "global"
        if key not in ("offline", "global", "mirror"):
            key = "offline"
        labels = {
            "offline": "内置源（清华）",
            "global": "国际源（PyPI）",
            "mirror": "自定义源",
        }
        return labels.get(key, labels["offline"])

    def _combo_current_data_value(self, combo, default=""):
        if combo is None:
            return str(default or "").strip()
        value = None
        try:
            value = combo.currentData()
        except Exception:
            value = None
        if value in (None, ""):
            try:
                index = int(combo.currentIndex())
            except Exception:
                index = -1
            if index >= 0:
                try:
                    value = combo.itemData(index)
                except Exception:
                    value = None
        text = str(value if value not in (None, "") else default).strip()
        return text or str(default or "").strip()

    def _collect_vps_deploy_form_data(self):
        source_combo = getattr(self, "settings_vps_deploy_source_combo", None)
        local_edit = getattr(self, "settings_vps_local_agent_dir_edit", None)
        repo_edit = getattr(self, "settings_vps_repo_url_edit", None)
        remote_edit = getattr(self, "settings_vps_remote_dir_edit", None)
        dep_mode_combo = getattr(self, "settings_vps_dep_install_mode_combo", None)
        mirror_edit = getattr(self, "settings_vps_pip_mirror_edit", None)
        excludes_edit = getattr(self, "settings_vps_upload_excludes_edit", None)
        source = self._combo_current_data_value(source_combo, "upload").lower()
        if source not in ("upload", "git"):
            source = "upload"
        dep_mode = self._combo_current_data_value(dep_mode_combo, "offline").lower()
        if dep_mode == "online":
            dep_mode = "global"
        if dep_mode not in ("offline", "global", "mirror"):
            dep_mode = "offline"
        local_raw = local_edit.text().strip() if local_edit is not None else ""
        local_dir = lz._make_config_relative_path(local_raw) if local_raw else ""
        username_edit = getattr(self, "settings_vps_username_edit", None)
        username = username_edit.text().strip() if username_edit is not None else ""
        return self._normalize_vps_deploy_cfg(
            {
                "source": source,
                "dep_install_mode": dep_mode,
                "local_agent_dir": local_dir,
                "repo_url": repo_edit.text().strip() if repo_edit is not None else "",
                "remote_dir": remote_edit.text().strip() if remote_edit is not None else "",
                "pip_mirror_url": mirror_edit.text().strip() if mirror_edit is not None else "",
                "upload_excludes": excludes_edit.text().strip() if excludes_edit is not None else "",
            },
            username=username,
        )

    def _collect_vps_takeover_form_data(self, *, preserve_legacy: bool = False, current_profile=None):
        agent_dir_edit = getattr(self, "settings_vps_takeover_agent_dir_edit", None)
        current = self._normalize_vps_takeover_cfg(
            current_profile if isinstance(current_profile, dict) else (self._current_vps_profile() or {})
        )
        payload = {
            "takeover_mode": current.get("takeover_mode") or "",
            "takeover_agent_dir": current.get("takeover_agent_dir") or "",
            "takeover_python_cmd": current.get("takeover_python_cmd") or "python3",
            "docker_takeover_container": "",
            "docker_takeover_agent_dir": "",
            "docker_takeover_python_cmd": "python3",
        }
        if agent_dir_edit is not None:
            payload["takeover_mode"] = "path"
            payload["takeover_agent_dir"] = agent_dir_edit.text().strip()
        return self._normalize_vps_takeover_cfg(
            payload
        )

    def _apply_vps_deploy_form_data(self, data):
        username_edit = getattr(self, "settings_vps_username_edit", None)
        username = username_edit.text().strip() if username_edit is not None else ""
        payload = self._normalize_vps_deploy_cfg(data, username=username)
        source_combo = getattr(self, "settings_vps_deploy_source_combo", None)
        if source_combo is not None:
            idx = source_combo.findData(payload["source"])
            source_combo.setCurrentIndex(idx if idx >= 0 else 0)
        dep_mode_combo = getattr(self, "settings_vps_dep_install_mode_combo", None)
        if dep_mode_combo is not None:
            idx = dep_mode_combo.findData(payload["dep_install_mode"])
            dep_mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
        local_edit = getattr(self, "settings_vps_local_agent_dir_edit", None)
        if local_edit is not None:
            local_edit.setText(payload["local_agent_dir"])
        repo_edit = getattr(self, "settings_vps_repo_url_edit", None)
        if repo_edit is not None:
            repo_edit.setText(payload["repo_url"])
        remote_edit = getattr(self, "settings_vps_remote_dir_edit", None)
        if remote_edit is not None:
            remote_edit.setText(payload["remote_dir"])
            self._refresh_vps_remote_dir_placeholder()
        mirror_edit = getattr(self, "settings_vps_pip_mirror_edit", None)
        if mirror_edit is not None:
            mirror_edit.setText(payload["pip_mirror_url"])
        excludes_edit = getattr(self, "settings_vps_upload_excludes_edit", None)
        if excludes_edit is not None:
            excludes_edit.setText(payload["upload_excludes"])

    def _apply_vps_takeover_form_data(self, data):
        payload = self._normalize_vps_takeover_cfg(data)
        agent_dir_edit = getattr(self, "settings_vps_takeover_agent_dir_edit", None)
        if agent_dir_edit is not None:
            agent_dir_edit.setText(payload["takeover_agent_dir"])

    def _apply_vps_form_data(self, data):
        item = self._normalize_vps_connection_cfg(data)
        host_edit = getattr(self, "settings_vps_host_edit", None)
        if host_edit is not None:
            host_edit.setText(item["host"])
        username_edit = getattr(self, "settings_vps_username_edit", None)
        if username_edit is not None:
            username_edit.setText(item["username"])
        self._refresh_vps_remote_dir_placeholder()
        port_spin = getattr(self, "settings_vps_port_spin", None)
        if port_spin is not None:
            port_spin.setValue(item["port"])
        key_edit = getattr(self, "settings_vps_key_path_edit", None)
        if key_edit is not None:
            key_edit.setText(item["ssh_key_path"])
        password_edit = getattr(self, "settings_vps_password_edit", None)
        if password_edit is not None:
            password_edit.setText(item["password"])

    def _refresh_vps_remote_dir_placeholder(self):
        remote_edit = getattr(self, "settings_vps_remote_dir_edit", None)
        if remote_edit is None:
            return
        username_edit = getattr(self, "settings_vps_username_edit", None)
        username = username_edit.text().strip() if username_edit is not None else ""
        default_dir = remote_agent_dir_default(username)
        remote_edit.setPlaceholderText(default_dir)
        current = remote_edit.text().strip()
        if is_auto_remote_agent_dir(current) and current != default_dir:
            remote_edit.blockSignals(True)
            remote_edit.setText(default_dir)
            remote_edit.blockSignals(False)

    def _set_vps_connect_running(self, running: bool):
        self._vps_connect_running = bool(running)
        self._refresh_vps_action_buttons()

    def _set_vps_dependency_install_running(self, running: bool):
        self._vps_dep_install_running = bool(running)
        self._refresh_vps_action_buttons()

    def _set_vps_terminal_connecting(self, running: bool):
        self._vps_terminal_connecting = bool(running)
        self._refresh_vps_terminal_meta()
        self._refresh_vps_action_buttons()

    def _set_vps_deploy_running(self, running: bool):
        self._vps_deploy_running = bool(running)
        self._refresh_vps_action_buttons()

    def _set_vps_takeover_running(self, running: bool):
        self._vps_takeover_running = bool(running)
        self._refresh_vps_action_buttons()

    def _vps_task_result_stale(self, profile_id: str = "") -> bool:
        expected = str(profile_id or "").strip()
        if expected:
            current = str(self._current_vps_profile_id() or "").strip()
            if current and current != expected:
                return True
        return False

    def _apply_vps_button_state(self, widget, enabled, *, enabled_tooltip="", disabled_tooltip=""):
        if widget is None:
            return
        widget.setEnabled(bool(enabled))
        tooltip = enabled_tooltip if bool(enabled) else disabled_tooltip
        try:
            widget.setToolTip(str(tooltip or ""))
        except Exception:
            pass

    def _vps_busy_reason(self):
        if bool(getattr(self, "_vps_dep_install_running", False)):
            return "正在安装 SSH 依赖，请等待当前任务完成。"
        if bool(getattr(self, "_vps_connect_running", False)):
            return "正在测试 VPS SSH 连接，请等待当前任务完成。"
        if bool(getattr(self, "_vps_terminal_connecting", False)):
            return "正在连接远程终端，请等待当前任务完成。"
        if bool(getattr(self, "_vps_deploy_running", False)):
            return "正在执行 VPS 直接部署，请等待当前任务完成。"
        if bool(getattr(self, "_vps_takeover_running", False)):
            return "正在接管 agant，请等待当前任务完成。"
        return ""

    def _vps_connection_incomplete_reason(self, payload=None):
        item = self._normalize_vps_connection_cfg(payload or self._collect_vps_form_data())
        host = str(item.get("host") or "").strip()
        username = str(item.get("username") or "").strip()
        if not host and not username:
            return "请先填写服务器地址和用户名。"
        if not host or not username:
            return "服务器地址和用户名需要同时填写。"
        return ""

    def _vps_auth_missing_reason(self, payload=None):
        item = self._normalize_vps_connection_cfg(payload or self._collect_vps_form_data())
        key_rel = str(item.get("ssh_key_path") or "").strip()
        password = str(item.get("password") or "").strip()
        if not key_rel and not password:
            return "请至少提供 SSH 私钥路径或密码。"
        key_abs = lz._resolve_config_path(key_rel) if key_rel else ""
        if key_rel and not os.path.isfile(key_abs):
            return "SSH 私钥路径不存在，请检查后重试。"
        return ""

    def _vps_runtime_connection_disabled_reason(self, payload=None):
        text = self._vps_connection_incomplete_reason(payload)
        if text:
            return text
        return self._vps_auth_missing_reason(payload)

    def _vps_terminal_connect_disabled_reason(self, *, payload=None, has_profiles=False):
        busy_reason = self._vps_busy_reason()
        if busy_reason:
            return busy_reason
        if not has_profiles:
            return "请先新建至少一个服务器配置。"
        if bool(getattr(self, "_vps_terminal_connected", False)):
            current_id = str(self._current_vps_profile_id() or getattr(self, "_vps_form_profile_id", "") or "").strip()
            connected_id = str(getattr(self, "_vps_terminal_profile_id", "") or "").strip()
            if connected_id and current_id and connected_id != current_id:
                return "当前终端已连接到另一台服务器，请先断开后再切换。"
            return "当前终端连接已在使用中，无需重复连接。"
        return self._vps_runtime_connection_disabled_reason(payload)

    def _vps_terminal_disconnect_disabled_reason(self):
        if bool(getattr(self, "_vps_terminal_connecting", False)):
            return "终端正在连接中，请等待当前连接结果。"
        if not bool(getattr(self, "_vps_terminal_connected", False)):
            return "当前没有已连接的远程终端。"
        return ""

    def _vps_terminal_send_disabled_reason(self):
        busy_reason = self._vps_busy_reason()
        if busy_reason:
            return busy_reason
        if not bool(getattr(self, "_vps_terminal_connected", False)):
            return "请先连接远程终端。"
        if getattr(self, "_vps_terminal_channel", None) is None:
            return "终端通道不可用，请重新连接。"
        return ""

    def _vps_deploy_validation_error(self, deploy_cfg=None):
        payload = self._normalize_vps_deploy_cfg(deploy_cfg or self._collect_vps_deploy_form_data())
        remote_dir = str(payload.get("remote_dir") or "").strip()
        source = str(payload.get("source") or "upload").strip().lower()
        local_rel = str(payload.get("local_agent_dir") or "").strip()
        local_abs = lz._resolve_config_path(local_rel) if local_rel else ""
        repo_url = str(payload.get("repo_url") or "").strip()
        dep_install_mode = str(payload.get("dep_install_mode") or "offline").strip().lower()
        if dep_install_mode == "online":
            dep_install_mode = "global"
        pip_mirror_url = str(payload.get("pip_mirror_url") or "").strip()
        if not remote_dir:
            return "请填写远端部署目录。"
        if source == "upload":
            if not local_abs or not os.path.isdir(local_abs):
                return "上传模式下，本地 agant 目录不存在。"
        elif not repo_url:
            return "拉取模式下，仓库地址不能为空。"
        if dep_install_mode == "mirror":
            if not pip_mirror_url:
                return "自定义镜像策略要求填写 pip 镜像地址。"
            parsed = urlparse(pip_mirror_url)
            if parsed.scheme not in ("http", "https") or (not parsed.netloc):
                return "pip 镜像地址格式无效，请填写 http(s) URL。"
        return ""

    def _vps_deploy_disabled_reason(self, *, payload=None, deploy_cfg=None, has_profiles=False):
        busy_reason = self._vps_busy_reason()
        if busy_reason:
            return busy_reason
        if not has_profiles:
            return "请先新建至少一个服务器配置。"
        connection_reason = self._vps_runtime_connection_disabled_reason(payload)
        if connection_reason:
            return connection_reason
        return self._vps_deploy_validation_error(deploy_cfg)

    def _vps_takeover_validation_error(self, takeover_cfg=None):
        payload = self._normalize_vps_takeover_cfg(takeover_cfg or self._collect_vps_takeover_form_data())
        agent_dir = str(payload.get("takeover_agent_dir") or "").strip()
        if not agent_dir:
            return "请先填写要接管的 agant 路径。"
        return ""

    def _vps_takeover_disabled_reason(self, *, payload=None, takeover_cfg=None, has_profiles=False):
        busy_reason = self._vps_busy_reason()
        if busy_reason:
            return busy_reason
        if not has_profiles:
            return "请先新建至少一个服务器配置。"
        connection_reason = self._vps_runtime_connection_disabled_reason(payload)
        if connection_reason:
            return connection_reason
        return self._vps_takeover_validation_error(takeover_cfg)

    def _vps_profile_action_disabled_reason(self, action: str, *, has_profiles=False):
        kind = str(action or "").strip().lower()
        busy_reason = self._vps_busy_reason()
        if busy_reason:
            return busy_reason
        if kind == "new":
            return ""
        if kind == "combo":
            return "" if has_profiles else "当前还没有服务器配置可切换。"
        if kind in {"save", "install", "test", "connect", "deploy", "rename", "delete"}:
            return "" if has_profiles else "请先新建至少一个服务器配置。"
        return ""

    def _refresh_vps_action_buttons(self):
        connect_running = bool(getattr(self, "_vps_connect_running", False))
        dep_running = bool(getattr(self, "_vps_dep_install_running", False))
        terminal_connecting = bool(getattr(self, "_vps_terminal_connecting", False))
        deploy_running = bool(getattr(self, "_vps_deploy_running", False))
        profiles = self._vps_profiles()
        has_profiles = bool(profiles)
        conn_payload = self._collect_vps_form_data()
        deploy_cfg = self._collect_vps_deploy_form_data()
        takeover_cfg = self._collect_vps_takeover_form_data()
        save_btn = getattr(self, "settings_vps_save_btn", None)
        if save_btn is not None:
            disabled_reason = self._vps_profile_action_disabled_reason("save", has_profiles=has_profiles)
            self._apply_vps_button_state(
                save_btn,
                not bool(disabled_reason),
                enabled_tooltip="保存当前服务器的连接与部署配置。",
                disabled_tooltip=disabled_reason,
            )
        install_btn = getattr(self, "settings_vps_install_dep_btn", None)
        if install_btn is not None:
            install_btn.setText("安装中…" if dep_running else "安装 SSH 依赖")
            disabled_reason = self._vps_profile_action_disabled_reason("install", has_profiles=has_profiles)
            self._apply_vps_button_state(
                install_btn,
                not bool(disabled_reason),
                enabled_tooltip="为当前启动器解释器安装 SSH 依赖（paramiko）。",
                disabled_tooltip=disabled_reason,
            )
        test_btn = getattr(self, "settings_vps_test_btn", None)
        if test_btn is not None:
            test_btn.setText("连接测试中…" if connect_running else "测试连接")
            disabled_reason = self._vps_profile_action_disabled_reason("test", has_profiles=has_profiles)
            if not disabled_reason:
                disabled_reason = self._vps_runtime_connection_disabled_reason(conn_payload)
            self._apply_vps_button_state(
                test_btn,
                not bool(disabled_reason),
                enabled_tooltip="使用当前配置测试 SSH 连接。",
                disabled_tooltip=disabled_reason,
            )
        connect_btn = getattr(self, "settings_vps_terminal_connect_btn", None)
        if connect_btn is not None:
            connect_btn.setText("连接中…" if terminal_connecting else "连接终端")
            disabled_reason = self._vps_terminal_connect_disabled_reason(payload=conn_payload, has_profiles=has_profiles)
            self._apply_vps_button_state(
                connect_btn,
                not bool(disabled_reason),
                enabled_tooltip="连接当前服务器的远程终端。",
                disabled_tooltip=disabled_reason,
            )
        disconnect_btn = getattr(self, "settings_vps_terminal_disconnect_btn", None)
        if disconnect_btn is not None:
            disabled_reason = self._vps_terminal_disconnect_disabled_reason()
            self._apply_vps_button_state(
                disconnect_btn,
                not bool(disabled_reason),
                enabled_tooltip="断开当前远程终端连接。",
                disabled_tooltip=disabled_reason,
            )
        send_btn = getattr(self, "settings_vps_terminal_send_btn", None)
        if send_btn is not None:
            disabled_reason = self._vps_terminal_send_disabled_reason()
            self._apply_vps_button_state(
                send_btn,
                not bool(disabled_reason),
                enabled_tooltip="把输入框中的命令发送到当前远程终端。",
                disabled_tooltip=disabled_reason,
            )
        input_edit = getattr(self, "settings_vps_terminal_input", None)
        if input_edit is not None:
            disabled_reason = self._vps_terminal_send_disabled_reason()
            self._apply_vps_button_state(
                input_edit,
                not bool(disabled_reason),
                enabled_tooltip="当前终端已连接，可输入命令并回车执行。",
                disabled_tooltip=disabled_reason,
            )
        deploy_btn = getattr(self, "settings_vps_deploy_btn", None)
        if deploy_btn is not None:
            deploy_btn.setText("部署中…" if deploy_running else "直接部署")
            disabled_reason = self._vps_deploy_disabled_reason(
                payload=conn_payload,
                deploy_cfg=deploy_cfg,
                has_profiles=has_profiles,
            )
            self._apply_vps_button_state(
                deploy_btn,
                not bool(disabled_reason),
                enabled_tooltip="把当前部署配置执行到目标服务器。",
                disabled_tooltip=disabled_reason,
            )
        takeover_btn = getattr(self, "settings_vps_takeover_btn", None)
        if takeover_btn is not None:
            takeover_btn.setText("接管中…" if bool(getattr(self, "_vps_takeover_running", False)) else "接管并注册 agant")
            disabled_reason = self._vps_takeover_disabled_reason(
                payload=conn_payload,
                takeover_cfg=takeover_cfg,
                has_profiles=has_profiles,
            )
            self._apply_vps_button_state(
                takeover_btn,
                not bool(disabled_reason),
                enabled_tooltip="校验 agant 路径并把它注册为启动器可用的远程设备。",
                disabled_tooltip=disabled_reason,
            )
        profile_combo = getattr(self, "settings_vps_profile_combo", None)
        if profile_combo is not None:
            disabled_reason = self._vps_profile_action_disabled_reason("combo", has_profiles=has_profiles)
            self._apply_vps_button_state(
                profile_combo,
                not bool(disabled_reason),
                enabled_tooltip="切换当前服务器配置。",
                disabled_tooltip=disabled_reason,
            )
        new_btn = getattr(self, "settings_vps_profile_new_btn", None)
        if new_btn is not None:
            disabled_reason = self._vps_profile_action_disabled_reason("new", has_profiles=has_profiles)
            self._apply_vps_button_state(
                new_btn,
                not bool(disabled_reason),
                enabled_tooltip="新增一台服务器配置。",
                disabled_tooltip=disabled_reason,
            )
        rename_btn = getattr(self, "settings_vps_profile_rename_btn", None)
        delete_btn = getattr(self, "settings_vps_profile_delete_btn", None)
        if rename_btn is not None:
            disabled_reason = self._vps_profile_action_disabled_reason("rename", has_profiles=has_profiles)
            self._apply_vps_button_state(
                rename_btn,
                not bool(disabled_reason),
                enabled_tooltip="重命名当前服务器配置。",
                disabled_tooltip=disabled_reason,
            )
        if delete_btn is not None:
            disabled_reason = self._vps_profile_action_disabled_reason("delete", has_profiles=has_profiles)
            self._apply_vps_button_state(
                delete_btn,
                not bool(disabled_reason),
                enabled_tooltip="删除当前服务器配置。",
                disabled_tooltip=disabled_reason,
            )

    def _reload_vps_panel(self):
        if not hasattr(self, "settings_vps_notice"):
            return
        rows = self._vps_profiles()
        current_id = self._current_vps_profile_id()
        self._set_current_vps_profile_id(current_id)
        profile = self._current_vps_profile() if current_id else None
        self._vps_profile_combo_updating = True
        combo = getattr(self, "settings_vps_profile_combo", None)
        if combo is not None:
            self._dismiss_combo_popup(combo)
            combo.clear()
            for item in rows:
                combo.addItem(self._vps_profile_combo_label(item), str(item.get("id") or "").strip())
            if rows:
                target_idx = combo.findData(current_id)
                combo.setCurrentIndex(target_idx if target_idx >= 0 else 0)
        self._vps_profile_combo_updating = False
        payload = self._normalize_vps_connection_cfg(profile or {})
        self._vps_form_profile_id = str((profile or {}).get("id") or "").strip()
        self._apply_vps_form_data(payload)
        deploy_cfg = self._normalize_vps_deploy_cfg(profile or {}, username=payload.get("username"))
        self._apply_vps_deploy_form_data(deploy_cfg)
        takeover_cfg = self._normalize_vps_takeover_cfg(profile or {})
        self._apply_vps_takeover_form_data(takeover_cfg)
        self._on_vps_deploy_source_changed()
        self._on_vps_dep_install_mode_changed()
        key_rel = str(payload.get("ssh_key_path") or "").strip()
        key_abs = lz._resolve_config_path(key_rel) if key_rel else ""
        has_key = bool(key_abs and os.path.isfile(key_abs))
        has_password = bool(str(payload.get("password") or "").strip())
        profile_notice = getattr(self, "settings_vps_profile_notice", None)
        if profile_notice is not None:
            if profile is not None:
                target = self._vps_profile_display_name(profile)
                host = str(profile.get("host") or "").strip()
                username = str(profile.get("username") or "").strip()
                port = int(profile.get("port") or 22)
                summary = self._format_vps_profile_runtime_summary(profile)
                if host and username:
                    text = f"当前目标：{target}。连接地址 {username}@{host}:{port}。"
                else:
                    text = f"当前目标：{target}。这台服务器的连接信息还没填完整。"
                if summary:
                    text += "\n" + summary
                profile_notice.setText(text)
            else:
                profile_notice.setText("还没有服务器配置。先点“新建”，再填写连接和部署信息。")
        state_label = getattr(self, "settings_vps_profile_state_label", None)
        if state_label is not None:
            if profile is None:
                state_label.setText("")
                state_label.setToolTip("")
            else:
                badges = self._vps_profile_status_badges(profile)
                state_label.setText(" | ".join(badges) if badges else "暂无记录")
                state_label.setToolTip(self._format_vps_profile_runtime_summary(profile))
        light = getattr(self, "settings_vps_profile_light", None)
        if light is not None:
            code, tip = self._vps_profile_health(profile or {})
            color = {"ok": "#16a34a", "error": "#dc2626", "pending": "#d97706", "idle": "#94a3b8"}.get(code, "#94a3b8")
            chat_common.set_label_svg_icon(light, "settings_vps_status", chat_common._SVG_DOT, color=color, size=12)
            light.setStyleSheet("background: transparent;")
            light.setToolTip(tip)
        if payload.get("host") and payload.get("username"):
            auth_text = "认证方式："
            if has_key and has_password:
                auth_text += "私钥 + 密码"
            elif has_key:
                auth_text += "私钥"
            elif has_password:
                auth_text += "密码"
            else:
                auth_text += "未设置"
            key_state = "私钥文件已就绪。" if has_key else ("私钥路径未设置。" if not key_rel else "私钥路径不存在，请检查。")
            self.settings_vps_notice.setText(
                f"当前配置：{payload['username']}@{payload['host']}:{payload['port']}。{auth_text}。{key_state}"
            )
        else:
            self.settings_vps_notice.setText("尚未完成当前服务器的连接配置。请先填写服务器地址、用户名和认证信息。")
        deploy_label = getattr(self, "settings_vps_deploy_notice", None)
        if deploy_label is not None:
            source_text = "上传本地项目" if deploy_cfg.get("source") == "upload" else "服务器拉取仓库"
            dep_text = self._vps_dep_install_mode_label(deploy_cfg.get("dep_install_mode"))
            deploy_label.setText(
                f"部署偏好：{source_text}；依赖策略 {dep_text}；远端目录 {deploy_cfg.get('remote_dir')}；模式 SSH 直部署。"
            )
        takeover_label = getattr(self, "settings_vps_takeover_notice", None)
        if takeover_label is not None:
            takeover_cfg = self._normalize_vps_takeover_cfg(profile or {})
            agent_dir = str(takeover_cfg.get("takeover_agent_dir") or "").strip()
            if str(takeover_cfg.get("takeover_mode") or "").strip().lower() == "path" and agent_dir:
                takeover_label.setText(f"接管偏好：agant 路径 {agent_dir}。")
            else:
                takeover_label.setText("尚未配置 agant 接管路径。")
        self._refresh_vps_terminal_meta()
        self._refresh_vps_action_buttons()

    def _refresh_vps_terminal_meta(self):
        label = getattr(self, "settings_vps_terminal_meta", None)
        if label is None:
            return
        current = self._current_vps_profile() or {}
        target_text = self._vps_profile_display_name(current) if current else "未选择服务器"
        connected = bool(getattr(self, "_vps_terminal_connected", False))
        connecting = bool(getattr(self, "_vps_terminal_connecting", False))
        connected_id = str(getattr(self, "_vps_terminal_profile_id", "") or "").strip()
        connected_name = str(getattr(self, "_vps_terminal_profile_name", "") or "").strip()
        if connected:
            state_text = f"终端已连接：{connected_name or target_text}。可直接输入命令。"
            fg = "#166534"
            bg = "rgba(34,197,94,0.14)"
            border = "rgba(34,197,94,0.28)"
        elif connecting:
            state_text = f"终端连接中：{target_text}"
            fg = "#92400e"
            bg = "rgba(245,158,11,0.14)"
            border = "rgba(245,158,11,0.28)"
        else:
            state_text = f"终端未连接：{target_text}"
            fg = "#475569"
            bg = "rgba(148,163,184,0.12)"
            border = "rgba(148,163,184,0.24)"
        if connected_id and current and connected_id != str(current.get("id") or "").strip():
            state_text += "。当前显示的是另一台服务器，切换后会重新连接。"
        encoding = str((getattr(self, "_vps_terminal_decoder_state_cache", {}) or {}).get("encoding") or "").strip()
        if encoding:
            state_text += f" 当前编码：{encoding}"
        try:
            label.setStyleSheet(
                "QLabel {"
                f" color: {fg};"
                f" background: {bg};"
                f" border: 1px solid {border};"
                " border-radius: 10px;"
                " padding: 8px 10px;"
                " font-size: 12px;"
                " font-weight: 600;"
                "}"
            )
        except Exception:
            pass
        label.setText(state_text)

    def _vps_terminal_input_text(self) -> str:
        edit = getattr(self, "settings_vps_terminal_input", None)
        if edit is None:
            return ""
        getter = getattr(edit, "text", None)
        if callable(getter):
            return str(getter() or "")
        return ""

    def _set_vps_terminal_input_text(self, text: str):
        edit = getattr(self, "settings_vps_terminal_input", None)
        if edit is None:
            return
        value = str(text or "")
        setter = getattr(edit, "setText", None)
        if callable(setter):
            setter(value)
        try:
            edit.setCursorPosition(len(value))
        except Exception:
            pass

    def _clear_vps_terminal_input(self):
        edit = getattr(self, "settings_vps_terminal_input", None)
        if edit is None:
            return
        try:
            edit.clear()
        except Exception:
            self._set_vps_terminal_input_text("")

    def _schedule_vps_terminal_prompt_refresh(self, *, delay_ms: int = 220):
        if bool(getattr(self, "_vps_terminal_prompt_refresh_pending", False)):
            return
        self._vps_terminal_prompt_refresh_pending = True

        def run():
            self._vps_terminal_prompt_refresh_pending = False
            if not bool(getattr(self, "_vps_terminal_connected", False)):
                return
            box = getattr(self, "settings_vps_terminal_output", None)
            current_text = ""
            if box is not None:
                try:
                    current_text = str(box.toPlainText() or "")
                except Exception:
                    current_text = ""
            if current_text.strip():
                return
            channel = getattr(self, "_vps_terminal_channel", None)
            if channel is None:
                return
            try:
                channel.send("\n")
            except Exception:
                pass

        QTimer.singleShot(max(80, int(delay_ms or 220)), self, run)

    def _navigate_vps_terminal_history(self, direction: int):
        edit = getattr(self, "settings_vps_terminal_input", None)
        if edit is None:
            return
        history = list(getattr(self, "_vps_terminal_history", []) or [])
        if not history:
            return
        index = getattr(self, "_vps_terminal_history_index", None)
        current_text = self._vps_terminal_input_text()
        if index is None:
            self._vps_terminal_history_draft = current_text
            index = len(history)
        index = int(index)
        if direction < 0:
            index = max(0, index - 1)
            self._set_vps_terminal_input_text(history[index])
        else:
            if index >= len(history) - 1:
                index = len(history)
                self._set_vps_terminal_input_text(str(getattr(self, "_vps_terminal_history_draft", "") or ""))
            else:
                index = min(len(history) - 1, index + 1)
                self._set_vps_terminal_input_text(history[index])
        self._vps_terminal_history_index = index

    def _remember_vps_terminal_command(self, cmd: str):
        text = str(cmd or "").strip()
        if not text:
            return
        history = list(getattr(self, "_vps_terminal_history", []) or [])
        if history and history[-1] == text:
            self._vps_terminal_history_index = len(history)
            self._vps_terminal_history_draft = ""
            return
        history.append(text)
        if len(history) > 200:
            history = history[-200:]
        self._vps_terminal_history = history
        self._vps_terminal_history_index = len(history)
        self._vps_terminal_history_draft = ""

    def _on_vps_profile_combo_changed(self, index):
        if bool(getattr(self, "_vps_profile_combo_updating", False)):
            return
        combo = getattr(self, "settings_vps_profile_combo", None)
        if combo is None:
            return
        profile_id = str(combo.itemData(index) or "").strip()
        if not profile_id:
            return
        previous_id = str(getattr(self, "_vps_form_profile_id", "") or "").strip()
        if previous_id and previous_id != profile_id:
            self._persist_current_vps_profile_from_form(validate_pair=False, silent=True)
        self._set_current_vps_profile_id(profile_id)
        if bool(getattr(self, "_vps_terminal_connected", False)):
            connected_id = str(getattr(self, "_vps_terminal_profile_id", "") or "").strip()
            if connected_id and connected_id != profile_id:
                next_profile = self._current_vps_profile() or {}
                self._disconnect_vps_terminal(reason=f"已切换到 {self._vps_profile_display_name(next_profile)}，旧终端已自动断开。")
        self._reload_vps_panel()

    def _create_vps_profile(self):
        self._persist_current_vps_profile_from_form(validate_pair=False, silent=True)
        text, ok = QInputDialog.getText(self, "新建服务器", "服务器名称", text="新服务器")
        if not ok:
            return
        name = str(text or "").strip() or "新服务器"
        rows = self._vps_profiles()
        new_id = self._make_vps_profile_id(f"{name}-{time.time_ns()}")
        rows.append(
            self._normalize_vps_profile(
                {
                    "id": new_id,
                    "name": name,
                    "remote_dir": remote_agent_dir_default(""),
                    "source": "upload",
                    "dep_install_mode": "offline",
                    "repo_url": str(getattr(lz, "REPO_URL", "") or "").strip(),
                    "python_cmd": "python3",
                }
            )
        )
        self._save_vps_profiles(rows, selected_id=new_id)
        self._reload_vps_panel()
        self._set_status(f"已新建服务器：{name}")

    def _rename_vps_profile(self):
        current = self._current_vps_profile()
        if not isinstance(current, dict):
            return
        old_name = str(current.get("name") or "").strip() or "未命名服务器"
        text, ok = QInputDialog.getText(self, "重命名服务器", "服务器名称", text=old_name)
        if not ok:
            return
        new_name = str(text or "").strip()
        if not new_name or new_name == old_name:
            return
        profile_id = str(current.get("id") or "").strip()
        rows = self._vps_profiles()
        for item in rows:
            if str(item.get("id") or "").strip() == profile_id:
                item["name"] = new_name
                break
        self._save_vps_profiles(rows, selected_id=profile_id)
        if str(getattr(self, "_vps_terminal_profile_id", "") or "").strip() == profile_id:
            self._vps_terminal_profile_name = new_name
        self._reload_vps_panel()
        self._set_status(f"已重命名服务器：{new_name}")

    def _delete_vps_profile(self):
        current = self._current_vps_profile()
        if not isinstance(current, dict):
            return
        name = self._vps_profile_display_name(current)
        answer = QMessageBox.question(self, "删除服务器", f"确定删除“{name}”吗？\n\n已保存的连接和部署配置会一起删除。")
        if answer != QMessageBox.Yes:
            return
        profile_id = str(current.get("id") or "").strip()
        rows = [item for item in self._vps_profiles() if str(item.get("id") or "").strip() != profile_id]
        next_id = str(rows[0].get("id") or "").strip() if rows else ""
        if str(getattr(self, "_vps_terminal_profile_id", "") or "").strip() == profile_id:
            self._disconnect_vps_terminal(reason="当前终端对应的服务器已删除，终端已自动断开。")
        self._save_vps_profiles(rows, selected_id=next_id)
        self._reload_vps_panel()
        self._set_status(f"已删除服务器：{name}")

    def _resolve_vps_python_for_dependency(self):
        cfg_py = str(self.cfg.get("python_exe") or "").strip()
        cfg_abs = lz._resolve_configured_python_exe(cfg_py, agent_dir=self.agent_dir) if cfg_py else ""
        if cfg_abs and os.path.isfile(cfg_abs):
            return cfg_abs
        last_check = getattr(self, "_last_dependency_check", None) or {}
        checked_py = str(last_check.get("python") or "").strip()
        if checked_py and os.path.isfile(checked_py):
            return checked_py
        candidates = lz._system_python_candidates(agent_dir=self.agent_dir)
        if candidates:
            path = str((candidates[0] or {}).get("path") or "").strip()
            if path and os.path.isfile(path):
                return path
        return ""

    def _install_vps_dependencies(self):
        if bool(getattr(self, "_vps_connect_running", False)) or bool(getattr(self, "_vps_dep_install_running", False)):
            QMessageBox.information(self, "请稍候", "当前有任务正在执行，请等待完成后再试。")
            return
        profile_id = str(self._current_vps_profile_id() or "").strip()
        py = self._resolve_vps_python_for_dependency()
        if not py:
            QMessageBox.warning(self, "无法安装", "未找到可用 Python 解释器，无法安装 SSH 依赖。")
            return
        self._set_vps_dependency_install_running(True)
        self._set_status("正在安装 VPS SSH 依赖（paramiko）…")
        self.settings_vps_notice.setText(f"正在为解释器安装依赖：{py}")
        self._append_vps_terminal_dependency_output("安装 SSH 依赖任务开始", banner=True)
        self._append_vps_terminal_dependency_output(f"目标解释器：{py}")
        event_queue: queue.Queue = queue.Queue()

        def push_progress(text: str):
            msg = str(text or "").strip()
            if msg:
                event_queue.put({"event": "progress", "msg": msg})

        holder = {"ok": False, "message": "", "detail": "", "python": py}

        def worker():
            try:
                ready, ready_detail, _payload = lz._probe_python_dependency(py, "paramiko>=3.4", import_name="paramiko")
                if ready:
                    holder["ok"] = True
                    holder["message"] = "SSH 依赖已就绪，无需重复安装。"
                    holder["detail"] = ready_detail
                    return
                boot_ok, boot_detail = lz._bootstrap_python_runtime(
                    py,
                    progress=lambda ev: push_progress((ev or {}).get("msg") or ""),
                )
                if not boot_ok:
                    holder["ok"] = False
                    holder["message"] = "基础依赖准备失败。"
                    holder["detail"] = str(boot_detail or "").strip()
                    return
                install_ok, install_detail = lz._install_python_packages(
                    py,
                    ["paramiko>=3.4"],
                    progress=lambda ev: push_progress((ev or {}).get("msg") or ""),
                    label="安装 SSH 依赖",
                )
                if not install_ok:
                    holder["ok"] = False
                    holder["message"] = "SSH 依赖安装失败。"
                    holder["detail"] = str(install_detail or "").strip()
                    return
                verify_ok, verify_detail, _payload = lz._probe_python_dependency(py, "paramiko>=3.4", import_name="paramiko")
                if not verify_ok:
                    holder["ok"] = False
                    holder["message"] = "安装完成但校验失败。"
                    holder["detail"] = str(verify_detail or "").strip()
                    return
                holder["ok"] = True
                holder["message"] = "SSH 依赖安装完成。"
                holder["detail"] = verify_detail
            except Exception as e:
                holder["ok"] = False
                holder["message"] = "安装过程出现异常。"
                holder["detail"] = str(e)
            finally:
                event_queue.put({"event": "done"})

        thread = threading.Thread(target=worker, name="vps-deps-install", daemon=True)
        thread.start()

        lines = []
        progress_state = {
            "started_at": time.time(),
            "last_msg": f"正在为解释器安装依赖：{py}",
            "last_log_key": "",
            "last_log_at": 0.0,
            "last_heartbeat_bucket": -1,
        }

        def poll():
            while True:
                try:
                    ev = event_queue.get_nowait()
                except queue.Empty:
                    break
                if ev.get("event") == "progress":
                    msg = str(ev.get("msg") or "").strip()
                    if not msg:
                        continue
                    lines.append(msg)
                    progress_state["last_msg"] = msg
                    self.settings_vps_notice.setText(msg)
                    log_key = msg
                    now = time.time()
                    if log_key != progress_state["last_log_key"] or (now - float(progress_state["last_log_at"] or 0.0)) >= 1.0:
                        self._append_vps_terminal_dependency_output(msg)
                        progress_state["last_log_key"] = log_key
                        progress_state["last_log_at"] = now
                elif ev.get("event") == "done":
                    self._set_vps_dependency_install_running(False)
                    stale = self._vps_task_result_stale(profile_id)
                    py_text = str(holder.get("python") or "").strip()
                    if holder.get("ok"):
                        if py_text:
                            self.cfg["python_exe"] = lz._make_python_exe_config_path(py_text, agent_dir=self.agent_dir)
                            lz.save_config(self.cfg)
                        detail = str(holder.get("detail") or "").strip()
                        msg = str(holder.get("message") or "SSH 依赖安装完成。")
                        self.settings_vps_notice.setText(msg + (f"\n{detail}" if detail else ""))
                        self._append_vps_terminal_dependency_output(msg, banner=True)
                        if detail:
                            self._append_vps_terminal_dependency_output(detail)
                        self._set_status("VPS SSH 依赖安装完成。")
                        if not stale:
                            QMessageBox.information(self, "依赖安装完成", msg if not detail else f"{msg}\n\n{detail}")
                    else:
                        detail = str(holder.get("detail") or "").strip()
                        msg = str(holder.get("message") or "SSH 依赖安装失败。")
                        self.settings_vps_notice.setText(msg + (f"\n{detail}" if detail else ""))
                        self._append_vps_terminal_dependency_output(msg, banner=True)
                        if detail:
                            self._append_vps_terminal_dependency_output(detail)
                        self._set_status("VPS SSH 依赖安装失败。")
                        if not stale:
                            QMessageBox.warning(self, "依赖安装失败", msg if not detail else f"{msg}\n\n{detail}")
                    refresher = getattr(self, "_refresh_welcome_state", None)
                    if callable(refresher):
                        refresher()
                    return
            if thread.is_alive():
                elapsed = max(0, int(time.time() - float(progress_state["started_at"] or time.time())))
                bucket = elapsed // 5
                if bucket != int(progress_state["last_heartbeat_bucket"]):
                    progress_state["last_heartbeat_bucket"] = bucket
                    heartbeat = f"安装中，已运行 {elapsed} 秒：{progress_state['last_msg'] or '正在执行依赖安装命令…'}"
                    self.settings_vps_notice.setText(heartbeat)
                    if elapsed >= 5:
                        self._append_vps_terminal_dependency_output(heartbeat)
                QTimer.singleShot(120, poll)

        QTimer.singleShot(120, poll)

    def _vps_decode_candidates(self):
        items = ["utf-8", "gb18030", "gbk", locale.getpreferredencoding(False)]
        seen = set()
        ordered = []
        for item in items:
            enc = str(item or "").strip().lower()
            if not enc or enc in seen:
                continue
            seen.add(enc)
            ordered.append(enc)
        return ordered or ["utf-8", "gb18030", "gbk"]

    def _decode_vps_bytes(self, data, *, final=False):
        raw = bytes(data or b"")
        if not raw:
            return ""
        for enc in self._vps_decode_candidates():
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return raw.decode("utf-8", errors="replace")

    def _vps_terminal_decoder_state(self):
        state = getattr(self, "_vps_terminal_decoder_state_cache", None)
        if isinstance(state, dict):
            return state
        state = {"encoding": "", "decoder": None}
        self._vps_terminal_decoder_state_cache = state
        return state

    def _reset_vps_terminal_decoder(self):
        self._vps_terminal_decoder_state_cache = {"encoding": "", "decoder": None}
        self._refresh_vps_terminal_meta()

    def _decode_vps_terminal_chunk(self, data, *, final=False):
        raw = bytes(data or b"")
        if not raw and not final:
            return ""
        state = self._vps_terminal_decoder_state()
        decoder = state.get("decoder")
        if decoder is None:
            chosen = ""
            for enc in self._vps_decode_candidates():
                try:
                    probe = codecs.getincrementaldecoder(enc)("strict")
                    probe.decode(raw, final=False)
                    chosen = enc
                    break
                except Exception:
                    continue
            if not chosen:
                chosen = "utf-8"
            decoder = codecs.getincrementaldecoder(chosen)("replace")
            state["encoding"] = chosen
            state["decoder"] = decoder
            self._refresh_vps_terminal_meta()
        try:
            return str(decoder.decode(raw, final=bool(final)) or "")
        except Exception:
            text = self._decode_vps_bytes(raw, final=final)
            if final:
                self._reset_vps_terminal_decoder()
                self._refresh_vps_terminal_meta()
            return text

    def _sanitize_vps_terminal_text(self, text: str) -> str:
        msg = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        msg = _ANSI_ESCAPE_RE.sub("", msg)
        msg = _VPS_PROMPT_TITLE_RESIDUE_RE.sub(r"\1", msg)
        msg = _VPS_SHELL_NOISE_RE.sub("\n", msg)
        msg = "".join(ch for ch in msg if ch in ("\n", "\t") or (ord(ch) >= 32 and not (127 <= ord(ch) <= 159)))
        msg = _VPS_DUPLICATED_PROMPT_RE.sub(
            lambda m: f"{m.group('userhost')}:{m.group('promptcwd')}{m.group('suffix')}",
            msg,
        )
        prompt_parts = []
        last = 0
        for match in _VPS_PROMPT_TOKEN_RE.finditer(msg):
            if match.start() <= 0 or msg[match.start() - 1] == "\n":
                continue
            prompt_parts.append(msg[last:match.start()])
            prompt_parts.append("\n")
            prompt_parts.append(match.group(0))
            last = match.end()
        if prompt_parts:
            prompt_parts.append(msg[last:])
            msg = "".join(prompt_parts)
        return msg

    def _sanitize_vps_feedback_text(self, text: str) -> str:
        cleaned = self._sanitize_vps_terminal_text(text)
        lines = [str(line or "").rstrip() for line in cleaned.split("\n")]
        compact = []
        last_blank = False
        for line in lines:
            blank = not str(line or "").strip()
            if blank:
                if last_blank:
                    continue
                compact.append("")
                last_blank = True
                continue
            compact.append(line)
            last_blank = False
        return "\n".join(compact).strip()

    def _append_vps_terminal_output(self, text: str):
        box = getattr(self, "settings_vps_terminal_output", None)
        msg = self._sanitize_vps_terminal_text(text)
        if box is None or not msg:
            return
        try:
            cursor = box.textCursor()
            cursor.movePosition(QTextCursor.End)
            box.setTextCursor(cursor)
            box.setPlaceholderText("")
            box.insertPlainText(msg)
            cursor = box.textCursor()
            cursor.movePosition(QTextCursor.End)
            box.setTextCursor(cursor)
        except Exception:
            pass

    def _append_vps_terminal_deploy_output(self, text: str, *, banner: bool = False):
        raw = str(text or "").strip()
        if not raw:
            return
        prefix = "[部署] "
        if banner:
            line = f"================ {prefix}{raw} ================\n"
        else:
            line = prefix + raw + "\n"
        box = getattr(self, "settings_vps_terminal_output", None)
        current_text = ""
        if box is not None:
            try:
                current_text = str(box.toPlainText() or "")
            except Exception:
                current_text = ""
        if current_text:
            if not current_text.endswith("\n"):
                line = "\n" + line
            elif banner and (not current_text.endswith("\n\n")):
                line = "\n" + line
        self._append_vps_terminal_output(line)

    def _append_vps_terminal_dependency_output(self, text: str, *, banner: bool = False):
        raw = str(text or "").strip()
        if not raw:
            return
        prefix = "[依赖] "
        if banner:
            line = f"================ {prefix}{raw} ================\n"
        else:
            line = prefix + raw + "\n"
        box = getattr(self, "settings_vps_terminal_output", None)
        current_text = ""
        if box is not None:
            try:
                current_text = str(box.toPlainText() or "")
            except Exception:
                current_text = ""
        if current_text:
            if not current_text.endswith("\n"):
                line = "\n" + line
            elif banner and (not current_text.endswith("\n\n")):
                line = "\n" + line
        self._append_vps_terminal_output(line)

    def _clear_vps_terminal_output(self):
        box = getattr(self, "settings_vps_terminal_output", None)
        if box is not None:
            box.clear()

    def _on_vps_deploy_source_changed(self):
        source_combo = getattr(self, "settings_vps_deploy_source_combo", None)
        source = self._combo_current_data_value(source_combo, "upload").lower()
        is_upload = source != "git"
        local_edit = getattr(self, "settings_vps_local_agent_dir_edit", None)
        local_btn = getattr(self, "settings_vps_local_agent_browse_btn", None)
        repo_edit = getattr(self, "settings_vps_repo_url_edit", None)
        if local_edit is not None:
            local_edit.setEnabled(is_upload)
        if local_btn is not None:
            local_btn.setEnabled(is_upload)
        if repo_edit is not None:
            repo_edit.setEnabled(not is_upload)
        self._refresh_vps_action_buttons()

    def _on_vps_dep_install_mode_changed(self):
        combo = getattr(self, "settings_vps_dep_install_mode_combo", None)
        mode = self._combo_current_data_value(combo, "offline").lower()
        if mode == "online":
            mode = "global"
        mirror_edit = getattr(self, "settings_vps_pip_mirror_edit", None)
        if mirror_edit is not None:
            mirror_edit.setEnabled(mode == "mirror")
        self._refresh_vps_action_buttons()

    def _split_vps_upload_excludes(self, raw: str):
        text = str(raw or "").strip()
        if not text:
            return []
        out = []
        for token in text.replace(";", ",").split(","):
            item = str(token or "").strip()
            if not item:
                continue
            normalized = item.replace("\\", "/").lstrip("./").strip("/")
            out.append(normalized or item)
        return out

    def _is_path_excluded_for_upload(self, rel_path: str, excludes):
        path = str(rel_path or "").replace("\\", "/").strip("/")
        if not path:
            return False
        parts = [p for p in path.split("/") if p]
        name = parts[-1] if parts else path
        for pattern in excludes or []:
            p = str(pattern or "").replace("\\", "/").strip()
            if not p:
                continue
            if "/" in p:
                if fnmatch.fnmatch(path, p):
                    return True
                continue
            if fnmatch.fnmatch(name, p):
                return True
            if p in parts:
                return True
        return False

    def _browse_vps_local_agent_dir(self):
        selected = QFileDialog.getExistingDirectory(self, "选择本地 agant 项目目录", os.path.expanduser("~"))
        if not selected:
            return
        edit = getattr(self, "settings_vps_local_agent_dir_edit", None)
        if edit is not None:
            edit.setText(lz._make_config_relative_path(selected))

    def _save_vps_deploy_preferences(self):
        payload = self._persist_current_vps_profile_from_form(validate_pair=False, silent=True)
        if payload is False:
            return self._collect_vps_deploy_form_data()
        if isinstance(payload, dict) and payload:
            return self._normalize_vps_deploy_cfg(payload)
        current = self._current_vps_profile() or {}
        if current:
            return self._normalize_vps_deploy_cfg(current)
        return self._collect_vps_deploy_form_data()

    def _resolve_vps_runtime_connection_payload(self):
        payload = self._persist_current_vps_profile_from_form(validate_pair=False, silent=True)
        if payload is False:
            return {}, "服务器地址和用户名需要同时填写。"
        payload = self._normalize_vps_connection_cfg(payload or self._collect_vps_form_data())
        host = str(payload.get("host") or "").strip()
        username = str(payload.get("username") or "").strip()
        key_rel = str(payload.get("ssh_key_path") or "").strip()
        password = str(payload.get("password") or "").strip()
        if not host or not username:
            return {}, "请先填写服务器地址和用户名。"
        if not key_rel and not password:
            return {}, "请至少提供 SSH 私钥路径或密码。"
        key_abs = lz._resolve_config_path(key_rel) if key_rel else ""
        if key_rel and not os.path.isfile(key_abs):
            return {}, "SSH 私钥路径不存在，请检查后重试。"
        runtime_payload = dict(payload)
        runtime_payload["key_abs"] = key_abs
        return runtime_payload, ""

    def _open_vps_ssh_client(self, payload, *, timeout: int = 10):
        item = payload if isinstance(payload, dict) else {}
        host = str(item.get("host") or "").strip()
        username = str(item.get("username") or "").strip()
        port = int(item.get("port") or 22)
        key_abs = str(item.get("key_abs") or "").strip()
        password = str(item.get("password") or "").strip()
        try:
            import paramiko
        except Exception as e:
            return None, "当前环境缺少 paramiko，无法创建 SSH 连接。", str(e), True
        client = None
        try:
            try:
                logging.getLogger("paramiko").setLevel(logging.CRITICAL)
                logging.getLogger("paramiko.transport").setLevel(logging.CRITICAL)
            except Exception:
                pass
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            connect_kwargs = {
                "hostname": host,
                "port": port,
                "username": username,
                "timeout": max(3, int(timeout or 10)),
                "banner_timeout": max(3, int(timeout or 10)),
                "auth_timeout": max(3, int(timeout or 10)),
                "look_for_keys": False,
                "allow_agent": False,
            }
            if key_abs:
                connect_kwargs["key_filename"] = key_abs
            if password:
                connect_kwargs["password"] = password
                if key_abs:
                    connect_kwargs["passphrase"] = password
            client.connect(**connect_kwargs)
            transport = client.get_transport()
            if transport is None or not transport.is_active():
                raise RuntimeError("SSH 连接已建立但传输层未激活。")
            try:
                transport.set_keepalive(20)
            except Exception:
                pass
            return client, "", "", False
        except Exception as e:
            try:
                if client is not None:
                    client.close()
            except Exception:
                pass
            detail = normalize_ssh_error_text(str(e), context="SSH 连接")
            return None, "SSH 连接失败。", detail, False

    def _vps_exec_remote(self, client, command: str, *, timeout: int = 180):
        if client is None:
            return 1, "", "SSH 客户端不可用"
        cmd = str(command or "").strip()
        if not cmd:
            return 0, "", ""
        try:
            _stdin, stdout, stderr = client.exec_command(cmd, timeout=max(5, int(timeout or 180)), get_pty=True)
            out = self._decode_vps_bytes(stdout.read(), final=True)
            err = self._decode_vps_bytes(stderr.read(), final=True)
            rc = int(stdout.channel.recv_exit_status())
            return rc, out, err
        except Exception as e:
            return 1, "", normalize_ssh_error_text(str(e), context="SSH 命令执行")

    def _vps_default_direct_requirements(self):
        resolved = [str(item or "").strip() for item in resolve_remote_fallback_requirement_specs(self.agent_dir) if str(item or "").strip()]
        if resolved:
            return resolved
        items = []
        seen = set()
        for dep in LAUNCHER_BOOTSTRAP_DEPENDENCIES:
            spec = str(dep.get("package") or "").strip()
            if spec and spec not in seen:
                seen.add(spec)
                items.append(spec)
        for spec in (
            "streamlit>=1.37",
            "markdown>=3.6",
            "qrcode>=8.0",
            "pycryptodome>=3.20",
            "bottle>=0.12",
            "simple-websocket-server>=0.4.4",
            "beautifulsoup4>=4.12",
        ):
            if spec not in seen:
                seen.add(spec)
                items.append(spec)
        return items

    def _vps_render_bootstrap_requirements(self):
        return "\n".join(self._vps_default_direct_requirements()) + "\n"

    def _vps_dep_install_source(self, dep_install_mode: str, pip_mirror_url: str = ""):
        mode = str(dep_install_mode or "offline").strip().lower()
        if mode == "online":
            mode = "global"
        if mode not in ("offline", "global", "mirror"):
            mode = "offline"
        source = {
            "mode": mode,
            "label": self._vps_dep_install_mode_label(mode),
            "index_url": "https://pypi.tuna.tsinghua.edu.cn/simple",
            "trusted_host": "pypi.tuna.tsinghua.edu.cn",
            "probe_url": "https://pypi.tuna.tsinghua.edu.cn/simple",
        }
        if mode == "global":
            source.update(
                {
                    "index_url": "https://pypi.org/simple",
                    "trusted_host": "pypi.org",
                    "probe_url": "https://pypi.org/simple",
                }
            )
        elif mode == "mirror":
            parsed = urlparse(str(pip_mirror_url or "").strip())
            host = str(getattr(parsed, "hostname", "") or "").strip()
            source.update(
                {
                    "index_url": str(pip_mirror_url or "").strip(),
                    "trusted_host": host,
                    "probe_url": str(pip_mirror_url or "").strip(),
                }
            )
        return source

    def _vps_render_remote_requirement_install_cmd(self, python_cmd: str, requirements_path: str, dep_install_mode: str, pip_mirror_url: str):
        source = self._vps_dep_install_source(dep_install_mode, pip_mirror_url)
        q_py = shlex.quote(str(python_cmd or "python3").strip() or "python3")
        q_req = shlex.quote(str(requirements_path or "").strip())
        base = (
            "set -e; "
            f"PY_BIN={q_py}; "
            f"REQ_FILE={q_req}; "
            "\"$PY_BIN\" -m ensurepip --upgrade >/dev/null 2>&1 || true; "
            "\"$PY_BIN\" -m pip --version >/dev/null 2>&1 || { echo '__NO_PIP__'; exit 71; }; "
            "[ -f \"$REQ_FILE\" ] || { echo '__REQ_MISSING__'; exit 72; }; "
            "export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1; "
        )
        if str(source.get("mode") or "").strip() == "global":
            return base + "\"$PY_BIN\" -m pip install --default-timeout=180 --retries=6 -r \"$REQ_FILE\""
        q_index = shlex.quote(str(source.get("index_url") or "").strip())
        q_host = shlex.quote(str(source.get("trusted_host") or "").strip())
        return (
            base
            + "\"$PY_BIN\" -m pip install --default-timeout=180 --retries=6 "
            + f"-i {q_index} --trusted-host {q_host} -r \"$REQ_FILE\" "
            + "|| \"$PY_BIN\" -m pip install --default-timeout=180 --retries=6 -r \"$REQ_FILE\""
        )

    def _connect_vps_terminal(self):
        if bool(getattr(self, "_vps_terminal_connected", False)):
            QMessageBox.information(self, "已连接", "远程终端已经连接。")
            return
        if bool(getattr(self, "_vps_terminal_connecting", False)):
            QMessageBox.information(self, "请稍候", "终端正在连接，请等待。")
            return
        payload, error = self._resolve_vps_runtime_connection_payload()
        if error:
            QMessageBox.warning(self, "无法连接终端", error)
            return
        current = self._current_vps_profile() or {}
        profile_id = str(current.get("id") or getattr(self, "_vps_form_profile_id", "") or "").strip()
        profile_name = self._vps_profile_display_name(current)
        self._set_vps_terminal_connecting(True)
        self._refresh_vps_terminal_meta()
        self._set_status("正在连接 VPS 终端…")

        holder = {"ok": False, "client": None, "channel": None, "error": "", "detail": "", "missing_paramiko": False}

        def worker():
            client, err_msg, detail, missing = self._open_vps_ssh_client(payload, timeout=10)
            if client is None:
                holder["ok"] = False
                holder["error"] = err_msg
                holder["detail"] = detail
                holder["missing_paramiko"] = bool(missing)
                return
            try:
                channel = client.invoke_shell(term="xterm", width=180, height=42)
                holder["ok"] = True
                holder["client"] = client
                holder["channel"] = channel
            except Exception as e:
                holder["ok"] = False
                holder["error"] = "终端通道创建失败。"
                holder["detail"] = str(e)
                try:
                    client.close()
                except Exception:
                    pass

        thread = threading.Thread(target=worker, name="vps-terminal-connect", daemon=True)
        thread.start()

        def poll():
            if thread.is_alive():
                QTimer.singleShot(120, poll)
                return
            self._set_vps_terminal_connecting(False)
            stale = self._vps_task_result_stale(profile_id)
            if not holder.get("ok"):
                msg = str(holder.get("error") or "终端连接失败。")
                detail = str(holder.get("detail") or "").strip()
                if bool(holder.get("missing_paramiko")):
                    msg += "\n\n请先在 VPS 管理页点击“安装 SSH 依赖”。"
                self.settings_vps_terminal_output.setPlaceholderText(msg)
                self._set_status("VPS 终端连接失败。")
                self._refresh_vps_terminal_meta()
                if not stale:
                    QMessageBox.warning(self, "终端连接失败", msg if not detail else f"{msg}\n\n{detail}")
                return
            if stale:
                client = holder.get("client")
                channel = holder.get("channel")
                try:
                    if channel is not None:
                        channel.close()
                except Exception:
                    pass
                try:
                    if client is not None:
                        client.close()
                except Exception:
                    pass
                self._set_status(f"已忽略过期的终端连接结果：{profile_name}")
                self._refresh_vps_terminal_meta()
                self._refresh_vps_action_buttons()
                return
            self._vps_terminal_client = holder.get("client")
            self._vps_terminal_channel = holder.get("channel")
            self._vps_terminal_connected = True
            self._vps_terminal_profile_id = profile_id
            self._vps_terminal_profile_name = profile_name
            self._vps_terminal_stop_event = threading.Event()
            self._vps_terminal_queue = queue.Queue()
            self._reset_vps_terminal_decoder()
            self._clear_vps_terminal_output()
            self._vps_terminal_bootstrap_marker = ""
            self._vps_terminal_bootstrap_done = True
            self._vps_terminal_bootstrap_buffer = ""
            self._set_status(f"VPS 终端已连接：{profile_name}")
            self.settings_vps_terminal_output.setPlaceholderText(
                f"已连接到 {profile_name}，正在等待远端 shell 输出…"
            )
            self._vps_terminal_history_index = len(list(getattr(self, "_vps_terminal_history", []) or []))
            self._vps_terminal_history_draft = ""
            self._refresh_vps_terminal_meta()
            self._refresh_vps_action_buttons()
            self._start_vps_terminal_reader()
            self._schedule_vps_terminal_prompt_refresh(delay_ms=360)

        QTimer.singleShot(120, poll)

    def _disconnect_vps_terminal(self, *, reason: str = ""):
        stop_event = getattr(self, "_vps_terminal_stop_event", None)
        if stop_event is not None:
            try:
                stop_event.set()
            except Exception:
                pass
        channel = getattr(self, "_vps_terminal_channel", None)
        if channel is not None:
            try:
                channel.close()
            except Exception:
                pass
        client = getattr(self, "_vps_terminal_client", None)
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        self._vps_terminal_channel = None
        self._vps_terminal_client = None
        self._vps_terminal_connected = False
        self._vps_terminal_profile_id = ""
        self._vps_terminal_profile_name = ""
        self._vps_terminal_bootstrap_marker = ""
        self._vps_terminal_bootstrap_done = False
        self._vps_terminal_bootstrap_buffer = ""
        self._vps_terminal_prompt_refresh_pending = False
        self._reset_vps_terminal_decoder()
        if reason:
            self._set_status(str(reason).strip())
        self._refresh_vps_terminal_meta()
        self._refresh_vps_action_buttons()
        self.settings_vps_terminal_output.setPlaceholderText(str(reason or "终端已断开。").strip())

    def _send_vps_terminal_command(self):
        if not bool(getattr(self, "_vps_terminal_connected", False)):
            QMessageBox.information(self, "未连接", "请先连接远程终端。")
            return
        cmd_raw = self._vps_terminal_input_text()
        cmd = str(cmd_raw or "").strip()
        if not cmd:
            return
        channel = getattr(self, "_vps_terminal_channel", None)
        if channel is None:
            QMessageBox.warning(self, "发送失败", "终端通道不可用，请重新连接。")
            self._disconnect_vps_terminal()
            return
        try:
            payload = cmd if cmd.endswith("\n") else (cmd + "\n")
            channel.send(payload)
            self._remember_vps_terminal_command(cmd)
            self._clear_vps_terminal_input()
            self._vps_terminal_history_index = len(list(getattr(self, "_vps_terminal_history", []) or []))
            self._vps_terminal_history_draft = ""
        except Exception as e:
            self.settings_vps_terminal_output.setPlaceholderText("命令发送失败，请重新连接后再试。")
            self._set_status("VPS 终端命令发送失败。")
            QMessageBox.warning(self, "发送失败", f"命令发送失败：\n\n{e}")
            self._disconnect_vps_terminal()

    def _start_vps_terminal_reader(self):
        channel = getattr(self, "_vps_terminal_channel", None)
        event_queue = getattr(self, "_vps_terminal_queue", None)
        stop_event = getattr(self, "_vps_terminal_stop_event", None)
        if channel is None or event_queue is None or stop_event is None:
            return

        def reader():
            try:
                while not stop_event.is_set():
                    if channel.closed:
                        break
                    if channel.recv_ready():
                        data = channel.recv(4096)
                        if not data:
                            break
                        chunk = self._decode_vps_terminal_chunk(data, final=False)
                        if chunk:
                            event_queue.put({"event": "chunk", "text": chunk})
                    else:
                        time.sleep(0.08)
            except Exception as e:
                detail = str(e or "").strip()
                if stop_event.is_set() or _looks_like_ssh_disconnect(detail):
                    reason = "终端已断开。" if stop_event.is_set() else _friendly_ssh_disconnect_reason(detail, context="终端")
                    event_queue.put({"event": "disconnect", "text": reason})
                else:
                    event_queue.put({"event": "error", "text": detail})
            finally:
                try:
                    tail = self._decode_vps_terminal_chunk(b"", final=True)
                    if tail:
                        event_queue.put({"event": "chunk", "text": tail})
                except Exception:
                    pass
                event_queue.put({"event": "closed"})

        thread = threading.Thread(target=reader, name="vps-terminal-reader", daemon=True)
        thread.start()

        def pump():
            q = getattr(self, "_vps_terminal_queue", None)
            if q is None:
                return
            closed = False
            disconnect_reason = ""
            while True:
                try:
                    ev = q.get_nowait()
                except queue.Empty:
                    break
                event = str(ev.get("event") or "").strip()
                if event == "chunk":
                    text = str(ev.get("text") or "")
                    marker = str(getattr(self, "_vps_terminal_bootstrap_marker", "") or "")
                    if marker and (not bool(getattr(self, "_vps_terminal_bootstrap_done", False))):
                        pending = str(getattr(self, "_vps_terminal_bootstrap_buffer", "") or "") + text
                        if marker in pending:
                            _before, after = pending.split(marker, 1)
                            self._vps_terminal_bootstrap_done = True
                            self._vps_terminal_bootstrap_buffer = ""
                            self._clear_vps_terminal_output()
                            cleaned = self._sanitize_vps_terminal_text(after).lstrip("\n")
                            if cleaned:
                                self.settings_vps_terminal_output.setPlaceholderText("")
                                self._append_vps_terminal_output(cleaned)
                            else:
                                current = self._current_vps_profile() or {}
                                profile_name = str(getattr(self, "_vps_terminal_profile_name", "") or "").strip()
                                target_name = profile_name or self._vps_profile_display_name(current)
                                self.settings_vps_terminal_output.setPlaceholderText(
                                    f"已连接到 {target_name}。远端当前没有输出，可直接输入命令。"
                                )
                                self._schedule_vps_terminal_prompt_refresh()
                        else:
                            self._vps_terminal_bootstrap_buffer = pending[-32768:]
                    else:
                        self._append_vps_terminal_output(text)
                elif event == "error":
                    self._set_status("VPS 终端读取异常。")
                    self.settings_vps_terminal_output.setPlaceholderText(str(ev.get("text") or "终端读取异常。"))
                elif event == "disconnect":
                    disconnect_reason = str(ev.get("text") or "终端已断开。").strip() or "终端已断开。"
                    closed = True
                elif event == "closed":
                    closed = True
            if closed:
                if bool(getattr(self, "_vps_terminal_connected", False)):
                    self._disconnect_vps_terminal(reason=disconnect_reason)
                return
            if bool(getattr(self, "_vps_terminal_connected", False)) or thread.is_alive():
                QTimer.singleShot(120, pump)

        QTimer.singleShot(120, pump)

    def _deploy_vps_agent_direct(self):
        if bool(getattr(self, "_vps_deploy_running", False)):
            QMessageBox.information(self, "请稍候", "部署任务正在执行，请等待完成。")
            return
        payload, error = self._resolve_vps_runtime_connection_payload()
        if error:
            QMessageBox.warning(self, "无法部署", error)
            return
        current = self._current_vps_profile() or {}
        target_name = self._vps_profile_display_name(current)
        deploy_cfg = self._save_vps_deploy_preferences()
        source = str(deploy_cfg.get("source") or "upload").strip().lower()
        local_rel = str(deploy_cfg.get("local_agent_dir") or "").strip()
        local_abs = lz._resolve_config_path(local_rel) if local_rel else ""
        repo_url = str(deploy_cfg.get("repo_url") or "").strip()
        remote_dir = str(deploy_cfg.get("remote_dir") or "").strip()
        dep_install_mode = str(deploy_cfg.get("dep_install_mode") or "offline").strip().lower()
        if dep_install_mode == "online":
            dep_install_mode = "global"
        if dep_install_mode not in ("offline", "global", "mirror"):
            dep_install_mode = "offline"
        pip_mirror_url = str(deploy_cfg.get("pip_mirror_url") or "").strip()
        dep_source = self._vps_dep_install_source(dep_install_mode, pip_mirror_url)
        upload_excludes_raw = str(deploy_cfg.get("upload_excludes") or "").strip()
        upload_excludes = self._split_vps_upload_excludes(upload_excludes_raw)
        if not remote_dir:
            QMessageBox.warning(self, "无法部署", "请填写远端部署目录。")
            return
        if source == "upload":
            if not local_abs or not os.path.isdir(local_abs):
                QMessageBox.warning(self, "无法部署", "上传模式下，本地 agant 目录不存在。")
                return
        elif not repo_url:
            QMessageBox.warning(self, "无法部署", "拉取模式下，仓库地址不能为空。")
            return
        if dep_install_mode == "mirror":
            if not pip_mirror_url:
                QMessageBox.warning(self, "无法部署", "自定义镜像策略要求填写 pip 镜像地址。")
                return
            parsed = urlparse(pip_mirror_url)
            if parsed.scheme not in ("http", "https") or (not parsed.netloc):
                QMessageBox.warning(self, "无法部署", "pip 镜像地址格式无效，请填写 http(s) URL。")
                return
        self._set_vps_deploy_running(True)
        self._set_status("正在执行 VPS 直接部署…")
        self.settings_vps_deploy_notice.setText(f"直接部署任务已启动，正在连接服务器… 当前目标：{target_name}")
        self._append_vps_terminal_deploy_output(f"部署任务开始：{target_name}", banner=True)
        profile_id = str(current.get("id") or "").strip()
        profile_python = str(current.get("python_cmd") or "python3").strip() or "python3"
        log_queue: queue.Queue = queue.Queue()
        holder = {"ok": False, "message": "", "detail": "", "python_cmd": profile_python}

        def push(msg: str):
            text = str(msg or "").strip()
            if text:
                log_queue.put({"event": "line", "text": text})

        def worker():
            client = None
            tar_path = ""
            try:
                client, err_msg, detail, missing = self._open_vps_ssh_client(payload, timeout=12)
                if client is None:
                    if missing:
                        holder["message"] = "缺少 paramiko，无法执行部署。"
                        holder["detail"] = detail
                    else:
                        holder["message"] = err_msg or "SSH 连接失败。"
                        holder["detail"] = detail
                    return
                push("SSH 连接成功，开始直部署前预检。")
                preflight_rows = []

                def _add_preflight(name: str, ok: bool, detail_text: str, *, critical: bool):
                    preflight_rows.append({"name": name, "ok": bool(ok), "detail": str(detail_text or "").strip(), "critical": bool(critical)})

                py_probe_cmd = (
                    "set -e; "
                    f"PY_BIN={shlex.quote(profile_python)}; "
                    "if command -v \"$PY_BIN\" >/dev/null 2>&1; then printf '__PY__|%s' \"$PY_BIN\"; "
                    "elif command -v python3 >/dev/null 2>&1; then printf '__PY__|python3'; "
                    "elif command -v python >/dev/null 2>&1; then printf '__PY__|python'; "
                    "else echo '__NO_PY__'; exit 41; fi"
                )
                rc, out, err = self._vps_exec_remote(client, py_probe_cmd, timeout=20)
                py_probe_text = str(out or err or "").strip()
                if rc == 0 and "__PY__|" in py_probe_text:
                    holder["python_cmd"] = py_probe_text.split("|", 1)[1].strip() or profile_python
                    _add_preflight("远端 Python", True, f"可用：{holder['python_cmd']}", critical=True)
                else:
                    _add_preflight("远端 Python", False, "未检测到 python3 / python", critical=True)

                parent_dir = os.path.dirname(remote_dir.rstrip("/")) or "/"
                rc, out, err = self._vps_exec_remote(client, f"test -w {shlex.quote(parent_dir)} && echo OK || echo FAIL", timeout=12)
                writable = (rc == 0) and ("OK" in (out or ""))
                _add_preflight("目录写权限", writable, f"{parent_dir} " + ("可写" if writable else "不可写"), critical=True)

                rc, out, err = self._vps_exec_remote(
                    client,
                    f"df -Pm {shlex.quote(parent_dir)} 2>/dev/null | tail -1 | awk '{{print $4}}'",
                    timeout=12,
                )
                disk_text = (out or err).strip()
                disk_ok = False
                if rc == 0:
                    try:
                        disk_free_mb = int(str(disk_text or "0").split()[0])
                        disk_ok = disk_free_mb >= 2048
                        disk_text = f"可用 {disk_free_mb} MB"
                    except Exception:
                        disk_text = disk_text or "读取失败"
                _add_preflight("磁盘空间", disk_ok, disk_text or "读取失败", critical=False)

                rc, out, err = self._vps_exec_remote(client, "free -m | awk '/Mem:/ {print $7}'", timeout=12)
                mem_text = (out or err).strip()
                mem_ok = False
                if rc == 0:
                    try:
                        mem_avail_mb = int(str(mem_text or "0").split()[0])
                        mem_ok = mem_avail_mb >= 512
                        mem_text = f"可用 {mem_avail_mb} MB"
                    except Exception:
                        mem_text = mem_text or "读取失败"
                _add_preflight("可用内存", mem_ok, mem_text or "读取失败", critical=False)

                probe_url = str(dep_source.get("probe_url") or "").strip()
                if probe_url:
                    q_probe = shlex.quote(probe_url)
                    rc, out, err = self._vps_exec_remote(
                        client,
                        (
                            f"(command -v curl >/dev/null 2>&1 && curl -I -L --max-time 10 {q_probe} >/dev/null 2>&1) "
                            f"|| (command -v wget >/dev/null 2>&1 && wget -q --spider --timeout=10 {q_probe}) "
                            "|| (echo '__NET_CHECK_SKIPPED__')"
                        ),
                        timeout=15,
                    )
                    net_raw = (out or err).strip()
                    if "__NET_CHECK_SKIPPED__" in net_raw:
                        _add_preflight("依赖源连通", True, "服务器无 curl/wget，跳过网络预检", critical=False)
                    else:
                        _add_preflight(
                            "依赖源连通",
                            rc == 0,
                            (f"可访问 {probe_url}" if rc == 0 else (net_raw or f"无法访问 {probe_url}")),
                            critical=True,
                        )

                push("预检结果：")
                for row in preflight_rows:
                    mark = "PASS" if row.get("ok") else ("FAIL" if row.get("critical") else "WARN")
                    push(f"- {mark} {row.get('name')}: {row.get('detail')}")
                critical_fail = [r for r in preflight_rows if (not r.get("ok")) and r.get("critical")]
                if critical_fail:
                    holder["message"] = "部署前预检未通过。"
                    holder["detail"] = "\n".join([f"{r.get('name')}: {r.get('detail')}" for r in critical_fail])
                    return

                q_remote_dir = shlex.quote(remote_dir)
                rc, out, err = self._vps_exec_remote(client, f"mkdir -p {q_remote_dir}", timeout=30)
                if rc != 0:
                    holder["message"] = "创建远端部署目录失败。"
                    holder["detail"] = (err or out or "").strip()
                    return

                if source == "upload":
                    push("部署来源：上传本地 agant 项目。正在打包并上传…")
                    fd, tar_path = tempfile.mkstemp(prefix="ga_agent_", suffix=".tar.gz")
                    os.close(fd)
                    local_mykey_abs = os.path.join(local_abs, "mykey.py")
                    has_local_mykey = bool(local_mykey_abs and os.path.isfile(local_mykey_abs))
                    if has_local_mykey:
                        push("检测到本地 mykey.py，将在上传后强制同步。")
                    else:
                        push("未检测到本地 mykey.py，部署后可能无法直接聊天。")
                    excludes = list(upload_excludes or [])
                    if not excludes:
                        excludes = self._split_vps_upload_excludes(".git,.venv,venv,temp,tests,__pycache__,.pytest_cache,node_modules")
                    excluded_counter = {"count": 0}

                    def _filter_tar(info):
                        raw = str(getattr(info, "name", "") or "").replace("\\", "/").strip("/")
                        if raw.startswith("agant_src/"):
                            rel = raw[len("agant_src/") :]
                        elif raw == "agant_src":
                            rel = ""
                        else:
                            rel = raw
                        if rel.lower() == "mykey.py":
                            return info
                        if rel and self._is_path_excluded_for_upload(rel, excludes):
                            excluded_counter["count"] += 1
                            return None
                        return info

                    with tarfile.open(tar_path, "w:gz") as tar:
                        tar.add(local_abs, arcname="agant_src", filter=_filter_tar)
                    try:
                        tar_size_mb = round(float(os.path.getsize(tar_path) or 0.0) / (1024 * 1024), 2)
                    except Exception:
                        tar_size_mb = 0.0
                    push(f"打包完成：{tar_size_mb} MB（排除 {excluded_counter['count']} 个条目）。")
                    remote_tar = f"/tmp/ga_launcher_agent_{int(time.time())}.tar.gz"
                    sftp = client.open_sftp()
                    try:
                        sftp.put(tar_path, remote_tar)
                    finally:
                        try:
                            sftp.close()
                        except Exception:
                            pass
                    deploy_cmd = (
                        f"set -e; mkdir -p {q_remote_dir}; "
                        f"find {q_remote_dir} -mindepth 1 -maxdepth 1 -exec rm -rf {{}} +; "
                        f"tar -xzf {shlex.quote(remote_tar)} -C {q_remote_dir} --strip-components=1; "
                        f"rm -f {shlex.quote(remote_tar)}"
                    )
                    rc, out, err = self._vps_exec_remote(client, deploy_cmd, timeout=300)
                    if rc != 0:
                        holder["message"] = "上传包解压失败。"
                        holder["detail"] = (err or out or "").strip()
                        return
                    if has_local_mykey:
                        remote_mykey = remote_dir.rstrip("/") + "/mykey.py"
                        sftp = client.open_sftp()
                        try:
                            sftp.put(local_mykey_abs, remote_mykey)
                        finally:
                            try:
                                sftp.close()
                            except Exception:
                                pass
                        push("已同步 mykey.py 到远端目录。")
                    push("本地项目上传并解压完成。")
                else:
                    push("部署来源：服务器拉取仓库。")
                    rc, out, err = self._vps_exec_remote(client, "git --version", timeout=30)
                    if rc != 0:
                        holder["message"] = "服务器未检测到 Git，无法拉取仓库。"
                        holder["detail"] = (err or out or "").strip()
                        return
                    q_repo = shlex.quote(repo_url)
                    pull_cmd = (
                        "set -e; "
                        f"if [ -d {q_remote_dir}/.git ]; then "
                        f"branch=$(git -C {q_remote_dir} symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@'); "
                        "if [ -z \"$branch\" ]; then branch=main; fi; "
                        f"git -C {q_remote_dir} fetch --all --prune; "
                        f"git -C {q_remote_dir} checkout \"$branch\"; "
                        f"git -C {q_remote_dir} reset --hard \"origin/$branch\"; "
                        f"elif [ -d {q_remote_dir} ] && [ -n \"$(ls -A {q_remote_dir} 2>/dev/null)\" ]; then "
                        "echo '__DEPLOY_DIR_NOT_EMPTY__'; exit 44; "
                        "else "
                        f"rm -rf {q_remote_dir}; git clone --depth 1 {q_repo} {q_remote_dir}; "
                        "fi"
                    )
                    rc, out, err = self._vps_exec_remote(client, pull_cmd, timeout=360)
                    if rc != 0:
                        text = (err or out or "").strip()
                        if "__DEPLOY_DIR_NOT_EMPTY__" in text:
                            holder["message"] = "远端目录非 Git 仓库且不为空，已停止部署。"
                            holder["detail"] = f"目录：{remote_dir}\n请改用“上传本地项目”或手动清空目录。"
                        else:
                            holder["message"] = "拉取仓库失败。"
                            holder["detail"] = text
                        return
                    push("仓库同步完成。")

                push("开始执行远端直部署。")
                if dep_source.get("mode") == "mirror":
                    push(f"依赖安装策略：{dep_source.get('label')}。")
                elif dep_source.get("mode") == "global":
                    push("依赖安装策略：国际源（PyPI）。")
                else:
                    push("依赖安装策略：内置源（清华）。")

                verify_cmd = (
                    "set -e; "
                    f"TARGET={q_remote_dir}; "
                    "[ -d \"$TARGET\" ] || { echo '__MISSING_DIR__'; exit 61; }; "
                    "[ -f \"$TARGET/agentmain.py\" ] || { echo '__MISSING_AGENTMAIN__'; exit 62; }; "
                    "[ -d \"$TARGET/frontends\" ] || { echo '__MISSING_FRONTENDS__'; exit 63; }; "
                    f"PY_BIN={shlex.quote(holder['python_cmd'])}; "
                    "if ! command -v \"$PY_BIN\" >/dev/null 2>&1; then "
                    "if command -v python3 >/dev/null 2>&1; then PY_BIN=python3; "
                    "elif command -v python >/dev/null 2>&1; then PY_BIN=python; "
                    "else echo '__NO_PY__'; exit 64; fi; "
                    "fi; "
                    "mkdir -p \"$TARGET/temp/launcher_runtime\" \"$TARGET/temp/launcher_sessions\"; "
                    "printf '__READY__|%s|%s' \"$TARGET\" \"$PY_BIN\""
                )
                rc, out, err = self._vps_exec_remote(client, verify_cmd, timeout=40)
                verify_text = str(out or err or "").strip()
                if rc != 0 or "__READY__|" not in verify_text:
                    if "__MISSING_DIR__" in verify_text:
                        holder["message"] = "远端部署目录不存在。"
                        holder["detail"] = remote_dir
                    elif "__MISSING_AGENTMAIN__" in verify_text:
                        holder["message"] = "远端项目缺少 agentmain.py。"
                        holder["detail"] = remote_dir
                    elif "__MISSING_FRONTENDS__" in verify_text:
                        holder["message"] = "远端项目缺少 frontends 目录。"
                        holder["detail"] = remote_dir
                    elif "__NO_PY__" in verify_text:
                        holder["message"] = "服务器未检测到可用 Python。"
                        holder["detail"] = "请确认服务器至少存在 python3 或 python。"
                    else:
                        holder["message"] = "远端项目预检失败。"
                        holder["detail"] = verify_text or "请检查目录结构与权限。"
                    return
                _prefix, ready_dir, ready_py = verify_text.split("|", 2)
                ready_dir = str(ready_dir or remote_dir).strip() or remote_dir
                holder["python_cmd"] = str(ready_py or holder["python_cmd"]).strip() or holder["python_cmd"]

                remote_req_path = ready_dir.rstrip("/") + "/requirements.txt"
                req_probe_cmd = (
                    "set -e; "
                    f"TARGET={shlex.quote(remote_req_path)}; "
                    "if [ -f \"$TARGET\" ]; then echo '__REQ__'; else echo '__NO_REQ__'; fi"
                )
                rc, out, err = self._vps_exec_remote(client, req_probe_cmd, timeout=20)
                if rc != 0:
                    holder["message"] = "检查远端 requirements.txt 失败。"
                    holder["detail"] = (err or out).strip()
                    return
                req_probe_text = str(out or err or "").strip()
                used_fallback_requirements = "__REQ__" not in req_probe_text
                if used_fallback_requirements:
                    remote_req_path = ready_dir.rstrip("/") + "/temp/launcher_runtime/requirements.launcher_bootstrap.txt"
                    manifest = resolve_upstream_dependency_manifest(self.agent_dir)
                    if manifest.get("pyproject_used"):
                        push("上游未提供 requirements.txt；当前改用 pyproject.toml 生成 fallback requirements。")
                    else:
                        push("上游未提供 requirements.txt；当前改用启动器维护的上游依赖表。")
                    try:
                        req_text = self._vps_render_bootstrap_requirements()
                        sftp = client.open_sftp()
                        try:
                            with sftp.open(remote_req_path, "wb") as fp:
                                fp.write(req_text.encode("utf-8"))
                        finally:
                            try:
                                sftp.close()
                            except Exception:
                                pass
                    except Exception as e:
                        holder["message"] = "写入远端 fallback requirements 失败。"
                        holder["detail"] = str(e)
                        return
                    push("已写入 fallback requirements 到 temp/launcher_runtime。")
                else:
                    push("检测到远端 requirements.txt，优先使用仓库自带依赖表。")

                install_cmd = self._vps_render_remote_requirement_install_cmd(
                    holder["python_cmd"],
                    remote_req_path,
                    dep_install_mode,
                    pip_mirror_url,
                )
                rc, out, err = self._vps_exec_remote(client, install_cmd, timeout=1200)
                install_text = str(out or err or "").strip()
                if rc != 0:
                    if "__NO_PIP__" in install_text:
                        holder["message"] = "服务器上的 Python 缺少 pip。"
                        holder["detail"] = f"Python：{holder['python_cmd']}"
                    elif "__REQ_MISSING__" in install_text:
                        holder["message"] = "远端依赖文件不存在。"
                        holder["detail"] = remote_req_path
                    else:
                        holder["message"] = "依赖安装失败。"
                        holder["detail"] = install_text
                    return
                push("依赖安装完成。")

                python_check_cmd = (
                    "set -e; "
                    f"cd {shlex.quote(ready_dir)}; "
                    f"PY_BIN={shlex.quote(holder['python_cmd'])}; "
                    "\"$PY_BIN\" -c \"import sys; print('__PY_OK__|' + sys.executable)\"; "
                    "\"$PY_BIN\" -m py_compile agentmain.py; "
                    "if [ -f frontends/stapp.py ]; then \"$PY_BIN\" -m py_compile frontends/stapp.py; fi"
                )
                rc, out, err = self._vps_exec_remote(client, python_check_cmd, timeout=120)
                python_check_text = str(out or err or "").strip()
                if rc != 0 or "__PY_OK__|" not in python_check_text:
                    holder["message"] = "远端 Python 校验失败。"
                    holder["detail"] = python_check_text or "请检查依赖安装与项目目录。"
                    return
                holder["ok"] = True
                holder["message"] = "直接部署完成。"
                req_label = "requirements.txt"
                if used_fallback_requirements:
                    manifest = resolve_upstream_dependency_manifest(self.agent_dir)
                    req_label = "temp/launcher_runtime/requirements.launcher_bootstrap.txt"
                    if manifest.get("pyproject_used"):
                        req_label += "（由 pyproject.toml 生成）"
                py_exec = python_check_text.split("|", 1)[1].splitlines()[0].strip() if "__PY_OK__|" in python_check_text else holder["python_cmd"]
                holder["detail"] = f"远端目录：{ready_dir}；Python：{py_exec or holder['python_cmd']}；依赖文件：{req_label}"
            except Exception as e:
                holder["ok"] = False
                holder["message"] = "部署过程中出现异常。"
                holder["detail"] = str(e)
            finally:
                if tar_path and os.path.isfile(tar_path):
                    try:
                        os.remove(tar_path)
                    except Exception:
                        pass
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass
                log_queue.put({"event": "done"})

        thread = threading.Thread(target=worker, name="vps-direct-deploy", daemon=True)
        thread.start()

        logs = []

        def poll():
            while True:
                try:
                    ev = log_queue.get_nowait()
                except queue.Empty:
                    break
                event = str(ev.get("event") or "").strip()
                if event == "line":
                    line = str(ev.get("text") or "").strip()
                    if line:
                        logs.append(line)
                        self.settings_vps_deploy_notice.setText(line)
                        self._append_vps_terminal_deploy_output(line)
                elif event == "done":
                    self._set_vps_deploy_running(False)
                    stale = self._vps_task_result_stale(profile_id)
                    msg = str(holder.get("message") or "部署结束。")
                    detail = self._sanitize_vps_feedback_text(holder.get("detail") or "")
                    if holder.get("ok"):
                        ready_py = str(holder.get("python_cmd") or "").strip()
                        if ready_py:
                            rows = self._vps_profiles()
                            changed = False
                            for item in rows:
                                if str(item.get("id") or "").strip() != profile_id:
                                    continue
                                if str(item.get("python_cmd") or "").strip() != ready_py:
                                    item["python_cmd"] = ready_py
                                    changed = True
                                break
                            if changed:
                                self._save_vps_profiles(rows, selected_id=profile_id)
                        self._update_vps_profile_runtime_summary(kind="deploy", ok=True, message=msg, detail=detail)
                        self.settings_vps_deploy_notice.setText(msg + (f"\n{detail}" if detail else ""))
                        self._append_vps_terminal_deploy_output(msg, banner=True)
                        if detail:
                            self._append_vps_terminal_deploy_output(detail)
                        self._set_status("VPS 直接部署完成。")
                        self._reload_vps_panel()
                        if not stale:
                            QMessageBox.information(self, "部署完成", msg if not detail else f"{msg}\n\n{detail}")
                    else:
                        self._update_vps_profile_runtime_summary(kind="deploy", ok=False, message=msg, detail=detail)
                        self.settings_vps_deploy_notice.setText(msg + (f"\n{detail}" if detail else ""))
                        self._append_vps_terminal_deploy_output(msg, banner=True)
                        if detail:
                            self._append_vps_terminal_deploy_output(detail)
                        self._set_status("VPS 直接部署失败。")
                        self._reload_vps_panel()
                        if not stale:
                            QMessageBox.warning(self, "部署失败", msg if not detail else f"{msg}\n\n{detail}")
                    return
            if thread.is_alive():
                QTimer.singleShot(140, poll)

        QTimer.singleShot(140, poll)

    def _takeover_vps_agent(self):
        if bool(getattr(self, "_vps_takeover_running", False)):
            QMessageBox.information(self, "请稍候", "agant 接管任务正在执行，请等待完成。")
            return
        payload, error = self._resolve_vps_runtime_connection_payload()
        if error:
            QMessageBox.warning(self, "无法接管", error)
            return
        takeover_cfg = self._collect_vps_takeover_form_data()
        validation_error = self._vps_takeover_validation_error(takeover_cfg)
        if validation_error:
            QMessageBox.warning(self, "无法接管", validation_error)
            return
        current = self._current_vps_profile() or {}
        profile_id = str(current.get("id") or self._current_vps_profile_id() or "").strip()
        target_name = self._vps_profile_display_name(current)
        requested_dir = str(takeover_cfg.get("takeover_agent_dir") or "").strip()
        profile_python = str(current.get("python_cmd") or "python3").strip() or "python3"
        holder = {"ok": False, "message": "", "detail": "", "agent_dir": requested_dir, "python_cmd": profile_python}
        self._persist_current_vps_profile_from_form(validate_pair=False, silent=True)
        self._set_vps_takeover_running(True)
        self._set_status("正在接管 agant…")
        notice = getattr(self, "settings_vps_takeover_notice", None)
        if notice is not None:
            notice.setText(f"正在校验 agant 路径 {requested_dir}，请稍候… 当前目标：{target_name}")

        def worker():
            client = None
            try:
                client, err_msg, detail, missing = self._open_vps_ssh_client(payload, timeout=10)
                if client is None:
                    holder["message"] = "当前环境缺少 paramiko，无法执行 agant 接管。" if missing else (err_msg or "SSH 连接失败。")
                    holder["detail"] = str(detail or "").strip()
                    return
                verify_inner = (
                    "set -e; "
                    f"TARGET={shlex.quote(requested_dir)}; "
                    "[ -d \"$TARGET\" ] || { echo '__MISSING_DIR__'; exit 61; }; "
                    "( [ -f \"$TARGET/agentmain.py\" ] || [ -d \"$TARGET/frontends\" ] ) || { echo '__INVALID_DIR__'; exit 62; }; "
                    "cd \"$TARGET\"; "
                    f"PY_BIN={shlex.quote(profile_python)}; "
                    "if ! command -v \"$PY_BIN\" >/dev/null 2>&1; then "
                    "if command -v python3 >/dev/null 2>&1; then PY_BIN=python3; "
                    "elif command -v python >/dev/null 2>&1; then PY_BIN=python; "
                    "else echo '__NO_PY__'; exit 63; fi; "
                    "fi; "
                    "mkdir -p \"$TARGET/temp/launcher_runtime\" \"$TARGET/temp/launcher_sessions\"; "
                    "REAL_TARGET=$(pwd -P 2>/dev/null || pwd); "
                    "printf '__READY__|%s|%s' \"$REAL_TARGET\" \"$PY_BIN\""
                )
                rc, out, err = self._vps_exec_remote(
                    client,
                    f"sh -lc {shlex.quote(verify_inner)}",
                    timeout=40,
                )
                verify_text = str(out or err or "").strip()
                if rc != 0 or "__READY__|" not in verify_text:
                    if "__MISSING_DIR__" in verify_text:
                        holder["message"] = "agant 路径不存在。"
                        holder["detail"] = requested_dir
                    elif "__INVALID_DIR__" in verify_text:
                        holder["message"] = "该路径不像 agant 根目录。"
                        holder["detail"] = requested_dir
                    elif "__NO_PY__" in verify_text:
                        holder["message"] = "远端未检测到可用 Python。"
                        holder["detail"] = "请确认目标机器至少存在 python3 或 python。"
                    else:
                        holder["message"] = "agant 接管预检失败。"
                        holder["detail"] = verify_text or "请检查路径状态与目录权限。"
                    return
                _prefix, ready_dir, ready_py = verify_text.split("|", 2)
                holder["ok"] = True
                holder["agent_dir"] = str(ready_dir or requested_dir).strip() or requested_dir
                holder["python_cmd"] = str(ready_py or profile_python).strip() or profile_python
                holder["message"] = "已接管 agant。"
                holder["detail"] = f"路径：{holder['agent_dir']}；Python：{holder['python_cmd']}"
            except Exception as e:
                holder["message"] = "agant 接管失败。"
                holder["detail"] = str(e)
            finally:
                try:
                    if client is not None:
                        client.close()
                except Exception:
                    pass

        thread = threading.Thread(target=worker, name="vps-agent-takeover", daemon=True)
        thread.start()

        def poll():
            if thread.is_alive():
                QTimer.singleShot(120, poll)
                return
            self._set_vps_takeover_running(False)
            stale = self._vps_task_result_stale(profile_id)
            msg = self._sanitize_vps_feedback_text(holder.get("message") or "")
            detail = self._sanitize_vps_feedback_text(holder.get("detail") or "")
            if holder.get("ok"):
                rows = self._vps_profiles()
                changed = False
                for idx, item in enumerate(rows):
                    if str(item.get("id") or "").strip() != profile_id:
                        continue
                    updated = dict(item)
                    updated["remote_mode"] = "ssh"
                    updated["python_cmd"] = holder.get("python_cmd") or profile_python
                    updated["remote_dir"] = holder.get("agent_dir") or requested_dir
                    updated["takeover_mode"] = "path"
                    updated["takeover_agent_dir"] = holder.get("agent_dir") or requested_dir
                    updated["takeover_python_cmd"] = holder.get("python_cmd") or profile_python
                    updated["docker_takeover_container"] = ""
                    updated["docker_takeover_agent_dir"] = ""
                    updated["docker_takeover_python_cmd"] = "python3"
                    rows[idx] = self._normalize_vps_profile(updated)
                    changed = True
                    break
                if changed:
                    self._save_vps_profiles(rows, selected_id=profile_id)
                self._update_vps_profile_runtime_summary(kind="takeover", ok=True, message=msg, detail=detail, profile_id=profile_id)
                if notice is not None:
                    notice.setText(msg + (f"\n{detail}" if detail else ""))
                self._set_status("agant 已接管并注册。")
                self._reload_vps_panel()
                sync_remote = getattr(self, "_sync_remote_device_launcher_sessions", None)
                if callable(sync_remote):
                    try:
                        sync_remote(force=True, device_id=profile_id)
                    except Exception:
                        pass
                if not stale:
                    QMessageBox.information(self, "接管完成", msg if not detail else f"{msg}\n\n{detail}")
                return
            self._update_vps_profile_runtime_summary(kind="takeover", ok=False, message=msg, detail=detail, profile_id=profile_id)
            if notice is not None:
                notice.setText(msg + (f"\n{detail}" if detail else ""))
            self._set_status("agant 接管失败。")
            self._reload_vps_panel()
            if not stale:
                QMessageBox.warning(self, "接管失败", msg if not detail else f"{msg}\n\n{detail}")

        QTimer.singleShot(120, poll)

    def _browse_vps_ssh_key(self):
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择 SSH 私钥",
            os.path.expanduser("~"),
            "私钥文件 (*.pem *.key *.ppk id_rsa id_ed25519 *);;所有文件 (*.*)",
        )
        if not selected:
            return
        edit = getattr(self, "settings_vps_key_path_edit", None)
        if edit is not None:
            edit.setText(lz._make_config_relative_path(selected))

    def _save_vps_connection(self, *, silent: bool = False):
        current = self._current_vps_profile()
        if current is None:
            if not silent:
                QMessageBox.information(self, "没有可保存的服务器", "请先新建一台服务器。")
            self._set_status("请先新建服务器。")
            return False
        payload = self._persist_current_vps_profile_from_form(validate_pair=True, silent=silent)
        if payload is False:
            return False
        payload = self._normalize_vps_connection_cfg(payload)
        has_host = bool(payload.get("host"))
        has_user = bool(payload.get("username"))
        key_rel = str(payload.get("ssh_key_path") or "").strip()
        key_abs = lz._resolve_config_path(key_rel) if key_rel else ""
        if key_rel and not os.path.isfile(key_abs):
            self.settings_vps_notice.setText("配置已保存，但 SSH 私钥路径当前无效，请检查文件是否存在。")
            self._set_status("VPS 配置已保存（私钥路径待确认）。")
        elif has_host and has_user:
            self.settings_vps_notice.setText(f"VPS 配置已保存：{payload['username']}@{payload['host']}:{payload['port']}")
            self._set_status("VPS 配置已保存。")
        else:
            self.settings_vps_notice.setText("VPS 配置已保存。")
            self._set_status("VPS 配置已保存。")
        probe = getattr(self, "_request_server_connection_probe", None)
        if callable(probe):
            try:
                probe(force=True)
            except Exception:
                pass
        return True

    def _test_vps_connection(self):
        if bool(getattr(self, "_vps_connect_running", False)) or bool(getattr(self, "_vps_deploy_running", False)):
            QMessageBox.information(self, "请稍候", "VPS 连接测试正在执行，请等待结果。")
            return
        payload, error = self._resolve_vps_runtime_connection_payload()
        if error:
            QMessageBox.warning(self, "无法连接", error)
            return
        current = self._current_vps_profile() or {}
        profile_id = str(current.get("id") or "").strip()
        target_name = self._vps_profile_display_name(current)
        holder = {"ok": False, "message": "", "detail": "", "missing_paramiko": False}
        self._set_vps_connect_running(True)
        self._set_status("正在测试 VPS SSH 连接…")
        self.settings_vps_notice.setText(f"正在连接服务器，请稍候… 当前目标：{target_name}")

        def worker():
            client = None
            try:
                client, err_msg, detail, missing = self._open_vps_ssh_client(payload, timeout=8)
                if client is None:
                    holder["ok"] = False
                    holder["missing_paramiko"] = bool(missing)
                    holder["message"] = "当前环境缺少 paramiko，无法执行 SSH 连接测试。" if missing else (err_msg or "连接失败。")
                    holder["detail"] = str(detail or "").strip()
                    return
                transport = client.get_transport()
                if transport is None or not transport.is_active():
                    raise RuntimeError("SSH 连接已建立但传输层未激活。")
                host = str(payload.get("host") or "").strip()
                username = str(payload.get("username") or "").strip()
                port = int(payload.get("port") or 22)
                key_abs = str(payload.get("key_abs") or "").strip()
                password = str(payload.get("password") or "").strip()
                auth_mode = "私钥 + 密码" if (key_abs and password) else ("私钥" if key_abs else "密码")
                holder["ok"] = True
                holder["message"] = f"连接成功：{username}@{host}:{port}（{auth_mode}）"
            except Exception as e:
                host = str(payload.get("host") or "").strip()
                username = str(payload.get("username") or "").strip()
                port = int(payload.get("port") or 22)
                holder["ok"] = False
                holder["message"] = f"连接失败：{username}@{host}:{port}"
                holder["detail"] = str(e)
            finally:
                try:
                    if client is not None:
                        client.close()
                except Exception:
                    pass

        thread = threading.Thread(target=worker, name="vps-connect-test", daemon=True)
        thread.start()

        def poll():
            if thread.is_alive():
                QTimer.singleShot(120, poll)
                return
            self._set_vps_connect_running(False)
            stale = self._vps_task_result_stale(profile_id)
            if holder.get("ok"):
                msg = str(holder.get("message") or "连接成功。")
                self._update_vps_profile_runtime_summary(kind="test", ok=True, message=msg, detail="")
                self.settings_vps_notice.setText(msg)
                self._set_status(msg)
                self._reload_vps_panel()
                probe = getattr(self, "_request_server_connection_probe", None)
                if callable(probe):
                    try:
                        probe(force=True)
                    except Exception:
                        pass
                if not stale:
                    QMessageBox.information(self, "VPS 连接测试", msg)
                return
            detail = str(holder.get("detail") or "").strip()
            msg = str(holder.get("message") or "连接失败。")
            if bool(holder.get("missing_paramiko")):
                detail = detail or "未安装 paramiko"
                msg = msg + "\n\n请在 VPS 管理页点击“安装 SSH 依赖”后重试。"
            self._update_vps_profile_runtime_summary(kind="test", ok=False, message=msg, detail=detail)
            self.settings_vps_notice.setText(msg)
            self._set_status("VPS 连接测试失败。")
            self._reload_vps_panel()
            probe = getattr(self, "_request_server_connection_probe", None)
            if callable(probe):
                try:
                    probe(force=True)
                except Exception:
                    pass
            if not stale:
                QMessageBox.warning(self, "VPS 连接测试失败", msg if not detail else f"{msg}\n\n{detail}")

        QTimer.singleShot(120, poll)

    def _on_theme_fade_changed(self, value):
        label = getattr(self, "settings_theme_fade_value", None)
        if label is not None:
            label.setText(str(max(0, min(100, int(value or 0)))))

    def _on_theme_floating_fade_changed(self, value):
        label = getattr(self, "settings_theme_floating_fade_value", None)
        if label is not None:
            label.setText(str(max(0, min(100, int(value or 0)))))

    def _theme_target_size(self) -> QSize:
        screen = None
        screen_getter = getattr(self, "screen", None)
        if callable(screen_getter):
            try:
                screen = screen_getter()
            except Exception:
                screen = None
        if screen is None:
            app = QApplication.instance()
            if app is not None:
                try:
                    screen = app.primaryScreen()
                except Exception:
                    screen = None
        if screen is not None:
            try:
                rect = screen.availableGeometry()
            except Exception:
                rect = None
            if rect is not None:
                try:
                    sw = int(rect.width() or 0)
                    sh = int(rect.height() or 0)
                except Exception:
                    sw, sh = 0, 0
                if sw > 0 and sh > 0:
                    return QSize(max(960, sw), max(640, sh))
        width = max(960, int(getattr(self, "width", lambda: 1440)() or 1440))
        height = max(640, int(getattr(self, "height", lambda: 920)() or 920))
        return QSize(width, height)

    def _theme_floating_target_size(self) -> QSize:
        floating = getattr(self, "_floating_chat_window", None)
        if floating is not None:
            expanded = getattr(floating, "_expanded_size", None)
            if isinstance(expanded, QSize):
                try:
                    ew = int(expanded.width() or 0)
                    eh = int(expanded.height() or 0)
                except Exception:
                    ew, eh = 0, 0
                if ew > 0 and eh > 0:
                    return QSize(max(360, ew), max(460, eh))
            try:
                fw = int(floating.width() or 0)
                fh = int(floating.height() or 0)
            except Exception:
                fw, fh = 0, 0
            if fw > 0 and fh > 0:
                return QSize(max(360, fw), max(460, fh))
        return QSize(480, 760)

    def _theme_avatar_target_size(self) -> QSize:
        return QSize(256, 256)

    def _theme_avatar_meta(self, role: str) -> dict[str, str]:
        role_key = "user" if str(role or "").strip().lower() == "user" else "assistant"
        if role_key == "user":
            return {
                "role": "user",
                "label": "我的头像",
                "cfg_prefix": "theme_user_avatar",
                "path_attr": "settings_theme_user_avatar_path",
                "source_attr": "_theme_user_avatar_source_selected_path",
                "crop_attr": "_theme_user_avatar_crop_selected",
                "clear_attr": "_theme_user_avatar_force_clear",
                "asset_tag": "user_avatar",
            }
        return {
            "role": "assistant",
            "label": "AI 头像",
            "cfg_prefix": "theme_ai_avatar",
            "path_attr": "settings_theme_ai_avatar_path",
            "source_attr": "_theme_ai_avatar_source_selected_path",
            "crop_attr": "_theme_ai_avatar_crop_selected",
            "clear_attr": "_theme_ai_avatar_force_clear",
            "asset_tag": "ai_avatar",
        }

    def _refresh_theme_background_assets_for_mode(self) -> bool:
        cfg = self.cfg if isinstance(getattr(self, "cfg", None), dict) else None
        if cfg is None:
            return False

        mode_normalizer = getattr(self, "_normalize_appearance_mode", None)
        if callable(mode_normalizer):
            mode = mode_normalizer(cfg.get("appearance_mode", "light"))
        else:
            mode = "light" if str(cfg.get("appearance_mode", "light") or "").strip().lower() == "light" else "dark"
        changed = False

        render_schema = "v2"

        def _signature(source_rel: str, crop_data, fade_value: int, target_size: QSize) -> str:
            crop = self._normalize_theme_crop_data(crop_data) or {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
            return (
                f"{render_schema}|{mode}|{source_rel}|{int(fade_value)}|"
                f"{int(target_size.width())}x{int(target_size.height())}|"
                f"{crop['x']:.6f},{crop['y']:.6f},{crop['w']:.6f},{crop['h']:.6f}"
            )

        def _regen(
            *,
            preset_key: str,
            source_key: str,
            image_key: str,
            crop_key: str,
            fade_key: str,
            blur_key: str,
            sig_key: str,
            asset_tag: str,
            target_size: QSize,
            enforce_launcher_min: bool = True,
        ):
            nonlocal changed
            preset = str(cfg.get(preset_key) or "").strip().lower()
            if preset != "image":
                return
            source_rel = str(cfg.get(source_key) or cfg.get(image_key) or "").strip()
            source_abs = lz._resolve_config_path(source_rel) if source_rel else ""
            if not source_abs or not os.path.isfile(source_abs):
                return
            fade_value = max(0, min(100, int(cfg.get(fade_key, cfg.get(blur_key, 18)) or 18)))
            crop_data = cfg.get(crop_key)
            sig = _signature(source_rel, crop_data, fade_value, target_size)
            current_sig = str(cfg.get(sig_key) or "")
            current_rel = str(cfg.get(image_key) or "").strip()
            current_abs = lz._resolve_config_path(current_rel) if current_rel else ""
            if current_sig == sig and current_abs and os.path.isfile(current_abs):
                return
            generated_abs = self._render_theme_background_asset(
                source_abs,
                crop_data,
                target_size,
                fade_value,
                asset_tag=asset_tag,
                enforce_launcher_min=enforce_launcher_min,
            )
            cfg[image_key] = lz._make_config_relative_path(generated_abs)
            cfg[sig_key] = sig
            changed = True

        avatar_render_schema = "v1"

        def _avatar_signature(source_rel: str, crop_data) -> str:
            crop = self._normalize_theme_crop_data(crop_data) or {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
            return (
                f"{avatar_render_schema}|{source_rel}|"
                f"{crop['x']:.6f},{crop['y']:.6f},{crop['w']:.6f},{crop['h']:.6f}"
            )

        def _regen_avatar(role: str):
            nonlocal changed
            meta = self._theme_avatar_meta(role)
            prefix = str(meta["cfg_prefix"])
            source_key = f"{prefix}_source"
            image_key = f"{prefix}_image"
            crop_key = f"{prefix}_crop"
            sig_key = f"{prefix}_render_sig"
            source_rel = str(cfg.get(source_key) or cfg.get(image_key) or "").strip()
            source_abs = lz._resolve_config_path(source_rel) if source_rel else ""
            if not source_abs or not os.path.isfile(source_abs):
                return
            crop_data = cfg.get(crop_key)
            sig = _avatar_signature(source_rel, crop_data)
            current_sig = str(cfg.get(sig_key) or "")
            current_rel = str(cfg.get(image_key) or "").strip()
            current_abs = lz._resolve_config_path(current_rel) if current_rel else ""
            if current_sig == sig and current_abs and os.path.isfile(current_abs):
                return
            generated_abs = self._render_theme_avatar_asset(
                source_abs,
                crop_data,
                asset_tag=str(meta["asset_tag"] or "chat_avatar"),
            )
            cfg[image_key] = lz._make_config_relative_path(generated_abs)
            cfg[sig_key] = sig
            changed = True

        try:
            _regen(
                preset_key="theme_bg_preset",
                source_key="theme_bg_source",
                image_key="theme_bg_image",
                crop_key="theme_bg_crop",
                fade_key="theme_bg_fade",
                blur_key="theme_bg_blur",
                sig_key="theme_bg_render_sig",
                asset_tag="launcher_bg",
                target_size=self._theme_target_size(),
                enforce_launcher_min=True,
            )
            _regen(
                preset_key="theme_floating_bg_preset",
                source_key="theme_floating_bg_source",
                image_key="theme_floating_bg_image",
                crop_key="theme_floating_bg_crop",
                fade_key="theme_floating_bg_fade",
                blur_key="theme_floating_bg_blur",
                sig_key="theme_floating_bg_render_sig",
                asset_tag="floating_bg",
                target_size=self._theme_floating_target_size(),
                enforce_launcher_min=False,
            )
            _regen_avatar("user")
            _regen_avatar("assistant")
        except Exception:
            return False
        if changed:
            lz.save_config(cfg)
        return changed

    def _normalize_theme_crop_data(self, data):
        if not isinstance(data, dict):
            return None
        try:
            x = float(data.get("x", 0.0))
            y = float(data.get("y", 0.0))
            w = float(data.get("w", 1.0))
            h = float(data.get("h", 1.0))
        except Exception:
            return None
        x = max(0.0, min(1.0, x))
        y = max(0.0, min(1.0, y))
        w = max(0.001, min(1.0, w))
        h = max(0.001, min(1.0, h))
        if x + w > 1.0:
            w = 1.0 - x
        if y + h > 1.0:
            h = 1.0 - y
        if w <= 0.0 or h <= 0.0:
            return None
        return {"x": x, "y": y, "w": w, "h": h}

    def _theme_apply_fade(self, image: QImage, fade_value: int) -> QImage:
        amount = max(0, min(100, int(fade_value or 0)))
        if amount <= 0 or image.isNull():
            return image
        mode = str(self.cfg.get("appearance_mode", "light") or "").strip().lower()
        base_color = QColor("#1c1e22" if mode == "dark" else "#f3f5f9")
        overlay_alpha = int(220 * (float(amount) / 100.0))
        src = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        out = QImage(src.size(), QImage.Format_ARGB32_Premultiplied)
        out.fill(Qt.transparent)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawImage(0, 0, src)
        overlay = QColor(base_color)
        overlay.setAlpha(max(0, min(255, overlay_alpha)))
        painter.fillRect(out.rect(), overlay)
        painter.end()
        return out.convertToFormat(QImage.Format_RGB32)

    def _render_theme_background_asset(
        self,
        source_path: str,
        crop_data,
        target_size: QSize,
        fade_value: int,
        *,
        asset_tag: str = "launcher_bg",
        enforce_launcher_min: bool = True,
    ) -> str:
        src = str(source_path or "").strip()
        if not src or not os.path.isfile(src):
            raise ValueError("背景源图不存在，请重新选择。")
        image = QImage(src)
        if image.isNull():
            raise ValueError("无法读取背景图片，请更换文件格式后重试。")
        crop = self._normalize_theme_crop_data(crop_data) or {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        sw = int(image.width())
        sh = int(image.height())
        x = int(round(crop["x"] * sw))
        y = int(round(crop["y"] * sh))
        w = int(round(crop["w"] * sw))
        h = int(round(crop["h"] * sh))
        x = max(0, min(sw - 1, x))
        y = max(0, min(sh - 1, y))
        w = max(1, min(sw - x, w))
        h = max(1, min(sh - y, h))
        cropped = image.copy(x, y, w, h)
        if enforce_launcher_min:
            out_size = QSize(max(960, int(target_size.width())), max(640, int(target_size.height())))
        else:
            out_size = QSize(max(1, int(target_size.width())), max(1, int(target_size.height())))
        rendered = cropped.scaled(out_size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        if rendered.width() != out_size.width() or rendered.height() != out_size.height():
            ox = max(0, int((rendered.width() - out_size.width()) / 2))
            oy = max(0, int((rendered.height() - out_size.height()) / 2))
            rendered = rendered.copy(ox, oy, int(out_size.width()), int(out_size.height()))
        rendered = self._theme_apply_fade(rendered, fade_value)
        out_dir = lz.launcher_data_path("theme_background")
        os.makedirs(out_dir, exist_ok=True)
        safe_tag = "".join(ch for ch in str(asset_tag or "launcher_bg") if ch.isalnum() or ch in ("_", "-")).strip("_-")
        if not safe_tag:
            safe_tag = "launcher_bg"
        out_path = os.path.join(out_dir, f"{safe_tag}_{int(time.time() * 1000)}.png")
        if not rendered.save(out_path, "PNG"):
            raise ValueError("背景图片写入失败，请检查目录权限。")
        return out_path

    def _render_theme_avatar_asset(
        self,
        source_path: str,
        crop_data,
        *,
        asset_tag: str = "chat_avatar",
    ) -> str:
        src = str(source_path or "").strip()
        if not src or not os.path.isfile(src):
            raise ValueError("头像源图不存在，请重新选择。")
        image = QImage(src)
        if image.isNull():
            raise ValueError("无法读取头像图片，请更换文件格式后重试。")
        crop = self._normalize_theme_crop_data(crop_data) or {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        sw = int(image.width())
        sh = int(image.height())
        x = int(round(crop["x"] * sw))
        y = int(round(crop["y"] * sh))
        w = int(round(crop["w"] * sw))
        h = int(round(crop["h"] * sh))
        x = max(0, min(sw - 1, x))
        y = max(0, min(sh - 1, y))
        w = max(1, min(sw - x, w))
        h = max(1, min(sh - y, h))
        cropped = image.copy(x, y, w, h)
        out_size = self._theme_avatar_target_size()
        rendered = cropped.scaled(out_size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        if rendered.width() != out_size.width() or rendered.height() != out_size.height():
            ox = max(0, int((rendered.width() - out_size.width()) / 2))
            oy = max(0, int((rendered.height() - out_size.height()) / 2))
            rendered = rendered.copy(ox, oy, int(out_size.width()), int(out_size.height()))
        out_dir = lz.launcher_data_path("theme_avatar")
        os.makedirs(out_dir, exist_ok=True)
        safe_tag = "".join(ch for ch in str(asset_tag or "chat_avatar") if ch.isalnum() or ch in ("_", "-")).strip("_-")
        if not safe_tag:
            safe_tag = "chat_avatar"
        out_path = os.path.join(out_dir, f"{safe_tag}_{int(time.time() * 1000)}.png")
        if not rendered.save(out_path, "PNG"):
            raise ValueError("头像图片写入失败，请检查目录权限。")
        return out_path

    def _ensure_theme_font_options(self):
        combo = getattr(self, "settings_theme_font_combo", None)
        if combo is None:
            return
        if bool(getattr(self, "_theme_font_options_loaded", False)) and combo.count() > 0:
            return
        families = []
        try:
            db = QFontDatabase()
            families = sorted({str(name).strip() for name in db.families() if str(name).strip()}, key=lambda x: x.lower())
        except Exception:
            families = []
        preferred = preferred_theme_font_families()
        merged = []
        seen = set()
        for name in preferred + families:
            key = str(name or "").strip()
            if not key:
                continue
            low = key.lower()
            if low in seen:
                continue
            seen.add(low)
            merged.append(key)
        self._dismiss_combo_popup(combo)
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("跟随默认", "")
        for name in merged:
            combo.addItem(name, name)
        combo.blockSignals(False)
        self._theme_font_options_loaded = True

    def _reload_theme_panel(self):
        if not hasattr(self, "settings_theme_notice"):
            return
        self._ensure_theme_font_options()
        font_combo = getattr(self, "settings_theme_font_combo", None)
        weight_combo = getattr(self, "settings_theme_weight_combo", None)
        size_combo = getattr(self, "settings_theme_size_combo", None)
        visual_combo = getattr(self, "settings_theme_visual_combo", None)
        bg_combo = getattr(self, "settings_theme_bg_combo", None)
        mode_combo = getattr(self, "settings_theme_bg_mode_combo", None)
        fade_slider = getattr(self, "settings_theme_fade_slider", None)
        floating_bg_combo = getattr(self, "settings_theme_floating_bg_combo", None)
        floating_mode_combo = getattr(self, "settings_theme_floating_bg_mode_combo", None)
        floating_fade_slider = getattr(self, "settings_theme_floating_fade_slider", None)
        auto_jump_checkbox = getattr(self, "settings_theme_auto_jump_latest", None)
        for combo in (font_combo, weight_combo, size_combo, visual_combo, bg_combo, mode_combo, floating_bg_combo, floating_mode_combo):
            self._apply_theme_combo_style(combo)
        path_edit = getattr(self, "settings_theme_bg_image_path", None)
        floating_path_edit = getattr(self, "settings_theme_floating_bg_image_path", None)
        user_avatar_edit = getattr(self, "settings_theme_user_avatar_path", None)
        ai_avatar_edit = getattr(self, "settings_theme_ai_avatar_path", None)
        if auto_jump_checkbox is not None:
            auto_jump_checkbox.setChecked(bool(self.cfg.get("theme_chat_auto_jump_latest", True)))
        current_font = str(self.cfg.get("theme_font_family") or "").strip()
        self._select_combo_data(font_combo, current_font, default_index=0)
        self._select_combo_data(weight_combo, str(self.cfg.get("theme_font_weight") or "400"), default_index=0)
        self._select_combo_data(size_combo, str(self.cfg.get("theme_font_size") or "14"), default_index=3)
        self._select_combo_data(visual_combo, resolve_theme_visual_preset(self.cfg), default_index=0)
        self._select_combo_data(bg_combo, normalize_theme_background_mode(self.cfg.get("theme_bg_preset")), default_index=0)
        self._select_combo_data(mode_combo, str(self.cfg.get("theme_bg_image_mode") or "center"), default_index=0)
        self._select_combo_data(floating_bg_combo, str(self.cfg.get("theme_floating_bg_preset") or "follow"), default_index=0)
        self._select_combo_data(
            floating_mode_combo,
            str(self.cfg.get("theme_floating_bg_image_mode") or "center"),
            default_index=0,
        )
        fade_value = max(0, min(100, int(self.cfg.get("theme_bg_fade", self.cfg.get("theme_bg_blur", 18)) or 18)))
        if fade_slider is not None:
            fade_slider.blockSignals(True)
            fade_slider.setValue(fade_value)
            fade_slider.blockSignals(False)
        self._on_theme_fade_changed(fade_value)
        floating_fade_value = max(
            0,
            min(
                100,
                int(
                    self.cfg.get(
                        "theme_floating_bg_fade",
                        self.cfg.get("theme_floating_bg_blur", self.cfg.get("theme_bg_fade", self.cfg.get("theme_bg_blur", 18))),
                    )
                    or 18
                ),
            ),
        )
        if floating_fade_slider is not None:
            floating_fade_slider.blockSignals(True)
            floating_fade_slider.setValue(floating_fade_value)
            floating_fade_slider.blockSignals(False)
        self._on_theme_floating_fade_changed(floating_fade_value)
        source_rel = str(self.cfg.get("theme_bg_source") or self.cfg.get("theme_bg_image") or "").strip()
        source_abs = lz._resolve_config_path(source_rel) if source_rel else ""
        crop_data = self._normalize_theme_crop_data(self.cfg.get("theme_bg_crop")) or {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        self._theme_bg_source_selected_path = source_abs if os.path.isfile(source_abs) else ""
        self._theme_bg_crop_selected = crop_data
        self._theme_bg_force_clear = False
        if path_edit is not None:
            path_edit.setText(self._theme_bg_source_selected_path)
        floating_source_rel = str(self.cfg.get("theme_floating_bg_source") or self.cfg.get("theme_floating_bg_image") or "").strip()
        floating_source_abs = lz._resolve_config_path(floating_source_rel) if floating_source_rel else ""
        floating_crop_data = self._normalize_theme_crop_data(self.cfg.get("theme_floating_bg_crop")) or {
            "x": 0.0,
            "y": 0.0,
            "w": 1.0,
            "h": 1.0,
        }
        self._theme_floating_bg_source_selected_path = floating_source_abs if os.path.isfile(floating_source_abs) else ""
        self._theme_floating_bg_crop_selected = floating_crop_data
        self._theme_floating_bg_force_clear = False
        if floating_path_edit is not None:
            floating_path_edit.setText(self._theme_floating_bg_source_selected_path)
        user_avatar_source_rel = str(self.cfg.get("theme_user_avatar_source") or self.cfg.get("theme_user_avatar_image") or "").strip()
        user_avatar_source_abs = lz._resolve_config_path(user_avatar_source_rel) if user_avatar_source_rel else ""
        user_avatar_crop_data = self._normalize_theme_crop_data(self.cfg.get("theme_user_avatar_crop")) or {
            "x": 0.0,
            "y": 0.0,
            "w": 1.0,
            "h": 1.0,
        }
        self._theme_user_avatar_source_selected_path = user_avatar_source_abs if os.path.isfile(user_avatar_source_abs) else ""
        self._theme_user_avatar_crop_selected = user_avatar_crop_data
        self._theme_user_avatar_force_clear = False
        if user_avatar_edit is not None:
            user_avatar_edit.setText(self._theme_user_avatar_source_selected_path)
        ai_avatar_source_rel = str(self.cfg.get("theme_ai_avatar_source") or self.cfg.get("theme_ai_avatar_image") or "").strip()
        ai_avatar_source_abs = lz._resolve_config_path(ai_avatar_source_rel) if ai_avatar_source_rel else ""
        ai_avatar_crop_data = self._normalize_theme_crop_data(self.cfg.get("theme_ai_avatar_crop")) or {
            "x": 0.0,
            "y": 0.0,
            "w": 1.0,
            "h": 1.0,
        }
        self._theme_ai_avatar_source_selected_path = ai_avatar_source_abs if os.path.isfile(ai_avatar_source_abs) else ""
        self._theme_ai_avatar_crop_selected = ai_avatar_crop_data
        self._theme_ai_avatar_force_clear = False
        if ai_avatar_edit is not None:
            ai_avatar_edit.setText(self._theme_ai_avatar_source_selected_path)
        bg_mode = normalize_theme_background_mode(self.cfg.get("theme_bg_preset"))
        visual_preset = resolve_theme_visual_preset(self.cfg)
        visual_label = ""
        if visual_combo is not None:
            idx = visual_combo.findData(visual_preset)
            if idx >= 0:
                visual_label = str(visual_combo.itemText(idx) or "").strip()
        if bg_mode == "image" and not os.path.isfile(source_abs):
            self.settings_theme_notice.setText("当前背景模式是图片背景，但还没有有效图片文件；界面会回退到主体预设默认底色。")
        elif bg_mode == "image":
            self.settings_theme_notice.setText("当前图片背景已配置裁切。主体预设仍会控制侧边栏、卡片、按钮和文本层级。")
        elif str(self.cfg.get("theme_floating_bg_preset") or "follow").strip().lower() == "image" and not os.path.isfile(floating_source_abs):
            self.settings_theme_notice.setText("悬浮窗预设是图片背景，但还没有有效图片文件；当前会跟随主背景。")
        elif str(self.cfg.get("theme_floating_bg_preset") or "follow").strip().lower() == "image":
            self.settings_theme_notice.setText("悬浮窗图片背景已配置裁切。点击“保存主题设置”后生效。")
        else:
            preset_text = f"当前主体预设：{visual_label}。" if visual_label else ""
            self.settings_theme_notice.setText(preset_text + "修改后点击“保存主题设置”即可立即应用。")

    def _choose_theme_chat_avatar(self, role: str):
        meta = self._theme_avatar_meta(role)
        current = str(getattr(self, meta["source_attr"], "") or "").strip()
        start_dir = os.path.dirname(current) if current else os.path.expanduser("~")
        selected, _ = QFileDialog.getOpenFileName(
            self,
            f"选择{meta['label']}",
            start_dir,
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp *.gif);;全部文件 (*.*)",
        )
        if not selected:
            return
        resolved = os.path.abspath(selected)
        try:
            dialog = _ThemeCropDialog(resolved, self._theme_avatar_target_size(), self)
        except Exception as e:
            QMessageBox.warning(self, "无法加载图片", str(e))
            return
        if dialog.exec() != QDialog.Accepted:
            return
        crop_data = self._normalize_theme_crop_data(dialog.crop_norm()) or {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        setattr(self, meta["source_attr"], resolved)
        setattr(self, meta["crop_attr"], crop_data)
        setattr(self, meta["clear_attr"], False)
        edit = getattr(self, meta["path_attr"], None)
        if edit is not None:
            edit.setText(resolved)
        notice = getattr(self, "settings_theme_notice", None)
        if notice is not None:
            notice.setText(f"已完成{meta['label']}裁切。点击“保存主题设置”后会立即刷新现有聊天头像。")

    def _clear_theme_chat_avatar(self, role: str):
        meta = self._theme_avatar_meta(role)
        setattr(self, meta["source_attr"], "")
        setattr(self, meta["crop_attr"], {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0})
        setattr(self, meta["clear_attr"], True)
        edit = getattr(self, meta["path_attr"], None)
        if edit is not None:
            edit.clear()
        notice = getattr(self, "settings_theme_notice", None)
        if notice is not None:
            notice.setText(f"{meta['label']}已清空；保存后会恢复默认极简头像。")

    def _choose_theme_background_image(self):
        current = str(getattr(self, "_theme_bg_source_selected_path", "") or "").strip()
        start_dir = os.path.dirname(current) if current else os.path.expanduser("~")
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择背景图片",
            start_dir,
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp *.gif);;全部文件 (*.*)",
        )
        if not selected:
            return
        resolved = os.path.abspath(selected)
        try:
            dialog = _ThemeCropDialog(resolved, self._theme_target_size(), self)
        except Exception as e:
            QMessageBox.warning(self, "无法加载图片", str(e))
            return
        if dialog.exec() != QDialog.Accepted:
            return
        crop_data = self._normalize_theme_crop_data(dialog.crop_norm()) or {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        self._theme_bg_source_selected_path = resolved
        self._theme_bg_crop_selected = crop_data
        self._theme_bg_force_clear = False
        edit = getattr(self, "settings_theme_bg_image_path", None)
        if edit is not None:
            edit.setText(resolved)
        bg_combo = getattr(self, "settings_theme_bg_combo", None)
        if bg_combo is not None:
            idx = bg_combo.findData("image")
            if idx >= 0:
                bg_combo.setCurrentIndex(idx)
        notice = getattr(self, "settings_theme_notice", None)
        if notice is not None:
            notice.setText("已完成裁切，并自动切换到“图片背景”模式；点击“保存主题设置”后生效。")

    def _clear_theme_background_image(self):
        self._theme_bg_source_selected_path = ""
        self._theme_bg_crop_selected = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        self._theme_bg_force_clear = True
        edit = getattr(self, "settings_theme_bg_image_path", None)
        if edit is not None:
            edit.clear()
        notice = getattr(self, "settings_theme_notice", None)
        if notice is not None:
            notice.setText("背景图片已清空；若保持“图片背景”模式，界面会回退到主体预设底色。")

    def _choose_theme_floating_background_image(self):
        current = str(getattr(self, "_theme_floating_bg_source_selected_path", "") or "").strip()
        start_dir = os.path.dirname(current) if current else os.path.expanduser("~")
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择悬浮窗背景图片",
            start_dir,
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp *.gif);;全部文件 (*.*)",
        )
        if not selected:
            return
        resolved = os.path.abspath(selected)
        try:
            dialog = _ThemeCropDialog(resolved, self._theme_floating_target_size(), self)
        except Exception as e:
            QMessageBox.warning(self, "无法加载图片", str(e))
            return
        if dialog.exec() != QDialog.Accepted:
            return
        crop_data = self._normalize_theme_crop_data(dialog.crop_norm()) or {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        self._theme_floating_bg_source_selected_path = resolved
        self._theme_floating_bg_crop_selected = crop_data
        self._theme_floating_bg_force_clear = False
        edit = getattr(self, "settings_theme_floating_bg_image_path", None)
        if edit is not None:
            edit.setText(resolved)
        combo = getattr(self, "settings_theme_floating_bg_combo", None)
        if combo is not None:
            idx = combo.findData("image")
            if idx >= 0:
                combo.setCurrentIndex(idx)
        notice = getattr(self, "settings_theme_notice", None)
        if notice is not None:
            notice.setText("已完成悬浮窗背景裁切，并自动切到“图片背景”；点击“保存主题设置”后生效。")

    def _clear_theme_floating_background_image(self):
        self._theme_floating_bg_source_selected_path = ""
        self._theme_floating_bg_crop_selected = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        self._theme_floating_bg_force_clear = True
        edit = getattr(self, "settings_theme_floating_bg_image_path", None)
        if edit is not None:
            edit.clear()
        notice = getattr(self, "settings_theme_notice", None)
        if notice is not None:
            notice.setText("悬浮窗背景图片已清空，点击“保存主题设置”后生效。")

    def _save_theme_preferences(self):
        font_combo = getattr(self, "settings_theme_font_combo", None)
        weight_combo = getattr(self, "settings_theme_weight_combo", None)
        size_combo = getattr(self, "settings_theme_size_combo", None)
        visual_combo = getattr(self, "settings_theme_visual_combo", None)
        bg_combo = getattr(self, "settings_theme_bg_combo", None)
        mode_combo = getattr(self, "settings_theme_bg_mode_combo", None)
        fade_slider = getattr(self, "settings_theme_fade_slider", None)
        floating_bg_combo = getattr(self, "settings_theme_floating_bg_combo", None)
        floating_mode_combo = getattr(self, "settings_theme_floating_bg_mode_combo", None)
        floating_fade_slider = getattr(self, "settings_theme_floating_fade_slider", None)
        auto_jump_checkbox = getattr(self, "settings_theme_auto_jump_latest", None)
        font_family = str(font_combo.itemData(font_combo.currentIndex()) or "").strip() if font_combo is not None else ""
        font_weight = str(weight_combo.itemData(weight_combo.currentIndex()) or "400").strip() if weight_combo is not None else "400"
        font_size = str(size_combo.itemData(size_combo.currentIndex()) or "14").strip() if size_combo is not None else "14"
        visual_preset = str(visual_combo.itemData(visual_combo.currentIndex()) or "graphite").strip() if visual_combo is not None else "graphite"
        bg_preset = normalize_theme_background_mode(bg_combo.itemData(bg_combo.currentIndex()) if bg_combo is not None else "default")
        bg_mode = str(mode_combo.itemData(mode_combo.currentIndex()) or "center").strip() if mode_combo is not None else "center"
        floating_bg_preset = str(floating_bg_combo.itemData(floating_bg_combo.currentIndex()) or "follow").strip() if floating_bg_combo is not None else "follow"
        floating_bg_mode = str(floating_mode_combo.itemData(floating_mode_combo.currentIndex()) or "center").strip() if floating_mode_combo is not None else "center"
        auto_jump_latest = bool(auto_jump_checkbox.isChecked()) if auto_jump_checkbox is not None else bool(self.cfg.get("theme_chat_auto_jump_latest", True))
        fade_value = max(0, min(100, int(fade_slider.value() if fade_slider is not None else self.cfg.get("theme_bg_fade", self.cfg.get("theme_bg_blur", 18)) or 18)))
        floating_fade_value = max(
            0,
            min(
                100,
                int(
                    floating_fade_slider.value()
                    if floating_fade_slider is not None
                    else self.cfg.get(
                        "theme_floating_bg_fade",
                        self.cfg.get("theme_floating_bg_blur", self.cfg.get("theme_bg_fade", self.cfg.get("theme_bg_blur", 18))),
                    )
                    or 18
                ),
            ),
        )
        bg_force_clear = bool(getattr(self, "_theme_bg_force_clear", False))
        source_path = ""
        if not bg_force_clear:
            source_path = str(getattr(self, "_theme_bg_source_selected_path", "") or "").strip()
        if (not bg_force_clear) and (not source_path):
            source_rel_cfg = str(self.cfg.get("theme_bg_source") or self.cfg.get("theme_bg_image") or "").strip()
            source_path = lz._resolve_config_path(source_rel_cfg) if source_rel_cfg else ""
        crop_data = self._normalize_theme_crop_data(getattr(self, "_theme_bg_crop_selected", None))
        if crop_data is None:
            crop_data = self._normalize_theme_crop_data(self.cfg.get("theme_bg_crop"))
        if crop_data is None:
            crop_data = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        floating_bg_force_clear = bool(getattr(self, "_theme_floating_bg_force_clear", False))
        floating_source_path = ""
        if not floating_bg_force_clear:
            floating_source_path = str(getattr(self, "_theme_floating_bg_source_selected_path", "") or "").strip()
        if (not floating_bg_force_clear) and (not floating_source_path):
            floating_source_rel_cfg = str(self.cfg.get("theme_floating_bg_source") or self.cfg.get("theme_floating_bg_image") or "").strip()
            floating_source_path = lz._resolve_config_path(floating_source_rel_cfg) if floating_source_rel_cfg else ""
        floating_crop_data = self._normalize_theme_crop_data(getattr(self, "_theme_floating_bg_crop_selected", None))
        if floating_crop_data is None:
            floating_crop_data = self._normalize_theme_crop_data(self.cfg.get("theme_floating_bg_crop"))
        if floating_crop_data is None:
            floating_crop_data = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        user_avatar_force_clear = bool(getattr(self, "_theme_user_avatar_force_clear", False))
        user_avatar_source_path = ""
        if not user_avatar_force_clear:
            user_avatar_source_path = str(getattr(self, "_theme_user_avatar_source_selected_path", "") or "").strip()
            if not user_avatar_source_path:
                user_avatar_source_rel_cfg = str(
                    self.cfg.get("theme_user_avatar_source") or self.cfg.get("theme_user_avatar_image") or ""
                ).strip()
                user_avatar_source_path = lz._resolve_config_path(user_avatar_source_rel_cfg) if user_avatar_source_rel_cfg else ""
        user_avatar_crop_data = self._normalize_theme_crop_data(getattr(self, "_theme_user_avatar_crop_selected", None))
        if user_avatar_crop_data is None:
            user_avatar_crop_data = self._normalize_theme_crop_data(self.cfg.get("theme_user_avatar_crop"))
        if user_avatar_crop_data is None:
            user_avatar_crop_data = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        ai_avatar_force_clear = bool(getattr(self, "_theme_ai_avatar_force_clear", False))
        ai_avatar_source_path = ""
        if not ai_avatar_force_clear:
            ai_avatar_source_path = str(getattr(self, "_theme_ai_avatar_source_selected_path", "") or "").strip()
            if not ai_avatar_source_path:
                ai_avatar_source_rel_cfg = str(
                    self.cfg.get("theme_ai_avatar_source") or self.cfg.get("theme_ai_avatar_image") or ""
                ).strip()
                ai_avatar_source_path = lz._resolve_config_path(ai_avatar_source_rel_cfg) if ai_avatar_source_rel_cfg else ""
        ai_avatar_crop_data = self._normalize_theme_crop_data(getattr(self, "_theme_ai_avatar_crop_selected", None))
        if ai_avatar_crop_data is None:
            ai_avatar_crop_data = self._normalize_theme_crop_data(self.cfg.get("theme_ai_avatar_crop"))
        if ai_avatar_crop_data is None:
            ai_avatar_crop_data = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}

        generated_abs = ""
        if bg_preset == "image":
            if not source_path or not os.path.isfile(source_path):
                if not bg_force_clear:
                    QMessageBox.warning(self, "图片无效", "当前未选择可用的背景图片，请先点“选择图片”完成裁切。")
                    return
            try:
                if source_path and os.path.isfile(source_path):
                    generated_abs = self._render_theme_background_asset(
                        source_path,
                        crop_data,
                        self._theme_target_size(),
                        fade_value,
                        asset_tag="launcher_bg",
                        enforce_launcher_min=True,
                    )
            except Exception as e:
                QMessageBox.warning(self, "生成背景失败", str(e))
                return

        floating_generated_abs = ""
        if floating_bg_preset == "image":
            if not floating_source_path or not os.path.isfile(floating_source_path):
                if not floating_bg_force_clear:
                    QMessageBox.warning(self, "图片无效", "当前未选择可用的悬浮窗背景图片，请先点“选择图片”完成裁切。")
                    return
            try:
                if floating_source_path and os.path.isfile(floating_source_path):
                    floating_generated_abs = self._render_theme_background_asset(
                        floating_source_path,
                        floating_crop_data,
                        self._theme_floating_target_size(),
                        floating_fade_value,
                        asset_tag="floating_bg",
                        enforce_launcher_min=False,
                    )
            except Exception as e:
                QMessageBox.warning(self, "生成悬浮窗背景失败", str(e))
                return

        user_avatar_generated_abs = ""
        if user_avatar_source_path and os.path.isfile(user_avatar_source_path):
            try:
                user_avatar_generated_abs = self._render_theme_avatar_asset(
                    user_avatar_source_path,
                    user_avatar_crop_data,
                    asset_tag="user_avatar",
                )
            except Exception as e:
                QMessageBox.warning(self, "生成我的头像失败", str(e))
                return

        ai_avatar_generated_abs = ""
        if ai_avatar_source_path and os.path.isfile(ai_avatar_source_path):
            try:
                ai_avatar_generated_abs = self._render_theme_avatar_asset(
                    ai_avatar_source_path,
                    ai_avatar_crop_data,
                    asset_tag="ai_avatar",
                )
            except Exception as e:
                QMessageBox.warning(self, "生成 AI 头像失败", str(e))
                return

        generated_rel = (
            ""
            if bg_force_clear
            else (lz._make_config_relative_path(generated_abs) if generated_abs else str(self.cfg.get("theme_bg_image") or "").strip())
        )
        source_rel = "" if bg_force_clear else (lz._make_config_relative_path(source_path) if source_path else "")
        floating_generated_rel = (
            ""
            if floating_bg_force_clear
            else (
                lz._make_config_relative_path(floating_generated_abs)
                if floating_generated_abs
                else str(self.cfg.get("theme_floating_bg_image") or "").strip()
            )
        )
        floating_source_rel = "" if floating_bg_force_clear else (lz._make_config_relative_path(floating_source_path) if floating_source_path else "")
        user_avatar_generated_rel = lz._make_config_relative_path(user_avatar_generated_abs) if user_avatar_generated_abs else ""
        user_avatar_source_rel = lz._make_config_relative_path(user_avatar_source_path) if user_avatar_source_path else ""
        ai_avatar_generated_rel = lz._make_config_relative_path(ai_avatar_generated_abs) if ai_avatar_generated_abs else ""
        ai_avatar_source_rel = lz._make_config_relative_path(ai_avatar_source_path) if ai_avatar_source_path else ""
        self.cfg["theme_font_family"] = font_family
        self.cfg["theme_font_weight"] = font_weight
        self.cfg["theme_font_size"] = font_size
        self.cfg["theme_visual_preset"] = visual_preset
        self.cfg["theme_chat_auto_jump_latest"] = auto_jump_latest
        self.cfg["theme_bg_preset"] = bg_preset
        self.cfg["theme_bg_image"] = generated_rel
        self.cfg["theme_bg_source"] = source_rel
        self.cfg["theme_bg_crop"] = crop_data if source_rel else {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        self.cfg["theme_bg_fade"] = fade_value
        self.cfg["theme_bg_blur"] = fade_value
        self.cfg["theme_bg_render_sig"] = ""
        self.cfg["theme_bg_image_mode"] = bg_mode
        self.cfg["theme_floating_bg_preset"] = floating_bg_preset
        self.cfg["theme_floating_bg_image"] = floating_generated_rel
        self.cfg["theme_floating_bg_source"] = floating_source_rel
        self.cfg["theme_floating_bg_crop"] = floating_crop_data if floating_source_rel else {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        self.cfg["theme_floating_bg_fade"] = floating_fade_value
        self.cfg["theme_floating_bg_blur"] = floating_fade_value
        self.cfg["theme_floating_bg_render_sig"] = ""
        self.cfg["theme_floating_bg_image_mode"] = floating_bg_mode
        self.cfg["theme_user_avatar_image"] = user_avatar_generated_rel
        self.cfg["theme_user_avatar_source"] = user_avatar_source_rel
        self.cfg["theme_user_avatar_crop"] = user_avatar_crop_data if user_avatar_source_rel else {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        self.cfg["theme_user_avatar_render_sig"] = ""
        self.cfg["theme_ai_avatar_image"] = ai_avatar_generated_rel
        self.cfg["theme_ai_avatar_source"] = ai_avatar_source_rel
        self.cfg["theme_ai_avatar_crop"] = ai_avatar_crop_data if ai_avatar_source_rel else {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        self.cfg["theme_ai_avatar_render_sig"] = ""
        lz.save_config(self.cfg)
        mode = self._normalize_appearance_mode(self.cfg.get("appearance_mode", "light"))
        self._apply_theme(mode)
        self._reload_theme_panel()
        visual_label = ""
        if visual_combo is not None:
            visual_label = str(visual_combo.currentText() or "").strip()
        avatars_updated = bool(user_avatar_generated_abs or ai_avatar_generated_abs or user_avatar_force_clear or ai_avatar_force_clear)
        if bg_preset == "image" and not generated_abs:
            self.settings_theme_notice.setText("主题已保存。你选择了图片背景，但还没有图片文件，当前会使用主体预设默认底色。")
        elif floating_bg_preset == "image" and not floating_generated_abs:
            self.settings_theme_notice.setText("主题已保存。悬浮窗选择了图片背景，但图片无效，当前会跟随主背景。")
        elif bg_preset == "image":
            tail = " 聊天头像也已同步刷新。" if avatars_updated else ""
            self.settings_theme_notice.setText("主题设置已保存并应用。当前显示的是裁切后背景图，主体预设层级也已同步刷新。" + tail)
        elif floating_bg_preset == "image":
            tail = " 聊天头像也已同步刷新。" if avatars_updated else ""
            self.settings_theme_notice.setText("主题设置已保存并应用。悬浮窗将使用单独背景图。" + tail)
        else:
            suffix = f"当前默认主体预设：{visual_label}。" if visual_label else ""
            avatar_suffix = "聊天头像也已同步刷新。" if avatars_updated else ""
            self.settings_theme_notice.setText("主题设置已保存并应用。" + suffix + avatar_suffix)
        self._set_status("主题设置已保存。")
