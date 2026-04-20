from __future__ import annotations

import time

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QMessageBox

import launcher_core as lz


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
                if disabled
                else "输入消息，Enter 发送，Shift+Enter 换行"
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
