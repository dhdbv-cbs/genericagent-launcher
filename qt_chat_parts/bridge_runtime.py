from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import uuid

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QStyle, QSystemTrayIcon, QVBoxLayout, QWidget

from launcher_app import core as lz
from launcher_app.theme import C

from .common import _session_copy


class BridgeRuntimeMixin:
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
        pending_items = list(self._pending_input_attachments())
        active_items = list(self._active_turn_attachments())
        active_mode = bool(active_items)
        items = active_items if active_mode else pending_items
        host.setVisible(bool(items))
        if list_widget is not None:
            list_widget.setVisible(bool(items))
        if not items:
            summary.setText("")
            layout.invalidate()
            self._refresh_attachment_geometry(host)
            return
        if active_mode:
            summary.setText(f"本轮已附带 {len(items)} 张图片。当前回复结束后会自动清除。")
        else:
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
            remove_btn.setEnabled(not active_mode)
            if not active_mode:
                remove_btn.clicked.connect(lambda _=False, i=idx: self._remove_pending_input_attachment(i))
            box.addWidget(remove_btn, 0)
            layout.addWidget(row)
        layout.invalidate()
        self._refresh_attachment_geometry(host)

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
        tray = self._ensure_reply_notify_tray()
        if tray is None:
            return
        msg = "AI 回复已完成"
        preview = str(final_text or "").strip().replace("\r", " ").replace("\n", " ")
        if preview:
            if len(preview) > 72:
                preview = preview[:72].rstrip() + "…"
            msg = f"{msg}：{preview}"
        tray.showMessage("GenericAgent 启动器", msg, QSystemTrayIcon.Information, 1500)

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
        floating_sync = getattr(self, "_sync_floating_llm_combo", None)
        if callable(floating_sync):
            floating_sync()

    def _on_llm_changed(self, index: int):
        if self._ignore_llm_change or index < 0 or not self._bridge_ready:
            return
        target = self.llm_combo.itemData(index)
        if target is None or int(target) < 0:
            return
        self._send_cmd({"cmd": "switch_llm", "idx": int(target)})
        floating_sync = getattr(self, "_sync_floating_llm_combo", None)
        if callable(floating_sync):
            floating_sync()

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
        refresher = getattr(self, "_refresh_floating_chat_window", None)
        if callable(refresher):
            refresher()

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

    def _submit_user_message(self, text: str, attachments=None, *, source_editor=None):
        text = str(text or "").strip()
        attachments = [
            dict(item)
            for item in (attachments or [])
            if os.path.isfile(str((item or {}).get("path") or "").strip())
        ]
        if not text and not attachments:
            return False
        if self._is_channel_process_session():
            QMessageBox.information(self, "不可发送", "当前选中的是渠道进程会话，只能回顾快照，不能从这里继续发送消息。")
            return False
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
        follower = getattr(self, "_set_follow_latest_user", None)
        if callable(follower):
            follower(True)
        self._scroll_row_to_top(user_row)
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
            self._busy = False
            self.send_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
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
        refresher = getattr(self, "_refresh_floating_chat_window", None)
        if callable(refresher):
            refresher()
        if not self._stream_flush_timer.isActive():
            self._stream_flush_timer.start(70)

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
        self._set_status("已完成。")
        self._refresh_composer_enabled()
        self._clear_active_turn_attachments()
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
        if et == "launcher_autonomous_trigger":
            if not self._busy:
                self._send(text=self.AUTO_TASK_TEXT, auto=True)
            return
        if self._handle_download_event(ev):
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
            trace = ev.get("trace", "")
            self._clear_active_turn_attachments()
            self._busy = False
            follower = getattr(self, "_set_follow_latest_user", None)
            if callable(follower):
                follower(False)
            self.send_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self._set_status(f"错误: {msg}")
            self._refresh_composer_enabled()
            if trace:
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Warning)
                box.setWindowTitle("桥接错误")
                box.setText(msg or "未知错误")
                box.setDetailedText(str(trace))
                box.exec()
            else:
                QMessageBox.warning(self, "桥接错误", msg or "未知错误")
