from __future__ import annotations

import os
import re
import shlex
import threading
import time

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QMessageBox

from launcher_app import core as lz
from launcher_app.theme import C, F
from .common import (
    process_cmdline_matches_agent_script,
    remote_device_agent_dir,
    remote_device_agent_mode,
    remote_device_container_name,
)


class SessionShellMixin:
    def _refresh_info_popup_style(self):
        popup = getattr(self, "_info_popup", None)
        if popup is None:
            return
        radius = max(6, int(F.get("radius_sm", 8) or 8))
        popup.setStyleSheet(
            f"QLabel#infoPopup {{ background: {C['panel']}; color: {C['text']};"
            f" border: 1px solid {C['border']}; padding: 8px 10px;"
            f" border-radius: {radius}px; font-size: 12px; }}"
        )
        refresher = getattr(self, "_refresh_info_tooltip", None)
        if callable(refresher):
            try:
                refresher()
            except Exception:
                pass

    def _apply_composer_widget_state(self, widget, enabled, *, enabled_tooltip="", disabled_tooltip=""):
        if widget is None:
            return
        widget.setEnabled(bool(enabled))
        tooltip = enabled_tooltip if bool(enabled) else disabled_tooltip
        try:
            widget.setToolTip(str(tooltip or ""))
        except Exception:
            pass

    def _composer_send_disabled_reason(self, *, disabled=False):
        if bool(disabled):
            return "渠道进程会话仅用于回顾日志与快照，不能在这里继续发送消息。"
        if bool(getattr(self, "_busy", False)):
            return "当前正在等待模型回复，请稍候或先停止当前任务。"
        return ""

    def _composer_stop_disabled_reason(self, *, disabled=False, remote=False):
        if bool(disabled):
            return "渠道进程会话仅用于回顾日志与快照，不能在这里停止任务。"
        if bool(remote):
            return "当前会话在远程设备执行，这里不支持直接停止远端任务。"
        if not bool(getattr(self, "_busy", False)):
            return "当前没有正在执行的本地回复任务。"
        if bool(getattr(self, "_abort_requested", False)):
            return "停止请求已发送，请等待当前任务退出。"
        return ""

    def _composer_llm_disabled_reason(self, *, disabled=False):
        if bool(disabled):
            return "渠道进程会话仅支持查看日志，不能切换模型。"
        if not bool(getattr(self, "llms", None)):
            return "当前还没有可用的 LLM 配置。"
        return ""

    def _composer_reasoning_effort_disabled_reason(self, *, disabled=False):
        if bool(disabled):
            return "渠道进程会话仅支持查看日志，不能切换思考强度。"
        if not bool(getattr(self, "llms", None)):
            return "当前还没有可用的 LLM 配置。"
        return ""

    def _bind_session_to_current_bridge(self, session, *, preserve_session_state=False):
        if not isinstance(session, dict):
            return
        snapshot = dict(session.get("snapshot") or {})
        current_llm_idx = int(self._current_llm_index() or 0)
        session["process_pid"] = getattr(self.bridge_proc, "pid", None)
        if preserve_session_state:
            try:
                session_llm_idx = int(session.get("llm_idx", snapshot.get("llm_idx", current_llm_idx)) or 0)
            except Exception:
                session_llm_idx = current_llm_idx
        else:
            session_llm_idx = current_llm_idx
            session["llm_idx"] = session_llm_idx
        snapshot["version"] = int(snapshot.get("version", 1) or 1)
        snapshot["kind"] = str(snapshot.get("kind") or "turn_complete").strip() or "turn_complete"
        snapshot["captured_at"] = float(snapshot.get("captured_at", session.get("updated_at", time.time())) or time.time())
        snapshot["turns"] = int(snapshot.get("turns", ((session.get("token_usage") or {}).get("turns", 0) or 0)) or 0)
        snapshot["llm_idx"] = int(session_llm_idx or 0)
        snapshot["process_pid"] = int(session.get("process_pid", 0) or 0)
        snapshot["has_backend_history"] = bool(session.get("backend_history"))
        snapshot["has_agent_history"] = bool(session.get("agent_history"))
        if "reasoning_effort" in session:
            reasoning_effort = str(session.get("reasoning_effort") or "").strip().lower()
            if reasoning_effort:
                snapshot["reasoning_effort"] = reasoning_effort
                snapshot["reasoning_effort_source"] = "override"
            else:
                snapshot.pop("reasoning_effort", None)
                snapshot.pop("reasoning_effort_source", None)
        elif preserve_session_state or str(snapshot.get("reasoning_effort_source") or "").strip().lower() in {"override", "runtime"}:
            preserved_reasoning = str(snapshot.get("reasoning_effort") or "").strip().lower()
            if preserved_reasoning:
                snapshot["reasoning_effort"] = preserved_reasoning
                source = str(snapshot.get("reasoning_effort_source") or "").strip().lower()
                snapshot["reasoning_effort_source"] = source if source in {"override", "runtime"} else "runtime"
            else:
                snapshot.pop("reasoning_effort", None)
                snapshot.pop("reasoning_effort_source", None)
        else:
            snapshot.pop("reasoning_effort", None)
            snapshot.pop("reasoning_effort_source", None)
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
        scope = "local"
        resolver = getattr(self, "_session_device_scope_id", None)
        if callable(resolver):
            try:
                scope, _did = resolver(session)
            except Exception:
                scope = "local"
        is_remote = scope == "remote"
        if self._is_channel_process_session(session):
            self._ensure_session_usage_metadata(session)
            lz.save_session(self.agent_dir, session)
            self._selected_session_id = session.get("id")
            self._refresh_sessions()
            return
        if not is_remote:
            self._bind_session_to_current_bridge(session)
        else:
            session["llm_idx"] = int(session.get("llm_idx", 0) or 0)
            snapshot = dict(session.get("snapshot") or {})
            snapshot["version"] = int(snapshot.get("version", 1) or 1)
            snapshot["kind"] = str(snapshot.get("kind") or "turn_complete").strip() or "turn_complete"
            snapshot["captured_at"] = float(session.get("updated_at", time.time()) or time.time())
            snapshot["turns"] = int(snapshot.get("turns", ((session.get("token_usage") or {}).get("turns", 0) or 0)) or 0)
            snapshot["llm_idx"] = int(session.get("llm_idx", 0) or 0)
            snapshot["process_pid"] = int(session.get("process_pid", 0) or 0)
            snapshot["has_backend_history"] = bool(session.get("backend_history"))
            snapshot["has_agent_history"] = bool(session.get("agent_history"))
            if "reasoning_effort" in session:
                reasoning_effort = str(session.get("reasoning_effort") or "").strip().lower()
                if reasoning_effort:
                    snapshot["reasoning_effort"] = reasoning_effort
                    snapshot["reasoning_effort_source"] = "override"
                else:
                    snapshot.pop("reasoning_effort", None)
                    snapshot.pop("reasoning_effort_source", None)
            else:
                source = str(snapshot.get("reasoning_effort_source") or "").strip().lower()
                if source not in {"override", "runtime"}:
                    snapshot.pop("reasoning_effort", None)
                    snapshot.pop("reasoning_effort_source", None)
            session["snapshot"] = snapshot
        self._ensure_session_usage_metadata(session)
        lz.save_session(self.agent_dir, session)
        if is_remote:
            sync_remote_async = getattr(self, "_save_remote_session_source_async", None)
            if callable(sync_remote_async):
                sync_remote_async(session, on_error_status=True)
            else:
                sync_remote = getattr(self, "_save_remote_session_source", None)
                if callable(sync_remote):
                    ok, err = sync_remote(session)
                    if not ok:
                        self._set_status(f"远端会话同步失败：{err}")
        self._selected_session_id = session.get("id")
        self._enforce_session_archive_limits(
            channel_id=session.get("channel_id"),
            device_scope=session.get("device_scope"),
            device_id=session.get("device_id"),
            exclude_session_ids={session.get("id")},
            refresh=False,
        )
        self._refresh_sessions()

    def _enforce_session_archive_limits(self, channel_id=None, device_scope=None, device_id=None, exclude_session_ids=None, refresh=True):
        if not lz.is_valid_agent_dir(self.agent_dir):
            return 0
        excluded = {str(item).strip() for item in (exclude_session_ids or set()) if str(item or "").strip()}
        if self.current_session and self.current_session.get("id"):
            excluded.add(str(self.current_session.get("id")))
        sessions = []
        target_cid = lz._normalize_usage_channel_id(channel_id, "launcher") if channel_id else ""
        target_scope = str(device_scope or "").strip().lower()
        if target_scope not in ("local", "remote"):
            target_scope = ""
        target_did = str(device_id or "").strip()
        for meta in lz.list_sessions(self.agent_dir):
            sid = str(meta.get("id") or "").strip()
            if not sid:
                continue
            cid = lz._normalize_usage_channel_id(meta.get("channel_id"), "launcher")
            if target_cid and cid != target_cid:
                continue
            scope = str(meta.get("device_scope") or "local").strip().lower()
            if scope not in ("local", "remote"):
                scope = "local"
            did = str(meta.get("device_id") or "").strip() if scope == "remote" else "local"
            if target_scope:
                if scope != target_scope:
                    continue
                if scope == "remote" and target_did and did != target_did:
                    continue
            sessions.append(
                {
                    "id": sid,
                    "channel_id": cid,
                    "device_scope": scope,
                    "device_id": did,
                    "updated_at": float(meta.get("updated_at", 0) or 0),
                    "pinned": bool(meta.get("pinned", False)),
                }
            )
        grouped = {}
        for data in sessions:
            key = (
                data.get("channel_id") or "launcher",
                data.get("device_scope") or "local",
                data.get("device_id") or "local",
            )
            grouped.setdefault(key, []).append(data)
        removed = 0
        for key, items in grouped.items():
            cid, scope, did = key
            limit = self._archive_limit_for_channel(cid, device_scope=scope, device_id=did)
            if limit <= 0 or len(items) <= limit:
                continue
            keep_ids = set(excluded)
            removable = sorted(
                [item for item in items if str(item.get("id") or "") not in keep_ids and not bool(item.get("pinned", False))],
                key=lambda item: float(item.get("updated_at", 0) or 0),
            )
            overflow = len(items) - limit
            for victim in removable[: max(0, overflow)]:
                if str(victim.get("device_scope") or "").strip().lower() == "remote":
                    sync_delete = getattr(self, "_delete_remote_session_source", None)
                    if callable(sync_delete):
                        try:
                            payload = lz.load_session(self.agent_dir, victim.get("id")) or dict(victim)
                            ok, _err = sync_delete(payload)
                            if not ok:
                                continue
                        except Exception:
                            continue
                lz.archive_session(self.agent_dir, victim.get("id"), victim, reason="auto_limit")
                removed += 1
        if removed and refresh:
            self._refresh_sessions()
        return removed

    def _info_tooltip_text(self) -> str:
        parts = []
        for name in ("session_mode_label", "status_label", "session_token_tree_label"):
            lbl = getattr(self, name, None)
            if lbl is None:
                continue
            text = (lbl.text() or "").strip()
            if text:
                parts.append(text)
        parts.append(self._subagent_runtime_summary_text())
        return "\n\n".join(parts) or "尚无状态信息"

    def _iter_local_subagent_processes(self):
        getter = getattr(self, "_iter_local_channel_processes", None)
        if not callable(getter):
            return []
        try:
            return list(getter() or [])
        except Exception:
            return []

    def _subagent_runtime_target_key(self):
        current_session = getattr(self, "current_session", None)
        session = current_session if isinstance(current_session, dict) else {}
        resolver = getattr(self, "_session_device_scope_id", None)
        if callable(resolver):
            try:
                scope, did = resolver(session)
            except Exception:
                scope, did = "local", "local"
            scope = str(scope or "local").strip().lower()
            did = str(did or ("local" if scope != "remote" else "")).strip()
            if scope == "remote" and did:
                return f"remote:{did}"
        context_getter = getattr(self, "_current_device_context", None)
        if callable(context_getter):
            try:
                scope, did = context_getter()
            except Exception:
                scope, did = "local", "local"
            scope = str(scope or "local").strip().lower()
            did = str(did or ("local" if scope != "remote" else "")).strip()
            if scope == "remote" and did:
                return f"remote:{did}"
        return "local:local"

    def _subagent_runtime_target_scope(self):
        key = str(self._subagent_runtime_target_key() or "").strip().lower()
        return "remote" if key.startswith("remote:") else "local"

    def _count_running_subagents(self) -> int:
        agent_dir = str(getattr(self, "agent_dir", "") or "").strip()
        if not agent_dir:
            return 0
        if self._subagent_runtime_target_scope() == "remote":
            return self._count_remote_running_subagents()
        try:
            agent_dir_real = os.path.realpath(agent_dir)
        except Exception:
            agent_dir_real = ""
        current_pid = int(os.getpid() or 0)
        count = 0
        for proc_info in self._iter_local_subagent_processes():
            pid = int(proc_info.get("pid") or 0)
            if pid <= 0 or pid == current_pid:
                continue
            cmdline = str(proc_info.get("cmdline") or "").strip()
            if not cmdline or not re.search(r"(^|\s)--task(?:\s|=|$)", cmdline):
                continue
            if process_cmdline_matches_agent_script(
                cmdline,
                agent_dir=agent_dir,
                script_rel="agentmain.py",
                cwd=proc_info.get("cwd") or "",
                agent_dir_real=agent_dir_real,
                cwd_real=proc_info.get("cwd_real") or "",
            ):
                count += 1
        return count

    def _parse_subagent_process_rows(self, raw_text):
        rows = []
        for line in str(raw_text or "").splitlines():
            text = str(line or "").strip()
            if not text:
                continue
            parts = text.split("\t", 2)
            pid_text = str(parts[0] if parts else "").strip()
            if not re.fullmatch(r"\d+", pid_text):
                continue
            rows.append(
                {
                    "pid": int(pid_text),
                    "cwd": str(parts[1] if len(parts) > 1 else "").strip(),
                    "cwd_real": str(parts[1] if len(parts) > 1 else "").strip(),
                    "cmdline": str(parts[2] if len(parts) > 2 else "").strip(),
                }
            )
        return rows

    def _count_remote_running_subagents(self, session=None) -> int:
        payload_getter = getattr(self, "_remote_device_payload", None)
        opener = getattr(self, "_open_vps_ssh_client", None)
        executor = getattr(self, "_vps_exec_remote", None)
        if not callable(payload_getter) or not callable(opener) or not callable(executor):
            return 0
        data = session if isinstance(session, dict) else (self.current_session or {})
        try:
            dev, payload = payload_getter(data)
        except Exception:
            return 0
        client, _err_msg, _detail, _missing = opener(payload, timeout=8)
        if client is None:
            return 0
        try:
            agent_dir = remote_device_agent_dir(dev, username=(dev or {}).get("username"))
            if not agent_dir:
                return 0
            shell_script = (
                "ps -eo pid=,args= | while IFS= read -r line; do "
                "[ -n \"$line\" ] || continue; "
                "pid=${line%% *}; "
                "case \"$pid\" in ''|*[!0-9]*) continue ;; esac; "
                "cmd=${line#\"$pid\"}; cmd=${cmd# }; "
                "cwd=$(readlink -f \"/proc/$pid/cwd\" 2>/dev/null || true); "
                "printf '%s\\t%s\\t%s\\n' \"$pid\" \"$cwd\" \"$cmd\"; "
                "done"
            )
            cmd = f"sh -lc {shlex.quote(shell_script)}"
            if remote_device_agent_mode(dev) == "docker":
                container = remote_device_container_name(dev)
                if not container:
                    return 0
                cmd = f"docker exec {shlex.quote(container)} sh -lc {shlex.quote(shell_script)}"
            rc, out, _err = executor(client, cmd, timeout=20)
            if int(rc or 0) != 0:
                return 0
            count = 0
            for proc_info in self._parse_subagent_process_rows(out):
                cmdline = str(proc_info.get("cmdline") or "").strip()
                if not cmdline or not re.search(r"(^|\s)--task(?:\s|=|$)", cmdline):
                    continue
                if process_cmdline_matches_agent_script(
                    cmdline,
                    agent_dir=agent_dir,
                    script_rel="agentmain.py",
                    cwd=proc_info.get("cwd") or "",
                    agent_dir_real=agent_dir,
                    cwd_real=proc_info.get("cwd_real") or "",
                ):
                    count += 1
            return count
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _apply_subagent_runtime_count(self, count, *, target_key="", scanned_at=None):
        current_key = str(self._subagent_runtime_target_key() or "").strip()
        event_key = str(target_key or "").strip()
        if event_key and current_key and event_key != current_key:
            return
        previous = int(getattr(self, "_subagent_runtime_count", 0) or 0)
        next_count = max(0, int(count or 0))
        self._subagent_runtime_bound_key = current_key or event_key or "local:local"
        self._subagent_runtime_count = next_count
        self._subagent_runtime_scan_ts = float(scanned_at or time.time())
        if previous != next_count:
            self._refresh_info_tooltip()
        refresher = getattr(self, "_refresh_info_button_icon", None)
        if callable(refresher):
            refresher()

    def _start_remote_subagent_runtime_refresh(self, target_key, session):
        event_queue = getattr(self, "_event_queue", None)
        if event_queue is None:
            count = self._count_remote_running_subagents(session=session)
            self._apply_subagent_runtime_count(count, target_key=target_key, scanned_at=time.time())
            return

        def worker():
            try:
                count = self._count_remote_running_subagents(session=session)
            except Exception:
                count = 0
            event_queue.put(
                {
                    "event": "subagent_runtime_count",
                    "target_key": str(target_key or "").strip(),
                    "count": int(count or 0),
                    "scanned_at": float(time.time()),
                }
            )

        threading.Thread(target=worker, name="subagent-runtime-scan", daemon=True).start()

    def _start_local_subagent_runtime_refresh(self, target_key):
        event_queue = getattr(self, "_event_queue", None)
        if event_queue is None:
            count = self._count_running_subagents()
            self._apply_subagent_runtime_count(count, target_key=target_key, scanned_at=time.time())
            return

        def worker():
            try:
                count = self._count_running_subagents()
            except Exception:
                count = 0
            event_queue.put(
                {
                    "event": "subagent_runtime_count",
                    "target_key": str(target_key or "").strip(),
                    "count": int(count or 0),
                    "scanned_at": float(time.time()),
                }
            )

        threading.Thread(target=worker, name="subagent-runtime-scan-local", daemon=True).start()

    def _refresh_subagent_runtime_state(self):
        target_key = str(self._subagent_runtime_target_key() or "").strip() or "local:local"
        previous_key = str(getattr(self, "_subagent_runtime_bound_key", "") or "").strip()
        if previous_key != target_key:
            self._subagent_runtime_bound_key = target_key
            self._subagent_runtime_count = 0
            self._subagent_runtime_scan_ts = 0.0
            self._subagent_runtime_refresh_inflight_key = ""
            self._refresh_info_tooltip()
            refresher = getattr(self, "_refresh_info_button_icon", None)
            if callable(refresher):
                refresher()
        inflight_key = str(getattr(self, "_subagent_runtime_refresh_inflight_key", "") or "").strip()
        if inflight_key == target_key:
            return
        self._subagent_runtime_refresh_inflight_key = target_key
        if self._subagent_runtime_target_scope() == "remote":
            current_session = getattr(self, "current_session", None)
            session = dict(current_session or {}) if isinstance(current_session, dict) else {}
            self._start_remote_subagent_runtime_refresh(target_key, session)
            return
        self._start_local_subagent_runtime_refresh(target_key)

    def _subagent_runtime_summary_text(self) -> str:
        count = int(getattr(self, "_subagent_runtime_count", 0) or 0)
        return f"后台子代理：{count}"

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
        if watched is getattr(self, "server_status_btn", None):
            et = event.type()
            if et == QEvent.Enter:
                shower = getattr(self, "_show_server_status_tooltip", None)
                if callable(shower):
                    shower()
            elif et == QEvent.Leave:
                hider = getattr(self, "_hide_server_status_tooltip", None)
                if callable(hider):
                    hider()
            elif et == QEvent.ToolTip:
                return True
        viewport = getattr(getattr(self, "scroll", None), "viewport", lambda: None)()
        if watched is viewport and event.type() in (QEvent.Resize, QEvent.Show):
            placer = getattr(self, "_place_jump_latest_button", None)
            if callable(placer):
                placer()
        return super().eventFilter(watched, event)

    def _is_channel_process_session(self, session=None):
        data = session if isinstance(session, dict) else (self.current_session or {})
        return str((data or {}).get("session_kind") or "").strip().lower() == "channel_process"

    def _refresh_composer_enabled(self):
        disabled = self._is_channel_process_session()
        remote = False
        checker = getattr(self, "_is_remote_session", None)
        if callable(checker):
            try:
                remote = bool(checker())
            except Exception:
                remote = False
        input_box = getattr(self, "input_box", None)
        send_btn = getattr(self, "send_btn", None)
        stop_btn = getattr(self, "stop_btn", None)
        llm_combo = getattr(self, "llm_combo", None)
        reasoning_effort_combo = getattr(self, "reasoning_effort_combo", None)
        send_disabled_reason = self._composer_send_disabled_reason(disabled=disabled)
        stop_disabled_reason = self._composer_stop_disabled_reason(disabled=disabled, remote=remote)
        llm_disabled_reason = self._composer_llm_disabled_reason(disabled=disabled)
        reasoning_disabled_reason = self._composer_reasoning_effort_disabled_reason(disabled=disabled)
        if input_box is not None:
            input_box.setReadOnly(disabled)
            input_box.setPlaceholderText(
                "渠道进程会话仅用于回顾日志与快照，不能在这里继续发送消息"
                if disabled
                else ("当前会话在远程设备执行，使用 SSH 发送。Enter 发送，Shift+Enter 换行" if remote else "输入消息，Enter 发送，Shift+Enter 换行")
            )
            try:
                input_box.setToolTip(
                    "渠道进程会话仅用于查看日志与快照。"
                    if disabled
                    else ("当前会话通过 SSH 在远程设备执行。" if remote else "当前会话在本机执行。")
                )
            except Exception:
                pass
        if send_btn is not None:
            self._apply_composer_widget_state(
                send_btn,
                not bool(send_disabled_reason),
                enabled_tooltip="发送当前输入内容。",
                disabled_tooltip=send_disabled_reason,
            )
        if stop_btn is not None:
            self._apply_composer_widget_state(
                stop_btn,
                not bool(stop_disabled_reason),
                enabled_tooltip="停止当前本地回复任务。",
                disabled_tooltip=stop_disabled_reason,
            )
        if llm_combo is not None:
            self._apply_composer_widget_state(
                llm_combo,
                not bool(llm_disabled_reason),
                enabled_tooltip="切换当前会话使用的模型。",
                disabled_tooltip=llm_disabled_reason,
            )
        if reasoning_effort_combo is not None:
            self._apply_composer_widget_state(
                reasoning_effort_combo,
                not bool(reasoning_disabled_reason),
                enabled_tooltip="切换当前会话使用的思考强度。",
                disabled_tooltip=reasoning_disabled_reason,
            )
        floating_sync = getattr(self, "_sync_floating_llm_combo", None)
        if callable(floating_sync):
            floating_sync()
        floating_reasoning_sync = getattr(self, "_sync_floating_reasoning_effort_combo", None)
        if callable(floating_reasoning_sync):
            floating_reasoning_sync()
        refresher = getattr(self, "_refresh_floating_chat_window", None)
        if callable(refresher):
            refresher()

    def _active_sessions_for_channel(self, channel_id: str, device_scope=None, device_id=None):
        cid = lz._normalize_usage_channel_id(channel_id, "launcher")
        scope = str(device_scope or "").strip().lower()
        did = str(device_id or "").strip()
        if scope not in ("local", "remote"):
            scope = "local"
            did = "local"
            resolver = getattr(self, "_current_device_context", None)
            if callable(resolver):
                try:
                    scope, did = resolver()
                except Exception:
                    scope, did = "local", "local"
        if scope == "remote":
            did = did or "local"
        else:
            scope = "local"
            did = "local"
        out = []
        if not lz.is_valid_agent_dir(self.agent_dir):
            return out
        for meta in lz.list_sessions(self.agent_dir):
            if lz._normalize_usage_channel_id(meta.get("channel_id"), "launcher") != cid:
                continue
            session_scope = str(meta.get("device_scope") or "local").strip().lower()
            if session_scope not in ("local", "remote"):
                session_scope = "local"
            if session_scope != scope:
                continue
            if session_scope == "remote":
                if str(meta.get("device_id") or "").strip() != str(did or "").strip():
                    continue
            out.append(
                {
                    "id": meta.get("id"),
                    "pinned": bool(meta.get("pinned", False)),
                    "updated_at": float(meta.get("updated_at", 0) or 0),
                }
            )
        return out

    def _can_create_session_for_channel(self, channel_id: str, show_message: bool = True, device_scope=None, device_id=None) -> bool:
        cid = lz._normalize_usage_channel_id(channel_id, "launcher")
        scope = str(device_scope or "").strip().lower()
        did = str(device_id or "").strip()
        if scope not in ("local", "remote"):
            resolver = getattr(self, "_current_device_context", None)
            if callable(resolver):
                try:
                    scope, did = resolver()
                except Exception:
                    scope, did = "local", "local"
            else:
                scope, did = "local", "local"
        if scope != "remote":
            scope, did = "local", "local"
        limit = self._archive_limit_for_channel(cid, device_scope=scope, device_id=did)
        if limit <= 0:
            return True
        sessions = self._active_sessions_for_channel(cid, device_scope=scope, device_id=did)
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
