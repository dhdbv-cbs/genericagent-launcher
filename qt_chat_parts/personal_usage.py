from __future__ import annotations

import json
import time
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QSpinBox, QVBoxLayout

import launcher_core as lz
from qt_theme import C, F


class PersonalUsageMixin:
    def _archive_limit_bucket(self):
        bucket = self.cfg.get("session_archive_limits")
        if not isinstance(bucket, dict):
            bucket = {}
            self.cfg["session_archive_limits"] = bucket
        return bucket

    def _archive_known_channel_ids(self):
        ids = ["launcher"]
        ids.extend(spec.get("id") for spec in lz.COMM_CHANNEL_SPECS if spec.get("id"))
        seen = set()
        ordered = []
        for cid in ids:
            cid = str(cid or "").strip().lower()
            if not cid or cid in seen:
                continue
            seen.add(cid)
            ordered.append(cid)
        if lz.is_valid_agent_dir(self.agent_dir):
            for meta in lz.list_sessions(self.agent_dir):
                try:
                    session = lz.load_session(self.agent_dir, meta["id"])
                except Exception:
                    session = None
                cid = lz._normalize_usage_channel_id((session or {}).get("channel_id"), "launcher")
                if cid not in seen:
                    seen.add(cid)
                    ordered.append(cid)
        return ordered

    def _archive_channel_label(self, channel_id):
        return lz._usage_channel_label(channel_id)

    def _archive_limit_for_channel(self, channel_id):
        bucket = self._archive_limit_bucket()
        raw = bucket.get(channel_id, 10)
        try:
            value = int(raw)
        except Exception:
            value = 10
        return max(0, value)

    def _collect_archive_stats(self):
        active = {}
        if not lz.is_valid_agent_dir(self.agent_dir):
            return {"active": active}
        for meta in lz.list_sessions(self.agent_dir):
            try:
                session = lz.load_session(self.agent_dir, meta["id"])
            except Exception:
                session = None
            cid = lz._normalize_usage_channel_id((session or {}).get("channel_id"), "launcher")
            active[cid] = active.get(cid, 0) + 1
        return {"active": active}

    def _reload_personal_panel(self):
        if not hasattr(self, "settings_personal_notice"):
            return
        self._clear_layout(self.settings_personal_list_layout)
        self._archive_limit_inputs = {}
        if not lz.is_valid_agent_dir(self.agent_dir):
            self.settings_personal_notice.setText("请先选择有效的 GenericAgent 目录。")
            return
        self.settings_personal_notice.setText(
            "启动器已经能识别会话所属渠道，并按 channel_id 区分会话上限。当前主聊天区记为“启动器”，其余渠道会按微信、QQ、Telegram 等分别统计。超出上限时会自动删除最旧未收藏会话。"
        )
        stats = self._collect_archive_stats()
        for cid in self._archive_known_channel_ids():
            card = self._panel_card()
            row = QHBoxLayout(card)
            row.setContentsMargins(14, 12, 14, 12)
            row.setSpacing(12)
            title = QLabel(self._archive_channel_label(cid))
            title.setFixedWidth(110)
            title.setObjectName("bodyText")
            row.addWidget(title, 0)
            spin = QSpinBox()
            spin.setRange(0, 9999)
            spin.setValue(self._archive_limit_for_channel(cid))
            spin.setSingleStep(10)
            spin.setStyleSheet(
                f"QSpinBox {{ background: {C['field_bg']}; color: {C['text']}; border: 1px solid {C['stroke_default']}; border-radius: {F['radius_md']}px; padding: 8px 10px; min-width: 96px; }}"
                f"QSpinBox::up-button, QSpinBox::down-button {{ width: 20px; border: none; background: transparent; }}"
            )
            row.addWidget(spin, 0)
            hint = QLabel("0 = 不自动清理")
            hint.setObjectName("mutedText")
            row.addWidget(hint, 0)
            row.addStretch(1)
            active_count = int(stats["active"].get(cid, 0) or 0)
            summary = QLabel(f"当前会话 {active_count}")
            summary.setObjectName("softTextSmall")
            row.addWidget(summary, 0)
            self._archive_limit_inputs[cid] = spin
            self.settings_personal_list_layout.addWidget(card)
        self.settings_personal_list_layout.addStretch(1)

    def _save_archive_settings(self):
        if not hasattr(self, "_archive_limit_inputs"):
            return
        bucket = self._archive_limit_bucket()
        for cid, spin in self._archive_limit_inputs.items():
            bucket[cid] = int(spin.value() or 0)
        self.cfg["session_archive_limits"] = bucket
        lz.save_config(self.cfg)
        removed = self._enforce_session_archive_limits(exclude_session_ids={((self.current_session or {}).get("id"))})
        self._reload_personal_panel()
        self._reload_usage_panel()
        self._refresh_sessions()
        if removed:
            QMessageBox.information(self, "已保存", f"会话上限已保存，并已自动删除 {removed} 个旧会话。")
        else:
            QMessageBox.information(self, "已保存", "会话上限已保存。当前没有触发新的自动清理。")

    def _collect_usage_stats(self, lookback_days=7):
        channel_stats = {}
        day_stats = {}
        now = time.time()
        today_key = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        lookback_cutoff = now - max(1, int(lookback_days)) * 86400
        today_total = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "sources": set()}
        recent_total = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "sources": set()}
        all_total = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "sources": set()}

        if not lz.is_valid_agent_dir(self.agent_dir):
            for item in (today_total, recent_total, all_total):
                item["mode"] = lz._usage_mode_from_sources(item.get("sources"))
            return {"today": today_total, "recent": recent_total, "all": all_total, "channels": [], "days": []}

        for meta in lz.list_sessions(self.agent_dir):
            try:
                session = lz.load_session(self.agent_dir, meta["id"])
            except Exception:
                session = None
            if not session:
                continue
            before = json.dumps(session.get("token_usage") or {}, ensure_ascii=False, sort_keys=True)
            self._ensure_session_usage_metadata(session)
            after = json.dumps(session.get("token_usage") or {}, ensure_ascii=False, sort_keys=True)
            if before != after:
                lz.save_session(self.agent_dir, session)
            usage = session.get("token_usage") or {}
            channel_id = str(session.get("channel_id") or usage.get("channel_id") or "launcher").strip().lower()
            channel_row = channel_stats.setdefault(
                channel_id,
                {
                    "channel_id": channel_id,
                    "label": lz._usage_channel_label(channel_id),
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "turns": 0,
                    "sessions": set(),
                    "last_active": 0,
                    "sources": set(),
                },
            )
            channel_row["sessions"].add(session.get("id"))
            channel_row["last_active"] = max(channel_row["last_active"], float(session.get("updated_at", 0) or 0))

            events = list(usage.get("events") or [])
            if not events:
                events = lz._fallback_token_events_from_bubbles(
                    session.get("bubbles") or [],
                    base_ts=session.get("updated_at") or session.get("created_at") or now,
                    channel_id=channel_id,
                    model_name=usage.get("last_model") or "",
                )

            for ev in events:
                inp = int(ev.get("input_tokens", 0) or 0)
                out = int(ev.get("output_tokens", 0) or 0)
                total = int(ev.get("total_tokens", inp + out) or (inp + out))
                try:
                    ts = float(ev.get("ts", session.get("updated_at", now)) or now)
                except Exception:
                    ts = now
                day_key = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                row = day_stats.setdefault(
                    day_key,
                    {
                        "date": day_key,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                        "turns": 0,
                        "channels": {},
                        "sources": set(),
                    },
                )
                source = str(ev.get("usage_source") or "estimate").strip().lower() or "estimate"
                row["input_tokens"] += inp
                row["output_tokens"] += out
                row["total_tokens"] += total
                row["turns"] += 1 if inp > 0 else 0
                row["channels"][channel_id] = row["channels"].get(channel_id, 0) + total
                row["sources"].add(source)

                channel_row["input_tokens"] += inp
                channel_row["output_tokens"] += out
                channel_row["total_tokens"] += total
                channel_row["turns"] += 1 if inp > 0 else 0
                channel_row["sources"].add(source)

                all_total["input_tokens"] += inp
                all_total["output_tokens"] += out
                all_total["total_tokens"] += total
                all_total["sources"].add(source)
                if day_key == today_key:
                    today_total["input_tokens"] += inp
                    today_total["output_tokens"] += out
                    today_total["total_tokens"] += total
                    today_total["sources"].add(source)
                if ts >= lookback_cutoff:
                    recent_total["input_tokens"] += inp
                    recent_total["output_tokens"] += out
                    recent_total["total_tokens"] += total
                    recent_total["sources"].add(source)

        channels = sorted(
            [
                {**row, "sessions": len(row["sessions"]), "mode": lz._usage_mode_from_sources(row.get("sources"))}
                for row in channel_stats.values()
            ],
            key=lambda x: (x["total_tokens"], x["last_active"]),
            reverse=True,
        )
        days = sorted(day_stats.values(), key=lambda x: x["date"], reverse=True)
        for item in (today_total, recent_total, all_total):
            item["mode"] = lz._usage_mode_from_sources(item.get("sources"))
        for row in days:
            row["mode"] = lz._usage_mode_from_sources(row.get("sources"))
        return {"today": today_total, "recent": recent_total, "all": all_total, "channels": channels, "days": days[: max(1, int(lookback_days))]}

    def _reload_usage_panel(self):
        if not hasattr(self, "settings_usage_notice"):
            return
        self._clear_layout(self.settings_usage_list_layout)
        if not lz.is_valid_agent_dir(self.agent_dir):
            self.settings_usage_notice.setText("请先选择有效的 GenericAgent 目录。")
            return
        self.settings_usage_notice.setText("旧会话、以及不返回 usage 的渠道，仍可能只能显示估算。")
        stats = self._collect_usage_stats(lookback_days=7)

        summary_row = QHBoxLayout()
        summary_row.setSpacing(10)
        for title, item in (("今天", stats["today"]), ("近 7 天", stats["recent"]), ("累计", stats["all"])):
            card = self._panel_card()
            box = QVBoxLayout(card)
            box.setContentsMargins(14, 12, 14, 12)
            box.setSpacing(6)
            head = QLabel(title)
            head.setObjectName("cardTitle")
            box.addWidget(head)
            body = QLabel(
                f"{lz._usage_mode_label(item.get('mode'))}\n"
                f"总计 {item['total_tokens']}\n"
                f"输入 {item['input_tokens']}\n"
                f"输出 {item['output_tokens']}"
            )
            body.setObjectName("tokenTree")
            body.setWordWrap(True)
            box.addWidget(body)
            summary_row.addWidget(card, 1)
        self.settings_usage_list_layout.addLayout(summary_row)

        channel_card = self._panel_card()
        channel_box = QVBoxLayout(channel_card)
        channel_box.setContentsMargins(14, 12, 14, 12)
        channel_box.setSpacing(8)
        channel_title = QLabel("按渠道")
        channel_title.setObjectName("cardTitle")
        channel_box.addWidget(channel_title)
        channels = stats.get("channels") or []
        if not channels:
            empty = QLabel("暂无可统计的会话数据")
            empty.setObjectName("mutedText")
            channel_box.addWidget(empty)
        else:
            for row in channels:
                line = QLabel(
                    f"{row['label']} · {lz._usage_mode_label(row.get('mode'))}\n"
                    f"总 {row['total_tokens']}  入 {row['input_tokens']}  出 {row['output_tokens']}  "
                    f"轮次 {row['turns']}  会话 {row['sessions']}"
                )
                line.setWordWrap(True)
                line.setObjectName("softTextSmall")
                channel_box.addWidget(line)
        self.settings_usage_list_layout.addWidget(channel_card)

        day_card = self._panel_card()
        day_box = QVBoxLayout(day_card)
        day_box.setContentsMargins(14, 12, 14, 12)
        day_box.setSpacing(8)
        day_title = QLabel("最近几天")
        day_title.setObjectName("cardTitle")
        day_box.addWidget(day_title)
        days = stats.get("days") or []
        if not days:
            empty = QLabel("最近几天没有可用统计")
            empty.setObjectName("mutedText")
            day_box.addWidget(empty)
        else:
            for row in days:
                parts = []
                for cid, total in sorted(row.get("channels", {}).items(), key=lambda kv: kv[1], reverse=True)[:3]:
                    parts.append(f"{lz._usage_channel_label(cid)} {total}")
                detail = " / ".join(parts) if parts else "无渠道细分"
                line = QLabel(
                    f"{row['date']} · {lz._usage_mode_label(row.get('mode'))}\n"
                    f"总 {row['total_tokens']}  入 {row['input_tokens']}  出 {row['output_tokens']}  "
                    f"轮次 {row['turns']}  |  {detail}"
                )
                line.setWordWrap(True)
                line.setObjectName("softTextSmall")
                day_box.addWidget(line)
        self.settings_usage_list_layout.addWidget(day_card)

    def _reload_about_panel(self):
        if not hasattr(self, "settings_about_list_layout"):
            return
        self._clear_layout(self.settings_about_list_layout)
        rows = [
            ("项目定位", "GenericAgent 的非官方桌面启动器 / 前端壳"),
            ("当前主架构", "Qt 主壳（欢迎页、聊天主区、设置主区）"),
            ("当前状态", "可用，且正持续把 Tk 时代的设置与工具页并到 Qt"),
            ("上游仓库", lz.REPO_URL),
            ("当前配置文件", lz.CONFIG_PATH),
        ]
        for title, value in rows:
            card = self._panel_card()
            line = QHBoxLayout(card)
            line.setContentsMargins(14, 12, 14, 12)
            line.setSpacing(12)
            left = QLabel(title)
            left.setFixedWidth(92)
            left.setObjectName("mutedText")
            right = QLabel(value)
            right.setWordWrap(True)
            right.setTextInteractionFlags(Qt.TextSelectableByMouse)
            right.setObjectName("bodyText")
            line.addWidget(left, 0)
            line.addWidget(right, 1)
            self.settings_about_list_layout.addWidget(card)
