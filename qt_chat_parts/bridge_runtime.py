from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time

from PySide6.QtWidgets import QMessageBox

import launcher_core as lz

from .common import _session_copy


class BridgeRuntimeMixin:
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
            self._busy = False
            self.send_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self._set_status(f"错误: {msg}")
            self._refresh_composer_enabled()
            QMessageBox.warning(self, "桥接错误", msg or "未知错误")
