from __future__ import annotations

import html
import io
import json
import os
import platform
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
import ctypes
from ctypes import byref, c_int
from datetime import datetime

from PySide6.QtCore import QByteArray, QEvent, QMetaObject, QPoint, QRect, QRectF, QSize, Qt, QTimer, qInstallMessageHandler
from PySide6.QtGui import QColor, QCursor, QIcon, QKeyEvent, QPainter, QPainterPath, QPixmap, QRegion, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QSystemTrayIcon,
    QTextBrowser,
    QTextEdit,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from launcher_app import core as lz
from launcher_app.theme import C, F, FLUENT_QSS, apply_fluent_shadow, apply_mica

SCROLLBAR_STYLE = """
QScrollBar:vertical { width: 10px; background: transparent; border: none; margin: 2px; }
QScrollBar::handle:vertical {
    background: rgba(148,163,184,0.28); border-radius: 4px; min-height: 28px;
}
QScrollBar::handle:vertical:hover { background: rgba(148,163,184,0.50); }
QScrollBar::handle:vertical:pressed { background: rgba(148,163,184,0.70); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; background: none; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
"""

from qt_chat_parts import common as chat_common
from qt_chat_parts.api_editor import ApiEditorMixin
from qt_chat_parts.bridge_runtime import BridgeRuntimeMixin
from qt_chat_parts.common import (
    PRIVATE_PYTHON_VERSION,
    _SVG_BOT,
    _SVG_CHEVRON_DOWN,
    _SVG_INFO,
    _SVG_SEND,
    _SVG_STOP,
    InputTextEdit,
    MessageRow,
    OptionCard,
    _assistant_segment_markdown,
    _probe_download_requirements,
    _session_copy,
    _session_source_label,
    _svg_icon,
)
from qt_chat_parts.chat_view import ChatViewMixin
from qt_chat_parts.channel_runtime import ChannelRuntimeMixin
from qt_chat_parts.dependency_runtime import DependencyRuntimeMixin
from qt_chat_parts.downloads import DownloadMixin
from qt_chat_parts.navigation import NavigationMixin
from qt_chat_parts.personal_usage import PersonalUsageMixin
from qt_chat_parts.schedule_runtime import ScheduleRuntimeMixin
from qt_chat_parts.session_shell import SessionShellMixin
from qt_chat_parts.setup_pages import SetupPagesMixin
from qt_chat_parts.settings_panel import SettingsPanelMixin
from qt_chat_parts.sidebar_sessions import SidebarSessionsMixin
from qt_chat_parts.window_shell import WindowShellMixin


def _qt_message_handler(mode, context, message):
    text = str(message or "").strip()
    if text.startswith("QFontDatabase: Cannot find font directory "):
        return
    if text.startswith("Note that Qt no longer ships fonts."):
        return
    if "QFont::setPointSize: Point size <= 0" in text:
        return
    if "QFont::setPointSizeF: Point size <= 0" in text:
        return
    sys.stderr.write(text + "\n")


qInstallMessageHandler(_qt_message_handler)


class FloatingOrbWindow(QWidget):
    def __init__(self, host):
        flags = Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        no_shadow_hint = getattr(Qt, "NoDropShadowWindowHint", None)
        if no_shadow_hint is not None:
            flags |= no_shadow_hint
        super().__init__(None, flags)
        self._host = host
        self._expanded = False
        self._drag_active = False
        self._drag_moved = False
        self._drag_origin = QPoint()
        self._drag_start = QPoint()
        self._last_signature = None
        self._position_initialized = False
        self._expanded_size = QSize(480, 760)
        self._collapsed_size = QSize(56, 56)
        self._ignore_llm_change = False
        self._rendered_rows = []
        self._focus_latest_user_after_refresh = False
        self._orb_hover = False
        self._orb_pressed = False

        self.setWindowTitle("GenericAgent 悬浮对话")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.resize(self._collapsed_size)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.panel = QFrame()
        self.panel.setObjectName("floatingPanel")
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)

        self.panel_head = QFrame()
        self.panel_head.setObjectName("floatingPanelHead")
        self.panel_head.installEventFilter(self)
        head_row = QHBoxLayout(self.panel_head)
        head_row.setContentsMargins(16, 14, 16, 12)
        head_row.setSpacing(10)
        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(3)
        self.title_label = QLabel("悬浮对话")
        self.title_label.setObjectName("cardTitle")
        self.subtitle_label = QLabel("点击下方悬浮球展开或收起。")
        self.subtitle_label.setObjectName("mutedText")
        self.subtitle_label.setWordWrap(True)
        title_col.addWidget(self.title_label)
        title_col.addWidget(self.subtitle_label)
        head_row.addLayout(title_col, 1)
        self.restore_btn = QPushButton("完整界面")
        self.restore_btn.clicked.connect(self._restore_main_window)
        head_row.addWidget(self.restore_btn, 0)
        self.tray_btn = QPushButton("仅托盘")
        self.tray_btn.clicked.connect(self._hide_to_tray_only)
        head_row.addWidget(self.tray_btn, 0)
        panel_layout.addWidget(self.panel_head)

        session_row_host = QFrame()
        session_row_host.setObjectName("floatingSessionRow")
        session_row = QHBoxLayout(session_row_host)
        session_row.setContentsMargins(16, 0, 16, 10)
        session_row.setSpacing(8)
        self.session_combo = QComboBox()
        self.session_combo.setMinimumWidth(220)
        self.session_combo.currentIndexChanged.connect(self._on_session_changed)
        session_row.addWidget(self.session_combo, 1)
        self.new_session_btn = QPushButton("新建")
        self.new_session_btn.clicked.connect(self._new_session)
        session_row.addWidget(self.new_session_btn, 0)
        self.session_menu_btn = QPushButton("⋯")
        self.session_menu_btn.setFixedWidth(36)
        self.session_menu_btn.setToolTip("当前会话操作")
        self.session_menu_btn.clicked.connect(self._open_session_menu)
        session_row.addWidget(self.session_menu_btn, 0)
        self.jump_latest_btn = QPushButton("最新")
        self.jump_latest_btn.clicked.connect(self._jump_to_latest)
        session_row.addWidget(self.jump_latest_btn, 0)
        panel_layout.addWidget(session_row_host)

        self.meta_label = QLabel("未连接")
        self.meta_label.setObjectName("softTextSmall")
        self.meta_label.setWordWrap(True)
        self.meta_label.setContentsMargins(16, 0, 16, 8)
        panel_layout.addWidget(self.meta_label)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("floatingChatScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.scroll.verticalScrollBar().valueChanged.connect(self._update_jump_button_state)
        self.msg_root = QWidget()
        self.msg_root.setObjectName("floatingMsgRoot")
        self.msg_layout = QVBoxLayout(self.msg_root)
        self.msg_layout.setContentsMargins(0, 12, 0, 12)
        self.msg_layout.setSpacing(4)
        self.msg_layout.addStretch(1)
        self.scroll.setWidget(self.msg_root)
        panel_layout.addWidget(self.scroll, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("mutedText")
        self.status_label.setWordWrap(True)
        self.status_label.setContentsMargins(16, 4, 16, 10)
        panel_layout.addWidget(self.status_label)

        composer = QFrame()
        composer.setObjectName("floatingComposer")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(14, 12, 14, 76)
        composer_layout.setSpacing(8)
        tool_row = QHBoxLayout()
        tool_row.setContentsMargins(0, 0, 0, 0)
        tool_row.setSpacing(8)
        self.llm_combo = QComboBox()
        self.llm_combo.setMinimumWidth(190)
        self.llm_combo.setMaximumWidth(280)
        self.llm_combo.currentIndexChanged.connect(self._on_llm_changed)
        tool_row.addWidget(self.llm_combo, 0)
        self.info_btn = QPushButton()
        self.info_btn.setObjectName("infoBtn")
        self.info_btn.setFixedSize(26, 26)
        self.info_btn.setCursor(Qt.PointingHandCursor)
        self.info_btn.setToolTip("查看当前状态")
        self.info_btn.clicked.connect(self._show_info_popup)
        tool_row.addWidget(self.info_btn, 0)
        tool_row.addStretch(1)
        composer_layout.addLayout(tool_row)
        self.input_box = InputTextEdit(self._handle_send, image_cb=host._handle_input_image_attachments)
        self.input_box.setMinimumHeight(30)
        self.input_box.setMaximumHeight(75)
        composer_layout.addWidget(self.input_box)
        self.input_attachment_host = QFrame()
        self.input_attachment_host.setObjectName("cardInset")
        self.input_attachment_host.setVisible(False)
        self.input_attachment_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        attachment_box = QVBoxLayout(self.input_attachment_host)
        attachment_box.setContentsMargins(12, 10, 12, 10)
        attachment_box.setSpacing(8)
        self.input_attachment_summary = QLabel("")
        self.input_attachment_summary.setObjectName("softTextSmall")
        self.input_attachment_summary.setWordWrap(True)
        attachment_box.addWidget(self.input_attachment_summary)
        self.input_attachment_list_widget = QWidget()
        self.input_attachment_list_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.input_attachment_list_layout = QVBoxLayout(self.input_attachment_list_widget)
        self.input_attachment_list_layout.setContentsMargins(0, 0, 0, 0)
        self.input_attachment_list_layout.setSpacing(8)
        attachment_box.addWidget(self.input_attachment_list_widget)
        composer_layout.addWidget(self.input_attachment_host)
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        self.abort_btn = QPushButton("  中断")
        self.abort_btn.setIcon(_svg_icon("floating_stop", _SVG_STOP, color=C["danger_text"], size=14))
        self.abort_btn.clicked.connect(self._abort_reply)
        action_row.addWidget(self.abort_btn, 0)
        self.regen_btn = QPushButton("重试")
        self.regen_btn.clicked.connect(self._regenerate_latest)
        action_row.addWidget(self.regen_btn, 0)
        action_row.addStretch(1)
        self.send_btn = QPushButton("  发送")
        self.send_btn.setIcon(_svg_icon("floating_send", _SVG_SEND, color="#ffffff", size=14))
        self.send_btn.clicked.connect(self._handle_send)
        action_row.addWidget(self.send_btn, 0)
        composer_layout.addLayout(action_row)
        panel_layout.addWidget(composer, 0)

        root.addWidget(self.panel)
        self.ball_btn = QPushButton(self)
        self.ball_btn.setObjectName("floatingBall")
        self.ball_btn.setFixedSize(56, 56)
        self.ball_btn.setCursor(Qt.PointingHandCursor)
        self.ball_btn.setIcon(_svg_icon("floating_ball", _SVG_BOT, color="#ffffff", size=24))
        self.ball_btn.setIconSize(QSize(24, 24))
        self.ball_btn.setFlat(False)
        self.ball_btn.setFocusPolicy(Qt.NoFocus)
        self.ball_btn.setToolTip("点击展开或收起悬浮对话")
        self.ball_btn.installEventFilter(self)
        self.ball_btn.show()
        self.ball_badge = QLabel(self)
        self.ball_badge.setObjectName("floatingBallBadge")
        self.ball_badge.setAlignment(Qt.AlignCenter)
        self.ball_badge.setFixedSize(18, 18)
        self.ball_badge.hide()

        self.collapse_panel()

    def _apply_native_window_style(self):
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            dwm = ctypes.windll.dwmapi
            border_attr = 34  # DWMWA_BORDER_COLOR
            corner_attr = 33  # DWMWA_WINDOW_CORNER_PREFERENCE
            backdrop_attr = 38  # DWMWA_SYSTEMBACKDROP_TYPE
            color_none = c_int(-2)  # DWMWA_COLOR_NONE
            corner_pref = c_int(2 if self._expanded else 1)  # round when expanded, do-not-round when collapsed
            backdrop_none = c_int(1)
            dwm.DwmSetWindowAttribute(hwnd, border_attr, byref(color_none), ctypes.sizeof(color_none))
            dwm.DwmSetWindowAttribute(hwnd, corner_attr, byref(corner_pref), ctypes.sizeof(corner_pref))
            dwm.DwmSetWindowAttribute(hwnd, backdrop_attr, byref(backdrop_none), ctypes.sizeof(backdrop_none))
        except Exception:
            pass

    def _restore_main_window(self):
        restore = getattr(self._host, "_restore_from_tray_mode", None)
        if callable(restore):
            restore()

    def _hide_to_tray_only(self):
        hide_only = getattr(self._host, "_hide_floating_chat_window", None)
        if callable(hide_only):
            hide_only()

    def _handle_send(self):
        sender = getattr(self._host, "_handle_floating_send", None)
        if callable(sender):
            sender()

    def _abort_reply(self):
        aborter = getattr(self._host, "_abort", None)
        if callable(aborter):
            aborter()

    def _on_llm_changed(self, index: int):
        if self._ignore_llm_change or index < 0:
            return
        syncer = getattr(self._host, "_on_floating_llm_changed", None)
        if callable(syncer):
            syncer(index)

    def _show_info_popup(self):
        getter = getattr(self._host, "_info_tooltip_text", None)
        if not callable(getter):
            return
        QToolTip.showText(
            self.info_btn.mapToGlobal(self.info_btn.rect().bottomLeft()),
            getter(),
            self.info_btn,
        )

    def _current_draft_text(self) -> str:
        return str(self.input_box.toPlainText() or "").strip()

    def _on_session_changed(self, index: int):
        if index < 0:
            return
        syncer = getattr(self._host, "_on_floating_session_changed", None)
        if callable(syncer):
            syncer(index)

    def _new_session(self):
        maker = getattr(self._host, "_new_session_from_floating", None)
        if callable(maker):
            maker()

    def _jump_to_latest(self):
        QTimer.singleShot(0, self._scroll_to_latest_dialogue)

    def _regenerate_latest(self):
        regen = getattr(self._host, "_regenerate_latest_from_floating", None)
        if callable(regen):
            regen()

    def _open_session_menu(self):
        opener = getattr(self._host, "_open_floating_session_menu", None)
        if callable(opener):
            opener(self.session_menu_btn)

    def _available_geometry(self):
        app = QApplication.instance()
        screen = None
        if app is not None:
            screen = app.primaryScreen()
        return screen.availableGeometry() if screen is not None else self.geometry()

    def _clamp_pos(self, pos: QPoint, size: QSize) -> QPoint:
        rect = self._available_geometry()
        margin = 12
        x = min(max(rect.left() + margin, pos.x()), rect.right() - size.width() - margin + 1)
        y = min(max(rect.top() + margin, pos.y()), rect.bottom() - size.height() - margin + 1)
        return QPoint(x, y)

    def _position_default(self):
        loader = getattr(self._host, "_load_floating_orb_position", None)
        if callable(loader):
            loaded = loader()
            if isinstance(loaded, QPoint):
                return loaded
        rect = self._available_geometry()
        margin = 18
        size = self.size()
        return QPoint(
            rect.right() - size.width() - margin + 1,
            rect.bottom() - size.height() - margin + 1,
        )

    def _apply_window_size(self, size: QSize):
        bottom_right = self.frameGeometry().bottomRight()
        rect = self._available_geometry()
        target = QSize(
            min(size.width(), max(240, rect.width() - 24)),
            min(size.height(), max(240, rect.height() - 24)),
        )
        self.setFixedSize(target)
        if not self._position_initialized:
            self.move(self._clamp_pos(self._position_default(), target))
            self._position_initialized = True
            return
        new_pos = QPoint(bottom_right.x() - target.width() + 1, bottom_right.y() - target.height() + 1)
        self.move(self._clamp_pos(new_pos, target))
        self._place_ball()

    def toggle_panel(self):
        if self._expanded:
            self.collapse_panel()
        else:
            self.expand_panel()

    def collapse_panel(self):
        self._expanded = False
        self.panel.hide()
        self.ball_btn.hide()
        self._apply_window_size(self._collapsed_size)
        self._place_ball()
        self._apply_native_window_style()
        self.update()

    def expand_panel(self):
        self._expanded = True
        self.panel.show()
        self._apply_window_size(self._expanded_size)
        self.ball_btn.show()
        self._place_ball()
        self._apply_native_window_style()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(0, self._scroll_to_bottom)
        self.update()

    def apply_theme(self):
        from launcher_app import theme as qt_theme

        host = self._host
        cfg = host.cfg if isinstance(getattr(host, "cfg", None), dict) else {}
        floating_preset = str(cfg.get("theme_floating_bg_preset") or "follow").strip().lower()
        floating_mode = str(cfg.get("theme_floating_bg_image_mode") or "center").strip().lower()
        use_main_image = str(cfg.get("theme_bg_preset") or "default").strip().lower() == "image"
        floating_image_rel = ""
        if floating_preset == "image":
            floating_image_rel = str(cfg.get("theme_floating_bg_image") or "").strip()
        elif use_main_image:
            floating_image_rel = str(cfg.get("theme_bg_image") or "").strip()
        floating_image_abs = lz._resolve_config_path(floating_image_rel) if floating_image_rel else ""
        has_floating_image = bool(floating_image_abs and os.path.isfile(floating_image_abs))

        def _alpha(color_text: str, alpha: int) -> str:
            color = QColor(str(color_text or ""))
            if not color.isValid():
                return str(color_text or "")
            a = max(0, min(255, int(alpha)))
            return f"rgba({color.red()},{color.green()},{color.blue()},{a})"

        panel_background_rule = f"background: {C['panel']};"
        head_background = "background: transparent;"
        composer_background = f"background: {C['surface']};"
        badge_border = C["panel"]
        if has_floating_image:
            qss_url = floating_image_abs.replace("\\", "/").replace('"', '\\"')
            if floating_mode == "stretch":
                img_rule = f'border-image: url("{qss_url}") 0 0 0 0 stretch stretch;'
            elif floating_mode == "tile":
                img_rule = (
                    f'background-image: url("{qss_url}"); '
                    "background-repeat: repeat-xy; "
                    "background-position: top left;"
                )
            else:
                img_rule = (
                    f'background-image: url("{qss_url}"); '
                    "background-repeat: no-repeat; "
                    "background-position: center center;"
                )
            panel_background_rule = f"background-color: {C['panel']}; {img_rule}"
            fade_value = max(
                0,
                min(
                    100,
                    int(
                        cfg.get(
                            "theme_floating_bg_fade",
                            cfg.get("theme_floating_bg_blur", cfg.get("theme_bg_fade", cfg.get("theme_bg_blur", 18))),
                        )
                        or 18
                    ),
                ),
            )
            head_background = f"background: {_alpha(C['panel'], min(220, 88 + int(fade_value * 1.15)))};"
            composer_background = f"background: {_alpha(C['surface'], min(236, 112 + int(fade_value * 1.15)))};"
            badge_border = _alpha(C["panel"], min(240, 136 + int(fade_value)))

        chat_bg = "transparent" if has_floating_image else qt_theme.chat_surface_background()
        body_fs = qt_theme.font_body_size()
        self.setStyleSheet(
            f"QWidget {{ background: transparent; color: {C['text']}; }}"
            f"QFrame#floatingPanel {{ {panel_background_rule} border: 1px solid {C['stroke_default']}; border-radius: {F['radius_xl']}px; }}"
            f"QFrame#floatingPanelHead {{ {head_background} border: none; }}"
            f"QFrame#floatingComposer {{ {composer_background} border-top: 1px solid {C['stroke_divider']}; border-bottom-left-radius: {F['radius_xl']}px; border-bottom-right-radius: {F['radius_xl']}px; }}"
            f"QPushButton#floatingBall {{ background: {C['accent']}; border: none; border-radius: 28px; }}"
            f"QPushButton#floatingBall:hover {{ background: {C['accent_hover']}; border: none; }}"
            f"QPushButton#floatingBall:pressed {{ background: {C['accent_pressed']}; border: none; }}"
            f"QLabel#floatingBallBadge {{ background: {C['danger']}; color: white; border: 2px solid {badge_border}; border-radius: 9px; font-size: 10px; font-weight: 700; }}"
        )
        self.scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {chat_bg}; }}" + SCROLLBAR_STYLE)
        viewport = self.scroll.viewport()
        if viewport is not None:
            viewport.setStyleSheet(f"background: {chat_bg};")
        self.input_box.setStyleSheet(
            f"QTextEdit {{ background: transparent; border: none; color: {C['text']}; font-size: {body_fs}px; padding: 2px; }}"
        )
        self.llm_combo.setStyleSheet(host._api_combo_style())
        self.session_combo.setStyleSheet(host._api_combo_style())
        self.info_btn.setIcon(_svg_icon("floating_info", _SVG_INFO, color=C["muted"], size=14))
        self.restore_btn.setStyleSheet(host._action_button_style())
        self.tray_btn.setStyleSheet(host._action_button_style(kind="subtle"))
        self.new_session_btn.setStyleSheet(host._action_button_style())
        self.jump_latest_btn.setStyleSheet(host._action_button_style(kind="subtle"))
        self.abort_btn.setStyleSheet(host._action_button_style())
        self.regen_btn.setStyleSheet(host._action_button_style())
        self.send_btn.setStyleSheet(host._action_button_style(primary=True))
        self.session_menu_btn.setStyleSheet(host._action_button_style(kind="subtle"))
        self.msg_root.setStyleSheet(f"background: {chat_bg};")
        if self.panel.graphicsEffect() is not None:
            self.panel.setGraphicsEffect(None)

    def _place_ball(self):
        if getattr(self, "ball_btn", None) is None:
            return
        if self._expanded:
            margin = 14
            x = max(0, self.width() - self.ball_btn.width() - margin)
            y = max(0, self.height() - self.ball_btn.height() - margin)
            self.ball_btn.show()
        else:
            x = 0
            y = 0
            self.ball_btn.show()
        self.ball_btn.move(x, y)
        self.ball_btn.setMask(QRegion(self.ball_btn.rect(), QRegion.Ellipse))
        self.ball_btn.raise_()
        badge = getattr(self, "ball_badge", None)
        if badge is not None:
            badge.move(x + self.ball_btn.width() - badge.width() + 2, y - 2)
            badge.setMask(QRegion(badge.rect(), QRegion.Ellipse))
            badge.raise_()
        self._update_window_mask()

    def _rounded_mask_region(self, radius: int) -> QRegion:
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), float(radius), float(radius))
        return QRegion(path.toFillPolygon().toPolygon())

    def _update_window_mask(self):
        if getattr(self, "ball_btn", None) is None:
            return
        if self._expanded:
            self.setMask(self._rounded_mask_region(int(F["radius_xl"] or 18)))
            return
        ball = self.ball_btn.geometry()
        region = QRegion(ball, QRegion.Ellipse)
        badge = getattr(self, "ball_badge", None)
        if badge is not None and badge.isVisible():
            region = region.united(QRegion(badge.geometry(), QRegion.Ellipse))
        self.setMask(region)

    def _clear_rows(self):
        self._rendered_rows = []
        while self.msg_layout.count() > 1:
            item = self.msg_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _scroll_to_bottom(self):
        bar = self.scroll.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())
        self._update_jump_button_state()

    def _update_jump_button_state(self):
        btn = getattr(self, "jump_latest_btn", None)
        if btn is None:
            return
        bar = self.scroll.verticalScrollBar()
        if bar is None:
            btn.hide()
            return
        near_bottom = bar.value() >= max(0, bar.maximum() - 40)
        btn.setVisible(not near_bottom)

    def _scroll_row_to_top(self, row, *, top_margin: int = 18):
        if row is None:
            return

        def apply():
            try:
                bar = self.scroll.verticalScrollBar()
                target_y = max(0, row.y() - int(top_margin or 0))
                bar.setValue(min(target_y, bar.maximum()))
            finally:
                self._update_jump_button_state()

        QTimer.singleShot(30, apply)

    def _latest_user_row(self):
        for row in reversed(self._rendered_rows):
            if getattr(row, "_role", "") == "user":
                return row
        return None

    def _scroll_to_latest_dialogue(self):
        latest_user_row = self._latest_user_row()
        if latest_user_row is not None:
            self._scroll_row_to_top(latest_user_row)
            return
        self._scroll_to_bottom()

    def focus_latest_user(self):
        self._focus_latest_user_after_refresh = True

    def _regenerate_from_row(self, row):
        if getattr(self._host, "_busy", False):
            return
        try:
            idx = self._rendered_rows.index(row)
        except ValueError:
            return
        user_text = None
        for pos in range(idx - 1, -1, -1):
            prev = self._rendered_rows[pos]
            if getattr(prev, "_role", "") == "user":
                user_text = getattr(prev, "_text", "")
                break
        if not user_text:
            return
        self.input_box.setPlainText(str(user_text or ""))
        self._handle_send()

    def _render_rows(self, bubbles, stream_text: str):
        signature = (
            tuple((str(item.get("role") or ""), str(item.get("text") or "")) for item in (bubbles or [])),
            str(stream_text or ""),
            bool(getattr(self._host, "_busy", False)),
        )
        if signature == self._last_signature:
            return
        self._last_signature = signature
        self._clear_rows()
        rows = list(bubbles or [])
        if not rows and not stream_text and not getattr(self._host, "_busy", False):
            empty = QLabel("点击悬浮球即可快速发送消息，当前会话内容会在这里同步显示。")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignCenter)
            empty.setObjectName("mutedText")
            empty.setStyleSheet(f"color: {C['muted']}; padding: 24px 18px;")
            self.msg_layout.insertWidget(0, empty)
            return
        insert_index = self.msg_layout.count() - 1
        for bubble in rows:
            role = str(bubble.get("role") or "assistant").strip().lower()
            on_resend = self._regenerate_from_row if role == "assistant" else None
            row = MessageRow(str(bubble.get("text") or ""), role, self.msg_root, on_resend=on_resend)
            row.set_finished(True)
            self.msg_layout.insertWidget(insert_index, row)
            self._rendered_rows.append(row)
            insert_index += 1
        if getattr(self._host, "_busy", False):
            row = MessageRow(str(stream_text or ""), "assistant", self.msg_root)
            row.update_content(str(stream_text or ""), finished=False)
            self.msg_layout.insertWidget(insert_index, row)
            self._rendered_rows.append(row)
        host_follow_latest = bool(getattr(self._host, "_follow_latest_user_message", False))
        keep_latest_user = self._focus_latest_user_after_refresh or host_follow_latest
        if keep_latest_user:
            self._focus_latest_user_after_refresh = False
            if host_follow_latest:
                follower = getattr(self._host, "_set_follow_latest_user", None)
                if callable(follower):
                    follower(False)
            QTimer.singleShot(0, self._scroll_to_latest_dialogue)
        else:
            QTimer.singleShot(0, self._scroll_to_bottom)

    def sync_view(self, *, title, subtitle, bubbles, stream_text, status, meta, can_send, can_abort, read_only):
        self.title_label.setText(title)
        self.subtitle_label.setText(subtitle)
        self.status_label.setText(status)
        self.meta_label.setText(meta)
        self._render_rows(bubbles, stream_text)
        self.input_box.setReadOnly(read_only)
        self.input_box.setPlaceholderText(
            "当前会话只读，不能从悬浮窗继续发送消息。"
            if read_only
            else "输入消息，Enter 发送，Shift+Enter 换行"
        )
        self.send_btn.setEnabled(bool(can_send))
        self.abort_btn.setEnabled(bool(can_abort))
        self.regen_btn.setEnabled(bool(bubbles) and (not getattr(self._host, "_busy", False)))
        self.session_menu_btn.setEnabled(bool((getattr(self._host, "current_session", None) or {}).get("id")))
        busy = bool(getattr(self._host, "_busy", False))
        badge = getattr(self, "ball_badge", None)
        if badge is not None:
            if busy:
                badge.setText("…")
                badge.show()
            elif status:
                badge.setText("!")
                badge.hide()
            else:
                badge.hide()
        self.ball_btn.setToolTip(f"{title}\n{meta}" if meta else title)
        self._place_ball()

    def sync_llm_items(self, llms, *, enabled: bool, current_index: int):
        self._ignore_llm_change = True
        self.llm_combo.clear()
        for pos, llm in enumerate(llms or []):
            self.llm_combo.addItem(str(llm.get("name") or "(未命名)"), llm.get("idx", pos))
        if self.llm_combo.count() == 0:
            self.llm_combo.addItem("未配置 LLM", -1)
        target = current_index if 0 <= int(current_index or -1) < self.llm_combo.count() else 0
        self.llm_combo.setCurrentIndex(target)
        self.llm_combo.setEnabled(bool(enabled) and bool(llms))
        self._ignore_llm_change = False

    def sync_session_items(self, rows, *, current_session_id: str, enabled: bool):
        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        current_index = -1
        for idx, row in enumerate(rows or []):
            title = str(row.get("title") or "(未命名)").strip() or "(未命名)"
            if bool(row.get("pinned", False)):
                title = f"★ {title}"
            channel_label = str(row.get("channel_label") or "").strip()
            label = f"{title} · {channel_label}" if channel_label else title
            self.session_combo.addItem(label, str(row.get("id") or ""))
            if str(row.get("id") or "") == str(current_session_id or ""):
                current_index = idx
        if self.session_combo.count() == 0:
            self.session_combo.addItem("暂无会话", "")
            enabled = False
        self.session_combo.setCurrentIndex(current_index if current_index >= 0 else 0)
        self.session_combo.setEnabled(bool(enabled))
        self.session_combo.blockSignals(False)

    def eventFilter(self, watched, event):
        ball_btn = getattr(self, "ball_btn", None)
        panel_head = getattr(self, "panel_head", None)
        if watched in (ball_btn, panel_head):
            et = event.type()
            if et == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._drag_active = True
                self._drag_moved = False
                self._drag_origin = event.globalPosition().toPoint()
                self._drag_start = self.pos()
                return True
            if et == QEvent.MouseMove and self._drag_active:
                delta = event.globalPosition().toPoint() - self._drag_origin
                if delta.manhattanLength() > 4:
                    self._drag_moved = True
                if self._drag_moved:
                    self.move(self._clamp_pos(self._drag_start + delta, self.size()))
                return True
            if et == QEvent.MouseButtonRelease and self._drag_active and event.button() == Qt.LeftButton:
                self._drag_active = False
                self._snap_to_edge()
                saver = getattr(self._host, "_save_floating_orb_position", None)
                if callable(saver):
                    saver(self.pos())
                if watched is self.ball_btn and not self._drag_moved:
                    self.toggle_panel()
                return True
        return super().eventFilter(watched, event)

    def enterEvent(self, event):
        if not self._expanded:
            self._orb_hover = True
            self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self._expanded:
            self._orb_hover = False
            self._orb_pressed = False
            self.update()
        super().leaveEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._position_initialized:
            self.move(self._clamp_pos(self._position_default(), self.size()))
            self._position_initialized = True
        self._place_ball()
        self._update_window_mask()
        self._apply_native_window_style()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_ball()
        self._update_window_mask()

    def mousePressEvent(self, event):
        if not self._expanded and event.button() == Qt.LeftButton:
            self._orb_pressed = True
            self._drag_active = True
            self._drag_moved = False
            self._drag_origin = event.globalPosition().toPoint()
            self._drag_start = self.pos()
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self._expanded and self._drag_active:
            delta = event.globalPosition().toPoint() - self._drag_origin
            if delta.manhattanLength() > 4:
                self._drag_moved = True
            if self._drag_moved:
                self.move(self._clamp_pos(self._drag_start + delta, self.size()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if not self._expanded and self._drag_active and event.button() == Qt.LeftButton:
            self._drag_active = False
            was_click = not self._drag_moved
            self._orb_pressed = False
            self._snap_to_edge()
            saver = getattr(self._host, "_save_floating_orb_position", None)
            if callable(saver):
                saver(self.pos())
            self.update()
            if was_click:
                self.toggle_panel()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)

    def _snap_to_edge(self):
        rect = self._available_geometry()
        pos = self.pos()
        left_gap = abs(pos.x() - rect.left())
        right_gap = abs((rect.right() - self.width() + 1) - pos.x())
        if left_gap <= right_gap:
            x = rect.left() + 12
        else:
            x = rect.right() - self.width() - 12 + 1
        y = min(max(rect.top() + 12, pos.y()), rect.bottom() - self.height() - 12 + 1)
        self.move(QPoint(x, y))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self._expanded:
            self.collapse_panel()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        if getattr(self._host, "_force_exit_requested", False):
            super().closeEvent(event)
            return
        event.ignore()
        self._restore_main_window()


class FloatingChatWindow(QWidget):
    def __init__(self, host):
        super().__init__(None, Qt.Tool | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint)
        self._host = host
        self._last_transcript = None
        self.setWindowTitle("GenericAgent 悬浮对话")
        self.resize(440, 680)
        self.setMinimumSize(360, 460)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        head = QFrame()
        head.setObjectName("floatingHead")
        head_row = QHBoxLayout(head)
        head_row.setContentsMargins(14, 12, 14, 12)
        head_row.setSpacing(10)
        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(2)
        self.title_label = QLabel("悬浮对话")
        self.title_label.setObjectName("cardTitle")
        self.subtitle_label = QLabel("发送消息后会继续沿用当前会话。")
        self.subtitle_label.setObjectName("mutedText")
        self.subtitle_label.setWordWrap(True)
        title_col.addWidget(self.title_label)
        title_col.addWidget(self.subtitle_label)
        head_row.addLayout(title_col, 1)
        self.restore_btn = QPushButton("完整界面")
        self.restore_btn.clicked.connect(self._restore_main_window)
        head_row.addWidget(self.restore_btn, 0)
        self.hide_btn = QPushButton("仅托盘")
        self.hide_btn.clicked.connect(self._hide_to_tray_only)
        head_row.addWidget(self.hide_btn, 0)
        root.addWidget(head)

        self.transcript = QTextBrowser()
        self.transcript.setOpenLinks(False)
        self.transcript.setOpenExternalLinks(False)
        root.addWidget(self.transcript, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("mutedText")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        composer = QFrame()
        composer.setObjectName("floatingComposer")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(12, 10, 12, 10)
        composer_layout.setSpacing(8)
        self.input_box = InputTextEdit(self._handle_send)
        self.input_box.setMinimumHeight(82)
        self.input_box.setMaximumHeight(180)
        composer_layout.addWidget(self.input_box)
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        self.meta_label = QLabel("未连接")
        self.meta_label.setObjectName("mutedText")
        self.meta_label.setWordWrap(True)
        action_row.addWidget(self.meta_label, 1)
        self.abort_btn = QPushButton("中断")
        self.abort_btn.clicked.connect(self._abort_reply)
        action_row.addWidget(self.abort_btn, 0)
        self.send_btn = QPushButton("发送")
        self.send_btn.clicked.connect(self._handle_send)
        action_row.addWidget(self.send_btn, 0)
        composer_layout.addLayout(action_row)
        root.addWidget(composer)

    def _restore_main_window(self):
        restore = getattr(self._host, "_restore_from_tray_mode", None)
        if callable(restore):
            restore()

    def _hide_to_tray_only(self):
        hide_only = getattr(self._host, "_hide_floating_chat_window", None)
        if callable(hide_only):
            hide_only()

    def _handle_send(self):
        sender = getattr(self._host, "_handle_floating_send", None)
        if callable(sender):
            sender()

    def _abort_reply(self):
        aborter = getattr(self._host, "_abort", None)
        if callable(aborter):
            aborter()

    def apply_theme(self):
        host = self._host
        self.setStyleSheet(
            f"QWidget {{ background: {C['bg']}; color: {C['text']}; }}"
            f"QFrame#floatingHead, QFrame#floatingComposer {{ background: {C['panel']}; border: 1px solid {C['stroke_default']}; "
            f"border-radius: {F['radius_lg']}px; }}"
        )
        self.transcript.setStyleSheet(
            f"QTextBrowser {{ background: {C['surface']}; color: {C['text']}; border: 1px solid {C['stroke_default']}; "
            f"border-radius: {F['radius_lg']}px; padding: 12px; selection-background-color: rgba(79,140,255,0.35); }}"
        )
        self.input_box.setStyleSheet(
            f"QTextEdit {{ background: transparent; border: none; color: {C['text']}; font-size: 14px; padding: 2px; }}"
        )
        self.restore_btn.setStyleSheet(host._action_button_style())
        self.hide_btn.setStyleSheet(host._action_button_style(kind="subtle"))
        self.abort_btn.setStyleSheet(host._action_button_style())
        self.send_btn.setStyleSheet(host._action_button_style(primary=True))

    def sync_view(self, *, title, subtitle, transcript, status, meta, can_send, can_abort, read_only):
        self.title_label.setText(title)
        self.subtitle_label.setText(subtitle)
        self.status_label.setText(status)
        self.meta_label.setText(meta)
        if transcript != self._last_transcript:
            self.transcript.setPlainText(transcript)
            bar = self.transcript.verticalScrollBar()
            if bar is not None:
                bar.setValue(bar.maximum())
            self._last_transcript = transcript
        self.input_box.setReadOnly(read_only)
        self.input_box.setPlaceholderText(
            "当前会话只读，不能从悬浮窗继续发送消息。"
            if read_only
            else "输入消息，Enter 发送，Shift+Enter 换行"
        )
        self.send_btn.setEnabled(bool(can_send))
        self.abort_btn.setEnabled(bool(can_abort))

    def closeEvent(self, event):
        if getattr(self._host, "_force_exit_requested", False):
            super().closeEvent(event)
            return
        event.ignore()
        self._restore_main_window()

class QtChatWindow(ApiEditorMixin, ChannelRuntimeMixin, DependencyRuntimeMixin, ScheduleRuntimeMixin, PersonalUsageMixin, WindowShellMixin, BridgeRuntimeMixin, SessionShellMixin, NavigationMixin, ChatViewMixin, SetupPagesMixin, SettingsPanelMixin, DownloadMixin, SidebarSessionsMixin, QMainWindow):
    def __init__(self, agent_dir: str | None = None):
        super().__init__()
        loaded_cfg = lz.load_config()
        self.cfg = loaded_cfg if isinstance(loaded_cfg, dict) else {}
        initial_dir = str(agent_dir or self.cfg.get("agent_dir") or "").strip()
        self.agent_dir = os.path.abspath(initial_dir) if initial_dir else ""
        self.install_parent = str(self.cfg.get("install_parent") or os.path.expanduser("~")).strip() or os.path.expanduser("~")
        self.sidebar_collapsed = bool(self.cfg.get("sidebar_collapsed", False))
        self._session_filter_keyword = ""
        self._sidebar_view_mode = "roots"
        self._sidebar_device_scope = "local"
        self._sidebar_device_id = "local"
        self._sidebar_channel_id = "launcher"
        self._download_running = False
        self._download_mode = ""
        self._last_dependency_check = None
        self._last_dependency_report = None

        self.bridge_proc = None
        self._stderr_buf = []
        self._event_queue: queue.Queue = queue.Queue()
        self._drain_timer = QTimer(self)
        self._drain_timer.timeout.connect(self._drain_events)
        self._drain_timer.start(40)
        self._channel_snapshot_timer = QTimer(self)
        self._channel_snapshot_timer.timeout.connect(self._sync_all_channel_process_sessions)
        self._channel_snapshot_timer.start(2000)
        self._server_status_timer = QTimer(self)
        self._server_status_timer.timeout.connect(self._request_server_connection_probe)
        self._server_status_timer.start(15000)

        self._stream_flush_timer = QTimer(self)
        self._stream_flush_timer.setSingleShot(True)
        self._stream_flush_timer.timeout.connect(self._flush_stream_render)

        self.llms = []
        self.current_session = None
        self._selected_session_id = None
        self._pending_state_session = None
        self._busy = False
        self._last_activity = time.time()
        self._idle_thread_started = False
        self._abort_requested = False
        self._bridge_ready = False
        self._ignore_session_select = False
        self._ignore_llm_change = False
        self._user_scrolled_up = False
        self._follow_latest_user_message = False
        self._state_request_seq = 0
        self._active_token_event_ts = None
        self._current_stream_text = ""
        self._pending_stream_text = None
        self._stream_row = None
        self._rendered_message_rows = []
        self._pending_input_attachments_data = []
        self._active_turn_attachments_data = []
        self.pages = None
        self._welcome_page = None
        self._locate_page = None
        self._download_page = None
        self._chat_page = None
        self._settings_page = None
        self._settings_top_back_btn = None
        self._settings_top_home_btn = None
        self._current_settings_category = "api"
        self._channel_procs = {}
        self._qt_api_hidden_configs = []
        self._qt_api_state = []
        self._qt_api_extras = {}
        self._qt_api_passthrough = []
        self._qt_api_py_path = ""
        self._qt_api_parse_error = ""
        self._qt_channel_configs = []
        self._qt_channel_passthrough = []
        self._qt_channel_extras = {}
        self._qt_channel_states = {}
        self._qt_channel_py_path = ""
        self._qt_channel_parse_error = ""
        self._launcher_tray_icon = None
        self._launcher_tray_menu = None
        self._tray_restore_main_action = None
        self._tray_show_floating_action = None
        self._tray_hide_floating_action = None
        self._tray_exit_action = None
        self._launcher_tray_signal_owner = None
        self._floating_chat_window = None
        self._tray_mode_active = False
        self._tray_mode_hint_shown = False
        self._tray_restore_to_fullscreen = False
        self._force_exit_requested = False
        self._closing_in_progress = False
        self._scheduler_proc = None
        self._scheduler_log_handle = None
        self._scheduler_last_exit_code = None
        self._lan_interface_proc = None
        self._lan_interface_log_handle = None
        self._lan_interface_last_exit_code = None
        self._last_session_list_signature = None
        self._session_index_warmup_started = False
        self._server_status_state = "unconfigured"
        self._server_status_detail = ""
        self._server_status_host = ""
        self._server_status_checked_at = 0.0
        self._server_status_probe_running = False
        self._server_status_probe_pending = False
        self._shutdown_cleanup_started = False
        self._app_quit_requested = False

        self.setWindowTitle("GenericAgent 启动器")
        self.resize(1440, 920)
        self.setMinimumSize(1100, 700)
        app = QApplication.instance()
        if app is not None:
            try:
                app.installEventFilter(self)
            except Exception:
                pass
            try:
                app.aboutToQuit.connect(self._on_app_about_to_quit)
            except Exception:
                pass
        self._build_shell()
        self._schedule_session_index_warmup()
        self._refresh_welcome_state()
        self._show_welcome()
        startup_channel_starter = getattr(self, "_schedule_local_channel_autostart", None)
        if callable(startup_channel_starter):
            try:
                startup_channel_starter()
            except Exception:
                pass
        startup_lan_starter = getattr(self, "_schedule_lan_interface_autostart", None)
        if callable(startup_lan_starter):
            try:
                startup_lan_starter()
            except Exception:
                pass
        startup_update_checker = getattr(self, "_schedule_startup_update_check", None)
        if callable(startup_update_checker):
            try:
                startup_update_checker()
            except Exception:
                pass
        QTimer.singleShot(1800, self, self._startup_server_connection_probe)

    def _startup_server_connection_probe(self):
        if bool(getattr(self, "_closing_in_progress", False)):
            return
        try:
            self._request_server_connection_probe(force=True)
        except Exception:
            pass

    def _begin_window_trace(self, context: str, *, duration_ms: int = 2600, suppress_blank_dialogs: bool = False):
        duration = max(200, int(duration_ms or 2600))
        self._window_trace_context = str(context or "").strip()
        self._window_trace_until = time.time() + (duration / 1000.0)
        self._window_trace_suppress_blank_dialogs = bool(suppress_blank_dialogs)

    def _window_trace_active(self) -> bool:
        until = float(getattr(self, "_window_trace_until", 0.0) or 0.0)
        return until > 0 and time.time() <= until

    def _append_window_trace_log(self, line: str):
        text = str(line or "").strip()
        if not text:
            return
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            path = lz.launcher_data_path("ui_window_trace.log")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"[{stamp}] {text}\n")
        except Exception:
            pass

    def _request_app_quit(self):
        if bool(getattr(self, "_app_quit_requested", False)):
            return
        self._app_quit_requested = True
        app = QApplication.instance()
        if app is None:
            return
        try:
            QMetaObject.invokeMethod(app, "quit", Qt.QueuedConnection)
            return
        except Exception:
            pass
        try:
            QTimer.singleShot(0, app, app.quit)
            return
        except Exception:
            pass
        try:
            app.quit()
        except Exception:
            pass

    def _start_shutdown_cleanup(self):
        self._stop_background_activity_for_shutdown()
        self._close_tray_helpers()
        shutdown_items = self._detach_processes_for_shutdown()
        self._clear_attachments_for_shutdown_fast()
        if bool(getattr(self, "_shutdown_cleanup_started", False)):
            return
        self._shutdown_cleanup_started = True
        if not shutdown_items:
            self._request_app_quit()
            return
        threading.Thread(
            target=self._run_shutdown_process_cleanup,
            args=(shutdown_items,),
            name="launcher-shutdown-cleanup",
            daemon=False,
        ).start()

    def _on_app_about_to_quit(self):
        if bool(getattr(self, "_shutdown_cleanup_started", False)):
            return
        self._closing_in_progress = True
        self._force_exit_requested = True
        self._start_shutdown_cleanup()

    def _stop_background_activity_for_shutdown(self):
        for attr in ("_drain_timer", "_channel_snapshot_timer", "_server_status_timer", "_stream_flush_timer"):
            timer = getattr(self, attr, None)
            if timer is None:
                continue
            try:
                timer.stop()
            except Exception:
                pass
        self._server_status_probe_running = False
        self._server_status_probe_pending = False
        self._qt_api_remote_loading = False
        self._qt_channel_remote_loading = False
        self._settings_personal_remote_sync_running = False
        self._settings_usage_remote_sync_running = False
        self._remote_channel_sync_running = False
        self._remote_launcher_sync_running = False
        disconnect_terminal = getattr(self, "_disconnect_vps_terminal", None)
        if callable(disconnect_terminal):
            try:
                disconnect_terminal(reason="启动器正在关闭。")
            except Exception:
                pass

    def _clear_attachments_for_shutdown_fast(self):
        pending = list(getattr(self, "_pending_input_attachments_data", []) or [])
        active = list(getattr(self, "_active_turn_attachments_data", []) or [])
        self._pending_input_attachments_data = []
        self._active_turn_attachments_data = []
        releaser = getattr(self, "_release_attachment_files", None)
        if callable(releaser):
            try:
                releaser(pending + active)
            except Exception:
                pass

    def _append_shutdown_process(self, items, name: str, proc, log_handle=None, *, quit_line: str = ""):
        if log_handle is not None:
            try:
                log_handle.flush()
            except Exception:
                pass
        if proc is None:
            if log_handle is not None:
                try:
                    log_handle.close()
                except Exception:
                    pass
            return
        if quit_line:
            try:
                if proc.poll() is None and getattr(proc, "stdin", None) is not None:
                    proc.stdin.write(quit_line)
                    proc.stdin.flush()
            except Exception:
                pass
        items.append({"name": str(name or "process"), "proc": proc, "log_handle": log_handle})

    def _detach_processes_for_shutdown(self):
        items = []
        bridge_proc = getattr(self, "bridge_proc", None)
        self.bridge_proc = None
        self._bridge_ready = False
        self._append_shutdown_process(items, "bridge", bridge_proc, quit_line='{"cmd":"quit"}\n')

        channel_map = dict(getattr(self, "_channel_procs", {}) or {})
        self._channel_procs = {}
        for channel_id, info in channel_map.items():
            data = info if isinstance(info, dict) else {}
            self._append_shutdown_process(
                items,
                f"channel:{channel_id}",
                data.get("proc"),
                data.get("log_handle"),
            )
            data["log_handle"] = None

        scheduler_proc = getattr(self, "_scheduler_proc", None)
        scheduler_handle = getattr(self, "_scheduler_log_handle", None)
        self._scheduler_proc = None
        self._scheduler_log_handle = None
        self._append_shutdown_process(items, "scheduler", scheduler_proc, scheduler_handle)

        lan_proc = getattr(self, "_lan_interface_proc", None)
        lan_handle = getattr(self, "_lan_interface_log_handle", None)
        self._lan_interface_proc = None
        self._lan_interface_log_handle = None
        self._append_shutdown_process(items, "lan-interface", lan_proc, lan_handle)
        return items

    def _run_shutdown_process_cleanup(self, items):
        records = [item for item in (items or []) if isinstance(item, dict)]
        for item in records:
            proc = item.get("proc")
            if proc is None:
                continue
            try:
                lz.terminate_process_tree(proc, terminate_timeout=1.4, kill_timeout=1.4)
            except Exception:
                pass
        for item in records:
            proc = item.get("proc")
            handle = item.get("log_handle")
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass
        self._request_app_quit()

    def eventFilter(self, watched, event):
        if event is not None:
            try:
                et = event.type()
            except Exception:
                et = None
            if et in (QEvent.Show, QEvent.ShowToParent) and bool(getattr(self, "_closing_in_progress", False)):
                widget = watched if isinstance(watched, QWidget) else None
                if widget is not None:
                    try:
                        is_window = bool(widget.isWindow())
                    except Exception:
                        is_window = False
                    if is_window and (isinstance(widget, QMessageBox) or (isinstance(widget, QDialog) and (not str(widget.windowTitle() or "").strip()))):
                        try:
                            cls_name = str(widget.metaObject().className() or widget.__class__.__name__ or "").strip()
                        except Exception:
                            cls_name = widget.__class__.__name__
                        self._append_window_trace_log(f"context=shutdown suppressed_dialog class={cls_name or '-'}")
                        QTimer.singleShot(0, lambda w=widget: (w.hide(), getattr(w, "reject", lambda: None)(), w.deleteLater()))
                        return True
            if et in (QEvent.Show, QEvent.ShowToParent) and self._window_trace_active():
                widget = watched if isinstance(watched, QWidget) else None
                if widget is not None:
                    try:
                        is_window = bool(widget.isWindow())
                    except Exception:
                        is_window = False
                    if is_window:
                        title = str(widget.windowTitle() or "").strip()
                        object_name = str(widget.objectName() or "").strip()
                        cls_name = str(widget.metaObject().className() or widget.__class__.__name__ or "").strip()
                        context = str(getattr(self, "_window_trace_context", "") or "").strip()
                        focus_widget = None
                        try:
                            focus_widget = QApplication.focusWidget()
                        except Exception:
                            focus_widget = None
                        focus_cls = ""
                        focus_name = ""
                        if focus_widget is not None:
                            try:
                                focus_cls = str(focus_widget.metaObject().className() or focus_widget.__class__.__name__ or "").strip()
                            except Exception:
                                focus_cls = ""
                            try:
                                focus_name = str(focus_widget.objectName() or "").strip()
                            except Exception:
                                focus_name = ""
                        combo_owner_name = ""
                        combo_owner = focus_widget
                        while combo_owner is not None:
                            try:
                                if isinstance(combo_owner, QComboBox):
                                    combo_owner_name = str(combo_owner.objectName() or "").strip()
                                    break
                            except Exception:
                                pass
                            try:
                                combo_owner = combo_owner.parentWidget()
                            except Exception:
                                combo_owner = None
                        watched_combo_owner = watched if isinstance(watched, QWidget) else None
                        watched_combo_name = ""
                        while watched_combo_owner is not None:
                            try:
                                if isinstance(watched_combo_owner, QComboBox):
                                    watched_combo_name = str(watched_combo_owner.objectName() or "").strip()
                                    break
                            except Exception:
                                pass
                            try:
                                watched_combo_owner = watched_combo_owner.parentWidget()
                            except Exception:
                                watched_combo_owner = None
                        self._append_window_trace_log(
                            f"context={context or '-'} class={cls_name or '-'} object={object_name or '-'} title={title or '-'} focus={focus_cls or '-'} focus_object={focus_name or '-'} focus_combo={combo_owner_name or '-'} watched_combo={watched_combo_name or '-'}"
                        )
                        if (
                            bool(getattr(self, "_window_trace_suppress_blank_dialogs", False))
                            and isinstance(widget, QDialog)
                            and (not title)
                            and cls_name in ("QDialog", "QMessageBox")
                        ):
                            self._append_window_trace_log(
                                f"context={context or '-'} suppressed_blank_dialog class={cls_name or '-'} object={object_name or '-'}"
                            )
                            self._set_status(f"已抑制远端渠道页空白弹窗：{cls_name or 'QDialog'}")
                            QTimer.singleShot(0, lambda w=widget: (w.hide(), w.reject()))
                            return True
        return super().eventFilter(watched, event)

    AUTO_TASK_TEXT = "[AUTO]🤖 用户已经离开超过30分钟，作为自主智能体，请阅读自动化sop，执行自动任务。"
    def _schedule_session_index_warmup(self):
        if self._session_index_warmup_started:
            return
        if not lz.is_valid_agent_dir(self.agent_dir):
            return
        self._session_index_warmup_started = True
        target_dir = str(self.agent_dir or "")

        def _worker():
            try:
                lz.list_sessions(target_dir)
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child = item.layout()
            if child is not None:
                self._clear_layout(child)
            if widget is not None:
                widget.deleteLater()

    def _build_ui(self):
        from launcher_app import theme as qt_theme

        chat_bg = qt_theme.chat_surface_background()
        body_fs = qt_theme.font_body_size()
        root = QSplitter()
        root.setOrientation(Qt.Horizontal)
        root.setHandleWidth(1)

        self.sidebar_host = QFrame()
        self.sidebar_host.setObjectName("chatSidebar")
        self.sidebar_layout = QVBoxLayout(self.sidebar_host)
        self.sidebar_layout.setContentsMargins(14, 14, 14, 14)
        self.sidebar_layout.setSpacing(6)
        self._rebuild_sidebar()

        main = QFrame()
        main.setObjectName("chatMain")
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        head = QFrame()
        head.setObjectName("chatHead")
        head.setFixedHeight(F["topbar_h"])
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(18, 0, 18, 0)
        head_layout.setSpacing(12)

        self.chat_title = QLabel("GenericAgent")
        self.chat_title.setObjectName("cardTitle")
        self.mode_label = QLabel("当前无活动会话")
        self.mode_label.setObjectName("mutedText")
        self.mode_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        head_layout.addWidget(self.chat_title, 0, Qt.AlignVCenter)
        head_layout.addStretch(1)
        self.server_status_btn = QPushButton("●")
        self.server_status_btn.setCursor(Qt.PointingHandCursor)
        self.server_status_btn.setFixedSize(36, 32)
        self.server_status_btn.installEventFilter(self)
        self.server_status_btn.clicked.connect(self._on_server_status_clicked)
        head_layout.addWidget(self.server_status_btn, 0, Qt.AlignRight | Qt.AlignVCenter)
        self._refresh_server_status_indicator()
        self.theme_btn = QPushButton("☀")
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.setFixedSize(36, 32)
        self.theme_btn.setStyleSheet(self._sidebar_button_style())
        self.theme_btn.clicked.connect(self._toggle_appearance_mode)
        head_layout.addWidget(self.theme_btn, 0, Qt.AlignRight | Qt.AlignVCenter)
        self._refresh_theme_button()
        self.gear_btn = QPushButton("⚙")
        self.gear_btn.setCursor(Qt.PointingHandCursor)
        self.gear_btn.setFixedSize(36, 32)
        self.gear_btn.setStyleSheet(self._sidebar_button_style())
        self.gear_btn.clicked.connect(self._open_functions_menu)
        head_layout.addWidget(self.gear_btn, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.mode_label.hide()

        main_layout.addWidget(head)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("chatScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {chat_bg}; }}" + SCROLLBAR_STYLE)
        viewport = self.scroll.viewport()
        if viewport is not None:
            viewport.setStyleSheet(f"background: {chat_bg};")
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        self.scroll.viewport().installEventFilter(self)
        self.msg_root = QWidget()
        self.msg_root.setObjectName("chatMsgRoot")
        self.msg_root.setStyleSheet(f"background: {chat_bg};")
        self.msg_layout = QVBoxLayout(self.msg_root)
        self.msg_layout.setContentsMargins(0, 12, 0, 12)
        self.msg_layout.setSpacing(4)
        self.msg_layout.addStretch(1)
        self.scroll.setWidget(self.msg_root)
        main_layout.addWidget(self.scroll, 1)

        self.jump_latest_btn = QPushButton(self.scroll.viewport())
        self.jump_latest_btn.setObjectName("jumpLatestBtn")
        self.jump_latest_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.jump_latest_btn.setToolTip("跳到最新对话")
        self.jump_latest_btn.setFixedSize(36, 36)
        self.jump_latest_btn.setIcon(_svg_icon("jump_latest", _SVG_CHEVRON_DOWN, color=C['text'], size=16))
        self.jump_latest_btn.setIconSize(QSize(16, 16))
        self.jump_latest_btn.setStyleSheet(self._jump_latest_button_style())
        self.jump_latest_btn.clicked.connect(self._jump_to_latest_dialogue)
        self.jump_latest_btn.hide()
        self.jump_latest_btn.raise_()

        token_bar = QHBoxLayout()
        token_bar.setContentsMargins(0, 0, 18, 8)
        token_bar.setSpacing(0)
        token_bar.addStretch(1)

        self.session_token_tree_label = QLabel("↑0  ↓0")
        self.session_token_tree_label.setObjectName("tokenTree")
        self.session_token_tree_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.session_token_tree_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.session_token_tree_label.hide()
        token_bar.addWidget(self.session_token_tree_label, 0, Qt.AlignRight | Qt.AlignVCenter)
        main_layout.addLayout(token_bar)

        msg_separator = QFrame()
        msg_separator.setFixedHeight(1)
        msg_separator.setObjectName("msgSeparator")
        main_layout.addWidget(msg_separator)

        footer = QFrame()
        footer.setObjectName("chatMain")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(16, 10, 16, 14)
        footer_layout.setSpacing(0)

        composer = QFrame()
        composer.setObjectName("chatComposer")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(12, 10, 12, 8)
        composer_layout.setSpacing(8)

        self.input_box = InputTextEdit(self._handle_send, image_cb=self._handle_input_image_attachments)
        self.input_box.setPlaceholderText("输入消息，Enter 发送，Shift+Enter 换行")
        self.input_box.setStyleSheet(
            f"QTextEdit {{ background: transparent; border: none; color: {C['text']}; font-size: {body_fs}px; padding: 2px; }}"
        )
        self.input_box.setMinimumHeight(44)
        self.input_box.setMaximumHeight(110)
        composer_layout.addWidget(self.input_box)

        self.input_attachment_host = QFrame()
        self.input_attachment_host.setObjectName("cardInset")
        self.input_attachment_host.setVisible(False)
        self.input_attachment_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        attachment_box = QVBoxLayout(self.input_attachment_host)
        attachment_box.setContentsMargins(12, 10, 12, 10)
        attachment_box.setSpacing(8)
        self.input_attachment_summary = QLabel("")
        self.input_attachment_summary.setObjectName("softTextSmall")
        self.input_attachment_summary.setWordWrap(True)
        attachment_box.addWidget(self.input_attachment_summary)
        self.input_attachment_list_widget = QWidget()
        self.input_attachment_list_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.input_attachment_list_layout = QVBoxLayout(self.input_attachment_list_widget)
        self.input_attachment_list_layout.setContentsMargins(0, 0, 0, 0)
        self.input_attachment_list_layout.setSpacing(8)
        attachment_box.addWidget(self.input_attachment_list_widget)
        composer_layout.addWidget(self.input_attachment_host)

        tool_row = QHBoxLayout()
        tool_row.setSpacing(8)

        self.llm_combo = QComboBox()
        self.llm_combo.setMinimumWidth(220)
        self.llm_combo.setMaximumWidth(320)
        self.llm_combo.currentIndexChanged.connect(self._on_llm_changed)
        tool_row.addWidget(self.llm_combo)
        tool_row.addStretch(1)

        self.info_btn = QPushButton()
        self.info_btn.setObjectName("infoBtn")
        self.info_btn.setIcon(_svg_icon("info_btn", _SVG_INFO, color=C['muted'], size=14))
        self.info_btn.setIconSize(QSize(14, 14))
        self.info_btn.setFixedSize(26, 26)
        self.info_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.info_btn.setToolTip("")
        self.info_btn.installEventFilter(self)
        tool_row.addWidget(self.info_btn)

        self._info_popup = QLabel(self, Qt.ToolTip | Qt.FramelessWindowHint)
        self._info_popup.setObjectName("infoPopup")
        self._info_popup.setStyleSheet(
            f"QLabel#infoPopup {{ background: {C['panel']}; color: {C['text']};"
            f" border: 1px solid {C['border']}; padding: 8px 10px;"
            f" border-radius: 6px; font-size: 12px; }}"
        )
        self._info_popup.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._info_popup.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._info_popup.setWordWrap(True)
        self._info_popup.hide()

        self.stop_btn = QPushButton("  中断")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setIcon(_svg_icon("stop_btn", _SVG_STOP, color=C['danger_text'], size=14))
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._abort)
        tool_row.addWidget(self.stop_btn)

        self.send_btn = QPushButton("  发送")
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.setIcon(_svg_icon("send_btn", _SVG_SEND, color="#ffffff", size=14))
        self.send_btn.clicked.connect(self._handle_send)
        tool_row.addWidget(self.send_btn)

        composer_layout.addLayout(tool_row)

        self.session_mode_label = QLabel("当前会话：新进程，尚未发送消息")
        self.session_mode_label.setObjectName("mutedText")
        self.session_mode_label.hide()

        self.status_label = QLabel("正在启动桥接进程…")
        self.status_label.setObjectName("mutedText")
        self.status_label.setWordWrap(True)
        self.status_label.hide()

        self.token_label = self.session_token_tree_label
        self._refresh_info_tooltip()
        footer_layout.addWidget(composer)
        main_layout.addWidget(footer)

        root.addWidget(self.sidebar_host)
        root.addWidget(main)
        root.setStretchFactor(0, 0)
        root.setStretchFactor(1, 1)
        root.setSizes([84 if self.sidebar_collapsed else 280, 1160])
        return root

    def _update_stream_row_tokens(self, *, live: bool):
        row = getattr(self, "_stream_row", None)
        if row is None:
            return
        summary_getter = getattr(self, "_single_turn_token_summary", None)
        if not callable(summary_getter):
            return
        summary = summary_getter(include_live=bool(live))
        output_tokens = int(summary.get("live_output_tokens", 0) or summary.get("output_tokens", 0) or 0)
        row.set_token_info(
            int(summary.get("input_tokens", 0) or 0),
            output_tokens,
            live=bool(live and summary.get("live_output_tokens", 0)),
        )

    def _ensure_launcher_tray_icon(self):
        tray = self._launcher_tray_icon or getattr(self, "_reply_notify_tray", None)
        if tray is None:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                return None
            icon = self.windowIcon()
            if icon is None or icon.isNull():
                app = QApplication.instance()
                if app is not None:
                    icon = app.windowIcon()
            if icon is None or icon.isNull():
                icon = self.style().standardIcon(QStyle.SP_ComputerIcon)
            tray = QSystemTrayIcon(icon, self)
            tray.setToolTip("GenericAgent 启动器")
            tray.activated.connect(self._on_launcher_tray_activated)
        if self._launcher_tray_menu is None:
            menu = QMenu(self)
            restore_action = menu.addAction("恢复完整界面")
            restore_action.triggered.connect(self._restore_from_tray_mode)
            show_floating_action = menu.addAction("显示悬浮窗")
            show_floating_action.triggered.connect(self._show_floating_chat_window)
            hide_floating_action = menu.addAction("隐藏悬浮窗")
            hide_floating_action.triggered.connect(self._hide_floating_chat_window)
            menu.addSeparator()
            exit_action = menu.addAction("退出启动器")
            exit_action.triggered.connect(self._quit_from_tray)
            tray.setContextMenu(menu)
            self._launcher_tray_menu = menu
            self._tray_restore_main_action = restore_action
            self._tray_show_floating_action = show_floating_action
            self._tray_hide_floating_action = hide_floating_action
            self._tray_exit_action = exit_action
        tray.show()
        self._launcher_tray_icon = tray
        self._reply_notify_tray = tray
        self._refresh_launcher_tray_menu()
        return tray

    def _refresh_launcher_tray_menu(self):
        visible = bool(getattr(self._floating_chat_window, "isVisible", lambda: False)())
        hidden_main = not self.isVisible()
        if self._tray_restore_main_action is not None:
            self._tray_restore_main_action.setVisible(hidden_main or self._tray_mode_active)
        if self._tray_show_floating_action is not None:
            self._tray_show_floating_action.setVisible(not visible)
        if self._tray_hide_floating_action is not None:
            self._tray_hide_floating_action.setVisible(visible)

    def _ensure_floating_chat_window(self):
        win = getattr(self, "_floating_chat_window", None)
        if win is None:
            win = FloatingOrbWindow(self)
            self._floating_chat_window = win
            win.apply_theme()
            self._refresh_input_attachment_bar()
            self._sync_floating_llm_combo()
            self._sync_floating_session_list()
            self._sync_draft_to_floating()
        return win

    def _save_floating_orb_position(self, pos: QPoint):
        if not isinstance(pos, QPoint):
            return
        self.cfg["floating_orb_pos"] = {"x": int(pos.x()), "y": int(pos.y())}
        lz.save_config(self.cfg)

    def _load_floating_orb_position(self):
        data = self.cfg.get("floating_orb_pos")
        if not isinstance(data, dict):
            return None
        try:
            return QPoint(int(data.get("x")), int(data.get("y")))
        except Exception:
            return None

    def _sync_floating_llm_combo(self):
        floating = getattr(self, "_floating_chat_window", None)
        if floating is None or not hasattr(floating, "sync_llm_items"):
            return
        disabled = self._is_channel_process_session()
        current_idx = -1
        for pos, llm in enumerate(self.llms):
            if llm.get("current"):
                current_idx = pos
                break
        if current_idx < 0:
            try:
                current_idx = int(self.llm_combo.currentIndex())
            except Exception:
                current_idx = 0
        floating.sync_llm_items(
            self.llms,
            enabled=((not disabled) and bool(self.llms) and (not self._busy)),
            current_index=current_idx,
        )

    def _on_floating_llm_changed(self, index: int):
        if getattr(self, "_ignore_llm_change", False):
            return
        if index < 0 or getattr(self, "llm_combo", None) is None:
            return
        self._ignore_llm_change = True
        try:
            self.llm_combo.setCurrentIndex(index)
        finally:
            self._ignore_llm_change = False
        self._on_llm_changed(index)

    def _sync_draft_to_floating(self):
        floating = getattr(self, "_floating_chat_window", None)
        if floating is None:
            return
        source = getattr(self, "input_box", None)
        target = getattr(floating, "input_box", None)
        if source is None or target is None:
            return
        main_text = str(source.toPlainText() or "")
        floating_text = str(target.toPlainText() or "")
        if main_text and not floating_text:
            target.setPlainText(main_text)

    def _sync_draft_from_floating(self):
        floating = getattr(self, "_floating_chat_window", None)
        if floating is None:
            return
        source = getattr(floating, "input_box", None)
        target = getattr(self, "input_box", None)
        if source is None or target is None:
            return
        floating_text = str(source.toPlainText() or "")
        main_text = str(target.toPlainText() or "")
        if floating_text and floating_text != main_text:
            target.setPlainText(floating_text)

    def _floating_recent_session_rows(self):
        if not lz.is_valid_agent_dir(self.agent_dir):
            return []
        current_sid = str((self.current_session or {}).get("id") or "")
        rows = []
        for meta in lz.list_sessions(self.agent_dir):
            sid = str(meta.get("id") or "")
            if not sid:
                continue
            current_data = self.current_session if (sid == current_sid and isinstance(self.current_session, dict)) else {}
            rows.append(
                {
                    "id": sid,
                    "title": str((current_data or {}).get("title") or meta.get("title") or "(未命名)").strip() or "(未命名)",
                    "channel_label": str(
                        (current_data or {}).get("channel_label")
                        or meta.get("channel_label")
                        or lz._usage_channel_label((current_data or {}).get("channel_id") or meta.get("channel_id") or "launcher")
                    ).strip(),
                    "updated_at": float((current_data or {}).get("updated_at", meta.get("updated_at", 0)) or 0),
                    "pinned": bool((current_data or {}).get("pinned", meta.get("pinned", False))),
                }
            )
        rows.sort(key=lambda row: (bool(row.get("pinned", False)), float(row.get("updated_at", 0) or 0)), reverse=True)
        if not current_sid:
            return rows[:12]
        current_row = None
        others = []
        for row in rows:
            if str(row.get("id") or "") == current_sid:
                current_row = row
            else:
                others.append(row)
        if current_row is None:
            return rows[:12]
        return [current_row] + others[:11]

    def _ensure_floating_default_session(self):
        if self._busy:
            return
        if isinstance(self.current_session, dict) and str(self.current_session.get("id") or "").strip():
            return
        rows = self._floating_recent_session_rows()
        if not rows:
            return
        sid = str((rows[0] or {}).get("id") or "").strip()
        if not sid:
            return
        self._load_session_by_id(sid)
        self._last_session_list_signature = None
        self._refresh_sessions()

    def _sync_floating_session_list(self):
        floating = getattr(self, "_floating_chat_window", None)
        if floating is None or not hasattr(floating, "sync_session_items"):
            return
        rows = self._floating_recent_session_rows()
        floating.sync_session_items(
            rows,
            current_session_id=str((self.current_session or {}).get("id") or ""),
            enabled=(not self._busy),
        )

    def _on_floating_session_changed(self, index: int):
        if self._busy:
            QMessageBox.information(self, "忙碌中", "当前还在生成，请先等待结束或手动中断。")
            self._sync_floating_session_list()
            return
        floating = getattr(self, "_floating_chat_window", None)
        if floating is None:
            return
        sid = str(floating.session_combo.itemData(index) or "")
        if not sid:
            return
        current_sid = str((self.current_session or {}).get("id") or "")
        if sid == current_sid:
            return
        self._load_session_by_id(sid)
        self._last_session_list_signature = None
        self._refresh_sessions()
        self._refresh_floating_chat_window()

    def _new_session_from_floating(self):
        self._new_session()
        self._refresh_floating_chat_window()

    def _open_floating_session_menu(self, anchor):
        menu = QMenu(self)
        current = self.current_session or {}
        current_sid = str(current.get("id") or "").strip()
        pinned = bool(current.get("pinned", False))
        if current_sid:
            pin_action = menu.addAction("取消收藏当前会话" if pinned else "收藏当前会话")
            delete_action = menu.addAction("删除当前会话")
            menu.addSeparator()
        else:
            pin_action = None
            delete_action = None
        refresh_action = menu.addAction("刷新会话列表")
        chosen = menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        if chosen is None:
            return
        if chosen is refresh_action:
            self._last_session_list_signature = None
            self._refresh_sessions()
            self._refresh_floating_chat_window()
            return
        if not current_sid:
            return
        row = {"id": current_sid}
        if chosen is pin_action:
            self._set_sidebar_sessions_pinned([row], not pinned)
            if isinstance(self.current_session, dict) and str(self.current_session.get("id") or "") == current_sid:
                self.current_session["pinned"] = not pinned
            self._refresh_floating_chat_window()
            return
        if chosen is delete_action:
            self._delete_sidebar_sessions([row])
            self._refresh_floating_chat_window()

    def _regenerate_latest_from_floating(self):
        if self._busy:
            QMessageBox.information(self, "忙碌中", "当前任务还在运行，请稍候。")
            return
        bubbles = list((self.current_session or {}).get("bubbles") or [])
        user_text = ""
        for bubble in reversed(bubbles):
            role = str(bubble.get("role") or "").strip().lower()
            if role == "user" and str(bubble.get("text") or "").strip():
                user_text = str(bubble.get("text") or "").strip()
                break
        if not user_text:
            QMessageBox.information(self, "无法重试", "当前会话里没有可重试的用户消息。")
            return
        if self._submit_user_message(user_text, attachments=None, source_editor=None):
            self._set_status("已从悬浮窗重试最近一条用户消息。")
            self._refresh_floating_chat_window()

    def _floating_chat_title(self):
        session = self.current_session or {}
        title = str(session.get("title") or "").strip()
        if title:
            return title
        if self._is_channel_process_session(session):
            return "渠道进程快照"
        return "新对话"

    def _floating_chat_subtitle(self):
        session = self.current_session or {}
        parts = []
        channel_label = str(
            session.get("channel_label")
            or lz._usage_channel_label(session.get("channel_id") or "launcher")
        ).strip()
        if channel_label:
            parts.append(channel_label)
        if self._is_channel_process_session(session):
            parts.append("只读快照")
        elif self._bridge_ready:
            parts.append("内核已就绪")
        else:
            parts.append("内核启动中")
        return " | ".join(parts) or "发送消息后会继续沿用当前会话。"

    def _floating_chat_meta(self):
        parts = []
        model = str(self._current_llm_name() or "").strip()
        if model:
            parts.append(model)
        if self._busy:
            parts.append("生成中")
        elif self._abort_requested:
            parts.append("中断中")
        else:
            parts.append("可发送")
        return " | ".join(parts)

    def _floating_chat_transcript(self):
        session = self.current_session or {}
        bubbles = list(session.get("bubbles") or [])
        if not bubbles and not self._busy:
            if self._is_channel_process_session(session):
                return "当前是渠道进程快照，会话只读。"
            return "当前还没有消息。\n\n直接在这里输入内容，发送后会自动创建新会话。"
        rows = []
        for bubble in bubbles:
            role = str(bubble.get("role") or "assistant").strip().lower()
            label = "我" if role == "user" else "AI"
            text = str(bubble.get("text") or "").strip() or "…"
            rows.append(f"{label}\n{text}")
        if self._busy:
            pending = str(self._pending_stream_text or self._current_stream_text or "").strip() or "…"
            rows.append(f"AI\n{pending}")
        return "\n\n".join(rows)

    def _refresh_floating_chat_window(self):
        win = getattr(self, "_floating_chat_window", None)
        if win is None:
            return
        disabled = self._is_channel_process_session()
        status = str(getattr(self, "status_label", None).text() if getattr(self, "status_label", None) is not None else "").strip()
        if not status:
            status = "已隐藏主窗口，悬浮窗可继续对话。"
        can_send = (not disabled) and (not self._busy)
        can_abort = (not disabled) and self._busy and (not self._abort_requested)
        if isinstance(win, FloatingOrbWindow):
            session = self.current_session or {}
            bubbles = list(session.get("bubbles") or [])
            stream_text = str(self._pending_stream_text or self._current_stream_text or "")
            win.sync_view(
                title=self._floating_chat_title(),
                subtitle=self._floating_chat_subtitle(),
                bubbles=bubbles,
                stream_text=stream_text,
                status=status,
                meta=self._floating_chat_meta(),
                can_send=can_send,
                can_abort=can_abort,
                read_only=disabled,
            )
            self._sync_floating_llm_combo()
            self._sync_floating_session_list()
            self._sync_draft_to_floating()
        else:
            win.sync_view(
                title=self._floating_chat_title(),
                subtitle=self._floating_chat_subtitle(),
                transcript=self._floating_chat_transcript(),
                status=status,
                meta=self._floating_chat_meta(),
                can_send=can_send,
                can_abort=can_abort,
                read_only=disabled,
            )
        self._refresh_launcher_tray_menu()

    def _show_floating_chat_window(self):
        tray = self._ensure_launcher_tray_icon()
        if tray is None:
            QMessageBox.warning(self, "无法缩小到托盘", "当前系统不支持托盘图标。")
            return
        self._ensure_floating_default_session()
        win = self._ensure_floating_chat_window()
        self._tray_mode_active = True
        self._refresh_floating_chat_window()
        win.show()
        win.raise_()
        win.activateWindow()
        tray.show()
        self._refresh_launcher_tray_menu()

    def _show_floating_chat_window_only(self):
        self._tray_restore_to_fullscreen = bool(self.isFullScreen())
        self._show_floating_chat_window()
        self.hide()
        self._tray_mode_active = True
        self._refresh_launcher_tray_menu()

    def _enter_tray_floating_mode(self):
        tray = self._ensure_launcher_tray_icon()
        if tray is None:
            QMessageBox.warning(self, "无法缩小到托盘", "当前系统不支持托盘图标。")
            return
        self._show_floating_chat_window_only()
        if not self._tray_mode_hint_shown:
            try:
                tray.showMessage("GenericAgent 启动器", "主窗口已缩小到托盘，继续使用右下角悬浮对话窗即可。", QSystemTrayIcon.Information, 1500)
            except Exception:
                pass
            self._tray_mode_hint_shown = True

    def _hide_floating_chat_window(self):
        win = getattr(self, "_floating_chat_window", None)
        if win is not None:
            win.hide()
        self._tray_mode_active = True
        self._refresh_launcher_tray_menu()

    def _restore_from_tray_mode(self):
        if (not self._tray_mode_active) and self.isVisible():
            return
        restore_fullscreen = bool(self._tray_restore_to_fullscreen)
        self._tray_restore_to_fullscreen = False
        self._tray_mode_active = False
        self._sync_draft_from_floating()
        win = getattr(self, "_floating_chat_window", None)
        if win is not None:
            win.hide()
        if restore_fullscreen:
            self.showFullScreen()
        else:
            self.showNormal()
        self.raise_()
        self.activateWindow()
        self._show_chat_page()
        self._refresh_floating_chat_window()
        self._refresh_launcher_tray_menu()

    def _handle_floating_send(self):
        win = getattr(self, "_floating_chat_window", None)
        if win is None:
            return
        text = win.input_box.toPlainText().strip()
        attachments = self._pending_input_attachments()
        if self._submit_user_message(text, attachments=attachments, source_editor=win.input_box):
            if getattr(self, "input_box", None) is not None:
                self.input_box.clear()
            self._refresh_floating_chat_window()

    def _on_launcher_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if self.isVisible() and not self._tray_mode_active:
                self._show_floating_chat_window_only()
            elif getattr(self._floating_chat_window, "isVisible", lambda: False)():
                self._restore_from_tray_mode()
            else:
                self._show_floating_chat_window()

    def _close_tray_helpers(self):
        win = getattr(self, "_floating_chat_window", None)
        if win is not None:
            win.hide()
            win.deleteLater()
            self._floating_chat_window = None
        tray = getattr(self, "_launcher_tray_icon", None)
        if tray is not None:
            try:
                tray.hide()
            except Exception:
                pass
        self._launcher_tray_icon = None
        self._launcher_tray_menu = None
        self._tray_restore_main_action = None
        self._tray_show_floating_action = None
        self._tray_hide_floating_action = None
        self._tray_exit_action = None

    def _quit_from_tray(self):
        if bool(getattr(self, "_closing_in_progress", False)):
            return
        self._force_exit_requested = True
        self._tray_mode_active = False
        self.close()

    def closeEvent(self, event):
        if bool(getattr(self, "_closing_in_progress", False)):
            event.accept()
            return
        self._closing_in_progress = True
        self._force_exit_requested = True
        try:
            self.hide()
        except Exception:
            pass
        event.accept()
        self._start_shutdown_cleanup()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        placer = getattr(self, "_place_jump_latest_button", None)
        if callable(placer):
            placer()


def main(agent_dir: str | None = None) -> int:
    from launcher_app import theme as qt_theme

    target = agent_dir if agent_dir is not None else (sys.argv[1] if len(sys.argv) > 1 else None)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("GenericAgent Launcher")
    loaded_cfg = lz.load_config()
    cfg = loaded_cfg if isinstance(loaded_cfg, dict) else {}
    mode = "light" if str(cfg.get("appearance_mode", "light") or "").strip().lower() == "light" else "dark"
    qt_theme.set_theme(mode)
    qt_theme.configure_visual_preferences(cfg)
    chat_common.set_md_css(chat_common._build_md_css())
    app.setStyleSheet(qt_theme.build_qss())
    try:
        win = QtChatWindow(target)
    except Exception as e:
        QMessageBox.critical(None, "启动失败", str(e))
        return 1
    win.show()
    apply_mica(win, dark=(mode == "dark"))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

