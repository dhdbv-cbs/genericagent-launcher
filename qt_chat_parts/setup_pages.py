from __future__ import annotations

import os
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from launcher_app import core as lz

from . import common as chat_common
from .common import NoWheelComboBox, OptionCard, _probe_download_requirements

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


class SetupPagesMixin:
    def _build_setup_scroll_body(self, *, margins=(36, 28, 36, 28), spacing: int = 16):
        body_scroll = QScrollArea()
        body_scroll.setWidgetResizable(True)
        body_scroll.setFrameShape(QFrame.NoFrame)
        body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }" + _SCROLLBAR_STYLE)

        body = QWidget()
        body.setStyleSheet("background: transparent;")
        body_scroll.setWidget(body)

        viewport = body_scroll.viewport()
        if viewport is not None:
            viewport.setStyleSheet("background: transparent;")

        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(*margins)
        body_layout.setSpacing(spacing)
        return body_scroll, body, body_layout

    def _refresh_recent_directory_card_layout(self, *, defer: bool = False, attempt: int = 0):
        if defer:
            QTimer.singleShot(0, self, lambda: self._refresh_recent_directory_card_layout(attempt=attempt + 1))
            return
        label = getattr(self, "recent_path_label", None)
        card = getattr(self, "recent_card", None)
        if label is None or card is None:
            return
        try:
            label.updateGeometry()
        except Exception:
            pass
        layout = card.layout()
        if layout is not None:
            try:
                layout.invalidate()
                layout.activate()
            except Exception:
                pass
        target_height = max(78, int(card.sizeHint().height() or 0))
        if card.minimumHeight() != target_height:
            card.setMinimumHeight(target_height)
        current = card
        seen = set()
        welcome_page = getattr(self, "_welcome_page", None)
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            try:
                if current.layout() is not None:
                    current.layout().activate()
            except Exception:
                pass
            try:
                current.updateGeometry()
                if current is not welcome_page:
                    current.adjustSize()
                current.update()
            except Exception:
                pass
            if current is welcome_page:
                break
            current = current.parentWidget()
        pages = getattr(self, "pages", None)
        if pages is not None:
            try:
                pages.updateGeometry()
                pages.update()
            except Exception:
                pass
        actual_height = int(card.height() or 0)
        desired_height = max(78, int(card.sizeHint().height() or 0))
        if actual_height < desired_height and attempt < 2:
            QTimer.singleShot(0, self, lambda: self._refresh_recent_directory_card_layout(attempt=attempt + 1))

    def _build_welcome_page(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        body_scroll, body, layout = self._build_setup_scroll_body(margins=(40, 32, 40, 28), spacing=14)
        self.welcome_body_scroll = body_scroll
        self.welcome_body_widget = body
        page_layout.addWidget(body_scroll, 1)

        brand = QWidget()
        brand_row = QHBoxLayout(brand)
        brand_row.setContentsMargins(0, 6, 0, 22)
        brand_row.setSpacing(14)
        self.welcome_icon = QLabel()
        self.welcome_icon.setObjectName("sidebarLogo")
        self.welcome_icon.setFixedSize(56, 56)
        self.welcome_icon.setAlignment(Qt.AlignCenter)
        chat_common.set_label_svg_icon(self.welcome_icon, "welcome_brand", chat_common._SVG_WINDOW, color="accent_text", size=24)
        brand_row.addWidget(self.welcome_icon, 0, Qt.AlignTop)
        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(2)
        title = QLabel("GenericAgent 启动器")
        title.setObjectName("titleDisplay")
        subtitle = QLabel("通用智能体 · 一键启动")
        subtitle.setObjectName("titleSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        brand_row.addLayout(title_box, 1)
        layout.addWidget(brand)

        self.recent_card = QFrame()
        self.recent_card.setObjectName("recentCard")
        self.recent_card.setMinimumHeight(78)
        recent_row = QHBoxLayout(self.recent_card)
        recent_row.setContentsMargins(18, 12, 16, 12)
        recent_info = QVBoxLayout()
        recent_info.setSpacing(2)
        recent_title = QLabel("最近使用的目录")
        recent_title.setObjectName("accentLabel")
        self.recent_path_label = QLabel("")
        self.recent_path_label.setObjectName("bodyText")
        self.recent_path_label.setWordWrap(True)
        recent_info.addWidget(recent_title)
        recent_info.addWidget(self.recent_path_label)
        recent_row.addLayout(recent_info, 1)
        self.enter_chat_btn = QPushButton("直接启动")
        self.enter_chat_btn.setStyleSheet(self._action_button_style(primary=True))
        self.enter_chat_btn.setFixedSize(110, 34)
        self.enter_chat_btn.clicked.connect(self._quick_enter_chat)
        recent_row.addWidget(self.enter_chat_btn, 0, Qt.AlignVCenter)
        layout.addWidget(self.recent_card)

        choose = QLabel("请选择你的情况")
        choose.setObjectName("mutedText")
        layout.addWidget(choose)

        locate_card = OptionCard(
            {"key": "welcome_locate", "svg": chat_common._SVG_FOLDER, "color": "accent_text", "size": 18},
            "我已经下载了 GenericAgent",
            "选择本地目录，立即载入内核",
            self._show_locate,
            body,
        )
        layout.addWidget(locate_card)

        download_card = OptionCard(
            {"key": "welcome_download", "svg": chat_common._SVG_DOWNLOAD, "color": "accent_text", "size": 18},
            "我还没有，帮我下载",
            "从 GitHub 自动克隆到你指定的位置",
            self._show_download,
            body,
        )
        layout.addWidget(download_card)

        official_card = OptionCard(
            {"key": "welcome_official", "svg": chat_common._SVG_WINDOW, "color": "accent_text", "size": 18},
            "或许你想试试官方的？",
            "直接拉起上游默认 GUI / 官方发布版桌面端",
            self._show_official_gui_page,
            body,
        )
        layout.addWidget(official_card)

        source = QLabel(f"源：{lz.REPO_URL}")
        source.setObjectName("mutedText")
        layout.addStretch(1)
        layout.addWidget(source, 0, Qt.AlignLeft)
        return page

    def _build_official_gui_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(
            self._build_setup_topbar(
                "试试官方前端",
                "这里会直接运行上游自带界面，而不是启动器自己的聊天主区。"
                "当前同时提供默认 GUI（launch.pyw）和官方发布版桌面端。",
                back_command=self._show_welcome,
            )
        )

        body_scroll, _body, body_layout = self._build_setup_scroll_body(margins=(36, 28, 36, 28), spacing=16)
        layout.addWidget(body_scroll, 1)

        target_card = self._panel_card()
        target_box = QVBoxLayout(target_card)
        target_box.setContentsMargins(20, 18, 20, 18)
        target_box.setSpacing(10)
        target_title = QLabel("当前目标目录")
        target_title.setObjectName("cardTitle")
        target_box.addWidget(target_title)
        target_desc = QLabel("默认 GUI 会直接使用当前选中的 GenericAgent 目录；发布版桌面端会按平台检测已安装应用或官方可执行文件。")
        target_desc.setWordWrap(True)
        target_desc.setObjectName("cardDesc")
        target_box.addWidget(target_desc)
        self.official_gui_path_label = QLabel("")
        self.official_gui_path_label.setObjectName("pathValue")
        self.official_gui_path_label.setWordWrap(True)
        self.official_gui_path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        target_box.addWidget(self.official_gui_path_label)
        self.official_gui_path_hint_label = QLabel("如果你想改目录或指定 Python，可先回到“我已经下载了 GenericAgent”页面调整。")
        self.official_gui_path_hint_label.setWordWrap(True)
        self.official_gui_path_hint_label.setObjectName("mutedText")
        target_box.addWidget(self.official_gui_path_hint_label)
        target_actions = QHBoxLayout()
        target_actions.setSpacing(8)
        adjust_btn = QPushButton("调整目录 / Python")
        adjust_btn.setStyleSheet(self._action_button_style())
        adjust_btn.clicked.connect(self._show_locate)
        target_actions.addWidget(adjust_btn, 0)
        target_actions.addStretch(1)
        target_box.addLayout(target_actions)
        body_layout.addWidget(target_card)

        status_card = self._panel_card()
        status_box = QVBoxLayout(status_card)
        status_box.setContentsMargins(20, 18, 20, 18)
        status_box.setSpacing(10)
        status_title = QLabel("启动环境")
        status_title.setObjectName("cardTitle")
        status_box.addWidget(status_title)
        self.official_gui_status_label = QLabel("")
        self.official_gui_status_label.setWordWrap(True)
        self.official_gui_status_label.setObjectName("bodyText")
        status_box.addWidget(self.official_gui_status_label)
        body_layout.addWidget(status_card)

        frontend_card = self._panel_card()
        frontend_box = QVBoxLayout(frontend_card)
        frontend_box.setContentsMargins(20, 18, 20, 18)
        frontend_box.setSpacing(14)
        frontend_title = QLabel("官方入口")
        frontend_title.setObjectName("cardTitle")
        frontend_box.addWidget(frontend_title)
        frontend_desc = QLabel("默认 GUI 会沿用当前目录、Python 解释器和依赖安装策略；发布版桌面端则按平台拉起官方已安装程序。")
        frontend_desc.setWordWrap(True)
        frontend_desc.setObjectName("cardDesc")
        frontend_box.addWidget(frontend_desc)

        default_gui_title = QLabel("官方默认 GUI")
        default_gui_title.setObjectName("accentLabel")
        frontend_box.addWidget(default_gui_title)
        default_gui_desc = QLabel("入口：launch.pyw。适合直接体验上游默认界面。")
        default_gui_desc.setWordWrap(True)
        default_gui_desc.setObjectName("mutedText")
        frontend_box.addWidget(default_gui_desc)
        self.official_gui_entry_status_label = QLabel("")
        self.official_gui_entry_status_label.setWordWrap(True)
        self.official_gui_entry_status_label.setObjectName("bodyText")
        frontend_box.addWidget(self.official_gui_entry_status_label)
        self.official_gui_dependency_label = QLabel("")
        self.official_gui_dependency_label.setWordWrap(True)
        self.official_gui_dependency_label.setObjectName("mutedText")
        frontend_box.addWidget(self.official_gui_dependency_label)
        default_launch_actions = QHBoxLayout()
        default_launch_actions.setSpacing(10)
        default_launch_actions.addStretch(1)
        self.official_gui_launch_btn = QPushButton("拉起官方默认 GUI")
        self.official_gui_launch_btn.setStyleSheet(self._action_button_style(primary=True))
        self.official_gui_launch_btn.setFixedHeight(40)
        self.official_gui_launch_btn.clicked.connect(self._launch_official_gui)
        default_launch_actions.addWidget(self.official_gui_launch_btn, 0)
        frontend_box.addLayout(default_launch_actions)

        desktop_separator = QFrame()
        desktop_separator.setFrameShape(QFrame.HLine)
        desktop_separator.setObjectName("setupDivider")
        frontend_box.addWidget(desktop_separator)

        desktop_title = QLabel("官方桌面版（发布版）")
        desktop_title.setObjectName("accentLabel")
        frontend_box.addWidget(desktop_title)
        desktop_desc = QLabel("入口不是源码脚本，而是 GitHub Release 发布的官方桌面客户端。Windows 和 macOS 的安装方式不同。")
        desktop_desc.setWordWrap(True)
        desktop_desc.setObjectName("mutedText")
        frontend_box.addWidget(desktop_desc)
        self.official_desktop_status_label = QLabel("")
        self.official_desktop_status_label.setWordWrap(True)
        self.official_desktop_status_label.setObjectName("bodyText")
        frontend_box.addWidget(self.official_desktop_status_label)
        self.official_desktop_dependency_label = QLabel("")
        self.official_desktop_dependency_label.setWordWrap(True)
        self.official_desktop_dependency_label.setObjectName("mutedText")
        frontend_box.addWidget(self.official_desktop_dependency_label)
        desktop_launch_actions = QHBoxLayout()
        desktop_launch_actions.setSpacing(10)
        desktop_launch_actions.addStretch(1)
        self.official_desktop_release_btn = QPushButton("打开 Release 页面")
        self.official_desktop_release_btn.setStyleSheet(self._action_button_style())
        self.official_desktop_release_btn.setFixedHeight(40)
        self.official_desktop_release_btn.clicked.connect(self._open_official_desktop_release_page)
        desktop_launch_actions.addWidget(self.official_desktop_release_btn, 0)
        self.official_desktop_launch_btn = QPushButton("拉起官方桌面版")
        self.official_desktop_launch_btn.setStyleSheet(self._action_button_style())
        self.official_desktop_launch_btn.setFixedHeight(40)
        self.official_desktop_launch_btn.clicked.connect(self._launch_official_desktop_app)
        desktop_launch_actions.addWidget(self.official_desktop_launch_btn, 0)
        frontend_box.addLayout(desktop_launch_actions)

        self.official_gui_notice_label = QLabel("这些入口只会新开官方窗口，不会替换启动器自己的聊天主区。")
        self.official_gui_notice_label.setWordWrap(True)
        self.official_gui_notice_label.setObjectName("mutedText")
        frontend_box.addWidget(self.official_gui_notice_label)
        body_layout.addWidget(frontend_card)
        body_layout.addStretch(1)
        return page

    def _build_setup_topbar(self, title_text: str, subtitle_text: str, *, back_text: str = "←  返回首页", back_command=None):
        wrap = QFrame()
        wrap.setObjectName("setupTopbar")
        row = QHBoxLayout(wrap)
        row.setContentsMargins(24, 14, 24, 14)
        row.setSpacing(12)

        back_btn = QPushButton(back_text)
        back_btn.setStyleSheet(self._sidebar_button_style(subtle=True))
        back_btn.setCursor(Qt.PointingHandCursor)
        if callable(back_command):
            back_btn.clicked.connect(back_command)
        row.addWidget(back_btn, 0)

        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(2)
        title = QLabel(title_text)
        title.setObjectName("cardTitle")
        subtitle = QLabel(subtitle_text)
        subtitle.setObjectName("cardDesc")
        subtitle.setWordWrap(True)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        row.addLayout(title_box, 1)
        return wrap

    def _build_locate_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(
            self._build_setup_topbar(
                "选择 GenericAgent 目录",
                "目录中需包含 launch.pyw 与 agentmain.py",
                back_command=self._show_welcome,
            )
        )

        body_scroll, _body, body_layout = self._build_setup_scroll_body(margins=(36, 28, 36, 28), spacing=16)
        layout.addWidget(body_scroll, 1)

        card = self._panel_card()
        card_box = QVBoxLayout(card)
        card_box.setContentsMargins(20, 18, 20, 18)
        card_box.setSpacing(10)
        label = QLabel("目录路径")
        label.setObjectName("mutedText")
        card_box.addWidget(label)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.locate_path_edit = QLineEdit()
        self.locate_path_edit.setPlaceholderText("请选择 GenericAgent 项目的根目录")
        self.locate_path_edit.setText(self.agent_dir or "")
        self.locate_path_edit.returnPressed.connect(self._locate_enter_chat)
        row.addWidget(self.locate_path_edit, 1)
        browse_btn = QPushButton("浏览…")
        browse_btn.setStyleSheet(self._action_button_style())
        browse_btn.clicked.connect(self._choose_agent_dir)
        row.addWidget(browse_btn, 0)
        card_box.addLayout(row)
        self.locate_hint_label = QLabel("选择 GenericAgent 项目的根目录。")
        self.locate_hint_label.setWordWrap(True)
        self.locate_hint_label.setObjectName("mutedText")
        card_box.addWidget(self.locate_hint_label)

        py_label = QLabel("Python 可执行文件（可选）")
        py_label.setObjectName("mutedText")
        card_box.addWidget(py_label)
        py_row = QHBoxLayout()
        py_row.setSpacing(8)
        self.locate_python_edit = QLineEdit()
        self.locate_python_edit.setPlaceholderText("留空则自动探测；可填 python / python3 / venv/bin/python，支持相对路径")
        self.locate_python_edit.setText(str(self.cfg.get("python_exe") or "").strip())
        self.locate_python_edit.returnPressed.connect(self._locate_enter_chat)
        py_row.addWidget(self.locate_python_edit, 1)
        py_browse_btn = QPushButton("浏览可执行文件…")
        py_browse_btn.setStyleSheet(self._action_button_style())
        py_browse_btn.clicked.connect(self._choose_python_executable)
        py_row.addWidget(py_browse_btn, 0)
        card_box.addLayout(py_row)
        self.locate_python_hint_label = QLabel(
            "这里建议选择具体的 Python 可执行文件。"
            "如果你用 uv 管理多版本 Python，也请填写 uv 实际创建的解释器路径，而不是 uv 本身。"
        )
        self.locate_python_hint_label.setWordWrap(True)
        self.locate_python_hint_label.setObjectName("mutedText")
        card_box.addWidget(self.locate_python_hint_label)

        installer_label = QLabel("依赖安装器策略")
        installer_label.setObjectName("mutedText")
        card_box.addWidget(installer_label)
        installer_row = QHBoxLayout()
        installer_row.setSpacing(8)
        self.locate_dependency_installer_combo = NoWheelComboBox()
        self.locate_dependency_installer_combo.addItem("自动（优先 uv，失败回退 pip）", "auto")
        self.locate_dependency_installer_combo.addItem("强制 uv", "uv")
        self.locate_dependency_installer_combo.addItem("强制 pip", "pip")
        current_mode = str(self.cfg.get("dependency_installer") or "auto").strip().lower()
        idx = self.locate_dependency_installer_combo.findData(current_mode if current_mode in ("auto", "uv", "pip") else "auto")
        self.locate_dependency_installer_combo.setCurrentIndex(idx if idx >= 0 else 0)
        installer_row.addWidget(self.locate_dependency_installer_combo, 1)
        card_box.addLayout(installer_row)
        self.locate_dependency_installer_hint_label = QLabel(
            "默认会优先尝试 uv；若不可用或失败，再自动回退到 pip。"
        )
        self.locate_dependency_installer_hint_label.setWordWrap(True)
        self.locate_dependency_installer_hint_label.setObjectName("mutedText")
        card_box.addWidget(self.locate_dependency_installer_hint_label)
        body_layout.addWidget(card)

        self.locate_status_card = QFrame()
        self.locate_status_card.setObjectName("statusCard")
        status_box = QVBoxLayout(self.locate_status_card)
        status_box.setContentsMargins(16, 12, 16, 12)
        status_box.setSpacing(4)
        status_title = QLabel("当前状态")
        status_title.setObjectName("accentLabel")
        self.locate_status_label = QLabel("")
        self.locate_status_label.setWordWrap(True)
        self.locate_status_label.setObjectName("bodyText")
        status_box.addWidget(status_title)
        status_box.addWidget(self.locate_status_label)
        body_layout.addWidget(self.locate_status_card)

        dep_card = self._panel_card()
        dep_box = QVBoxLayout(dep_card)
        dep_box.setContentsMargins(20, 18, 20, 18)
        dep_box.setSpacing(10)
        dep_title = QLabel("载入前依赖检查")
        dep_title.setObjectName("cardTitle")
        dep_box.addWidget(dep_title)
        dep_desc = QLabel(
            "从这个页面点击“载入内核”时，会检查系统 Python、基础依赖和 GenericAgent 可载入性。"
            "缺失时会自动补齐，并展示实时过程。欢迎页的“直接启动”不会先做这一步。"
            "留空时会自动尝试 python / python3；mac 下也会补试常见 Homebrew 绝对路径。"
            "如果你已有项目虚拟环境，也可以手动填写 venv/bin/python。"
        )
        dep_desc.setWordWrap(True)
        dep_desc.setObjectName("cardDesc")
        dep_box.addWidget(dep_desc)
        self.locate_dependency_label = QLabel("依赖检查：尚未执行。进入聊天时会自动检查并补齐。")
        self.locate_dependency_label.setWordWrap(True)
        self.locate_dependency_label.setObjectName("bodyText")
        dep_box.addWidget(self.locate_dependency_label)
        dep_actions = QHBoxLayout()
        dep_actions.setSpacing(10)
        dep_btn = QPushButton("检查并补齐依赖")
        dep_btn.setStyleSheet(self._action_button_style())
        dep_btn.clicked.connect(self._check_runtime_dependencies_from_locate)
        dep_actions.addWidget(dep_btn, 0)
        dep_report_btn = QPushButton("查看详细报告")
        dep_report_btn.setStyleSheet(self._action_button_style())
        dep_report_btn.clicked.connect(self._show_dependency_report)
        dep_actions.addWidget(dep_report_btn, 0)
        dep_actions.addStretch(1)
        dep_box.addLayout(dep_actions)
        body_layout.addWidget(dep_card)

        enter_btn = QPushButton("载入内核 →")
        enter_btn.setStyleSheet(self._action_button_style(primary=True))
        enter_btn.setFixedHeight(40)
        enter_btn.clicked.connect(self._locate_enter_chat)
        body_layout.addWidget(enter_btn)
        body_layout.addStretch(1)
        return page

    def _download_dependency_placeholder(self):
        return {
            "git_ok": False,
            "git_text": "正在检测 Git…",
            "python_ok": False,
            "python_text": "正在检测系统 Python…",
            "python_warn": True,
            "requests_ok": False,
            "requests_text": "正在检测 requests…",
            "requests_warn": True,
        }

    def _download_dependency_severity(self, ok: bool, *, warning: bool = False):
        if ok and not warning:
            return "ok", "✓"
        if warning:
            return "warn", "!"
        return "error", "✕"

    def _refresh_download_dependency_row(self, key: str, title_text: str, ok: bool, detail: str, *, warning: bool = False):
        rows = getattr(self, "download_dependency_rows", None)
        if not isinstance(rows, dict):
            return
        row = rows.get(str(key or ""))
        if not isinstance(row, dict):
            return
        severity, mark = self._download_dependency_severity(bool(ok), warning=bool(warning))
        card = row.get("card")
        icon = row.get("icon")
        name = row.get("name")
        detail_label = row.get("detail")
        if card is not None:
            try:
                card.setObjectName({"ok": "depRowOk", "warn": "depRowWarn", "error": "depRowError"}[severity])
                card.style().unpolish(card)
                card.style().polish(card)
            except Exception:
                pass
        if icon is not None:
            try:
                icon.setText(mark)
                icon.setProperty("severity", severity)
                icon.style().unpolish(icon)
                icon.style().polish(icon)
            except Exception:
                pass
        if name is not None:
            try:
                name.setText(title_text)
            except Exception:
                pass
        if detail_label is not None:
            try:
                detail_label.setText(detail)
            except Exception:
                pass

    def _schedule_download_requirements_probe(self):
        if bool(getattr(self, "_download_requirements_probe_running", False)):
            return
        self._download_requirements_probe_running = True

        def worker():
            try:
                deps = _probe_download_requirements()
            except Exception as e:
                deps = self._download_dependency_placeholder()
                deps["git_text"] = f"环境检测失败：{e}"
                deps["python_text"] = "环境检测失败。"
                deps["requests_text"] = "环境检测失败。"

            def done():
                self._download_requirements_probe_running = False
                if bool(getattr(self, "_closing_in_progress", False)):
                    return
                self._refresh_download_dependency_row("git", "Git", deps["git_ok"], deps["git_text"])
                self._refresh_download_dependency_row(
                    "python",
                    "Python",
                    deps["python_ok"],
                    deps["python_text"],
                    warning=deps.get("python_warn", False),
                )
                self._refresh_download_dependency_row(
                    "requests",
                    "requests",
                    deps["requests_ok"],
                    deps["requests_text"],
                    warning=deps.get("requests_warn", True),
                )

            poster = getattr(self, "_api_on_ui_thread", None)
            if callable(poster):
                try:
                    poster(done)
                    return
                except Exception:
                    pass
            try:
                QTimer.singleShot(0, done)
            except Exception:
                pass

        threading.Thread(target=worker, name="download-requirements-probe", daemon=True).start()

    def _build_download_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(
            self._build_setup_topbar(
                "下载 GenericAgent",
                f"来源：{lz.REPO_URL}",
                back_command=self._show_welcome,
            )
        )

        body_scroll, body, body_layout = self._build_setup_scroll_body(margins=(36, 28, 36, 16), spacing=16)
        self.download_body_scroll = body_scroll
        self.download_body_widget = body
        layout.addWidget(body_scroll, 1)

        card = self._panel_card()
        card_box = QVBoxLayout(card)
        card_box.setContentsMargins(20, 18, 20, 18)
        card_box.setSpacing(12)

        title = QLabel("安装位置")
        title.setObjectName("cardTitle")
        card_box.addWidget(title)
        desc = QLabel("启动器会在你选择的目录下创建 `GenericAgent` 文件夹；如果该目录已存在，会先让你确认是否直接使用。")
        desc.setWordWrap(True)
        desc.setObjectName("cardDesc")
        card_box.addWidget(desc)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.download_parent_value = QLabel("")
        self.download_parent_value.setObjectName("pathValue")
        self.download_parent_value.setWordWrap(True)
        self.download_parent_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self.download_parent_value, 1)
        choose_btn = QPushButton("浏览…")
        choose_btn.setStyleSheet(self._action_button_style())
        choose_btn.clicked.connect(self._choose_install_parent)
        row.addWidget(choose_btn, 0)
        card_box.addLayout(row)

        self.download_parent_label = QLabel("")
        self.download_parent_label.setWordWrap(True)
        self.download_parent_label.setObjectName("accentLabel")
        card_box.addWidget(self.download_parent_label)

        self.download_status_label = QLabel("")
        self.download_status_label.setWordWrap(True)
        self.download_status_label.setObjectName("mutedText")
        card_box.addWidget(self.download_status_label)

        body_layout.addWidget(card)

        supports_private_python = bool(getattr(lz, "PLATFORM_SUPPORTS_PRIVATE_PYTHON_INSTALLER", os.name == "nt"))
        managed_env_button_text = (
            self._download_managed_env_button_text()
            if callable(getattr(self, "_download_managed_env_button_text", None))
            else ("下载并配置 3.12 虚拟环境" if supports_private_python else "构建项目虚拟环境")
        )
        deps = self._download_dependency_placeholder()
        deps_card = self._panel_card()
        deps_box = QVBoxLayout(deps_card)
        deps_box.setContentsMargins(20, 18, 20, 18)
        deps_box.setSpacing(10)
        deps_title = QLabel("环境提示")
        deps_title.setObjectName("cardTitle")
        deps_box.addWidget(deps_title)
        deps_desc_text = (
            "下面显示的是对你这台电脑当前环境的实时扫描结果，不是写死的版本要求。"
            "普通下载只依赖 Git；下载完成后，这个启动器会直接拉起 GenericAgent 的 agentmain，因此还需要系统 Python。"
            "当前不会强制限制 Python 版本，而是实际探测它能否载入 GenericAgent；只是经验上 3.11 / 3.12 更稳。"
        )
        if supports_private_python:
            deps_desc_text += "如果你不想动系统 Python，可以直接用下面的私有 3.12 虚拟环境安装，它会自己下载并管理一套私有解释器。"
        else:
            deps_desc_text += (
                "mac 版不会下载私有解释器，但可以直接用现有 Python 为当前 GenericAgent 构建项目虚拟环境。"
                "这个过程只会把依赖装进项目 venv，不会污染系统 Python。"
                "seed Python 会优先复用你已配置的 python_exe，否则自动尝试 python3 / python / 常见 Homebrew 绝对路径。"
            )
        deps_desc = QLabel(deps_desc_text)
        deps_desc.setWordWrap(True)
        deps_desc.setObjectName("cardDesc")
        deps_box.addWidget(deps_desc)

        self.download_dependency_rows = {}

        def add_dep_row(key: str, title_text: str, ok: bool, detail: str, *, warning: bool = False):
            severity, mark = self._download_dependency_severity(ok, warning=warning)
            row_card = QFrame()
            row_card.setObjectName({"ok": "depRowOk", "warn": "depRowWarn", "error": "depRowError"}[severity])
            row = QHBoxLayout(row_card)
            row.setContentsMargins(12, 10, 12, 10)
            row.setSpacing(10)
            icon = QLabel(mark)
            icon.setObjectName("depMark")
            icon.setProperty("severity", severity)
            icon.setFixedWidth(16)
            icon.setAlignment(Qt.AlignCenter)
            row.addWidget(icon, 0)
            name = QLabel(title_text)
            name.setObjectName("depName")
            name.setFixedWidth(76)
            row.addWidget(name, 0)
            detail_label = QLabel(detail)
            detail_label.setObjectName("depDetail")
            detail_label.setWordWrap(True)
            row.addWidget(detail_label, 1)
            deps_box.addWidget(row_card)
            self.download_dependency_rows[str(key or "")] = {
                "card": row_card,
                "icon": icon,
                "name": name,
                "detail": detail_label,
            }

        add_dep_row("git", "Git", deps["git_ok"], deps["git_text"], warning=True)
        add_dep_row("python", "Python", deps["python_ok"], deps["python_text"], warning=deps.get("python_warn", False))
        add_dep_row("requests", "requests", deps["requests_ok"], deps["requests_text"], warning=True)
        body_layout.addWidget(deps_card)

        log_card = self._panel_card()
        log_box = QVBoxLayout(log_card)
        log_box.setContentsMargins(20, 18, 20, 18)
        log_box.setSpacing(10)
        log_head = QHBoxLayout()
        log_title = QLabel("下载日志")
        log_title.setObjectName("cardTitle")
        log_head.addWidget(log_title, 0)
        log_head.addStretch(1)
        log_hint = QLabel(
            "git clone、私有 Python 安装和 venv 构建都会在这里实时输出"
            if supports_private_python
            else "git clone 和项目虚拟环境构建都会在这里实时输出；进入聊天后的依赖检查会在单独窗口展示。"
        )
        log_hint.setObjectName("mutedText")
        log_head.addWidget(log_hint, 0)
        log_box.addLayout(log_head)
        self.download_log = QTextEdit()
        self.download_log.setReadOnly(True)
        self.download_log.setMinimumHeight(220)
        log_box.addWidget(self.download_log)
        body_layout.addWidget(log_card)
        body_layout.addStretch(1)

        footer = QFrame()
        footer.setObjectName("setupTopbar")
        footer_box = QVBoxLayout(footer)
        footer_box.setContentsMargins(36, 12, 36, 16)
        footer_box.setSpacing(10)
        self.download_progress = QProgressBar()
        self.download_progress.setTextVisible(False)
        self.download_progress.setRange(0, 1)
        self.download_progress.setValue(0)
        self.download_progress.setFixedHeight(6)
        footer_box.addWidget(self.download_progress)
        private_hint = (
            "私有 3.12 环境会下载到当前 GenericAgent 目录内，只供启动器使用，不会改系统 PATH。"
            if supports_private_python
            else "mac 版会直接在当前 GenericAgent 目录里构建项目虚拟环境；只会借用现有 Python 执行 -m venv，不会把依赖写进系统 Python，也不会改系统 PATH。"
        )
        self.download_private_hint = QLabel(private_hint)
        self.download_private_hint.setWordWrap(True)
        self.download_private_hint.setObjectName("mutedText")
        footer_box.addWidget(self.download_private_hint)

        sources_hint = (
            "Python 安装包下载源（可多选；只会尝试你勾选的源）"
            if supports_private_python
            else "mac 会优先使用已配置且存在的 python_exe；否则依次尝试 python3 / python / 常见 Homebrew 绝对路径。若已有项目虚拟环境，也可以在“载入内核”页手动指定它的 Python 可执行文件。"
        )
        self.download_sources_hint = QLabel(sources_hint)
        self.download_sources_hint.setWordWrap(True)
        self.download_sources_hint.setObjectName("mutedText")
        footer_box.addWidget(self.download_sources_hint)

        self.download_source_checkboxes = {}
        if supports_private_python:
            for item in self._private_python_source_ui_options():
                source_id = str(item.get("id") or "").strip()
                source_label = str(item.get("label") or source_id).strip()
                if not source_id:
                    continue
                cb = QCheckBox(source_label)
                cb.setObjectName("mutedText")
                cb.toggled.connect(lambda _checked, sid=source_id: self._on_private_python_source_toggled(sid))
                self.download_source_checkboxes[source_id] = cb
                footer_box.addWidget(cb)
            self._sync_private_python_source_checkboxes_from_cfg()

            self.download_private_only_checkbox = QCheckBox("仅配置虚拟环境，不下载原项目（要求目标目录已存在有效 GenericAgent）")
            self.download_private_only_checkbox.setObjectName("mutedText")
            footer_box.addWidget(self.download_private_only_checkbox)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.download_btn = QPushButton("开始下载")
        self.download_btn.setStyleSheet(self._action_button_style(primary=True))
        self.download_btn.setFixedHeight(44)
        self.download_btn.clicked.connect(self._start_download_repo)
        actions.addWidget(self.download_btn, 1)
        self.download_private_btn = QPushButton(managed_env_button_text)
        self.download_private_btn.setStyleSheet(self._action_button_style())
        self.download_private_btn.setFixedHeight(44)
        self.download_private_btn.clicked.connect(
            lambda: self._start_download_repo(
                private_python=True,
                private_only=bool(getattr(self, "download_private_only_checkbox", None) and self.download_private_only_checkbox.isChecked()),
            )
        )
        actions.addWidget(self.download_private_btn, 1)
        footer_box.addLayout(actions)
        layout.addWidget(footer, 0)
        self._restyle_download_page_widgets()
        self._append_download_log("等待开始…")
        self._schedule_download_requirements_probe()
        return page

    def _show_locate(self):
        self.setWindowTitle("GenericAgent 启动器")
        self.pages.setCurrentWidget(self._locate_page)
        self._refresh_welcome_state()

    def _show_download(self):
        self.setWindowTitle("GenericAgent 启动器")
        ensure = getattr(self, "_ensure_download_page_built", None)
        if callable(ensure):
            ensure()
        self.pages.setCurrentWidget(self._download_page)
        self._refresh_download_state()

    def _show_official_gui_page(self):
        self.setWindowTitle("GenericAgent 启动器")
        self.pages.setCurrentWidget(self._official_gui_page)
        self._refresh_welcome_state()

    def _show_welcome(self):
        self.setWindowTitle("GenericAgent 启动器")
        self.pages.setCurrentWidget(self._welcome_page)
        self._refresh_welcome_state()
        self._refresh_recent_directory_card_layout(defer=True)
