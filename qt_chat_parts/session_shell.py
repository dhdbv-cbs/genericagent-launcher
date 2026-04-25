from __future__ import annotations

import time

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QMessageBox

from launcher_app import core as lz


class SessionShellMixin:
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
        if input_box is not None:
            input_box.setReadOnly(disabled)
            input_box.setPlaceholderText(
                "渠道进程会话仅用于回顾日志与快照，不能在这里继续发送消息"
                if disabled
                else ("当前会话在远程设备执行，使用 SSH 发送。Enter 发送，Shift+Enter 换行" if remote else "输入消息，Enter 发送，Shift+Enter 换行")
            )
        if send_btn is not None:
            send_btn.setEnabled((not disabled) and (not self._busy))
        if stop_btn is not None:
            stop_btn.setEnabled((not disabled) and (not remote) and self._busy and (not self._abort_requested))
        if llm_combo is not None:
            llm_combo.setEnabled((not disabled) and bool(self.llms))
        floating_sync = getattr(self, "_sync_floating_llm_combo", None)
        if callable(floating_sync):
            floating_sync()
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
