from __future__ import annotations

import re
import threading
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

import launcher_core as lz
from qt_theme import C, F, apply_mica

from . import common as chat_common
from .common import _session_source_label

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


class WindowShellMixin:
    def _build_shell(self):
        self.pages = QStackedWidget()
        self.setCentralWidget(self.pages)
        self._welcome_page = self._build_welcome_page()
        self._locate_page = self._build_locate_page()
        self._download_page = self._build_download_page()
        self._chat_page = self._build_ui()
        self._settings_page = self._build_settings_page()
        self.pages.addWidget(self._welcome_page)
        self.pages.addWidget(self._locate_page)
        self.pages.addWidget(self._download_page)
        self.pages.addWidget(self._chat_page)
        self.pages.addWidget(self._settings_page)
        if lz.is_valid_agent_dir(self.agent_dir):
            self._refresh_sessions()
        self._reset_chat_area("选择一个会话，或新建会话开始聊天。")

    def _panel_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("panelCard")
        return card

    def _fluent_input(self, editor) -> None:
        editor.setStyleSheet(
            f"QLineEdit, QTextEdit {{ background: {C['field_bg']}; color: {C['text']}; "
            f"border: 1px solid {C['stroke_default']}; border-radius: {F['radius_md']}px; "
            f"padding: 7px 10px; selection-background-color: rgba(79,140,255,0.40); selection-color: white; }}"
            f"QLineEdit:hover, QTextEdit:hover {{ border-color: {C['stroke_hover']}; }}"
            f"QLineEdit:focus, QTextEdit:focus {{ border-color: {C['stroke_focus']}; }}"
        )

    def _fluent_topbar(self, title_text: str, subtitle_text: str = "") -> QFrame:
        wrap = QFrame()
        wrap.setFixedHeight(F["topbar_h"])
        wrap.setStyleSheet(
            f"QFrame {{ background: {C['layer1']}; border: none; border-bottom: 1px solid {C['stroke_divider']}; }}"
        )
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(18, 0, 18, 0)
        lay.setSpacing(10)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        title = QLabel(title_text)
        title.setStyleSheet(
            f"color: {C['text']}; font-size: {F['font_subtitle']}px; font-weight: 600;"
        )
        col.addWidget(title)
        if subtitle_text:
            sub = QLabel(subtitle_text)
            sub.setStyleSheet(f"color: {C['muted']}; font-size: {F['font_caption']}px;")
            col.addWidget(sub)
        lay.addLayout(col, 1)
        return wrap

    def _settings_intro(self, title_text: str, desc_text: str) -> QWidget:
        wrap = QWidget()
        box = QVBoxLayout(wrap)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)
        title = QLabel(title_text)
        title.setObjectName("cardTitle")
        desc = QLabel(desc_text)
        desc.setWordWrap(True)
        desc.setObjectName("cardDesc")
        box.addWidget(title)
        box.addWidget(desc)
        return wrap

    def _action_button_style(self, primary: bool = False, *, kind: str | None = None) -> str:
        style = (kind or ("primary" if primary else "default")).lower()
        radius = F["radius_md"]
        marker = f"QPushButton[factoryKey=\"action-{style}\"] {{ }}"
        if style == "primary":
            return marker + (
                f"QPushButton {{ background: {C['accent']}; color: white; border: 1px solid {C['accent']}; "
                f"border-radius: {radius}px; padding: 7px 18px; font-size: 14px; font-weight: 600; }}"
                f"QPushButton:hover {{ background: {C['accent_hover']}; border-color: {C['accent_hover']}; }}"
                f"QPushButton:pressed {{ background: {C['accent_pressed']}; border-color: {C['accent_pressed']}; }}"
                f"QPushButton:disabled {{ background: {C['accent_disabled']}; border-color: {C['accent_disabled']}; color: rgba(255,255,255,0.70); }}"
            )
        if style == "destructive":
            return marker + (
                f"QPushButton {{ background: {C['danger']}; color: white; border: 1px solid {C['danger']}; "
                f"border-radius: {radius}px; padding: 7px 18px; font-size: 14px; font-weight: 600; }}"
                f"QPushButton:hover {{ background: {C['danger_hover']}; border-color: {C['danger_hover']}; }}"
                f"QPushButton:pressed {{ background: {C['danger_hover']}; border-color: {C['danger_hover']}; }}"
                f"QPushButton:disabled {{ background: {C['layer1']}; color: {C['muted']}; border-color: {C['stroke_default']}; }}"
            )
        if style == "subtle":
            return marker + (
                f"QPushButton {{ background: transparent; color: {C['text_soft']}; border: 1px solid transparent; "
                f"border-radius: {radius}px; padding: 7px 14px; font-size: 14px; font-weight: 500; }}"
                f"QPushButton:hover {{ background: {C['layer2']}; color: {C['text']}; }}"
                f"QPushButton:pressed {{ background: {C['layer1']}; }}"
                f"QPushButton:disabled {{ color: {C['muted']}; }}"
            )
        return marker + (
            f"QPushButton {{ background: {C['layer2']}; color: {C['text']}; border: 1px solid {C['stroke_default']}; "
            f"border-radius: {radius}px; padding: 7px 16px; font-size: 14px; font-weight: 500; }}"
            f"QPushButton:hover {{ background: {C['layer3']}; border-color: {C['stroke_hover']}; }}"
            f"QPushButton:pressed {{ background: {C['layer1']}; border-color: {C['stroke_default']}; }}"
            f"QPushButton:disabled {{ background: {C['layer1']}; color: {C['muted']}; border-color: {C['stroke_default']}; }}"
        )

    def _normalize_appearance_mode(self, mode):
        return "light" if str(mode or "").strip().lower() == "light" else "dark"

    def _refresh_theme_button(self):
        btn = getattr(self, "theme_btn", None)
        if btn is None:
            return
        mode = self._normalize_appearance_mode(self.cfg.get("appearance_mode", "light"))
        btn.setText("🌙" if mode == "dark" else "☀")
        btn.setToolTip("切换为浅色主题" if mode == "dark" else "切换为深色主题")

    def _apply_theme(self, mode: str):
        import qt_theme

        normalized = qt_theme.set_theme(mode)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(qt_theme.build_qss())
        try:
            chat_common.set_md_css(chat_common._build_md_css())
        except Exception:
            pass
        apply_mica(self, dark=(normalized == "dark"))
        self._refresh_theme_button()
        self._restyle_factory_widgets()
        try:
            self.style().unpolish(self)
            self.style().polish(self)
            for w in self.findChildren(QWidget):
                w.style().unpolish(w)
                w.style().polish(w)
        except Exception:
            pass
        try:
            self.update()
        except Exception:
            pass

    def _restyle_factory_widgets(self):
        action_variants = {
            "action-primary": lambda: self._action_button_style(primary=True),
            "action-default": lambda: self._action_button_style(),
            "action-subtle": lambda: self._action_button_style(kind="subtle"),
            "action-destructive": lambda: self._action_button_style(kind="destructive"),
            "nav-primary": lambda: self._sidebar_button_style(primary=True),
            "nav-subtle": lambda: self._sidebar_button_style(subtle=True),
            "nav-selected": lambda: self._sidebar_button_style(selected=True),
            "nav-default": lambda: self._sidebar_button_style(),
        }
        for btn in self.findChildren(QPushButton):
            try:
                ss = btn.styleSheet() or ""
            except Exception:
                continue
            if "factoryKey=" not in ss:
                continue
            for key, builder in action_variants.items():
                if f"factoryKey=\"{key}\"" in ss:
                    try:
                        btn.setStyleSheet(builder())
                    except Exception:
                        pass
                    break
        self._restyle_download_page_widgets()
        ib = getattr(self, "input_box", None)
        if ib is not None:
            try:
                ib.setStyleSheet(
                    f"QTextEdit {{ background: transparent; border: none; color: {C['text']}; font-size: 14px; padding: 2px; }}"
                )
            except Exception:
                pass
        dl = getattr(self, "download_log", None)
        if dl is not None:
            try:
                dl.setStyleSheet(
                    f"QTextEdit {{ background: {C['layer2']}; color: {C['code_text']}; border: none; "
                    f"border-radius: {F['radius_md']}px; font-family: Consolas, 'Microsoft YaHei UI'; font-size: 12px; padding: 10px 12px; }}"
                )
            except Exception:
                pass
        scr = getattr(self, "scroll", None)
        if scr is not None:
            try:
                scr.setStyleSheet(f"QScrollArea {{ border: none; background: {C['bg']}; }}" + _SCROLLBAR_STYLE)
            except Exception:
                pass
        msg_root = getattr(self, "msg_root", None)
        if msg_root is not None:
            try:
                msg_root.setStyleSheet(f"background: {C['bg']};")
            except Exception:
                pass
        wi = getattr(self, "welcome_icon", None)
        if wi is not None:
            try:
                wi.setStyleSheet(f"font-size: 42px; color: {C['accent']}; background: transparent;")
            except Exception:
                pass
        for browser in self.findChildren(QTextBrowser):
            try:
                browser.setStyleSheet(
                    f"QTextBrowser {{ background: transparent; color: {C['text_soft']}; border: none; padding: 0; font-size: 14px; }}"
                )
                browser.document().setDefaultStyleSheet(chat_common._MD_CSS)
                html = browser.toHtml()
                browser.setHtml(html)
            except Exception:
                pass

    def _restyle_download_page_widgets(self):
        body_scroll = getattr(self, "download_body_scroll", None)
        if body_scroll is not None:
            try:
                body_scroll.setStyleSheet(f"QScrollArea {{ border: none; background: transparent; }}" + _SCROLLBAR_STYLE)
                body_scroll.viewport().setStyleSheet(f"background: {C['bg']};")
            except Exception:
                pass
        body = getattr(self, "download_body_widget", None)
        if body is not None:
            try:
                body.setStyleSheet("background: transparent;")
            except Exception:
                pass
        dl = getattr(self, "download_log", None)
        if dl is not None:
            try:
                dl.setStyleSheet(
                    f"QTextEdit {{ background: {C['layer2']}; color: {C['code_text']}; border: none; "
                    f"border-radius: {F['radius_md']}px; font-family: Consolas, 'Microsoft YaHei UI'; font-size: 12px; padding: 10px 12px; }}"
                )
                vp = dl.viewport()
                if vp is not None:
                    vp.setStyleSheet(f"background: {C['layer2']};")
            except Exception:
                pass

    def _toggle_appearance_mode(self):
        cur = self._normalize_appearance_mode(self.cfg.get("appearance_mode", "light"))
        new_mode = "light" if cur == "dark" else "dark"
        self.cfg["appearance_mode"] = new_mode
        lz.save_config(self.cfg)
        self._apply_theme(new_mode)
        self._set_status(f"已切换为{'浅色' if new_mode == 'light' else '深色'}主题。")

    def _open_functions_menu(self):
        menu = QMenu(self)
        auto_on = bool(self.cfg.get("autonomous_enabled", False))
        settings_action = menu.addAction("⚙  设置")
        welcome_action = menu.addAction("⌂  欢迎页")
        menu.addSeparator()
        reinject_action = menu.addAction("🛠  重新注入工具示范")
        pet_action = menu.addAction("🐱  启动桌面宠物")
        menu.addSeparator()
        trigger_action = menu.addAction("🤖  立即触发自主任务")
        auto_action = menu.addAction("⏸  禁止空闲自主行动" if auto_on else "▶  允许空闲自主行动")
        menu.addSeparator()
        refresh_action = menu.addAction("↻  刷新会话列表")
        restart_action = menu.addAction("♻  重启内核")
        chosen = menu.exec(self.gear_btn.mapToGlobal(self.gear_btn.rect().bottomRight()))
        if chosen is settings_action:
            self._show_settings()
        elif chosen is welcome_action:
            self._show_welcome()
        elif chosen is reinject_action:
            self._reinject_tools()
        elif chosen is pet_action:
            self._launch_pet()
        elif chosen is trigger_action:
            self._trigger_autonomous()
        elif chosen is auto_action:
            self._toggle_autonomous()
        elif chosen is refresh_action:
            self._refresh_session_list()
        elif chosen is restart_action:
            self._restart_bridge()

    def _reinject_tools(self):
        self._send_cmd({"cmd": "reinject_tools"})

    def _launch_pet(self):
        self._send_cmd({"cmd": "launch_pet"})

    def _trigger_autonomous(self):
        if self._busy:
            QMessageBox.information(self, "忙碌中", "当前任务还在运行，请稍候。")
            return
        if QMessageBox.question(self, "立即触发自主任务", "将向 Agent 发送一次自主任务指令，确定继续？") != QMessageBox.Yes:
            return
        self._send(text=self.AUTO_TASK_TEXT, auto=True)

    def _toggle_autonomous(self):
        new_state = not bool(self.cfg.get("autonomous_enabled", False))
        self.cfg["autonomous_enabled"] = new_state
        lz.save_config(self.cfg)
        QMessageBox.information(
            self,
            "自主行动",
            "已开启：空闲超过 30 分钟会自动触发一次自主任务。"
            if new_state
            else "已关闭：不再自动触发。",
        )
        if new_state and not self._idle_thread_started:
            self._idle_thread_started = True
            threading.Thread(target=self._idle_monitor, daemon=True).start()

    def _idle_monitor(self):
        while True:
            time.sleep(60)
            try:
                if not self.cfg.get("autonomous_enabled", False):
                    continue
                if self._busy:
                    continue
                if time.time() - float(self._last_activity or time.time()) < 1800:
                    continue
                self._last_activity = time.time()
                self._event_queue.put({"event": "launcher_autonomous_trigger"})
            except Exception:
                pass

    def _open_search_filter(self):
        self._open_search()

    def _open_search(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("检索会话内容")
        dlg.setModal(True)
        dlg.resize(740, 580)
        dlg.setMinimumSize(520, 360)
        dlg.setStyleSheet(f"QDialog {{ background: {C['surface']}; }}")

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(12)

        head = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        title = QLabel("检索会话内容")
        title.setObjectName("titleDisplay")
        subtitle = QLabel("输入关键词搜索历史消息，支持按会话分组并跳转到对应消息。")
        subtitle.setObjectName("mutedText")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        head.addLayout(title_box, 1)
        close_btn = QPushButton("×")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setFixedSize(34, 34)
        close_btn.setStyleSheet(self._sidebar_button_style())
        close_btn.clicked.connect(dlg.reject)
        head.addWidget(close_btn, 0, Qt.AlignTop)
        layout.addLayout(head)

        search_bar = QFrame()
        search_bar.setStyleSheet(f"QFrame {{ background: {C['field_alt']}; border-radius: 12px; }}")
        search_row = QHBoxLayout(search_bar)
        search_row.setContentsMargins(12, 8, 10, 8)
        search_row.setSpacing(6)
        search_icon = QLabel("🔍")
        search_icon.setObjectName("mutedText")
        search_row.addWidget(search_icon, 0)
        entry = QLineEdit()
        entry.setPlaceholderText("输入关键词开始检索")
        entry.setText(self._session_filter_keyword)
        entry.setStyleSheet("QLineEdit { background: transparent; border: none; padding: 6px 4px; }")
        search_row.addWidget(entry, 1)
        clear_btn = QPushButton("×")
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.setFixedSize(30, 30)
        clear_btn.setStyleSheet(self._sidebar_button_style())
        search_row.addWidget(clear_btn, 0)
        layout.addWidget(search_bar)

        results = QListWidget()
        results.setStyleSheet(
            f"""
            QListWidget {{
                background: {C['field_bg']};
                border: none;
                border-radius: 12px;
                padding: 8px;
                outline: none;
            }}
            QListWidget::item {{
                background: {C['card']};
                border: 1px solid transparent;
                border-radius: 10px;
                padding: 12px 14px;
                margin: 4px 0;
            }}
            QListWidget::item:hover {{
                background: {C['card_hover']};
            }}
            QListWidget::item:selected {{
                background: {C['active']};
            }}
            """
            + _SCROLLBAR_STYLE
        )
        layout.addWidget(results, 1)

        def snippet(text: str, kw: str, width: int = 80) -> str:
            raw = re.sub(r"\s+", " ", str(text or "").replace("\n", " ").replace("\r", " ")).strip()
            if not raw:
                return ""
            idx = raw.lower().find(kw.lower())
            if idx < 0:
                return raw[:width] + ("…" if len(raw) > width else "")
            pre = 24
            start = max(0, idx - pre)
            end = min(len(raw), idx + len(kw) + (width - pre))
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(raw) else ""
            return prefix + raw[start:end] + suffix

        def populate():
            kw = str(entry.text() or "").strip()
            self._session_filter_keyword = kw
            results.clear()
            if not kw:
                item = QListWidgetItem("输入关键词开始检索")
                item.setFlags(Qt.NoItemFlags)
                results.addItem(item)
                return
            hits = 0
            for meta in lz.list_sessions(self.agent_dir):
                if hits >= 80:
                    break
                try:
                    data = lz.load_session(self.agent_dir, meta["id"])
                except Exception:
                    data = None
                if not data:
                    continue
                for idx, bubble in enumerate(data.get("bubbles") or []):
                    if hits >= 80:
                        break
                    text = str(bubble.get("text") or "")
                    if kw.lower() not in text.lower():
                        continue
                    role = "🙂" if bubble.get("role") == "user" else "🤖"
                    title_text = str(meta.get("title") or "(未命名)")[:38]
                    when = time.strftime("%m-%d %H:%M", time.localtime(meta.get("updated_at", 0) or 0))
                    source = _session_source_label(data)
                    body = snippet(text, kw)
                    item_text = f"{role}  {title_text} · 第 {idx + 1} 条消息\n{source} · {when}\n{body}"
                    item = QListWidgetItem(item_text)
                    item.setData(Qt.UserRole, {"sid": meta.get("id"), "bubble_index": idx})
                    item.setToolTip(body)
                    results.addItem(item)
                    hits += 1
            if hits == 0:
                item = QListWidgetItem(f"未找到包含“{kw}”的消息")
                item.setFlags(Qt.NoItemFlags)
                results.addItem(item)

        def activate(item):
            data = item.data(Qt.UserRole)
            if not isinstance(data, dict):
                return
            sid = data.get("sid")
            bubble_index = int(data.get("bubble_index", 0) or 0)
            dlg.accept()
            self._load_session_by_id(sid)
            QTimer.singleShot(220, lambda: self._jump_to_bubble(bubble_index))

        clear_btn.clicked.connect(lambda: (entry.clear(), entry.setFocus()))
        entry.textChanged.connect(lambda _text: populate())
        entry.returnPressed.connect(populate)
        results.itemActivated.connect(activate)
        results.itemDoubleClicked.connect(activate)
        populate()
        entry.setFocus()
        dlg.exec()
