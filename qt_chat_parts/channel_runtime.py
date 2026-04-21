from __future__ import annotations

import io
import json
import os
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from launcher_app import core as lz
from launcher_app.theme import C


class ChannelRuntimeMixin:
    def _channel_extra_packages(self, spec):
        if not isinstance(spec, dict):
            return []
        return lz._split_requirement_tokens(spec.get("pip", ""))

    def _channel_cfg_bucket(self):
        bucket = self.cfg.get("communication_channels")
        if not isinstance(bucket, dict):
            bucket = {}
            self.cfg["communication_channels"] = bucket
        return bucket

    def _channel_runtime_cfg(self, channel_id):
        bucket = self._channel_cfg_bucket()
        item = bucket.get(channel_id)
        if not isinstance(item, dict):
            item = {}
            bucket[channel_id] = item
        return item

    def _channel_is_auto_start(self, channel_id):
        return bool(self._channel_runtime_cfg(channel_id).get("auto_start", False))

    def _channel_set_auto_start(self, channel_id, enabled, persist=True):
        self._channel_runtime_cfg(channel_id)["auto_start"] = bool(enabled)
        if persist:
            lz.save_config(self.cfg)

    def _channel_format_value(self, field, value):
        if field.get("kind") in ("list_str", "list_int"):
            if not isinstance(value, (list, tuple)):
                return ""
            return ", ".join(str(x) for x in value if str(x).strip())
        return "" if value is None else str(value)

    def _channel_parse_value(self, field, raw):
        text = (raw or "").strip()
        kind = field.get("kind", "text")
        if kind == "list_str":
            return [item.strip() for item in text.split(",") if item.strip()]
        if kind == "list_int":
            out = []
            for item in text.split(","):
                item = item.strip()
                if not item:
                    continue
                out.append(int(item) if re.fullmatch(r"-?\d+", item) else item)
            return out
        return text

    def _channel_field_label(self, channel_id, key):
        spec = lz.COMM_CHANNEL_INDEX.get(channel_id, {})
        for field in spec.get("fields", []):
            if field.get("key") == key:
                return field.get("label", key)
        return key

    def _wx_token_info(self):
        path = lz.WX_TOKEN_PATH
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_wx_token_info(self, payload):
        path = lz.WX_TOKEN_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        current = self._wx_token_info()
        current.update(payload or {})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)

    def _open_wechat_qr_dialog(self, show_errors=True):
        try:
            resp = lz.requests.get(f"{lz.WX_BOT_API}/ilink/bot/get_bot_qrcode", params={"bot_type": 3}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            qr_id = data["qrcode"]
            qr_text = data.get("qrcode_img_content", "")
            if not qr_text:
                raise RuntimeError("接口没有返回二维码内容。")
        except Exception as e:
            if show_errors:
                QMessageBox.warning(self, "微信二维码获取失败", str(e))
            return False

        dlg = QDialog(self)
        dlg.setWindowTitle("微信扫码登录")
        dlg.setModal(True)
        dlg.resize(420, 560)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("微信扫码登录")
        title.setObjectName("titleDisplay")
        layout.addWidget(title)

        desc = QLabel("这是上游个人微信 Bot 的登录二维码。扫码并在手机上确认后，启动器会写入绑定缓存，然后再启动微信渠道。")
        desc.setWordWrap(True)
        desc.setObjectName("cardDesc")
        layout.addWidget(desc)

        qr_frame = self._panel_card()
        qr_box = QVBoxLayout(qr_frame)
        qr_box.setContentsMargins(16, 16, 16, 16)
        qr_box.setSpacing(0)
        qr_img = lz.qrcode.make(qr_text).convert("RGB")
        buf = io.BytesIO()
        qr_img.save(buf, format="PNG")
        pix = QPixmap()
        pix.loadFromData(buf.getvalue(), "PNG")
        qr_label = QLabel()
        qr_label.setAlignment(Qt.AlignCenter)
        qr_label.setPixmap(pix.scaled(280, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        qr_box.addWidget(qr_label)
        layout.addWidget(qr_frame)

        status_label = QLabel("请使用微信扫码，确认后会自动完成绑定。")
        status_label.setWordWrap(True)
        status_label.setObjectName("softText")
        layout.addWidget(status_label)

        detail_label = QLabel(f"二维码 ID: {qr_id}")
        detail_label.setWordWrap(True)
        detail_label.setObjectName("mutedText")
        layout.addWidget(detail_label)

        btns = QHBoxLayout()
        btns.setSpacing(10)
        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet(self._action_button_style())
        refresh_btn = QPushButton("重新获取")
        refresh_btn.setStyleSheet(self._action_button_style(primary=True))
        btns.addWidget(close_btn, 0)
        btns.addStretch(1)
        btns.addWidget(refresh_btn, 0)
        layout.addLayout(btns)

        stop_event = threading.Event()
        result = {"ok": False}

        def finish_accept(payload):
            if result["ok"]:
                return
            result["ok"] = True
            stop_event.set()
            bot_token = str(payload.get("bot_token", "") or "").strip()
            bot_id = str(payload.get("ilink_bot_id", "") or "").strip()
            self._save_wx_token_info(
                {
                    "bot_token": bot_token,
                    "ilink_bot_id": bot_id,
                    "login_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
            dlg.accept()

        def close_dialog():
            stop_event.set()
            dlg.reject()

        def restart_dialog():
            stop_event.set()
            dlg.done(2)

        close_btn.clicked.connect(close_dialog)
        refresh_btn.clicked.connect(restart_dialog)

        def poll_status():
            last_status = ""
            while not stop_event.is_set():
                time.sleep(2)
                if stop_event.is_set():
                    return
                try:
                    resp = lz.requests.get(f"{lz.WX_BOT_API}/ilink/bot/get_qrcode_status", params={"qrcode": qr_id}, timeout=60)
                    payload = resp.json()
                except lz.requests.exceptions.ReadTimeout:
                    continue
                except Exception as e:
                    QTimer.singleShot(0, lambda msg=str(e): status_label.setText(f"轮询失败：{msg}"))
                    continue
                status = str(payload.get("status", "") or "")
                if status != last_status:
                    last_status = status
                    QTimer.singleShot(0, lambda st=status: status_label.setText(f"当前状态：{st or '等待扫码'}"))
                if status == "confirmed":
                    QTimer.singleShot(0, lambda p=payload: finish_accept(p))
                    return
                if status == "expired":
                    QTimer.singleShot(
                        0,
                        lambda: (
                            status_label.setText("二维码已过期，请点“重新获取”。"),
                            detail_label.setText(f"二维码 ID: {qr_id}"),
                        ),
                    )
                    return

        threading.Thread(target=poll_status, daemon=True).start()
        code = dlg.exec()
        stop_event.set()
        if code == 2:
            return self._open_wechat_qr_dialog(show_errors=show_errors)
        return bool(result["ok"])

    def _load_channels_source(self):
        py_path, parsed = self._load_mykey_source()
        return self.agent_dir, py_path, parsed

    def _channel_missing_required(self, channel_id, values):
        spec = lz.COMM_CHANNEL_INDEX.get(channel_id, {})
        missing = []
        for key in spec.get("required", []):
            value = values.get(key)
            if isinstance(value, (list, tuple, set)):
                if len(value) == 0:
                    missing.append(self._channel_field_label(channel_id, key))
            elif not str(value or "").strip():
                missing.append(self._channel_field_label(channel_id, key))
        return missing

    def _channel_log_path(self, channel_id):
        base = os.path.join(self.agent_dir, "temp", "launcher_channels")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, f"{channel_id}.log")

    def _channel_tail_log(self, channel_id, limit=1000):
        log_path = self._channel_log_path(channel_id)
        if not os.path.isfile(log_path):
            return ""
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                data = f.read()
            return data[-limit:].strip()
        except Exception:
            return ""

    def _channel_session_title(self, channel_id, started_at=None):
        ts = float(started_at or time.time())
        return f"{lz._usage_channel_label(channel_id)} 进程 {time.strftime('%m-%d %H:%M', time.localtime(ts))}"

    def _channel_session_markdown(self, channel_id, process_status, pid, started_at, ended_at, log_path):
        started_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started_at or time.time()))
        ended_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ended_at)) if ended_at else "仍在运行"
        log_text = self._channel_tail_log(channel_id, limit=16000)
        parts = [
            f"**{lz._usage_channel_label(channel_id)} 渠道进程快照**",
            "",
            f"- 状态：{process_status}",
            f"- PID：{pid or '未知'}",
            f"- 启动时间：{started_text}",
            f"- 结束时间：{ended_text}",
            f"- 日志文件：`{log_path}`" if log_path else "- 日志文件：暂无",
            "",
            "```log",
            log_text or "(暂无日志输出)",
            "```",
        ]
        return "\n".join(parts)

    def _find_reusable_channel_process_session(self, channel_id):
        if not lz.is_valid_agent_dir(self.agent_dir):
            return None
        cid = lz._normalize_usage_channel_id(channel_id, "launcher")
        latest = None
        latest_ts = -1.0
        for meta in lz.list_sessions(self.agent_dir):
            try:
                data = lz.load_session(self.agent_dir, meta["id"])
            except Exception:
                data = None
            if not isinstance(data, dict):
                continue
            if str(data.get("session_kind") or "").strip().lower() != "channel_process":
                continue
            if lz._normalize_usage_channel_id(data.get("channel_id"), "launcher") != cid:
                continue
            ts = float(data.get("updated_at", data.get("process_started_at", 0)) or 0)
            if ts >= latest_ts:
                latest = data
                latest_ts = ts
        return latest

    def _create_channel_process_session(self, channel_id, proc, log_path):
        started_at = time.time()
        existing = self._find_reusable_channel_process_session(channel_id)
        session_id = str((existing or {}).get("id") or "").strip() or uuid.uuid4().hex[:12]
        session = {
            "id": session_id,
            "title": self._channel_session_title(channel_id, started_at),
            "created_at": float((existing or {}).get("created_at", started_at) or started_at),
            "updated_at": started_at,
            "session_kind": "channel_process",
            "session_source_label": lz._usage_channel_label(channel_id),
            "channel_id": lz._normalize_usage_channel_id(channel_id, "launcher"),
            "channel_label": lz._usage_channel_label(channel_id),
            "process_pid": int(getattr(proc, "pid", 0) or 0),
            "process_status": "运行中",
            "process_started_at": started_at,
            "process_ended_at": 0,
            "channel_log_path": log_path,
            "bubbles": [
                {
                    "role": "assistant",
                    "text": self._channel_session_markdown(channel_id, "运行中", getattr(proc, "pid", None), started_at, 0, log_path),
                }
            ],
            "backend_history": [],
            "agent_history": [],
            "llm_idx": 0,
            "token_usage": {"events": []},
            "snapshot": {
                "version": 1,
                "kind": "channel_process",
                "captured_at": started_at,
                "turns": 0,
                "llm_idx": 0,
                "process_pid": int(getattr(proc, "pid", 0) or 0),
                "has_backend_history": False,
                "has_agent_history": False,
            },
        }
        if isinstance(existing, dict) and bool(existing.get("pinned", False)):
            session["pinned"] = True
        self._ensure_session_usage_metadata(session)
        lz.save_session(self.agent_dir, session, touch=False)
        return session_id

    def _sync_channel_process_session(self, channel_id, *, final=False, exit_code=None):
        info = self._channel_procs.get(channel_id) or {}
        session_id = str(info.get("session_id") or "").strip()
        if not session_id:
            return
        data = lz.load_session(self.agent_dir, session_id)
        if not data:
            return
        proc = info.get("proc")
        started_at = float(data.get("process_started_at", data.get("created_at", time.time())) or time.time())
        pid = int((getattr(proc, "pid", None) or data.get("process_pid") or 0) or 0)
        status = "运行中"
        ended_at = 0
        if final:
            ended_at = time.time()
            code = exit_code if exit_code is not None else (proc.returncode if proc else None)
            status = f"已退出 ({code})" if code is not None else "已退出"
        data["process_pid"] = pid
        data["process_status"] = status
        data["process_started_at"] = started_at
        data["process_ended_at"] = ended_at
        data["channel_log_path"] = info.get("log_path") or data.get("channel_log_path") or self._channel_log_path(channel_id)
        markdown = self._channel_session_markdown(channel_id, status, pid, started_at, ended_at, data.get("channel_log_path"))
        bubbles = list(data.get("bubbles") or [])
        if bubbles and bubbles[-1].get("role") == "assistant":
            bubbles[-1]["text"] = markdown
        else:
            bubbles.append({"role": "assistant", "text": markdown})
        data["bubbles"] = bubbles[-1:]
        snapshot = dict(data.get("snapshot") or {})
        snapshot["version"] = int(snapshot.get("version", 1) or 1)
        snapshot["kind"] = "channel_process"
        snapshot["captured_at"] = time.time()
        snapshot["turns"] = 0
        snapshot["llm_idx"] = 0
        snapshot["process_pid"] = pid
        snapshot["has_backend_history"] = False
        snapshot["has_agent_history"] = False
        data["snapshot"] = snapshot
        new_sig = (status, pid, os.path.getsize(data["channel_log_path"]) if os.path.isfile(data["channel_log_path"]) else -1)
        if (not final) and info.get("last_snapshot_sig") == new_sig:
            return
        info["last_snapshot_sig"] = new_sig
        lz.save_session(self.agent_dir, data)
        if self.current_session and self.current_session.get("id") == session_id:
            self.current_session = data
            self._render_session(self.current_session)
            self._set_status("已同步渠道进程快照。")

    def _sync_all_channel_process_sessions(self):
        if not lz.is_valid_agent_dir(self.agent_dir):
            return
        for channel_id, info in list(self._channel_procs.items()):
            proc = info.get("proc")
            if not proc or proc.poll() is not None:
                continue
            try:
                self._sync_channel_process_session(channel_id, final=False)
            except Exception:
                continue

    def _channel_proc_alive(self, channel_id):
        info = self._channel_procs.get(channel_id) or {}
        proc = info.get("proc")
        return bool(proc and proc.poll() is None)

    def _channel_conflict_message(self, channel_id):
        spec = lz.COMM_CHANNEL_INDEX.get(channel_id, {})
        for other_id in spec.get("conflicts_with", []):
            if self._channel_proc_alive(other_id):
                other = lz.COMM_CHANNEL_INDEX.get(other_id, {}).get("label", other_id)
                return f"{spec.get('label', channel_id)} 与 {other} 在上游共用单实例锁，不能同时启动。"
        return ""

    def _channel_status(self, channel_id, values):
        if self._channel_proc_alive(channel_id):
            return "运行中", C["accent"]
        conflict = self._channel_conflict_message(channel_id)
        if conflict:
            return "冲突", C["danger_text"]
        missing = self._channel_missing_required(channel_id, values)
        if missing:
            return "待配置", C["danger_text"]
        info = self._channel_procs.get(channel_id) or {}
        proc = info.get("proc")
        if proc and proc.poll() is not None:
            return f"已退出 ({proc.returncode})", C["danger_text"]
        if self._channel_is_auto_start(channel_id):
            return "待自动启动", C["text_soft"]
        return "未启动", C["muted"]

    def _reload_channels_editor_state(self):
        if not hasattr(self, "settings_channels_notice"):
            return
        self._clear_layout(self.settings_channels_list_layout)
        if not lz.is_valid_agent_dir(self.agent_dir):
            self.settings_channels_notice.setText("请先选择有效的 GenericAgent 目录。")
            return
        _, py_path, parsed = self._load_channels_source()
        self._qt_channel_py_path = py_path
        self._qt_channel_parse_error = parsed.get("error") or ""
        self._qt_channel_configs = list(parsed.get("configs") or [])
        self._qt_channel_passthrough = list(parsed.get("passthrough") or [])
        self._qt_channel_extras = dict(parsed.get("extras") or {})
        self._qt_channel_states = {}
        notices = [py_path]
        if self._qt_channel_parse_error:
            notices.append(f"当前解析失败：{self._qt_channel_parse_error}。继续保存会覆盖成可识别格式。")
        self.settings_channels_notice.setText("\n".join(notices))
        self._render_channel_cards()

    def _render_channel_cards(self):
        self._clear_layout(self.settings_channels_list_layout)
        for spec in lz.COMM_CHANNEL_SPECS:
            values = {field["key"]: self._qt_channel_extras.get(field["key"]) for field in spec.get("fields", [])}
            status_text, status_color = self._channel_status(spec["id"], values)
            card = self._panel_card()
            body = QVBoxLayout(card)
            body.setContentsMargins(16, 14, 16, 14)
            body.setSpacing(8)

            head = QHBoxLayout()
            title = QLabel(spec["label"])
            title.setObjectName("cardTitle")
            head.addWidget(title, 0)
            subtitle = QLabel(spec.get("subtitle", ""))
            subtitle.setObjectName("mutedText")
            head.addWidget(subtitle, 0)
            head.addStretch(1)
            status = QLabel(status_text)
            status.setStyleSheet(f"font-size: 12px; color: {status_color};")
            head.addWidget(status, 0)
            body.addLayout(head)

            note = QLabel(spec.get("notes", ""))
            note.setWordWrap(True)
            note.setObjectName("mutedText")
            body.addWidget(note)

            state = {"widgets": {}, "auto": None}
            for field in spec.get("fields", []):
                row = QHBoxLayout()
                row.setSpacing(10)
                label = QLabel(field.get("label", field["key"]))
                label.setFixedWidth(92)
                label.setObjectName("softTextSmall")
                row.addWidget(label, 0)
                edit = QLineEdit()
                edit.setPlaceholderText(field.get("placeholder", ""))
                edit.setText(self._channel_format_value(field, values.get(field["key"])))
                if field.get("kind") == "password":
                    edit.setEchoMode(QLineEdit.Password)
                row.addWidget(edit, 1)
                state["widgets"][field["key"]] = edit
                if field.get("kind") == "password":
                    toggle = QPushButton("显示")
                    toggle.setCheckable(True)
                    toggle.setStyleSheet(self._action_button_style())

                    def on_toggle(checked, target=edit, btn=toggle):
                        target.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
                        btn.setText("隐藏" if checked else "显示")

                    toggle.toggled.connect(on_toggle)
                    row.addWidget(toggle, 0)
                body.addLayout(row)

            controls = QHBoxLayout()
            controls.setSpacing(8)
            auto_box = QCheckBox("自动启动")
            auto_box.setChecked(self._channel_is_auto_start(spec["id"]))
            controls.addWidget(auto_box, 0)
            controls.addStretch(1)
            if spec["id"] == "wechat":
                token_info = self._wx_token_info()
                has_token = bool(str(token_info.get("bot_token", "") or "").strip())
                bind_btn = QPushButton("重新扫码" if has_token else "扫码登录")
                bind_btn.setStyleSheet(self._action_button_style(primary=not has_token))
                bind_btn.clicked.connect(lambda _=False: self._open_wechat_qr_dialog())
                controls.addWidget(bind_btn, 0)
            save_btn = QPushButton("保存")
            save_btn.setStyleSheet(self._action_button_style())
            save_btn.clicked.connect(lambda _=False: self._qt_channels_save(silent=False))
            controls.addWidget(save_btn, 0)
            start_btn = QPushButton("启动")
            start_btn.setStyleSheet(self._action_button_style(primary=True))
            start_btn.clicked.connect(lambda _=False, cid=spec["id"]: self._start_channel_process(cid))
            controls.addWidget(start_btn, 0)
            stop_btn = QPushButton("停止")
            stop_btn.setStyleSheet(self._action_button_style())
            stop_btn.clicked.connect(lambda _=False, cid=spec["id"]: self._stop_channel_process(cid))
            controls.addWidget(stop_btn, 0)
            log_btn = QPushButton("日志尾部")
            log_btn.setStyleSheet(self._action_button_style())
            log_btn.clicked.connect(lambda _=False, cid=spec["id"], title=spec["label"]: self._show_channel_log_tail(cid, title))
            controls.addWidget(log_btn, 0)
            body.addLayout(controls)

            self._qt_channel_states[spec["id"]] = state
            state["auto"] = auto_box
            self.settings_channels_list_layout.addWidget(card)
        self.settings_channels_list_layout.addStretch(1)

    def _qt_channels_save(self, silent=False, apply_running=True):
        if not self._qt_channel_py_path:
            if not silent:
                QMessageBox.warning(self, "保存失败", "尚未载入通讯渠道配置。")
            return False
        extras = dict(self._qt_channel_extras)
        for spec in lz.COMM_CHANNEL_SPECS:
            state = self._qt_channel_states.get(spec["id"]) or {}
            for field in spec.get("fields", []):
                edit = state.get("widgets", {}).get(field["key"])
                value = self._channel_parse_value(field, edit.text() if edit is not None else "")
                if isinstance(value, list):
                    if value:
                        extras[field["key"]] = value
                    else:
                        extras.pop(field["key"], None)
                else:
                    if str(value or "").strip():
                        extras[field["key"]] = value
                    else:
                        extras.pop(field["key"], None)
            auto = state.get("auto")
            if auto is not None:
                self._channel_set_auto_start(spec["id"], auto.isChecked(), persist=False)
        try:
            txt = lz.serialize_mykey_py(
                configs=self._qt_channel_configs,
                extras=extras,
                passthrough=self._qt_channel_passthrough,
            )
            with open(self._qt_channel_py_path, "w", encoding="utf-8") as f:
                f.write(txt)
            self._qt_channel_extras = extras
            lz.save_config(self.cfg)
        except Exception as e:
            if not silent:
                QMessageBox.critical(self, "保存失败", str(e))
            return False
        restarted = self._restart_running_channels(show_errors=False) if apply_running else 0
        if not silent:
            msg = "已写入 mykey.py 和启动器渠道配置。"
            if restarted:
                msg += f"\n已自动重启 {restarted} 个由启动器托管的通讯渠道。"
            QMessageBox.information(self, "已保存", msg)
        self._reload_channels_editor_state()
        return True

    def _start_channel_process(self, channel_id, show_errors=True):
        spec = lz.COMM_CHANNEL_INDEX.get(channel_id)
        if not spec:
            return False
        if not self._qt_channels_save(silent=True, apply_running=False):
            return False
        if channel_id == "wechat":
            token_info = self._wx_token_info()
            if not str(token_info.get("bot_token", "") or "").strip():
                if not self._open_wechat_qr_dialog(show_errors=show_errors):
                    return False
        if self._channel_proc_alive(channel_id):
            self._reload_channels_editor_state()
            return True
        if not lz.is_valid_agent_dir(self.agent_dir):
            if show_errors:
                QMessageBox.warning(self, "目录无效", "请先选择有效的 GenericAgent 目录。")
            return False
        conflict = self._channel_conflict_message(channel_id)
        if conflict:
            if show_errors:
                QMessageBox.warning(self, "无法启动", conflict)
            return False
        extra_packages = self._channel_extra_packages(spec)
        if not self._check_runtime_dependencies(
            purpose=f"启动{spec.get('label', channel_id)}渠道",
            extra_packages=extra_packages,
            visual=bool(show_errors),
        ):
            if show_errors and not extra_packages:
                QMessageBox.critical(self, "缺少 Python", "未找到可用的系统 Python，或依赖检查失败。")
            return False
        py = lz._resolve_config_path(str(self.cfg.get("python_exe") or "").strip()) or lz._find_system_python()
        if not py or not os.path.isfile(py):
            if show_errors:
                QMessageBox.critical(self, "缺少 Python", "依赖检查完成后仍未找到可用的 Python 可执行文件。")
            return False
        values = {field["key"]: self._qt_channel_extras.get(field["key"]) for field in spec.get("fields", [])}
        missing = self._channel_missing_required(channel_id, values)
        if missing:
            if show_errors:
                QMessageBox.warning(self, "配置不完整", f"{spec.get('label', channel_id)} 还缺少这些字段：\n- " + "\n- ".join(missing))
            return False
        script_path = os.path.join(self.agent_dir, "frontends", spec.get("script", ""))
        if not os.path.isfile(script_path):
            if show_errors:
                QMessageBox.critical(self, "脚本不存在", f"未找到渠道脚本：\n{script_path}")
            return False
        log_path = self._channel_log_path(channel_id)
        try:
            log_handle = open(log_path, "a", encoding="utf-8", buffering=1)
            log_handle.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} start {channel_id} ====\n")
            py_env = lz._external_subprocess_env()
            proc = lz._popen_external_subprocess(
                [py, "-u", script_path],
                cwd=self.agent_dir,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                env=py_env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception as e:
            try:
                log_handle.close()
            except Exception:
                pass
            if show_errors:
                QMessageBox.critical(self, "启动失败", str(e))
            return False
        session_id = self._create_channel_process_session(channel_id, proc, log_path)
        self._channel_procs[channel_id] = {
            "proc": proc,
            "log_handle": log_handle,
            "log_path": log_path,
            "session_id": session_id,
            "last_snapshot_sig": None,
        }
        self._sync_channel_process_session(channel_id, final=False)
        QTimer.singleShot(1200, lambda cid=channel_id, se=show_errors: self._after_channel_launch_check(cid, show_errors=se))
        self._reload_channels_editor_state()
        self._last_session_list_signature = None
        self._refresh_sessions()
        return True

    def _after_channel_launch_check(self, channel_id, show_errors=True):
        info = self._channel_procs.get(channel_id)
        if not info:
            return
        proc = info.get("proc")
        if not proc or proc.poll() is None:
            self._sync_channel_process_session(channel_id, final=False)
            self._reload_channels_editor_state()
            return
        self._sync_channel_process_session(channel_id, final=True, exit_code=proc.returncode)
        self._close_channel_log_handle(channel_id)
        tail = self._channel_tail_log(channel_id)
        self._channel_procs.pop(channel_id, None)
        self._reload_channels_editor_state()
        self._last_session_list_signature = None
        self._refresh_sessions()
        if show_errors:
            QMessageBox.warning(self, "渠道启动失败", f"{channel_id} 已退出，返回码 {proc.returncode}。\n\n日志尾部：\n{tail or '(空)'}")

    def _close_channel_log_handle(self, channel_id):
        info = self._channel_procs.get(channel_id) or {}
        handle = info.get("log_handle")
        if handle:
            try:
                handle.close()
            except Exception:
                pass
            info["log_handle"] = None

    def _stop_channel_process(self, channel_id):
        info = self._channel_procs.get(channel_id)
        if not info:
            self._reload_channels_editor_state()
            return False
        proc = info.get("proc")
        try:
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                        proc.wait(timeout=2)
                    except Exception:
                        pass
        finally:
            exit_code = proc.returncode if proc else None
            self._sync_channel_process_session(channel_id, final=True, exit_code=exit_code)
            self._close_channel_log_handle(channel_id)
            self._channel_procs.pop(channel_id, None)
            self._reload_channels_editor_state()
            self._last_session_list_signature = None
            self._refresh_sessions()
        return True

    def _stop_all_managed_channels(self, refresh=True):
        count = 0
        for channel_id in list(self._channel_procs.keys()):
            if self._stop_channel_process(channel_id):
                count += 1
        if refresh:
            self._reload_channels_editor_state()
        return count

    def _restart_running_channels(self, show_errors=False):
        running = [cid for cid in self._channel_procs if self._channel_proc_alive(cid)]
        restarted = 0
        for channel_id in running:
            self._stop_channel_process(channel_id)
            if self._start_channel_process(channel_id, show_errors=show_errors):
                restarted += 1
        return restarted

    def _show_channel_log_tail(self, channel_id, title):
        tail = self._channel_tail_log(channel_id) or "暂无日志。"
        QMessageBox.information(self, f"{title} 日志尾部", tail)

    def _start_autostart_channels(self):
        if not lz.is_valid_agent_dir(self.agent_dir):
            return
        for spec in lz.COMM_CHANNEL_SPECS:
            if self._channel_is_auto_start(spec["id"]) and not self._channel_proc_alive(spec["id"]):
                self._start_channel_process(spec["id"], show_errors=False)
