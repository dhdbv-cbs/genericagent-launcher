from __future__ import annotations

import json
import math
import os
import re

from PySide6.QtCore import QByteArray, QPoint, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QCursor, QIcon, QImage, QKeyEvent, QPainter, QPainterPath, QPalette, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from launcher_app import core as lz
from launcher_app.theme import C, F

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
_SVG_CHEVRON_LEFT = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>'
_SVG_CHEVRON_RIGHT = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg>'
_SVG_SETTINGS = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><line x1="4" x2="20" y1="6" y2="6"/><line x1="4" x2="20" y1="12" y2="12"/><line x1="4" x2="20" y1="18" y2="18"/><circle cx="9" cy="6" r="2"/><circle cx="15" cy="12" r="2"/><circle cx="11" cy="18" r="2"/></svg>'
_SVG_SEARCH = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>'
_SVG_FOLDER = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H10l2 2h6.5A2.5 2.5 0 0 1 21 9.5v7A2.5 2.5 0 0 1 18.5 19h-13A2.5 2.5 0 0 1 3 16.5z"/></svg>'
_SVG_DOWNLOAD = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v10"/><path d="m8 10 4 4 4-4"/><path d="M5 19h14"/></svg>'
_SVG_HOME = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="m3 10 9-7 9 7"/><path d="M5 9.5V20h14V9.5"/></svg>'
_SVG_WINDOW = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="M3 9h18"/><path d="M7 7h.01"/><path d="M10 7h.01"/></svg>'
_SVG_SUN = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2.5"/><path d="M12 19.5V22"/><path d="m4.93 4.93 1.77 1.77"/><path d="m17.3 17.3 1.77 1.77"/><path d="M2 12h2.5"/><path d="M19.5 12H22"/><path d="m4.93 19.07 1.77-1.77"/><path d="m17.3 6.7 1.77-1.77"/></svg>'
_SVG_MOON = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A8.8 8.8 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/></svg>'
_SVG_WRENCH = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 5.5a4 4 0 0 0 4.9 4.9l-8.8 8.8a2 2 0 0 1-2.8-2.8l8.8-8.8a4 4 0 0 0-2.1-7.1Z"/></svg>'
_SVG_SPARKLE = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8Z"/><path d="M19 3v4"/><path d="M21 5h-4"/></svg>'
_SVG_PLAY = '<svg viewBox="0 0 24 24" fill="{c}" stroke="none"><path d="M8 5.5v13l10-6.5Z"/></svg>'
_SVG_PAUSE = '<svg viewBox="0 0 24 24" fill="{c}" stroke="none"><rect x="7" y="5" width="4" height="14" rx="1.5"/><rect x="13" y="5" width="4" height="14" rx="1.5"/></svg>'
_SVG_CLOSE = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 6 12 12"/><path d="M18 6 6 18"/></svg>'
_SVG_KEY = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="8.5" cy="15.5" r="4.5"/><path d="M12 12 21 3"/><path d="M17 7h4v4"/></svg>'
_SVG_MESSAGE = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v7A2.5 2.5 0 0 1 17.5 16H10l-4.5 4v-4H6.5A2.5 2.5 0 0 1 4 13.5z"/></svg>'
_SVG_SERVER = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="6" rx="2"/><rect x="4" y="14" width="16" height="6" rx="2"/><path d="M8 7h.01"/><path d="M8 17h.01"/><path d="M12 7h5"/><path d="M12 17h5"/></svg>'
_SVG_CLOCK = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>'
_SVG_PUZZLE = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 4h3a2 2 0 1 1 4 0h3v5a2 2 0 1 0 0 4v5h-5a2 2 0 1 1-4 0H5v-5a2 2 0 1 0 0-4V4h4"/></svg>'
_SVG_SWATCH = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 4 7v10l8 4 8-4V7Z"/><path d="m4 7 8 4 8-4"/><path d="M12 11v10"/></svg>'
_SVG_RECEIPT = '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h12v18l-2.5-1.5L13 21l-2.5-1.5L8 21 6 19.5z"/><path d="M9 8h6"/><path d="M9 12h6"/><path d="M9 16h4"/></svg>'
_SVG_DOT = '<svg viewBox="0 0 24 24" fill="{c}" stroke="none"><circle cx="12" cy="12" r="5"/></svg>'

_ICON_CACHE: dict[str, QIcon] = {}
_AVATAR_PIXMAP_CACHE: dict[str, QPixmap] = {}
_MD_CSS = ""
_HTML_STYLE_ATTR_RE = re.compile(r"\s(?:style|bgcolor|color|face|size)\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
_HTML_FONT_OPEN_RE = re.compile(r"<\s*font\b[^>]*>", re.IGNORECASE)
_HTML_FONT_CLOSE_RE = re.compile(r"<\s*/\s*font\s*>", re.IGNORECASE)
_LEGACY_REMOTE_AGENT_DIR = "/opt/agant"
_ROOT_REMOTE_AGENT_DIR = "/root/agant"
_HOME_REMOTE_AGENT_DIR_RE = re.compile(r"^/home/[^/\s]+/agant/?$")
_AUTO_DOCKER_NAME_SUFFIX = "（Docker）"
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


class NoWheelComboBox(QComboBox):
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


class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event):
        if event is not None:
            event.ignore()


def remote_agent_dir_default(username: str) -> str:
    user = str(username or "").strip().strip("/")
    if not user or user.lower() == "root":
        return _ROOT_REMOTE_AGENT_DIR
    return f"/home/{user}/agant"


def is_auto_remote_agent_dir(path: str) -> bool:
    value = str(path or "").strip()
    if not value:
        return True
    if value in (_LEGACY_REMOTE_AGENT_DIR, _ROOT_REMOTE_AGENT_DIR):
        return True
    return bool(_HOME_REMOTE_AGENT_DIR_RE.fullmatch(value))


def normalize_remote_agent_dir(path: str, *, username: str = "") -> str:
    value = str(path or "").strip()
    if not value or value == _LEGACY_REMOTE_AGENT_DIR:
        return remote_agent_dir_default(username)
    return value


def strip_auto_docker_name_suffix(value: str) -> str:
    text = str(value or "").strip()
    while text.endswith(_AUTO_DOCKER_NAME_SUFFIX):
        next_text = text[: -len(_AUTO_DOCKER_NAME_SUFFIX)].rstrip()
        if next_text == text:
            break
        text = next_text
    return text


def remote_device_agent_mode(raw, *, default: str = "host") -> str:
    item = raw if isinstance(raw, dict) else {}
    container = str(
        item.get("docker_container")
        or item.get("docker_container_name")
        or item.get("takeover_docker_container")
        or item.get("container_name")
        or ""
    ).strip()
    text = str(item.get("agent_mode") or default or "host").strip().lower()
    if text not in ("host", "docker"):
        text = str(default or "host").strip().lower()
    if text not in ("host", "docker"):
        text = "host"
    if text == "host" and container:
        return "docker"
    if text == "host":
        return "host"
    return "docker" if container else "host"


def remote_device_container_name(raw) -> str:
    item = raw if isinstance(raw, dict) else {}
    if remote_device_agent_mode(item) != "docker":
        return ""
    return str(
        item.get("docker_container")
        or item.get("docker_container_name")
        or item.get("takeover_docker_container")
        or item.get("container_name")
        or ""
    ).strip()


def remote_device_remote_mode(raw, *, default: str = "ssh") -> str:
    item = raw if isinstance(raw, dict) else {}
    text = str(item.get("remote_mode") or "").strip().lower()
    if text == "docker_container":
        return "docker_container"
    if text in ("ssh", "host"):
        return "ssh"
    docker_container = str(
        item.get("docker_container")
        or item.get("docker_container_name")
        or item.get("takeover_docker_container")
        or item.get("container_name")
        or ""
    ).strip()
    docker_agent_dir = str(
        item.get("docker_agent_dir")
        or item.get("takeover_docker_agent_dir")
        or item.get("container_agent_dir")
        or ""
    ).strip()
    agent_mode = str(item.get("agent_mode") or "").strip().lower()
    if docker_container or docker_agent_dir or agent_mode == "docker":
        return "docker_container"
    fallback = str(default or "ssh").strip().lower()
    return "ssh" if fallback not in ("ssh", "docker_container") else fallback


def remote_device_agent_dir(raw, *, username: str = "") -> str:
    item = raw if isinstance(raw, dict) else {}
    if remote_device_agent_mode(item) == "docker":
        value = str(
            item.get("docker_agent_dir")
            or item.get("takeover_docker_agent_dir")
            or item.get("container_agent_dir")
            or item.get("agent_dir")
            or ""
        ).strip()
        return value
    return normalize_remote_agent_dir(item.get("agent_dir") or item.get("remote_dir"), username=username)


def normalize_process_match_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return os.path.normpath(text).replace("\\", "/").lower()


def process_path_aliases(path: str, *, real_path: str = "") -> set[str]:
    aliases: set[str] = set()
    for candidate in (path, real_path):
        raw = str(candidate or "").strip()
        if not raw:
            continue
        norm = normalize_process_match_text(raw)
        if norm:
            aliases.add(norm)
    return aliases


def process_dirs_match(path: str, other: str, *, path_real: str = "", other_real: str = "") -> bool:
    left = process_path_aliases(path, real_path=path_real)
    right = process_path_aliases(other, real_path=other_real)
    return bool(left and right and left.intersection(right))


def process_cmdline_has_script(cmdline: str, script_rel: str) -> bool:
    norm_cmd = normalize_process_match_text(cmdline)
    rel_script = normalize_process_match_text(script_rel)
    script_name = normalize_process_match_text(os.path.basename(str(script_rel or "").strip()))
    module_name = normalize_process_match_text(str(script_rel or "").strip())
    if module_name.endswith(".py"):
        module_name = module_name[:-3]
    module_name = module_name.replace("/", ".").replace("\\", ".")
    module_leaf = normalize_process_match_text(str(module_name.rsplit(".", 1)[-1] if module_name else ""))
    if not norm_cmd:
        return False
    if rel_script and rel_script in norm_cmd:
        return True
    if script_name and re.search(rf"(^|[/\s\"']){re.escape(script_name)}($|[/\s\"'])", norm_cmd):
        return True
    if module_name and re.search(rf"(^|[\s\"']){re.escape(module_name)}($|[\s\"'])", norm_cmd):
        return True
    if module_leaf and re.search(rf"(^|[\s\"']){re.escape(module_leaf)}($|[\s\"'])", norm_cmd):
        return True
    return False


def process_path_looks_absolute(path: str) -> bool:
    text = str(path or "").strip()
    if not text:
        return False
    if os.path.isabs(text):
        return True
    return bool(re.match(r"^[A-Za-z]:[\\/]", text))


def process_cmdline_matches_agent_script(
    cmdline: str,
    *,
    agent_dir: str,
    script_rel: str,
    cwd: str = "",
    agent_dir_real: str = "",
    cwd_real: str = "",
) -> bool:
    norm_cmd = normalize_process_match_text(cmdline)
    norm_script_rel = normalize_process_match_text(script_rel)
    if (not norm_cmd) or (not norm_script_rel):
        return False
    if process_path_looks_absolute(script_rel) and norm_script_rel in norm_cmd:
        return True
    target_script = normalize_process_match_text(os.path.join(str(agent_dir or "").strip(), script_rel))
    if target_script and target_script in norm_cmd:
        return True
    if not process_cmdline_has_script(norm_cmd, norm_script_rel):
        return False
    if process_dirs_match(cwd, agent_dir, path_real=cwd_real, other_real=agent_dir_real):
        return True
    agent_aliases = process_path_aliases(agent_dir, real_path=agent_dir_real)
    return bool(agent_aliases and any(alias in norm_cmd for alias in agent_aliases))


def process_matcher_script_source() -> str:
    return (
        "def normalize_process_match_text(value):\n"
        "    text = str(value or '').strip()\n"
        "    if not text:\n"
        "        return ''\n"
        "    return os.path.normpath(text).replace('\\\\', '/').lower()\n"
        "\n"
        "def process_path_aliases(path, real_path=''):\n"
        "    aliases = set()\n"
        "    for candidate in (path, real_path):\n"
        "        raw = str(candidate or '').strip()\n"
        "        if not raw:\n"
        "            continue\n"
        "        norm = normalize_process_match_text(raw)\n"
        "        if norm:\n"
        "            aliases.add(norm)\n"
        "    return aliases\n"
        "\n"
        "def process_dirs_match(path, other, path_real='', other_real=''):\n"
        "    left = process_path_aliases(path, real_path=path_real)\n"
        "    right = process_path_aliases(other, real_path=other_real)\n"
        "    return bool(left and right and left.intersection(right))\n"
        "\n"
        "def process_cmdline_has_script(cmdline, script_rel):\n"
        "    norm_cmd = normalize_process_match_text(cmdline)\n"
        "    rel_script = normalize_process_match_text(script_rel)\n"
        "    script_name = normalize_process_match_text(os.path.basename(str(script_rel or '').strip()))\n"
        "    module_name = normalize_process_match_text(str(script_rel or '').strip())\n"
        "    if module_name.endswith('.py'):\n"
        "        module_name = module_name[:-3]\n"
        "    module_name = module_name.replace('/', '.').replace('\\\\', '.')\n"
        "    module_leaf = normalize_process_match_text(str(module_name.rsplit('.', 1)[-1] if module_name else ''))\n"
        "    if not norm_cmd:\n"
        "        return False\n"
        "    if rel_script and rel_script in norm_cmd:\n"
        "        return True\n"
        "    if script_name and re.search(r\"(^|[/\\\\s\\\"'])\" + re.escape(script_name) + r\"($|[/\\\\s\\\"'])\", norm_cmd):\n"
        "        return True\n"
        "    if module_name and re.search(r\"(^|[\\\\s\\\"'])\" + re.escape(module_name) + r\"($|[\\\\s\\\"'])\", norm_cmd):\n"
        "        return True\n"
        "    if module_leaf and re.search(r\"(^|[\\\\s\\\"'])\" + re.escape(module_leaf) + r\"($|[\\\\s\\\"'])\", norm_cmd):\n"
        "        return True\n"
        "    return False\n"
        "\n"
        "def process_path_looks_absolute(path):\n"
        "    text = str(path or '').strip()\n"
        "    if not text:\n"
        "        return False\n"
        "    if os.path.isabs(text):\n"
        "        return True\n"
        "    return bool(re.match(r\"^[A-Za-z]:[\\\\/]\", text))\n"
        "\n"
        "def process_cmdline_matches_agent_script(cmdline, agent_dir, script_rel, cwd='', agent_dir_real='', cwd_real=''):\n"
        "    norm_cmd = normalize_process_match_text(cmdline)\n"
        "    norm_script_rel = normalize_process_match_text(script_rel)\n"
        "    if (not norm_cmd) or (not norm_script_rel):\n"
        "        return False\n"
        "    if process_path_looks_absolute(script_rel) and norm_script_rel in norm_cmd:\n"
        "        return True\n"
        "    target_script = normalize_process_match_text(os.path.join(str(agent_dir or '').strip(), script_rel))\n"
        "    if target_script and target_script in norm_cmd:\n"
        "        return True\n"
        "    if not process_cmdline_has_script(norm_cmd, norm_script_rel):\n"
        "        return False\n"
        "    if process_dirs_match(cwd, agent_dir, path_real=cwd_real, other_real=agent_dir_real):\n"
        "        return True\n"
        "    agent_aliases = process_path_aliases(agent_dir, real_path=agent_dir_real)\n"
        "    return bool(agent_aliases and any(alias in norm_cmd for alias in agent_aliases))\n"
    )


def looks_like_ssh_disconnect(detail: str) -> bool:
    text = str(detail or "").strip().lower()
    if not text:
        return False
    return any(hint in text for hint in _SSH_DISCONNECT_HINTS)


def normalize_ssh_error_text(detail: str, *, context: str = "SSH 连接") -> str:
    text = str(detail or "").strip()
    lowered = text.lower()
    if (not text) or ("10054" in lowered) or ("reset by peer" in lowered) or ("forcibly closed by the remote host" in lowered):
        return f"{context}已被远端重置，请稍后重试。"
    if ("socket is closed" in lowered) or ("socket closed" in lowered):
        return f"{context}对应的 socket 已关闭，请重新连接。"
    if ("transport is closed" in lowered) or ("transport closed" in lowered):
        return f"{context}传输层已关闭，请重新连接。"
    if "channel closed" in lowered:
        return f"{context}通道已关闭，请重新连接。"
    if "eof during negotiation" in lowered:
        return f"{context}在握手阶段被远端关闭，请检查服务器 SSH 状态后重试。"
    return text or f"{context}失败。"


def runtime_context_generation(host) -> int:
    try:
        return int(getattr(host, "_runtime_context_generation", 0) or 0)
    except Exception:
        return 0


def bump_runtime_context_generation(host) -> int:
    token = runtime_context_generation(host) + 1
    try:
        setattr(host, "_runtime_context_generation", token)
    except Exception:
        pass
    return token


def capture_runtime_context(host, *, include_settings_target: bool = False) -> dict:
    raw_agent_dir = str(getattr(host, "agent_dir", "") or "").strip()
    agent_dir = os.path.abspath(raw_agent_dir) if raw_agent_dir else ""
    target_token = 0
    if include_settings_target:
        getter = getattr(host, "_settings_target_generation", None)
        if callable(getter):
            try:
                target_token = int(getter() or 0)
            except Exception:
                target_token = 0
    return {
        "agent_dir": agent_dir,
        "runtime_generation": runtime_context_generation(host),
        "settings_target_generation": target_token,
    }


def runtime_context_matches(host, context, *, include_settings_target: bool = False) -> bool:
    if bool(getattr(host, "_closing_in_progress", False) or getattr(host, "_force_exit_requested", False)):
        return False
    if not isinstance(context, dict):
        return True
    raw_agent_dir = str(getattr(host, "agent_dir", "") or "").strip()
    current_agent_dir = os.path.abspath(raw_agent_dir) if raw_agent_dir else ""
    expected_agent_dir = str(context.get("agent_dir") or "").strip()
    if os.path.normcase(current_agent_dir) != os.path.normcase(expected_agent_dir):
        return False
    if runtime_context_generation(host) != int(context.get("runtime_generation", 0) or 0):
        return False
    if include_settings_target:
        getter = getattr(host, "_settings_target_generation", None)
        current_target_generation = 0
        if callable(getter):
            try:
                current_target_generation = int(getter() or 0)
            except Exception:
                current_target_generation = 0
        if current_target_generation != int(context.get("settings_target_generation", 0) or 0):
            return False
    return True


def invalidate_runtime_bound_state(
    host,
    *,
    bump_runtime: bool = False,
    bump_settings_target: bool = False,
    clear_remote_sync_queues: bool = False,
) -> None:
    if bump_runtime:
        bump_runtime_context_generation(host)
    if bump_settings_target:
        bumper = getattr(host, "_bump_settings_target_generation", None)
        if callable(bumper):
            try:
                bumper()
            except Exception:
                pass
        else:
            try:
                token = int(getattr(host, "_settings_target_change_token", 0) or 0) + 1
            except Exception:
                token = 1
            try:
                setattr(host, "_settings_target_change_token", token)
            except Exception:
                pass
    for attr, value in (
        ("_qt_api_remote_loading", False),
        ("_qt_channel_remote_loading", False),
        ("_settings_personal_remote_sync_running", False),
        ("_settings_usage_remote_sync_running", False),
        ("_settings_personal_remote_sync_key", ""),
        ("_settings_personal_remote_synced_key", ""),
        ("_settings_usage_remote_sync_key", ""),
        ("_settings_usage_remote_synced_key", ""),
        ("_settings_schedule_remote_reload_token", 0),
    ):
        if hasattr(host, attr):
            try:
                setattr(host, attr, value)
            except Exception:
                pass
    if not clear_remote_sync_queues:
        return
    for attr, value in (
        ("_remote_channel_sync_running", False),
        ("_remote_launcher_sync_running", False),
        ("_remote_launcher_sync_pending_force", False),
        ("_remote_launcher_sync_pending_device_id", ""),
        ("_remote_launcher_sync_pending_refresh", False),
        ("_next_remote_launcher_sync_at", 0.0),
        ("_next_remote_channel_sync_at", 0.0),
    ):
        if hasattr(host, attr):
            try:
                setattr(host, attr, value)
            except Exception:
                pass


def _build_md_css() -> str:
    body_font = str(F.get("font_family") or "sans-serif")
    mono_font = str(F.get("font_family_mono") or "monospace")
    return f"""
body {{ color: {C['text']} !important; background: transparent !important; font-family: {body_font}; font-size: 13px; line-height: 1.6; font-weight: 400; }}
div, p, li, span, strong, em, b, i {{ color: {C['text']} !important; background: transparent !important; }}
h1 {{ color: {C['text']}; font-size: 20px; font-weight: 700; border-bottom: 1px solid {C['border']}; padding-bottom: 4px; margin-top: 16px; }}
h2 {{ color: {C['text']}; font-size: 17px; font-weight: 700; border-bottom: 1px solid {C['border']}; padding-bottom: 3px; margin-top: 14px; }}
h3 {{ color: {C['text']}; font-size: 15px; font-weight: 600; margin-top: 12px; }}
h4, h5, h6 {{ color: {C['text_soft']}; font-size: 13px; font-weight: 600; margin-top: 10px; }}
code {{ background: {C['field_alt']} !important; color: {C['code_text']} !important; padding: 1px 4px; border-radius: 3px; font-family: {mono_font}; font-size: 12px; }}
pre {{ background: {C['field_alt']} !important; color: {C['code_text']} !important; padding: 12px; border-radius: 8px; overflow-x: auto; border: 1px solid {C['stroke_default']} !important; margin: 8px 0; white-space: pre; font-family: {mono_font}; font-size: 12px; line-height: 1.45; }}
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
    resolved_color = _svg_resolve_color(color)
    cache_key = f"{key}_{resolved_color}_{size}"
    if cache_key not in _ICON_CACHE:
        from PySide6.QtSvg import QSvgRenderer

        data = QByteArray(svg_template.format(c=resolved_color).encode("utf-8"))
        renderer = QSvgRenderer(data)
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        _ICON_CACHE[cache_key] = QIcon(pixmap)
    return _ICON_CACHE[cache_key]


def _svg_resolve_color(color: str = "#94a3b8") -> str:
    raw = str(color or "").strip()
    if raw in C:
        return str(C.get(raw) or "#94a3b8")
    return raw or "#94a3b8"


def set_button_svg_icon(button: QPushButton, key: str, svg_template: str, *, color: str = "muted", size: int = 16) -> None:
    if button is None:
        return
    px = max(12, int(size or 16))
    button.setProperty("_ga_svg_target", "button")
    button.setProperty("_ga_svg_key", str(key or "icon"))
    button.setProperty("_ga_svg_template", str(svg_template or ""))
    button.setProperty("_ga_svg_color", str(color or "muted"))
    button.setProperty("_ga_svg_size", px)
    button.setIcon(_svg_icon(str(key or "icon"), str(svg_template or ""), color=_svg_resolve_color(color), size=px))
    button.setIconSize(QSize(px, px))


def set_label_svg_icon(label: QLabel, key: str, svg_template: str, *, color: str = "muted", size: int = 16) -> None:
    if label is None:
        return
    px = max(12, int(size or 16))
    label.setProperty("_ga_svg_target", "label")
    label.setProperty("_ga_svg_key", str(key or "icon"))
    label.setProperty("_ga_svg_template", str(svg_template or ""))
    label.setProperty("_ga_svg_color", str(color or "muted"))
    label.setProperty("_ga_svg_size", px)
    icon = _svg_icon(str(key or "icon"), str(svg_template or ""), color=_svg_resolve_color(color), size=px)
    label.setPixmap(icon.pixmap(px, px))


def refresh_svg_icons(root: QWidget | None) -> None:
    if root is None:
        return
    widgets = [root]
    widgets.extend(root.findChildren(QWidget))
    for widget in widgets:
        target = str(widget.property("_ga_svg_target") or "").strip().lower()
        if target not in ("button", "label"):
            continue
        key = str(widget.property("_ga_svg_key") or "icon").strip() or "icon"
        svg_template = str(widget.property("_ga_svg_template") or "").strip()
        color = str(widget.property("_ga_svg_color") or "muted").strip() or "muted"
        try:
            size = max(12, int(widget.property("_ga_svg_size") or 16))
        except Exception:
            size = 16
        if (not svg_template) or size <= 0:
            continue
        if target == "button" and isinstance(widget, QPushButton):
            widget.setIcon(_svg_icon(key, svg_template, color=_svg_resolve_color(color), size=size))
            widget.setIconSize(QSize(size, size))
        elif target == "label" and isinstance(widget, QLabel):
            widget.setPixmap(_svg_icon(key, svg_template, color=_svg_resolve_color(color), size=size).pixmap(size, size))


def combo_popup_view_style() -> str:
    return (
        f"QAbstractItemView {{ background: {C['layer1']}; color: {C['text']}; "
        f"border: 1px solid {C['stroke_hover']}; border-radius: {F['radius_md']}px; padding: 4px; "
        f"selection-background-color: {C['accent_soft_bg']}; selection-color: {C['text']}; outline: 0; }}"
    )


def combo_popup_container_style() -> str:
    return f"QFrame {{ background: {C['layer1']}; color: {C['text']}; border: none; }}"


def menu_popup_style() -> str:
    return (
        f"QMenu {{ background: {C['layer1']}; color: {C['text']}; border: 1px solid {C['stroke_hover']}; "
        f"border-radius: {F['radius_md']}px; padding: 4px; }}"
        f"QMenu::item {{ padding: 7px 16px; border-radius: {max(4, int(F['radius_sm']))}px; }}"
        f"QMenu::item:selected {{ background: {C['accent_soft_bg']}; color: {C['text']}; }}"
        f"QMenu::separator {{ height: 1px; background: {C['stroke_default']}; margin: 4px 6px; }}"
    )


def apply_menu_popup_theme(menu: QMenu | None) -> None:
    if menu is None:
        return
    try:
        menu.setStyleSheet(menu_popup_style())
    except Exception:
        pass
    try:
        pal = QPalette(menu.palette())
        bg = QColor(str(C["layer1"]))
        text = QColor(str(C["text"]))
        highlight = QColor(str(C["accent_soft_bg"]))
        for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
            pal.setColor(group, QPalette.Window, bg)
            pal.setColor(group, QPalette.Base, bg)
            pal.setColor(group, QPalette.Button, bg)
            pal.setColor(group, QPalette.Text, text)
            pal.setColor(group, QPalette.WindowText, text)
            pal.setColor(group, QPalette.ButtonText, text)
            pal.setColor(group, QPalette.Highlight, highlight)
            pal.setColor(group, QPalette.HighlightedText, text)
        menu.setPalette(pal)
    except Exception:
        pass


def apply_combo_popup_theme(combo: QComboBox | None, *, combo_style: str = "") -> None:
    if combo is None:
        return
    if combo_style:
        try:
            combo.setStyleSheet(combo_style)
        except Exception:
            pass
    try:
        view = combo.view()
    except Exception:
        view = None
    if view is None:
        return
    popup_style = combo_popup_view_style()
    try:
        view.setStyleSheet(popup_style)
    except Exception:
        pass
    try:
        popup = view.window()
    except Exception:
        popup = None
    if popup is not None and popup is not view:
        try:
            popup.setStyleSheet(combo_popup_container_style())
        except Exception:
            pass
    try:
        viewport = view.viewport()
        if viewport is not None:
            viewport.setStyleSheet(f"background: {C['layer1']}; color: {C['text']};")
    except Exception:
        pass


def refresh_theme_aware_popup_surfaces(root: QWidget | None, *, combo_style: str = "") -> None:
    if root is None:
        return
    widgets = [root]
    widgets.extend(root.findChildren(QWidget))
    for widget in widgets:
        if isinstance(widget, QMenu):
            apply_menu_popup_theme(widget)
            continue
        if isinstance(widget, QComboBox):
            apply_combo_popup_theme(widget, combo_style=combo_style)
            continue
        for hook_name in ("_refresh_slash_popup_theme", "_refresh_info_popup_style"):
            refresher = getattr(widget, hook_name, None)
            if not callable(refresher):
                continue
            try:
                refresher()
            except Exception:
                pass


def chat_auto_jump_latest_enabled(cfg: dict | None) -> bool:
    data = cfg if isinstance(cfg, dict) else {}
    value = data.get("theme_chat_auto_jump_latest", True)
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    text = str(value).strip().lower()
    if text in ("0", "false", "no", "off", "disable", "disabled", "关", "关闭", "否"):
        return False
    if text in ("1", "true", "yes", "on", "enable", "enabled", "开", "开启", "是"):
        return True
    return bool(value)


def _avatar_role_key(role: str) -> str:
    return "user" if str(role or "").strip().lower() == "user" else "assistant"


def _avatar_cfg_value(cfg: dict | None, role: str, key_suffix: str) -> str:
    data = cfg if isinstance(cfg, dict) else {}
    prefix = "theme_user_avatar" if _avatar_role_key(role) == "user" else "theme_ai_avatar"
    return str(data.get(f"{prefix}_{key_suffix}") or "").strip()


def _avatar_source_path(cfg: dict | None, role: str) -> str:
    generated = _avatar_cfg_value(cfg, role, "image")
    source = _avatar_cfg_value(cfg, role, "source")
    raw = generated or source
    if not raw:
        return ""
    resolved = lz._resolve_config_path(raw)
    if not resolved or (not os.path.isfile(resolved)):
        return ""
    return resolved


def _render_custom_avatar_pixmap(image_path: str, size: int) -> QPixmap | None:
    path = str(image_path or "").strip()
    px = max(12, int(size or 30))
    if not path or not os.path.isfile(path):
        return None
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        mtime = 0.0
    cache_key = f"{path}|{mtime}|{px}"
    cached = _AVATAR_PIXMAP_CACHE.get(cache_key)
    if cached is not None and not cached.isNull():
        return QPixmap(cached)
    image = QImage(path)
    if image.isNull():
        return None
    inner_px = max(4, px - 2)
    rendered = image.scaled(inner_px, inner_px, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    if rendered.width() != inner_px or rendered.height() != inner_px:
        ox = max(0, int((rendered.width() - inner_px) / 2))
        oy = max(0, int((rendered.height() - inner_px) / 2))
        rendered = rendered.copy(ox, oy, inner_px, inner_px)
    pixmap = QPixmap(px, px)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    clip = QPainterPath()
    clip.addEllipse(1.0, 1.0, float(inner_px), float(inner_px))
    painter.setClipPath(clip)
    painter.drawImage(1, 1, rendered)
    painter.end()
    _AVATAR_PIXMAP_CACHE[cache_key] = QPixmap(pixmap)
    return pixmap


def _refresh_widget_style(widget: QWidget | None) -> None:
    if widget is None:
        return
    try:
        style = widget.style()
        if style is not None:
            style.unpolish(widget)
            style.polish(widget)
    except Exception:
        pass
    widget.update()


def _apply_message_avatar(label: QLabel | None, role: str, cfg: dict | None = None, *, size: int = 30) -> None:
    if label is None:
        return
    role_key = _avatar_role_key(role)
    px = max(12, int(size or 30))
    label.setProperty("_ga_avatar_role", role_key)
    label.setProperty("_ga_avatar_size", px)
    label.clear()
    label.setPixmap(QPixmap())
    image_path = _avatar_source_path(cfg, role_key)
    if image_path:
        pixmap = _render_custom_avatar_pixmap(image_path, px)
        if pixmap is not None and (not pixmap.isNull()):
            label.setProperty("avatarVariant", "custom")
            _refresh_widget_style(label)
            label.setPixmap(pixmap)
            return
    label.setProperty("avatarVariant", "default")
    _refresh_widget_style(label)
    svg_data = _SVG_USER if role_key == "user" else _SVG_BOT
    avatar_color = C["user_avatar_color"] if role_key == "user" else C["bot_avatar_color"]
    icon = _svg_icon(f"msg_avatar_{role_key}", svg_data, color=avatar_color, size=px)
    pixmap = icon.pixmap(px, px)
    if pixmap is not None and (not pixmap.isNull()):
        label.setPixmap(pixmap)
        return
    label.setText("你" if role_key == "user" else "助")


def _configure_message_label_selection(label: QLabel | None) -> None:
    if label is None:
        return
    # GitHub macOS runners can segfault when a headless QLabel enables mouse text
    # selection. Keep normal desktop behavior, but skip the risky path in CI.
    if bool(getattr(lz, "IS_MACOS", False)) and str(os.environ.get("GITHUB_ACTIONS") or "").strip().lower() == "true":
        try:
            label.setProperty("_ga_selection_mode", "disabled")
        except Exception:
            pass
        return
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    try:
        label.setProperty("_ga_selection_mode", "mouse")
    except Exception:
        pass


_STREAMING_HEIGHT_GROWTH_STEP_PX = 8
_BROWSER_MIN_HEIGHT_PX = 18
_BROWSER_HEIGHT_SLACK_PX = 2
_STREAMING_HEIGHT_BASELINE_SLACK_PX = 8


def _browser_frame_vertical_inset(browser: QTextBrowser) -> int:
    try:
        if browser.frameShape() == QFrame.NoFrame:
            return 0
    except Exception:
        pass
    try:
        return max(0, int(browser.frameWidth() or 0)) * 2
    except Exception:
        return 0


def _fit_browser_height(browser: QTextBrowser) -> None:
    doc = browser.document()
    viewport_w = browser.viewport().width()
    width = viewport_w if viewport_w > 40 else 560
    width_key = width
    old_width = int(browser.property("_fitWidth") or 0)
    old_height = int(browser.property("_fitHeight") or 0)
    if old_width == width_key and old_height > 0 and not browser.property("_fitForce"):
        return
    doc.setTextWidth(width)
    new_h = max(
        _BROWSER_MIN_HEIGHT_PX,
        int(math.ceil(doc.size().height()))
        + _browser_frame_vertical_inset(browser)
        + _BROWSER_HEIGHT_SLACK_PX,
    )
    if browser.property("streamingHold"):
        current_h = max(0, int(browser.height() or 0))
        trusted_current_h = 0
        if old_height > 0 and abs(current_h - old_height) <= _STREAMING_HEIGHT_BASELINE_SLACK_PX:
            trusted_current_h = max(current_h, old_height)
        trusted_floor = trusted_current_h or old_height
        if trusted_floor > 0 and new_h < trusted_floor:
            new_h = trusted_floor
        elif old_height > 0:
            growth = new_h - max(old_height, trusted_current_h)
            if 0 < growth < _STREAMING_HEIGHT_GROWTH_STEP_PX:
                browser.setProperty("_fitForce", False)
                return
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


def _refit_browser_for_state(browser: QTextBrowser, *, streaming: bool) -> None:
    browser.setProperty("streamingHold", bool(streaming))
    browser.setProperty("_fitWidth", 0)
    browser.setProperty("_fitHeight", 0)
    browser.setProperty("_fitForce", True)
    _fit_browser_height(browser)


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
        self._slash_command_provider = None
        self._slash_popup = None
        self._slash_popup_signature = ()
        self._slash_popup_refresh_timer = QTimer(self)
        self._slash_popup_refresh_timer.setSingleShot(True)
        self._slash_popup_refresh_timer.timeout.connect(self._refresh_slash_command_popup)
        self.setAcceptDrops(True)
        self.textChanged.connect(self._schedule_slash_command_popup_refresh)

    def set_slash_command_provider(self, provider):
        self._slash_command_provider = provider
        self._refresh_slash_command_popup()

    def _schedule_slash_command_popup_refresh(self):
        timer = getattr(self, "_slash_popup_refresh_timer", None)
        if timer is None:
            self._refresh_slash_command_popup()
            return
        if timer.isActive():
            return
        timer.start(0)

    def _ensure_slash_popup(self):
        popup = getattr(self, "_slash_popup", None)
        if popup is not None:
            return popup
        host = self.window() if isinstance(self.window(), QWidget) else self
        popup = QListWidget(host)
        popup.setWindowFlags(Qt.FramelessWindowHint)
        popup.setFocusPolicy(Qt.NoFocus)
        popup.setAttribute(Qt.WA_ShowWithoutActivating, True)
        popup.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        popup.setSelectionMode(QAbstractItemView.SingleSelection)
        popup.setSelectionBehavior(QAbstractItemView.SelectRows)
        popup.setUniformItemSizes(True)
        popup.setMouseTracking(True)
        popup.itemClicked.connect(self._accept_slash_popup_item)
        self._slash_popup = popup
        self._refresh_slash_popup_theme()
        return popup

    def _slash_popup_style_sheet(self) -> str:
        hover_bg = C.get("hover") or C.get("field_bg") or C.get("surface") or "#eef4ff"
        return (
            f"QListWidget {{ background: {C['panel']}; color: {C['text']}; border: 1px solid {C['stroke_default']}; "
            f"border-radius: {F['radius_md']}px; padding: 6px; outline: none; }}"
            f"QListWidget::item {{ padding: 8px 10px; border-radius: {max(4, int(F['radius_sm']))}px; }}"
            f"QListWidget::item:selected {{ background: {hover_bg}; color: {C['text']}; }}"
            f"QListWidget::item:hover {{ background: {hover_bg}; }}"
        )

    def _refresh_slash_popup_theme(self):
        popup = getattr(self, "_slash_popup", None)
        if popup is None:
            return
        try:
            popup.setStyleSheet(self._slash_popup_style_sheet())
        except Exception:
            pass
        try:
            viewport = popup.viewport()
            if viewport is not None:
                viewport.setStyleSheet(f"background: {C['panel']}; color: {C['text']};")
        except Exception:
            pass

    def _slash_query_text(self) -> str:
        if bool(getattr(self, "isReadOnly", lambda: False)()):
            return ""
        text = str(self.toPlainText() or "")
        if "\n" in text or "\r" in text:
            return ""
        query = text.lstrip()
        return query if query.startswith("/") else ""

    def _slash_popup_items(self):
        provider = getattr(self, "_slash_command_provider", None)
        if not callable(provider):
            return []
        query = self._slash_query_text()
        if not query:
            return []
        try:
            rows = provider(query, editor=self)
        except TypeError:
            rows = provider(query)
        except Exception:
            return []
        out = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            command = str(row.get("command") or "").strip()
            if not command:
                continue
            item = dict(row)
            item["command"] = command
            insert_text = row.get("insert_text")
            if insert_text is None or insert_text == "":
                insert_text = command
            item["insert_text"] = str(insert_text or command)
            out.append(item)
        return out

    def _hide_slash_command_popup(self):
        popup = getattr(self, "_slash_popup", None)
        if popup is not None:
            popup.hide()

    def _slash_popup_signature_for_rows(self, rows) -> tuple:
        signature = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            signature.append(
                (
                    str(row.get("command") or ""),
                    str(row.get("insert_text") or ""),
                    str(row.get("description") or ""),
                )
            )
        return tuple(signature)

    def _position_slash_popup(self, popup, *, width: int, height: int):
        top_left = self.mapToGlobal(QPoint(0, 0))
        x = int(top_left.x())
        y = int(top_left.y()) - int(height) - 4
        screen = None
        try:
            screen = QApplication.screenAt(top_left)
        except Exception:
            screen = None
        if screen is None:
            try:
                screen = self.screen()
            except Exception:
                screen = None
        if screen is not None:
            area = screen.availableGeometry()
            x = max(int(area.left()) + 4, min(x, int(area.right()) - int(width) - 4))
            if y < int(area.top()) + 4:
                y = int(top_left.y()) + int(self.height()) + 4
        popup.resize(width, height)
        host = popup.parentWidget()
        if host is not None:
            popup.move(host.mapFromGlobal(QPoint(x, y)))
            return
        popup.move(x, y)

    def _refresh_slash_command_popup(self):
        rows = self._slash_popup_items()
        if not rows:
            self._slash_popup_signature = ()
            self._hide_slash_command_popup()
            return
        popup = self._ensure_slash_popup()
        signature = self._slash_popup_signature_for_rows(rows)
        if signature != getattr(self, "_slash_popup_signature", ()):
            popup.setUpdatesEnabled(False)
            try:
                popup.blockSignals(True)
                popup.clear()
                for row in rows:
                    command = str(row.get("command") or "").strip()
                    desc = str(row.get("description") or "").strip()
                    label = command if not desc else f"{command}    {desc}"
                    item = QListWidgetItem(label)
                    item.setData(Qt.UserRole, row)
                    popup.addItem(item)
                self._slash_popup_signature = signature
            finally:
                popup.blockSignals(False)
                popup.setUpdatesEnabled(True)
        if popup.count() <= 0:
            popup.hide()
            return
        if popup.currentRow() < 0:
            popup.setCurrentRow(0)
        row_height = max(24, popup.sizeHintForRow(0))
        visible_rows = min(7, popup.count())
        height = max(40, row_height * visible_rows + 14)
        width = min(560, max(self.width(), 320))
        self._position_slash_popup(popup, width=width, height=height)
        popup.show()
        try:
            popup.raise_()
        except Exception:
            pass

    def _apply_slash_popup_row(self, row: dict):
        raw = (row or {}).get("insert_text")
        if raw is None or raw == "":
            raw = (row or {}).get("command") or ""
        text = str(raw or "")
        if not text.strip():
            return
        self.setPlainText(text)
        self.moveCursor(QTextCursor.End)
        self.setFocus(Qt.OtherFocusReason)
        self._hide_slash_command_popup()

    def _accept_slash_popup_item(self, item):
        if item is None:
            return
        row = item.data(Qt.UserRole)
        if isinstance(row, dict):
            self._apply_slash_popup_row(row)

    def _accept_current_slash_popup_item(self):
        popup = getattr(self, "_slash_popup", None)
        if popup is None or not popup.isVisible():
            return False
        item = popup.currentItem()
        if item is None and popup.count() > 0:
            item = popup.item(0)
        if item is None:
            return False
        self._accept_slash_popup_item(item)
        return True

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
        popup = getattr(self, "_slash_popup", None)
        if popup is not None and popup.isVisible():
            key = event.key()
            if key in (Qt.Key_Down, Qt.Key_Up):
                step = 1 if key == Qt.Key_Down else -1
                count = popup.count()
                if count > 0:
                    current = popup.currentRow()
                    if current < 0:
                        current = 0 if step > 0 else count - 1
                    else:
                        current = (current + step) % count
                    popup.setCurrentRow(current)
                return
            if key in (Qt.Key_Tab, Qt.Key_Backtab):
                if self._accept_current_slash_popup_item():
                    return
            if key == Qt.Key_Escape:
                popup.hide()
                return
        if (
            event.key() in (Qt.Key_Return, Qt.Key_Enter)
            and not (event.modifiers() & Qt.ShiftModifier)
        ):
            self._submit_cb()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event) -> None:
        self._hide_slash_command_popup()
        super().hideEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        popup = getattr(self, "_slash_popup", None)
        if popup is not None and popup.isVisible():
            self._refresh_slash_command_popup()

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

        self._button = QPushButton(self._title)
        self._button.setObjectName("turnFoldHeader")
        self._button.setCursor(QCursor(Qt.PointingHandCursor))
        self._button.clicked.connect(self.toggle)
        layout.addWidget(self._button)
        self._refresh_button_state()

        self._body = QTextBrowser()
        self._body.setReadOnly(True)
        self._body.setOpenExternalLinks(True)
        self._body.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard | Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard
        )
        self._body.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._body.setFrameShape(QFrame.NoFrame)
        self._body.document().setDocumentMargin(0)
        self._body.document().setDefaultStyleSheet(_MD_CSS)
        self._body.setStyleSheet(
            f"QTextBrowser {{ background: transparent; border: none; color: {C['text_soft']}; font-size: 13px; }}"
        )
        self._body.hide()
        layout.addWidget(self._body)
        self.set_text(text)

    def toggle(self):
        self._expanded = not self._expanded
        self._refresh_button_state()
        self._body.setVisible(self._expanded)
        if self._expanded:
            _fit_browser_height(self._body)

    def _refresh_button_state(self) -> None:
        self._button.setText(self._title)
        set_button_svg_icon(
            self._button,
            "turn_fold_open" if self._expanded else "turn_fold_closed",
            _SVG_CHEVRON_DOWN if self._expanded else _SVG_CHEVRON_RIGHT,
            color="muted",
            size=14,
        )

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
    def __init__(self, text: str, role: str, parent=None, on_resend=None, avatar_cfg: dict | None = None):
        super().__init__(parent)
        self._text = text or ""
        self._role = role
        self._avatar_cfg = avatar_cfg if isinstance(avatar_cfg, dict) else {}
        self._finished = True
        self._on_resend = on_resend
        self._stream_prefix_signature = None
        self._stream_live_browser = None
        self._last_browser_width = 0

        is_user = role == "user"
        self.setObjectName("userMsgRow" if is_user else "botMsgRow")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(20, 10, 20, 10)
        outer.setSpacing(12)
        outer.setAlignment(Qt.AlignTop)

        avatar = QLabel()
        avatar.setObjectName("msgAvatar")
        avatar.setFixedSize(30, 30)
        avatar.setAlignment(Qt.AlignCenter)
        self._avatar_label = avatar
        self.refresh_avatar()
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
            _configure_message_label_selection(label)
            label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
            right.addWidget(label)
            self._label = label
            self._content_layout = None
            self._action_row = None
            self._bubble = None
        else:
            host = QWidget()
            host.setStyleSheet("background: transparent;")
            host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            host_layout = QVBoxLayout(host)
            host_layout.setContentsMargins(0, 0, 0, 0)
            host_layout.setSpacing(6)
            self._assistant_host = host
            self._content_layout = host_layout
            right.addWidget(host)

            self._action_row = QWidget()
            self._action_row.setStyleSheet("background: transparent;")
            self._action_row.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
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
                f"font-family: {F['font_family_mono']}; }}"
            )
            self._token_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._token_label.hide()
            arow.addWidget(self._token_label)
            right.addWidget(self._action_row)
            self._action_row.hide()
            self._action_row_hovered = False
            self._action_row_live = False

            self._label = None
            self._bubble = None

        outer.addLayout(right, 1)
        self.set_text(self._text)

    def refresh_avatar(self):
        _apply_message_avatar(getattr(self, "_avatar_label", None), self._role, getattr(self, "_avatar_cfg", None), size=30)

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

    def _sync_action_row_visibility(self):
        row = getattr(self, "_action_row", None)
        if row is None:
            return
        if bool(getattr(self, "_action_row_hovered", False)) or bool(getattr(self, "_action_row_live", False)):
            row.show()
        else:
            row.hide()

    def enterEvent(self, event):
        if self._role != "user" and self._finished:
            self._action_row_hovered = True
            if getattr(self, "_copy_btn", None):
                self._copy_btn.show()
            if getattr(self, "_regen_btn", None):
                self._regen_btn.show()
            self._sync_action_row_visibility()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._action_row_hovered = False
        if getattr(self, "_copy_btn", None):
            self._copy_btn.hide()
        if getattr(self, "_regen_btn", None):
            self._regen_btn.hide()
        self._sync_action_row_visibility()
        super().leaveEvent(event)

    def set_token_info(self, input_tokens: int, output_tokens: int, *, live: bool = False):
        lbl = getattr(self, "_token_label", None)
        if lbl is None:
            return
        inp = int(input_tokens or 0)
        out = int(output_tokens or 0)
        if inp <= 0 and out <= 0:
            self._action_row_live = False
            lbl.hide()
            self._sync_action_row_visibility()
            return
        suffix = " …" if live else ""
        lbl.setText(f"↑{inp}  ↓{out}{suffix}")
        lbl.show()
        self._action_row_live = bool(live)
        self._sync_action_row_visibility()
        row = getattr(self, "_action_row", None)
        if row is not None:
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

    def _iter_active_assistant_browsers(self):
        if self._content_layout is None:
            return
        for idx in range(self._content_layout.count()):
            item = self._content_layout.itemAt(idx)
            widget = item.widget() if item is not None else None
            if widget is None:
                continue
            if isinstance(widget, QTextBrowser):
                yield widget
                continue
            for browser in widget.findChildren(QTextBrowser):
                yield browser

    def _refit_finished_assistant_browsers(self):
        if self._role == "user" or self._content_layout is None or not self._finished:
            return
        for browser in self._iter_active_assistant_browsers():
            _refit_browser_for_state(browser, streaming=False)
        host = getattr(self, "_assistant_host", None)
        if host is not None:
            host.adjustSize()
            host.updateGeometry()
        row = getattr(self, "_action_row", None)
        if row is not None:
            row.adjustSize()
            row.updateGeometry()
        self.adjustSize()
        self.updateGeometry()
        self.update()

    def _schedule_finished_assistant_refit(self):
        if self._role == "user" or self._content_layout is None or not self._finished:
            return
        QTimer.singleShot(0, self._refit_finished_assistant_browsers)

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
        browser.setFrameShape(QFrame.NoFrame)
        browser.document().setDocumentMargin(0)
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
            return

        self._refit_finished_assistant_browsers()
        self._schedule_finished_assistant_refit()


def refresh_message_row_avatars(root: QWidget | None) -> None:
    if root is None:
        return
    widgets = []
    if isinstance(root, MessageRow):
        widgets.append(root)
    widgets.extend(root.findChildren(MessageRow))
    for widget in widgets:
        try:
            widget.refresh_avatar()
        except Exception:
            pass


def build_message_row(text: str, role: str, parent=None, *, on_resend=None, avatar_cfg: dict | None = None, row_cls=None):
    cls = row_cls or MessageRow
    try:
        return cls(text, role, parent, on_resend=on_resend, avatar_cfg=avatar_cfg)
    except TypeError:
        row = cls(text, role, parent, on_resend=on_resend)
        if hasattr(row, "_avatar_cfg") and isinstance(avatar_cfg, dict):
            try:
                row._avatar_cfg = avatar_cfg
                refresher = getattr(row, "refresh_avatar", None)
                if callable(refresher):
                    refresher()
            except Exception:
                pass
        return row


class OptionCard(QFrame):
    def __init__(self, icon: str, title: str, desc: str, command, parent=None):
        super().__init__(parent)
        self._command = command
        self._pressed = False
        self.setObjectName("optionCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(92)
        row = QHBoxLayout(self)
        row.setContentsMargins(22, 14, 22, 14)
        row.setSpacing(16)

        icon_lbl = QLabel()
        icon_lbl.setObjectName("optionIcon")
        icon_lbl.setFixedSize(40, 40)
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        if isinstance(icon, dict):
            set_label_svg_icon(
                icon_lbl,
                str(icon.get("key") or "option_icon"),
                str(icon.get("svg") or _SVG_WINDOW),
                color=str(icon.get("color") or "accent_text"),
                size=int(icon.get("size") or 18),
            )
        else:
            icon_lbl.setText(str(icon or ""))
        row.addWidget(icon_lbl, 0)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("optionTitle")
        title_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        desc_lbl = QLabel(desc)
        desc_lbl.setObjectName("optionDesc")
        desc_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        text_col.addWidget(title_lbl)
        text_col.addWidget(desc_lbl)
        row.addLayout(text_col, 1)

        arrow = QLabel()
        arrow.setObjectName("optionArrow")
        arrow.setFixedSize(18, 18)
        arrow.setAlignment(Qt.AlignCenter)
        arrow.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        set_label_svg_icon(arrow, "option_arrow", _SVG_CHEVRON_RIGHT, color="muted", size=14)
        row.addWidget(arrow, 0)

    def mousePressEvent(self, event):
        self._pressed = bool(event.button() == Qt.LeftButton)
        return super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        should_trigger = self._pressed and event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint())
        self._pressed = False
        if should_trigger and callable(self._command):
            self._command()
            event.accept()
            return
        return super().mouseReleaseEvent(event)


set_md_css(_build_md_css())
