from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid

from PySide6.QtCore import QByteArray, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QIcon, QKeyEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import launcher as lz


def _lz_dark(value, fallback: str) -> str:
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        return str(value[1])
    if isinstance(value, str) and value:
        return value
    return fallback


C = {
    "bg": QColor(_lz_dark(lz.COLOR_APP_BG, "#1c1e22")),
    "panel": QColor(_lz_dark(lz.COLOR_PANEL, "#23262c")),
    "surface": QColor(_lz_dark(lz.COLOR_SURFACE, "#1c1e22")),
    "sidebar_bg": QColor(_lz_dark(lz.COLOR_SIDEBAR_BG, "#181a1e")),
    "card": QColor(_lz_dark(lz.COLOR_CARD, "#2a2d33")),
    "card_hover": QColor(_lz_dark(lz.COLOR_CARD_HOVER, "#34383f")),
    "field_bg": QColor(_lz_dark(lz.COLOR_FIELD_BG, "#14161a")),
    "field_alt": QColor(_lz_dark(lz.COLOR_FIELD_ALT, "#262a31")),
    "border": QColor(_lz_dark(lz.COLOR_DIVIDER, "#3a3f47")),
    "active": QColor(_lz_dark(lz.COLOR_ACTIVE, "#2d3544")),
    "active_hover": QColor(_lz_dark(lz.COLOR_ACTIVE_HOVER, "#34405a")),
    "code_bg": QColor(_lz_dark(lz.COLOR_CODE_BG, "#14161a")),
    "text": _lz_dark(lz.COLOR_TEXT, "#e8ecf2"),
    "text_soft": _lz_dark(lz.COLOR_TEXT_SOFT, "#cfd4dc"),
    "muted": _lz_dark(lz.COLOR_MUTED, "#8a8f99"),
    "code_text": _lz_dark(lz.COLOR_CODE_TEXT, "#dde1e7"),
    "accent": _lz_dark(lz.COLOR_ACCENT, "#4f8cff"),
    "accent_hover": _lz_dark(lz.COLOR_ACCENT_HOVER, "#3a75e0"),
    "danger": _lz_dark(lz.COLOR_DANGER_BG, "#c24848"),
    "danger_hover": _lz_dark(lz.COLOR_DANGER_BG_HOVER, "#a13a3a"),
    "danger_text": _lz_dark(lz.COLOR_DANGER_TEXT, "#ea7070"),
}

SCROLLBAR_STYLE = """
QScrollBar:vertical { width: 7px; background: transparent; border: none; }
QScrollBar::handle:vertical {
    background: rgba(148,163,184,0.30); border-radius: 3px; min-height: 24px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
"""

_SVG_COPY = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>'
_SVG_BOT = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/><path d="M5 3v4"/><path d="M7 5H3"/></svg>'
_SVG_USER = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>'
_SVG_PLUS = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" x2="12" y1="5" y2="19"/><line x1="5" x2="19" y1="12" y2="12"/></svg>'
_SVG_REFRESH = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>'
_SVG_TRASH = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c0-1-1-2-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/></svg>'
_SVG_SEND = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13"/><path d="m22 2-7 20-4-9-9-4Z"/></svg>'
_SVG_STOP = '<svg viewBox="0 0 24 24" fill="{c}" stroke="none"><rect width="10" height="10" x="7" y="7" rx="1.5" ry="1.5"/></svg>'

_MD_CSS = f"""
body {{ color: {C['text_soft']}; font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; font-size: 13px; line-height: 1.65; }}
h1 {{ color: {C['text']}; font-size: 20px; font-weight: 700; border-bottom: 1px solid {C['border'].name()}; padding-bottom: 4px; margin-top: 16px; }}
h2 {{ color: {C['text']}; font-size: 17px; font-weight: 700; border-bottom: 1px solid {C['border'].name()}; padding-bottom: 3px; margin-top: 14px; }}
h3 {{ color: {C['text']}; font-size: 15px; font-weight: 600; margin-top: 12px; }}
code {{ background: {C['field_alt'].name()}; color: {C['code_text']}; padding: 1px 4px; border-radius: 3px; font-family: Consolas, "Courier New", monospace; font-size: 12px; }}
pre  {{ background: {C['code_bg'].name()}; border: 1px solid {C['border'].name()}; border-radius: 8px; padding: 10px 12px; margin: 8px 0; }}
pre code {{ background: transparent; padding: 0; color: {C['code_text']}; }}
a {{ color: {C['accent']}; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
blockquote {{ border-left: 3px solid {C['accent']}; margin: 8px 0; padding: 4px 0 4px 12px; color: {C['muted']}; }}
table {{ border-collapse: collapse; margin: 8px 0; }}
th, td {{ border: 1px solid {C['border'].name()}; padding: 5px 10px; }}
th {{ background: {C['field_alt'].name()}; color: {C['text']}; font-weight: 700; }}
hr {{ border: none; border-top: 1px solid {C['border'].name()}; margin: 12px 0; }}
ul, ol {{ padding-left: 22px; margin: 4px 0; }}
li {{ margin: 2px 0; }}
p {{ margin: 6px 0; }}
"""

_ICON_CACHE: dict[str, QIcon] = {}


def _md_to_html(text: str) -> str:
    try:
        import markdown

        return markdown.markdown(
            text or "",
            extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
        )
    except Exception:
        pass
    text = text or ""
    html, in_code, in_ul = [], False, False
    for raw in text.split("\n"):
        if raw.strip().startswith("```"):
            html.append("</code></pre>" if in_code else "<pre><code>")
            in_code = not in_code
            continue
        if in_code:
            html.append(raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            continue
        line = raw
        line = re.sub(r"`([^`]+)`", r"<code>\1</code>", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
        line = re.sub(r"\*(.+?)\*", r"<i>\1</i>", line)
        line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', line)
        if re.match(r"^#{1,6}\s", line):
            lvl = len(line.split()[0])
            line = f"<h{lvl}>{line[lvl:].strip()}</h{lvl}>"
        elif re.match(r"^-{3,}$|^_{3,}$|^\*{3,}$", line.strip()):
            line = "<hr>"
        elif re.match(r"^\s*[-*+]\s", line):
            content = re.sub(r"^\s*[-*+]\s", "", line)
            if not in_ul:
                html.append("<ul>")
                in_ul = True
            line = f"<li>{content}</li>"
        else:
            if in_ul:
                html.append("</ul>")
                in_ul = False
            line = f"<p>{line}</p>" if line.strip() else ""
        html.append(line)
    if in_code:
        html.append("</code></pre>")
    if in_ul:
        html.append("</ul>")
    return "\n".join(html)


def _svg_icon(key: str, svg_template: str, color: str = "#94a3b8", size: int = 16) -> QIcon:
    cache_key = f"{key}_{color}_{size}"
    if cache_key not in _ICON_CACHE:
        from PySide6.QtSvg import QSvgRenderer

        data = QByteArray(svg_template.format(c=color).encode("utf-8"))
        renderer = QSvgRenderer(data)
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        _ICON_CACHE[cache_key] = QIcon(pixmap)
    return _ICON_CACHE[cache_key]


def _fit_browser_height(browser: QTextBrowser) -> None:
    doc = browser.document()
    viewport_w = browser.viewport().width()
    width = viewport_w if viewport_w > 40 else 560
    doc.setTextWidth(width)
    browser.setFixedHeight(max(38, int(doc.size().height() + 10)))


def _session_copy(data):
    return json.loads(json.dumps(data or {}, ensure_ascii=False))


def _assistant_segment_markdown(text: str, streaming: bool = False) -> str:
    body = lz._strip_turn_marker(text or "")
    shown = lz._assistant_visible_markup(body or text or "").strip()
    if not shown:
        shown = body.strip() or lz._turn_marker_title(text or "") or (text or "").strip() or "…"
    if streaming:
        return (shown.rstrip() or "处理中…") + "\n\n▌"
    return shown


def _session_source_label(session) -> str:
    if not isinstance(session, dict):
        return ""
    if session.get("session_source_label"):
        return str(session.get("session_source_label"))
    if session.get("imported_from"):
        return "官方导入"
    return "启动器"


class InputTextEdit(QTextEdit):
    def __init__(self, submit_cb, parent=None):
        super().__init__(parent)
        self._submit_cb = submit_cb

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if (
            event.key() in (Qt.Key_Return, Qt.Key_Enter)
            and not (event.modifiers() & Qt.ShiftModifier)
        ):
            self._submit_cb()
            return
        super().keyPressEvent(event)


class TurnFold(QFrame):
    def __init__(self, title: str, text: str, parent=None):
        super().__init__(parent)
        self._title = title or "处理中"
        self._text = text or ""
        self._expanded = False

        self.setStyleSheet(
            f"QFrame {{ background: {C['field_alt'].name()}; border: none; border-radius: 8px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self._button = QPushButton(f"▸ {self._title}")
        self._button.setCursor(QCursor(Qt.PointingHandCursor))
        self._button.setStyleSheet(
            f"QPushButton {{ background: {C['card_hover'].name()}; color: {C['text_soft']}; border: none; border-radius: 6px; text-align: left; padding: 8px 10px; font-size: 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {C['card'].name()}; }}"
        )
        self._button.clicked.connect(self.toggle)
        layout.addWidget(self._button)

        self._body = QTextBrowser()
        self._body.setReadOnly(True)
        self._body.setOpenExternalLinks(True)
        self._body.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._body.document().setDefaultStyleSheet(_MD_CSS)
        self._body.setStyleSheet(
            f"QTextBrowser {{ background: transparent; border: none; color: {C['text_soft']}; font-size: 13px; }}"
        )
        self._body.hide()
        layout.addWidget(self._body)
        self.set_text(text)

    def toggle(self):
        self._expanded = not self._expanded
        self._button.setText(("▾ " if self._expanded else "▸ ") + self._title)
        self._body.setVisible(self._expanded)
        if self._expanded:
            _fit_browser_height(self._body)

    def set_text(self, text: str):
        self._text = text or ""
        shown = _assistant_segment_markdown(self._text, streaming=False)
        self._body.setHtml(_md_to_html(shown))
        if self._expanded:
            _fit_browser_height(self._body)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._expanded:
            _fit_browser_height(self._body)


class MessageRow(QWidget):
    def __init__(self, text: str, role: str, parent=None):
        super().__init__(parent)
        self._text = text or ""
        self._role = role
        self._finished = True
        self._stream_prefix_signature = None
        self._stream_live_browser = None

        is_user = role == "user"
        self.setStyleSheet("background: transparent;")

        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 6, 16, 6)
        outer.setSpacing(0)
        outer.setAlignment(Qt.AlignTop)

        if is_user:
            outer.addStretch(1)
            bubble = QFrame()
            bubble.setMaximumWidth(960)
            bubble.setStyleSheet(
                f"QFrame {{ background: {C['accent']}; border: none; border-radius: 14px; }}"
            )
            bubble_layout = QVBoxLayout(bubble)
            bubble_layout.setContentsMargins(14, 10, 14, 10)
            bubble_layout.setSpacing(0)
            label = QLabel(self._text)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
            label.setStyleSheet("QLabel { color: white; font-size: 14px; line-height: 1.6; }")
            bubble_layout.addWidget(label)
            outer.addWidget(bubble, 0, Qt.AlignRight | Qt.AlignTop)
            self._label = label
            self._content_layout = None
            self._action_row = None
            self._bubble = bubble
        else:
            wrap = QWidget()
            wrap_layout = QVBoxLayout(wrap)
            wrap_layout.setContentsMargins(6, 0, 80, 0)
            wrap_layout.setSpacing(4)

            role_row = QHBoxLayout()
            role_row.setContentsMargins(0, 0, 0, 0)
            role_row.setSpacing(0)
            role_lbl = QLabel("助手")
            role_lbl.setStyleSheet(f"color: {C['muted']}; font-size: 12px; font-weight: 500;")
            role_row.addWidget(role_lbl)
            role_row.addStretch(1)
            wrap_layout.addLayout(role_row)

            host = QWidget()
            host_layout = QVBoxLayout(host)
            host_layout.setContentsMargins(0, 0, 0, 0)
            host_layout.setSpacing(6)
            self._content_layout = host_layout
            wrap_layout.addWidget(host)

            outer.addWidget(wrap, 1, Qt.AlignLeft | Qt.AlignTop)
            self._label = None
            self._action_row = None
            self._bubble = None
        self.set_text(self._text)

    def enterEvent(self, event):
        super().enterEvent(event)

    def leaveEvent(self, event):
        super().leaveEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._role != "user" and self._content_layout is not None:
            for browser in self.findChildren(QTextBrowser):
                _fit_browser_height(browser)

    def _clear_assistant_widgets(self):
        if self._content_layout is None:
            return
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._stream_prefix_signature = None
        self._stream_live_browser = None

    def _make_browser(self, markdown_text: str) -> QTextBrowser:
        browser = QTextBrowser()
        browser.setReadOnly(True)
        browser.setOpenExternalLinks(True)
        browser.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        browser.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        browser.document().setDefaultStyleSheet(_MD_CSS)
        browser.setStyleSheet(
            f"QTextBrowser {{ background: transparent; color: {C['text_soft']}; border: none; padding: 0; font-size: 14px; }}"
        )
        browser.setHtml(_md_to_html(markdown_text))
        _fit_browser_height(browser)
        return browser

    def _set_browser_markdown(self, browser: QTextBrowser, markdown_text: str) -> None:
        browser.setHtml(_md_to_html(markdown_text))
        _fit_browser_height(browser)

    def set_finished(self, done: bool):
        self._finished = done
        self.set_text(self._text)

    def set_text(self, text: str):
        self._text = text or ""
        if self._role == "user":
            self._label.setText(self._text)
            self._label.adjustSize()
            return

        segments = lz.fold_turns(self._text)
        if not segments:
            segments = [{"type": "text", "content": self._text or "…"}]

        if not self._finished:
            last = segments[-1]
            last_type = str(last.get("type") or "text")
            if last_type == "text":
                prefix_segments = segments[:-1]
                live_content = last.get("content") or ""
            else:
                prefix_segments = segments
                live_content = ""
            prefix_signature = tuple(
                (
                    str(seg.get("type") or "text"),
                    str(seg.get("title") or ""),
                    str(seg.get("content") or ""),
                )
                for seg in prefix_segments
            )
            browser = getattr(self, "_stream_live_browser", None)
            try:
                browser_ok = browser is not None and browser.parent() is not None
            except Exception:
                browser_ok = False
            if prefix_signature == self._stream_prefix_signature and browser_ok:
                self._set_browser_markdown(
                    browser,
                    _assistant_segment_markdown(live_content, streaming=True),
                )
                return

        self._clear_assistant_widgets()

        for idx, seg in enumerate(segments):
            content = seg.get("content") or ""
            is_last = idx == len(segments) - 1
            if seg.get("type") == "fold":
                fold = TurnFold(seg.get("title") or "处理中", content, self)
                self._content_layout.addWidget(fold)
                continue
            shown = _assistant_segment_markdown(content, streaming=(not self._finished and is_last))
            browser = self._make_browser(shown)
            self._content_layout.addWidget(browser)
            if not self._finished and is_last:
                self._stream_live_browser = browser

        if not self._finished:
            last = segments[-1]
            last_type = str(last.get("type") or "text")
            if last_type == "text":
                prefix_segments = segments[:-1]
            else:
                prefix_segments = segments
            self._stream_prefix_signature = tuple(
                (
                    str(seg.get("type") or "text"),
                    str(seg.get("title") or ""),
                    str(seg.get("content") or ""),
                )
                for seg in prefix_segments
            )


class QtChatWindow(QMainWindow):
    def __init__(self, agent_dir: str):
        super().__init__()
        self.agent_dir = os.path.abspath(agent_dir)
        if not lz.is_valid_agent_dir(self.agent_dir):
            raise RuntimeError(f"GenericAgent 目录无效：{self.agent_dir}")

        self.bridge_proc = None
        self._stderr_buf = []
        self._event_queue: queue.Queue = queue.Queue()
        self._drain_timer = QTimer(self)
        self._drain_timer.timeout.connect(self._drain_events)
        self._drain_timer.start(40)

        self._stream_flush_timer = QTimer(self)
        self._stream_flush_timer.setSingleShot(True)
        self._stream_flush_timer.timeout.connect(self._flush_stream_render)

        self.llms = []
        self.current_session = None
        self._selected_session_id = None
        self._pending_state_session = None
        self._pending_official_restore = None
        self._pending_import_queue = []
        self._pending_legacy_meta = None
        self._importing = False
        self._legacy_items = []
        self._busy = False
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

        self.setWindowTitle("GenericAgent Launcher Chat")
        self.resize(1440, 920)
        self.setMinimumSize(1100, 700)
        self._build_ui()
        self._refresh_sessions()
        self._reset_chat_area("选择一个会话，或新建会话开始聊天。")
        QTimer.singleShot(0, self._safe_start_bridge)

    def _build_ui(self):
        self.setStyleSheet(
            f"""
            QMainWindow {{ background: {C['bg'].name()}; }}
            QWidget {{ color: {C['text']}; font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; }}
            QListWidget {{
                background: transparent;
                border: none;
                outline: none;
                padding: 4px;
            }}
            QListWidget::item {{
                background: transparent;
                border: 1px solid transparent;
                border-radius: 10px;
                padding: 10px 12px;
                margin: 3px 0;
            }}
            QListWidget::item:hover {{
                background: {C['card'].name()};
            }}
            QListWidget::item:selected {{
                background: {C['active'].name()};
                border: 1px solid {C['active_hover'].name()};
            }}
            QLineEdit, QTextEdit {{
                background: {C['field_bg'].name()};
                border: 1px solid {C['border'].name()};
                border-radius: 12px;
                color: {C['text']};
                padding: 10px 12px;
                selection-background-color: rgba(96,165,250,0.35);
            }}
            QPushButton {{
                background: {C['field_alt'].name()};
                border: 1px solid {C['border'].name()};
                border-radius: 10px;
                color: {C['text']};
                padding: 8px 12px;
            }}
            QPushButton:hover {{ background: {C['card_hover'].name()}; }}
            QComboBox {{
                background: {C['field_bg'].name()};
                border: 1px solid {C['border'].name()};
                border-radius: 10px;
                padding: 7px 10px;
                color: {C['text']};
            }}
            QComboBox QAbstractItemView {{
                background: {C['panel'].name()};
                color: {C['text']};
                border: 1px solid {C['border'].name()};
                selection-background-color: rgba(96,165,250,0.18);
            }}
            """
        )

        root = QSplitter()
        root.setOrientation(Qt.Horizontal)
        root.setHandleWidth(1)
        self.setCentralWidget(root)

        sidebar = QFrame()
        sidebar.setStyleSheet(
            f"QFrame {{ background: {C['sidebar_bg'].name()}; border-right: 1px solid {C['border'].name()}; }}"
        )
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(14, 14, 14, 14)
        side_layout.setSpacing(10)

        title = QLabel("会话")
        title.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {C['text_soft']};")
        side_layout.addWidget(title)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.new_btn = QPushButton("新建")
        self.new_btn.setIcon(_svg_icon("plus", _SVG_PLUS))
        self.new_btn.clicked.connect(self._new_session)
        btn_row.addWidget(self.new_btn)

        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setIcon(_svg_icon("refresh", _SVG_REFRESH))
        self.refresh_btn.clicked.connect(self._refresh_official_sessions)
        btn_row.addWidget(self.refresh_btn)

        self.delete_btn = QPushButton("删除")
        self.delete_btn.setIcon(_svg_icon("trash", _SVG_TRASH))
        self.delete_btn.clicked.connect(self._delete_selected_session)
        btn_row.addWidget(self.delete_btn)
        side_layout.addLayout(btn_row)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索会话标题 / 来源")
        self.search_edit.textChanged.connect(self._refresh_sessions)
        side_layout.addWidget(self.search_edit)

        self.session_list = QListWidget()
        self.session_list.currentItemChanged.connect(self._on_session_item_changed)
        self.session_list.setStyleSheet("QListWidget { background: transparent; }" + SCROLLBAR_STYLE)
        side_layout.addWidget(self.session_list, 1)

        main = QFrame()
        main.setStyleSheet(f"QFrame {{ background: {C['bg'].name()}; }}")
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        head = QFrame()
        head.setStyleSheet(
            f"QFrame {{ background: {C['panel'].name()}; border: none; }}"
        )
        head.setFixedHeight(56)
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(18, 10, 18, 10)
        head_layout.setSpacing(12)

        self.chat_title = QLabel("GenericAgent")
        self.chat_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {C['text_soft']};")
        self.mode_label = QLabel("当前无活动会话")
        self.mode_label.setStyleSheet(f"font-size: 12px; color: {C['muted']};")
        self.mode_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        head_layout.addWidget(self.chat_title, 0, Qt.AlignVCenter)
        head_layout.addStretch(1)
        head_layout.addWidget(self.mode_label, 0, Qt.AlignRight | Qt.AlignVCenter)

        main_layout.addWidget(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }" + SCROLLBAR_STYLE)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        self.msg_root = QWidget()
        self.msg_layout = QVBoxLayout(self.msg_root)
        self.msg_layout.setContentsMargins(0, 0, 0, 0)
        self.msg_layout.setSpacing(0)
        self.msg_layout.addStretch(1)
        self.scroll.setWidget(self.msg_root)
        main_layout.addWidget(self.scroll, 1)

        footer = QFrame()
        footer.setStyleSheet(
            f"QFrame {{ background: {C['panel'].name()}; border: none; }}"
        )
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(16, 14, 16, 14)
        footer_layout.setSpacing(0)

        composer = QFrame()
        composer.setStyleSheet(
            f"QFrame {{ background: {C['card'].name()}; border: none; border-radius: 14px; }}"
        )
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(12, 10, 12, 8)
        composer_layout.setSpacing(8)

        self.input_box = InputTextEdit(self._handle_send)
        self.input_box.setPlaceholderText("输入消息，Enter 发送，Shift+Enter 换行")
        self.input_box.setMinimumHeight(78)
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

        self.stop_btn = QPushButton("中断")
        self.stop_btn.setIcon(_svg_icon("stop", _SVG_STOP, "#ffffff"))
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; border-radius: 8px; color: {C['danger_text']}; padding: 8px 12px; }}"
            f"QPushButton:hover {{ background: rgba(194,72,72,0.18); }}"
            f"QPushButton:disabled {{ color: rgba(255,255,255,0.40); background: transparent; }}"
        )
        self.stop_btn.clicked.connect(self._abort)
        tool_row.addWidget(self.stop_btn)

        self.send_btn = QPushButton("发送")
        self.send_btn.setIcon(_svg_icon("send", _SVG_SEND, "#ffffff"))
        self.send_btn.setStyleSheet(
            f"QPushButton {{ background: {C['accent']}; border: none; border-radius: 8px; color: white; padding: 8px 16px; font-weight: 700; }}"
            f"QPushButton:hover {{ background: {C['accent_hover']}; }}"
            "QPushButton:disabled { background: #64748b; color: #e2e8f0; }"
        )
        self.send_btn.clicked.connect(self._handle_send)
        tool_row.addWidget(self.send_btn)

        composer_layout.addLayout(tool_row)

        self.session_token_tree_label = QLabel("Token 估算\n└ 暂无数据")
        self.session_token_tree_label.setStyleSheet(
            f"font-size: 12px; color: {C['muted']}; font-family: Consolas, 'Microsoft YaHei UI';"
        )
        self.session_token_tree_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.session_token_tree_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        composer_layout.addWidget(self.session_token_tree_label)

        self.session_mode_label = QLabel("当前会话：新进程，尚未发送消息")
        self.session_mode_label.setStyleSheet(f"font-size: 12px; color: {C['muted']};")
        composer_layout.addWidget(self.session_mode_label)

        self.status_label = QLabel("正在启动桥接进程…")
        self.status_label.setStyleSheet(f"font-size: 12px; color: {C['text_soft']};")
        composer_layout.addWidget(self.status_label)

        self.token_label = self.session_token_tree_label
        footer_layout.addWidget(composer)
        main_layout.addWidget(footer)

        root.addWidget(sidebar)
        root.addWidget(main)
        root.setStretchFactor(0, 0)
        root.setStretchFactor(1, 1)
        root.setSizes([320, 1120])

    def _session_official_log(self, session):
        if not isinstance(session, dict):
            return ""
        candidates = [
            lz._canon_path(session.get("imported_from")),
            lz._canon_path(session.get("official_log_path")),
            lz._canon_path(session.get("restored_from_official")),
        ]
        for path in candidates:
            if path and os.path.isfile(path):
                return path
        for path in candidates:
            if path:
                return path
        return ""

    def _session_external_paths(self, session):
        if not isinstance(session, dict):
            return []
        model_root = os.path.join(self.agent_dir, "temp", "model_responses")
        paths = []
        for raw in (session.get("imported_from"), session.get("official_log_path")):
            path = lz._canon_path(raw)
            if not path or path in paths:
                continue
            if lz._is_under_dir(path, model_root):
                paths.append(path)
        return paths

    def _bind_session_to_current_bridge(self, session):
        if not isinstance(session, dict):
            return
        session["process_pid"] = getattr(self.bridge_proc, "pid", None)
        if not session.get("imported_from"):
            session["official_log_path"] = lz._official_log_path(self.agent_dir, session["process_pid"])

    def _ensure_session_usage_metadata(self, session):
        if not isinstance(session, dict):
            return
        lz._normalize_token_usage_inplace(session)
        channel_id = str(session.get("channel_id") or "").strip().lower()
        if not channel_id:
            channel_id = "official" if session.get("imported_from") else "launcher"
        session["channel_id"] = channel_id
        session["channel_label"] = lz._usage_channel_label(channel_id)
        usage = session.get("token_usage") or {}
        usage["channel_id"] = channel_id
        usage["channel_label"] = lz._usage_channel_label(channel_id)
        session["token_usage"] = usage

    def _persist_session(self, session):
        if not isinstance(session, dict):
            return
        self._ensure_session_usage_metadata(session)
        lz.save_session(self.agent_dir, session)
        self._selected_session_id = session.get("id")
        self._refresh_sessions()

    def _request_backend_state(self, session_id=None):
        sid = session_id or ((self.current_session or {}).get("id"))
        if not sid or not self._bridge_ready:
            return
        self._state_request_seq += 1
        self._send_cmd({"cmd": "get_state", "session_id": sid, "request_id": self._state_request_seq})

    def _apply_state_to_session(self, session_id, backend_history, agent_history):
        if not session_id:
            return
        target = None
        if self.current_session and self.current_session.get("id") == session_id:
            target = self.current_session
        else:
            try:
                target = lz.load_session(self.agent_dir, session_id)
            except Exception:
                target = None
        if not target:
            return
        target["backend_history"] = list(backend_history or [])
        target["agent_history"] = list(agent_history or [])
        if self.current_session and self.current_session.get("id") == session_id:
            self.current_session = target
        self._persist_session(target)

    def _current_llm_name(self):
        for llm in self.llms:
            if llm.get("current"):
                return str(llm.get("name") or "").strip()
        idx = self.llm_combo.currentIndex()
        if idx >= 0:
            return str(self.llm_combo.itemText(idx) or "").strip()
        return ""

    def _sync_llm_combo(self):
        self._ignore_llm_change = True
        self.llm_combo.clear()
        current_idx = -1
        for pos, llm in enumerate(self.llms):
            self.llm_combo.addItem(str(llm.get("name") or "(未命名)"), llm.get("idx"))
            if llm.get("current"):
                current_idx = pos
        if current_idx >= 0:
            self.llm_combo.setCurrentIndex(current_idx)
        self.llm_combo.setEnabled(bool(self.llms))
        if not self.llms:
            self.llm_combo.addItem("未配置 LLM", -1)
            self.llm_combo.setEnabled(False)
        self._ignore_llm_change = False

    def _on_llm_changed(self, index: int):
        if self._ignore_llm_change or index < 0 or not self._bridge_ready:
            return
        target = self.llm_combo.itemData(index)
        if target is None or int(target) < 0:
            return
        self._send_cmd({"cmd": "switch_llm", "idx": int(target)})

    def _set_status(self, text: str):
        self.status_label.setText(text)

    def _message_row_insert_index(self) -> int:
        return max(0, self.msg_layout.count() - 1)

    def _clear_messages(self):
        self._stream_row = None
        self._current_stream_text = ""
        self._pending_stream_text = None
        while self.msg_layout.count() > 1:
            item = self.msg_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _reset_chat_area(self, placeholder: str | None = None):
        self._clear_messages()
        if placeholder:
            label = QLabel(placeholder)
            label.setAlignment(Qt.AlignCenter)
            label.setWordWrap(True)
            label.setStyleSheet("color: #94a3b8; font-size: 14px; padding: 40px 20px;")
            self.msg_layout.insertWidget(0, label)
        self._update_header_labels()
        self._refresh_token_label()

    def _add_message_row(self, role: str, text: str, finished: bool = True):
        row = MessageRow(text, role, self.msg_root)
        row.set_finished(finished)
        self.msg_layout.insertWidget(self._message_row_insert_index(), row)
        self._scroll_to_bottom()
        return row

    def _render_session(self, session):
        self._clear_messages()
        if not session:
            self._reset_chat_area("选择一个会话，或新建会话开始聊天。")
            return
        bubbles = list(session.get("bubbles") or [])
        if not bubbles:
            self._reset_chat_area("当前会话还没有消息。")
            return
        for bubble in bubbles:
            self._add_message_row(bubble.get("role", "assistant"), bubble.get("text", ""), finished=True)
        self._update_header_labels()
        self._refresh_token_label()
        self._scroll_to_bottom(force=True)

    def _update_header_labels(self):
        session = self.current_session
        if not session:
            self.mode_label.setText("当前无活动会话")
            self._refresh_session_mode_label()
            return
        title = str(session.get("title") or "未命名会话").strip() or "未命名会话"
        parts = [title, _session_source_label(session)]
        pid = session.get("process_pid")
        if pid:
            parts.append(f"进程 {pid}")
        if session.get("imported_from"):
            parts.append("官方恢复上下文")
        self.mode_label.setText(" | ".join(parts))
        self._refresh_session_mode_label()

    def _refresh_token_label(self):
        session = self.current_session
        if not isinstance(session, dict):
            self.session_token_tree_label.setText("Token 估算\n└ 暂无数据")
            return
        self._ensure_session_usage_metadata(session)
        summary = self._session_token_summary(include_live=True)
        if summary["total_tokens"] == 0 and summary["live_output_tokens"] == 0:
            self.session_token_tree_label.setText("Token 估算\n└ 暂无数据")
            return
        last_line = (
            f"└ 本轮流式 {summary['live_output_tokens']}"
            if summary["live_output_tokens"] > 0
            else f"└ 轮次 {summary['turns']}"
        )
        mode = summary.get("mode") or "estimate_chars_div_2_5"
        if mode == "provider_usage":
            title = "Token 计数（真实）"
        elif mode == "mixed_provider_and_estimate":
            title = "Token 计数（混合）"
        else:
            title = "Token 计数（估算）"
        if summary["channel_label"]:
            title += f" · {summary['channel_label']}"
        self.session_token_tree_label.setText(
            f"{title}\n"
            f"├ 输入 {summary['input_tokens']}\n"
            f"├ 输出 {summary['output_tokens']}\n"
            f"├ 总计 {summary['total_tokens']}\n"
            f"{last_line}"
        )

    def _session_token_summary(self, include_live: bool = False):
        session = self.current_session or {}
        usage = session.get("token_usage") or {}
        events = list(usage.get("events") or [])
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens) or (input_tokens + output_tokens))
        live_output_tokens = 0
        if include_live and self._busy and self._current_stream_text:
            live_output_tokens = lz._estimate_tokens(self._current_stream_text)
            target = None
            for ev in reversed(events):
                if self._active_token_event_ts is not None and float(ev.get("ts", 0) or 0) == float(self._active_token_event_ts):
                    target = ev
                    break
            if target is not None:
                output_tokens = max(0, output_tokens - int(target.get("output_tokens", 0) or 0) + live_output_tokens)
                total_tokens = input_tokens + output_tokens
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "turns": int(usage.get("turns", 0) or 0),
            "mode": str(usage.get("mode") or "estimate_chars_div_2_5").strip(),
            "channel_label": str(
                session.get("channel_label")
                or usage.get("channel_label")
                or lz._usage_channel_label(session.get("channel_id") or usage.get("channel_id") or "launcher")
            ).strip(),
            "live_output_tokens": live_output_tokens,
        }

    def _refresh_session_mode_label(self):
        label = getattr(self, "session_mode_label", None)
        if label is None:
            return
        current = self.current_session or {}
        pid = current.get("process_pid")
        if pid:
            label.setText(f"当前会话：进程 {pid}")
        else:
            label.setText("当前会话：新进程，尚未发送消息")

    def _on_scroll_changed(self, value: int):
        bar = self.scroll.verticalScrollBar()
        self._user_scrolled_up = value < bar.maximum() - 40

    def _scroll_to_bottom(self, force: bool = False):
        if self._user_scrolled_up and not force:
            return
        QTimer.singleShot(30, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))

    def _session_list_signature(self, items, keyword: str):
        wanted = self._selected_session_id or ((self.current_session or {}).get("id"))
        return (
            wanted,
            keyword,
            tuple(
                (
                    meta.get("id"),
                    meta.get("title"),
                    meta.get("updated_at"),
                    bool(meta.get("pinned", False)),
                    meta.get("imported_from"),
                )
                for meta in items
            ),
        )

    def _refresh_sessions(self):
        keyword = self.search_edit.text().strip().lower() if hasattr(self, "search_edit") else ""
        items = list(lz.list_sessions(self.agent_dir))
        signature = self._session_list_signature(items, keyword)
        if signature == getattr(self, "_last_session_list_signature", None):
            return
        wanted = self._selected_session_id or ((self.current_session or {}).get("id"))
        self._ignore_session_select = True
        self.session_list.clear()
        for meta in items:
            source_label = ""
            try:
                data = lz.load_session(self.agent_dir, meta["id"])
            except Exception:
                data = None
            if data:
                source_label = _session_source_label(data)
            haystack = f"{meta.get('title', '')} {source_label}".lower()
            if keyword and keyword not in haystack:
                continue
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(meta.get("updated_at", 0) or 0))
            text = f"{meta.get('title', '(未命名)')}\n{source_label or '未知来源'} · {when}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, meta.get("id"))
            self.session_list.addItem(item)
            if wanted and meta.get("id") == wanted:
                self.session_list.setCurrentItem(item)
        self._ignore_session_select = False
        self._last_session_list_signature = signature

    def _on_session_item_changed(self, current, previous):
        if self._ignore_session_select or current is None:
            return
        sid = current.data(Qt.UserRole)
        if not sid:
            return
        self._load_session_by_id(sid)

    def _load_session_by_id(self, sid: str):
        if self._busy:
            QMessageBox.information(self, "忙碌中", "当前还在生成，请先等待结束或手动中断。")
            return
        data = lz.load_session(self.agent_dir, sid)
        if not data:
            self._refresh_sessions()
            QMessageBox.warning(self, "会话不存在", "该会话已经失效，请重新选择。")
            return
        self._selected_session_id = sid
        if data.get("imported_from"):
            self._restore_imported_session(data)
            return
        self.current_session = data
        self._bind_session_to_current_bridge(self.current_session)
        self._render_session(self.current_session)
        if self._bridge_ready:
            self._send_cmd(
                {
                    "cmd": "set_state",
                    "backend_history": data.get("backend_history") or [],
                    "agent_history": data.get("agent_history") or [],
                }
            )
            self._request_backend_state(sid)
            self._set_status("已载入本地会话。")
        else:
            self._pending_state_session = _session_copy(data)
            self._set_status("桥接进程启动中，稍后载入会话…")

    def _delete_session_external_state(self, session, blacklist_on_fail: bool = True):
        paths = self._session_external_paths(session)
        if not paths:
            return []
        blacklist = lz.load_import_blacklist(self.agent_dir)
        failed = []
        for path in paths:
            try:
                if os.path.isfile(path):
                    os.remove(path)
                blacklist.discard(path)
            except Exception:
                failed.append(path)
                if blacklist_on_fail:
                    blacklist.add(path)
        lz.save_import_blacklist(self.agent_dir, blacklist)
        return failed

    def _delete_selected_session(self):
        item = self.session_list.currentItem()
        if item is None:
            return
        sid = item.data(Qt.UserRole)
        if not sid:
            return
        data = lz.load_session(self.agent_dir, sid)
        title = (data or {}).get("title") or "该会话"
        if QMessageBox.question(self, "删除会话", f"确定删除「{title}」？") != QMessageBox.Yes:
            return
        failed_paths = self._delete_session_external_state(data)
        lz.delete_session(self.agent_dir, sid)
        if self.current_session and self.current_session.get("id") == sid:
            self.current_session = None
            self._selected_session_id = None
            self._new_session()
        else:
            self._refresh_sessions()
        if failed_paths:
            QMessageBox.warning(
                self,
                "删除不完整",
                "会话已删，但以下官方日志删除失败：\n\n" + "\n".join(failed_paths),
            )

    def _new_session(self):
        if self._busy:
            QMessageBox.information(self, "忙碌中", "当前还在生成，请先等待结束或手动中断。")
            return
        self._pending_official_restore = None
        self._pending_state_session = None
        self.current_session = None
        self._selected_session_id = None
        self._set_status("正在创建新会话进程…")
        self._reset_chat_area("新进程已准备，发送第一条消息后会创建会话。")
        self._restart_bridge()

    def _restore_imported_session(self, data):
        fp = self._session_official_log(data)
        if not fp:
            QMessageBox.warning(self, "无法恢复", "该官方导入会话没有对应的官方日志引用。")
            return
        if not os.path.isfile(fp):
            QMessageBox.warning(self, "无法恢复", f"对应官方日志不存在：\n{fp}")
            return
        self.current_session = _session_copy(data)
        self._render_session(self.current_session)
        self._set_status("正在恢复官方上下文…")
        self._pending_official_restore = {"file": fp, "source_session": _session_copy(data)}
        self._pending_state_session = None
        self._restart_bridge()

    def _ensure_session(self, first_user_text: str):
        if self.current_session is not None:
            return
        title = (first_user_text or "新会话").strip().replace("\n", " ")
        if len(title) > 30:
            title = title[:30] + "…"
        self.current_session = {
            "id": uuid.uuid4().hex[:12],
            "title": title or "新会话",
            "created_at": time.time(),
            "updated_at": time.time(),
            "bubbles": [],
            "process_pid": getattr(self.bridge_proc, "pid", None),
            "official_log_path": lz._official_log_path(self.agent_dir, getattr(self.bridge_proc, "pid", None)),
            "session_source_label": "启动器",
            "channel_id": "launcher",
            "channel_label": lz._usage_channel_label("launcher"),
            "backend_history": [],
            "agent_history": [],
        }
        self._ensure_session_usage_metadata(self.current_session)
        self._selected_session_id = self.current_session["id"]
        self._update_header_labels()

    def _send_cmd(self, obj):
        if not self.bridge_proc or self.bridge_proc.poll() is not None:
            raise RuntimeError("桥接进程未运行")
        self.bridge_proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.bridge_proc.stdin.flush()

    def _safe_start_bridge(self):
        try:
            self._start_bridge()
        except Exception as e:
            self._set_status("桥接进程启动失败。")
            QMessageBox.critical(self, "启动失败", str(e))

    def _start_bridge(self):
        if self.bridge_proc and self.bridge_proc.poll() is None:
            return
        py = lz._find_system_python()
        if not py:
            raise RuntimeError("未找到可用的系统 Python。")
        bridge = lz._bridge_script_path()
        if not os.path.isfile(bridge):
            raise RuntimeError(f"bridge.py 不存在：{bridge}")
        self._bridge_ready = False
        self.llms = []
        self._sync_llm_combo()
        self._set_status("正在启动桥接进程…")
        self._stderr_buf = []
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.bridge_proc = subprocess.Popen(
            [py, "-u", bridge, self.agent_dir],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.agent_dir,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )

        def read_stdout():
            try:
                for line in self.bridge_proc.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    self._event_queue.put(ev)
            except Exception:
                pass

        def read_stderr():
            try:
                for line in self.bridge_proc.stderr:
                    line = line.rstrip()
                    self._stderr_buf.append(line)
                    if len(self._stderr_buf) > 200:
                        self._stderr_buf = self._stderr_buf[-200:]
            except Exception:
                pass

        threading.Thread(target=read_stdout, daemon=True).start()
        threading.Thread(target=read_stderr, daemon=True).start()

    def _stop_bridge(self):
        proc = self.bridge_proc
        self.bridge_proc = None
        self._bridge_ready = False
        if proc is None:
            return
        try:
            if proc.poll() is None:
                try:
                    proc.stdin.write('{"cmd":"quit"}\n')
                    proc.stdin.flush()
                except Exception:
                    pass
                try:
                    proc.terminate()
                except Exception:
                    pass
        except Exception:
            pass

    def _restart_bridge(self):
        self._stop_bridge()
        self._safe_start_bridge()

    def _handle_send(self):
        text = self.input_box.toPlainText().strip()
        if not text:
            return
        if not self._bridge_ready:
            QMessageBox.information(self, "尚未就绪", "桥接进程还没准备好，请稍候再发送。")
            return
        self._ensure_session(text)
        self.input_box.clear()
        self._selected_session_id = self.current_session.get("id")
        self._add_message_row("user", text, finished=True)
        self.current_session.setdefault("bubbles", []).append({"role": "user", "text": text})
        self._stream_row = self._add_message_row("assistant", "", finished=False)
        self._busy = True
        self._abort_requested = False
        self._current_stream_text = ""
        self._pending_stream_text = None
        self.send_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._set_status("生成中…")

        usage = self.current_session.get("token_usage") or {}
        event = {
            "ts": time.time(),
            "input_tokens": lz._estimate_tokens(text),
            "output_tokens": 0,
            "total_tokens": lz._estimate_tokens(text),
            "channel_id": str(self.current_session.get("channel_id") or "launcher").strip().lower(),
            "model": self._current_llm_name(),
            "usage_source": "estimate",
        }
        usage.setdefault("events", []).append(event)
        usage["last_model"] = event["model"]
        self.current_session["token_usage"] = usage
        self._active_token_event_ts = event["ts"]
        self._persist_session(self.current_session)
        self._refresh_token_label()
        try:
            self._send_cmd({"cmd": "send", "text": text})
        except Exception as e:
            self._busy = False
            self.send_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            QMessageBox.critical(self, "发送失败", str(e))

    def _stream_update(self, cumulative_text: str):
        self._pending_stream_text = cumulative_text or ""
        self._current_stream_text = cumulative_text or ""
        self._refresh_token_label()
        if not self._stream_flush_timer.isActive():
            self._stream_flush_timer.start(70)

    def _flush_stream_render(self):
        if self._stream_row is None:
            return
        self._stream_row.set_text(self._pending_stream_text or "")
        self._stream_row.set_finished(False)
        self._scroll_to_bottom()

    def _format_interrupted_text(self, final_text=None):
        text = (final_text or "").strip()
        if not text:
            text = (self._current_stream_text or self._pending_stream_text or "").strip()
        if text.endswith("▌"):
            text = text[:-1].rstrip()
        if "已按用户请求中断" in text:
            return text
        if not text:
            return "[系统] 已按用户请求中断本轮生成。"
        return text + "\n\n[系统] 已按用户请求中断本轮生成。"

    def _stream_done(self, final_text: str, provider_usage: dict | None = None):
        if self._abort_requested:
            final_text = self._format_interrupted_text(final_text)
        if self._stream_row is not None:
            self._stream_row.set_text(final_text or "…")
            self._stream_row.set_finished(True)
        self._stream_row = None
        self._busy = False
        self._abort_requested = False
        self._current_stream_text = ""
        self._pending_stream_text = None
        self.send_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._set_status("已完成。")

        if self.current_session is not None:
            self.current_session.setdefault("bubbles", []).append({"role": "assistant", "text": final_text})
            usage = self.current_session.get("token_usage") or {}
            events = usage.get("events") or []
            output_tokens = lz._estimate_tokens(final_text)
            target = None
            for ev in reversed(events):
                if self._active_token_event_ts is not None and float(ev.get("ts", 0) or 0) == float(self._active_token_event_ts):
                    target = ev
                    break
                if int(ev.get("output_tokens", 0) or 0) == 0:
                    target = ev
                    break
            if target is None:
                target = {
                    "ts": time.time(),
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "channel_id": str(self.current_session.get("channel_id") or "launcher").strip().lower(),
                    "model": self._current_llm_name(),
                }
                events.append(target)
            if provider_usage:
                target["input_tokens"] = int(provider_usage.get("input_tokens", target.get("input_tokens", 0)) or 0)
                target["output_tokens"] = int(provider_usage.get("output_tokens", output_tokens) or 0)
                target["total_tokens"] = int(provider_usage.get("total_tokens", target["input_tokens"] + target["output_tokens"]) or (target["input_tokens"] + target["output_tokens"]))
                target["usage_source"] = "provider"
                target["cached_tokens"] = int(provider_usage.get("cached_tokens", 0) or 0)
                target["cache_creation_input_tokens"] = int(provider_usage.get("cache_creation_input_tokens", 0) or 0)
                target["cache_read_input_tokens"] = int(provider_usage.get("cache_read_input_tokens", 0) or 0)
                target["api_calls"] = int(provider_usage.get("api_calls", 0) or 0)
            else:
                target["output_tokens"] = output_tokens
                target["total_tokens"] = int(target.get("input_tokens", 0) or 0) + output_tokens
                target["usage_source"] = str(target.get("usage_source") or "estimate")
            target["model"] = target.get("model") or self._current_llm_name()
            usage["events"] = events
            usage["last_model"] = target.get("model") or ""
            self.current_session["token_usage"] = usage
            self._active_token_event_ts = None
            self._persist_session(self.current_session)
            self._request_backend_state(self.current_session.get("id"))
        self._refresh_token_label()
        self._scroll_to_bottom()

    def _abort(self):
        if not self._busy or self._abort_requested:
            return
        self._abort_requested = True
        self.stop_btn.setEnabled(False)
        self._set_status("正在中断…")
        try:
            self._send_cmd({"cmd": "abort"})
        except Exception as e:
            QMessageBox.warning(self, "中断失败", str(e))

    def _auto_import_legacy(self):
        blacklist = lz.load_import_blacklist(self.agent_dir)
        existing_by_path = {}
        for meta in lz.list_sessions(self.agent_dir):
            try:
                data = lz.load_session(self.agent_dir, meta["id"])
            except Exception:
                data = None
            if not data or not data.get("imported_from"):
                continue
            existing_by_path[lz._canon_path(data.get("imported_from"))] = data

        queue_items = []
        for item in self._legacy_items:
            fp = lz._canon_path(item.get("file", ""))
            if not fp or fp in blacklist:
                continue
            existing = existing_by_path.get(fp)
            if existing and not lz.imported_session_needs_refresh(existing, item.get("mtime", 0)):
                continue
            enriched = dict(item)
            if existing:
                enriched["existing_session"] = existing
            queue_items.append(enriched)
        self._pending_import_queue = queue_items
        if queue_items and not self._importing:
            self._process_next_import()
        elif not queue_items:
            self._refresh_sessions()

    def _process_next_import(self):
        if not self._pending_import_queue:
            self._importing = False
            self._pending_legacy_meta = None
            self._refresh_sessions()
            return
        self._importing = True
        self._pending_legacy_meta = self._pending_import_queue.pop(0)
        self._send_cmd({"cmd": "restore_legacy", "file": self._pending_legacy_meta.get("file", "")})

    def _on_legacy_restored(self, ev):
        meta = self._pending_legacy_meta or {}
        bubbles = ev.get("bubbles") or []
        agent_history = ev.get("agent_history") or []
        existing = meta.get("existing_session") or {}
        if bubbles:
            title = ""
            for bubble in bubbles:
                if bubble.get("role") == "user":
                    title = (bubble.get("text") or "").strip().replace("\n", " ")[:30]
                    if title:
                        break
            session = {
                "id": existing.get("id") or uuid.uuid4().hex[:12],
                "title": existing.get("title") or title or "(从 GenericAgent 导入)",
                "created_at": existing.get("created_at", meta.get("mtime", time.time())),
                "updated_at": meta.get("mtime", time.time()),
                "bubbles": bubbles,
                "backend_history": existing.get("backend_history") or [],
                "agent_history": agent_history,
                "imported_from": lz._canon_path(meta.get("file", "")),
                "official_log_path": lz._canon_path(meta.get("file", "")),
                "official_log_mtime": meta.get("mtime", time.time()),
                "process_pid": lz._session_pid_from_log_path(lz._canon_path(meta.get("file", ""))),
                "session_source_label": existing.get("session_source_label") or "官方日志",
                "pinned": existing.get("pinned", False),
                "legacy_restore_version": lz.LEGACY_RESTORE_VERSION,
            }
            self._ensure_session_usage_metadata(session)
            self._persist_session(session)
        QTimer.singleShot(20, self._process_next_import)

    def _refresh_official_sessions(self):
        try:
            lz.save_import_blacklist(self.agent_dir, set())
        except Exception as e:
            QMessageBox.warning(self, "刷新失败", f"无法清空导入黑名单：\n{e}")
            return
        self._pending_import_queue = []
        self._pending_legacy_meta = None
        self._importing = False
        if not self._bridge_ready:
            self._set_status("桥接进程尚未就绪，准备好后会重新扫描官方会话。")
            return
        self._set_status("正在扫描官方会话…")
        self._send_cmd({"cmd": "list_legacy"})

    def _drain_events(self):
        while True:
            try:
                ev = self._event_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_event(ev)
        proc = self.bridge_proc
        if proc is not None and proc.poll() is not None and not self._bridge_ready and not self._busy:
            stderr_tail = "\n".join(self._stderr_buf[-20:]) or "(空)"
            self._set_status("桥接进程已退出。")
            QMessageBox.critical(self, "桥接进程退出", f"启动失败或已退出。\n\nstderr 尾部：\n{stderr_tail}")
            self.bridge_proc = None

    def _handle_event(self, ev):
        et = ev.get("event")
        if et == "ready":
            self._bridge_ready = True
            self.llms = ev.get("llms", [])
            self._sync_llm_combo()
            self._set_status("桥接进程已就绪。")
            if self._pending_official_restore:
                self._send_cmd({"cmd": "restore_official", "file": self._pending_official_restore.get("file", "")})
                self._set_status("正在恢复官方上下文…")
            elif self._pending_state_session:
                data = _session_copy(self._pending_state_session)
                self.current_session = data
                self._render_session(data)
                self._send_cmd(
                    {
                        "cmd": "set_state",
                        "backend_history": data.get("backend_history") or [],
                        "agent_history": data.get("agent_history") or [],
                    }
                )
                self._request_backend_state(data.get("id"))
                self._pending_state_session = None
            self._send_cmd({"cmd": "list_legacy"})
            return
        if et == "next":
            self._stream_update(ev.get("text", ""))
            return
        if et == "done":
            provider_usage = ev.get("usage") if isinstance(ev.get("usage"), dict) else None
            self._stream_done(ev.get("text", ""), provider_usage=provider_usage)
            return
        if et == "aborted":
            self._set_status("已发送中断请求。")
            return
        if et == "state":
            self._apply_state_to_session(
                ev.get("session_id") or ((self.current_session or {}).get("id")),
                ev.get("backend_history") or [],
                ev.get("agent_history") or [],
            )
            return
        if et == "legacy_list":
            self._legacy_items = ev.get("items", [])
            self._auto_import_legacy()
            return
        if et == "legacy_restored":
            self._on_legacy_restored(ev)
            return
        if et == "official_restored":
            self._on_official_restored(ev)
            return
        if et == "llm_switched":
            self.llms = ev.get("llms", self.llms)
            self._sync_llm_combo()
            self._set_status("模型已切换。")
            return
        if et == "error":
            msg = ev.get("msg", "")
            self._busy = False
            self.send_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self._set_status(f"错误: {msg}")
            QMessageBox.warning(self, "桥接错误", msg or "未知错误")

    def _on_official_restored(self, ev):
        pending = self._pending_official_restore or {}
        self._pending_official_restore = None
        err = (ev.get("error") or "").strip()
        if err:
            self._set_status("官方上下文恢复失败。")
            QMessageBox.warning(self, "恢复失败", err)
            return
        source = pending.get("source_session") or {}
        file_path = ev.get("file") or self._session_official_log(source)
        self.current_session = dict(source)
        self.current_session["updated_at"] = time.time()
        self._bind_session_to_current_bridge(self.current_session)
        self.current_session["restored_from_official"] = file_path
        self.current_session["official_log_path"] = file_path
        self.current_session.setdefault("session_source_label", "官方导入")
        self.current_session.setdefault("backend_history", [])
        self.current_session.setdefault("agent_history", [])
        self._ensure_session_usage_metadata(self.current_session)
        self._selected_session_id = self.current_session.get("id")
        self._persist_session(self.current_session)
        self._render_session(self.current_session)
        self._request_backend_state(self.current_session.get("id"))
        self._set_status("官方上下文已恢复，可以继续聊天。")

    def closeEvent(self, event):
        self._stop_bridge()
        super().closeEvent(event)


def main(agent_dir: str | None = None) -> int:
    target = agent_dir or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not target:
        print("缺少 agent_dir 参数", file=sys.stderr)
        return 2
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("GenericAgent Launcher Chat")
    try:
        win = QtChatWindow(target)
    except Exception as e:
        QMessageBox.critical(None, "启动失败", str(e))
        return 1
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
