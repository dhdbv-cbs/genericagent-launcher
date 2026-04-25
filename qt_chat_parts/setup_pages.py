from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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

from .common import OptionCard, _probe_download_requirements

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
    def _build_welcome_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 32, 40, 28)
        layout.setSpacing(14)

        brand = QWidget()
        brand_row = QHBoxLayout(brand)
        brand_row.setContentsMargins(0, 6, 0, 22)
        brand_row.setSpacing(14)
        self.welcome_icon = QLabel("⚙")
        self.welcome_icon.setStyleSheet(f"font-size: 42px; color: #4f8cff; background: transparent;")
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
        self.recent_card.setFixedHeight(74)
        recent_row = QHBoxLayout(self.recent_card)
        recent_row.setContentsMargins(18, 12, 16, 12)
        recent_info = QVBoxLayout()
        recent_info.setSpacing(2)
        recent_title = QLabel("📁  上次使用的目录")
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
            "✅",
            "我已经下载了 GenericAgent",
            "选择本地目录，立即载入内核",
            self._show_locate,
            page,
        )
        layout.addWidget(locate_card)

        download_card = OptionCard(
            "⬇",
            "我还没有，帮我下载",
            "从 GitHub 自动克隆到你指定的位置",
            self._show_download,
            page,
        )
        layout.addWidget(download_card)

        source = QLabel(f"源：{lz.REPO_URL}")
        source.setObjectName("mutedText")
        layout.addStretch(1)
        layout.addWidget(source, 0, Qt.AlignLeft)
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

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(36, 28, 36, 28)
        body_layout.setSpacing(16)
        layout.addWidget(body, 1)

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
        self.locate_hint_label = QLabel("💡  提示：选择 GenericAgent 项目的根目录")
        self.locate_hint_label.setWordWrap(True)
        self.locate_hint_label.setObjectName("mutedText")
        card_box.addWidget(self.locate_hint_label)

        py_label = QLabel("Python 可执行文件（可选）")
        py_label.setObjectName("mutedText")
        card_box.addWidget(py_label)
        py_row = QHBoxLayout()
        py_row.setSpacing(8)
        self.locate_python_edit = QLineEdit()
        self.locate_python_edit.setPlaceholderText("留空则自动探测；可填 python.exe 路径，支持相对路径")
        self.locate_python_edit.setText(str(self.cfg.get("python_exe") or "").strip())
        self.locate_python_edit.returnPressed.connect(self._locate_enter_chat)
        py_row.addWidget(self.locate_python_edit, 1)
        py_browse_btn = QPushButton("浏览可执行文件…")
        py_browse_btn.setStyleSheet(self._action_button_style())
        py_browse_btn.clicked.connect(self._choose_python_executable)
        py_row.addWidget(py_browse_btn, 0)
        card_box.addLayout(py_row)
        self.locate_python_hint_label = QLabel(
            "💡  提示：这里建议选择具体的 python.exe。"
            "如果你用 uv 管理多版本 Python，也请填写 uv 实际创建的解释器路径，而不是 uv.exe 本身。"
        )
        self.locate_python_hint_label.setWordWrap(True)
        self.locate_python_hint_label.setObjectName("mutedText")
        card_box.addWidget(self.locate_python_hint_label)

        installer_label = QLabel("依赖安装器策略")
        installer_label.setObjectName("mutedText")
        card_box.addWidget(installer_label)
        installer_row = QHBoxLayout()
        installer_row.setSpacing(8)
        self.locate_dependency_installer_combo = QComboBox()
        self.locate_dependency_installer_combo.addItem("自动（优先 uv，失败回退 pip）", "auto")
        self.locate_dependency_installer_combo.addItem("强制 uv", "uv")
        self.locate_dependency_installer_combo.addItem("强制 pip", "pip")
        current_mode = str(self.cfg.get("dependency_installer") or "auto").strip().lower()
        idx = self.locate_dependency_installer_combo.findData(current_mode if current_mode in ("auto", "uv", "pip") else "auto")
        self.locate_dependency_installer_combo.setCurrentIndex(idx if idx >= 0 else 0)
        installer_row.addWidget(self.locate_dependency_installer_combo, 1)
        card_box.addLayout(installer_row)
        self.locate_dependency_installer_hint_label = QLabel(
            "💡  提示：默认自动优先 uv（若可用），失败会自动回退 pip。"
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

        body_scroll = QScrollArea()
        body_scroll.setWidgetResizable(True)
        body_scroll.setFrameShape(QFrame.NoFrame)
        body_scroll.setStyleSheet(f"QScrollArea {{ border: none; background: transparent; }}" + _SCROLLBAR_STYLE)
        self.download_body_scroll = body_scroll
        layout.addWidget(body_scroll, 1)

        body = QWidget()
        body.setStyleSheet("background: transparent;")
        self.download_body_widget = body
        body_scroll.setWidget(body)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(36, 28, 36, 16)
        body_layout.setSpacing(16)

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

        deps = self._download_dependency_placeholder()
        deps_card = self._panel_card()
        deps_box = QVBoxLayout(deps_card)
        deps_box.setContentsMargins(20, 18, 20, 18)
        deps_box.setSpacing(10)
        deps_title = QLabel("环境提示")
        deps_title.setObjectName("cardTitle")
        deps_box.addWidget(deps_title)
        deps_desc = QLabel(
            "下面显示的是对你这台电脑当前环境的实时扫描结果，不是写死的版本要求。"
            "普通下载只依赖 Git；下载完成后，这个启动器会直接拉起 GenericAgent 的 agentmain，因此还需要系统 Python。"
            "当前不会强制限制 Python 版本，而是实际探测它能否载入 GenericAgent；只是经验上 3.11 / 3.12 更稳。"
            "如果你不想动系统 Python，可以直接用下面的私有 3.12 虚拟环境安装，它会自己下载并管理一套私有解释器。"
        )
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
        log_hint = QLabel("git clone、私有 Python 安装和 venv 构建都会在这里实时输出")
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
        self.download_private_hint = QLabel("私有 3.12 环境会下载到当前 GenericAgent 目录内，只供启动器使用，不会改系统 PATH。")
        self.download_private_hint.setWordWrap(True)
        self.download_private_hint.setObjectName("mutedText")
        footer_box.addWidget(self.download_private_hint)

        self.download_sources_hint = QLabel("Python 安装包下载源（可多选；只会尝试你勾选的源）")
        self.download_sources_hint.setWordWrap(True)
        self.download_sources_hint.setObjectName("mutedText")
        footer_box.addWidget(self.download_sources_hint)

        self.download_source_checkboxes = {}
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
        self.download_private_btn = QPushButton("下载并配置 3.12 虚拟环境")
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

    def _show_welcome(self):
        self.setWindowTitle("GenericAgent 启动器")
        self.pages.setCurrentWidget(self._welcome_page)
        self._refresh_welcome_state()
