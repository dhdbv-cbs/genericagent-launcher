from __future__ import annotations

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
from datetime import datetime

from PySide6.QtCore import QByteArray, QEvent, QSize, Qt, QTimer, qInstallMessageHandler
from PySide6.QtGui import QColor, QCursor, QIcon, QKeyEvent, QPainter, QPixmap, QTextCursor
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
    QTextBrowser,
    QTextEdit,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

import launcher_core as lz
from qt_theme import C, F, FLUENT_QSS, apply_fluent_shadow, apply_mica

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
from qt_chat_parts.downloads import DownloadMixin
from qt_chat_parts.navigation import NavigationMixin
from qt_chat_parts.personal_usage import PersonalUsageMixin
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
    sys.stderr.write(text + "\n")


qInstallMessageHandler(_qt_message_handler)

class QtChatWindow(ApiEditorMixin, ChannelRuntimeMixin, PersonalUsageMixin, WindowShellMixin, BridgeRuntimeMixin, SessionShellMixin, NavigationMixin, ChatViewMixin, SetupPagesMixin, SettingsPanelMixin, DownloadMixin, SidebarSessionsMixin, QMainWindow):
    def __init__(self, agent_dir: str | None = None):
        super().__init__()
        self.cfg = lz.load_config() if isinstance(lz.load_config(), dict) else {}
        initial_dir = str(agent_dir or self.cfg.get("agent_dir") or "").strip()
        self.agent_dir = os.path.abspath(initial_dir) if initial_dir else ""
        self.install_parent = str(self.cfg.get("install_parent") or os.path.expanduser("~")).strip() or os.path.expanduser("~")
        self.sidebar_collapsed = bool(self.cfg.get("sidebar_collapsed", False))
        self._session_filter_keyword = ""
        self._sidebar_view_mode = "channels"
        self._sidebar_channel_id = "launcher"
        self._download_running = False
        self._download_mode = ""

        self.bridge_proc = None
        self._stderr_buf = []
        self._event_queue: queue.Queue = queue.Queue()
        self._drain_timer = QTimer(self)
        self._drain_timer.timeout.connect(self._drain_events)
        self._drain_timer.start(40)
        self._channel_snapshot_timer = QTimer(self)
        self._channel_snapshot_timer.timeout.connect(self._sync_all_channel_process_sessions)
        self._channel_snapshot_timer.start(2000)

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
        self._state_request_seq = 0
        self._active_token_event_ts = None
        self._current_stream_text = ""
        self._pending_stream_text = None
        self._stream_row = None
        self._rendered_message_rows = []
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
        self._last_session_list_signature = None

        self.setWindowTitle("GenericAgent 启动器")
        self.resize(1440, 920)
        self.setMinimumSize(1100, 700)
        self._build_shell()
        self._refresh_welcome_state()
        self._show_welcome()

    AUTO_TASK_TEXT = "[AUTO]🤖 用户已经离开超过30分钟，作为自主智能体，请阅读自动化sop，执行自动任务。"
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
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {C['bg']}; }}" + SCROLLBAR_STYLE)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        self.msg_root = QWidget()
        self.msg_root.setStyleSheet(f"background: {C['bg']};")
        self.msg_layout = QVBoxLayout(self.msg_root)
        self.msg_layout.setContentsMargins(0, 12, 0, 12)
        self.msg_layout.setSpacing(4)
        self.msg_layout.addStretch(1)
        self.scroll.setWidget(self.msg_root)
        main_layout.addWidget(self.scroll, 1)

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

        self.input_box = InputTextEdit(self._handle_send)
        self.input_box.setPlaceholderText("输入消息，Enter 发送，Shift+Enter 换行")
        self.input_box.setStyleSheet(f"QTextEdit {{ background: transparent; border: none; color: {C['text']}; font-size: 14px; padding: 2px; }}")
        self.input_box.setMinimumHeight(88)
        self.input_box.setMaximumHeight(220)
        composer_layout.addWidget(self.input_box)

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
        return

    def closeEvent(self, event):
        self._stop_bridge()
        self._stop_all_managed_channels(refresh=False)
        super().closeEvent(event)


def main(agent_dir: str | None = None) -> int:
    import qt_theme
    target = agent_dir if agent_dir is not None else (sys.argv[1] if len(sys.argv) > 1 else None)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("GenericAgent Launcher")
    cfg = lz.load_config() if isinstance(lz.load_config(), dict) else {}
    mode = "light" if str(cfg.get("appearance_mode", "light") or "").strip().lower() == "light" else "dark"
    qt_theme.set_theme(mode)
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
