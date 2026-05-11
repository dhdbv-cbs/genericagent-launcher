from __future__ import annotations

import json
import os
import posixpath
import queue
import shlex
import subprocess
import threading
import time
import uuid

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QStyle, QSystemTrayIcon, QVBoxLayout

from launcher_app import core as lz
from launcher_app.theme import C

from .common import (
    _session_copy,
    normalize_remote_agent_dir,
    normalize_ssh_error_text,
    remote_device_agent_dir,
    remote_device_agent_mode,
    remote_device_container_name,
)

_RUNTIME_REASONING_EFFORT_CHOICES = [
    ("", "跟随配置"),
    ("none", "none"),
    ("minimal", "minimal"),
    ("low", "low"),
    ("medium", "medium"),
    ("high", "high"),
    ("xhigh", "xhigh"),
]
_RUNTIME_REASONING_EFFORT_VALUES = {value for value, _label in _RUNTIME_REASONING_EFFORT_CHOICES if value}


class BridgeRuntimeMixin:
    def _local_slash_command_items(self):
        return [
            {"command": "/help", "insert_text": "/help", "description": "显示启动器支持的斜杠命令"},
            {"command": "/stop", "insert_text": "/stop", "description": "停止当前回复任务"},
            {"command": "/new", "insert_text": "/new", "description": "新建一个当前设备上下文会话"},
            {
                "command": "/llm",
                "insert_text": "/llm",
                "description": "显示当前模型列表",
                "aliases": ["/llm"],
            },
            {
                "command": "/llm N",
                "insert_text": "/llm ",
                "description": "切换到第 N 个模型编号",
                "aliases": ["/llm", "/llm "],
            },
            {
                "command": "/continue",
                "insert_text": "/continue",
                "description": "后台恢复最近一条历史会话",
                "aliases": ["/continue"],
            },
            {
                "command": "/continue N",
                "insert_text": "/continue ",
                "description": "后台恢复第 N 条历史会话",
                "aliases": ["/continue", "/continue "],
            },
            {
                "command": "/session.<attr>=<val>",
                "insert_text": "/session.",
                "description": "透传给上游 agant 的运行时参数覆盖",
                "aliases": ["/session."],
            },
        ]

    def _local_slash_command_suggestions(self, query_text="", *, editor=None):
        query = str(query_text or "").strip().lower()
        items = list(self._local_slash_command_items())
        if not query or query == "/":
            return items
        matched = []
        for item in items:
            patterns = [str(item.get("command") or "").strip().lower(), str(item.get("insert_text") or "").strip().lower()]
            patterns += [str(value or "").strip().lower() for value in (item.get("aliases") or [])]
            patterns = [value for value in patterns if value]
            if any(value.startswith(query) or query.startswith(value) for value in patterns):
                matched.append(item)
        return matched or items

    def _apply_bridge_widget_state(self, widget, enabled, *, enabled_tooltip="", disabled_tooltip=""):
        if widget is None:
            return
        widget.setEnabled(bool(enabled))
        tooltip = enabled_tooltip if bool(enabled) else disabled_tooltip
        try:
            widget.setToolTip(str(tooltip or ""))
        except Exception:
            pass

    def _bridge_attachment_remove_disabled_reason(self, *, active_mode=False):
        if bool(active_mode):
            return "当前这一轮还没有结束；本轮已附带图片会在回复完成后自动清除。"
        return ""

    def _bridge_llm_combo_disabled_reason(self):
        if bool(getattr(self, "llms", None)):
            return ""
        return "当前还没有可用的 LLM 配置。"

    def _bridge_reasoning_effort_combo_disabled_reason(self):
        if bool(getattr(self, "llms", None)):
            return ""
        return "当前还没有可用的 LLM 配置。"

    def _local_slash_help_text(self):
        return "\n".join(
            [
                "/help - 显示帮助",
                "/stop - 停止当前任务",
                "/new - 触发启动器新建会话",
                "/llm - 查看当前模型列表",
                "/llm N - 切换到第 N 个模型",
                "/continue - 后台恢复最近一条历史会话",
                "/continue N - 后台恢复第 N 条历史会话",
                "/session.<attr>=<val> - 透传给上游 agant 执行运行时参数覆盖",
            ]
        )

    def _local_slash_llm_text(self):
        if not self.llms:
            return "当前没有可用的 LLM 配置。"
        lines = []
        for pos, llm in enumerate(self.llms):
            marker = "→" if llm.get("current") else "  "
            label = str(llm.get("name") or "(未命名)").strip() or "(未命名)"
            lines.append(f"{marker} [{int(llm.get('idx', pos) or pos)}] {label}")
        return "LLMs:\n" + "\n".join(lines)

    def _show_local_slash_feedback(self, title: str, body: str = "", *, status_text: str = ""):
        parts = [str(title or "").strip(), str(body or "").strip()]
        text = "\n".join(part for part in parts if part)
        if not text:
            text = str(status_text or "").strip()
        if text:
            bucket = getattr(self, "_transient_chat_feedback", None)
            if not isinstance(bucket, list):
                bucket = []
                self._transient_chat_feedback = bucket
            key_resolver = getattr(self, "_transient_chat_feedback_key", None)
            key = ""
            if callable(key_resolver):
                try:
                    key = str(key_resolver(self.current_session if isinstance(getattr(self, "current_session", None), dict) else None) or "")
                except Exception:
                    key = ""
            bucket.append({"key": key, "role": "assistant", "text": text})
            if len(bucket) > 24:
                del bucket[:-24]
            renderer = getattr(self, "_render_session", None)
            if callable(renderer):
                try:
                    renderer(self.current_session if isinstance(getattr(self, "current_session", None), dict) else None)
                except Exception:
                    pass
            else:
                adder = getattr(self, "_add_message_row", None)
                if callable(adder):
                    try:
                        adder("assistant", text, finished=True, auto_scroll=True)
                    except Exception:
                        pass
            refresher = getattr(self, "_refresh_floating_chat_window", None)
            if callable(refresher):
                try:
                    refresher()
                except Exception:
                    pass
        setter = getattr(self, "_set_status", None)
        final_status = str(status_text or title or "").strip()
        if callable(setter) and final_status:
            setter(final_status)

    def _local_slash_clear_input(self, source_editor=None):
        editors = []
        for editor in (
            source_editor,
            getattr(self, "input_box", None),
            getattr(getattr(self, "_floating_chat_window", None), "input_box", None),
        ):
            if editor is None or any(editor is existing for existing in editors):
                continue
            editors.append(editor)
        for editor in editors:
            try:
                editor.clear()
            except Exception:
                pass
        self._pending_input_attachments_data = []
        refresher = getattr(self, "_refresh_input_attachment_bar", None)
        if callable(refresher):
            refresher()
        sync_to_floating = getattr(self, "_sync_draft_to_floating", None)
        if callable(sync_to_floating):
            try:
                sync_to_floating(force=True)
            except Exception:
                pass

    def _local_slash_switch_llm(self, llm_idx):
        try:
            target = int(llm_idx)
        except Exception:
            return False
        combo = getattr(self, "llm_combo", None)
        if combo is None:
            return False
        for pos, llm in enumerate(self.llms):
            try:
                candidate = int(llm.get("idx", pos) or pos)
            except Exception:
                continue
            if candidate != target:
                continue
            self._ignore_llm_change = True
            try:
                combo.setCurrentIndex(pos)
            finally:
                self._ignore_llm_change = False
            self._on_llm_changed(pos)
            return True
        return False

    def _local_slash_continue_rows(self):
        scope = "local"
        did = "local"
        resolver = getattr(self, "_current_device_context", None)
        if callable(resolver):
            try:
                scope, did = resolver()
            except Exception:
                scope, did = "local", "local"
        rows = list(self._active_sessions_for_channel("launcher", device_scope=scope, device_id=did))
        current_sid = str((self.current_session or {}).get("id") or "").strip()
        rows = [row for row in rows if str(row.get("id") or "").strip() and str(row.get("id") or "").strip() != current_sid]
        rows.sort(key=lambda row: float(row.get("updated_at", 0) or 0), reverse=True)
        return rows

    def _local_slash_restore_session(self, index: int):
        rows = self._local_slash_continue_rows()
        if not rows:
            self._show_local_slash_feedback("没有可恢复的历史会话。", status_text="没有可恢复的历史会话。")
            return True
        target = int(index or 0)
        if target < 0 or target >= len(rows):
            detail = f"历史会话索引越界（有效范围 1-{len(rows)}）。"
            self._show_local_slash_feedback("恢复失败", detail, status_text=detail)
            return True
        sid = str(rows[target].get("id") or "").strip()
        if not sid:
            self._show_local_slash_feedback("恢复失败", "目标历史会话无效。", status_text="目标历史会话无效。")
            return True
        self._load_session_by_id(sid)
        return True

    def _handle_local_slash_command(self, text: str, *, source_editor=None):
        cmd = str(text or "").strip()
        if not cmd.startswith("/"):
            return False
        if cmd.startswith("/session."):
            return False
        parts = cmd.split()
        op = str(parts[0] if parts else "").strip().lower()
        if op == "/help":
            self._local_slash_clear_input(source_editor=source_editor)
            self._show_local_slash_feedback("启动器斜杠命令", self._local_slash_help_text(), status_text="已显示启动器支持的斜杠命令。")
            return True
        if op == "/stop":
            self._local_slash_clear_input(source_editor=source_editor)
            self._abort()
            self._show_local_slash_feedback("已请求中断当前任务。", status_text="已请求中断当前任务。")
            return True
        if op == "/new":
            self._local_slash_clear_input(source_editor=source_editor)
            scope, did = ("local", "local")
            resolver = getattr(self, "_current_device_context", None)
            if callable(resolver):
                try:
                    scope, did = resolver()
                except Exception:
                    scope, did = "local", "local"
            self._new_session(scope=scope, device_id=did, prompt_device=False)
            return True
        if op == "/llm":
            self._local_slash_clear_input(source_editor=source_editor)
            if len(parts) == 1:
                self._show_local_slash_feedback("当前模型列表", self._local_slash_llm_text(), status_text="已显示当前模型列表。")
                return True
            disabled_reason = ""
            channel_checker = getattr(self, "_is_channel_process_session", None)
            try:
                if callable(channel_checker) and channel_checker():
                    disabled_reason = "渠道进程会话仅支持查看日志，不能切换模型。"
            except Exception:
                disabled_reason = ""
            if disabled_reason:
                self._show_local_slash_feedback("当前会话只读", disabled_reason, status_text=disabled_reason)
                return True
            if self._local_slash_switch_llm(parts[1]):
                self._show_local_slash_feedback("已切换模型。", status_text="已切换模型。")
                return True
            valid_ids = []
            for pos, llm in enumerate(self.llms):
                try:
                    valid_ids.append(str(int(llm.get("idx", pos) or pos)))
                except Exception:
                    continue
            detail = f"有效编号: {', '.join(dict.fromkeys(valid_ids))}" if valid_ids else "当前没有可用的 LLM 配置。"
            self._show_local_slash_feedback("切换失败", f"用法: /llm N\n{detail}", status_text="斜杠命令 /llm 执行失败。")
            return True
        if op == "/continue":
            self._local_slash_clear_input(source_editor=source_editor)
            if len(parts) == 1:
                return self._local_slash_restore_session(0)
            try:
                target_index = int(parts[1]) - 1
            except Exception:
                self._show_local_slash_feedback("恢复失败", "用法: /continue 或 /continue N", status_text="用法: /continue 或 /continue N")
                return True
            return self._local_slash_restore_session(target_index)
        return False

    def _normalize_reasoning_effort_value(self, value):
        text = str(value or "").strip().lower()
        if not text:
            return ""
        return text if text in _RUNTIME_REASONING_EFFORT_VALUES else ""

    def _session_snapshot_reasoning_effort_source(self, session=None):
        data = session if isinstance(session, dict) else (self.current_session if isinstance(getattr(self, "current_session", None), dict) else None)
        if not isinstance(data, dict):
            return ""
        source = str(((data.get("snapshot") or {}).get("reasoning_effort_source")) or "").strip().lower()
        return source if source in {"override", "runtime"} else ""

    def _current_session_reasoning_effort_override(self):
        session = self.current_session if isinstance(getattr(self, "current_session", None), dict) else None
        if isinstance(session, dict):
            if "reasoning_effort" in session:
                return self._normalize_reasoning_effort_value(session.get("reasoning_effort"))
            if self._session_snapshot_reasoning_effort_source(session) == "override":
                return self._session_snapshot_reasoning_effort(session)
            return ""
        return self._normalize_reasoning_effort_value(getattr(self, "_pending_reasoning_effort_override", None))

    def _session_snapshot_reasoning_effort(self, session=None):
        data = session if isinstance(session, dict) else (self.current_session if isinstance(getattr(self, "current_session", None), dict) else None)
        if not isinstance(data, dict):
            return ""
        return self._normalize_reasoning_effort_value((data.get("snapshot") or {}).get("reasoning_effort"))

    def _session_reasoning_effort_payload(self, session=None):
        data = session if isinstance(session, dict) else (self.current_session if isinstance(getattr(self, "current_session", None), dict) else None)
        if not isinstance(data, dict):
            return False, None
        if "reasoning_effort" in data:
            return True, self._normalize_reasoning_effort_value(data.get("reasoning_effort")) or None
        if self._session_snapshot_reasoning_effort_source(data) == "override":
            snapshot_value = self._session_snapshot_reasoning_effort(data) or None
            if snapshot_value:
                return True, snapshot_value
        snapshot_value = self._session_snapshot_reasoning_effort(data) or None
        return False, None

    def _current_reasoning_effort_selection(self):
        override = self._current_session_reasoning_effort_override()
        if override:
            return override
        if isinstance(getattr(self, "current_session", None), dict):
            return ""
        if self._is_remote_session():
            return ""
        return self._normalize_reasoning_effort_value(getattr(self, "_bridge_reasoning_effort", None))

    def _remote_parse_bridge_event_text(self, text):
        raw = str(text or "").strip()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
        for idx, ch in enumerate(raw):
            if ch != "{":
                continue
            try:
                parsed = json.loads(raw[idx:])
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    def _resolve_bridge_python(self):
        cfg_py = str((getattr(self, "cfg", {}) or {}).get("python_exe") or "").strip()
        if cfg_py:
            resolved = lz._resolve_configured_python_exe(cfg_py, agent_dir=self.agent_dir)
            if resolved and os.path.isfile(resolved):
                return resolved, None

        cached = getattr(self, "_last_dependency_check", None) or {}
        cached_py = str(cached.get("python") or "").strip()
        if cached_py and os.path.isfile(cached_py):
            return cached_py, None

        return lz._find_compatible_system_python(self.agent_dir)

    def _remember_bridge_python(self, py_path):
        py = str(py_path or "").strip()
        if not py:
            return
        try:
            rel = lz._make_python_exe_config_path(py, agent_dir=self.agent_dir)
        except Exception:
            return
        if not rel:
            return
        current = str((getattr(self, "cfg", {}) or {}).get("python_exe") or "").strip()
        if current == rel:
            return
        self.cfg["python_exe"] = rel
        try:
            lz.save_config(self.cfg)
        except Exception:
            pass

    def _attachment_bar_targets(self):
        targets = []
        host = getattr(self, "input_attachment_host", None)
        layout = getattr(self, "input_attachment_list_layout", None)
        summary = getattr(self, "input_attachment_summary", None)
        if host is not None and layout is not None and summary is not None:
            targets.append((host, layout, summary))
        floating = getattr(self, "_floating_chat_window", None)
        host = getattr(floating, "input_attachment_host", None)
        layout = getattr(floating, "input_attachment_list_layout", None)
        summary = getattr(floating, "input_attachment_summary", None)
        if host is not None and layout is not None and summary is not None:
            targets.append((host, layout, summary))
        return targets

    def _render_attachment_bar_target(self, host, layout, summary):
        if host is None or layout is None or summary is None:
            return
        list_widget = getattr(layout, "parentWidget", lambda: None)()
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child = item.layout()
            if child is not None:
                self._clear_layout(child)
            spacer = item.spacerItem()
            if spacer is not None:
                del spacer
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        items = list(self._attachment_bar_display_items())
        host.setVisible(bool(items))
        if list_widget is not None:
            list_widget.setVisible(bool(items))
        if not items:
            summary.setText("")
            layout.invalidate()
            self._refresh_attachment_geometry(host)
            return
        summary.setText(f"本轮将附带 {len(items)} 张图片。发送成功后它们只对这一轮有效。")
        for idx, item in enumerate(items):
            row = QFrame()
            row.setObjectName("cardInset")
            box = QHBoxLayout(row)
            box.setContentsMargins(10, 8, 10, 8)
            box.setSpacing(10)

            thumb = QLabel()
            thumb.setFixedSize(44, 44)
            thumb.setAlignment(Qt.AlignCenter)
            thumb.setStyleSheet(f"background: {C['field_bg']}; border-radius: 8px;")
            pix = QPixmap(str(item.get("path") or ""))
            if not pix.isNull():
                thumb.setPixmap(pix.scaled(44, 44, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                thumb.setText("图")
            box.addWidget(thumb, 0)

            text_col = QVBoxLayout()
            text_col.setContentsMargins(0, 0, 0, 0)
            text_col.setSpacing(2)
            title = QLabel(str(item.get("name") or f"图片 {idx + 1}"))
            title.setObjectName("bodyText")
            text_col.addWidget(title)
            path_label = QLabel(str(item.get("path") or ""))
            path_label.setObjectName("softTextSmall")
            path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            path_label.setWordWrap(True)
            text_col.addWidget(path_label)
            box.addLayout(text_col, 1)

            remove_btn = QPushButton("移除")
            remove_btn.setStyleSheet(self._action_button_style())
            disabled_reason = self._bridge_attachment_remove_disabled_reason(active_mode=False)
            self._apply_bridge_widget_state(
                remove_btn,
                not bool(disabled_reason),
                enabled_tooltip="把这张图片从下一轮输入中移除。",
                disabled_tooltip=disabled_reason,
            )
            remove_btn.clicked.connect(lambda _=False, i=idx: self._remove_pending_input_attachment(i))
            box.addWidget(remove_btn, 0)
            layout.addWidget(row)
        layout.invalidate()
        self._refresh_attachment_geometry(host)

    def _attachment_bar_display_items(self):
        return list(self._pending_input_attachments())

    def _refresh_attachment_geometry(self, host):
        current = host
        seen = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            is_window = bool(getattr(current, "isWindow", lambda: False)())
            try:
                if current.layout() is not None:
                    current.layout().activate()
            except Exception:
                pass
            try:
                current.updateGeometry()
                if not is_window:
                    current.adjustSize()
                current.update()
            except Exception:
                pass
            if is_window:
                break
            current = current.parentWidget()

    def _pending_input_attachments(self):
        items = getattr(self, "_pending_input_attachments_data", None)
        if not isinstance(items, list):
            items = []
            self._pending_input_attachments_data = items
        return items

    def _active_turn_attachments(self):
        items = getattr(self, "_active_turn_attachments_data", None)
        if not isinstance(items, list):
            items = []
            self._active_turn_attachments_data = items
        return items

    def _input_attachment_temp_dir(self):
        root = self.agent_dir if lz.is_valid_agent_dir(self.agent_dir) else os.path.join(os.path.expanduser("~"), ".genericagent_launcher")
        path = os.path.join(root, "temp", "launcher_input_images")
        os.makedirs(path, exist_ok=True)
        return path

    def _save_input_clipboard_image(self, image, name_hint="clipboard.png"):
        qimage = image if image is not None else None
        if qimage is None or qimage.isNull():
            raise ValueError("剪贴板图片无效。")
        ext = os.path.splitext(str(name_hint or "clipboard.png"))[1].lower() or ".png"
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            ext = ".png"
        fmt = "PNG" if ext == ".png" else ("JPG" if ext in (".jpg", ".jpeg") else ext[1:].upper())
        file_name = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}{ext}"
        path = os.path.join(self._input_attachment_temp_dir(), file_name)
        if not qimage.save(path, fmt):
            raise ValueError("保存剪贴板图片失败。")
        return path

    def _release_attachment_files(self, items):
        for item in list(items or []):
            if not bool(item.get("owned")):
                continue
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except Exception:
                pass

    def _clear_active_turn_attachments(self):
        items = list(self._active_turn_attachments())
        self._active_turn_attachments_data = []
        self._release_attachment_files(items)

    def _clear_pending_input_attachments(self, *, delete_owned=True):
        items = list(self._pending_input_attachments())
        self._pending_input_attachments_data = []
        if delete_owned:
            self._release_attachment_files(items)
        self._refresh_input_attachment_bar()

    def _remove_pending_input_attachment(self, index):
        items = self._pending_input_attachments()
        try:
            idx = int(index)
        except Exception:
            return
        if idx < 0 or idx >= len(items):
            return
        item = items.pop(idx)
        self._release_attachment_files([item])
        self._refresh_input_attachment_bar()

    def _refresh_input_attachment_bar(self):
        for host, layout, summary in self._attachment_bar_targets():
            self._render_attachment_bar_target(host, layout, summary)

    def _handle_input_image_attachments(self, attachments):
        if self._is_channel_process_session():
            QMessageBox.information(self, "不可添加", "当前选中的是渠道进程会话，不能为它附带图片。")
            return
        if self._busy or self._active_turn_attachments():
            QMessageBox.information(self, "请稍候", "当前这一轮还没有结束。等回复完成后，再为下一轮附带图片。")
            return
        items = self._pending_input_attachments()
        existing_paths = {os.path.normcase(os.path.abspath(str(item.get("path") or ""))) for item in items if str(item.get("path") or "").strip()}
        added = 0
        errors = []
        for raw in attachments or []:
            kind = str((raw or {}).get("kind") or "").strip().lower()
            try:
                if kind == "path":
                    path = os.path.abspath(str(raw.get("path") or "").strip())
                    if not os.path.isfile(path):
                        raise ValueError("图片文件不存在。")
                    norm = os.path.normcase(path)
                    if norm in existing_paths:
                        continue
                    items.append({"path": path, "name": str(raw.get("name") or os.path.basename(path)), "owned": False})
                    existing_paths.add(norm)
                    added += 1
                elif kind == "image":
                    path = self._save_input_clipboard_image(raw.get("image"), str(raw.get("name") or "clipboard.png"))
                    norm = os.path.normcase(os.path.abspath(path))
                    items.append({"path": path, "name": str(raw.get("name") or os.path.basename(path)), "owned": True})
                    existing_paths.add(norm)
                    added += 1
            except Exception as e:
                errors.append(str(e))
        self._refresh_input_attachment_bar()
        if added:
            self._set_status(f"已附带 {added} 张图片；它们只会用于下一轮发送。")
        if errors:
            QMessageBox.warning(self, "添加图片失败", "\n".join(errors[:3]))

    def _ensure_reply_notify_tray(self):
        tray = getattr(self, "_reply_notify_tray", None)
        if tray is not None:
            if getattr(self, "_launcher_tray_icon", None) is None:
                visible_getter = getattr(tray, "isVisible", None)
                if callable(visible_getter):
                    try:
                        if not bool(visible_getter()):
                            try:
                                tray.hide()
                            except Exception:
                                pass
                            tray = None
                            self._reply_notify_tray = None
                    except Exception:
                        pass
            if tray is not None:
                return tray
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        icon = self.windowIcon()
        if icon is None or icon.isNull():
            app = QApplication.instance()
            if app is not None:
                icon = app.windowIcon()
        if icon is None or icon.isNull():
            icon = self.style().standardIcon(QStyle.SP_MessageBoxInformation)
        tray = QSystemTrayIcon(icon, self)
        tray.setToolTip("GenericAgent 启动器")
        tray.show()
        self._reply_notify_tray = tray
        return tray

    def _reply_sound_enabled(self):
        return not bool(self.cfg.get("disable_reply_sound", False))

    def _reply_message_enabled(self):
        return not bool(self.cfg.get("disable_reply_message", False))

    def _play_reply_done_sound(self):
        if not self._reply_sound_enabled():
            return
        if os.name == "nt":
            try:
                import winsound

                try:
                    winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
                    return
                except Exception:
                    pass
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
                return
            except Exception:
                pass
        try:
            app = QApplication.instance()
            if app is not None:
                app.beep()
        except Exception:
            pass

    def _notify_reply_done(self, final_text: str):
        self._play_reply_done_sound()
        if not self._reply_message_enabled():
            return
        msg = "AI 回复已完成"
        preview = str(final_text or "").strip().replace("\r", " ").replace("\n", " ")
        if preview:
            if len(preview) > 72:
                preview = preview[:72].rstrip() + "…"
            msg = f"{msg}：{preview}"
        tray = None
        launcher_tray_getter = getattr(self, "_ensure_launcher_tray_icon", None)
        if callable(launcher_tray_getter):
            try:
                tray = launcher_tray_getter()
            except Exception:
                tray = None
        if tray is None:
            tray = self._ensure_reply_notify_tray()
        if tray is None:
            if lz.IS_MACOS:
                setter = getattr(self, "_set_status", None)
                if callable(setter):
                    try:
                        setter(msg)
                    except Exception:
                        pass
            return
        try:
            tray.show()
        except Exception:
            pass
        try:
            tray.showMessage("GenericAgent 启动器", msg, QSystemTrayIcon.Information, 1500)
        except Exception:
            pass

    def _request_backend_state(self, session_id=None):
        sid = session_id or ((self.current_session or {}).get("id"))
        if not sid or not self._bridge_ready:
            return
        self._state_request_seq += 1
        self._send_cmd({"cmd": "get_state", "session_id": sid, "request_id": self._state_request_seq})

    def _apply_state_to_session(
        self,
        session_id,
        backend_history,
        agent_history,
        llm_idx=None,
        process_pid=None,
        snapshot_ts=None,
        reasoning_effort=None,
    ):
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
        had_explicit_override = bool("reasoning_effort" in target or self._session_snapshot_reasoning_effort_source(target) == "override")
        normalized_reasoning_effort = self._normalize_reasoning_effort_value(reasoning_effort)
        if had_explicit_override and normalized_reasoning_effort:
            target["reasoning_effort"] = normalized_reasoning_effort
        else:
            target.pop("reasoning_effort", None)
        snapshot = dict(target.get("snapshot") or {})
        snapshot["version"] = int(snapshot.get("version", 1) or 1)
        snapshot["kind"] = "turn_complete"
        snapshot["captured_at"] = float(snapshot_ts or time.time())
        snapshot["turns"] = int(((target.get("token_usage") or {}).get("turns", 0) or 0))
        snapshot["llm_idx"] = int(target.get("llm_idx", 0) or 0)
        snapshot["process_pid"] = int(target.get("process_pid", 0) or 0)
        snapshot["has_backend_history"] = bool(target["backend_history"])
        snapshot["has_agent_history"] = bool(target["agent_history"])
        if normalized_reasoning_effort:
            snapshot["reasoning_effort"] = normalized_reasoning_effort
            snapshot["reasoning_effort_source"] = "override" if ("reasoning_effort" in target) else "runtime"
        else:
            snapshot.pop("reasoning_effort", None)
            snapshot.pop("reasoning_effort_source", None)
        target["snapshot"] = snapshot
        if self.current_session and self.current_session.get("id") == session_id:
            self.current_session = target
            self._bridge_reasoning_effort = normalized_reasoning_effort
            self._sync_reasoning_effort_combo()
        self._persist_session(target)

    def _current_llm_name(self):
        for llm in self.llms:
            if llm.get("current"):
                return str(llm.get("name") or "").strip()
        idx = self.llm_combo.currentIndex()
        if idx >= 0:
            return str(self.llm_combo.itemText(idx) or "").strip()
        return ""

    def _current_api_card_info(self):
        idx = 0
        try:
            idx = self._current_llm_index()
        except Exception:
            idx = 0
        try:
            configs = (lz.parse_mykey_py(os.path.join(self.agent_dir, "mykey.py")).get("configs") or [])
        except Exception:
            configs = []
        cfg = configs[idx] if 0 <= int(idx or 0) < len(configs) else None
        if isinstance(cfg, dict):
            var = str(cfg.get("var") or "").strip()
            data = cfg.get("data") if isinstance(cfg.get("data"), dict) else {}
            label = str(data.get("name") or data.get("model") or var).strip() or var
            return {"llm_idx": int(idx or 0), "api_card_var": var, "api_card_label": label}
        return {"llm_idx": int(idx or 0), "api_card_var": "", "api_card_label": ""}

    def _current_usage_target_info(self):
        session = self.current_session if isinstance(getattr(self, "current_session", None), dict) else {}
        scope = str(session.get("device_scope") or "local").strip().lower()
        if scope not in ("local", "remote"):
            scope = "local"
        device_id = str(session.get("device_id") or "").strip() if scope == "remote" else "local"
        if scope == "remote" and not device_id:
            scope = "local"
            device_id = "local"
        return {"device_scope": scope, "device_id": device_id, "target_key": lz.usage_pricing_target_key(scope, device_id)}

    def _build_usage_event(self, *, text="", model="", source="estimate"):
        api_card = self._current_api_card_info()
        target = self._current_usage_target_info()
        input_tokens = lz._estimate_tokens(text)
        event = {
            "ts": time.time(),
            "input_tokens": input_tokens,
            "output_tokens": 0,
            "total_tokens": input_tokens,
            "channel_id": str((self.current_session or {}).get("channel_id") or "launcher").strip().lower(),
            "model": str(model or self._current_llm_name() or "").strip(),
            "usage_source": str(source or "estimate").strip().lower() or "estimate",
            "billing_mode": "pending",
            **api_card,
            **target,
        }
        if not event.get("api_card_var"):
            event.pop("api_card_var", None)
            event.pop("api_card_label", None)
        return event

    def _finalize_usage_event_billing(self, event):
        if not isinstance(event, dict):
            return event
        if lz.usage_event_is_priced(event):
            return event
        scope = str(event.get("device_scope") or (self.current_session or {}).get("device_scope") or "local").strip().lower()
        device_id = str(event.get("device_id") or (self.current_session or {}).get("device_id") or "local").strip()
        api_var = str(event.get("api_card_var") or "").strip()
        if not api_var:
            api_card = self._current_api_card_info()
            api_var = str(api_card.get("api_card_var") or "").strip()
            if api_var:
                event["api_card_var"] = api_var
                event["api_card_label"] = str(api_card.get("api_card_label") or api_var).strip() or api_var
                event["llm_idx"] = int(api_card.get("llm_idx", event.get("llm_idx", 0)) or 0)
        if not api_var:
            event["billing_mode"] = "legacy_unpriced"
            return event
        pricing = lz.normalize_usage_pricing_config(getattr(self, "cfg", {}) or {})
        rule = lz.usage_price_rule(getattr(self, "cfg", {}) or {}, scope, device_id, api_var)
        snapshot = lz.usage_price_snapshot(rule, pricing.get("currency") or "USD")
        if not snapshot:
            event["billing_mode"] = "unpriced"
            return event
        return lz.apply_usage_price_snapshot(event, snapshot)

    def _mark_current_llm_index(self, combo_index: int):
        try:
            combo_pos = int(combo_index)
        except Exception:
            combo_pos = -1
        target_data = None
        if combo_pos >= 0 and getattr(self, "llm_combo", None) is not None:
            try:
                target_data = self.llm_combo.itemData(combo_pos)
            except Exception:
                target_data = None
        for pos, llm in enumerate(self.llms):
            current = False
            if target_data is not None:
                try:
                    current = int(llm.get("idx", pos) or pos) == int(target_data)
                except Exception:
                    current = str(llm.get("idx", pos)) == str(target_data)
            elif combo_pos >= 0:
                current = pos == combo_pos
            llm["current"] = bool(current)

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
        if not self.llms:
            self.llm_combo.addItem("未配置 LLM", -1)
        disabled_reason = self._bridge_llm_combo_disabled_reason()
        self._apply_bridge_widget_state(
            getattr(self, "llm_combo", None),
            not bool(disabled_reason),
            enabled_tooltip="切换当前会话使用的模型。",
            disabled_tooltip=disabled_reason,
        )
        self._ignore_llm_change = False
        floating_sync = getattr(self, "_sync_floating_llm_combo", None)
        if callable(floating_sync):
            floating_sync()

    def _sync_reasoning_effort_combo(self):
        combo = getattr(self, "reasoning_effort_combo", None)
        if combo is None:
            return
        self._ignore_reasoning_effort_change = True
        combo.clear()
        current_value = self._current_reasoning_effort_selection()
        current_idx = 0
        for idx, (value, label) in enumerate(_RUNTIME_REASONING_EFFORT_CHOICES):
            combo.addItem(label, value)
            if value == current_value:
                current_idx = idx
        combo.setCurrentIndex(current_idx)
        disabled_reason = self._bridge_reasoning_effort_combo_disabled_reason()
        self._apply_bridge_widget_state(
            combo,
            not bool(disabled_reason),
            enabled_tooltip="切换当前会话使用的思考强度。",
            disabled_tooltip=disabled_reason,
        )
        self._ignore_reasoning_effort_change = False
        floating_sync = getattr(self, "_sync_floating_reasoning_effort_combo", None)
        if callable(floating_sync):
            floating_sync()

    def _on_llm_changed(self, index: int):
        if self._ignore_llm_change or index < 0:
            return
        target = self.llm_combo.itemData(index)
        if target is None or int(target) < 0:
            return
        if self._is_remote_session():
            self._mark_current_llm_index(index)
            if isinstance(self.current_session, dict) and self.current_session.get("id"):
                self.current_session["llm_idx"] = int(target)
                snapshot = dict(self.current_session.get("snapshot") or {})
                snapshot["llm_idx"] = int(target)
                self.current_session["snapshot"] = snapshot
                try:
                    lz.save_session(self.agent_dir, self.current_session, touch=False)
                except Exception:
                    pass
            self._set_status("远程会话模型已记录，将在下一次发送时传给服务器 agant。")
            floating_sync = getattr(self, "_sync_floating_llm_combo", None)
            if callable(floating_sync):
                floating_sync()
            return
        if not self._bridge_ready:
            return
        self._send_cmd({"cmd": "switch_llm", "idx": int(target)})
        override = self._current_session_reasoning_effort_override()
        if override:
            self._send_cmd({"cmd": "switch_reasoning_effort", "reasoning_effort": override})
        floating_sync = getattr(self, "_sync_floating_llm_combo", None)
        if callable(floating_sync):
            floating_sync()

    def _on_reasoning_effort_changed(self, index: int):
        if getattr(self, "_ignore_reasoning_effort_change", False) or index < 0:
            return
        combo = getattr(self, "reasoning_effort_combo", None)
        if combo is None:
            return
        value = self._normalize_reasoning_effort_value(combo.itemData(index))
        self._pending_reasoning_effort_override = value or None
        if isinstance(self.current_session, dict):
            snapshot = dict(self.current_session.get("snapshot") or {})
            if value:
                self.current_session["reasoning_effort"] = value
                snapshot["reasoning_effort"] = value
                snapshot["reasoning_effort_source"] = "override"
            else:
                self.current_session.pop("reasoning_effort", None)
                snapshot.pop("reasoning_effort", None)
                snapshot.pop("reasoning_effort_source", None)
            self.current_session["snapshot"] = snapshot
            self._persist_session(self.current_session)
        if self._is_remote_session():
            self._set_status("远程会话思考强度已记录，将在下一次发送时传给服务器 agant。")
            floating_sync = getattr(self, "_sync_floating_reasoning_effort_combo", None)
            if callable(floating_sync):
                floating_sync()
            return
        self._bridge_reasoning_effort = value
        if not self._bridge_ready:
            return
        self._send_cmd({"cmd": "switch_reasoning_effort", "reasoning_effort": (value or None)})
        floating_sync = getattr(self, "_sync_floating_reasoning_effort_combo", None)
        if callable(floating_sync):
            floating_sync()

    def _current_llm_index(self) -> int:
        for pos, llm in enumerate(getattr(self, "llms", None) or []):
            if llm.get("current"):
                try:
                    return int(llm.get("idx", pos) or pos)
                except Exception:
                    return pos
        combo = getattr(self, "llm_combo", None)
        if combo is None:
            return 0
        idx = combo.currentIndex()
        if idx >= 0:
            data = self.llm_combo.itemData(idx)
            try:
                return int(data if data is not None else idx)
            except Exception:
                return idx
        return 0

    def _set_status(self, text: str):
        new_text = str(text or "")
        if new_text == "桥接进程已就绪。":
            try:
                last_done_at = float(getattr(self, "_last_task_complete_status_at", 0.0) or 0.0)
            except Exception:
                last_done_at = 0.0
            if last_done_at > 0 and (time.time() - last_done_at) <= 6.0:
                try:
                    current = str(self.status_label.text() or "").strip()
                except Exception:
                    current = ""
                if current in {"已完成。", "正在中断…", "已发送中断请求。"}:
                    return
        self.status_label.setText(new_text)
        self._refresh_info_tooltip()
        refresher = getattr(self, "_refresh_floating_chat_window", None)
        if callable(refresher):
            refresher()

    def _session_device_scope_id(self, session):
        data = session if isinstance(session, dict) else {}
        scope = str(data.get("device_scope") or "local").strip().lower()
        if scope not in ("local", "remote"):
            scope = "local"
        if scope == "remote":
            did = str(data.get("device_id") or "").strip()
            if did:
                return scope, did
        return "local", "local"

    def _is_remote_session(self, session=None):
        if isinstance(session, dict):
            data = session
        elif isinstance(self.current_session, dict) and self.current_session.get("id"):
            data = self.current_session
        else:
            resolver = getattr(self, "_current_device_context", None)
            if callable(resolver):
                try:
                    scope, _did = resolver()
                    return str(scope or "").strip().lower() == "remote"
                except Exception:
                    pass
            data = {}
        scope, _did = self._session_device_scope_id(data)
        return scope == "remote"

    def _remote_device_payload(self, session):
        getter = getattr(self, "_remote_device_by_id", None)
        if not callable(getter):
            raise RuntimeError("当前构建未包含远程设备配置能力。")
        _scope, did = self._session_device_scope_id(session)
        dev = getter(did)
        if not isinstance(dev, dict):
            raise RuntimeError("远程设备配置不存在，请先在“其他设备”里确认连接信息。")
        checker = getattr(self, "_remote_device_auto_ssh_enabled", None)
        if callable(checker):
            try:
                if not bool(checker(dev)):
                    raise RuntimeError("该远程设备已关闭自动 SSH，请先在“其他设备”中打开开关。")
            except RuntimeError:
                raise
            except Exception:
                pass
        key_path = str(dev.get("ssh_key_path") or "").strip()
        key_abs = lz._resolve_config_path(key_path) if key_path else ""
        if key_path and (not key_abs or not os.path.isfile(key_abs)):
            raise RuntimeError("远程设备 SSH 私钥路径无效，请先修正设备配置。")
        payload = {
            "host": str(dev.get("host") or "").strip(),
            "username": str(dev.get("username") or "").strip(),
            "port": int(dev.get("port") or 22),
            "password": str(dev.get("password") or "").strip(),
            "key_abs": key_abs,
        }
        if not payload["host"] or not payload["username"]:
            raise RuntimeError("远程设备缺少 host/username。")
        if (not payload["password"]) and (not payload["key_abs"]):
            raise RuntimeError("远程设备至少需要 SSH 私钥或密码。")
        return dev, payload

    def _remote_bridge_source_text(self):
        bridge_path = lz._bridge_script_path()
        if not os.path.isfile(bridge_path):
            raise RuntimeError(f"bridge.py 不存在：{bridge_path}")
        with open(bridge_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    def _remote_stage_bridge_runtime(self, client, remote_dir: str):
        base_dir = normalize_remote_agent_dir(remote_dir)
        runtime_dir = posixpath.join(str(base_dir).rstrip("/"), "temp", "launcher_runtime")
        rc, _out, err = self._vps_exec_remote(client, f"mkdir -p {shlex.quote(runtime_dir)}", timeout=20)
        if rc != 0:
            raise RuntimeError(str(err or "创建远端 bridge 运行目录失败。").strip() or "创建远端 bridge 运行目录失败。")
        remote_bridge = posixpath.join(runtime_dir, "bridge.py")
        text = self._remote_bridge_source_text()
        sftp = client.open_sftp()
        try:
            with sftp.open(remote_bridge, "wb") as fp:
                fp.write(text.encode("utf-8"))
        finally:
            try:
                sftp.close()
            except Exception:
                pass
        return remote_bridge

    def _remote_device_stage_root(self, device) -> str:
        dev = device if isinstance(device, dict) else {}
        raw = str(dev.get("id") or dev.get("host") or "remote-device").strip() or "remote-device"
        safe = "".join(ch for ch in raw if (ch.isalnum() or ch in ("_", "-")))
        if not safe:
            safe = uuid.uuid4().hex[:12]
        return f"/tmp/genericagent_launcher_remote/{safe}"

    def _remote_stage_bridge_runtime_for_device(self, client, device):
        dev = device if isinstance(device, dict) else {}
        remote_dir = remote_device_agent_dir(dev, username=dev.get("username"))
        if remote_device_agent_mode(dev) != "docker":
            return self._remote_stage_bridge_runtime(client, remote_dir)
        runtime_dir = posixpath.join(str(remote_dir).rstrip("/"), "temp", "launcher_runtime")
        host_stage_root = self._remote_device_stage_root(dev)
        host_stage_fp = posixpath.join(host_stage_root, "bridge.py")
        container = remote_device_container_name(dev)
        rc, _out, err = self._vps_exec_remote(client, f"mkdir -p {shlex.quote(host_stage_root)}", timeout=20)
        if rc != 0:
            raise RuntimeError(str(err or "创建远端 bridge 暂存目录失败。").strip() or "创建远端 bridge 暂存目录失败。")
        sftp = client.open_sftp()
        try:
            with sftp.open(host_stage_fp, "wb") as fp:
                fp.write(self._remote_bridge_source_text().encode("utf-8"))
        finally:
            try:
                sftp.close()
            except Exception:
                pass
        cmd = (
            f"docker exec {shlex.quote(container)} sh -lc {shlex.quote('mkdir -p ' + shlex.quote(runtime_dir))} && "
            f"docker cp {shlex.quote(host_stage_fp)} {shlex.quote(container + ':' + posixpath.join(runtime_dir, 'bridge.py'))}"
        )
        rc, _out, err = self._vps_exec_remote(client, cmd, timeout=30)
        if rc != 0:
            raise RuntimeError(str(err or "写入容器内 bridge 运行文件失败。").strip() or "写入容器内 bridge 运行文件失败。")
        try:
            self._vps_exec_remote(client, f"rm -f {shlex.quote(host_stage_fp)} >/dev/null 2>&1 || true", timeout=10)
        except Exception:
            pass
        return posixpath.join(runtime_dir, "bridge.py")

    def _remote_stage_chat_images(self, client, remote_dir: str, images):
        local_images = [str(p or "").strip() for p in (images or []) if os.path.isfile(str(p or "").strip())]
        if not local_images:
            return []
        base_dir = normalize_remote_agent_dir(remote_dir)
        upload_dir = posixpath.join(str(base_dir).rstrip("/"), "temp", "launcher_runtime", "chat_uploads")
        rc, _out, err = self._vps_exec_remote(client, f"mkdir -p {shlex.quote(upload_dir)}", timeout=20)
        if rc != 0:
            raise RuntimeError(str(err or "创建远端图片上传目录失败。").strip() or "创建远端图片上传目录失败。")
        remote_paths = []
        sftp = client.open_sftp()
        try:
            for local_fp in local_images:
                name = f"{uuid.uuid4().hex[:12]}_{os.path.basename(local_fp)}"
                remote_fp = posixpath.join(upload_dir, name)
                sftp.put(local_fp, remote_fp)
                remote_paths.append(remote_fp)
        finally:
            try:
                sftp.close()
            except Exception:
                pass
        return remote_paths

    def _remote_stage_chat_images_for_device(self, client, device, images):
        dev = device if isinstance(device, dict) else {}
        remote_dir = remote_device_agent_dir(dev, username=dev.get("username"))
        if remote_device_agent_mode(dev) != "docker":
            return self._remote_stage_chat_images(client, remote_dir, images)
        local_images = [str(p or "").strip() for p in (images or []) if os.path.isfile(str(p or "").strip())]
        if not local_images:
            return []
        upload_dir = posixpath.join(str(remote_dir).rstrip("/"), "temp", "launcher_runtime", "chat_uploads")
        host_stage_root = self._remote_device_stage_root(dev)
        container = remote_device_container_name(dev)
        rc, _out, err = self._vps_exec_remote(client, f"mkdir -p {shlex.quote(host_stage_root)}", timeout=20)
        if rc != 0:
            raise RuntimeError(str(err or "创建远端图片暂存目录失败。").strip() or "创建远端图片暂存目录失败。")
        rc, _out, err = self._vps_exec_remote(
            client,
            f"docker exec {shlex.quote(container)} sh -lc {shlex.quote('mkdir -p ' + shlex.quote(upload_dir))}",
            timeout=20,
        )
        if rc != 0:
            raise RuntimeError(str(err or "创建容器内图片上传目录失败。").strip() or "创建容器内图片上传目录失败。")
        remote_paths = []
        sftp = client.open_sftp()
        try:
            for local_fp in local_images:
                name = f"{uuid.uuid4().hex[:12]}_{os.path.basename(local_fp)}"
                host_stage_fp = posixpath.join(host_stage_root, name)
                container_fp = posixpath.join(upload_dir, name)
                sftp.put(local_fp, host_stage_fp)
                cmd = f"docker cp {shlex.quote(host_stage_fp)} {shlex.quote(container + ':' + container_fp)}"
                rc, _out, err = self._vps_exec_remote(client, cmd, timeout=30)
                if rc != 0:
                    raise RuntimeError(str(err or "复制图片到容器失败。").strip() or "复制图片到容器失败。")
                try:
                    self._vps_exec_remote(client, f"rm -f {shlex.quote(host_stage_fp)} >/dev/null 2>&1 || true", timeout=10)
                except Exception:
                    pass
                remote_paths.append(container_fp)
        finally:
            try:
                sftp.close()
            except Exception:
                pass
        return remote_paths

    def _remote_cleanup_files(self, client, remote_paths, *, device=None):
        paths = [str(p or "").strip() for p in (remote_paths or []) if str(p or "").strip()]
        if (client is None) or (not paths):
            return
        if remote_device_agent_mode(device) == "docker":
            container = remote_device_container_name(device)
            inner = "rm -f " + " ".join(shlex.quote(path) for path in paths) + " >/dev/null 2>&1 || true"
            cmd = f"docker exec {shlex.quote(container)} sh -lc {shlex.quote(inner)}"
        else:
            cmd = "rm -f " + " ".join(shlex.quote(path) for path in paths) + " >/dev/null 2>&1 || true"
        try:
            self._vps_exec_remote(client, cmd, timeout=20)
        except Exception:
            pass

    def _remote_emit_bridge_event(self, ev, *, session_id=""):
        event = dict(ev or {})
        if session_id and not str(event.get("session_id") or "").strip():
            event["session_id"] = str(session_id)
        self._event_queue.put(event)

    def _remote_exec_chat_turn(self, session, prompt_text: str, images):
        dev, payload = self._remote_device_payload(session)
        remote_dir = remote_device_agent_dir(dev, username=dev.get("username"))
        python_cmd = str(dev.get("python_cmd") or "python3").strip() or "python3"
        agent_mode = remote_device_agent_mode(dev)
        container = remote_device_container_name(dev)
        client, err_msg, detail, missing = self._open_vps_ssh_client(payload, timeout=12)
        if client is None:
            if missing:
                raise RuntimeError("缺少 paramiko，无法连接远程设备。")
            text = (err_msg or "SSH 连接失败。") + (f"\n{detail}" if detail else "")
            raise RuntimeError(normalize_ssh_error_text(text, context="远端 SSH 连接"))
        remote_cleanup = []
        try:
            remote_bridge = self._remote_stage_bridge_runtime_for_device(client, dev)
            remote_images = self._remote_stage_chat_images_for_device(client, dev, images)
            remote_cleanup.extend(remote_images)
            try:
                inner_cmd = (
                    "set -e; "
                    f"cd {shlex.quote(remote_dir)}; "
                    f"PY_BIN={shlex.quote(python_cmd)}; "
                    "if ! command -v \"$PY_BIN\" >/dev/null 2>&1; then "
                    "if command -v python3 >/dev/null 2>&1; then PY_BIN=python3; "
                    "elif command -v python >/dev/null 2>&1; then PY_BIN=python; "
                    "else echo '{\"event\":\"error\",\"msg\":\"远端设备未检测到 Python，可在设备配置里指定 python_cmd。\"}'; exit 62; fi; "
                    "fi; "
                    "export PYTHONIOENCODING=utf-8 PYTHONUTF8=1; "
                    f"\"$PY_BIN\" -u {shlex.quote(remote_bridge)} {shlex.quote(remote_dir)}"
                )
                exec_cmd = inner_cmd
                if agent_mode == "docker":
                    exec_cmd = f"docker exec -i {shlex.quote(container)} sh -lc {shlex.quote(inner_cmd)}"
                stdin, stdout, stderr = client.exec_command(
                    exec_cmd,
                    timeout=7200,
                    get_pty=False,
                )
            except Exception as e:
                detail = normalize_ssh_error_text(str(e), context="远端 bridge 连接")
                raise RuntimeError(f"启动远端 bridge 失败：{detail}")

            channel = stdout.channel
            try:
                channel.settimeout(None)
            except Exception:
                pass
            line_queue: queue.Queue = queue.Queue()
            stderr_lines = []

            def decode_line(raw):
                if isinstance(raw, bytes):
                    return raw.decode("utf-8", errors="replace")
                return str(raw or "")

            def read_stdout():
                try:
                    for raw in stdout:
                        text = decode_line(raw).rstrip("\r\n")
                        if text:
                            line_queue.put(("stdout", text))
                except Exception as e:
                    line_queue.put(("stdout_error", str(e)))

            def read_stderr():
                try:
                    for raw in stderr:
                        text = decode_line(raw).rstrip("\r\n")
                        if not text:
                            continue
                        stderr_lines.append(text)
                        if len(stderr_lines) > 80:
                            del stderr_lines[:-80]
                        line_queue.put(("stderr", text))
                except Exception as e:
                    line_queue.put(("stderr_error", str(e)))

            threading.Thread(target=read_stdout, daemon=True, name="remote-bridge-stdout").start()
            threading.Thread(target=read_stderr, daemon=True, name="remote-bridge-stderr").start()

            session_id = str((session or {}).get("id") or "").strip()
            state_payload = {
                "cmd": "set_state",
                "backend_history": list((session or {}).get("backend_history") or []),
                "agent_history": list((session or {}).get("agent_history") or []),
                "llm_idx": (session or {}).get("llm_idx", (((session or {}).get("snapshot") or {}).get("llm_idx"))),
            }
            include_reasoning, reasoning_value = self._session_reasoning_effort_payload(session)
            if include_reasoning:
                state_payload["reasoning_effort"] = reasoning_value
            send_payload = {
                "cmd": "send",
                "text": str(prompt_text or ""),
                "images": list(remote_images or []),
                "session_id": session_id,
            }

            def send_cmd(obj):
                text = json.dumps(obj if isinstance(obj, dict) else {}, ensure_ascii=False) + "\n"
                try:
                    stdin.write(text)
                except TypeError:
                    stdin.write(text.encode("utf-8"))
                stdin.flush()

            ready_seen = False
            state_loaded = False
            done_text = ""
            done_seen = False
            post_done_deadline = 0.0
            bridge_errors = []

            while True:
                if done_seen and post_done_deadline > 0 and time.time() >= post_done_deadline:
                    break
                try:
                    kind, text = line_queue.get(timeout=0.2)
                except queue.Empty:
                    if channel.exit_status_ready():
                        break
                    continue
                if kind == "stderr":
                    continue
                if kind.endswith("_error"):
                    bridge_errors.append(normalize_ssh_error_text(str(text or "").strip(), context="远端 bridge 连接"))
                    continue
                ev = self._remote_parse_bridge_event_text(text)
                if not isinstance(ev, dict):
                    bridge_errors.append(str(text or "").strip())
                    continue
                et = str(ev.get("event") or "").strip()
                if et == "log":
                    continue
                if et == "ready":
                    ready_seen = True
                    send_cmd(state_payload)
                    continue
                if et == "state_loaded":
                    state_loaded = True
                    send_cmd(send_payload)
                    continue
                if et == "next":
                    self._remote_emit_bridge_event({"event": "remote_next", "text": ev.get("text", "")}, session_id=session_id)
                    continue
                if et == "done":
                    done_text = str(ev.get("text") or "").strip()
                    done_seen = True
                    post_done_deadline = time.time() + 2.5
                    payload = {
                        "event": "remote_done",
                        "text": done_text,
                        "usage": ev.get("usage") if isinstance(ev.get("usage"), dict) else None,
                    }
                    self._remote_emit_bridge_event(payload, session_id=session_id)
                    continue
                if et == "turn_snapshot":
                    payload = {
                        "event": "remote_turn_snapshot",
                        "backend_history": ev.get("backend_history") or [],
                        "agent_history": ev.get("agent_history") or [],
                        "llm_idx": ev.get("llm_idx"),
                        "reasoning_effort": ev.get("reasoning_effort"),
                        "process_pid": ev.get("process_pid"),
                        "snapshot_ts": ev.get("snapshot_ts"),
                    }
                    self._remote_emit_bridge_event(payload, session_id=(ev.get("session_id") or session_id))
                    if done_seen:
                        post_done_deadline = time.time() + 0.2
                    continue
                if et == "state":
                    payload = {
                        "event": "remote_state",
                        "backend_history": ev.get("backend_history") or [],
                        "agent_history": ev.get("agent_history") or [],
                        "llm_idx": ev.get("llm_idx"),
                        "reasoning_effort": ev.get("reasoning_effort"),
                    }
                    self._remote_emit_bridge_event(payload, session_id=(ev.get("session_id") or session_id))
                    continue
                if et == "error":
                    msg = str(ev.get("msg") or "远端 bridge 执行失败。").strip() or "远端 bridge 执行失败。"
                    trace = str(ev.get("trace") or "").strip()
                    if trace:
                        msg = msg + "\n" + trace
                    raise RuntimeError(msg)

            try:
                send_cmd({"cmd": "quit"})
            except Exception:
                pass

            if not ready_seen:
                tail = "\n".join([line for line in bridge_errors if line][-10:] + stderr_lines[-10:]).strip()
                raise RuntimeError(tail or "远端 bridge 未成功启动。")
            if ready_seen and (not state_loaded):
                tail = "\n".join([line for line in bridge_errors if line][-10:] + stderr_lines[-10:]).strip()
                raise RuntimeError(tail or "远端 bridge 状态恢复失败。")
            if not done_seen:
                tail = "\n".join([line for line in bridge_errors if line][-10:] + stderr_lines[-10:]).strip()
                raise RuntimeError(tail or "远端聊天未返回完成事件。")
            if not done_text:
                raise RuntimeError("远端返回为空，请检查服务器日志。")
            return done_text
        finally:
            self._remote_cleanup_files(client, remote_cleanup, device=dev)
            try:
                client.close()
            except Exception:
                pass

    def _submit_remote_user_message(self, text: str, attachments=None, *, source_editor=None):
        clean_text = str(text or "").strip()
        files = [
            str(item.get("path") or "").strip()
            for item in (attachments or [])
            if os.path.isfile(str((item or {}).get("path") or "").strip())
        ]
        if not clean_text and not files:
            return False
        self._last_activity = time.time()
        self._ensure_session(clean_text)
        if source_editor is not None:
            try:
                source_editor.clear()
            except Exception:
                pass
        self._selected_session_id = self.current_session.get("id")
        display_text = clean_text or f"[已发送 {len(files)} 张图片]"
        user_row = self._add_message_row("user", display_text, finished=True, auto_scroll=False)
        self.current_session.setdefault("bubbles", []).append({"role": "user", "text": display_text})
        self._stream_row = self._add_message_row("assistant", "", finished=False, auto_scroll=False)
        anchor_setter = getattr(self, "_set_current_turn_user_row", None)
        if callable(anchor_setter):
            anchor_setter(user_row)
        self._user_scrolled_up = False
        follower = getattr(self, "_set_follow_latest_user", None)
        if callable(follower):
            follower(True)
        self._scroll_row_to_top(user_row, preserve_scroll_state=True)
        self._busy = True
        self._abort_requested = False
        self._current_stream_text = ""
        self._pending_stream_text = None
        self.send_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self._set_status("远程生成中…")
        self._refresh_composer_enabled()

        usage = self.current_session.get("token_usage") or {}
        event = self._build_usage_event(text=clean_text, model="remote", source="estimate")
        usage.setdefault("events", []).append(event)
        usage["last_model"] = "remote"
        self.current_session["token_usage"] = usage
        self._active_token_event_ts = event["ts"]
        self._persist_session(self.current_session)
        self._refresh_token_label()
        self._update_stream_row_tokens(live=True)

        session_id = str(self.current_session.get("id") or "")
        session_copy = _session_copy(self.current_session)

        def worker():
            try:
                self._remote_exec_chat_turn(session_copy, clean_text, files)
            except Exception as e:
                self._event_queue.put({"event": "remote_error", "session_id": session_id, "msg": str(e)})

        threading.Thread(target=worker, name="remote-chat-turn", daemon=True).start()
        self._active_turn_attachments_data = [dict(item) for item in (attachments or []) if os.path.isfile(str((item or {}).get("path") or "").strip())]
        self._pending_input_attachments_data = []
        self._refresh_input_attachment_bar()
        refresher = getattr(self, "_refresh_floating_chat_window", None)
        if callable(refresher):
            refresher()
        return True

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
        py, py_err = self._resolve_bridge_python()
        if not py:
            raise RuntimeError(py_err or "未找到可用的系统 Python。")
        self._remember_bridge_python(py)
        bridge = lz._bridge_script_path()
        if not os.path.isfile(bridge):
            raise RuntimeError(f"bridge.py 不存在：{bridge}")
        self._bridge_ready = False
        self.llms = []
        self._bridge_reasoning_effort = ""
        self._sync_llm_combo()
        self._sync_reasoning_effort_combo()
        self._set_status("正在启动桥接进程…")
        self._stderr_buf = []
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        bridge_env = lz._external_subprocess_env()
        self.bridge_proc = lz._popen_external_subprocess(
            [py, "-u", bridge, self.agent_dir],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.agent_dir,
            env=bridge_env,
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
                        self._event_queue.put({"event": "bridge_text", "text": line})
                        continue
                    if isinstance(ev, dict):
                        self._event_queue.put(ev)
                    else:
                        self._event_queue.put({"event": "bridge_text", "text": str(ev)})
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
        self._clear_active_turn_attachments()
        if proc is None:
            return
        try:
            lz.terminate_process_tree(proc, quit_line='{"cmd":"quit"}\n', terminate_timeout=0.8, kill_timeout=0.8)
        except Exception:
            pass

    def _restart_bridge(self):
        self._stop_bridge()
        self._safe_start_bridge()

    def _submit_user_message(self, text: str, attachments=None, *, source_editor=None):
        text = str(text or "").strip()
        attachments = [
            dict(item)
            for item in (attachments or [])
            if os.path.isfile(str((item or {}).get("path") or "").strip())
        ]
        if not text and not attachments:
            return False
        if self._handle_local_slash_command(text, source_editor=source_editor):
            return False
        if self._is_channel_process_session():
            QMessageBox.information(self, "不可发送", "当前选中的是渠道进程会话，只能回顾快照，不能从这里继续发送消息。")
            return False
        if self._is_remote_session(self.current_session):
            return self._submit_remote_user_message(text, attachments=attachments, source_editor=source_editor)
        self._last_activity = time.time()
        if not self._bridge_ready:
            QMessageBox.information(self, "尚未就绪", "桥接进程还没准备好，请稍候再发送。")
            return False
        self._ensure_session(text)
        if source_editor is not None:
            try:
                source_editor.clear()
            except Exception:
                pass
        self._selected_session_id = self.current_session.get("id")
        display_text = text or f"[已发送 {len(attachments)} 张图片]"
        user_row = self._add_message_row("user", display_text, finished=True, auto_scroll=False)
        self.current_session.setdefault("bubbles", []).append({"role": "user", "text": display_text})
        self._stream_row = self._add_message_row("assistant", "", finished=False, auto_scroll=False)
        anchor_setter = getattr(self, "_set_current_turn_user_row", None)
        if callable(anchor_setter):
            anchor_setter(user_row)
        self._user_scrolled_up = False
        follower = getattr(self, "_set_follow_latest_user", None)
        if callable(follower):
            follower(True)
        self._scroll_row_to_top(user_row, preserve_scroll_state=True)
        self._busy = True
        self._abort_requested = False
        self._current_stream_text = ""
        self._pending_stream_text = None
        self.send_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._set_status("生成中…")
        self._refresh_composer_enabled()

        usage = self.current_session.get("token_usage") or {}
        event = self._build_usage_event(text=text, model=self._current_llm_name(), source="estimate")
        usage.setdefault("events", []).append(event)
        usage["last_model"] = event["model"]
        self.current_session["token_usage"] = usage
        self._active_token_event_ts = event["ts"]
        self._persist_session(self.current_session)
        self._refresh_token_label()
        self._update_stream_row_tokens(live=True)
        try:
            self._send_cmd(
                {
                    "cmd": "send",
                    "text": text,
                    "images": [str(item.get("path") or "").strip() for item in attachments],
                    "session_id": self.current_session.get("id"),
                }
            )
            self._active_turn_attachments_data = attachments
            self._pending_input_attachments_data = []
            self._refresh_input_attachment_bar()
            refresher = getattr(self, "_refresh_floating_chat_window", None)
            if callable(refresher):
                refresher()
            return True
        except Exception as e:
            discard_stream = getattr(self, "_discard_stream_row", None)
            if callable(discard_stream):
                discard_stream(self._stream_row)
            else:
                self._stream_row = None
            self._busy = False
            self._abort_requested = False
            self._current_stream_text = ""
            self._pending_stream_text = None
            self._active_token_event_ts = None
            follower = getattr(self, "_set_follow_latest_user", None)
            if callable(follower):
                follower(False)
            anchor_clearer = getattr(self, "_clear_current_turn_user_row", None)
            if callable(anchor_clearer):
                anchor_clearer()
            self.send_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self._refresh_composer_enabled()
            self._refresh_token_label()
            QMessageBox.critical(self, "发送失败", str(e))
            refresher = getattr(self, "_refresh_floating_chat_window", None)
            if callable(refresher):
                refresher()
            return False

    def _handle_send(self):
        self._submit_user_message(
            self.input_box.toPlainText().strip(),
            attachments=self._pending_input_attachments(),
            source_editor=self.input_box,
        )

    def _stream_update(self, cumulative_text: str):
        self._pending_stream_text = cumulative_text or ""
        self._current_stream_text = cumulative_text or ""
        self._refresh_token_label()
        self._update_stream_row_tokens(live=True)
        if not self._stream_flush_timer.isActive():
            self._stream_flush_timer.start(90)

    def _flush_stream_render(self):
        if self._stream_row is None:
            return
        pending = self._pending_stream_text or ""
        if getattr(self._stream_row, "_text", "") == pending and getattr(self._stream_row, "_finished", False) is False:
            self._update_stream_row_tokens(live=True)
            return
        self._stream_row.update_content(pending, finished=False)
        self._update_stream_row_tokens(live=True)
        sync_view = getattr(self, "_sync_current_turn_view", None)
        if callable(sync_view):
            sync_view()
        else:
            self._scroll_to_bottom()
        refresher = getattr(self, "_refresh_floating_chat_window", None)
        if callable(refresher):
            refresher()

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
        was_aborted = bool(self._abort_requested)
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
        self._last_task_complete_status_at = float(time.time())
        self._set_status("已完成。")
        self._refresh_composer_enabled()
        self._clear_active_turn_attachments()
        anchor_clearer = getattr(self, "_clear_current_turn_user_row", None)
        if callable(anchor_clearer):
            anchor_clearer()
        if not was_aborted:
            self._notify_reply_done(final_text)

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
            self._finalize_usage_event_billing(target)
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
            if not self._is_remote_session(self.current_session):
                self._request_backend_state(self.current_session.get("id"))
        self._refresh_token_label()
        follower = getattr(self, "_set_follow_latest_user", None)
        if callable(follower):
            follower(False)
        sync_view = getattr(self, "_sync_current_turn_view", None)
        if callable(sync_view):
            sync_view(force=True)
        refresher = getattr(self, "_refresh_floating_chat_window", None)
        if callable(refresher):
            refresher()
        else:
            self._scroll_to_bottom()

    def _abort(self):
        if not self._busy or self._abort_requested:
            return
        if self._is_remote_session(self.current_session):
            QMessageBox.information(self, "提示", "远程会话当前不支持即时中断，请等待该轮完成。")
            return
        self._abort_requested = True
        self.stop_btn.setEnabled(False)
        self._set_status("正在中断…")
        self._refresh_composer_enabled()
        refresher = getattr(self, "_refresh_floating_chat_window", None)
        if callable(refresher):
            refresher()
        try:
            self._send_cmd({"cmd": "abort"})
        except Exception as e:
            QMessageBox.warning(self, "中断失败", str(e))

    def _drain_events(self):
        while True:
            try:
                ev = self._event_queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(ev, str):
                text = ev.strip()
                if not text:
                    continue
                try:
                    parsed = json.loads(text)
                except Exception:
                    ev = {"event": "bridge_text", "text": text}
                else:
                    ev = parsed if isinstance(parsed, dict) else {"event": "bridge_text", "text": str(parsed)}
            elif not isinstance(ev, dict):
                ev = {"event": "bridge_text", "text": str(ev)}
            self._handle_event(ev)
        proc = self.bridge_proc
        if proc is not None and proc.poll() is not None and not self._bridge_ready and not self._busy:
            self._clear_active_turn_attachments()
            stderr_tail = "\n".join(self._stderr_buf[-20:]) or "(空)"
            self._set_status("桥接进程已退出。")
            QMessageBox.critical(self, "桥接进程退出", f"启动失败或已退出。\n\nstderr 尾部：\n{stderr_tail}")
            self.bridge_proc = None

    def _handle_event(self, ev):
        if not isinstance(ev, dict):
            return
        et = ev.get("event")
        if et == "bridge_text":
            return
        if et == "subagent_runtime_count":
            target_key = str(ev.get("target_key") or "").strip()
            if str(getattr(self, "_subagent_runtime_refresh_inflight_key", "") or "").strip() == target_key:
                self._subagent_runtime_refresh_inflight_key = ""
            applier = getattr(self, "_apply_subagent_runtime_count", None)
            if callable(applier):
                applier(
                    ev.get("count"),
                    target_key=target_key,
                    scanned_at=ev.get("scanned_at"),
                )
            return
        if et == "remote_done":
            target_sid = str(ev.get("session_id") or "").strip()
            current_sid = str((self.current_session or {}).get("id") or "").strip()
            if target_sid and current_sid and target_sid != current_sid:
                return
            provider_usage = ev.get("usage") if isinstance(ev.get("usage"), dict) else None
            self._stream_done(ev.get("text", ""), provider_usage=provider_usage)
            return
        if et == "remote_next":
            target_sid = str(ev.get("session_id") or "").strip()
            current_sid = str((self.current_session or {}).get("id") or "").strip()
            if target_sid and current_sid and target_sid != current_sid:
                return
            self._stream_update(ev.get("text", ""))
            return
        if et == "remote_state":
            self._apply_state_to_session(
                ev.get("session_id") or ((self.current_session or {}).get("id")),
                ev.get("backend_history") or [],
                ev.get("agent_history") or [],
                llm_idx=ev.get("llm_idx"),
                reasoning_effort=ev.get("reasoning_effort"),
            )
            return
        if et == "remote_turn_snapshot":
            self._apply_state_to_session(
                ev.get("session_id") or ((self.current_session or {}).get("id")),
                ev.get("backend_history") or [],
                ev.get("agent_history") or [],
                llm_idx=ev.get("llm_idx"),
                reasoning_effort=ev.get("reasoning_effort"),
                process_pid=ev.get("process_pid"),
                snapshot_ts=ev.get("snapshot_ts"),
            )
            return
        if et == "remote_error":
            target_sid = str(ev.get("session_id") or "").strip()
            current_sid = str((self.current_session or {}).get("id") or "").strip()
            if target_sid and current_sid and target_sid != current_sid:
                return
            msg = str(ev.get("msg") or "远程执行失败。").strip() or "远程执行失败。"
            self._clear_active_turn_attachments()
            discard_stream = getattr(self, "_discard_stream_row", None)
            if callable(discard_stream):
                discard_stream(self._stream_row)
            else:
                self._stream_row = None
            self._busy = False
            self._abort_requested = False
            self._current_stream_text = ""
            self._pending_stream_text = None
            self._active_token_event_ts = None
            follower = getattr(self, "_set_follow_latest_user", None)
            if callable(follower):
                follower(False)
            anchor_clearer = getattr(self, "_clear_current_turn_user_row", None)
            if callable(anchor_clearer):
                anchor_clearer()
            self.send_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self._set_status(msg)
            self._refresh_composer_enabled()
            self._refresh_token_label()
            refresher = getattr(self, "_refresh_floating_chat_window", None)
            if callable(refresher):
                refresher()
            QMessageBox.warning(self, "远程聊天失败", msg)
            return
        if et == "launcher_autonomous_trigger":
            if not self._busy:
                self._send(text=self.AUTO_TASK_TEXT, auto=True)
            return
        if self._handle_download_event(ev):
            return
        if et == "ready":
            self._bridge_ready = True
            self.llms = ev.get("llms", [])
            self._bridge_reasoning_effort = self._normalize_reasoning_effort_value(ev.get("reasoning_effort"))
            self._sync_llm_combo()
            self._sync_reasoning_effort_combo()
            self._set_status("桥接进程已就绪。")
            if self._pending_state_session:
                data = _session_copy(self._pending_state_session)
                self.current_session = data
                self._render_session(data)
                payload = {
                    "cmd": "set_state",
                    "backend_history": data.get("backend_history") or [],
                    "agent_history": data.get("agent_history") or [],
                    "llm_idx": data.get("llm_idx", ((data.get("snapshot") or {}).get("llm_idx"))),
                }
                include_reasoning, reasoning_value = self._session_reasoning_effort_payload(data)
                if include_reasoning:
                    payload["reasoning_effort"] = reasoning_value
                self._send_cmd(payload)
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
                reasoning_effort=ev.get("reasoning_effort"),
            )
            return
        if et == "turn_snapshot":
            self._apply_state_to_session(
                ev.get("session_id") or ((self.current_session or {}).get("id")),
                ev.get("backend_history") or [],
                ev.get("agent_history") or [],
                llm_idx=ev.get("llm_idx"),
                reasoning_effort=ev.get("reasoning_effort"),
                process_pid=ev.get("process_pid"),
                snapshot_ts=ev.get("snapshot_ts"),
            )
            return
        if et == "llm_switched":
            self.llms = ev.get("llms", self.llms)
            self._bridge_reasoning_effort = self._normalize_reasoning_effort_value(ev.get("reasoning_effort"))
            self._sync_llm_combo()
            self._sync_reasoning_effort_combo()
            if self.current_session:
                self.current_session["llm_idx"] = int(self._current_llm_index() or 0)
                self._persist_session(self.current_session)
            self._set_status("模型已切换。")
            self._refresh_composer_enabled()
            return
        if et == "reasoning_effort_switched":
            self._bridge_reasoning_effort = self._normalize_reasoning_effort_value(ev.get("reasoning_effort"))
            if isinstance(self.current_session, dict):
                explicit_override = bool("reasoning_effort" in self.current_session or self._session_snapshot_reasoning_effort_source(self.current_session) == "override")
                if explicit_override and self._bridge_reasoning_effort:
                    self.current_session["reasoning_effort"] = self._bridge_reasoning_effort
                else:
                    self.current_session.pop("reasoning_effort", None)
                snapshot = dict(self.current_session.get("snapshot") or {})
                if self._bridge_reasoning_effort:
                    snapshot["reasoning_effort"] = self._bridge_reasoning_effort
                    snapshot["reasoning_effort_source"] = "override" if explicit_override else "runtime"
                else:
                    snapshot.pop("reasoning_effort", None)
                    snapshot.pop("reasoning_effort_source", None)
                self.current_session["snapshot"] = snapshot
                self._persist_session(self.current_session)
            self._sync_reasoning_effort_combo()
            self._set_status("思考强度已切换。")
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
            trace = ev.get("trace", "")
            self._clear_active_turn_attachments()
            discard_stream = getattr(self, "_discard_stream_row", None)
            if callable(discard_stream):
                discard_stream(self._stream_row)
            else:
                self._stream_row = None
            self._busy = False
            self._abort_requested = False
            self._current_stream_text = ""
            self._pending_stream_text = None
            self._active_token_event_ts = None
            follower = getattr(self, "_set_follow_latest_user", None)
            if callable(follower):
                follower(False)
            anchor_clearer = getattr(self, "_clear_current_turn_user_row", None)
            if callable(anchor_clearer):
                anchor_clearer()
            self.send_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self._set_status(f"错误: {msg}")
            self._refresh_composer_enabled()
            self._refresh_token_label()
            refresher = getattr(self, "_refresh_floating_chat_window", None)
            if callable(refresher):
                refresher()
            if trace:
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Warning)
                box.setWindowTitle("桥接错误")
                box.setText(msg or "未知错误")
                box.setDetailedText(str(trace))
                box.exec()
            else:
                QMessageBox.warning(self, "桥接错误", msg or "未知错误")
