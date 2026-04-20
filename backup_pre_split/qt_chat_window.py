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

_SVG_COPY = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>'
_SVG_BOT = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/><path d="M5 3v4"/><path d="M7 5H3"/></svg>'
_SVG_USER = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>'
_SVG_PLUS = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" x2="12" y1="5" y2="19"/><line x1="5" x2="19" y1="12" y2="12"/></svg>'
_SVG_REFRESH = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>'
_SVG_TRASH = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c0-1-1-2-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/></svg>'
_SVG_SEND = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13"/><path d="m22 2-7 20-4-9-9-4Z"/></svg>'
_SVG_STOP = '<svg viewBox="0 0 24 24" fill="{c}" stroke="none"><rect width="10" height="10" x="7" y="7" rx="1.5" ry="1.5"/></svg>'
_SVG_INFO = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>'

PRIVATE_PYTHON_VERSION = "3.12.10"

def _build_md_css() -> str:
    return f"""
body {{ color: {C['text']}; font-family: "Arial", "Microsoft YaHei UI", "Segoe UI", sans-serif; font-size: 13px; line-height: 1.6; font-weight: 400; }}
h1 {{ color: {C['text']}; font-size: 20px; font-weight: 700; border-bottom: 1px solid {C['border']}; padding-bottom: 4px; margin-top: 16px; }}
h2 {{ color: {C['text']}; font-size: 17px; font-weight: 700; border-bottom: 1px solid {C['border']}; padding-bottom: 3px; margin-top: 14px; }}
h3 {{ color: {C['text']}; font-size: 15px; font-weight: 600; margin-top: 12px; }}
h4, h5, h6 {{ color: {C['text_soft']}; font-size: 13px; font-weight: 600; margin-top: 10px; }}
code {{ background: {C['field_alt']}; color: {C['code_text']}; padding: 1px 4px; border-radius: 3px; font-family: Consolas, "Courier New", monospace; font-size: 12px; }}
pre  {{ background: {C['code_bg']}; border: 1px solid {C['border']}; border-radius: 6px; padding: 10px 12px; margin: 8px 0; }}
pre code {{ background: transparent; padding: 0; color: {C['code_text']}; }}
a {{ color: {C['accent']}; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
blockquote {{ border-left: 3px solid {C['accent']}; margin: 8px 0; padding: 4px 0 4px 12px; color: {C['muted']}; }}
table {{ border-collapse: collapse; margin: 8px 0; }}
th, td {{ border: 1px solid {C['border']}; padding: 5px 10px; }}
th {{ background: {C['field_alt']}; color: {C['text']}; font-weight: 700; }}
hr {{ border: none; border-top: 1px solid {C['border']}; margin: 12px 0; }}
ul, ol {{ padding-left: 22px; margin: 4px 0; }}
li {{ margin: 2px 0; }}
p {{ margin: 6px 0; }}
"""


_MD_CSS = _build_md_css()

_ICON_CACHE: dict[str, QIcon] = {}


def _qt_message_handler(mode, context, message):
    text = str(message or "").strip()
    if text.startswith("QFontDatabase: Cannot find font directory "):
        return
    if text.startswith("Note that Qt no longer ships fonts."):
        return
    sys.stderr.write(text + "\n")


qInstallMessageHandler(_qt_message_handler)


def _probe_download_requirements():
    out = {
        "git_ok": False,
        "git_text": "未检测到 Git",
        "python_ok": False,
        "python_text": "未检测到系统 Python",
        "python_warn": False,
        "requests_ok": False,
        "requests_text": "无法检查 requests",
        "requests_warn": True,
    }
    try:
        result = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            timeout=8,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode == 0:
            out["git_ok"] = True
            out["git_text"] = ((result.stdout or result.stderr or "Git 已安装").strip().splitlines()[0]) + "（本机扫描）"
    except Exception:
        pass

    candidates = lz._system_python_candidates()
    if not candidates:
        out["python_text"] = "未检测到系统 Python（本机扫描）"
        out["requests_text"] = "因为未检测到 Python，暂时无法检查 requests"
        return out

    info = candidates[0]
    py = info.get("path") or ""
    version = str(info.get("version") or "").strip()
    out["python_ok"] = True
    if version:
        if version.startswith("3.11") or version.startswith("3.12"):
            suffix = "（本机扫描，推荐）"
        elif version.startswith("3.14"):
            suffix = "（本机扫描，可尝试，但不推荐，建议改用 3.11 / 3.12）"
            out["python_warn"] = True
        else:
            suffix = "（本机扫描，可用，推荐 3.11 / 3.12）"
            out["python_warn"] = True
        out["python_text"] = f"Python {version}{suffix}"
    else:
        out["python_text"] = (py or "已找到系统 Python") + "（本机扫描）"
        out["python_warn"] = True

    try:
        result = subprocess.run(
            [py, "-c", "import requests;print(requests.__version__)"],
            capture_output=True,
            text=True,
            timeout=8,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode == 0:
            ver = (result.stdout or "").strip().splitlines()[-1].strip()
            out["requests_ok"] = True
            out["requests_text"] = (f"requests {ver}" if ver else "requests 已安装") + "（按本机 Python 扫描）"
        else:
            out["requests_text"] = "未安装 requests（按本机 Python 扫描；首次启动前建议先 pip install requests）"
    except Exception:
        out["requests_text"] = "无法检查 requests（按本机 Python 扫描；首次启动前建议先 pip install requests）"
    return out


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
    old_width = int(browser.property("_fitWidth") or 0)
    old_height = int(browser.property("_fitHeight") or 0)
    if old_width == width and old_height > 0 and not browser.property("_fitForce"):
        return
    doc.setTextWidth(width)
    new_h = max(38, int(doc.size().height() + 10))
    if browser.property("streamingHold"):
        current_h = browser.height()
        if new_h < current_h:
            new_h = current_h
    browser.setProperty("_fitWidth", width)
    browser.setProperty("_fitForce", False)
    if abs(new_h - old_height) <= 1 and browser.height() == new_h:
        browser.setProperty("_fitHeight", new_h)
        return
    browser.setProperty("_fitHeight", new_h)
    browser.setFixedHeight(new_h)


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
    channel_label = str(session.get("channel_label") or "").strip()
    if channel_label:
        return channel_label
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
        self._last_body_width = 0

        self.setObjectName("turnFold")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self._button = QPushButton(f"▸  {self._title}")
        self._button.setObjectName("turnFoldHeader")
        self._button.setCursor(QCursor(Qt.PointingHandCursor))
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
        self._body.setHtml(_md_to_html(self._text))
        self._body.setProperty("_fitForce", True)
        if self._expanded:
            _fit_browser_height(self._body)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._expanded:
            width = self._body.viewport().width()
            if width == self._last_body_width:
                return
            self._last_body_width = width
            self._body.setProperty("_fitForce", True)
            _fit_browser_height(self._body)


class MessageRow(QWidget):
    def __init__(self, text: str, role: str, parent=None, on_resend=None):
        super().__init__(parent)
        self._text = text or ""
        self._role = role
        self._finished = True
        self._on_resend = on_resend
        self._stream_prefix_signature = None
        self._stream_live_browser = None
        self._last_browser_width = 0

        is_user = role == "user"
        self.setObjectName("userMsgRow" if is_user else "botMsgRow")

        outer = QHBoxLayout(self)
        outer.setContentsMargins(20, 10, 20, 10)
        outer.setSpacing(12)
        outer.setAlignment(Qt.AlignTop)

        avatar = QLabel()
        avatar.setObjectName("msgAvatar")
        avatar.setFixedSize(30, 30)
        avatar.setAlignment(Qt.AlignCenter)
        svg_data = _SVG_USER if is_user else _SVG_BOT
        avatar_color = C["user_avatar_color"] if is_user else C["bot_avatar_color"]
        try:
            from PySide6.QtSvg import QSvgRenderer

            pm = QPixmap(30, 30)
            pm.fill(Qt.transparent)
            renderer = QSvgRenderer(QByteArray(svg_data.replace("{c}", avatar_color).encode("utf-8")))
            p = QPainter(pm)
            renderer.render(p)
            p.end()
            avatar.setPixmap(pm)
        except Exception:
            avatar.setText("👤" if is_user else "🤖")
        outer.addWidget(avatar, 0, Qt.AlignTop)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(2)

        role_lbl = QLabel("你" if is_user else "助手")
        role_lbl.setObjectName("msgRoleLabel")
        right.addWidget(role_lbl)

        if is_user:
            label = QLabel(self._text)
            label.setObjectName("userMsgText")
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
            right.addWidget(label)
            self._label = label
            self._content_layout = None
            self._action_row = None
            self._bubble = None
        else:
            host = QWidget()
            host.setStyleSheet("background: transparent;")
            host_layout = QVBoxLayout(host)
            host_layout.setContentsMargins(0, 0, 0, 0)
            host_layout.setSpacing(6)
            self._content_layout = host_layout
            right.addWidget(host)

            self._action_row = QWidget()
            self._action_row.setStyleSheet("background: transparent;")
            arow = QHBoxLayout(self._action_row)
            arow.setContentsMargins(0, 4, 10, 0)
            arow.setSpacing(4)
            copy_btn = QPushButton()
            copy_btn.setObjectName("msgActionBtn")
            copy_btn.setIcon(_svg_icon("msg_copy", _SVG_COPY, color=C['muted'], size=14))
            copy_btn.setIconSize(QSize(14, 14))
            copy_btn.setFixedSize(26, 24)
            copy_btn.setToolTip("复制")
            copy_btn.setCursor(QCursor(Qt.PointingHandCursor))
            copy_btn.clicked.connect(self._copy_text)
            copy_btn.hide()
            arow.addWidget(copy_btn)
            self._copy_btn = copy_btn
            self._regen_btn = None
            if callable(self._on_resend):
                regen_btn = QPushButton()
                regen_btn.setObjectName("msgActionBtn")
                regen_btn.setIcon(_svg_icon("msg_regen", _SVG_REFRESH, color=C['muted'], size=14))
                regen_btn.setIconSize(QSize(14, 14))
                regen_btn.setFixedSize(26, 24)
                regen_btn.setToolTip("重新生成")
                regen_btn.setCursor(QCursor(Qt.PointingHandCursor))
                regen_btn.clicked.connect(self._do_resend)
                regen_btn.hide()
                arow.addWidget(regen_btn)
                self._regen_btn = regen_btn
            arow.addStretch(1)
            self._token_label = QLabel("")
            self._token_label.setObjectName("msgTokenLabel")
            self._token_label.setStyleSheet(
                f"QLabel#msgTokenLabel {{ color: {C['muted']}; font-size: 11px; "
                f"font-family: Consolas, 'Cascadia Mono', 'Microsoft YaHei UI'; }}"
            )
            self._token_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._token_label.hide()
            arow.addWidget(self._token_label)
            right.addWidget(self._action_row)

            self._label = None
            self._bubble = None

        outer.addLayout(right, 1)
        self.set_text(self._text)

    def _copy_text(self):
        try:
            QApplication.clipboard().setText(self._text or "")
        except Exception:
            pass

    def _do_resend(self):
        if callable(self._on_resend):
            try:
                self._on_resend(self)
            except Exception:
                pass

    def enterEvent(self, event):
        if self._role != "user" and self._finished:
            if getattr(self, "_copy_btn", None):
                self._copy_btn.show()
            if getattr(self, "_regen_btn", None):
                self._regen_btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if getattr(self, "_copy_btn", None):
            self._copy_btn.hide()
        if getattr(self, "_regen_btn", None):
            self._regen_btn.hide()
        super().leaveEvent(event)

    def set_token_info(self, input_tokens: int, output_tokens: int, *, live: bool = False):
        lbl = getattr(self, "_token_label", None)
        if lbl is None:
            return
        inp = int(input_tokens or 0)
        out = int(output_tokens or 0)
        if inp <= 0 and out <= 0:
            lbl.hide()
            row = getattr(self, "_action_row", None)
            if row is not None:
                row.updateGeometry()
            return
        suffix = " …" if live else ""
        lbl.setText(f"↑{inp}  ↓{out}{suffix}")
        lbl.show()
        row = getattr(self, "_action_row", None)
        if row is not None:
            row.show()
            row.adjustSize()
            row.updateGeometry()
        self.updateGeometry()
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._role != "user" and self._content_layout is not None:
            width = self.width()
            if width == self._last_browser_width:
                return
            self._last_browser_width = width
            for browser in self.findChildren(QTextBrowser):
                browser.setProperty("_fitForce", True)
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

    def _make_browser(self, markdown_text: str, *, streaming: bool = False) -> QTextBrowser:
        browser = QTextBrowser()
        browser.setObjectName("botMsgBrowser")
        browser.setReadOnly(True)
        browser.setOpenExternalLinks(True)
        browser.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        browser.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        browser.document().setDefaultStyleSheet(_MD_CSS)
        browser.setProperty("_markdownText", markdown_text)
        browser.setProperty("streamingHold", bool(streaming))
        browser.setProperty("_fitForce", True)
        browser.setHtml(_md_to_html(markdown_text))
        _fit_browser_height(browser)
        return browser

    def _set_browser_markdown(self, browser: QTextBrowser, markdown_text: str) -> None:
        if browser.property("_markdownText") == markdown_text:
            return
        browser.setProperty("_markdownText", markdown_text)
        browser.setProperty("_fitForce", True)
        browser.setHtml(_md_to_html(markdown_text))
        _fit_browser_height(browser)

    def set_finished(self, done: bool):
        if self._finished == done:
            return
        self._finished = done
        self.set_text(self._text)

    def update_content(self, text: str, *, finished: bool):
        new_text = text or ""
        state_changed = self._finished != finished
        text_changed = self._text != new_text
        if not state_changed and not text_changed:
            return
        self._finished = finished
        self.set_text(new_text)

    def set_text(self, text: str):
        self._text = text or ""
        if self._role == "user":
            self._label.setText(self._text)
            self._label.adjustSize()
            return

        segments = lz.fold_turns(self._text)
        if not segments:
            segments = [{"type": "text", "content": self._text or "…"}]

        def seg_sig(seg):
            return (
                str(seg.get("type") or "text"),
                str(seg.get("title") or ""),
                str(seg.get("content") or ""),
            )

        if not self._finished:
            last = segments[-1]
            last_type = str(last.get("type") or "text")
            if last_type == "text":
                prefix_segments = segments[:-1]
                live_content = last.get("content") or ""
            else:
                prefix_segments = segments
                live_content = ""
            prefix_signature = tuple(seg_sig(s) for s in prefix_segments)
            old_sig = self._stream_prefix_signature or ()
            browser = getattr(self, "_stream_live_browser", None)
            try:
                browser_ok = browser is not None and browser.parent() is not None
            except Exception:
                browser_ok = False

            if prefix_signature == old_sig and browser_ok:
                shown = (live_content.rstrip() + " ▌") if live_content.strip() else "…"
                self._set_browser_markdown(browser, shown)
                return

            if (
                browser_ok
                and len(prefix_signature) > len(old_sig)
                and prefix_signature[: len(old_sig)] == old_sig
            ):
                if self._content_layout is not None:
                    idx = self._content_layout.indexOf(browser)
                    if idx >= 0:
                        self._content_layout.takeAt(idx)
                    browser.deleteLater()
                self._stream_live_browser = None
                for seg in prefix_segments[len(old_sig):]:
                    content = seg.get("content") or ""
                    if seg.get("type") == "fold":
                        fold = TurnFold(seg.get("title") or "处理中", content, self)
                        self._content_layout.addWidget(fold)
                    else:
                        br = self._make_browser(content)
                        self._content_layout.addWidget(br)
                shown = (live_content.rstrip() + " ▌") if live_content.strip() else "…"
                new_live = self._make_browser(shown, streaming=True)
                self._content_layout.addWidget(new_live)
                self._stream_live_browser = new_live
                self._stream_prefix_signature = prefix_signature
                return

        self._clear_assistant_widgets()

        for idx, seg in enumerate(segments):
            content = seg.get("content") or ""
            is_last = idx == len(segments) - 1
            if seg.get("type") == "fold":
                fold = TurnFold(seg.get("title") or "处理中", content, self)
                self._content_layout.addWidget(fold)
                continue
            shown = content
            if not self._finished and is_last:
                shown = (shown.rstrip() + " ▌") if shown.strip() else "…"
            browser = self._make_browser(shown, streaming=(not self._finished and is_last))
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
            self._stream_prefix_signature = tuple(seg_sig(s) for s in prefix_segments)

class OptionCard(QFrame):
    def __init__(self, icon: str, title: str, desc: str, command, parent=None):
        super().__init__(parent)
        self._command = command
        self.setObjectName("optionCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(92)
        row = QHBoxLayout(self)
        row.setContentsMargins(22, 14, 22, 14)
        row.setSpacing(16)

        icon_lbl = QLabel(icon)
        icon_lbl.setObjectName("optionIcon")
        icon_lbl.setFixedWidth(34)
        icon_lbl.setAlignment(Qt.AlignCenter)
        row.addWidget(icon_lbl, 0)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("optionTitle")
        desc_lbl = QLabel(desc)
        desc_lbl.setObjectName("optionDesc")
        text_col.addWidget(title_lbl)
        text_col.addWidget(desc_lbl)
        row.addLayout(text_col, 1)

        arrow = QLabel("›")
        arrow.setObjectName("optionArrow")
        row.addWidget(arrow, 0)

    def mousePressEvent(self, event):
        if callable(self._command):
            self._command()
        return super().mousePressEvent(event)


class QtChatWindow(QMainWindow):
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

    def _sidebar_button_style(self, *, primary: bool = False, subtle: bool = False, selected: bool = False) -> str:
        radius = F["radius_md"]
        if selected:
            variant = "selected"
        elif primary:
            variant = "primary"
        elif subtle:
            variant = "subtle"
        else:
            variant = "default"
        marker = f"QPushButton[factoryKey=\"nav-{variant}\"] {{ }}"
        if selected:
            return marker + (
                f"QPushButton {{ background: {C['accent_soft_bg']}; color: {C['text']}; "
                f"border: 1px solid transparent; border-left: 2px solid {C['accent']}; "
                f"border-radius: {radius}px; padding: 8px 10px; font-size: 13px; font-weight: 600; text-align: left; }}"
                f"QPushButton:hover {{ background: {C['accent_soft_bg_hover']}; }}"
            )
        if primary:
            return marker + (
                f"QPushButton {{ background: {C['layer2']}; color: {C['text']}; border: 1px solid {C['stroke_default']}; "
                f"border-radius: {radius}px; padding: 8px 12px; font-size: 13px; font-weight: 600; text-align: left; }}"
                f"QPushButton:hover {{ background: {C['layer3']}; border-color: {C['stroke_hover']}; }}"
                f"QPushButton:pressed {{ background: {C['layer1']}; }}"
            )
        if subtle:
            return marker + (
                f"QPushButton {{ background: transparent; color: {C['text_soft']}; border: 1px solid transparent; "
                f"border-radius: {radius}px; padding: 7px 10px; font-size: 13px; text-align: left; }}"
                f"QPushButton:hover {{ background: {C['layer2']}; color: {C['text']}; }}"
                f"QPushButton:pressed {{ background: {C['layer1']}; }}"
            )
        return marker + (
            f"QPushButton {{ background: transparent; color: {C['text_soft']}; border: 1px solid transparent; "
            f"border-radius: {radius}px; padding: 7px 12px; font-size: 13px; text-align: center; }}"
            f"QPushButton:hover {{ background: {C['layer2']}; color: {C['text']}; }}"
            f"QPushButton:pressed {{ background: {C['layer1']}; }}"
        )

    def _toggle_sidebar(self):
        self.sidebar_collapsed = not self.sidebar_collapsed
        self.cfg["sidebar_collapsed"] = self.sidebar_collapsed
        lz.save_config(self.cfg)
        self._rebuild_sidebar()

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
            global _MD_CSS
            _MD_CSS = _build_md_css()
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
                scr.setStyleSheet(f"QScrollArea {{ border: none; background: {C['bg']}; }}" + SCROLLBAR_STYLE)
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
                browser.document().setDefaultStyleSheet(_MD_CSS)
                html = browser.toHtml()
                browser.setHtml(html)
            except Exception:
                pass

    def _restyle_download_page_widgets(self):
        body_scroll = getattr(self, "download_body_scroll", None)
        if body_scroll is not None:
            try:
                body_scroll.setStyleSheet(f"QScrollArea {{ border: none; background: transparent; }}" + SCROLLBAR_STYLE)
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

    AUTO_TASK_TEXT = "[AUTO]🤖 用户已经离开超过30分钟，作为自主智能体，请阅读自动化sop，执行自动任务。"

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
            if new_state else
            "已关闭：不再自动触发。",
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
            + SCROLLBAR_STYLE
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

    def _rebuild_sidebar(self):
        self._clear_layout(self.sidebar_layout)
        collapsed = self.sidebar_collapsed
        self.sidebar_host.setFixedWidth(64 if collapsed else 280)
        if collapsed:
            self.sidebar_layout.setContentsMargins(8, 10, 8, 10)
            self.sidebar_layout.setSpacing(4)
        else:
            self.sidebar_layout.setContentsMargins(14, 14, 14, 14)
            self.sidebar_layout.setSpacing(6)

        if collapsed:
            toggle = QPushButton("⇥")
            toggle.setCursor(Qt.PointingHandCursor)
            toggle.setFixedSize(48, 36)
            toggle.setToolTip("展开侧边栏")
            toggle.setStyleSheet(self._sidebar_button_style())
            toggle.clicked.connect(self._toggle_sidebar)
            self.sidebar_layout.addWidget(toggle, 0, Qt.AlignHCenter)

            logo = QLabel("⚙")
            logo.setFixedSize(48, 48)
            logo.setAlignment(Qt.AlignCenter)
            logo.setObjectName("sidebarLogo")
            self.sidebar_layout.addWidget(logo, 0, Qt.AlignHCenter)
            self.sidebar_layout.addSpacing(6)

            for text, handler, tip in (
                ("＋", self._new_session, "新建会话"),
                ("🔍", self._open_search_filter, "搜索历史消息"),
                ("↻", self._refresh_session_list, "刷新会话列表"),
            ):
                btn = QPushButton(text)
                btn.setCursor(Qt.PointingHandCursor)
                btn.setFixedSize(48, 40)
                btn.setToolTip(tip)
                btn.setStyleSheet(self._sidebar_button_style())
                btn.clicked.connect(handler)
                self.sidebar_layout.addWidget(btn, 0, Qt.AlignHCenter)
        else:
            top = QFrame()
            top.setStyleSheet("background: transparent;")
            top.setFixedHeight(44)
            top_row = QHBoxLayout(top)
            top_row.setContentsMargins(0, 8, 0, 0)
            top_row.setSpacing(0)
            toggle = QPushButton("⇤")
            toggle.setCursor(Qt.PointingHandCursor)
            toggle.setFixedSize(32, 32)
            toggle.setToolTip("收起侧边栏")
            toggle.setStyleSheet(self._sidebar_button_style())
            toggle.clicked.connect(self._toggle_sidebar)
            top_row.addWidget(toggle, 0, Qt.AlignLeft)
            top_row.addStretch(1)
            self.sidebar_layout.addWidget(top)

            brand = QFrame()
            brand.setStyleSheet("background: transparent;")
            brand_row = QHBoxLayout(brand)
            brand_row.setContentsMargins(0, 6, 0, 12)
            brand_row.setSpacing(10)
            icon = QLabel("⚙")
            icon.setFixedSize(42, 42)
            icon.setAlignment(Qt.AlignCenter)
            icon.setObjectName("sidebarLogo")
            brand_row.addWidget(icon, 0)
            title = QLabel("GenericAgent")
            title.setObjectName("cardTitle")
            brand_row.addWidget(title, 1)
            self.sidebar_layout.addWidget(brand)

            new_btn = QPushButton("＋  新会话")
            new_btn.setCursor(Qt.PointingHandCursor)
            new_btn.setStyleSheet(self._sidebar_button_style(primary=True))
            new_btn.clicked.connect(self._new_session)
            self.sidebar_layout.addWidget(new_btn)

            search_btn = QPushButton("🔍  搜索")
            search_btn.setCursor(Qt.PointingHandCursor)
            search_btn.setStyleSheet(self._sidebar_button_style(subtle=True))
            search_btn.clicked.connect(self._open_search_filter)
            self.sidebar_layout.addWidget(search_btn)

            refresh_btn = QPushButton("↻  刷新会话")
            refresh_btn.setCursor(Qt.PointingHandCursor)
            refresh_btn.setStyleSheet(self._sidebar_button_style(subtle=True))
            refresh_btn.clicked.connect(self._refresh_session_list)
            self.sidebar_layout.addWidget(refresh_btn)

            group = QLabel("渠道")
            group.setObjectName("sectionLabel")
            self.sidebar_group_label = group
            self.sidebar_layout.addWidget(group)
        if collapsed:
            self.sidebar_group_label = None

        self.session_list = QListWidget()
        self.session_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.session_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.session_list.currentItemChanged.connect(self._on_session_item_changed)
        self.session_list.customContextMenuRequested.connect(self._open_session_list_context_menu)
        if collapsed:
            self.session_list.hide()
        self.sidebar_layout.addWidget(self.session_list, 1)
        if collapsed:
            self.sidebar_layout.addStretch(1)

        bottom = QFrame()
        bottom.setStyleSheet("background: transparent;")
        bottom.setFixedHeight(52)
        bottom_row = QHBoxLayout(bottom)
        bottom_row.setContentsMargins(0, 8, 0, 0)
        bottom_row.setSpacing(0)
        settings = QPushButton("⚙" if collapsed else "⚙   设置")
        settings.setCursor(Qt.PointingHandCursor)
        settings.setToolTip("设置" if collapsed else "")
        settings.setStyleSheet(self._sidebar_button_style(subtle=not collapsed))
        settings.clicked.connect(self._show_settings)
        if collapsed:
            settings.setFixedSize(48, 40)
            bottom_row.setContentsMargins(0, 6, 0, 0)
            bottom_row.addWidget(settings, 0, Qt.AlignHCenter)
        else:
            bottom_row.addWidget(settings, 1)
        self.sidebar_layout.addWidget(bottom)

        self._last_session_list_signature = None
        if lz.is_valid_agent_dir(self.agent_dir):
            self._refresh_sessions()

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
        self.welcome_icon.setStyleSheet(f"font-size: 42px; color: {C['accent']}; background: transparent;")
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
        self.enter_chat_btn.clicked.connect(self._enter_chat)
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

        enter_btn = QPushButton("载入内核 →")
        enter_btn.setStyleSheet(self._action_button_style(primary=True))
        enter_btn.setFixedHeight(40)
        enter_btn.clicked.connect(self._locate_enter_chat)
        body_layout.addWidget(enter_btn)
        body_layout.addStretch(1)
        return page

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
        body_scroll.setStyleSheet(f"QScrollArea {{ border: none; background: transparent; }}" + SCROLLBAR_STYLE)
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

        deps = _probe_download_requirements()
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

        def add_dep_row(title_text: str, ok: bool, detail: str, *, warning: bool = False):
            if ok and not warning:
                severity = "ok"
                mark = "✓"
            elif warning:
                severity = "warn"
                mark = "!"
            else:
                severity = "error"
                mark = "✕"
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

        add_dep_row("Git", deps["git_ok"], deps["git_text"])
        add_dep_row("Python", deps["python_ok"], deps["python_text"], warning=deps.get("python_warn", False))
        add_dep_row("requests", deps["requests_ok"], deps["requests_text"], warning=True)
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
        return page

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
            scroll.setStyleSheet(f"QScrollArea {{ border: none; background: transparent; }}" + SCROLLBAR_STYLE)
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

    def _append_download_log(self, message: str):
        box = getattr(self, "download_log", None)
        text = str(message or "").rstrip()
        if box is None or not text:
            return
        box.append(text)
        box.moveCursor(QTextCursor.End)

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child = item.layout()
            if child is not None:
                self._clear_layout(child)
            if widget is not None:
                widget.deleteLater()

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

    def _refresh_download_state(self):
        if hasattr(self, "download_parent_label"):
            target = os.path.join(self.install_parent, "GenericAgent") if self.install_parent else "未选择安装位置"
            self.download_parent_label.setText(f"安装位置：{self.install_parent}\n目标目录：{target}")
        if hasattr(self, "download_parent_value"):
            self.download_parent_value.setText(self.install_parent or "未选择安装位置")
        if hasattr(self, "download_btn"):
            self.download_btn.setEnabled(not self._download_running)
            self.download_btn.setText("下载中…" if self._download_running and self._download_mode == "clone" else "开始下载")
        if hasattr(self, "download_private_btn"):
            self.download_private_btn.setEnabled(not self._download_running)
            self.download_private_btn.setText(
                "构建中…" if self._download_running and self._download_mode == "private_python" else "下载并配置 3.12 虚拟环境"
            )
        if hasattr(self, "download_private_only_checkbox"):
            self.download_private_only_checkbox.setEnabled(not self._download_running)
        if hasattr(self, "download_progress"):
            if self._download_running:
                self.download_progress.setRange(0, 0)
            else:
                self.download_progress.setRange(0, 1)
                self.download_progress.setValue(0)

    def _choose_install_parent(self):
        path = QFileDialog.getExistingDirectory(
            self,
            "选择安装位置",
            self.install_parent or os.path.expanduser("~"),
        )
        if path:
            self.install_parent = path
            self.cfg["install_parent"] = path
            lz.save_config(self.cfg)
            self._refresh_download_state()

    def _start_download_repo(self, private_python=False, private_only=False):
        parent = str(self.install_parent or "").strip()
        if not parent or not os.path.isdir(parent):
            QMessageBox.warning(self, "位置无效", "请选择有效的安装位置。")
            return
        if hasattr(self, "download_log"):
            self.download_log.clear()
            self._append_download_log(f"[{datetime.now().strftime('%H:%M:%S')}] 准备开始下载")
        target = os.path.join(parent, "GenericAgent")
        if private_python and private_only and not os.path.exists(target):
            QMessageBox.warning(self, "目录不存在", "你勾选了“仅配置虚拟环境”，但目标目录里还没有 GenericAgent。\n\n请先下载原项目，或取消该勾选。")
            self._append_download_log(f"[{datetime.now().strftime('%H:%M:%S')}] 仅配置虚拟环境失败：目标目录不存在 {target}")
            return
        if os.path.exists(target):
            if QMessageBox.question(self, "目录已存在", f"{target}\n\n已存在。是否直接使用它作为 GenericAgent 目录？") != QMessageBox.Yes:
                return
            if lz.is_valid_agent_dir(target):
                if not private_python:
                    self._set_agent_dir(target)
                    self.download_status_label.setText("已使用现有目录。现在可以直接进入聊天。")
                    self._append_download_log(f"[{datetime.now().strftime('%H:%M:%S')}] 目录已存在，已直接接管：{target}")
                    return
                self._append_download_log(f"[{datetime.now().strftime('%H:%M:%S')}] 目录已存在，将继续为它配置私有 3.12 虚拟环境：{target}")
            else:
                QMessageBox.warning(self, "目录无效", "该目录存在但不是有效的 GenericAgent 目录。")
                self._append_download_log(f"[{datetime.now().strftime('%H:%M:%S')}] 目录存在，但不是有效的 GenericAgent 根目录：{target}")
                return
        elif private_python and private_only:
            QMessageBox.warning(self, "目录不存在", "你勾选了“仅配置虚拟环境”，但目标目录里还没有 GenericAgent。\n\n请先下载原项目，或取消该勾选。")
            self._append_download_log(f"[{datetime.now().strftime('%H:%M:%S')}] 仅配置虚拟环境失败：目标目录不存在 {target}")
            return
        self._download_running = True
        self._download_mode = "private_python" if private_python else "clone"
        self._refresh_download_state()
        if private_python and private_only:
            self.download_status_label.setText("正在为现有 GenericAgent 配置私有 3.12 环境…")
        else:
            self.download_status_label.setText("正在准备私有 3.12 环境…" if private_python else "正在检查 Git 并开始下载…")
        self._append_download_log(f"[{datetime.now().strftime('%H:%M:%S')}] 目标目录：{target}")
        self.cfg["install_parent"] = parent
        lz.save_config(self.cfg)
        threading.Thread(target=self._run_clone_repo, args=(target, private_python, private_only), daemon=True).start()

    def _private_python_spec(self):
        machine = (platform.machine() or "").lower()
        if machine in ("amd64", "x86_64", "x64", ""):
            arch = "amd64"
        elif "arm64" in machine or "aarch64" in machine:
            arch = "arm64"
        else:
            return None, f"当前机器架构 {platform.machine() or 'unknown'} 暂未接入私有 Python 3.12 自动安装。"
        filename = f"python-{PRIVATE_PYTHON_VERSION}-{arch}.exe"
        return {
            "version": PRIVATE_PYTHON_VERSION,
            "arch": arch,
            "filename": filename,
            "url": f"https://www.python.org/ftp/python/{PRIVATE_PYTHON_VERSION}/{filename}",
        }, None

    def _private_runtime_paths(self, target):
        root = os.path.join(target, ".launcher_runtime")
        python_root = os.path.join(root, "python312")
        venv_root = os.path.join(root, "venv312")
        downloads_root = os.path.join(root, "downloads")
        python_exe = os.path.join(python_root, "python.exe" if os.name == "nt" else "bin/python")
        venv_python = os.path.join(venv_root, "Scripts", "python.exe") if os.name == "nt" else os.path.join(venv_root, "bin", "python")
        return {
            "root": root,
            "python_root": python_root,
            "venv_root": venv_root,
            "downloads_root": downloads_root,
            "python_exe": python_exe,
            "venv_python": venv_python,
        }

    def _probe_python_version_prefix(self, py_path):
        try:
            result = subprocess.run(
                [py_path, "-c", "import sys;print(sys.version.split()[0])"],
                capture_output=True,
                text=True,
                timeout=15,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception:
            return ""
        if result.returncode != 0:
            return ""
        return str((result.stdout or "").strip().splitlines()[-1] if (result.stdout or "").strip() else "")

    def _read_tail_text(self, path, max_lines=20):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            return "".join(lines[-max_lines:]).strip()
        except Exception:
            return ""

    def _download_to_file(self, url, dest, label):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        self._event_queue.put({"event": "clone_status", "msg": f"{label}：开始下载"})
        with lz.requests.get(url, stream=True, timeout=(20, 600)) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            downloaded = 0
            last_report = 0
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        progress = int(downloaded * 100 / total)
                        if progress >= last_report + 10 or downloaded == total:
                            last_report = progress
                            self._event_queue.put(
                                {
                                    "event": "clone_status",
                                    "msg": f"{label}：{progress}% ({downloaded // (1024 * 1024)} / {max(total // (1024 * 1024), 1)} MB)",
                                }
                            )
                    elif downloaded - last_report >= 8 * 1024 * 1024:
                        last_report = downloaded
                        self._event_queue.put(
                            {
                                "event": "clone_status",
                                "msg": f"{label}：已下载 {downloaded // (1024 * 1024)} MB",
                            }
                        )

    def _run_checked_command(self, args, *, cwd=None, timeout=1200, log_path=None, label=""):
        result = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode == 0:
            return True, ""
        detail = (result.stderr or result.stdout or "").strip()
        if log_path and os.path.isfile(log_path):
            tail = self._read_tail_text(log_path)
            if tail:
                detail = tail
        if detail:
            lines = [line.strip() for line in detail.splitlines() if line.strip()]
            detail = "\n".join(lines[-12:]) if lines else detail
        else:
            detail = f"{label or '命令'}失败，退出码 {result.returncode}"
        return False, detail

    def _ensure_private_python_env(self, target):
        spec, spec_err = self._private_python_spec()
        if spec_err:
            return None, spec_err
        paths = self._private_runtime_paths(target)
        os.makedirs(paths["root"], exist_ok=True)
        os.makedirs(paths["downloads_root"], exist_ok=True)

        python_version = self._probe_python_version_prefix(paths["python_exe"])
        if not python_version.startswith("3.12"):
            installer_path = os.path.join(paths["downloads_root"], spec["filename"])
            if not os.path.isfile(installer_path):
                self._download_to_file(spec["url"], installer_path, f"下载官方 Python {spec['version']} 安装包")
            else:
                self._event_queue.put({"event": "clone_status", "msg": f"复用已下载的 Python 安装包：{installer_path}"})
            install_log = os.path.join(paths["downloads_root"], "python-install.log")
            self._event_queue.put({"event": "clone_status", "msg": "正在安装私有 Python 3.12（不会修改系统 PATH）…"})
            ok, detail = self._run_checked_command(
                [
                    installer_path,
                    "/quiet",
                    "/log",
                    install_log,
                    "InstallAllUsers=0",
                    f"TargetDir={paths['python_root']}",
                    "PrependPath=0",
                    "Include_launcher=0",
                    "Include_test=0",
                    "Include_pip=1",
                    "Include_venv=1",
                    "Include_tcltk=0",
                    "Include_doc=0",
                    "Include_dev=0",
                    "Include_symbols=0",
                    "Include_debug=0",
                    "AssociateFiles=0",
                    "Shortcuts=0",
                    "SimpleInstall=1",
                ],
                timeout=1800,
                log_path=install_log,
                label="安装私有 Python 3.12",
            )
            if not ok:
                return None, detail
            python_version = self._probe_python_version_prefix(paths["python_exe"])
            if not python_version.startswith("3.12"):
                return None, "私有 Python 3.12 安装完成后仍未检测到可用的 python.exe。"
        else:
            self._event_queue.put({"event": "clone_status", "msg": f"复用已存在的私有 Python {python_version}：{paths['python_exe']}"})

        self._event_queue.put({"event": "clone_status", "msg": "正在创建私有 3.12 虚拟环境…"})
        ok, detail = self._run_checked_command(
            [paths["python_exe"], "-m", "venv", "--clear", paths["venv_root"]],
            timeout=1200,
            label="创建私有虚拟环境",
        )
        if not ok:
            return None, detail
        if not os.path.isfile(paths["venv_python"]):
            return None, "虚拟环境创建完成后未找到 venv 的 python.exe。"

        self._event_queue.put({"event": "clone_status", "msg": "正在初始化 pip…"})
        ok, detail = self._run_checked_command(
            [paths["venv_python"], "-m", "ensurepip", "--upgrade"],
            timeout=1200,
            label="初始化 pip",
        )
        if not ok:
            return None, detail

        self._event_queue.put({"event": "clone_status", "msg": "正在为私有虚拟环境安装 requests…"})
        ok, detail = self._run_checked_command(
            [paths["venv_python"], "-m", "pip", "install", "requests"],
            timeout=1800,
            label="安装 requests",
        )
        if not ok:
            return None, detail

        ok, detail = lz._probe_python_agent_compat(paths["venv_python"], target)
        if not ok:
            return None, f"私有 3.12 虚拟环境已创建，但载入 GenericAgent 失败：{detail}"
        return paths["venv_python"], None

    def _run_clone_repo(self, target, private_python=False, private_only=False):
        try:
            if private_python and private_only:
                if not lz.is_valid_agent_dir(target):
                    self._event_queue.put({"event": "clone_error", "msg": "仅配置虚拟环境时，目标目录必须已经是有效的 GenericAgent 根目录。"})
                    return
                self._event_queue.put({"event": "clone_status", "msg": "已跳过源码下载，继续为现有目录配置私有 3.12 环境。"})
            elif not lz.is_valid_agent_dir(target):
                try:
                    subprocess.run(
                        ["git", "--version"],
                        check=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                except Exception:
                    self._event_queue.put({"event": "clone_error", "msg": "未检测到 Git。请先安装 Git for Windows：\nhttps://git-scm.com/download/win"})
                    return
                self._event_queue.put({"event": "clone_status", "msg": f"开始下载到：{target}"})
                proc = subprocess.Popen(
                    ["git", "clone", "--progress", lz.REPO_URL, target],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                last_lines = []
                for line in proc.stdout:
                    text = line.rstrip()
                    if text:
                        last_lines.append(text)
                        if len(last_lines) > 3:
                            last_lines = last_lines[-3:]
                        self._event_queue.put({"event": "clone_status", "msg": text})
                proc.wait()
                if proc.returncode != 0 or not lz.is_valid_agent_dir(target):
                    detail = "\n".join(last_lines).strip()
                    self._event_queue.put({"event": "clone_error", "msg": detail or "git clone 失败，请检查网络后重试。"})
                    return
            elif private_python:
                self._event_queue.put({"event": "clone_status", "msg": "检测到现有 GenericAgent 目录，跳过 git clone，继续配置私有 3.12 环境。"})

            python_exe = ""
            if private_python:
                python_exe, py_err = self._ensure_private_python_env(target)
                if not python_exe:
                    self._event_queue.put({"event": "clone_error", "msg": py_err or "私有 3.12 虚拟环境配置失败。"})
                    return
            self._event_queue.put({"event": "clone_done", "target": target, "python_exe": python_exe, "private_python": bool(private_python)})
        except Exception as e:
            self._event_queue.put({"event": "clone_error", "msg": str(e)})

    def _api_format_options(self):
        return [lz.SIMPLE_FORMAT_LABEL[k] for k in ("claude_native", "oai_chat", "oai_responses")]

    def _api_format_from_label(self, label):
        for format_key, txt in lz.SIMPLE_FORMAT_LABEL.items():
            if txt == label:
                return format_key
        return "oai_chat"

    def _api_format_meta(self, format_key):
        return lz.SIMPLE_FORMAT_RULES.get(format_key) or lz.SIMPLE_FORMAT_RULES["oai_chat"]

    def _api_template_choices(self, format_key):
        keys = self._api_format_meta(format_key).get("templates", [])
        return [(k, lz.TEMPLATE_INDEX[k]["label"]) for k in keys if k in lz.TEMPLATE_INDEX]

    def _api_infer_template_key(self, kind, data):
        best_key = None
        best_score = -1
        for tpl_key, meta in lz.TEMPLATE_INDEX.items():
            if meta.get("kind") != kind:
                continue
            defaults = dict(meta.get("defaults") or {})
            if not defaults:
                continue
            matched = True
            score = 0
            for dk, dv in defaults.items():
                if dk == "apibase" and dv == "":
                    continue
                if data.get(dk) != dv:
                    matched = False
                    break
                score += 1
            if matched and score > best_score:
                best_key = tpl_key
                best_score = score
        if best_key:
            return best_key
        return "custom-claude" if kind == "native_claude" else "custom-oai"

    def _api_infer_format_key(self, kind, data):
        if kind == "native_claude":
            return "claude_native"
        if kind == "native_oai":
            return "oai_responses" if data.get("api_mode") == "responses" else "oai_chat"
        return "oai_chat"

    def _api_prune_managed_extra(self, raw_extra, *, drop_template=False, drop_format=False):
        extra = dict(raw_extra or {})
        if drop_template:
            for key in lz.TEMPLATE_MANAGED_KEYS:
                extra.pop(key, None)
        if drop_format:
            extra.pop("api_mode", None)
        return extra

    def _api_make_simple_state(self, cfg):
        data = dict(cfg.get("data") or {})
        kind = cfg.get("kind") or "native_oai"
        format_key = self._api_infer_format_key(kind, data)
        tpl_key = self._api_infer_template_key(kind, data)
        valid_tpl_keys = {k for k, _ in self._api_template_choices(format_key)}
        if tpl_key not in valid_tpl_keys:
            tpl_key = "custom-claude" if kind == "native_claude" else "custom-oai"
        raw_extra = dict(data)
        for key in ("name", "apikey", "apibase", "model"):
            raw_extra.pop(key, None)
        for key in lz.TEMPLATE_INDEX.get(tpl_key, {}).get("defaults", {}):
            raw_extra.pop(key, None)
        raw_extra.pop("api_mode", None)
        return {
            "var": cfg["var"],
            "format": format_key,
            "tpl_key": tpl_key,
            "apibase": data.get("apibase", ""),
            "apikey": data.get("apikey", ""),
            "model": data.get("model", lz.TEMPLATE_INDEX.get(tpl_key, {}).get("defaults", {}).get("model", "")),
            "raw_extra": raw_extra,
            "model_choices": [],
            "model_status": "",
            "model_fetching": False,
        }

    def _api_default_model_for_state(self, state):
        tpl_key = state.get("tpl_key")
        return str(lz.TEMPLATE_INDEX.get(tpl_key, {}).get("defaults", {}).get("model", "") or "").strip()

    def _api_apply_template_model(self, state, previous_default=""):
        new_default = self._api_default_model_for_state(state)
        current = (state.get("model") or "").strip()
        if (not current) or (previous_default and current == previous_default):
            state["model"] = new_default

    def _api_base_name(self, state, idx):
        raw = (state.get("apibase") or "").strip()
        host = ""
        if raw:
            try:
                parsed = lz.urlparse(raw if "://" in raw else f"https://{raw}")
                host = (parsed.netloc or parsed.path.split("/", 1)[0]).strip()
            except Exception:
                host = ""
        if host:
            host = host.split("@")[-1].split(":", 1)[0].strip().lower()
            if host.startswith("www."):
                host = host[4:]
            if host:
                return host
        return f"{lz.TEMPLATE_INDEX.get(state.get('tpl_key'), {}).get('label', 'api')}-{idx + 1}"

    def _api_build_save_configs(self):
        configs = []
        used_names = set()
        for idx, state in enumerate(self._qt_api_state):
            fmt = self._api_format_meta(state.get("format"))
            tpl = lz.TEMPLATE_INDEX.get(state.get("tpl_key"), {})
            kind = fmt.get("kind") or tpl.get("kind") or "native_oai"
            data = dict(tpl.get("defaults") or {})
            data.update(dict(state.get("raw_extra") or {}))
            api_mode = fmt.get("api_mode")
            if api_mode:
                data["api_mode"] = api_mode
            else:
                data.pop("api_mode", None)
            apibase = (state.get("apibase") or "").strip()
            apikey = (state.get("apikey") or "").strip()
            model = (state.get("model") or "").strip()
            if apibase:
                data["apibase"] = apibase
            elif not data.get("apibase"):
                data.pop("apibase", None)
            if apikey:
                data["apikey"] = apikey
            else:
                data.pop("apikey", None)
            if model:
                data["model"] = model
            else:
                data.pop("model", None)
            base_name = self._api_base_name(state, idx) or f"api-{idx + 1}"
            name = base_name
            serial = 2
            while name in used_names:
                name = f"{base_name}-{serial}"
                serial += 1
            used_names.add(name)
            data["name"] = name
            configs.append({"var": state["var"], "kind": kind, "data": data})
        return configs + list(self._qt_api_hidden_configs)

    def _reload_api_editor_state(self):
        if not hasattr(self, "settings_api_notice"):
            return
        self._clear_layout(self.settings_api_list_layout)
        if not lz.is_valid_agent_dir(self.agent_dir):
            self.settings_api_notice.setText("请先选择有效的 GenericAgent 目录。")
            return
        py_path, parsed = self._load_mykey_source()
        self._qt_api_py_path = py_path
        self._qt_api_parse_error = parsed.get("error") or ""
        self._qt_api_hidden_configs = [
            {"var": c["var"], "kind": c["kind"], "data": dict(c["data"])}
            for c in parsed["configs"]
            if c["kind"] not in ("native_claude", "native_oai")
        ]
        self._qt_api_state = [
            self._api_make_simple_state(c)
            for c in parsed["configs"]
            if c["kind"] in ("native_claude", "native_oai")
        ]
        if not self._qt_api_state:
            self._qt_api_add_channel("oai_chat", render=False)
        self._qt_api_extras = dict(parsed.get("extras") or {})
        self._qt_api_passthrough = list(parsed.get("passthrough") or [])
        notices = [py_path]
        if self._qt_api_parse_error:
            notices.append(f"当前解析失败：{self._qt_api_parse_error}。继续保存会覆盖为启动器可识别的格式。")
        if self._qt_api_hidden_configs:
            notices.append(f"检测到 {len(self._qt_api_hidden_configs)} 条旧式或高级配置，本页保存时会原样保留。")
        if self._qt_api_passthrough:
            notices.append(f"检测到 {len(self._qt_api_passthrough)} 条表单不直接编辑的原文项，保存时会原样保留。")
        self.settings_api_notice.setText("\n".join(notices))
        self._render_api_cards()

    def _render_api_cards(self):
        self._clear_layout(self.settings_api_list_layout)
        if not self._qt_api_state:
            empty = QLabel("（还没有 API 卡片，点上方“添加 API 卡片”开始）")
            empty.setStyleSheet(f"font-size: 13px; color: {C['muted']}; padding: 12px 0;")
            self.settings_api_list_layout.addWidget(empty)
            return
        for idx, state in enumerate(self._qt_api_state):
            card = self._panel_card()
            body = QVBoxLayout(card)
            body.setContentsMargins(16, 14, 16, 14)
            body.setSpacing(10)

            head = QHBoxLayout()
            title = QLabel(f"API 卡片 {idx + 1}")
            title.setObjectName("cardTitle")
            head.addWidget(title, 0)
            meta = QLabel(
                f"{lz.SIMPLE_FORMAT_LABEL.get(state.get('format'), state.get('format', ''))} · "
                f"{lz.TEMPLATE_INDEX.get(state.get('tpl_key'), {}).get('label', '未选择模板')}"
            )
            meta.setObjectName("mutedText")
            head.addWidget(meta, 0)
            head.addStretch(1)
            delete_btn = QPushButton("删除")
            delete_btn.setStyleSheet(self._action_button_style())
            delete_btn.clicked.connect(lambda _=False, i=idx: self._qt_api_delete(i))
            head.addWidget(delete_btn, 0)
            body.addLayout(head)

            row1 = QHBoxLayout()
            row1.setSpacing(10)
            row1.addWidget(QLabel("协议"), 0)
            format_box = QComboBox()
            format_box.addItems(self._api_format_options())
            format_box.setCurrentText(lz.SIMPLE_FORMAT_LABEL.get(state.get("format"), "Chat Completions"))
            row1.addWidget(format_box, 1)
            row1.addWidget(QLabel("模板"), 0)
            tpl_box = QComboBox()
            tpl_choices = self._api_template_choices(state.get("format"))
            tpl_map = {k: lbl for k, lbl in tpl_choices}
            tpl_box.addItems([lbl for _, lbl in tpl_choices])
            tpl_box.setCurrentText(tpl_map.get(state.get("tpl_key"), tpl_choices[0][1] if tpl_choices else ""))
            row1.addWidget(tpl_box, 1)
            body.addLayout(row1)

            row2 = QHBoxLayout()
            row2.setSpacing(10)
            row2.addWidget(QLabel("URL"), 0)
            url_edit = QLineEdit()
            url_edit.setPlaceholderText("例如 https://api.openai.com/v1")
            url_edit.setText(str(state.get("apibase") or ""))
            row2.addWidget(url_edit, 1)
            body.addLayout(row2)

            row3 = QHBoxLayout()
            row3.setSpacing(10)
            row3.addWidget(QLabel("模型"), 0)
            model_box = QComboBox()
            model_box.setEditable(True)
            model_choices = list(state.get("model_choices") or [])
            current_model = (state.get("model") or self._api_default_model_for_state(state) or "").strip()
            if current_model and current_model not in model_choices:
                model_choices.insert(0, current_model)
            if not model_choices:
                model_choices = [current_model] if current_model else [""]
            model_box.addItems(model_choices)
            model_box.setCurrentText(current_model)
            row3.addWidget(model_box, 1)
            fetch_btn = QPushButton("拉取模型")
            fetch_btn.setEnabled(not state.get("model_fetching"))
            fetch_btn.setStyleSheet(self._action_button_style())
            fetch_btn.clicked.connect(lambda _=False, s=state: self._qt_api_fetch_models(s))
            row3.addWidget(fetch_btn, 0)
            body.addLayout(row3)

            row4 = QHBoxLayout()
            row4.setSpacing(10)
            row4.addWidget(QLabel("Key"), 0)
            key_edit = QLineEdit()
            key_edit.setEchoMode(QLineEdit.Password)
            key_edit.setPlaceholderText("API Key")
            key_edit.setText(str(state.get("apikey") or ""))
            row4.addWidget(key_edit, 1)
            show_btn = QPushButton("显示")
            show_btn.setCheckable(True)
            show_btn.setStyleSheet(self._action_button_style())
            def toggle_key(checked, edit=key_edit, btn=show_btn):
                edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
                btn.setText("隐藏" if checked else "显示")
            show_btn.toggled.connect(toggle_key)
            row4.addWidget(show_btn, 0)
            body.addLayout(row4)

            status = QLabel(state.get("model_status") or "")
            status.setWordWrap(True)
            status.setObjectName("mutedText")
            body.addWidget(status)

            summary = QLabel("")
            summary.setWordWrap(True)
            summary.setObjectName("mutedText")
            body.addWidget(summary)

            def sync_summary(s=state, label=summary):
                fmt = self._api_format_meta(s.get("format"))
                defaults = dict(lz.TEMPLATE_INDEX.get(s.get("tpl_key"), {}).get("defaults") or {})
                model = (s.get("model") or defaults.get("model") or "请手动填写模型名").strip()
                notes = []
                if defaults.get("fake_cc_system_prompt"):
                    notes.append("自动带 Claude Code 兼容参数")
                if fmt.get("api_mode"):
                    notes.append(f"api_mode={fmt['api_mode']}")
                if defaults.get("read_timeout"):
                    notes.append(f"read_timeout={defaults['read_timeout']}")
                label.setText(
                    f"{fmt.get('hint', '')} 模板默认模型：{model}；"
                    f"{'，'.join(notes) if notes else '自动写入模板里的默认参数'}。"
                )

            def on_format_change(choice, s=state, tpl_widget=tpl_box, model_widget=model_box, status_label=status):
                format_key = self._api_format_from_label(choice)
                previous_default = self._api_default_model_for_state(s)
                s["format"] = format_key
                s["model_status"] = ""
                s["raw_extra"] = self._api_prune_managed_extra(s.get("raw_extra"), drop_template=True, drop_format=True)
                new_choices = self._api_template_choices(format_key)
                new_map = {k: lbl for k, lbl in new_choices}
                current_key = s.get("tpl_key")
                if current_key not in new_map:
                    current_key = self._api_format_meta(format_key).get("default_template", new_choices[0][0] if new_choices else "")
                s["tpl_key"] = current_key
                self._api_apply_template_model(s, previous_default)
                tpl_widget.blockSignals(True)
                tpl_widget.clear()
                tpl_widget.addItems([lbl for _, lbl in new_choices])
                tpl_widget.setCurrentText(new_map.get(current_key, ""))
                tpl_widget.blockSignals(False)
                model_widget.setCurrentText(s.get("model") or "")
                status_label.setText("")
                sync_summary()

            def on_tpl_change(choice, s=state, status_label=status, model_widget=model_box):
                rev = {lbl: key for key, lbl in self._api_template_choices(s.get("format"))}
                previous_default = self._api_default_model_for_state(s)
                s["tpl_key"] = rev.get(choice, s.get("tpl_key"))
                s["model_status"] = ""
                s["raw_extra"] = self._api_prune_managed_extra(s.get("raw_extra"), drop_template=True, drop_format=False)
                self._api_apply_template_model(s, previous_default)
                model_widget.setCurrentText(s.get("model") or "")
                status_label.setText("")
                sync_summary()

            format_box.currentTextChanged.connect(on_format_change)
            tpl_box.currentTextChanged.connect(on_tpl_change)
            url_edit.textChanged.connect(lambda text, s=state: s.__setitem__("apibase", text))
            key_edit.textChanged.connect(lambda text, s=state: s.__setitem__("apikey", text))
            model_box.currentTextChanged.connect(lambda text, s=state: s.__setitem__("model", text.strip()))
            sync_summary()
            self.settings_api_list_layout.addWidget(card)
        self.settings_api_list_layout.addStretch(1)

    def _qt_api_add_channel(self, format_key, *, render=True):
        fmt = self._api_format_meta(format_key)
        kind = fmt.get("kind")
        if kind not in ("native_claude", "native_oai"):
            return
        existing = {s["var"] for s in self._qt_api_state}
        existing.update({c["var"] for c in self._qt_api_hidden_configs})
        var = lz.auto_config_var(kind, existing)
        tpl_key = fmt.get("default_template", "openai")
        defaults = dict(lz.TEMPLATE_INDEX.get(tpl_key, {}).get("defaults") or {})
        self._qt_api_state.append(
            {
                "var": var,
                "format": format_key,
                "tpl_key": tpl_key,
                "apibase": defaults.get("apibase", ""),
                "apikey": "",
                "model": defaults.get("model", ""),
                "raw_extra": {},
                "model_choices": [],
                "model_status": "",
                "model_fetching": False,
            }
        )
        if render:
            self._render_api_cards()

    def _qt_api_delete(self, idx):
        if 0 <= idx < len(self._qt_api_state):
            del self._qt_api_state[idx]
            self._render_api_cards()

    def _qt_api_fetch_models(self, state):
        base = (state.get("apibase") or "").strip()
        if not base:
            state["model_status"] = "请先填写 URL，再拉取模型。"
            self._render_api_cards()
            return
        if state.get("model_fetching"):
            return
        state["model_fetching"] = True
        state["model_status"] = "正在拉取模型列表…"
        self._render_api_cards()

        def worker():
            try:
                models = lz._fetch_remote_models(state.get("format"), state.get("apibase"), state.get("apikey"))
                def done_ok():
                    state["model_fetching"] = False
                    state["model_choices"] = models
                    if models and not (state.get("model") or "").strip():
                        state["model"] = models[0]
                    state["model_status"] = f"已拉取 {len(models)} 个模型，可直接选择或继续手输。"
                    self._render_api_cards()
                QTimer.singleShot(0, done_ok)
            except Exception as e:
                def done_err():
                    state["model_fetching"] = False
                    state["model_status"] = f"拉取失败：{e}"
                    self._render_api_cards()
                QTimer.singleShot(0, done_err)

        threading.Thread(target=worker, daemon=True).start()

    def _qt_api_save(self, restart=False):
        if not self._qt_api_py_path:
            QMessageBox.warning(self, "无法保存", "还没有可用的 mykey.py。")
            return
        try:
            txt = lz.serialize_mykey_py(
                configs=self._api_build_save_configs(),
                extras=self._qt_api_extras,
                passthrough=self._qt_api_passthrough,
            )
            with open(self._qt_api_py_path, "w", encoding="utf-8") as f:
                f.write(txt)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return
        restarted = self._restart_running_channels(show_errors=False)
        if restart:
            self._restart_bridge()
            QMessageBox.information(self, "已保存", "已写入 mykey.py，并已重启聊天内核。")
        else:
            extra = f"\n已自动重启 {restarted} 个由启动器托管的通讯渠道。" if restarted else ""
            QMessageBox.information(self, "已保存", "已写入 mykey.py。聊天内核需重启后才会读取新配置。" + extra)
        self._reload_api_editor_state()
        self._reload_channels_editor_state()

    def _open_raw_mykey_editor(self):
        if not lz.is_valid_agent_dir(self.agent_dir):
            QMessageBox.warning(self, "目录无效", "请先选择有效的 GenericAgent 目录。")
            return
        py_path, _ = self._load_mykey_source()
        try:
            with open(py_path, "r", encoding="utf-8") as f:
                original = f.read()
        except Exception as e:
            QMessageBox.critical(self, "打开失败", str(e))
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("直接编辑 mykey.py")
        dlg.resize(920, 720)
        dlg.setModal(True)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        path_label = QLabel(py_path)
        path_label.setWordWrap(True)
        path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path_label.setObjectName("mutedText")
        layout.addWidget(path_label)

        hint = QLabel("这里是 Qt 下的原文编辑入口。保存后会直接写回 mykey.py；如果你需要高级字段或手写结构，用这个入口最稳。")
        hint.setWordWrap(True)
        hint.setObjectName("softTextSmall")
        layout.addWidget(hint)

        editor = QTextEdit()
        editor.setPlainText(original)
        editor.setStyleSheet(
            f"QTextEdit {{ background: {C['field_bg']}; color: {C['code_text']}; border: 1px solid {C['stroke_default']}; border-radius: {F['radius_md']}px; font-family: Consolas, 'Microsoft YaHei UI'; font-size: 13px; }}"
        )
        layout.addWidget(editor, 1)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        cancel_btn = QPushButton("关闭")
        cancel_btn.setStyleSheet(self._action_button_style())
        btns.addWidget(cancel_btn, 0)
        btns.addStretch(1)
        save_btn = QPushButton("保存")
        save_btn.setStyleSheet(self._action_button_style())
        btns.addWidget(save_btn, 0)
        restart_btn = QPushButton("保存并重启内核")
        restart_btn.setStyleSheet(self._action_button_style(primary=True))
        btns.addWidget(restart_btn, 0)
        layout.addLayout(btns)

        def do_save(restart=False):
            text = editor.toPlainText()
            try:
                compile(text, py_path, "exec")
            except Exception as e:
                QMessageBox.warning(dlg, "语法错误", str(e))
                return
            try:
                with open(py_path, "w", encoding="utf-8") as f:
                    f.write(text)
            except Exception as e:
                QMessageBox.critical(dlg, "保存失败", str(e))
                return
            restarted = self._restart_running_channels(show_errors=False)
            if restart:
                self._restart_bridge()
            self._reload_api_editor_state()
            self._reload_channels_editor_state()
            msg = "已写入 mykey.py。"
            if restarted:
                msg += f"\n已自动重启 {restarted} 个由启动器托管的通讯渠道。"
            if restart:
                msg += "\n聊天内核也已重启。"
            QMessageBox.information(dlg, "已保存", msg)
            dlg.accept()

        cancel_btn.clicked.connect(dlg.reject)
        save_btn.clicked.connect(lambda: do_save(False))
        restart_btn.clicked.connect(lambda: do_save(True))
        dlg.exec()

    def _archive_limit_bucket(self):
        bucket = self.cfg.get("session_archive_limits")
        if not isinstance(bucket, dict):
            bucket = {}
            self.cfg["session_archive_limits"] = bucket
        return bucket

    def _archive_known_channel_ids(self):
        ids = ["launcher"]
        ids.extend(spec.get("id") for spec in lz.COMM_CHANNEL_SPECS if spec.get("id"))
        seen = set()
        ordered = []
        for cid in ids:
            cid = str(cid or "").strip().lower()
            if not cid or cid in seen:
                continue
            seen.add(cid)
            ordered.append(cid)
        if lz.is_valid_agent_dir(self.agent_dir):
            for meta in lz.list_sessions(self.agent_dir):
                try:
                    session = lz.load_session(self.agent_dir, meta["id"])
                except Exception:
                    session = None
                cid = lz._normalize_usage_channel_id((session or {}).get("channel_id"), "launcher")
                if cid not in seen:
                    seen.add(cid)
                    ordered.append(cid)
        return ordered

    def _archive_channel_label(self, channel_id):
        return lz._usage_channel_label(channel_id)

    def _archive_limit_for_channel(self, channel_id):
        bucket = self._archive_limit_bucket()
        raw = bucket.get(channel_id, 10)
        try:
            value = int(raw)
        except Exception:
            value = 10
        return max(0, value)

    def _collect_archive_stats(self):
        active = {}
        if not lz.is_valid_agent_dir(self.agent_dir):
            return {"active": active}
        for meta in lz.list_sessions(self.agent_dir):
            try:
                session = lz.load_session(self.agent_dir, meta["id"])
            except Exception:
                session = None
            cid = lz._normalize_usage_channel_id((session or {}).get("channel_id"), "launcher")
            active[cid] = active.get(cid, 0) + 1
        return {"active": active}

    def _reload_personal_panel(self):
        if not hasattr(self, "settings_personal_notice"):
            return
        self._clear_layout(self.settings_personal_list_layout)
        self._archive_limit_inputs = {}
        if not lz.is_valid_agent_dir(self.agent_dir):
            self.settings_personal_notice.setText("请先选择有效的 GenericAgent 目录。")
            return
        self.settings_personal_notice.setText(
            "启动器已经能识别会话所属渠道，并按 channel_id 区分会话上限。当前主聊天区记为“启动器”，其余渠道会按微信、QQ、Telegram 等分别统计。超出上限时会自动删除最旧未收藏会话。"
        )
        stats = self._collect_archive_stats()
        for cid in self._archive_known_channel_ids():
            card = self._panel_card()
            row = QHBoxLayout(card)
            row.setContentsMargins(14, 12, 14, 12)
            row.setSpacing(12)
            title = QLabel(self._archive_channel_label(cid))
            title.setFixedWidth(110)
            title.setObjectName("bodyText")
            row.addWidget(title, 0)
            spin = QSpinBox()
            spin.setRange(0, 9999)
            spin.setValue(self._archive_limit_for_channel(cid))
            spin.setSingleStep(10)
            spin.setStyleSheet(
                f"QSpinBox {{ background: {C['field_bg']}; color: {C['text']}; border: 1px solid {C['stroke_default']}; border-radius: {F['radius_md']}px; padding: 8px 10px; min-width: 96px; }}"
                f"QSpinBox::up-button, QSpinBox::down-button {{ width: 20px; border: none; background: transparent; }}"
            )
            row.addWidget(spin, 0)
            hint = QLabel("0 = 不自动清理")
            hint.setObjectName("mutedText")
            row.addWidget(hint, 0)
            row.addStretch(1)
            active_count = int(stats["active"].get(cid, 0) or 0)
            summary = QLabel(f"当前会话 {active_count}")
            summary.setObjectName("softTextSmall")
            row.addWidget(summary, 0)
            self._archive_limit_inputs[cid] = spin
            self.settings_personal_list_layout.addWidget(card)
        self.settings_personal_list_layout.addStretch(1)

    def _save_archive_settings(self):
        if not hasattr(self, "_archive_limit_inputs"):
            return
        bucket = self._archive_limit_bucket()
        for cid, spin in self._archive_limit_inputs.items():
            bucket[cid] = int(spin.value() or 0)
        self.cfg["session_archive_limits"] = bucket
        lz.save_config(self.cfg)
        removed = self._enforce_session_archive_limits(exclude_session_ids={((self.current_session or {}).get("id"))})
        self._reload_personal_panel()
        self._reload_usage_panel()
        self._refresh_sessions()
        if removed:
            QMessageBox.information(self, "已保存", f"会话上限已保存，并已自动删除 {removed} 个旧会话。")
        else:
            QMessageBox.information(self, "已保存", "会话上限已保存。当前没有触发新的自动清理。")

    def _channel_cfg_bucket(self):
        bucket = self.cfg.get("communication_channels")
        if not isinstance(bucket, dict):
            bucket = {}
            self.cfg["communication_channels"] = bucket
        return bucket

    def _channel_runtime_cfg(self, channel_id):
        bucket = self._channel_cfg_bucket()
        item = bucket.get(channel_id)
        if not isinstance(item, dict):
            item = {}
            bucket[channel_id] = item
        return item

    def _channel_is_auto_start(self, channel_id):
        return bool(self._channel_runtime_cfg(channel_id).get("auto_start", False))

    def _channel_set_auto_start(self, channel_id, enabled, persist=True):
        self._channel_runtime_cfg(channel_id)["auto_start"] = bool(enabled)
        if persist:
            lz.save_config(self.cfg)

    def _channel_format_value(self, field, value):
        if field.get("kind") in ("list_str", "list_int"):
            if not isinstance(value, (list, tuple)):
                return ""
            return ", ".join(str(x) for x in value if str(x).strip())
        return "" if value is None else str(value)

    def _channel_parse_value(self, field, raw):
        text = (raw or "").strip()
        kind = field.get("kind", "text")
        if kind == "list_str":
            return [item.strip() for item in text.split(",") if item.strip()]
        if kind == "list_int":
            out = []
            for item in text.split(","):
                item = item.strip()
                if not item:
                    continue
                out.append(int(item) if re.fullmatch(r"-?\d+", item) else item)
            return out
        return text

    def _channel_field_label(self, channel_id, key):
        spec = lz.COMM_CHANNEL_INDEX.get(channel_id, {})
        for field in spec.get("fields", []):
            if field.get("key") == key:
                return field.get("label", key)
        return key

    def _wx_token_info(self):
        path = lz.WX_TOKEN_PATH
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_wx_token_info(self, payload):
        path = lz.WX_TOKEN_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        current = self._wx_token_info()
        current.update(payload or {})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)

    def _open_wechat_qr_dialog(self, show_errors=True):
        try:
            resp = lz.requests.get(f"{lz.WX_BOT_API}/ilink/bot/get_bot_qrcode", params={"bot_type": 3}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            qr_id = data["qrcode"]
            qr_text = data.get("qrcode_img_content", "")
            if not qr_text:
                raise RuntimeError("接口没有返回二维码内容。")
        except Exception as e:
            if show_errors:
                QMessageBox.warning(self, "微信二维码获取失败", str(e))
            return False

        dlg = QDialog(self)
        dlg.setWindowTitle("微信扫码登录")
        dlg.setModal(True)
        dlg.resize(420, 560)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("微信扫码登录")
        title.setObjectName("titleDisplay")
        layout.addWidget(title)

        desc = QLabel("这是上游个人微信 Bot 的登录二维码。扫码并在手机上确认后，启动器会写入绑定缓存，然后再启动微信渠道。")
        desc.setWordWrap(True)
        desc.setObjectName("cardDesc")
        layout.addWidget(desc)

        qr_frame = self._panel_card()
        qr_box = QVBoxLayout(qr_frame)
        qr_box.setContentsMargins(16, 16, 16, 16)
        qr_box.setSpacing(0)
        qr_img = lz.qrcode.make(qr_text).convert("RGB")
        buf = io.BytesIO()
        qr_img.save(buf, format="PNG")
        pix = QPixmap()
        pix.loadFromData(buf.getvalue(), "PNG")
        qr_label = QLabel()
        qr_label.setAlignment(Qt.AlignCenter)
        qr_label.setPixmap(pix.scaled(280, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        qr_box.addWidget(qr_label)
        layout.addWidget(qr_frame)

        status_label = QLabel("请使用微信扫码，确认后会自动完成绑定。")
        status_label.setWordWrap(True)
        status_label.setObjectName("softText")
        layout.addWidget(status_label)

        detail_label = QLabel(f"二维码 ID: {qr_id}")
        detail_label.setWordWrap(True)
        detail_label.setObjectName("mutedText")
        layout.addWidget(detail_label)

        btns = QHBoxLayout()
        btns.setSpacing(10)
        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet(self._action_button_style())
        refresh_btn = QPushButton("重新获取")
        refresh_btn.setStyleSheet(self._action_button_style(primary=True))
        btns.addWidget(close_btn, 0)
        btns.addStretch(1)
        btns.addWidget(refresh_btn, 0)
        layout.addLayout(btns)

        stop_event = threading.Event()
        result = {"ok": False}

        def finish_accept(payload):
            if result["ok"]:
                return
            result["ok"] = True
            stop_event.set()
            bot_token = str(payload.get("bot_token", "") or "").strip()
            bot_id = str(payload.get("ilink_bot_id", "") or "").strip()
            self._save_wx_token_info(
                {
                    "bot_token": bot_token,
                    "ilink_bot_id": bot_id,
                    "login_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
            dlg.accept()

        def close_dialog():
            stop_event.set()
            dlg.reject()

        def restart_dialog():
            stop_event.set()
            dlg.done(2)

        close_btn.clicked.connect(close_dialog)
        refresh_btn.clicked.connect(restart_dialog)

        def poll_status():
            last_status = ""
            while not stop_event.is_set():
                time.sleep(2)
                if stop_event.is_set():
                    return
                try:
                    resp = lz.requests.get(f"{lz.WX_BOT_API}/ilink/bot/get_qrcode_status", params={"qrcode": qr_id}, timeout=60)
                    payload = resp.json()
                except lz.requests.exceptions.ReadTimeout:
                    continue
                except Exception as e:
                    QTimer.singleShot(0, lambda msg=str(e): status_label.setText(f"轮询失败：{msg}"))
                    continue
                status = str(payload.get("status", "") or "")
                if status != last_status:
                    last_status = status
                    QTimer.singleShot(0, lambda st=status: status_label.setText(f"当前状态：{st or '等待扫码'}"))
                if status == "confirmed":
                    QTimer.singleShot(0, lambda p=payload: finish_accept(p))
                    return
                if status == "expired":
                    QTimer.singleShot(
                        0,
                        lambda: (
                            status_label.setText("二维码已过期，请点“重新获取”。"),
                            detail_label.setText(f"二维码 ID: {qr_id}"),
                        ),
                    )
                    return

        threading.Thread(target=poll_status, daemon=True).start()
        code = dlg.exec()
        stop_event.set()
        if code == 2:
            return self._open_wechat_qr_dialog(show_errors=show_errors)
        return bool(result["ok"])

    def _load_channels_source(self):
        py_path, parsed = self._load_mykey_source()
        return self.agent_dir, py_path, parsed

    def _channel_missing_required(self, channel_id, values):
        spec = lz.COMM_CHANNEL_INDEX.get(channel_id, {})
        missing = []
        for key in spec.get("required", []):
            value = values.get(key)
            if isinstance(value, (list, tuple, set)):
                if len(value) == 0:
                    missing.append(self._channel_field_label(channel_id, key))
            elif not str(value or "").strip():
                missing.append(self._channel_field_label(channel_id, key))
        return missing

    def _channel_log_path(self, channel_id):
        base = os.path.join(self.agent_dir, "temp", "launcher_channels")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, f"{channel_id}.log")

    def _channel_tail_log(self, channel_id, limit=1000):
        log_path = self._channel_log_path(channel_id)
        if not os.path.isfile(log_path):
            return ""
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                data = f.read()
            return data[-limit:].strip()
        except Exception:
            return ""

    def _channel_session_title(self, channel_id, started_at=None):
        ts = float(started_at or time.time())
        return f"{lz._usage_channel_label(channel_id)} 进程 {time.strftime('%m-%d %H:%M', time.localtime(ts))}"

    def _channel_session_markdown(self, channel_id, process_status, pid, started_at, ended_at, log_path):
        started_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started_at or time.time()))
        ended_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ended_at)) if ended_at else "仍在运行"
        log_text = self._channel_tail_log(channel_id, limit=16000)
        parts = [
            f"**{lz._usage_channel_label(channel_id)} 渠道进程快照**",
            "",
            f"- 状态：{process_status}",
            f"- PID：{pid or '未知'}",
            f"- 启动时间：{started_text}",
            f"- 结束时间：{ended_text}",
            f"- 日志文件：`{log_path}`" if log_path else "- 日志文件：暂无",
            "",
            "```log",
            log_text or "(暂无日志输出)",
            "```",
        ]
        return "\n".join(parts)

    def _find_reusable_channel_process_session(self, channel_id):
        if not lz.is_valid_agent_dir(self.agent_dir):
            return None
        cid = lz._normalize_usage_channel_id(channel_id, "launcher")
        latest = None
        latest_ts = -1.0
        for meta in lz.list_sessions(self.agent_dir):
            try:
                data = lz.load_session(self.agent_dir, meta["id"])
            except Exception:
                data = None
            if not isinstance(data, dict):
                continue
            if str(data.get("session_kind") or "").strip().lower() != "channel_process":
                continue
            if lz._normalize_usage_channel_id(data.get("channel_id"), "launcher") != cid:
                continue
            ts = float(data.get("updated_at", data.get("process_started_at", 0)) or 0)
            if ts >= latest_ts:
                latest = data
                latest_ts = ts
        return latest

    def _create_channel_process_session(self, channel_id, proc, log_path):
        started_at = time.time()
        existing = self._find_reusable_channel_process_session(channel_id)
        session_id = str((existing or {}).get("id") or "").strip() or uuid.uuid4().hex[:12]
        session = {
            "id": session_id,
            "title": self._channel_session_title(channel_id, started_at),
            "created_at": float((existing or {}).get("created_at", started_at) or started_at),
            "updated_at": started_at,
            "session_kind": "channel_process",
            "session_source_label": lz._usage_channel_label(channel_id),
            "channel_id": lz._normalize_usage_channel_id(channel_id, "launcher"),
            "channel_label": lz._usage_channel_label(channel_id),
            "process_pid": int(getattr(proc, "pid", 0) or 0),
            "process_status": "运行中",
            "process_started_at": started_at,
            "process_ended_at": 0,
            "channel_log_path": log_path,
            "bubbles": [
                {
                    "role": "assistant",
                    "text": self._channel_session_markdown(channel_id, "运行中", getattr(proc, "pid", None), started_at, 0, log_path),
                }
            ],
            "backend_history": [],
            "agent_history": [],
            "llm_idx": 0,
            "token_usage": {"events": []},
            "snapshot": {
                "version": 1,
                "kind": "channel_process",
                "captured_at": started_at,
                "turns": 0,
                "llm_idx": 0,
                "process_pid": int(getattr(proc, "pid", 0) or 0),
                "has_backend_history": False,
                "has_agent_history": False,
            },
        }
        if isinstance(existing, dict):
            if bool(existing.get("pinned", False)):
                session["pinned"] = True
        self._ensure_session_usage_metadata(session)
        lz.save_session(self.agent_dir, session, touch=False)
        return session_id

    def _sync_channel_process_session(self, channel_id, *, final=False, exit_code=None):
        info = self._channel_procs.get(channel_id) or {}
        session_id = str(info.get("session_id") or "").strip()
        if not session_id:
            return
        data = lz.load_session(self.agent_dir, session_id)
        if not data:
            return
        proc = info.get("proc")
        started_at = float(data.get("process_started_at", data.get("created_at", time.time())) or time.time())
        pid = int((getattr(proc, "pid", None) or data.get("process_pid") or 0) or 0)
        status = "运行中"
        ended_at = 0
        if final:
            ended_at = time.time()
            code = exit_code if exit_code is not None else (proc.returncode if proc else None)
            status = f"已退出 ({code})" if code is not None else "已退出"
        data["process_pid"] = pid
        data["process_status"] = status
        data["process_started_at"] = started_at
        data["process_ended_at"] = ended_at
        data["channel_log_path"] = info.get("log_path") or data.get("channel_log_path") or self._channel_log_path(channel_id)
        markdown = self._channel_session_markdown(channel_id, status, pid, started_at, ended_at, data.get("channel_log_path"))
        bubbles = list(data.get("bubbles") or [])
        if bubbles and bubbles[-1].get("role") == "assistant":
            bubbles[-1]["text"] = markdown
        else:
            bubbles.append({"role": "assistant", "text": markdown})
        data["bubbles"] = bubbles[-1:]
        snapshot = dict(data.get("snapshot") or {})
        snapshot["version"] = int(snapshot.get("version", 1) or 1)
        snapshot["kind"] = "channel_process"
        snapshot["captured_at"] = time.time()
        snapshot["turns"] = 0
        snapshot["llm_idx"] = 0
        snapshot["process_pid"] = pid
        snapshot["has_backend_history"] = False
        snapshot["has_agent_history"] = False
        data["snapshot"] = snapshot
        new_sig = (status, pid, os.path.getsize(data["channel_log_path"]) if os.path.isfile(data["channel_log_path"]) else -1)
        if (not final) and info.get("last_snapshot_sig") == new_sig:
            return
        info["last_snapshot_sig"] = new_sig
        lz.save_session(self.agent_dir, data)
        if self.current_session and self.current_session.get("id") == session_id:
            self.current_session = data
            self._render_session(self.current_session)
            self._set_status("已同步渠道进程快照。")

    def _sync_all_channel_process_sessions(self):
        if not lz.is_valid_agent_dir(self.agent_dir):
            return
        for channel_id, info in list(self._channel_procs.items()):
            proc = info.get("proc")
            if not proc or proc.poll() is not None:
                continue
            try:
                self._sync_channel_process_session(channel_id, final=False)
            except Exception:
                continue

    def _channel_proc_alive(self, channel_id):
        info = self._channel_procs.get(channel_id) or {}
        proc = info.get("proc")
        return bool(proc and proc.poll() is None)

    def _channel_conflict_message(self, channel_id):
        spec = lz.COMM_CHANNEL_INDEX.get(channel_id, {})
        for other_id in spec.get("conflicts_with", []):
            if self._channel_proc_alive(other_id):
                other = lz.COMM_CHANNEL_INDEX.get(other_id, {}).get("label", other_id)
                return f"{spec.get('label', channel_id)} 与 {other} 在上游共用单实例锁，不能同时启动。"
        return ""

    def _channel_status(self, channel_id, values):
        if self._channel_proc_alive(channel_id):
            return "运行中", C["accent"]
        conflict = self._channel_conflict_message(channel_id)
        if conflict:
            return "冲突", C["danger_text"]
        missing = self._channel_missing_required(channel_id, values)
        if missing:
            return "待配置", C["danger_text"]
        info = self._channel_procs.get(channel_id) or {}
        proc = info.get("proc")
        if proc and proc.poll() is not None:
            return f"已退出 ({proc.returncode})", C["danger_text"]
        if self._channel_is_auto_start(channel_id):
            return "待自动启动", C["text_soft"]
        return "未启动", C["muted"]

    def _collect_usage_stats(self, lookback_days=7):
        channel_stats = {}
        day_stats = {}
        now = time.time()
        today_key = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        lookback_cutoff = now - max(1, int(lookback_days)) * 86400
        today_total = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "sources": set()}
        recent_total = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "sources": set()}
        all_total = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "sources": set()}

        if not lz.is_valid_agent_dir(self.agent_dir):
            for item in (today_total, recent_total, all_total):
                item["mode"] = lz._usage_mode_from_sources(item.get("sources"))
            return {"today": today_total, "recent": recent_total, "all": all_total, "channels": [], "days": []}

        for meta in lz.list_sessions(self.agent_dir):
            try:
                session = lz.load_session(self.agent_dir, meta["id"])
            except Exception:
                session = None
            if not session:
                continue
            before = json.dumps(session.get("token_usage") or {}, ensure_ascii=False, sort_keys=True)
            self._ensure_session_usage_metadata(session)
            after = json.dumps(session.get("token_usage") or {}, ensure_ascii=False, sort_keys=True)
            if before != after:
                lz.save_session(self.agent_dir, session)
            usage = session.get("token_usage") or {}
            channel_id = str(session.get("channel_id") or usage.get("channel_id") or "launcher").strip().lower()
            channel_row = channel_stats.setdefault(
                channel_id,
                {
                    "channel_id": channel_id,
                    "label": lz._usage_channel_label(channel_id),
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "turns": 0,
                    "sessions": set(),
                    "last_active": 0,
                    "sources": set(),
                },
            )
            channel_row["sessions"].add(session.get("id"))
            channel_row["last_active"] = max(channel_row["last_active"], float(session.get("updated_at", 0) or 0))

            events = list(usage.get("events") or [])
            if not events:
                events = lz._fallback_token_events_from_bubbles(
                    session.get("bubbles") or [],
                    base_ts=session.get("updated_at") or session.get("created_at") or now,
                    channel_id=channel_id,
                    model_name=usage.get("last_model") or "",
                )

            for ev in events:
                inp = int(ev.get("input_tokens", 0) or 0)
                out = int(ev.get("output_tokens", 0) or 0)
                total = int(ev.get("total_tokens", inp + out) or (inp + out))
                try:
                    ts = float(ev.get("ts", session.get("updated_at", now)) or now)
                except Exception:
                    ts = now
                day_key = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                row = day_stats.setdefault(
                    day_key,
                    {
                        "date": day_key,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                        "turns": 0,
                        "channels": {},
                        "sources": set(),
                    },
                )
                source = str(ev.get("usage_source") or "estimate").strip().lower() or "estimate"
                row["input_tokens"] += inp
                row["output_tokens"] += out
                row["total_tokens"] += total
                row["turns"] += 1 if inp > 0 else 0
                row["channels"][channel_id] = row["channels"].get(channel_id, 0) + total
                row["sources"].add(source)

                channel_row["input_tokens"] += inp
                channel_row["output_tokens"] += out
                channel_row["total_tokens"] += total
                channel_row["turns"] += 1 if inp > 0 else 0
                channel_row["sources"].add(source)

                all_total["input_tokens"] += inp
                all_total["output_tokens"] += out
                all_total["total_tokens"] += total
                all_total["sources"].add(source)
                if day_key == today_key:
                    today_total["input_tokens"] += inp
                    today_total["output_tokens"] += out
                    today_total["total_tokens"] += total
                    today_total["sources"].add(source)
                if ts >= lookback_cutoff:
                    recent_total["input_tokens"] += inp
                    recent_total["output_tokens"] += out
                    recent_total["total_tokens"] += total
                    recent_total["sources"].add(source)

        channels = sorted(
            [
                {**row, "sessions": len(row["sessions"]), "mode": lz._usage_mode_from_sources(row.get("sources"))}
                for row in channel_stats.values()
            ],
            key=lambda x: (x["total_tokens"], x["last_active"]),
            reverse=True,
        )
        days = sorted(day_stats.values(), key=lambda x: x["date"], reverse=True)
        for item in (today_total, recent_total, all_total):
            item["mode"] = lz._usage_mode_from_sources(item.get("sources"))
        for row in days:
            row["mode"] = lz._usage_mode_from_sources(row.get("sources"))
        return {"today": today_total, "recent": recent_total, "all": all_total, "channels": channels, "days": days[: max(1, int(lookback_days))]}

    def _reload_usage_panel(self):
        if not hasattr(self, "settings_usage_notice"):
            return
        self._clear_layout(self.settings_usage_list_layout)
        if not lz.is_valid_agent_dir(self.agent_dir):
            self.settings_usage_notice.setText("请先选择有效的 GenericAgent 目录。")
            return
        self.settings_usage_notice.setText("旧会话、以及不返回 usage 的渠道，仍可能只能显示估算。")
        stats = self._collect_usage_stats(lookback_days=7)

        summary_row = QHBoxLayout()
        summary_row.setSpacing(10)
        for title, item in (("今天", stats["today"]), ("近 7 天", stats["recent"]), ("累计", stats["all"])):
            card = self._panel_card()
            box = QVBoxLayout(card)
            box.setContentsMargins(14, 12, 14, 12)
            box.setSpacing(6)
            head = QLabel(title)
            head.setObjectName("cardTitle")
            box.addWidget(head)
            body = QLabel(
                f"{lz._usage_mode_label(item.get('mode'))}\n"
                f"总计 {item['total_tokens']}\n"
                f"输入 {item['input_tokens']}\n"
                f"输出 {item['output_tokens']}"
            )
            body.setObjectName("tokenTree")
            body.setWordWrap(True)
            box.addWidget(body)
            summary_row.addWidget(card, 1)
        self.settings_usage_list_layout.addLayout(summary_row)

        channel_card = self._panel_card()
        channel_box = QVBoxLayout(channel_card)
        channel_box.setContentsMargins(14, 12, 14, 12)
        channel_box.setSpacing(8)
        channel_title = QLabel("按渠道")
        channel_title.setObjectName("cardTitle")
        channel_box.addWidget(channel_title)
        channels = stats.get("channels") or []
        if not channels:
            empty = QLabel("暂无可统计的会话数据")
            empty.setObjectName("mutedText")
            channel_box.addWidget(empty)
        else:
            for row in channels:
                line = QLabel(
                    f"{row['label']} · {lz._usage_mode_label(row.get('mode'))}\n"
                    f"总 {row['total_tokens']}  入 {row['input_tokens']}  出 {row['output_tokens']}  "
                    f"轮次 {row['turns']}  会话 {row['sessions']}"
                )
                line.setWordWrap(True)
                line.setObjectName("softTextSmall")
                channel_box.addWidget(line)
        self.settings_usage_list_layout.addWidget(channel_card)

        day_card = self._panel_card()
        day_box = QVBoxLayout(day_card)
        day_box.setContentsMargins(14, 12, 14, 12)
        day_box.setSpacing(8)
        day_title = QLabel("最近几天")
        day_title.setObjectName("cardTitle")
        day_box.addWidget(day_title)
        days = stats.get("days") or []
        if not days:
            empty = QLabel("最近几天没有可用统计")
            empty.setObjectName("mutedText")
            day_box.addWidget(empty)
        else:
            for row in days:
                parts = []
                for cid, total in sorted(row.get("channels", {}).items(), key=lambda kv: kv[1], reverse=True)[:3]:
                    parts.append(f"{lz._usage_channel_label(cid)} {total}")
                detail = " / ".join(parts) if parts else "无渠道细分"
                line = QLabel(
                    f"{row['date']} · {lz._usage_mode_label(row.get('mode'))}\n"
                    f"总 {row['total_tokens']}  入 {row['input_tokens']}  出 {row['output_tokens']}  "
                    f"轮次 {row['turns']}  |  {detail}"
                )
                line.setWordWrap(True)
                line.setObjectName("softTextSmall")
                day_box.addWidget(line)
        self.settings_usage_list_layout.addWidget(day_card)

    def _reload_about_panel(self):
        if not hasattr(self, "settings_about_list_layout"):
            return
        self._clear_layout(self.settings_about_list_layout)
        rows = [
            ("项目定位", "GenericAgent 的非官方桌面启动器 / 前端壳"),
            ("当前主架构", "Qt 主壳（欢迎页、聊天主区、设置主区）"),
            ("当前状态", "可用，且正持续把 Tk 时代的设置与工具页并到 Qt"),
            ("上游仓库", lz.REPO_URL),
            ("当前配置文件", lz.CONFIG_PATH),
        ]
        for title, value in rows:
            card = self._panel_card()
            line = QHBoxLayout(card)
            line.setContentsMargins(14, 12, 14, 12)
            line.setSpacing(12)
            left = QLabel(title)
            left.setFixedWidth(92)
            left.setObjectName("mutedText")
            right = QLabel(value)
            right.setWordWrap(True)
            right.setTextInteractionFlags(Qt.TextSelectableByMouse)
            right.setObjectName("bodyText")
            line.addWidget(left, 0)
            line.addWidget(right, 1)
            self.settings_about_list_layout.addWidget(card)

    def _reload_channels_editor_state(self):
        if not hasattr(self, "settings_channels_notice"):
            return
        self._clear_layout(self.settings_channels_list_layout)
        if not lz.is_valid_agent_dir(self.agent_dir):
            self.settings_channels_notice.setText("请先选择有效的 GenericAgent 目录。")
            return
        _, py_path, parsed = self._load_channels_source()
        self._qt_channel_py_path = py_path
        self._qt_channel_parse_error = parsed.get("error") or ""
        self._qt_channel_configs = list(parsed.get("configs") or [])
        self._qt_channel_passthrough = list(parsed.get("passthrough") or [])
        self._qt_channel_extras = dict(parsed.get("extras") or {})
        self._qt_channel_states = {}
        notices = [py_path]
        if self._qt_channel_parse_error:
            notices.append(f"当前解析失败：{self._qt_channel_parse_error}。继续保存会覆盖成可识别格式。")
        self.settings_channels_notice.setText("\n".join(notices))
        self._render_channel_cards()

    def _render_channel_cards(self):
        self._clear_layout(self.settings_channels_list_layout)
        for spec in lz.COMM_CHANNEL_SPECS:
            values = {field["key"]: self._qt_channel_extras.get(field["key"]) for field in spec.get("fields", [])}
            status_text, status_color = self._channel_status(spec["id"], values)
            card = self._panel_card()
            body = QVBoxLayout(card)
            body.setContentsMargins(16, 14, 16, 14)
            body.setSpacing(8)

            head = QHBoxLayout()
            title = QLabel(spec["label"])
            title.setObjectName("cardTitle")
            head.addWidget(title, 0)
            subtitle = QLabel(spec.get("subtitle", ""))
            subtitle.setObjectName("mutedText")
            head.addWidget(subtitle, 0)
            head.addStretch(1)
            status = QLabel(status_text)
            status.setStyleSheet(f"font-size: 12px; color: {status_color};")
            head.addWidget(status, 0)
            body.addLayout(head)

            note = QLabel(spec.get("notes", ""))
            note.setWordWrap(True)
            note.setObjectName("mutedText")
            body.addWidget(note)

            state = {"widgets": {}, "auto": None}
            for field in spec.get("fields", []):
                row = QHBoxLayout()
                row.setSpacing(10)
                label = QLabel(field.get("label", field["key"]))
                label.setFixedWidth(92)
                label.setObjectName("softTextSmall")
                row.addWidget(label, 0)
                edit = QLineEdit()
                edit.setPlaceholderText(field.get("placeholder", ""))
                edit.setText(self._channel_format_value(field, values.get(field["key"])))
                if field.get("kind") == "password":
                    edit.setEchoMode(QLineEdit.Password)
                row.addWidget(edit, 1)
                state["widgets"][field["key"]] = edit
                if field.get("kind") == "password":
                    toggle = QPushButton("显示")
                    toggle.setCheckable(True)
                    toggle.setStyleSheet(self._action_button_style())
                    def on_toggle(checked, target=edit, btn=toggle):
                        target.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
                        btn.setText("隐藏" if checked else "显示")
                    toggle.toggled.connect(on_toggle)
                    row.addWidget(toggle, 0)
                body.addLayout(row)

            controls = QHBoxLayout()
            controls.setSpacing(8)
            auto_box = QCheckBox("自动启动")
            auto_box.setChecked(self._channel_is_auto_start(spec["id"]))
            controls.addWidget(auto_box, 0)
            controls.addStretch(1)
            if spec["id"] == "wechat":
                token_info = self._wx_token_info()
                has_token = bool(str(token_info.get("bot_token", "") or "").strip())
                bind_btn = QPushButton("重新扫码" if has_token else "扫码登录")
                bind_btn.setStyleSheet(self._action_button_style(primary=not has_token))
                bind_btn.clicked.connect(lambda _=False: self._open_wechat_qr_dialog())
                controls.addWidget(bind_btn, 0)
            save_btn = QPushButton("保存")
            save_btn.setStyleSheet(self._action_button_style())
            save_btn.clicked.connect(lambda _=False: self._qt_channels_save(silent=False))
            controls.addWidget(save_btn, 0)
            start_btn = QPushButton("启动")
            start_btn.setStyleSheet(self._action_button_style(primary=True))
            start_btn.clicked.connect(lambda _=False, cid=spec["id"]: self._start_channel_process(cid))
            controls.addWidget(start_btn, 0)
            stop_btn = QPushButton("停止")
            stop_btn.setStyleSheet(self._action_button_style())
            stop_btn.clicked.connect(lambda _=False, cid=spec["id"]: self._stop_channel_process(cid))
            controls.addWidget(stop_btn, 0)
            log_btn = QPushButton("日志尾部")
            log_btn.setStyleSheet(self._action_button_style())
            log_btn.clicked.connect(lambda _=False, cid=spec["id"], title=spec["label"]: self._show_channel_log_tail(cid, title))
            controls.addWidget(log_btn, 0)
            body.addLayout(controls)

            self._qt_channel_states[spec["id"]] = state
            state["auto"] = auto_box
            self.settings_channels_list_layout.addWidget(card)
        self.settings_channels_list_layout.addStretch(1)

    def _qt_channels_save(self, silent=False, apply_running=True):
        if not self._qt_channel_py_path:
            if not silent:
                QMessageBox.warning(self, "保存失败", "尚未载入通讯渠道配置。")
            return False
        extras = dict(self._qt_channel_extras)
        for spec in lz.COMM_CHANNEL_SPECS:
            state = self._qt_channel_states.get(spec["id"]) or {}
            for field in spec.get("fields", []):
                edit = state.get("widgets", {}).get(field["key"])
                value = self._channel_parse_value(field, edit.text() if edit is not None else "")
                if isinstance(value, list):
                    if value:
                        extras[field["key"]] = value
                    else:
                        extras.pop(field["key"], None)
                else:
                    if str(value or "").strip():
                        extras[field["key"]] = value
                    else:
                        extras.pop(field["key"], None)
            auto = state.get("auto")
            if auto is not None:
                self._channel_set_auto_start(spec["id"], auto.isChecked(), persist=False)
        try:
            txt = lz.serialize_mykey_py(
                configs=self._qt_channel_configs,
                extras=extras,
                passthrough=self._qt_channel_passthrough,
            )
            with open(self._qt_channel_py_path, "w", encoding="utf-8") as f:
                f.write(txt)
            self._qt_channel_extras = extras
            lz.save_config(self.cfg)
        except Exception as e:
            if not silent:
                QMessageBox.critical(self, "保存失败", str(e))
            return False
        restarted = self._restart_running_channels(show_errors=False) if apply_running else 0
        if not silent:
            msg = "已写入 mykey.py 和启动器渠道配置。"
            if restarted:
                msg += f"\n已自动重启 {restarted} 个由启动器托管的通讯渠道。"
            QMessageBox.information(self, "已保存", msg)
        self._reload_channels_editor_state()
        return True

    def _start_channel_process(self, channel_id, show_errors=True):
        spec = lz.COMM_CHANNEL_INDEX.get(channel_id)
        if not spec:
            return False
        if not self._qt_channels_save(silent=True, apply_running=False):
            return False
        if channel_id == "wechat":
            token_info = self._wx_token_info()
            if not str(token_info.get("bot_token", "") or "").strip():
                if not self._open_wechat_qr_dialog(show_errors=show_errors):
                    return False
        if self._channel_proc_alive(channel_id):
            self._reload_channels_editor_state()
            return True
        if not lz.is_valid_agent_dir(self.agent_dir):
            if show_errors:
                QMessageBox.warning(self, "目录无效", "请先选择有效的 GenericAgent 目录。")
            return False
        conflict = self._channel_conflict_message(channel_id)
        if conflict:
            if show_errors:
                QMessageBox.warning(self, "无法启动", conflict)
            return False
        py, py_err = lz._find_compatible_system_python(self.agent_dir)
        if not py:
            if show_errors:
                QMessageBox.critical(self, "缺少 Python", py_err or "未找到可用的系统 Python。")
            return False
        values = {field["key"]: self._qt_channel_extras.get(field["key"]) for field in spec.get("fields", [])}
        missing = self._channel_missing_required(channel_id, values)
        if missing:
            if show_errors:
                QMessageBox.warning(self, "配置不完整", f"{spec.get('label', channel_id)} 还缺少这些字段：\n- " + "\n- ".join(missing))
            return False
        script_path = os.path.join(self.agent_dir, "frontends", spec.get("script", ""))
        if not os.path.isfile(script_path):
            if show_errors:
                QMessageBox.critical(self, "脚本不存在", f"未找到渠道脚本：\n{script_path}")
            return False
        log_path = self._channel_log_path(channel_id)
        try:
            log_handle = open(log_path, "a", encoding="utf-8", buffering=1)
            log_handle.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} start {channel_id} ====\n")
            proc = subprocess.Popen(
                [py, "-u", script_path],
                cwd=self.agent_dir,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception as e:
            try:
                log_handle.close()
            except Exception:
                pass
            if show_errors:
                QMessageBox.critical(self, "启动失败", str(e))
            return False
        session_id = self._create_channel_process_session(channel_id, proc, log_path)
        self._channel_procs[channel_id] = {
            "proc": proc,
            "log_handle": log_handle,
            "log_path": log_path,
            "session_id": session_id,
            "last_snapshot_sig": None,
        }
        self._sync_channel_process_session(channel_id, final=False)
        QTimer.singleShot(1200, lambda cid=channel_id, se=show_errors: self._after_channel_launch_check(cid, show_errors=se))
        self._reload_channels_editor_state()
        self._last_session_list_signature = None
        self._refresh_sessions()
        return True

    def _after_channel_launch_check(self, channel_id, show_errors=True):
        info = self._channel_procs.get(channel_id)
        if not info:
            return
        proc = info.get("proc")
        if not proc or proc.poll() is None:
            self._sync_channel_process_session(channel_id, final=False)
            self._reload_channels_editor_state()
            return
        self._sync_channel_process_session(channel_id, final=True, exit_code=proc.returncode)
        self._close_channel_log_handle(channel_id)
        tail = self._channel_tail_log(channel_id)
        self._channel_procs.pop(channel_id, None)
        self._reload_channels_editor_state()
        self._last_session_list_signature = None
        self._refresh_sessions()
        if show_errors:
            QMessageBox.warning(self, "渠道启动失败", f"{channel_id} 已退出，返回码 {proc.returncode}。\n\n日志尾部：\n{tail or '(空)'}")

    def _close_channel_log_handle(self, channel_id):
        info = self._channel_procs.get(channel_id) or {}
        handle = info.get("log_handle")
        if handle:
            try:
                handle.close()
            except Exception:
                pass
            info["log_handle"] = None

    def _stop_channel_process(self, channel_id):
        info = self._channel_procs.get(channel_id)
        if not info:
            self._reload_channels_editor_state()
            return False
        proc = info.get("proc")
        try:
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                        proc.wait(timeout=2)
                    except Exception:
                        pass
        finally:
            exit_code = proc.returncode if proc else None
            self._sync_channel_process_session(channel_id, final=True, exit_code=exit_code)
            self._close_channel_log_handle(channel_id)
            self._channel_procs.pop(channel_id, None)
            self._reload_channels_editor_state()
            self._last_session_list_signature = None
            self._refresh_sessions()
        return True

    def _stop_all_managed_channels(self, refresh=True):
        count = 0
        for channel_id in list(self._channel_procs.keys()):
            if self._stop_channel_process(channel_id):
                count += 1
        if refresh:
            self._reload_channels_editor_state()
        return count

    def _restart_running_channels(self, show_errors=False):
        running = [cid for cid in self._channel_procs if self._channel_proc_alive(cid)]
        restarted = 0
        for channel_id in running:
            self._stop_channel_process(channel_id)
            if self._start_channel_process(channel_id, show_errors=show_errors):
                restarted += 1
        return restarted

    def _show_channel_log_tail(self, channel_id, title):
        tail = self._channel_tail_log(channel_id) or "暂无日志。"
        QMessageBox.information(self, f"{title} 日志尾部", tail)

    def _start_autostart_channels(self):
        if not lz.is_valid_agent_dir(self.agent_dir):
            return
        for spec in lz.COMM_CHANNEL_SPECS:
            if self._channel_is_auto_start(spec["id"]) and not self._channel_proc_alive(spec["id"]):
                self._start_channel_process(spec["id"], show_errors=False)

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

    def _set_agent_dir(self, path: str, *, persist: bool = True):
        raw = str(path or "").strip()
        new_dir = os.path.abspath(raw) if raw else ""
        changed = os.path.normcase(new_dir) != os.path.normcase(self.agent_dir)
        self.agent_dir = new_dir
        if changed:
            self._stop_bridge()
            self._stop_all_managed_channels(refresh=False)
            self.current_session = None
            self._selected_session_id = None
            self._pending_state_session = None
            self._ignore_session_select = True
            self.session_list.clear()
            self._ignore_session_select = False
            self._last_session_list_signature = None
        if persist:
            self.cfg["agent_dir"] = self.agent_dir
            lz.save_config(self.cfg)
        self._refresh_welcome_state()
        self._settings_reload()
        if lz.is_valid_agent_dir(self.agent_dir):
            lz.purge_archived_sessions(self.agent_dir)
            self._enforce_session_archive_limits(refresh=False)
            self._refresh_sessions()

    def _refresh_welcome_state(self):
        valid = lz.is_valid_agent_dir(self.agent_dir)
        if hasattr(self, "recent_path_label"):
            self.recent_path_label.setText(self.agent_dir if valid else "尚未配置有效的 GenericAgent 目录。")
        if hasattr(self, "recent_card"):
            self.recent_card.setVisible(valid)
        if hasattr(self, "enter_chat_btn"):
            self.enter_chat_btn.setEnabled(valid)
        if hasattr(self, "locate_path_edit"):
            self.locate_path_edit.setText(self.agent_dir or "")
        if hasattr(self, "locate_python_edit"):
            self.locate_python_edit.setText(str(self.cfg.get("python_exe") or "").strip())
        if hasattr(self, "locate_status_label"):
            py_cfg = str(self.cfg.get("python_exe") or "").strip()
            if py_cfg:
                py_resolved = lz._resolve_config_path(py_cfg)
                py_text = f"\nPython 可执行文件：{py_cfg}\n解析后：{py_resolved}"
            else:
                py_text = "\nPython 可执行文件：未指定（将自动探测）"
            self.locate_status_label.setText(
                (f"当前目录有效，可以直接载入：\n{self.agent_dir}{py_text}")
                if valid else
                ("当前还没有有效的 GenericAgent 目录。请先浏览并选择正确的项目根目录。" + py_text)
            )
        self._refresh_download_state()
        if hasattr(self, "settings_status_label"):
            self._settings_reload()

    def _choose_agent_dir(self):
        path = QFileDialog.getExistingDirectory(
            self,
            "选择 GenericAgent 目录",
            self.agent_dir or os.path.expanduser("~"),
        )
        if path:
            self._set_agent_dir(path)
            if hasattr(self, "locate_path_edit"):
                self.locate_path_edit.setText(path)

    def _choose_python_executable(self):
        current = ""
        if hasattr(self, "locate_python_edit"):
            current = self.locate_python_edit.text().strip()
        start_dir = os.path.dirname(lz._resolve_config_path(current)) if current else os.path.expanduser("~")
        if not os.path.isdir(start_dir):
            start_dir = os.path.expanduser("~")
        if os.name == "nt":
            path, _ = QFileDialog.getOpenFileName(
                self,
                "选择 Python 可执行文件",
                start_dir,
                "Executable (*.exe);;All Files (*)",
            )
        else:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "选择 Python 可执行文件",
                start_dir,
                "All Files (*)",
            )
        if path and hasattr(self, "locate_python_edit"):
            self.locate_python_edit.setText(lz._make_config_relative_path(path))

    def _locate_enter_chat(self):
        raw = self.locate_path_edit.text().strip() if hasattr(self, "locate_path_edit") else self.agent_dir
        py_raw = self.locate_python_edit.text().strip() if hasattr(self, "locate_python_edit") else ""
        if py_raw:
            resolved = lz._resolve_config_path(py_raw)
            if not os.path.isfile(resolved):
                QMessageBox.warning(self, "Python 路径无效", f"未找到可执行文件：\n{resolved}")
                return
            self.cfg["python_exe"] = lz._make_config_relative_path(resolved)
        else:
            self.cfg.pop("python_exe", None)
        lz.save_config(self.cfg)
        self._set_agent_dir(raw)
        self._enter_chat()

    def _show_locate(self):
        self.setWindowTitle("GenericAgent 启动器")
        self.pages.setCurrentWidget(self._locate_page)
        self._refresh_welcome_state()

    def _show_download(self):
        self.setWindowTitle("GenericAgent 启动器")
        self.pages.setCurrentWidget(self._download_page)
        self._refresh_download_state()

    def _show_welcome(self):
        self.setWindowTitle("GenericAgent 启动器")
        self.pages.setCurrentWidget(self._welcome_page)
        self._refresh_welcome_state()

    def _show_settings(self):
        self.setWindowTitle("GenericAgent 启动器")
        self.pages.setCurrentWidget(self._settings_page)
        valid = lz.is_valid_agent_dir(self.agent_dir)
        if self._settings_top_back_btn is not None:
            self._settings_top_back_btn.setText("←  返回聊天" if valid else "←  返回首页")
            try:
                self._settings_top_back_btn.clicked.disconnect()
            except Exception:
                pass
            self._settings_top_back_btn.clicked.connect(self._show_chat_page if valid else self._show_welcome)
        self._refresh_welcome_state()
        self._settings_reload()

    def _show_chat_page(self):
        self.setWindowTitle("GenericAgent 启动器")
        self.pages.setCurrentWidget(self._chat_page)

    def _save_settings_and_enter_chat(self):
        self._enter_chat()

    def _enter_chat(self):
        if not lz.is_valid_agent_dir(self.agent_dir):
            QMessageBox.warning(self, "目录无效", "请选择有效的 GenericAgent 根目录。")
            self._show_settings()
            return
        ensured = lz._ensure_mykey_file(self.agent_dir)
        if not ensured.get("ok"):
            QMessageBox.critical(
                self,
                "初始化失败",
                "无法准备 mykey.py。\n\n"
                f"目标：{ensured.get('path', '')}\n"
                f"错误：{ensured.get('error', '未知错误')}",
            )
            return
        if ensured.get("created"):
            QMessageBox.information(
                self,
                "已初始化配置文件",
                "已自动创建 mykey.py。\n\n接下来如果提示未配置 LLM，请在后续 Qt 设置页补充 API 配置。",
            )
        self._show_chat_page()
        self._refresh_sessions()
        if self.current_session:
            self._render_session(self.current_session)
        else:
            self._reset_chat_area("选择一个会话，或新建会话开始聊天。")
        self._start_autostart_channels()
        if self.bridge_proc is None or self.bridge_proc.poll() is not None:
            self._safe_start_bridge()

    def _bind_session_to_current_bridge(self, session):
        if not isinstance(session, dict):
            return
        session["process_pid"] = getattr(self.bridge_proc, "pid", None)
        session["llm_idx"] = int(self._current_llm_index() or 0)
        snapshot = dict(session.get("snapshot") or {})
        snapshot["version"] = int(snapshot.get("version", 1) or 1)
        snapshot["kind"] = str(snapshot.get("kind") or "turn_complete").strip() or "turn_complete"
        snapshot["captured_at"] = float(snapshot.get("captured_at", session.get("updated_at", time.time())) or time.time())
        snapshot["turns"] = int(snapshot.get("turns", ((session.get("token_usage") or {}).get("turns", 0) or 0)) or 0)
        snapshot["llm_idx"] = int(session.get("llm_idx", 0) or 0)
        snapshot["process_pid"] = int(session.get("process_pid", 0) or 0)
        snapshot["has_backend_history"] = bool(session.get("backend_history"))
        snapshot["has_agent_history"] = bool(session.get("agent_history"))
        session["snapshot"] = snapshot

    def _ensure_session_usage_metadata(self, session):
        if not isinstance(session, dict):
            return
        lz._normalize_token_usage_inplace(session)
        channel_id = str(session.get("channel_id") or "").strip().lower()
        if not channel_id:
            channel_id = "launcher"
        session["channel_id"] = channel_id
        session["channel_label"] = lz._usage_channel_label(channel_id)
        usage = session.get("token_usage") or {}
        usage["channel_id"] = channel_id
        usage["channel_label"] = lz._usage_channel_label(channel_id)
        session["token_usage"] = usage

    def _persist_session(self, session):
        if not isinstance(session, dict):
            return
        if self._is_channel_process_session(session):
            self._ensure_session_usage_metadata(session)
            lz.save_session(self.agent_dir, session)
            self._selected_session_id = session.get("id")
            self._refresh_sessions()
            return
        self._bind_session_to_current_bridge(session)
        self._ensure_session_usage_metadata(session)
        lz.save_session(self.agent_dir, session)
        self._selected_session_id = session.get("id")
        self._enforce_session_archive_limits(
            channel_id=session.get("channel_id"),
            exclude_session_ids={session.get("id")},
            refresh=False,
        )
        self._refresh_sessions()

    def _enforce_session_archive_limits(self, channel_id=None, exclude_session_ids=None, refresh=True):
        if not lz.is_valid_agent_dir(self.agent_dir):
            return 0
        excluded = {str(item).strip() for item in (exclude_session_ids or set()) if str(item or "").strip()}
        if self.current_session and self.current_session.get("id"):
            excluded.add(str(self.current_session.get("id")))
        sessions = []
        for meta in lz.list_sessions(self.agent_dir):
            try:
                data = lz.load_session(self.agent_dir, meta["id"])
            except Exception:
                data = None
            if not data:
                continue
            cid = lz._normalize_usage_channel_id(data.get("channel_id"), "launcher")
            if channel_id and cid != lz._normalize_usage_channel_id(channel_id, "launcher"):
                continue
            data["channel_id"] = cid
            sessions.append(data)
        grouped = {}
        for data in sessions:
            grouped.setdefault(data.get("channel_id") or "launcher", []).append(data)
        removed = 0
        for cid, items in grouped.items():
            limit = self._archive_limit_for_channel(cid)
            if limit <= 0 or len(items) <= limit:
                continue
            keep_ids = set(excluded)
            removable = sorted(
                [item for item in items if str(item.get("id") or "") not in keep_ids and not bool(item.get("pinned", False))],
                key=lambda item: float(item.get("updated_at", 0) or 0),
            )
            overflow = len(items) - limit
            for victim in removable[: max(0, overflow)]:
                lz.archive_session(self.agent_dir, victim.get("id"), victim, reason="auto_limit")
                removed += 1
        if removed and refresh:
            self._refresh_sessions()
        return removed

    def _request_backend_state(self, session_id=None):
        sid = session_id or ((self.current_session or {}).get("id"))
        if not sid or not self._bridge_ready:
            return
        self._state_request_seq += 1
        self._send_cmd({"cmd": "get_state", "session_id": sid, "request_id": self._state_request_seq})

    def _apply_state_to_session(self, session_id, backend_history, agent_history, llm_idx=None, process_pid=None, snapshot_ts=None):
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
        if llm_idx is not None:
            try:
                target["llm_idx"] = int(llm_idx)
            except Exception:
                pass
        if process_pid is not None:
            try:
                target["process_pid"] = int(process_pid)
            except Exception:
                pass
        snapshot = dict(target.get("snapshot") or {})
        snapshot["version"] = int(snapshot.get("version", 1) or 1)
        snapshot["kind"] = "turn_complete"
        snapshot["captured_at"] = float(snapshot_ts or time.time())
        snapshot["turns"] = int(((target.get("token_usage") or {}).get("turns", 0) or 0))
        snapshot["llm_idx"] = int(target.get("llm_idx", 0) or 0)
        snapshot["process_pid"] = int(target.get("process_pid", 0) or 0)
        snapshot["has_backend_history"] = bool(target["backend_history"])
        snapshot["has_agent_history"] = bool(target["agent_history"])
        target["snapshot"] = snapshot
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

    def _current_llm_index(self) -> int:
        for pos, llm in enumerate(self.llms):
            if llm.get("current"):
                try:
                    return int(llm.get("idx", pos) or pos)
                except Exception:
                    return pos
        idx = self.llm_combo.currentIndex()
        if idx >= 0:
            data = self.llm_combo.itemData(idx)
            try:
                return int(data if data is not None else idx)
            except Exception:
                return idx
        return 0

    def _set_status(self, text: str):
        self.status_label.setText(text)
        self._refresh_info_tooltip()

    def _info_tooltip_text(self) -> str:
        parts = []
        for name in ("session_mode_label", "status_label", "session_token_tree_label"):
            lbl = getattr(self, name, None)
            if lbl is None:
                continue
            text = (lbl.text() or "").strip()
            if text:
                parts.append(text)
        return "\n\n".join(parts) or "尚无状态信息"

    def _show_info_tooltip(self):
        btn = getattr(self, "info_btn", None)
        popup = getattr(self, "_info_popup", None)
        if btn is None or popup is None or not btn.isVisible():
            return
        popup.setText(self._info_tooltip_text())
        btn_top_left = btn.mapToGlobal(btn.rect().topLeft())
        screen = btn.screen().availableGeometry() if btn.screen() else None
        if screen is not None:
            left_budget = btn_top_left.x() + btn.width() - (screen.left() + 4)
            popup.setMaximumWidth(max(160, left_budget))
        popup.adjustSize()
        x = btn_top_left.x() + btn.width() - popup.width()
        y = btn_top_left.y() - popup.height() - 6
        if screen is not None:
            x = max(screen.left() + 4, x)
            if y < screen.top() + 4:
                y = btn.mapToGlobal(btn.rect().bottomLeft()).y() + 6
        popup.move(x, y)
        popup.show()
        popup.raise_()

    def _hide_info_tooltip(self):
        popup = getattr(self, "_info_popup", None)
        if popup is not None:
            popup.hide()

    def _refresh_info_tooltip(self):
        popup = getattr(self, "_info_popup", None)
        if popup is None or not popup.isVisible():
            return
        popup.setText(self._info_tooltip_text())
        popup.adjustSize()

    def eventFilter(self, watched, event):
        if watched is getattr(self, "info_btn", None):
            et = event.type()
            if et == QEvent.Enter:
                self._show_info_tooltip()
            elif et == QEvent.Leave:
                self._hide_info_tooltip()
            elif et == QEvent.ToolTip:
                return True
        return super().eventFilter(watched, event)

    def _message_row_insert_index(self) -> int:
        return max(0, self.msg_layout.count() - 1)

    def _clear_messages(self):
        self._stream_row = None
        self._current_stream_text = ""
        self._pending_stream_text = None
        self._rendered_message_rows = []
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
        self._refresh_composer_enabled()

    def _add_message_row(self, role: str, text: str, finished: bool = True):
        on_resend = self._regenerate_from_row if role == "assistant" else None
        row = MessageRow(text, role, self.msg_root, on_resend=on_resend)
        row.set_finished(finished)
        self.msg_layout.insertWidget(self._message_row_insert_index(), row)
        self._rendered_message_rows.append(row)
        self._scroll_to_bottom()
        return row

    def _regenerate_from_row(self, row):
        if getattr(self, "_busy", False):
            return
        try:
            idx = self._rendered_message_rows.index(row)
        except ValueError:
            return
        user_text = None
        for j in range(idx - 1, -1, -1):
            prev = self._rendered_message_rows[j]
            if getattr(prev, "_role", "") == "user":
                user_text = prev._text
                break
        if not user_text:
            return
        self.input_box.setPlainText(user_text)
        self._handle_send()

    def _render_session(self, session):
        self._clear_messages()
        if not session:
            self._reset_chat_area("选择一个会话，或新建会话开始聊天。")
            return
        bubbles = list(session.get("bubbles") or [])
        if not bubbles:
            self._reset_chat_area("当前会话还没有消息。")
            return
        events = (session.get("token_usage") or {}).get("events") or []
        assistant_idx = 0
        for bubble in bubbles:
            role = bubble.get("role", "assistant")
            self._add_message_row(role, bubble.get("text", ""), finished=True)
            if role == "assistant":
                assistant_idx += 1
        self._update_header_labels()
        self._refresh_token_label()
        self._scroll_to_bottom(force=True)

    def _jump_to_bubble(self, bubble_index: int):
        try:
            idx = int(bubble_index)
        except Exception:
            return
        if idx < 0 or idx >= len(self._rendered_message_rows):
            return
        target = self._rendered_message_rows[idx]
        if target is None:
            return
        self.scroll.ensureWidgetVisible(target, 0, 24)

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
        self.mode_label.setText(" | ".join(parts))
        self._refresh_session_mode_label()

    def _refresh_token_label(self):
        try:
            self._refresh_token_label_impl()
        finally:
            self._refresh_info_tooltip()

    def _refresh_token_label_impl(self):
        session = self.current_session
        if not isinstance(session, dict):
            self.session_token_tree_label.setText("↑0  ↓0")
            self.session_token_tree_label.hide()
            return
        self._ensure_session_usage_metadata(session)
        summary = self._single_turn_token_summary(include_live=True)
        if summary["input_tokens"] == 0 and summary["output_tokens"] == 0 and summary["live_output_tokens"] == 0:
            self.session_token_tree_label.setText("↑0  ↓0")
            self.session_token_tree_label.hide()
            return
        output_tokens = int(summary["output_tokens"] or 0)
        if summary["live_output_tokens"] > 0:
            output_tokens = int(summary["live_output_tokens"] or 0)
        suffix = " …" if summary["live_output_tokens"] > 0 else ""
        self.session_token_tree_label.setText(
            f"↑{int(summary['input_tokens'] or 0)}  ↓{output_tokens}{suffix}"
        )
        self.session_token_tree_label.show()

    def _single_turn_token_summary(self, include_live: bool = False):
        session = self.current_session or {}
        usage = session.get("token_usage") or {}
        events = list(usage.get("events") or [])
        target = None
        if self._active_token_event_ts is not None:
            for ev in reversed(events):
                try:
                    if float(ev.get("ts", 0) or 0) == float(self._active_token_event_ts):
                        target = ev
                        break
                except Exception:
                    continue
        if target is None:
            for ev in reversed(events):
                if int(ev.get("input_tokens", 0) or 0) > 0:
                    target = ev
                    break
        if target is None and events:
            target = events[-1]

        input_tokens = int((target or {}).get("input_tokens", 0) or 0)
        output_tokens = int((target or {}).get("output_tokens", 0) or 0)
        live_output_tokens = 0
        if include_live and self._busy and self._current_stream_text:
            live_output_tokens = lz._estimate_tokens(self._current_stream_text)
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "live_output_tokens": live_output_tokens,
        }

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
        try:
            self._refresh_session_mode_label_impl()
        finally:
            self._refresh_info_tooltip()

    def _refresh_session_mode_label_impl(self):
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

    def _is_channel_process_session(self, session=None):
        data = session if isinstance(session, dict) else (self.current_session or {})
        return str((data or {}).get("session_kind") or "").strip().lower() == "channel_process"

    def _refresh_composer_enabled(self):
        disabled = self._is_channel_process_session()
        input_box = getattr(self, "input_box", None)
        send_btn = getattr(self, "send_btn", None)
        stop_btn = getattr(self, "stop_btn", None)
        llm_combo = getattr(self, "llm_combo", None)
        if input_box is not None:
            input_box.setReadOnly(disabled)
            input_box.setPlaceholderText(
                "渠道进程会话仅用于回顾日志与快照，不能在这里继续发送消息"
                if disabled else
                "输入消息，Enter 发送，Shift+Enter 换行"
            )
        if send_btn is not None:
            send_btn.setEnabled((not disabled) and (not self._busy))
        if stop_btn is not None:
            stop_btn.setEnabled((not disabled) and self._busy and (not self._abort_requested))
        if llm_combo is not None:
            llm_combo.setEnabled((not disabled) and bool(self.llms))

    def _active_sessions_for_channel(self, channel_id: str):
        cid = lz._normalize_usage_channel_id(channel_id, "launcher")
        out = []
        if not lz.is_valid_agent_dir(self.agent_dir):
            return out
        for meta in lz.list_sessions(self.agent_dir):
            try:
                data = lz.load_session(self.agent_dir, meta["id"])
            except Exception:
                data = None
            if not data:
                continue
            if lz._normalize_usage_channel_id(data.get("channel_id"), "launcher") != cid:
                continue
            out.append(data)
        return out

    def _can_create_session_for_channel(self, channel_id: str, show_message: bool = True) -> bool:
        cid = lz._normalize_usage_channel_id(channel_id, "launcher")
        limit = self._archive_limit_for_channel(cid)
        if limit <= 0:
            return True
        sessions = self._active_sessions_for_channel(cid)
        active_count = len(sessions)
        pinned_count = sum(1 for item in sessions if bool(item.get("pinned", False)))
        if active_count >= limit and pinned_count >= limit:
            if show_message:
                QMessageBox.information(
                    self,
                    "无法新建会话",
                    f"{lz._usage_channel_label(cid)} 的会话上限是 {limit}，而当前可用名额都已经被收藏会话占满。\n\n请先取消部分收藏、提高上限，或删除旧会话。",
                )
            return False
        return True

    def _sidebar_switch_to_channels(self):
        self._sidebar_view_mode = "channels"
        self._last_session_list_signature = None
        self._refresh_sessions()

    def _sidebar_open_channel(self, channel_id: str):
        self._sidebar_view_mode = "sessions"
        self._sidebar_channel_id = lz._normalize_usage_channel_id(channel_id, "launcher")
        self._last_session_list_signature = None
        self._refresh_sessions()

    def _sidebar_channel_rows(self):
        stats = self._collect_archive_stats()
        rows = []
        for cid in self._archive_known_channel_ids():
            active_count = int(stats["active"].get(cid, 0) or 0)
            rows.append(
                {
                    "kind": "channel",
                    "channel_id": cid,
                    "channel_label": lz._usage_channel_label(cid),
                    "active_count": active_count,
                    "total_count": active_count,
                }
            )
        rows.sort(key=lambda row: (0 if row["channel_id"] == "launcher" else 1, -row["total_count"], row["channel_label"]))
        return rows

    def _sidebar_session_rows(self, channel_id: str):
        cid = lz._normalize_usage_channel_id(channel_id, "launcher")
        rows = []
        for meta in lz.list_sessions(self.agent_dir):
            try:
                data = lz.load_session(self.agent_dir, meta["id"])
            except Exception:
                data = None
            if not data:
                continue
            if lz._normalize_usage_channel_id(data.get("channel_id"), "launcher") != cid:
                continue
            rows.append(
                {
                    "kind": "session",
                    "id": data.get("id") or meta.get("id"),
                    "title": data.get("title") or "(未命名)",
                    "updated_at": float(data.get("updated_at", 0) or 0),
                    "pinned": bool(data.get("pinned", False)),
                    "channel_id": cid,
                    "channel_label": lz._usage_channel_label(cid),
                    "path": meta.get("path"),
                }
            )
        rows.sort(key=lambda row: (0 if row.get("pinned") else 1, -(row.get("updated_at", 0) or 0)))
        return rows

    def _sidebar_row_matches_keyword(self, row, keyword: str):
        kw = str(keyword or "").strip().lower()
        if not kw:
            return True
        haystack = " ".join(
            str(row.get(key) or "")
            for key in ("title", "channel_label", "channel_id")
        ).lower()
        return kw in haystack

    def _session_list_signature(self, items, keyword: str):
        return (
            self._sidebar_view_mode,
            self._sidebar_channel_id,
            self._selected_session_id or ((self.current_session or {}).get("id")),
            keyword,
            tuple(
                tuple(
                    row.get(key)
                    for key in ("kind", "id", "channel_id", "title", "updated_at", "pinned", "active_count")
                )
                for row in items
            ),
        )

    def _sidebar_item_text(self, row):
        if row.get("kind") == "channel":
            label = row.get("channel_label") or row.get("channel_id") or "未知渠道"
            if self.sidebar_collapsed:
                return (label[:1] or "•").upper()
            return f"{label}\n会话 {row.get('active_count', 0)}"
        if row.get("kind") == "back":
            return "← 返回渠道"
        if row.get("kind") == "session":
            title = str(row.get("title") or "(未命名)").strip() or "(未命名)"
            title_prefix = "★ " if row.get("pinned") else ""
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(row.get("updated_at", 0) or 0))
            if self.sidebar_collapsed:
                return (title[:1] or "•").upper()
            return f"{title_prefix}{title}\n{when}"
        return ""

    def _refresh_sessions(self):
        if not lz.is_valid_agent_dir(self.agent_dir):
            self._ignore_session_select = True
            if hasattr(self, "session_list") and self.session_list is not None:
                self.session_list.clear()
            self._ignore_session_select = False
            self._last_session_list_signature = None
            return
        keyword = str(getattr(self, "_session_filter_keyword", "") or "").strip().lower()
        if self._sidebar_view_mode == "sessions":
            rows = [row for row in self._sidebar_session_rows(self._sidebar_channel_id) if self._sidebar_row_matches_keyword(row, keyword)]
        else:
            rows = [row for row in self._sidebar_channel_rows() if self._sidebar_row_matches_keyword(row, keyword)]
        signature = self._session_list_signature(rows, keyword)
        if signature == getattr(self, "_last_session_list_signature", None):
            return
        wanted = self._selected_session_id or ((self.current_session or {}).get("id"))
        self._ignore_session_select = True
        self.session_list.clear()
        if getattr(self, "sidebar_group_label", None) is not None:
            if self._sidebar_view_mode == "sessions":
                self.sidebar_group_label.setText(lz._usage_channel_label(self._sidebar_channel_id))
            else:
                self.sidebar_group_label.setText("渠道")
        if self._sidebar_view_mode == "sessions":
            back_item = QListWidgetItem("← 返回渠道" if not self.sidebar_collapsed else "←")
            back_item.setData(Qt.UserRole, {"kind": "back"})
            self.session_list.addItem(back_item)
        for row in rows:
            item = QListWidgetItem(self._sidebar_item_text(row))
            item.setData(Qt.UserRole, row)
            tip = self._sidebar_item_text(row)
            if row.get("kind") == "channel":
                tip = f"{row.get('channel_label')}\n会话 {row.get('active_count', 0)}"
            item.setToolTip(tip)
            if self.sidebar_collapsed:
                item.setTextAlignment(Qt.AlignCenter)
            self.session_list.addItem(item)
            if row.get("kind") == "session" and wanted and row.get("id") == wanted:
                self.session_list.setCurrentItem(item)
        if self.session_list.count() == (1 if self._sidebar_view_mode == "sessions" else 0):
            empty = QListWidgetItem("当前分类还没有会话")
            empty.setFlags(Qt.NoItemFlags)
            self.session_list.addItem(empty)
        self._ignore_session_select = False
        self._last_session_list_signature = signature

    def _on_session_item_changed(self, current, previous):
        if self._ignore_session_select or current is None:
            return
        data = current.data(Qt.UserRole)
        if not isinstance(data, dict):
            return
        kind = data.get("kind")
        if kind == "channel":
            self._sidebar_open_channel(data.get("channel_id") or "launcher")
            return
        if kind == "back":
            self._sidebar_switch_to_channels()
            return
        sid = data.get("id")
        if not sid:
            return
        self._load_session_by_id(sid)

    def _sidebar_selected_session_rows(self):
        rows = []
        for item in self.session_list.selectedItems():
            data = item.data(Qt.UserRole)
            if isinstance(data, dict) and data.get("kind") == "session":
                rows.append(dict(data))
        return rows

    def _open_session_list_context_menu(self, pos):
        if self._sidebar_view_mode != "sessions":
            return
        item = self.session_list.itemAt(pos)
        data = item.data(Qt.UserRole) if item is not None else None
        if not isinstance(data, dict) or data.get("kind") != "session":
            return
        if not item.isSelected():
            self.session_list.clearSelection()
            item.setSelected(True)
        rows = self._sidebar_selected_session_rows()
        if not rows:
            return
        count = len(rows)
        all_pinned = all(bool(row.get("pinned", False)) for row in rows)
        menu = QMenu(self)
        pin_action = menu.addAction(f"{'取消收藏' if all_pinned else '收藏'}所选 ({count})")
        delete_action = menu.addAction(f"删除所选 ({count})")
        chosen = menu.exec(self.session_list.viewport().mapToGlobal(pos))
        if chosen is pin_action:
            self._set_sidebar_sessions_pinned(rows, not all_pinned)
            return
        if chosen is delete_action:
            self._delete_sidebar_sessions(rows)

    def _load_sidebar_session_row(self, row):
        return lz.load_session(self.agent_dir, row.get("id"))

    def _save_sidebar_session_row(self, row, data, *, touch=True):
        lz.save_session(self.agent_dir, data, touch=touch)

    def _set_sidebar_sessions_pinned(self, rows, pinned: bool):
        for row in rows:
            data = self._load_sidebar_session_row(row)
            if not data:
                continue
            data["pinned"] = bool(pinned)
            self._save_sidebar_session_row(row, data, touch=True)
        self._last_session_list_signature = None
        self._refresh_sessions()

    def _clear_current_context_after_session_removed(self, status_text: str):
        self._pending_state_session = None
        self.current_session = None
        self._selected_session_id = None
        self._set_status(status_text)
        self._reset_chat_area("选择一个会话，或新建会话开始聊天。")
        self._restart_bridge()
        self._refresh_composer_enabled()

    def _delete_sidebar_sessions(self, rows):
        if not rows:
            return
        count = len(rows)
        if QMessageBox.question(self, "删除会话", f"确定删除选中的 {count} 个会话？") != QMessageBox.Yes:
            return
        current_sid = str((self.current_session or {}).get("id") or "")
        if self._busy and any(str(row.get("id") or "") == current_sid for row in rows):
            QMessageBox.information(self, "忙碌中", "当前活动会话还在生成，暂时不能删除。")
            return
        deleted_current = False
        for row in rows:
            sid = str(row.get("id") or "")
            if not sid:
                continue
            lz.delete_session(self.agent_dir, sid)
            if sid == current_sid:
                deleted_current = True
        if deleted_current:
            self._clear_current_context_after_session_removed("当前会话已删除。")
        self._last_session_list_signature = None
        self._refresh_sessions()

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
        self.current_session = data
        self._render_session(self.current_session)
        self._refresh_composer_enabled()
        if self._is_channel_process_session(self.current_session):
            self._set_status("已载入渠道进程快照。该会话仅用于回顾，不能在这里继续发送消息。")
            return
        self._bind_session_to_current_bridge(self.current_session)
        if self._bridge_ready:
            self._send_cmd(
                {
                    "cmd": "set_state",
                    "backend_history": data.get("backend_history") or [],
                    "agent_history": data.get("agent_history") or [],
                    "llm_idx": data.get("llm_idx", ((data.get("snapshot") or {}).get("llm_idx"))),
                }
            )
            self._request_backend_state(sid)
            self._set_status("已载入本地会话。")
        else:
            self._pending_state_session = _session_copy(data)
            self._set_status("桥接进程启动中，稍后载入会话…")

    def _delete_selected_session(self):
        rows = self._sidebar_selected_session_rows()
        if not rows:
            return
        self._delete_sidebar_sessions(rows)

    def _new_session(self):
        if self._busy:
            QMessageBox.information(self, "忙碌中", "当前还在生成，请先等待结束或手动中断。")
            return
        if not self._can_create_session_for_channel("launcher", show_message=True):
            return
        self._pending_state_session = None
        self.current_session = None
        self._selected_session_id = None
        self._sidebar_view_mode = "sessions"
        self._sidebar_channel_id = "launcher"
        self._set_status("正在创建新会话进程…")
        self._reset_chat_area("新进程已准备，发送第一条消息后会创建会话。")
        self._restart_bridge()
        self._refresh_composer_enabled()
        self._last_session_list_signature = None
        self._refresh_sessions()

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
            "session_source_label": "启动器",
            "channel_id": "launcher",
            "channel_label": lz._usage_channel_label("launcher"),
            "backend_history": [],
            "agent_history": [],
            "llm_idx": int(self._current_llm_index() or 0),
            "snapshot": {
                "version": 1,
                "kind": "turn_complete",
                "captured_at": time.time(),
                "turns": 0,
                "llm_idx": int(self._current_llm_index() or 0),
                "process_pid": int(getattr(self.bridge_proc, "pid", 0) or 0),
                "has_backend_history": False,
                "has_agent_history": False,
            },
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
        py, py_err = lz._find_compatible_system_python(self.agent_dir)
        if not py:
            raise RuntimeError(py_err or "未找到可用的系统 Python。")
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
        if self._is_channel_process_session():
            QMessageBox.information(self, "不可发送", "当前选中的是渠道进程会话，只能回顾快照，不能从这里继续发送消息。")
            return
        self._last_activity = time.time()
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
        self._refresh_composer_enabled()

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
        self._update_stream_row_tokens(live=True)
        try:
            self._send_cmd({"cmd": "send", "text": text, "session_id": self.current_session.get("id")})
        except Exception as e:
            self._busy = False
            self.send_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            QMessageBox.critical(self, "发送失败", str(e))

    def _stream_update(self, cumulative_text: str):
        self._pending_stream_text = cumulative_text or ""
        self._current_stream_text = cumulative_text or ""
        self._refresh_token_label()
        self._update_stream_row_tokens(live=True)
        if not self._stream_flush_timer.isActive():
            self._stream_flush_timer.start(70)

    def _update_stream_row_tokens(self, *, live: bool):
        return

    def _flush_stream_render(self):
        if self._stream_row is None:
            return
        pending = self._pending_stream_text or ""
        if getattr(self._stream_row, "_text", "") == pending and getattr(self._stream_row, "_finished", False) is False:
            self._update_stream_row_tokens(live=True)
            return
        self._stream_row.update_content(pending, finished=False)
        self._update_stream_row_tokens(live=True)
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
        finished_row = self._stream_row
        if finished_row is not None:
            finished_row.update_content(final_text or "…", finished=True)
        self._stream_row = None
        self._busy = False
        self._abort_requested = False
        self._current_stream_text = ""
        self._pending_stream_text = None
        self.send_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._set_status("已完成。")
        self._refresh_composer_enabled()

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
            if finished_row is not None:
                finished_row.set_token_info(
                    int(target.get("input_tokens", 0) or 0),
                    int(target.get("output_tokens", 0) or 0),
                    live=False,
                )
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
        self._refresh_composer_enabled()
        try:
            self._send_cmd({"cmd": "abort"})
        except Exception as e:
            QMessageBox.warning(self, "中断失败", str(e))

    def _refresh_session_list(self):
        self._refresh_sessions()
        self._set_status("已刷新会话列表。")

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
        if et == "launcher_autonomous_trigger":
            if not self._busy:
                self._send(text=self.AUTO_TASK_TEXT, auto=True)
            return
        if et == "clone_status":
            msg = str(ev.get("msg") or "").strip()
            self.download_status_label.setText(msg)
            self._append_download_log(msg)
            return
        if et == "clone_done":
            self._download_running = False
            self._download_mode = ""
            target = str(ev.get("target") or "").strip()
            python_exe = str(ev.get("python_exe") or "").strip()
            private_python = bool(ev.get("private_python"))
            if python_exe:
                self.cfg["python_exe"] = lz._make_config_relative_path(python_exe)
                lz.save_config(self.cfg)
            if target:
                self._set_agent_dir(target)
            self._refresh_download_state()
            if private_python and python_exe:
                self.download_status_label.setText("下载完成，已配置私有 3.12 虚拟环境并设置为当前 GenericAgent 目录。现在可以直接进入聊天。")
                self._append_download_log(f"[{datetime.now().strftime('%H:%M:%S')}] 私有 3.12 虚拟环境已就绪：{python_exe}")
            else:
                self.download_status_label.setText("下载完成，已设置为当前 GenericAgent 目录。现在可以直接进入聊天。")
            self._append_download_log(f"[{datetime.now().strftime('%H:%M:%S')}] 下载完成")
            return
        if et == "clone_error":
            self._download_running = False
            self._download_mode = ""
            self._refresh_download_state()
            msg = str(ev.get("msg") or "下载失败").strip()
            self.download_status_label.setText(f"下载失败：{msg}")
            self._append_download_log(f"[{datetime.now().strftime('%H:%M:%S')}] 下载失败：{msg}")
            QMessageBox.warning(self, "下载失败", msg)
            return
        if et == "ready":
            self._bridge_ready = True
            self.llms = ev.get("llms", [])
            self._sync_llm_combo()
            self._set_status("桥接进程已就绪。")
            if self._pending_state_session:
                data = _session_copy(self._pending_state_session)
                self.current_session = data
                self._render_session(data)
                self._send_cmd(
                    {
                        "cmd": "set_state",
                        "backend_history": data.get("backend_history") or [],
                        "agent_history": data.get("agent_history") or [],
                        "llm_idx": data.get("llm_idx", ((data.get("snapshot") or {}).get("llm_idx"))),
                    }
                )
                self._request_backend_state(data.get("id"))
                self._pending_state_session = None
            self._refresh_composer_enabled()
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
                llm_idx=ev.get("llm_idx"),
            )
            return
        if et == "turn_snapshot":
            self._apply_state_to_session(
                ev.get("session_id") or ((self.current_session or {}).get("id")),
                ev.get("backend_history") or [],
                ev.get("agent_history") or [],
                llm_idx=ev.get("llm_idx"),
                process_pid=ev.get("process_pid"),
                snapshot_ts=ev.get("snapshot_ts"),
            )
            return
        if et == "llm_switched":
            self.llms = ev.get("llms", self.llms)
            self._sync_llm_combo()
            if self.current_session:
                self.current_session["llm_idx"] = int(self._current_llm_index() or 0)
                self._persist_session(self.current_session)
            self._set_status("模型已切换。")
            self._refresh_composer_enabled()
            return
        if et == "tools_reinjected":
            self._set_status("已重新注入工具示范。")
            return
        if et == "pet_launched":
            self._set_status("已启动桌面宠物。")
            return
        if et == "error":
            msg = ev.get("msg", "")
            self._busy = False
            self.send_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self._set_status(f"错误: {msg}")
            self._refresh_composer_enabled()
            QMessageBox.warning(self, "桥接错误", msg or "未知错误")

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
    global _MD_CSS
    _MD_CSS = _build_md_css()
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
