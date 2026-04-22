from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QLabel

from launcher_app import core as lz

from .common import MessageRow, _session_source_label


class ChatViewMixin:
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
        self._refresh_jump_latest_button()
        refresher = getattr(self, "_refresh_floating_chat_window", None)
        if callable(refresher):
            refresher()

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
        refresher = getattr(self, "_refresh_floating_chat_window", None)
        if callable(refresher):
            refresher()

    def _add_message_row(self, role: str, text: str, finished: bool = True, *, auto_scroll: bool = True):
        on_resend = self._regenerate_from_row if role == "assistant" else None
        row = MessageRow(text, role, self.msg_root, on_resend=on_resend)
        row.set_finished(finished)
        self.msg_layout.insertWidget(self._message_row_insert_index(), row)
        self._rendered_message_rows.append(row)
        if auto_scroll:
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
        for bubble in bubbles:
            role = bubble.get("role", "assistant")
            self._add_message_row(role, bubble.get("text", ""), finished=True, auto_scroll=False)
        self._apply_row_token_usage(live=False)
        self._update_header_labels()
        self._refresh_token_label()
        self._sync_current_turn_view(force=True)
        self._refresh_jump_latest_button()
        refresher = getattr(self, "_refresh_floating_chat_window", None)
        if callable(refresher):
            refresher()

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
            self._apply_row_token_usage(live=bool(getattr(self, "_busy", False)))
        finally:
            self._refresh_info_tooltip()

    def _refresh_token_label_impl(self):
        session = self.current_session
        if not isinstance(session, dict):
            self.session_token_tree_label.setText("↑0  ↓0")
            self.session_token_tree_label.hide()
            return
        self._ensure_session_usage_metadata(session)
        summary = self._session_token_summary(include_live=True)
        if summary["input_tokens"] == 0 and summary["output_tokens"] == 0 and summary["live_output_tokens"] == 0:
            self.session_token_tree_label.setText("↑0  ↓0")
            self.session_token_tree_label.hide()
            return
        output_tokens = int(summary["output_tokens"] or 0)
        suffix = " …" if summary["live_output_tokens"] > 0 else ""
        self.session_token_tree_label.setText(f"↑{int(summary['input_tokens'] or 0)}  ↓{output_tokens}{suffix}")
        self.session_token_tree_label.show()

    def _apply_row_token_usage(self, *, live: bool = False):
        rows = list(getattr(self, "_rendered_message_rows", None) or [])
        if not rows:
            return
        session = self.current_session or {}
        usage = session.get("token_usage") or {}
        events = list(usage.get("events") or [])
        event_index = 0
        stream_row = getattr(self, "_stream_row", None)
        for row in rows:
            if getattr(row, "_role", "") != "assistant":
                continue
            if row is stream_row:
                if live:
                    summary = self._single_turn_token_summary(include_live=True)
                    output_tokens = int(summary.get("live_output_tokens", 0) or summary.get("output_tokens", 0) or 0)
                    row.set_token_info(
                        int(summary.get("input_tokens", 0) or 0),
                        output_tokens,
                        live=bool(summary.get("live_output_tokens", 0)),
                    )
                continue
            if event_index >= len(events):
                row.set_token_info(0, 0, live=False)
                continue
            ev = events[event_index] or {}
            event_index += 1
            row.set_token_info(
                int(ev.get("input_tokens", 0) or 0),
                int(ev.get("output_tokens", 0) or 0),
                live=False,
            )

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
        self._refresh_jump_latest_button()

    def _scroll_row_to_top(self, row, *, top_margin: int = 18):
        if row is None:
            return

        def apply():
            try:
                bar = self.scroll.verticalScrollBar()
                target_y = max(0, row.y() - int(top_margin or 0))
                bar.setValue(min(target_y, bar.maximum()))
            finally:
                self._refresh_jump_latest_button()

        QTimer.singleShot(30, apply)

    def _latest_user_row(self):
        for row in reversed(self._rendered_message_rows):
            if getattr(row, "_role", "") == "user":
                return row
        return None

    def _set_follow_latest_user(self, enabled: bool):
        self._follow_latest_user_message = bool(enabled)

    def _sync_current_turn_view(self, *, force: bool = False):
        if getattr(self, "_follow_latest_user_message", False):
            latest_user_row = self._latest_user_row()
            if latest_user_row is not None:
                self._scroll_row_to_top(latest_user_row)
                return
        self._scroll_to_bottom(force=force)

    def _refresh_jump_latest_button(self):
        btn = getattr(self, "jump_latest_btn", None)
        if btn is None:
            return
        try:
            bar = self.scroll.verticalScrollBar()
            near_bottom = bar.value() >= max(0, bar.maximum() - 40)
            btn.setVisible(not near_bottom)
            if btn.isVisible():
                btn.raise_()
        except Exception:
            btn.hide()

    def _scroll_to_bottom(self, force: bool = False):
        if self._user_scrolled_up and not force:
            return

        def apply():
            bar = self.scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
            self._refresh_jump_latest_button()

        QTimer.singleShot(30, apply)
