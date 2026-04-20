from __future__ import annotations

import time
import uuid

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from launcher_app import core as lz
from launcher_app.theme import C, F

from .common import _session_copy


class SidebarSessionsMixin:
    def _sidebar_button_style(self, *, primary: bool = False, subtle: bool = False, selected: bool = False) -> str:
        radius = F["radius_md"]
        palette = C
        if selected:
            variant = "selected"
        elif primary:
            variant = "primary"
        elif subtle:
            variant = "subtle"
        else:
            variant = "default"
        marker = f'QPushButton[factoryKey="nav-{variant}"] {{ }}'
        if selected:
            return marker + (
                f"QPushButton {{ background: {palette['accent_soft_bg']}; color: {palette['text']}; "
                f"border: 1px solid transparent; border-left: 2px solid {palette['accent']}; "
                f"border-radius: {radius}px; padding: 8px 10px; font-size: 13px; font-weight: 600; text-align: left; }}"
                f"QPushButton:hover {{ background: {palette['accent_soft_bg_hover']}; }}"
            )
        if primary:
            return marker + (
                f"QPushButton {{ background: {palette['layer2']}; color: {palette['text']}; border: 1px solid {palette['stroke_default']}; "
                f"border-radius: {radius}px; padding: 8px 12px; font-size: 13px; font-weight: 600; text-align: left; }}"
                f"QPushButton:hover {{ background: {palette['layer3']}; border-color: {palette['stroke_hover']}; }}"
                f"QPushButton:pressed {{ background: {palette['layer1']}; }}"
            )
        if subtle:
            return marker + (
                f"QPushButton {{ background: transparent; color: {palette['text_soft']}; border: 1px solid transparent; "
                f"border-radius: {radius}px; padding: 7px 10px; font-size: 13px; text-align: left; }}"
                f"QPushButton:hover {{ background: {palette['layer2']}; color: {palette['text']}; }}"
                f"QPushButton:pressed {{ background: {palette['layer1']}; }}"
            )
        return marker + (
            f"QPushButton {{ background: transparent; color: {palette['text_soft']}; border: 1px solid transparent; "
            f"border-radius: {radius}px; padding: 7px 12px; font-size: 13px; text-align: center; }}"
            f"QPushButton:hover {{ background: {palette['layer2']}; color: {palette['text']}; }}"
            f"QPushButton:pressed {{ background: {palette['layer1']}; }}"
        )

    def _toggle_sidebar(self):
        self.sidebar_collapsed = not self.sidebar_collapsed
        self.cfg["sidebar_collapsed"] = self.sidebar_collapsed
        lz.save_config(self.cfg)
        self._rebuild_sidebar()

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
        haystack = " ".join(str(row.get(key) or "") for key in ("title", "channel_label", "channel_id")).lower()
        return kw in haystack

    def _session_list_signature(self, items, keyword: str):
        return (
            self._sidebar_view_mode,
            self._sidebar_channel_id,
            self._selected_session_id or ((self.current_session or {}).get("id")),
            keyword,
            tuple(
                tuple(row.get(key) for key in ("kind", "id", "channel_id", "title", "updated_at", "pinned", "active_count"))
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

    def _refresh_session_list(self):
        self._refresh_sessions()
