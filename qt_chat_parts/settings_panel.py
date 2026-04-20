from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import launcher_core as lz

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


class SettingsPanelMixin:
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
        back_btn = QPushButton("←  返回聊天")
        back_btn.setStyleSheet(self._sidebar_button_style(subtle=True))
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.clicked.connect(self._show_chat_page)
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
        content_col.addWidget(self.settings_status_label)
        self.settings_stack = QStackedWidget()
        content_col.addWidget(self.settings_stack, 1)
        body_row.addWidget(content_wrap, 1)

        self._settings_nav_buttons = {}
        self._settings_pages = {}
        categories = [
            ("api", "🔑  API"),
            ("channels", "💬  通讯渠道"),
            ("schedule", "⏰  定时任务"),
            ("personal", "🧩  个性设置"),
            ("usage", "📊  使用计数"),
            ("about", "ℹ  关于"),
        ]

        def make_page():
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setStyleSheet(f"QScrollArea {{ border: none; background: transparent; }}" + _SCROLLBAR_STYLE)
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
        api_add_btn = QPushButton("+ 添加 API 卡片")
        api_add_btn.setStyleSheet(self._action_button_style(primary=True))
        api_add_btn.clicked.connect(lambda: self._qt_api_add_channel("oai_chat"))
        api_toolbar.addWidget(api_add_btn, 0)
        api_save_btn = QPushButton("仅保存")
        api_save_btn.setStyleSheet(self._action_button_style())
        api_save_btn.clicked.connect(lambda: self._qt_api_save(restart=False))
        api_toolbar.addWidget(api_save_btn, 0)
        api_restart_btn = QPushButton("保存并重启内核")
        api_restart_btn.setStyleSheet(self._action_button_style())
        api_restart_btn.clicked.connect(lambda: self._qt_api_save(restart=True))
        api_toolbar.addWidget(api_restart_btn, 0)
        api_raw_btn = QPushButton("直接编辑文件")
        api_raw_btn.setStyleSheet(self._action_button_style())
        api_raw_btn.clicked.connect(self._open_raw_mykey_editor)
        api_toolbar.addWidget(api_raw_btn, 0)
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
        channel_refresh_btn = QPushButton("刷新状态")
        channel_refresh_btn.setStyleSheet(self._action_button_style())
        channel_refresh_btn.clicked.connect(self._reload_channels_editor_state)
        channel_toolbar.addWidget(channel_refresh_btn, 0)
        channel_stop_btn = QPushButton("停止全部")
        channel_stop_btn.setStyleSheet(self._action_button_style())
        channel_stop_btn.clicked.connect(self._stop_all_managed_channels)
        channel_toolbar.addWidget(channel_stop_btn, 0)
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

        sch_layout = self._settings_pages["schedule"]["layout"]
        sch_layout.addWidget(
            self._settings_intro(
                "定时任务",
                "这里后续计划接入周期性自动任务入口，用来配置周期执行的自动任务。",
            )
        )
        schedule_card = self._panel_card()
        schedule_box = QVBoxLayout(schedule_card)
        schedule_box.setContentsMargins(20, 18, 20, 18)
        schedule_box.setSpacing(10)
        schedule_title = QLabel("功能占位")
        schedule_title.setObjectName("cardTitle")
        schedule_box.addWidget(schedule_title)
        schedule_desc = QLabel("当前先保留结构位置，后续再补任务列表、执行时间、启停开关和运行记录。")
        schedule_desc.setWordWrap(True)
        schedule_desc.setObjectName("cardDesc")
        schedule_box.addWidget(schedule_desc)
        sch_layout.addWidget(schedule_card)
        sch_layout.addStretch(1)

        personal_layout = self._settings_pages["personal"]["layout"]
        personal_layout.addWidget(
            self._settings_intro(
                "个性设置",
                "这里用于控制会话保留策略。你可以按主聊天区和各通讯渠道分别设置活跃会话上限，超过后会自动删除最旧未收藏会话。",
            )
        )
        personal_card = self._panel_card()
        personal_box = QVBoxLayout(personal_card)
        personal_box.setContentsMargins(20, 18, 20, 18)
        personal_box.setSpacing(10)
        personal_title = QLabel("自动清理")
        personal_title.setObjectName("cardTitle")
        personal_box.addWidget(personal_title)
        personal_desc = QLabel("数值表示该渠道保留在侧边栏中的活跃会话上限。填 0 表示关闭该渠道的自动清理。默认值是 10。")
        personal_desc.setWordWrap(True)
        personal_desc.setObjectName("cardDesc")
        personal_box.addWidget(personal_desc)
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
        personal_layout.addStretch(1)

        usage_layout = self._settings_pages["usage"]["layout"]
        usage_layout.addWidget(
            self._settings_intro(
                "使用计数",
                "标注说明：真实 = 直接读取模型接口返回的 usage；估算 = 按字符数 / 2.5 回推；混合 = 同一统计范围里两者都有。",
            )
        )
        usage_card = self._panel_card()
        usage_box = QVBoxLayout(usage_card)
        usage_box.setContentsMargins(20, 18, 20, 18)
        usage_box.setSpacing(10)
        usage_title = QLabel("统计汇总")
        usage_title.setObjectName("cardTitle")
        usage_box.addWidget(usage_title)
        usage_desc = QLabel("旧会话，以及不返回 usage 的渠道，仍可能只能显示估算。")
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

        self._show_settings_category("api")
        return page

    def _show_settings_category(self, key: str):
        if not hasattr(self, "settings_stack"):
            return
        page_info = (getattr(self, "_settings_pages", None) or {}).get(key)
        if not page_info:
            return
        self._current_settings_category = key
        self.settings_stack.setCurrentWidget(page_info["widget"])
        for nav_key, btn in (getattr(self, "_settings_nav_buttons", None) or {}).items():
            if nav_key == key:
                btn.setStyleSheet(self._sidebar_button_style(selected=True))
            else:
                btn.setStyleSheet(self._sidebar_button_style(subtle=True))

    def _load_mykey_source(self):
        agent_dir = self.agent_dir
        py_path = os.path.join(agent_dir, "mykey.py")
        tpl_path = os.path.join(agent_dir, "mykey_template.py")
        if os.path.isdir(agent_dir) and not os.path.isfile(py_path):
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
        parsed = lz.parse_mykey_py(py_path)
        return py_path, parsed

    def _settings_reload(self):
        if not hasattr(self, "settings_status_label"):
            return
        valid = lz.is_valid_agent_dir(self.agent_dir)
        self.settings_status_label.setText(
            "当前目录有效，下面的 API 与渠道配置都会写回这个 GenericAgent 目录。"
            if valid else
            "还没有可用的 GenericAgent 目录，先在上面选择目录。"
        )
        self._reload_api_editor_state()
        self._reload_channels_editor_state()
        self._reload_personal_panel()
        self._reload_usage_panel()
        self._reload_about_panel()
