from __future__ import annotations

import json
import os
import re
import subprocess

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QCursor, QIcon, QImage, QKeyEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from launcher_app import core as lz
from launcher_app.theme import C

PRIVATE_PYTHON_VERSION = "3.12.10"

_SVG_COPY = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>'
_SVG_BOT = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/><path d="M5 3v4"/><path d="M7 5H3"/></svg>'
_SVG_USER = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>'
_SVG_PLUS = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" x2="12" y1="5" y2="19"/><line x1="5" x2="19" y1="12" y2="12"/></svg>'
_SVG_REFRESH = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>'
_SVG_TRASH = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c0-1-1-2-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/></svg>'
_SVG_SEND = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13"/><path d="m22 2-7 20-4-9-9-4Z"/></svg>'
_SVG_STOP = '<svg viewBox="0 0 24 24" fill="{c}" stroke="none"><rect width="10" height="10" x="7" y="7" rx="1.5" ry="1.5"/></svg>'
_SVG_INFO = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>'
_SVG_CHEVRON_DOWN = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>'

_ICON_CACHE: dict[str, QIcon] = {}
_MD_CSS = ""
_HTML_STYLE_ATTR_RE = re.compile(r"\s(?:style|bgcolor|color|face|size)\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
_HTML_FONT_OPEN_RE = re.compile(r"<\s*font\b[^>]*>", re.IGNORECASE)
_HTML_FONT_CLOSE_RE = re.compile(r"<\s*/\s*font\s*>", re.IGNORECASE)


def _build_md_css() -> str:
    return f"""
body {{ color: {C['text']} !important; background: transparent !important; font-family: "Arial", "Microsoft YaHei UI", "Segoe UI", sans-serif; font-size: 13px; line-height: 1.6; font-weight: 400; }}
div, p, li, span, strong, em, b, i {{ color: {C['text']} !important; background: transparent !important; }}
h1 {{ color: {C['text']}; font-size: 20px; font-weight: 700; border-bottom: 1px solid {C['border']}; padding-bottom: 4px; margin-top: 16px; }}
h2 {{ color: {C['text']}; font-size: 17px; font-weight: 700; border-bottom: 1px solid {C['border']}; padding-bottom: 3px; margin-top: 14px; }}
h3 {{ color: {C['text']}; font-size: 15px; font-weight: 600; margin-top: 12px; }}
h4, h5, h6 {{ color: {C['text_soft']}; font-size: 13px; font-weight: 600; margin-top: 10px; }}
code {{ background: {C['field_alt']} !important; color: {C['code_text']} !important; padding: 1px 4px; border-radius: 3px; font-family: Consolas, "Courier New", monospace; font-size: 12px; }}
pre {{ background: {C['field_alt']} !important; color: {C['code_text']} !important; padding: 12px; border-radius: 8px; overflow-x: auto; border: 1px solid {C['stroke_default']} !important; margin: 8px 0; white-space: pre; font-family: Consolas, "Cascadia Mono", "Courier New", monospace; font-size: 12px; line-height: 1.45; }}
pre code {{ background: transparent; padding: 0; border-radius: 0; white-space: pre; }}
blockquote {{ border-left: 3px solid {C['accent']}; margin: 8px 0; padding: 6px 10px; color: {C['text_soft']} !important; background: {C['layer1']} !important; }}
a {{ color: {C['accent']}; text-decoration: none; }}
ul, ol {{ margin: 6px 0 8px 18px; }}
li {{ margin: 4px 0; }}
hr {{ border: none; border-top: 1px solid {C['stroke_default']}; margin: 12px 0; }}
p {{ margin: 6px 0; word-break: break-word; }}
table {{ border-collapse: collapse; margin: 10px 0; width: auto; font-size: 12px; background: transparent !important; }}
th, td {{ border: 1px solid {C['stroke_default']} !important; padding: 7px 10px; text-align: left; vertical-align: top; white-space: nowrap; color: {C['text']} !important; }}
th {{ background: {C['layer2']} !important; color: {C['text']} !important; font-weight: 600; }}
tr:nth-child(even) td {{ background: {C['layer1']} !important; }}
tr:nth-child(odd) td {{ background: transparent !important; }}
"""


def set_md_css(css: str) -> None:
    global _MD_CSS
    _MD_CSS = css or ""


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
        result = lz._run_external_subprocess(
            ["git", "--version"],
            capture_output=True,
            text=True,
            timeout=8,
            encoding="utf-8",
            errors="replace",
            creationflags=lz._python_creationflags(),
        )
        if result.returncode == 0:
            out["git_ok"] = True
            out["git_text"] = (result.stdout or result.stderr or "Git").strip()
    except Exception:
        pass
    py = lz._find_system_python()
    if py:
        out["python_ok"] = True
        try:
            version = lz._run_external_subprocess(
                [py, "-c", "import sys;print(sys.version.split()[0])"],
                capture_output=True,
                text=True,
                timeout=8,
                encoding="utf-8",
                errors="replace",
                creationflags=lz._python_creationflags(),
            )
            ver = (version.stdout or "").strip()
        except Exception:
            ver = ""
        label = py
        if ver:
            label = f"{py} (Python {ver})"
        out["python_text"] = label
    try:
        if py:
            rr = lz._run_external_subprocess(
                [py, "-c", "import requests;print(requests.__version__)"],
                capture_output=True,
                text=True,
                timeout=8,
                encoding="utf-8",
                errors="replace",
                creationflags=lz._python_creationflags(),
            )
            if rr.returncode == 0:
                out["requests_ok"] = True
                ver = (rr.stdout or "").strip()
                out["requests_text"] = f"requests 已安装：{ver}" if ver else "requests 已安装"
            else:
                detail = ((rr.stderr or "") + "\n" + (rr.stdout or "")).lower()
                if "jsondecodeerror" in detail and "simplejson" in detail:
                    out["requests_text"] = "检测到 simplejson 版本过低；首次运行时会自动升级到最新版 simplejson / requests"
                else:
                    out["requests_text"] = "未检测到 requests（首次运行时会自动安装最新版 requests / simplejson）"
        else:
            out["requests_text"] = "无法检查 requests（未找到可用 Python）"
    except Exception:
        out["requests_text"] = "无法检查 requests（按本机 Python 扫描；首次启动时会尝试自动补最新版 requests / simplejson）"
    return out


def _md_to_html(text: str) -> str:
    def _sanitize_render_html(raw_html: str) -> str:
        html = str(raw_html or "")
        if not html:
            return html
        html = _HTML_FONT_OPEN_RE.sub("<span>", html)
        html = _HTML_FONT_CLOSE_RE.sub("</span>", html)
        prev = None
        while prev != html:
            prev = html
            html = _HTML_STYLE_ATTR_RE.sub("", html)
        return html

    try:
        import markdown

        html = markdown.markdown(
            text or "",
            extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
        )
        return _sanitize_render_html(html)
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
    return _sanitize_render_html("\n".join(html))


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
    has_wide_content = bool(browser.property("_hasWideContent"))
    width_key = width
    old_width = int(browser.property("_fitWidth") or 0)
    old_height = int(browser.property("_fitHeight") or 0)
    if old_width == width_key and old_height > 0 and not browser.property("_fitForce"):
        return
    doc.setTextWidth(width)
    new_h = max(38, int(doc.size().height() + 10))
    if browser.property("streamingHold"):
        current_h = browser.height()
        if new_h < current_h:
            new_h = current_h
    try:
        hbar = browser.horizontalScrollBar()
        if hbar is not None and hbar.isVisible():
            new_h += int(hbar.sizeHint().height() or 0) + 4
    except Exception:
        pass
    browser.setProperty("_fitWidth", width_key)
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
    def __init__(self, submit_cb, image_cb=None, parent=None):
        super().__init__(parent)
        self._submit_cb = submit_cb
        self._image_cb = image_cb
        self.setAcceptDrops(True)

    def _image_extensions(self):
        return {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".bmp",
            ".webp",
            ".tif",
            ".tiff",
            ".ico",
        }

    def _mime_to_image_attachments(self, mime):
        attachments = []
        if mime is None:
            return attachments
        if mime.hasUrls():
            for url in mime.urls():
                if not url.isLocalFile():
                    continue
                path = url.toLocalFile()
                if not path or not os.path.isfile(path):
                    continue
                if os.path.splitext(path)[1].lower() in self._image_extensions():
                    attachments.append(
                        {
                            "kind": "path",
                            "path": path,
                            "name": os.path.basename(path),
                        }
                    )
        if attachments:
            return attachments
        if mime.hasImage():
            img = mime.imageData()
            qimage = None
            if isinstance(img, QImage):
                qimage = img
            elif isinstance(img, QPixmap):
                qimage = img.toImage()
            elif hasattr(img, "toImage"):
                try:
                    qimage = img.toImage()
                except Exception:
                    qimage = None
            if qimage is not None and not qimage.isNull():
                attachments.append(
                    {
                        "kind": "image",
                        "name": "clipboard.png",
                        "image": qimage,
                    }
                )
        return attachments

    def _dispatch_image_attachments(self, mime):
        attachments = self._mime_to_image_attachments(mime)
        if not attachments:
            return False
        if callable(self._image_cb):
            self._image_cb(attachments)
        return True

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if (
            event.key() in (Qt.Key_Return, Qt.Key_Enter)
            and not (event.modifiers() & Qt.ShiftModifier)
        ):
            self._submit_cb()
            return
        super().keyPressEvent(event)

    def canInsertFromMimeData(self, source) -> bool:
        if self._mime_to_image_attachments(source):
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source) -> None:
        if self._dispatch_image_attachments(source):
            return
        super().insertFromMimeData(source)

    def dragEnterEvent(self, event) -> None:
        if self._mime_to_image_attachments(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if self._mime_to_image_attachments(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if self._dispatch_image_attachments(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dropEvent(event)


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
        self._body.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard | Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard
        )
        self._body.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
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
        self._body.setProperty("_markdownText", self._text)
        html = _md_to_html(self._text)
        html_lower = html.lower()
        has_wide = ("<table" in html_lower) or ("<pre" in html_lower)
        self._body.setProperty("_hasWideContent", has_wide)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded if has_wide else Qt.ScrollBarAlwaysOff)
        self._body.setSizePolicy(QSizePolicy.Ignored if has_wide else QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._body.setHtml(html)
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
            copy_btn.setIcon(_svg_icon("msg_copy", _SVG_COPY, color=C["muted"], size=14))
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
                regen_btn.setIcon(_svg_icon("msg_regen", _SVG_REFRESH, color=C["muted"], size=14))
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
            selected = ""
            for browser in self.findChildren(QTextBrowser):
                cursor = browser.textCursor()
                if cursor is not None and cursor.hasSelection():
                    selected = str(cursor.selectedText() or "").replace("\u2029", "\n").strip()
                    if selected:
                        break
            QApplication.clipboard().setText(selected or (self._text or ""))
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
        browser.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard | Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard
        )
        browser.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        browser.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        browser.document().setDefaultStyleSheet(_MD_CSS)
        browser.setProperty("_markdownText", markdown_text)
        browser.setProperty("streamingHold", bool(streaming))
        browser.setProperty("_fitForce", True)
        html = _md_to_html(markdown_text)
        html_lower = html.lower()
        has_wide = ("<table" in html_lower) or ("<pre" in html_lower)
        browser.setProperty("_hasWideContent", has_wide)
        browser.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded if has_wide else Qt.ScrollBarAlwaysOff)
        browser.setSizePolicy(QSizePolicy.Ignored if has_wide else QSizePolicy.Expanding, QSizePolicy.Minimum)
        browser.setHtml(html)
        _fit_browser_height(browser)
        return browser

    def _set_browser_markdown(self, browser: QTextBrowser, markdown_text: str) -> None:
        if browser.property("_markdownText") == markdown_text:
            return
        browser.setProperty("_markdownText", markdown_text)
        browser.setProperty("_fitForce", True)
        html = _md_to_html(markdown_text)
        html_lower = html.lower()
        has_wide = ("<table" in html_lower) or ("<pre" in html_lower)
        browser.setProperty("_hasWideContent", has_wide)
        browser.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded if has_wide else Qt.ScrollBarAlwaysOff)
        browser.setSizePolicy(QSizePolicy.Ignored if has_wide else QSizePolicy.Expanding, QSizePolicy.Minimum)
        browser.setHtml(html)
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


set_md_css(_build_md_css())
