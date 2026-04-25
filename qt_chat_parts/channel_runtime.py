from __future__ import annotations

import io
import json
import os
import re
import signal
import shlex
import socket
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

from .common import normalize_remote_agent_dir, normalize_ssh_error_text


class ChannelRuntimeMixin:
    def _channel_dialogs_allowed(self) -> bool:
        return not bool(getattr(self, "_closing_in_progress", False) or getattr(self, "_force_exit_requested", False))

    def _channel_message(self, icon, title: str, text: str, *, detail: str = ""):
        if not self._channel_dialogs_allowed():
            return
        box = QMessageBox(self)
        box.setIcon(icon)
        box.setWindowTitle(str(title or "提示"))
        box.setTextFormat(Qt.PlainText)
        box.setText(str(text or ""))
        if str(detail or "").strip():
            box.setDetailedText(str(detail))
        box.exec()

    def _channel_info(self, title: str, text: str, *, detail: str = ""):
        self._channel_message(QMessageBox.Information, title, text, detail=detail)

    def _channel_warning(self, title: str, text: str, *, detail: str = ""):
        self._channel_message(QMessageBox.Warning, title, text, detail=detail)

    def _channel_critical(self, title: str, text: str, *, detail: str = ""):
        self._channel_message(QMessageBox.Critical, title, text, detail=detail)

    def _safe_channel_ui_call(self, action_name: str, callback, *, show_errors=True):
        if not self._channel_dialogs_allowed():
            return False
        try:
            if callable(callback):
                return callback()
        except Exception as e:
            detail = normalize_ssh_error_text(str(e), context="远端连接")
            msg = f"{str(action_name or '操作').strip() or '操作'}失败：{detail}"
            setter = getattr(self, "_set_status", None)
            if callable(setter):
                try:
                    setter(msg)
                except Exception:
                    pass
            if show_errors:
                self._channel_critical("执行失败", msg)
            return False
        return False

    def _channel_post_ui(self, callback, *, action_name="界面刷新"):
        wrapped = lambda: self._safe_channel_ui_call(action_name, callback, show_errors=False)
        poster = getattr(self, "_api_on_ui_thread", None)
        if callable(poster):
            try:
                poster(wrapped)
                return
            except Exception:
                pass
        try:
            QTimer.singleShot(0, wrapped)
        except Exception:
            pass

    def _apply_loaded_channels_source(self, py_path, parsed):
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

    def _channel_set_external_running(self, channel_id, enabled, *, persist=False):
        cfg = self._channel_runtime_cfg(channel_id)
        cfg["external_running"] = bool(enabled)
        cfg["external_seen_at"] = float(time.time()) if enabled else 0.0
        if persist:
            lz.save_config(self.cfg)

    def _channel_external_running(self, channel_id):
        return bool(self._channel_runtime_cfg(channel_id).get("external_running", False))

    def _wechat_singleton_locked(self):
        lock_sock = None
        try:
            lock_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            lock_sock.bind(("127.0.0.1", 19528))
            return False
        except OSError:
            return True
        except Exception:
            return False
        finally:
            if lock_sock is not None:
                try:
                    lock_sock.close()
                except Exception:
                    pass

    def _refresh_wechat_external_running(self, *, persist=False):
        active = False
        if self._channel_external_running("wechat") != active:
            self._channel_set_external_running("wechat", active, persist=persist)
        return False

    def _find_local_wechat_process_pids(self):
        if os.name != "nt":
            return []
        target = os.path.normcase(os.path.normpath(os.path.join(self.agent_dir, "frontends", "wechatapp.py")))
        cmd = (
            "$ErrorActionPreference='SilentlyContinue'; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -and $_.CommandLine -match 'wechatapp\\.py' } | "
            "ForEach-Object { "
            "  $line=[string]$_.CommandLine; "
            "  \"$($_.ProcessId)`t$line\" "
            "}"
        )
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
            )
        except Exception:
            return []
        if r.returncode != 0:
            return []
        exact = []
        loose = []
        for line in str(r.stdout or "").splitlines():
            text = str(line or "").strip()
            if not text:
                continue
            parts = text.split("\t", 1)
            pid_text = str(parts[0] if parts else "").strip()
            cmdline = str(parts[1] if len(parts) > 1 else "").strip()
            if not re.fullmatch(r"\d+", pid_text):
                continue
            pid = int(pid_text)
            norm_cmd = os.path.normcase(cmdline.replace("/", "\\"))
            if target and (target in norm_cmd):
                exact.append(pid)
                continue
            # 回退匹配：外部实例常用相对路径启动（frontends/wechatapp.py）
            if re.search(r"(^|[\\\s\"'])frontends\\wechatapp\.py($|[\\\s\"'])", norm_cmd):
                loose.append(pid)
                continue
            if re.search(r"(^|[\\\s\"'])wechatapp\.py($|[\\\s\"'])", norm_cmd):
                loose.append(pid)
        # 去重并保持顺序
        seen = set()
        uniq = []
        for pid in (exact + loose):
            if pid in seen:
                continue
            seen.add(pid)
            uniq.append(pid)
        return uniq

    def _terminate_pid_force(self, pid):
        p = int(pid or 0)
        if p <= 0:
            return False
        try:
            if os.name == "nt":
                r = subprocess.run(
                    ["taskkill", "/PID", str(p), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=8,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                return r.returncode == 0
            os.kill(p, signal.SIGTERM)
            return True
        except Exception:
            return False

    def _terminate_external_wechat_instances(self):
        managed = set()
        info = self._channel_procs.get("wechat") or {}
        proc = info.get("proc")
        if proc is not None:
            try:
                pid = int(getattr(proc, "pid", 0) or 0)
                if pid > 0:
                    managed.add(pid)
            except Exception:
                pass
        candidates = [pid for pid in self._find_local_wechat_process_pids() if int(pid or 0) not in managed and int(pid or 0) > 0]
        killed = 0
        failed = []
        for pid in candidates:
            if self._terminate_pid_force(pid):
                killed += 1
            else:
                failed.append(str(pid))
        return killed, failed

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

    def _wx_state_dir(self):
        return os.path.dirname(lz.WX_TOKEN_PATH)

    def _wx_qr_state_path(self):
        return os.path.join(self._wx_state_dir(), "qr_state.json")

    def _wx_qr_debug_log_path(self):
        return os.path.join(self._wx_state_dir(), "qr_login_debug.log")

    def _extract_wechat_token_fields(self, payload):
        data = payload if isinstance(payload, dict) else {}
        candidates = [data]
        for key in ("data", "result", "payload"):
            nested = data.get(key)
            if isinstance(nested, dict):
                candidates.append(nested)
        token = ""
        bot_id = ""
        for item in candidates:
            if not token:
                for key in ("bot_token", "token", "botToken"):
                    token = str(item.get(key) or "").strip()
                    if token:
                        break
            if not bot_id:
                for key in ("ilink_bot_id", "bot_id", "ilinkBotId"):
                    bot_id = str(item.get(key) or "").strip()
                    if bot_id:
                        break
            if token and bot_id:
                break
        return token, bot_id

    def _wechat_qr_payload_summary(self, payload):
        data = payload if isinstance(payload, dict) else {}
        token, bot_id = self._extract_wechat_token_fields(data)
        summary = {
            "payload_type": type(payload).__name__,
            "raw_keys": sorted(list(data.keys())) if isinstance(data, dict) else [],
            "status": str(data.get("status") or "").strip() if isinstance(data, dict) else "",
            "errcode": data.get("errcode") if isinstance(data, dict) else None,
            "errmsg": str(data.get("errmsg") or "").strip() if isinstance(data, dict) else "",
            "has_extracted_token": bool(token),
            "has_bot_id": bool(bot_id),
        }
        for key in ("data", "result", "payload"):
            nested = data.get(key) if isinstance(data, dict) else None
            if isinstance(nested, dict):
                summary[f"{key}_keys"] = sorted(list(nested.keys()))
        return summary

    def _write_wx_qr_state_file(self, state):
        try:
            path = self._wx_qr_state_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            safe = dict(state or {})
            safe.pop("bot_token", None)
            safe.pop("token", None)
            tmp_path = f"{path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(safe, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except Exception:
            pass

    def _append_wx_qr_debug_log(self, event, *, login_id="", qrcode="", status="", payload=None, error="", extra=None):
        try:
            path = self._wx_qr_debug_log_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            entry = {
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "event": str(event or "").strip(),
                "login_id": str(login_id or "").strip(),
                "qrcode": str(qrcode or "").strip(),
                "status": str(status or "").strip(),
                "error": str(error or "").strip(),
            }
            if payload is not None:
                entry["payload"] = self._wechat_qr_payload_summary(payload)
            if isinstance(extra, dict):
                entry["extra"] = dict(extra)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

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

    def _clear_wx_token_info(self):
        path = lz.WX_TOKEN_PATH
        try:
            if os.path.isfile(path):
                os.remove(path)
        except Exception:
            pass

    def _probe_local_wechat_token(self, token_info=None, *, timeout=6):
        info = dict(token_info or self._wx_token_info())
        token = str(info.get("bot_token") or "").strip()
        if not token:
            return False, "missing", "本地微信未绑定。"
        payload = {
            "get_updates_buf": str(info.get("updates_buf") or ""),
            "base_info": {"channel_version": "2.1.8"},
        }
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": uuid.uuid4().hex[:16],
            "Authorization": f"Bearer {token}",
        }
        try:
            resp = lz.requests.post(
                f"{lz.WX_BOT_API}/ilink/bot/getupdates",
                json=payload,
                headers=headers,
                timeout=max(3, int(timeout or 6)),
            )
            resp.raise_for_status()
            data = resp.json()
        except lz.requests.exceptions.ReadTimeout:
            return True, "timeout", ""
        except Exception as e:
            return None, "error", str(e)
        if not isinstance(data, dict):
            return None, "invalid_payload", "微信接口返回格式异常。"
        errcode = int(data.get("errcode", 0) or 0)
        errmsg = str(data.get("errmsg") or "").strip()
        if errcode == -14:
            return False, "session_timeout", errmsg or "session timeout"
        if errcode != 0:
            detail = errmsg or f"errcode={errcode}"
            return None, "api_error", detail
        return True, "ok", ""

    def _local_begin_wechat_qr_login(self, *, timeout=45):
        issued_at = float(time.time())
        login_id = f"{int(issued_at * 1000)}-{uuid.uuid4().hex}"
        try:
            resp = lz.requests.get(f"{lz.WX_BOT_API}/ilink/bot/get_bot_qrcode", params={"bot_type": 3}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            self._append_wx_qr_debug_log("local_qr_issue_error", login_id=login_id, error=str(e or "本地获取二维码失败。"))
            return False, {}, str(e or "本地获取二维码失败。")
        qr_id = str((data or {}).get("qrcode") or "").strip()
        qr_text = str((data or {}).get("qrcode_img_content") or "").strip()
        if (not qr_id) or (not qr_text):
            self._append_wx_qr_debug_log(
                "local_qr_issue_invalid",
                login_id=login_id,
                qrcode=qr_id,
                payload=data if isinstance(data, dict) else {},
                error="接口没有返回完整二维码信息。",
            )
            return False, {}, "接口没有返回完整二维码信息。"
        bucket = getattr(self, "_local_wechat_qr_states", None)
        if not isinstance(bucket, dict):
            bucket = {}
            self._local_wechat_qr_states = bucket
        bucket[login_id] = {
            "ok": True,
            "status": "",
            "error": "",
            "bot_id": "",
            "has_token": False,
            "qrcode": qr_id,
            "issued_at": issued_at,
            "login_id": login_id,
            "token_path": lz.WX_TOKEN_PATH,
            "state_path": self._wx_qr_state_path(),
            "debug_log_path": self._wx_qr_debug_log_path(),
            "raw_keys": [],
            "raw_status": "",
            "payload_summary": {},
            "updated_at": issued_at,
        }

        def write_state(*, status="", error="", bot_id="", has_token=False, raw_payload=None):
            state = bucket.get(login_id)
            if not isinstance(state, dict):
                return
            payload = raw_payload if isinstance(raw_payload, dict) else {}
            payload_summary = self._wechat_qr_payload_summary(payload)
            state.update(
                {
                    "ok": True,
                    "status": str(status or "").strip(),
                    "error": str(error or "").strip(),
                    "bot_id": str(bot_id or "").strip(),
                    "has_token": bool(has_token),
                    "raw_keys": sorted(list(payload.keys())) if isinstance(payload, dict) else [],
                    "raw_status": str(payload.get("status") or "").strip() if isinstance(payload, dict) else "",
                    "payload_summary": payload_summary,
                    "updated_at": float(time.time()),
                }
            )
            self._write_wx_qr_state_file(state)
            self._append_wx_qr_debug_log(
                "local_qr_state",
                login_id=login_id,
                qrcode=qr_id,
                status=str(status or "").strip(),
                payload=payload,
                error=str(error or "").strip(),
            )

        write_state(status="issued", raw_payload=data if isinstance(data, dict) else {})

        def worker():
            deadline = time.time() + max(10, int(timeout or 45))
            while time.time() < deadline:
                try:
                    resp = lz.requests.get(f"{lz.WX_BOT_API}/ilink/bot/get_qrcode_status", params={"qrcode": qr_id}, timeout=60)
                    payload = resp.json()
                except lz.requests.exceptions.ReadTimeout:
                    write_state(status="poll_read_timeout", error="状态接口长轮询超时，继续等待。", raw_payload={})
                    continue
                except Exception as e:
                    write_state(status="poll_error", error=str(e), raw_payload={})
                    time.sleep(2)
                    continue
                payload = payload if isinstance(payload, dict) else {}
                status = str(payload.get("status") or "").strip()
                token, bot_id = self._extract_wechat_token_fields(payload)
                if token:
                    self._save_wx_token_info(
                        {
                            "bot_token": token,
                            "ilink_bot_id": bot_id,
                            "login_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "launcher_login_id": login_id,
                            "launcher_login_issued_at": issued_at,
                        }
                    )
                    write_state(status="confirmed", bot_id=bot_id, has_token=True, raw_payload=payload)
                    return
                if status == "expired":
                    write_state(status="expired", error="二维码已过期，请重新扫码。", bot_id=bot_id, has_token=False, raw_payload=payload)
                    return
                if status == "confirmed":
                    write_state(status=status, error="扫码已确认但接口未返回 bot_token。", bot_id=bot_id, has_token=False, raw_payload=payload)
                    time.sleep(2)
                    continue
                write_state(status=status, bot_id=bot_id, has_token=False, raw_payload=payload)
                time.sleep(2)
            write_state(status="timeout", error="扫码等待超时，请重新扫码。", raw_payload={})

        threading.Thread(target=worker, daemon=True, name=f"wechat-local-qr-{login_id[:8]}").start()
        return True, {
            "qrcode": qr_id,
            "issued_at": issued_at,
            "login_id": login_id,
            "qrcode_img_content": qr_text,
        }, ""

    def _local_wechat_qr_state(self, login_id):
        bucket = getattr(self, "_local_wechat_qr_states", None)
        if not isinstance(bucket, dict):
            return False, {}, "本地二维码状态不存在。"
        state = bucket.get(str(login_id or "").strip())
        if not isinstance(state, dict):
            return False, {}, "本地二维码状态不存在。"
        return True, dict(state), ""

    def _wechat_session_timeout_log_hit(self, text):
        raw = str(text or "")
        lowered = raw.lower()
        return ("[getupdates] err: -14" in lowered) or ("session timeout" in lowered)

    def _channel_log_since(self, channel_id, start_pos=0, limit=16000):
        info = self._channel_procs.get(channel_id) or {}
        path = str(info.get("log_path") or self._channel_log_path(channel_id)).strip()
        if not path or (not os.path.isfile(path)):
            return ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                try:
                    f.seek(max(0, int(start_pos or 0)))
                except Exception:
                    f.seek(0)
                text = f.read()
        except Exception:
            return ""
        if limit and len(text) > int(limit):
            text = text[-int(limit):]
        return text

    def _start_wechat_health_watch(self, *, show_errors=True):
        channel_id = "wechat"
        info = self._channel_procs.get(channel_id) or {}
        proc = info.get("proc")
        if proc is None:
            return
        watch_key = int(getattr(proc, "pid", 0) or 0)
        if watch_key <= 0:
            return
        if info.get("health_watch_pid") == watch_key:
            return
        info["health_watch_pid"] = watch_key
        start_pos = int(info.get("log_start_pos", 0) or 0)

        def worker():
            hit = False
            excerpt = ""
            for _ in range(45):
                time.sleep(1.0)
                current = self._channel_procs.get(channel_id) or {}
                current_proc = current.get("proc")
                if current_proc is None or int(getattr(current_proc, "pid", 0) or 0) != watch_key:
                    return
                if current_proc.poll() is not None:
                    return
                delta = self._channel_log_since(channel_id, start_pos=start_pos, limit=24000)
                if self._wechat_session_timeout_log_hit(delta):
                    hit = True
                    excerpt = delta
                    break
            if not hit:
                return

            def done():
                current = self._channel_procs.get(channel_id) or {}
                current_proc = current.get("proc")
                if current_proc is None or int(getattr(current_proc, "pid", 0) or 0) != watch_key:
                    return
                self._stop_channel_process(channel_id)
                self._clear_wx_token_info()
                msg = "本地微信绑定已失效（getUpdates 返回 -14 session timeout），已自动停止微信进程并清除失效 token。请重新扫码绑定。"
                self._set_status(msg)
                if show_errors:
                    self._channel_warning("微信绑定失效", msg, detail=(excerpt or ""))

            self._channel_post_ui(done, action_name="微信健康检查回调")

        threading.Thread(target=worker, daemon=True, name="wechat-health-watch").start()

    def _channel_target_context(self):
        getter = getattr(self, "_settings_target_context", None)
        ctx = getter() if callable(getter) else {"is_remote": False}
        if not isinstance(ctx, dict):
            ctx = {"is_remote": False}
        is_remote = bool(ctx.get("is_remote"))
        dev = dict(ctx.get("device") or {}) if (is_remote and isinstance(ctx.get("device"), dict)) else {}
        return is_remote, dev, ctx

    def _extract_json_payload(self, text):
        raw = str(text or "").strip()
        if not raw:
            return None
        for line in reversed(raw.splitlines()):
            s = str(line or "").strip()
            if not s.startswith("{"):
                continue
            try:
                data = json.loads(s)
            except Exception:
                data = None
            if isinstance(data, dict):
                return data
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = None
        return parsed if isinstance(parsed, dict) else None

    def _remote_exec_json_script(self, device, script_text: str, *, timeout=120):
        opener = getattr(self, "_settings_target_open_remote_client", None)
        if not callable(opener):
            return False, {}, "当前构建缺少远端连接能力。"
        exec_remote = getattr(self, "_vps_exec_remote", None)
        if not callable(exec_remote):
            return False, {}, "当前构建缺少远端命令执行能力。"
        dev = device if isinstance(device, dict) else {}
        try:
            client, err = opener(dev, timeout=min(20, max(8, int(timeout or 120))))
        except Exception as e:
            return False, {}, normalize_ssh_error_text(str(e), context="远端 SSH 连接")
        if client is None:
            msg = str(err or "远端 SSH 连接失败。").strip() or "远端 SSH 连接失败。"
            return False, {}, normalize_ssh_error_text(msg, context="远端 SSH 连接")
        try:
            remote_dir = normalize_remote_agent_dir(dev.get("agent_dir"), username=dev.get("username"))
            python_cmd = str(dev.get("python_cmd") or "python3").strip() or "python3"
            q_dir = shlex.quote(remote_dir)
            q_py = shlex.quote(python_cmd)
            cmd = (
                "set -e; "
                f"cd {q_dir}; "
                f"PY_BIN={q_py}; "
                "if ! command -v \"$PY_BIN\" >/dev/null 2>&1; then "
                "if command -v python3 >/dev/null 2>&1; then PY_BIN=python3; "
                "elif command -v python >/dev/null 2>&1; then PY_BIN=python; "
                "else echo '{\"ok\": false, \"error\": \"未找到可用 Python\"}'; exit 0; fi; "
                "fi; "
                "GA_PY_BIN=\"$PY_BIN\" \"$PY_BIN\" - <<'GA_REMOTE_PY'\n"
                f"{script_text}\n"
                "GA_REMOTE_PY"
            )
            try:
                rc, out, err_text = exec_remote(client, cmd, timeout=max(15, int(timeout or 120)))
            except Exception as e:
                return False, {}, normalize_ssh_error_text(str(e), context="远端命令执行")
            raw = "\n".join(part for part in [str(out or "").strip(), str(err_text or "").strip()] if part).strip()
            payload = self._extract_json_payload(raw)
            if rc != 0:
                detail = str((payload or {}).get("error") or "").strip() if isinstance(payload, dict) else ""
                msg = detail or raw or f"远端命令执行失败 (exit {rc})"
                msg = normalize_ssh_error_text(msg, context="远端命令执行")
                return False, (payload if isinstance(payload, dict) else {}), msg
            if not isinstance(payload, dict):
                return False, {}, raw or "远端返回格式异常（缺少 JSON 结果）。"
            return True, payload, ""
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _remote_wx_token_info(self, device):
        script = (
            "import json, os\n"
            "base = os.getcwd()\n"
            "launcher_home = os.path.join(base, 'temp', 'launcher_home')\n"
            "path = os.path.join(launcher_home, '.wxbot', 'token.json')\n"
            "data = {}\n"
            "if os.path.isfile(path):\n"
            "    try:\n"
            "        with open(path, 'r', encoding='utf-8') as f:\n"
            "            obj = json.load(f)\n"
            "        if isinstance(obj, dict):\n"
            "            data = obj\n"
            "    except Exception:\n"
            "        data = {}\n"
            "print(json.dumps({'ok': True, 'data': data, 'path': path}, ensure_ascii=False))\n"
        )
        ok, payload, err = self._remote_exec_json_script(device, script, timeout=40)
        if (not ok) or (not isinstance(payload, dict)):
            return {}, err
        return dict(payload.get("data") or {}), ""

    def _save_remote_wx_token_info(self, device, payload):
        raw_payload = json.dumps(payload if isinstance(payload, dict) else {}, ensure_ascii=False)
        script = (
            "import json, os\n"
            f"payload = json.loads({raw_payload!r})\n"
            "base = os.getcwd()\n"
            "launcher_home = os.path.join(base, 'temp', 'launcher_home')\n"
            "path = os.path.join(launcher_home, '.wxbot', 'token.json')\n"
            "os.makedirs(os.path.dirname(path), exist_ok=True)\n"
            "current = {}\n"
            "if os.path.isfile(path):\n"
            "    try:\n"
            "        with open(path, 'r', encoding='utf-8') as f:\n"
            "            obj = json.load(f)\n"
            "        if isinstance(obj, dict):\n"
            "            current = obj\n"
            "    except Exception:\n"
            "        current = {}\n"
            "if isinstance(payload, dict):\n"
            "    current.update(payload)\n"
            "with open(path, 'w', encoding='utf-8') as f:\n"
            "    json.dump(current, f, ensure_ascii=False, indent=2)\n"
            "token = str(current.get('bot_token') or '').strip()\n"
            "bot_id = str(current.get('ilink_bot_id') or '').strip()\n"
            "print(json.dumps({'ok': True, 'path': path, 'has_token': bool(token), 'bot_id': bot_id}, ensure_ascii=False))\n"
        )
        ok, out, err = self._remote_exec_json_script(device, script, timeout=45)
        if not ok:
            return False, err
        if not bool(out.get("ok", False)):
            return False, str(out.get("error") or "写入远端微信 token 失败。").strip() or "写入远端微信 token 失败。"
        if not bool(out.get("has_token", False)):
            return False, "远端扫码确认后未拿到有效 bot_token，请重新扫码。"
        return True, ""

    def _remote_begin_wechat_qr_login(self, device, *, timeout=45):
        script = (
            "import json, os, site, subprocess, sys, time, uuid\n"
            "import requests\n"
            "API = 'https://ilinkai.weixin.qq.com'\n"
            "base = os.getcwd()\n"
            "launcher_home = os.path.join(base, 'temp', 'launcher_home')\n"
            "wx_dir = os.path.join(launcher_home, '.wxbot')\n"
            "token_path = os.path.join(wx_dir, 'token.json')\n"
            "state_path = os.path.join(wx_dir, 'qr_state.json')\n"
            "os.makedirs(wx_dir, exist_ok=True)\n"
            "r = requests.get(f'{API}/ilink/bot/get_bot_qrcode', params={'bot_type': 3}, timeout=20)\n"
            "r.raise_for_status()\n"
            "data = r.json() if hasattr(r, 'json') else {}\n"
            "qr_id = str(data.get('qrcode') or '').strip()\n"
            "qr_text = str(data.get('qrcode_img_content') or '').strip()\n"
            "issued_at = time.time()\n"
            "login_id = f'{int(issued_at * 1000)}-{uuid.uuid4().hex}'\n"
            "if (not qr_id) or (not qr_text):\n"
            "    print(json.dumps({'ok': False, 'error': '远端接口未返回有效二维码。'}, ensure_ascii=False))\n"
            "    raise SystemExit(0)\n"
            "state = {\n"
            "    'ok': True,\n"
            "    'qrcode': qr_id,\n"
            "    'issued_at': issued_at,\n"
            "    'login_id': login_id,\n"
            "    'status': 'wait',\n"
            "    'error': '',\n"
            "    'bot_id': '',\n"
            "    'has_token': False,\n"
            "    'updated_at': time.time(),\n"
            "}\n"
            "with open(state_path, 'w', encoding='utf-8') as f:\n"
            "    json.dump(state, f, ensure_ascii=False, indent=2)\n"
            "poll_ctx = json.dumps({\n"
            "    'qr_id': qr_id,\n"
            "    'token_path': token_path,\n"
            "    'state_path': state_path,\n"
            "    'issued_at': issued_at,\n"
            "    'login_id': login_id,\n"
            "    'deadline_secs': 180,\n"
            "}, ensure_ascii=False)\n"
            "poll_code = '''\n"
            "import json, os, time, requests\n"
            "API = 'https://ilinkai.weixin.qq.com'\n"
            "ctx = json.loads(@@POLL_CTX@@)\n"
            "qr_id = str(ctx.get('qr_id') or '').strip()\n"
            "token_path = str(ctx.get('token_path') or '').strip()\n"
            "state_path = str(ctx.get('state_path') or '').strip()\n"
            "issued_at = float(ctx.get('issued_at') or 0)\n"
            "login_id = str(ctx.get('login_id') or '').strip()\n"
            "deadline = time.time() + max(30.0, float(ctx.get('deadline_secs') or 180.0))\n"
            "def extract_fields(data):\n"
            "    data = data if isinstance(data, dict) else {}\n"
            "    candidates = [data]\n"
            "    for key in ('data', 'result', 'payload'):\n"
            "        nested = data.get(key)\n"
            "        if isinstance(nested, dict):\n"
            "            candidates.append(nested)\n"
            "    token = ''\n"
            "    bot_id = ''\n"
            "    for item in candidates:\n"
            "        if not token:\n"
            "            for key in ('bot_token', 'token', 'botToken'):\n"
            "                token = str(item.get(key) or '').strip()\n"
            "                if token:\n"
            "                    break\n"
            "        if not bot_id:\n"
            "            for key in ('ilink_bot_id', 'bot_id', 'ilinkBotId'):\n"
            "                bot_id = str(item.get(key) or '').strip()\n"
            "                if bot_id:\n"
            "                    break\n"
            "        if token and bot_id:\n"
            "            break\n"
            "    return token, bot_id\n"
            "def write_state(status='', error='', bot_id='', has_token=False):\n"
            "    payload = {\n"
            "        'ok': True,\n"
            "        'qrcode': qr_id,\n"
            "        'issued_at': float(issued_at or 0),\n"
            "        'login_id': login_id,\n"
            "        'status': str(status or '').strip(),\n"
            "        'error': str(error or '').strip(),\n"
            "        'bot_id': str(bot_id or '').strip(),\n"
            "        'has_token': bool(has_token),\n"
            "        'updated_at': time.time(),\n"
            "    }\n"
            "    os.makedirs(os.path.dirname(state_path), exist_ok=True)\n"
            "    with open(state_path, 'w', encoding='utf-8') as f:\n"
            "        json.dump(payload, f, ensure_ascii=False, indent=2)\n"
            "while time.time() < deadline:\n"
            "    try:\n"
            "        resp = requests.get(f'{API}/ilink/bot/get_qrcode_status', params={'qrcode': qr_id}, timeout=30)\n"
            "        resp.raise_for_status()\n"
            "        data = resp.json() if hasattr(resp, 'json') else {}\n"
            "    except requests.exceptions.ReadTimeout:\n"
            "        write_state(status='poll_read_timeout', error='状态接口长轮询超时，继续等待。')\n"
            "        continue\n"
            "    except Exception as e:\n"
            "        write_state(status='poll_error', error=str(e))\n"
            "        time.sleep(2)\n"
            "        continue\n"
            "    status = str(data.get('status') or '').strip() or 'wait'\n"
            "    token, bot_id = extract_fields(data)\n"
            "    if token:\n"
            "        os.makedirs(os.path.dirname(token_path), exist_ok=True)\n"
            "        with open(token_path, 'w', encoding='utf-8') as f:\n"
            "            json.dump({'bot_token': token, 'ilink_bot_id': bot_id, 'login_time': time.strftime('%Y-%m-%d %H:%M:%S'), 'launcher_login_id': login_id, 'launcher_login_issued_at': issued_at}, f, ensure_ascii=False, indent=2)\n"
            "        write_state(status='confirmed', bot_id=bot_id, has_token=True)\n"
            "        raise SystemExit(0)\n"
            "    if status == 'expired':\n"
            "        write_state(status='expired', error='二维码已过期，请重新扫码。')\n"
            "        raise SystemExit(0)\n"
            "    if status == 'confirmed':\n"
            "        write_state(status='confirmed', error='扫码已确认但接口未返回 bot_token。', bot_id=bot_id, has_token=False)\n"
            "        time.sleep(2)\n"
            "        continue\n"
            "    write_state(status=status, bot_id=bot_id, has_token=False)\n"
            "    time.sleep(2)\n"
            "write_state(status='timeout', error='扫码等待超时，请重新扫码。')\n"
            "'''\n"
            "poll_code = poll_code.replace('@@POLL_CTX@@', repr(poll_ctx))\n"
            "proc_env = dict(os.environ)\n"
            "proc_env['HOME'] = launcher_home\n"
            "user_site = ''\n"
            "user_base = ''\n"
            "try:\n"
            "    user_site = str(site.getusersitepackages() or '').strip()\n"
            "except Exception:\n"
            "    user_site = ''\n"
            "try:\n"
            "    user_base = str(site.getuserbase() or '').strip()\n"
            "except Exception:\n"
            "    user_base = ''\n"
            "if user_site:\n"
            "    prev_path = str(proc_env.get('PYTHONPATH') or '').strip()\n"
            "    proc_env['PYTHONPATH'] = user_site if not prev_path else (user_site + os.pathsep + prev_path)\n"
            "if user_base:\n"
            "    proc_env['PYTHONUSERBASE'] = user_base\n"
            "py_bin = str(os.environ.get('GA_PY_BIN') or sys.executable or 'python3').strip() or 'python3'\n"
            "subprocess.Popen(\n"
            "    [py_bin, '-c', poll_code],\n"
            "    cwd=base,\n"
            "    stdin=subprocess.DEVNULL,\n"
            "    stdout=subprocess.DEVNULL,\n"
            "    stderr=subprocess.DEVNULL,\n"
            "    env=proc_env,\n"
            "    start_new_session=True,\n"
            "    close_fds=True,\n"
            ")\n"
            "print(json.dumps({'ok': True, 'qrcode': qr_id, 'issued_at': issued_at, 'login_id': login_id, 'qrcode_img_content': qr_text, 'state_path': state_path, 'token_path': token_path}, ensure_ascii=False))\n"
        )
        ok, payload, err = self._remote_exec_json_script(device, script, timeout=max(20, int(timeout or 45)))
        if not ok:
            return False, {}, str(err or "远端获取二维码失败。").strip() or "远端获取二维码失败。"
        if not bool(payload.get("ok", False)):
            return False, dict(payload or {}), str(payload.get("error") or "远端获取二维码失败。").strip() or "远端获取二维码失败。"
        return True, dict(payload or {}), ""

    def _remote_wechat_qr_state(self, device):
        script = (
            "import json, os\n"
            "base = os.getcwd()\n"
            "state_path = os.path.join(base, 'temp', 'launcher_home', '.wxbot', 'qr_state.json')\n"
            "token_path = os.path.join(base, 'temp', 'launcher_home', '.wxbot', 'token.json')\n"
            "state = {}\n"
            "if os.path.isfile(state_path):\n"
            "    try:\n"
            "        with open(state_path, 'r', encoding='utf-8') as f:\n"
            "            obj = json.load(f)\n"
            "        if isinstance(obj, dict):\n"
            "            state = obj\n"
            "    except Exception:\n"
            "        state = {}\n"
            "token = ''\n"
            "login_id = str(state.get('login_id') or '').strip()\n"
            "token_login_id = ''\n"
            "bot_id = str(state.get('bot_id') or '').strip()\n"
            "if os.path.isfile(token_path):\n"
            "    try:\n"
            "        with open(token_path, 'r', encoding='utf-8') as f:\n"
            "            obj = json.load(f)\n"
            "        if isinstance(obj, dict):\n"
            "            token = str(obj.get('bot_token') or '').strip()\n"
            "            token_login_id = str(obj.get('launcher_login_id') or '').strip()\n"
            "            if (not bot_id) and token_login_id == login_id:\n"
            "                bot_id = str(obj.get('ilink_bot_id') or '').strip()\n"
            "    except Exception:\n"
            "        token = ''\n"
            "        token_login_id = ''\n"
            "has_token = bool(token) and bool(login_id) and token_login_id == login_id\n"
            "print(json.dumps({'ok': True, 'status': str(state.get('status') or '').strip(), 'error': str(state.get('error') or '').strip(), 'bot_id': bot_id, 'has_token': has_token, 'qrcode': str(state.get('qrcode') or '').strip(), 'issued_at': float(state.get('issued_at') or 0), 'login_id': login_id, 'token_login_id': token_login_id}, ensure_ascii=False))\n"
        )
        ok, payload, err = self._remote_exec_json_script(device, script, timeout=25)
        if not ok:
            return False, {}, str(err or "读取远端二维码状态失败。").strip() or "读取远端二维码状态失败。"
        if not bool(payload.get("ok", False)):
            return False, dict(payload or {}), str(payload.get("error") or "读取远端二维码状态失败。").strip() or "读取远端二维码状态失败。"
        return True, dict(payload or {}), ""

    def _open_wechat_qr_dialog(self, show_errors=True, remote_device=None):
        remote_dev = dict(remote_device or {}) if isinstance(remote_device, dict) else {}
        try:
            if remote_dev:
                ok, payload, err = self._remote_begin_wechat_qr_login(remote_dev, timeout=45)
                if not ok:
                    raise RuntimeError(str(err or "远端获取二维码失败。"))
                qr_id = str(payload.get("qrcode") or "").strip()
                qr_text = str(payload.get("qrcode_img_content") or "").strip()
            else:
                ok, payload, err = self._local_begin_wechat_qr_login(timeout=45)
                if not ok:
                    raise RuntimeError(str(err or "本地获取二维码失败。"))
                qr_id = str(payload.get("qrcode") or "").strip()
                qr_text = str(payload.get("qrcode_img_content") or "").strip()
            if not qr_text:
                raise RuntimeError("接口没有返回二维码内容。")
        except Exception as e:
            if show_errors:
                self._channel_warning("微信二维码获取失败", str(e))
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
        if isinstance(remote_device, dict) and remote_device:
            host = str(remote_device.get("host") or "").strip() or "-"
            user = str(remote_device.get("username") or "").strip() or "-"
            remote_dir = str(remote_device.get("agent_dir") or "").strip() or "-"
            target_label = QLabel(
                "当前绑定目标：\n"
                f"- 设备：{user}@{host}\n"
                f"- 目录：{remote_dir}\n"
                f"- Token 路径：{remote_dir}/temp/launcher_home/.wxbot/token.json"
            )
            target_label.setWordWrap(True)
            target_label.setObjectName("mutedText")
            layout.addWidget(target_label)

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
        local_login_id = str(payload.get("login_id") or "").strip() if not remote_dev else ""
        local_issued_at = float(payload.get("issued_at", 0) or 0) if not remote_dev else 0.0
        result = {
            "ok": False,
            "confirmed": False,
            "saving_remote": False,
            "remote_issued_at": float(payload.get("issued_at", 0) or 0) if remote_dev else 0.0,
            "remote_login_id": str(payload.get("login_id") or "").strip() if remote_dev else "",
            "local_issued_at": local_issued_at,
            "local_login_id": local_login_id,
            "dialog_alive": True,
        }

        def extract_token_fields(payload):
            return self._extract_wechat_token_fields(payload)

        def post_dialog_ui(callback, *, action_name="微信扫码窗口回调", allow_after_stop=False):
            def run():
                if not bool(result.get("dialog_alive", False)):
                    return
                if stop_event.is_set() and not allow_after_stop:
                    return
                if callable(callback):
                    callback()

            self._channel_post_ui(run, action_name=action_name)

        def display_status_text(status):
            st = str(status or "").strip()
            if st in ("", "issued", "wait"):
                return "等待扫码/手机确认"
            if st == "poll_read_timeout":
                return "正在等待扫码确认（状态接口长轮询中）"
            return st

        def auto_accept_by_token(token_payload, *, source="检测"):
            if result["ok"] or bool(result.get("saving_remote")):
                return False
            token, bot_id = extract_token_fields(token_payload)
            if not token:
                return False
            status_label.setText(f"{source}到有效 token，已自动完成绑定。")
            if bot_id:
                detail_label.setText(f"二维码 ID: {qr_id}\nBot: {bot_id}")
            result["ok"] = True
            stop_event.set()
            dlg.accept()
            return True

        def finish_accept(payload):
            if result["ok"] or bool(result.get("saving_remote")):
                return False
            bot_token, bot_id = extract_token_fields(payload)
            if not bot_token:
                status_label.setText("扫码已确认，正在等待 token 下发…")
                result["ok"] = False
                return False
            token_payload = {
                "bot_token": bot_token,
                "ilink_bot_id": bot_id,
                "login_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            if remote_dev:
                if result.get("remote_login_id"):
                    token_payload["launcher_login_id"] = str(result.get("remote_login_id") or "").strip()
                if result.get("remote_issued_at", 0):
                    token_payload["launcher_login_issued_at"] = float(result.get("remote_issued_at", 0) or 0)
                result["saving_remote"] = True
                stop_event.set()
                status_label.setText("扫码成功，正在写入远端微信绑定缓存…")

                def worker():
                    ok, err = self._save_remote_wx_token_info(remote_dev, token_payload)

                    def done():
                        result["saving_remote"] = False
                        if ok:
                            result["ok"] = True
                            dlg.accept()
                            return
                        status_label.setText(f"写入远端缓存失败：{err}")
                        if show_errors:
                            self._channel_warning("写入失败", str(err or "写入远端微信 token 失败。"))

                    post_dialog_ui(done, action_name="微信远端扫码回调", allow_after_stop=True)

                threading.Thread(target=worker, name="wechat-remote-token-save", daemon=True).start()
                return True
            stop_event.set()
            token_payload["launcher_login_id"] = str(result.get("local_login_id") or "").strip()
            token_payload["launcher_login_issued_at"] = float(result.get("local_issued_at", 0) or 0)
            self._save_wx_token_info(token_payload)
            result["ok"] = True
            dlg.accept()
            return True

        def close_dialog():
            stop_event.set()
            dlg.reject()

        def restart_dialog():
            stop_event.set()
            dlg.done(2)

        close_btn.clicked.connect(close_dialog)
        refresh_btn.clicked.connect(restart_dialog)

        def poll_token_ready():
            if remote_dev:
                return
            while not stop_event.is_set():
                time.sleep(2)
                if stop_event.is_set():
                    return
                if not bool(result.get("confirmed")):
                    continue
                if result["ok"] or bool(result.get("saving_remote")):
                    return
                try:
                    if remote_dev:
                        token_info, _err = self._remote_wx_token_info(remote_dev)
                    else:
                        token_info = self._wx_token_info()
                except Exception:
                    token_info = {}
                if not isinstance(token_info, dict):
                    token_info = {}
                token_login_id = str(token_info.get("launcher_login_id") or "").strip()
                if (
                    str(token_info.get("bot_token", "") or "").strip()
                    and token_login_id
                    and token_login_id == str(result.get("local_login_id") or "").strip()
                ):
                    post_dialog_ui(lambda p=dict(token_info): auto_accept_by_token(p, source="已确认"), action_name="微信本地 token 确认回调")
                    return

        def poll_remote_status():
            last_status = ""
            while (not stop_event.is_set()) and remote_dev:
                time.sleep(2)
                if stop_event.is_set():
                    return
                if result["ok"] or bool(result.get("saving_remote")):
                    return
                ok, payload, err = self._remote_wechat_qr_state(remote_dev)
                if not ok:
                    post_dialog_ui(lambda msg=str(err or "读取远端二维码状态失败。"): status_label.setText(msg), action_name="微信远端扫码状态回调")
                    continue
                state = dict(payload or {})
                status = str(state.get("status") or "").strip()
                state_qr = str(state.get("qrcode") or "").strip()
                issued_at = float(state.get("issued_at", 0) or 0)
                login_id = str(state.get("login_id") or "").strip()
                if state_qr and state_qr != qr_id:
                    continue
                if result["remote_login_id"] and login_id and login_id != result["remote_login_id"]:
                    continue
                if result["remote_issued_at"] > 0 and issued_at > 0 and abs(issued_at - result["remote_issued_at"]) > 0.5:
                    continue
                if status and status != last_status:
                    last_status = status
                    text = f"当前状态：{display_status_text(status)}"
                    if status == "confirmed":
                        text = "扫码已确认，服务器正在写入 token…"
                    post_dialog_ui(lambda st=text: status_label.setText(st), action_name="微信远端扫码状态回调")
                if bool(state.get("has_token", False)):
                    try:
                        token_info, _err = self._remote_wx_token_info(remote_dev)
                    except Exception:
                        token_info = {}
                    token_login_id = ""
                    if isinstance(token_info, dict):
                        token_login_id = str(token_info.get("launcher_login_id") or "").strip()
                    if (
                        isinstance(token_info, dict)
                        and str(token_info.get("bot_token", "") or "").strip()
                        and (
                            (not result["remote_login_id"])
                            or (token_login_id and token_login_id == result["remote_login_id"])
                        )
                    ):
                        post_dialog_ui(lambda p=dict(token_info): auto_accept_by_token(p, source="远端"), action_name="微信远端 token 确认回调")
                        return
                if status in ("expired", "timeout", "poll_error"):
                    msg = str(state.get("error") or "远端扫码失败，请重新扫码。").strip() or "远端扫码失败，请重新扫码。"
                    post_dialog_ui(lambda text=msg: status_label.setText(text), action_name="微信远端扫码状态回调")
                    return

        def poll_status():
            if remote_dev:
                return
            last_status = ""
            while not stop_event.is_set():
                time.sleep(2)
                if stop_event.is_set():
                    return
                try:
                    ok, payload, err = self._local_wechat_qr_state(result.get("local_login_id"))
                    if not ok:
                        raise RuntimeError(str(err or "读取本地二维码状态失败。"))
                except Exception as e:
                    post_dialog_ui(lambda msg=str(e): status_label.setText(f"轮询失败：{msg}"), action_name="微信本地扫码状态回调")
                    continue
                payload = payload if isinstance(payload, dict) else {}
                status = str(payload.get("status", "") or "")
                if status != last_status:
                    last_status = status
                    post_dialog_ui(lambda st=status: status_label.setText(f"当前状态：{display_status_text(st)}"), action_name="微信本地扫码状态回调")
                if status == "confirmed":
                    result["confirmed"] = True
                    if bool(payload.get("has_token", False)):
                        try:
                            token_info = self._wx_token_info()
                        except Exception:
                            token_info = {}
                        token_login_id = str((token_info or {}).get("launcher_login_id") or "").strip()
                        if (
                            isinstance(token_info, dict)
                            and str(token_info.get("bot_token", "") or "").strip()
                            and token_login_id
                            and token_login_id == str(result.get("local_login_id") or "").strip()
                        ):
                            post_dialog_ui(lambda p=dict(token_info): auto_accept_by_token(p, source="本地"), action_name="微信本地 token 确认回调")
                            return
                    post_dialog_ui(lambda: status_label.setText("扫码已确认，正在等待 token 下发…"), action_name="微信本地扫码状态回调")
                    continue
                if status == "expired":
                    post_dialog_ui(
                        lambda: (
                            status_label.setText("二维码已过期，请点“重新获取”。"),
                            detail_label.setText(f"二维码 ID: {qr_id}"),
                        ),
                        action_name="微信本地扫码状态回调",
                    )
                    return
                if status in ("timeout", "poll_error"):
                    msg = str(payload.get("error") or "本地扫码失败，请重新扫码。").strip() or "本地扫码失败，请重新扫码。"
                    post_dialog_ui(lambda text=msg: status_label.setText(text), action_name="微信本地扫码状态回调")
                    return

        threading.Thread(target=poll_token_ready, daemon=True, name="wechat-token-watch").start()
        if remote_dev:
            threading.Thread(target=poll_remote_status, daemon=True, name="wechat-remote-status-watch").start()
        threading.Thread(target=poll_status, daemon=True).start()
        code = dlg.exec()
        stop_event.set()
        result["dialog_alive"] = False
        if code == 2:
            return self._open_wechat_qr_dialog(show_errors=show_errors, remote_device=remote_device)
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
        latest_sid = ""
        latest_ts = -1.0
        for meta in lz.list_sessions(self.agent_dir):
            if str(meta.get("session_kind") or "").strip().lower() != "channel_process":
                continue
            if lz._normalize_usage_channel_id(meta.get("channel_id"), "launcher") != cid:
                continue
            ts = float(meta.get("updated_at", 0) or 0)
            if ts >= latest_ts:
                latest_sid = str(meta.get("id") or "").strip()
                latest_ts = ts
        if not latest_sid:
            return None
        try:
            return lz.load_session(self.agent_dir, latest_sid)
        except Exception:
            return None

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
        remote_sync = getattr(self, "_sync_remote_device_channel_process_sessions", None)
        if callable(remote_sync):
            try:
                remote_sync()
            except Exception:
                pass
        remote_chat_sync = getattr(self, "_sync_remote_device_launcher_sessions", None)
        if callable(remote_chat_sync):
            try:
                remote_chat_sync()
            except Exception:
                pass
        refresher = getattr(self, "_refresh_channels_runtime_status_labels", None)
        if callable(refresher):
            try:
                refresher()
            except Exception:
                pass

    def _channel_proc_alive(self, channel_id):
        info = self._channel_procs.get(channel_id) or {}
        proc = info.get("proc")
        return bool(proc and proc.poll() is None)

    def _remote_channel_cache_session_id(self, device_id, channel_id):
        did = str(device_id or "").strip()
        cid = lz._normalize_usage_channel_id(channel_id, "launcher")
        if (not did) or (not cid):
            return ""
        return f"rdev_{did}_{cid}_proc"

    def _remote_channel_cached_session(self, device_id, channel_id):
        if not lz.is_valid_agent_dir(self.agent_dir):
            return {}
        sid = self._remote_channel_cache_session_id(device_id, channel_id)
        if not sid:
            return {}
        data = lz.load_session(self.agent_dir, sid)
        return dict(data) if isinstance(data, dict) else {}

    def _remote_channel_status_check_age(self, device_id, channel_id):
        did = str(device_id or "").strip()
        cid = lz._normalize_usage_channel_id(channel_id, "launcher")
        if (not did) or (not cid):
            return float("inf"), float("inf")
        checked_map = getattr(self, "_remote_channel_checked_at", None)
        if not isinstance(checked_map, dict):
            checked_map = {}
            self._remote_channel_checked_at = checked_map
        device_map = getattr(self, "_remote_channel_device_checked_at", None)
        if not isinstance(device_map, dict):
            device_map = {}
            self._remote_channel_device_checked_at = device_map
        now = float(time.time())
        channel_checked_at = float(checked_map.get((did, cid), 0) or 0)
        device_checked_at = float(device_map.get(did, 0) or 0)
        channel_age = (now - channel_checked_at) if channel_checked_at > 0 else float("inf")
        device_age = (now - device_checked_at) if device_checked_at > 0 else float("inf")
        return channel_age, device_age

    def _remote_channel_last_checked_at(self, device_id, channel_id):
        did = str(device_id or "").strip()
        cid = lz._normalize_usage_channel_id(channel_id, "launcher")
        if (not did) or (not cid):
            return 0.0
        checked_map = getattr(self, "_remote_channel_checked_at", None)
        if not isinstance(checked_map, dict):
            checked_map = {}
            self._remote_channel_checked_at = checked_map
        device_map = getattr(self, "_remote_channel_device_checked_at", None)
        if not isinstance(device_map, dict):
            device_map = {}
            self._remote_channel_device_checked_at = device_map
        return max(
            float(checked_map.get((did, cid), 0) or 0),
            float(device_map.get(did, 0) or 0),
        )

    def _remote_channel_device_sync_state(self, device_id):
        did = str(device_id or "").strip()
        if not did:
            return {}
        error_map = getattr(self, "_remote_channel_device_sync_errors", None)
        if not isinstance(error_map, dict):
            error_map = {}
            self._remote_channel_device_sync_errors = error_map
        item = error_map.get(did)
        return dict(item) if isinstance(item, dict) else {}

    def _remote_channel_time_text(self, ts):
        try:
            value = float(ts or 0)
        except Exception:
            value = 0.0
        if value <= 0:
            return "暂无"
        try:
            return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return "暂无"

    def _remote_channel_check_hint(self, device_id, channel_id):
        auto_checker = getattr(self, "_remote_device_auto_ssh_enabled", None)
        if callable(auto_checker):
            try:
                if not bool(auto_checker(device_id)):
                    return "自动 SSH 已关闭，不会主动校验服务器状态。"
            except Exception:
                pass
        sync_running = bool(getattr(self, "_remote_channel_sync_running", False))
        last_checked_at = float(self._remote_channel_last_checked_at(device_id, channel_id) or 0)
        sync_state = self._remote_channel_device_sync_state(device_id)
        fail_count = int(sync_state.get("fail_count") or 0)
        last_error = normalize_ssh_error_text(str(sync_state.get("last_error") or "").strip(), context="SSH 连接")
        last_attempt_at = float(sync_state.get("last_attempt_at") or 0)
        if sync_running:
            if last_attempt_at > 0:
                return f"正在校验服务器状态… 上次发起：{self._remote_channel_time_text(last_attempt_at)}"
            return "正在校验服务器状态…"
        if fail_count >= 2 and last_error:
            return f"服务器连接异常：最近连续 {fail_count} 次校验失败。原因：{last_error}"
        if last_checked_at > 0:
            return f"最近校验：{self._remote_channel_time_text(last_checked_at)}"
        if last_error:
            return f"最近校验失败：{last_error}"
        return "等待首次校验服务器状态。"

    def _show_remote_channel_status_detail(self, device_id, channel_id, title):
        did = str(device_id or "").strip()
        cid = lz._normalize_usage_channel_id(channel_id, "launcher")
        data = self._remote_channel_cached_session(did, cid)
        sync_state = self._remote_channel_device_sync_state(did)
        last_checked_at = float(self._remote_channel_last_checked_at(did, cid) or 0)
        detail_lines = [
            f"渠道：{str(title or cid or '未知渠道').strip() or '未知渠道'}",
            f"最近校验：{self._remote_channel_time_text(last_checked_at)}",
            f"最近发起：{self._remote_channel_time_text(sync_state.get('last_attempt_at'))}",
            f"最近成功：{self._remote_channel_time_text(sync_state.get('last_success_at'))}",
            f"连续失败次数：{int(sync_state.get('fail_count') or 0)}",
        ]
        if isinstance(data, dict) and data:
            detail_lines.extend(
                [
                    f"缓存状态：{str(data.get('process_status') or '未知').strip() or '未知'}",
                    f"缓存 PID：{int(data.get('process_pid') or 0) or '暂无'}",
                    f"缓存更新时间：{self._remote_channel_time_text(data.get('updated_at'))}",
                ]
            )
        last_error = normalize_ssh_error_text(str(sync_state.get("last_error") or "").strip(), context="SSH 连接")
        if last_error:
            detail_lines.append("")
            detail_lines.append("最近失败原因：")
            detail_lines.append(last_error)
        self._channel_info(
            f"{str(title or cid or '渠道').strip() or '渠道'} 校验详情",
            self._remote_channel_check_hint(did, cid),
            detail="\n".join(detail_lines).strip(),
        )

    def _request_remote_channel_status_refresh(self):
        now = float(time.time())
        last_at = float(getattr(self, "_last_remote_channel_status_refresh_at", 0) or 0)
        if (now - last_at) < 3.0:
            return
        self._last_remote_channel_status_refresh_at = now
        notice = getattr(self, "settings_channels_notice", None)
        auto_devices = getattr(self, "_auto_ssh_remote_devices", None)
        if callable(auto_devices):
            try:
                if not auto_devices():
                    if notice is not None:
                        notice.setText("所有远程设备都已关闭自动 SSH，未发起远端渠道校验。")
                    return
            except Exception:
                pass
        if notice is not None:
            notice.setText("正在校验远端渠道运行状态…")
        self._force_remote_channel_sync()

    def _remote_channel_is_running(self, device_id, channel_id):
        data = self._remote_channel_cached_session(device_id, channel_id)
        if not data:
            return False
        status = str(data.get("process_status") or "").strip()
        if "运行" in status and "退出" not in status:
            return True
        return False

    def _remote_channel_status(self, device_id, channel_id):
        data = self._remote_channel_cached_session(device_id, channel_id)
        channel_age, device_age = self._remote_channel_status_check_age(device_id, channel_id)
        sync_running = bool(getattr(self, "_remote_channel_sync_running", False))
        checked_recently = min(channel_age, device_age) <= 30.0
        sync_state = self._remote_channel_device_sync_state(device_id)
        fail_count = int(sync_state.get("fail_count") or 0)
        last_error = str(sync_state.get("last_error") or "").strip()
        if not data:
            if fail_count >= 2 and last_error:
                return "服务器连接异常", C["danger_text"]
            if sync_running or (not checked_recently):
                self._request_remote_channel_status_refresh()
                return "正在校验远端状态", C["text_soft"]
            return "未检测到远端进程", C["muted"]
        status = str(data.get("process_status") or "").strip() or "未知状态"
        pid = int(data.get("process_pid") or 0)
        if fail_count >= 2 and last_error and (sync_running or (not checked_recently)):
            return "服务器连接异常", C["danger_text"]
        if any(key in status for key in ("退出", "失败", "错误", "异常")) and (sync_running or (not checked_recently)):
            self._request_remote_channel_status_refresh()
            return "正在校验远端状态", C["text_soft"]
        if pid > 0 and ("运行" in status) and ("退出" not in status):
            status_text = f"{status} (PID {pid})"
            return status_text, C["accent"]
        if any(key in status for key in ("退出", "失败", "错误", "异常")):
            return status, C["danger_text"]
        if "运行" in status:
            return status, C["accent"]
        return status, C["text_soft"]

    def _channel_conflict_message(self, channel_id):
        spec = lz.COMM_CHANNEL_INDEX.get(channel_id, {})
        for other_id in spec.get("conflicts_with", []):
            if self._channel_proc_alive(other_id):
                other = lz.COMM_CHANNEL_INDEX.get(other_id, {}).get("label", other_id)
                return f"{spec.get('label', channel_id)} 与 {other} 在上游共用单实例锁，不能同时启动。"
        return ""

    def _remote_channel_conflict_message(self, device_id, channel_id):
        spec = lz.COMM_CHANNEL_INDEX.get(channel_id, {})
        for other_id in spec.get("conflicts_with", []):
            if self._remote_channel_is_running(device_id, other_id):
                other = lz.COMM_CHANNEL_INDEX.get(other_id, {}).get("label", other_id)
                return f"{spec.get('label', channel_id)} 与 {other} 在上游共用单实例锁，不能同时启动。"
        return ""

    def _local_channel_autostart_status(self, channel_id):
        cid = str(channel_id or "").strip()
        if not cid:
            return "待自动启动", C["text_soft"]
        if cid == "wechat":
            token_info = self._wx_token_info()
            if not str(token_info.get("bot_token", "") or "").strip():
                return "待扫码绑定", C["danger_text"]
        current = str(getattr(self, "_autostart_channel_current", "") or "").strip()
        pending_ids = getattr(self, "_autostart_channel_pending_ids", None)
        if not isinstance(pending_ids, set):
            pending_ids = set()
        if cid == current:
            return "正在自动启动", C["text_soft"]
        if cid in pending_ids:
            return "等待自动启动", C["text_soft"]
        if bool(getattr(self, "_local_channel_autostart_scheduled", False)):
            return "等待自动启动", C["text_soft"]
        return "待自动启动", C["text_soft"]

    def _channel_status(self, channel_id, values, *, target_ctx=None):
        ctx = target_ctx if isinstance(target_ctx, dict) else {}
        if not ctx:
            _is_remote, _dev, ctx = self._channel_target_context()
        is_remote = bool(ctx.get("is_remote"))
        if is_remote:
            did = str(ctx.get("device_id") or "").strip()
            return self._remote_channel_status(did, channel_id)
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
            return self._local_channel_autostart_status(channel_id)
        return "未启动", C["muted"]

    def _defocus_settings_target_combo_if_idle(self):
        combo = getattr(self, "settings_target_combo", None)
        if combo is not None:
            try:
                view = combo.view()
                popup = view.window() if view is not None else None
                if popup is not None and popup.isVisible():
                    return
            except Exception:
                pass
        defocus = getattr(self, "_defocus_settings_target_combo", None)
        if callable(defocus):
            try:
                defocus(fallback=getattr(self, "settings_channels_list", None))
            except Exception:
                pass

    def _refresh_channels_runtime_status_labels(self):
        target_ctx_getter = getattr(self, "_settings_target_context", None)
        target_ctx = target_ctx_getter() if callable(target_ctx_getter) else {"is_remote": False}
        is_remote_target = bool((target_ctx or {}).get("is_remote"))
        if is_remote_target:
            did = str((target_ctx or {}).get("device_id") or "").strip()
            notice = getattr(self, "settings_channels_notice", None)
            if notice is not None:
                sync_running = bool(getattr(self, "_remote_channel_sync_running", False))
                sync_state = self._remote_channel_device_sync_state(did)
                fail_count = int(sync_state.get("fail_count") or 0)
                last_error = normalize_ssh_error_text(str(sync_state.get("last_error") or "").strip(), context="SSH 连接")
                if sync_running:
                    notice.setText("正在校验远端渠道运行状态…")
                elif fail_count >= 2 and last_error:
                    notice.setText(f"远端渠道状态连续校验失败：{last_error}")
        states = getattr(self, "_qt_channel_states", None)
        if not isinstance(states, dict):
            return
        for spec in lz.COMM_CHANNEL_SPECS:
            state = states.get(spec["id"]) or {}
            status_widget = state.get("status_label")
            hint_widget = state.get("status_hint_label")
            if status_widget is None:
                continue
            values = {field["key"]: self._qt_channel_extras.get(field["key"]) for field in spec.get("fields", [])}
            status_text, status_color = self._channel_status(spec["id"], values, target_ctx=target_ctx)
            status_widget.setText(status_text)
            status_widget.setStyleSheet(f"font-size: 12px; color: {status_color};")
            if hint_widget is not None:
                if is_remote_target:
                    did = str((target_ctx or {}).get("device_id") or "").strip()
                    hint_widget.setText(self._remote_channel_check_hint(did, spec["id"]))
                    hint_widget.setVisible(True)
                else:
                    hint_widget.setVisible(False)
            if spec["id"] == "wechat":
                bind_btn = state.get("bind_btn")
                if bind_btn is not None:
                    if is_remote_target:
                        bind_btn.setText("远端扫码")
                    else:
                        token_info = self._wx_token_info()
                        has_token = bool(str(token_info.get("bot_token", "") or "").strip())
                        bind_btn.setText("重新扫码" if has_token else "扫码登录")

    def _reload_channels_editor_state(self):
        if not hasattr(self, "settings_channels_notice"):
            return
        self._clear_layout(self.settings_channels_list_layout)
        target_ctx_getter = getattr(self, "_settings_target_context", None)
        target_ctx = target_ctx_getter() if callable(target_ctx_getter) else {"is_remote": False}
        if (not bool((target_ctx or {}).get("is_remote"))) and (not lz.is_valid_agent_dir(self.agent_dir)):
            self.settings_channels_notice.setText("请先选择有效的 GenericAgent 目录。")
            return
        if bool((target_ctx or {}).get("is_remote")):
            if bool(getattr(self, "_qt_channel_remote_loading", False)):
                self.settings_channels_notice.setText("正在读取远端 mykey.py…")
                return
            self._qt_channel_remote_loading = True
            self._defocus_settings_target_combo_if_idle()
            trace = getattr(self, "_begin_window_trace", None)
            if callable(trace):
                try:
                    trace("channels_remote_load", duration_ms=3200, suppress_blank_dialogs=True)
                except Exception:
                    pass
            token_getter = getattr(self, "_settings_target_generation", None)
            target_token = token_getter() if callable(token_getter) else 0
            self.settings_channels_notice.setText("正在读取远端 mykey.py…")
            loading = QLabel("正在从远端设备拉取渠道配置，请稍候…")
            loading.setObjectName("mutedText")
            self.settings_channels_list_layout.addWidget(loading)

            def worker():
                _root, py_path, parsed = self._load_channels_source()

                def done():
                    current_token = token_getter() if callable(token_getter) else target_token
                    if int(current_token or 0) != int(target_token or 0):
                        return
                    self._qt_channel_remote_loading = False
                    self._clear_layout(self.settings_channels_list_layout)
                    self._apply_loaded_channels_source(py_path, parsed)
                    self._force_remote_channel_sync()

                self._channel_post_ui(done, action_name="远端渠道配置加载")

            threading.Thread(target=worker, name="settings-channels-remote-load", daemon=True).start()
            return
        _, py_path, parsed = self._load_channels_source()
        self._apply_loaded_channels_source(py_path, parsed)

    def _render_channel_cards(self):
        self._clear_layout(self.settings_channels_list_layout)
        target_ctx_getter = getattr(self, "_settings_target_context", None)
        target_ctx = target_ctx_getter() if callable(target_ctx_getter) else {"is_remote": False}
        is_remote_target = bool((target_ctx or {}).get("is_remote"))
        remote_dev = dict((target_ctx or {}).get("device") or {}) if is_remote_target else {}
        remote_actions_ready = (not is_remote_target) or bool(remote_dev)
        for spec in lz.COMM_CHANNEL_SPECS:
            values = {field["key"]: self._qt_channel_extras.get(field["key"]) for field in spec.get("fields", [])}
            status_text, status_color = self._channel_status(spec["id"], values, target_ctx=target_ctx)
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
            status_hint = QLabel("")
            status_hint.setWordWrap(True)
            status_hint.setObjectName("softTextSmall")
            if is_remote_target:
                status_hint.setText(self._remote_channel_check_hint(str((target_ctx or {}).get("device_id") or "").strip(), spec["id"]))
            body.addWidget(status_hint)
            status_hint.setVisible(bool(is_remote_target))

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
            auto_box = None
            if not is_remote_target:
                auto_box = QCheckBox("自动启动")
                auto_box.setChecked(self._channel_is_auto_start(spec["id"]))
                auto_box.setEnabled(True)
                controls.addWidget(auto_box, 0)
            controls.addStretch(1)
            if spec["id"] == "wechat":
                if is_remote_target:
                    bind_btn = QPushButton("远端扫码")
                    bind_btn.setStyleSheet(self._action_button_style(primary=True))
                    bind_btn.clicked.connect(
                        lambda _=False, dev=dict(remote_dev): self._safe_channel_ui_call(
                            "远端微信扫码",
                            lambda: self._open_wechat_qr_dialog(remote_device=dev),
                        )
                    )
                else:
                    token_info = self._wx_token_info()
                    has_token = bool(str(token_info.get("bot_token", "") or "").strip())
                    bind_btn = QPushButton("重新扫码" if has_token else "扫码登录")
                    bind_btn.setStyleSheet(self._action_button_style(primary=not has_token))
                    bind_btn.clicked.connect(
                        lambda _=False: self._safe_channel_ui_call(
                            "微信扫码",
                            lambda: self._open_wechat_qr_dialog(),
                        )
                    )
                bind_btn.setEnabled(remote_actions_ready)
                controls.addWidget(bind_btn, 0)
                state["bind_btn"] = bind_btn
            save_btn = QPushButton("保存")
            save_btn.setStyleSheet(self._action_button_style())
            save_btn.clicked.connect(
                lambda _=False: self._safe_channel_ui_call(
                    "保存渠道配置",
                    lambda: self._qt_channels_save(silent=False),
                )
            )
            controls.addWidget(save_btn, 0)
            start_btn = QPushButton("启动" if not is_remote_target else "远端启动")
            start_btn.setStyleSheet(self._action_button_style(primary=True))
            start_btn.clicked.connect(
                lambda _=False, cid=spec["id"]: self._safe_channel_ui_call(
                    "启动渠道",
                    lambda: self._start_channel_process(cid),
                )
            )
            start_btn.setEnabled(remote_actions_ready)
            controls.addWidget(start_btn, 0)
            stop_btn = QPushButton("停止" if not is_remote_target else "远端停止")
            stop_btn.setStyleSheet(self._action_button_style())
            stop_btn.clicked.connect(
                lambda _=False, cid=spec["id"]: self._safe_channel_ui_call(
                    "停止渠道",
                    lambda: self._stop_channel_process(cid),
                )
            )
            stop_btn.setEnabled(remote_actions_ready)
            controls.addWidget(stop_btn, 0)
            log_btn = QPushButton("日志尾部" if not is_remote_target else "远端日志")
            log_btn.setStyleSheet(self._action_button_style())
            log_btn.clicked.connect(
                lambda _=False, cid=spec["id"], title=spec["label"]: self._safe_channel_ui_call(
                    "查看渠道日志",
                    lambda: self._show_channel_log_tail(cid, title),
                )
            )
            log_btn.setEnabled(remote_actions_ready)
            controls.addWidget(log_btn, 0)
            if is_remote_target:
                detail_btn = QPushButton("校验详情")
                detail_btn.setStyleSheet(self._action_button_style(kind="subtle"))
                detail_btn.clicked.connect(
                    lambda _=False, cid=spec["id"], title=spec["label"], did=str((target_ctx or {}).get("device_id") or "").strip(): self._safe_channel_ui_call(
                        "查看校验详情",
                        lambda: self._show_remote_channel_status_detail(did, cid, title),
                    )
                )
                detail_btn.setEnabled(remote_actions_ready)
                controls.addWidget(detail_btn, 0)
            body.addLayout(controls)

            self._qt_channel_states[spec["id"]] = state
            state["auto"] = auto_box
            state["status_label"] = status
            state["status_hint_label"] = status_hint
            self.settings_channels_list_layout.addWidget(card)
        self.settings_channels_list_layout.addStretch(1)

    def _qt_channels_save(self, silent=False, apply_running=True):
        if not self._qt_channel_py_path:
            if not silent:
                self._channel_warning("保存失败", "尚未载入通讯渠道配置。")
            return False
        target_ctx_getter = getattr(self, "_settings_target_context", None)
        target_ctx = target_ctx_getter() if callable(target_ctx_getter) else {"is_remote": False}
        is_remote_target = bool((target_ctx or {}).get("is_remote"))
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
            if (not is_remote_target) and (auto is not None):
                self._channel_set_auto_start(spec["id"], auto.isChecked(), persist=False)
        try:
            txt = lz.serialize_mykey_py(
                configs=self._qt_channel_configs,
                extras=extras,
                passthrough=self._qt_channel_passthrough,
            )
            writer = getattr(self, "_settings_target_write_mykey_text", None)
            if callable(writer):
                ok, path_text, err = writer(txt)
                if not ok:
                    raise RuntimeError(err or "写入 mykey.py 失败。")
                self._qt_channel_py_path = str(path_text or self._qt_channel_py_path)
            else:
                with open(self._qt_channel_py_path, "w", encoding="utf-8") as f:
                    f.write(txt)
            self._qt_channel_extras = extras
            lz.save_config(self.cfg)
        except Exception as e:
            if not silent:
                self._channel_critical("保存失败", str(e))
            return False
        if is_remote_target:
            # 远端配置模式下，自动启动属于本机启动器行为，不写入本地 auto_start 列表。
            lz.save_config(self.cfg)
        restarted = (self._restart_running_channels(show_errors=False) if apply_running else 0) if not is_remote_target else 0
        if not silent:
            if is_remote_target:
                msg = "已写入远端 mykey.py 渠道配置。远端渠道进程需在服务器侧重启后生效。"
            else:
                msg = "已写入 mykey.py 和启动器渠道配置。"
                if restarted:
                    msg += f"\n已自动重启 {restarted} 个由启动器托管的通讯渠道。"
            self._channel_info("已保存", msg)
        self._reload_channels_editor_state()
        return True

    def _force_remote_channel_sync(self):
        try:
            self._next_remote_channel_sync_at = 0.0
        except Exception:
            pass
        self._defocus_settings_target_combo_if_idle()
        trace = getattr(self, "_begin_window_trace", None)
        if callable(trace):
            try:
                trace("channels_remote_sync", duration_ms=3200, suppress_blank_dialogs=True)
            except Exception:
                pass
        syncer = getattr(self, "_sync_remote_device_channel_process_sessions", None)
        if callable(syncer):
            try:
                syncer()
            except Exception:
                pass
        refresher = getattr(self, "_refresh_channels_runtime_status_labels", None)
        if callable(refresher):
            try:
                refresher()
            except Exception:
                pass

    def _remote_start_channel_process_blocking(self, device, channel_id, spec):
        script_rel = ("frontends/" + str(spec.get("script") or "").strip()).replace("\\", "/")
        label = str(spec.get("label") or channel_id).strip() or channel_id
        conflicts = [lz._normalize_usage_channel_id(x, "") for x in (spec.get("conflicts_with") or []) if str(x or "").strip()]
        script = (
            "import json, os, signal, site, socket, subprocess, sys, time\n"
            f"channel_id = {str(channel_id)!r}\n"
            f"channel_label = {label!r}\n"
            f"script_rel = {script_rel!r}\n"
            f"conflicts = {conflicts!r}\n"
            "base = os.getcwd()\n"
            "sess_dir = os.path.join(base, 'temp', 'launcher_sessions')\n"
            "log_dir = os.path.join(base, 'temp', 'launcher_channels')\n"
            "os.makedirs(sess_dir, exist_ok=True)\n"
            "os.makedirs(log_dir, exist_ok=True)\n"
            "sid = f'launcher_remote_channel_{channel_id}'\n"
            "session_path = os.path.join(sess_dir, sid + '.json')\n"
            "\n"
            "def pid_alive(pid):\n"
            "    if not pid:\n"
            "        return False\n"
            "    try:\n"
            "        os.kill(int(pid), 0)\n"
            "        return True\n"
            "    except Exception:\n"
            "        return False\n"
            "\n"
            "def load_json(path):\n"
            "    if not os.path.isfile(path):\n"
            "        return {}\n"
            "    try:\n"
            "        with open(path, 'r', encoding='utf-8', errors='replace') as f:\n"
            "            obj = json.load(f)\n"
            "        return obj if isinstance(obj, dict) else {}\n"
            "    except Exception:\n"
            "        return {}\n"
            "\n"
            "def read_tail(path, limit=12000):\n"
            "    p = str(path or '').strip()\n"
            "    if (not p) or (not os.path.isfile(p)):\n"
            "        return ''\n"
            "    try:\n"
            "        with open(p, 'r', encoding='utf-8', errors='replace') as f:\n"
            "            text = f.read()\n"
            "        return text[-int(limit or 12000):]\n"
            "    except Exception:\n"
            "        return ''\n"
            "\n"
            "def wechat_lock_occupied():\n"
            "    s = None\n"
            "    try:\n"
            "        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "        s.bind(('127.0.0.1', 19528))\n"
            "        return False\n"
            "    except OSError:\n"
            "        return True\n"
            "    except Exception:\n"
            "        return False\n"
            "    finally:\n"
            "        if s is not None:\n"
            "            try:\n"
            "                s.close()\n"
            "            except Exception:\n"
            "                pass\n"
            "\n"
            "def find_wechat_pids():\n"
            "    target_script = os.path.normpath(os.path.join(base, script_rel)).lower().replace('\\\\', '/')\n"
            "    target_base = os.path.normpath(base).lower().replace('\\\\', '/')\n"
            "    rows = []\n"
            "    try:\n"
            "        r = subprocess.run(\n"
            "            ['sh', '-lc', 'ps -eo pid,args'],\n"
            "            capture_output=True,\n"
            "            text=True,\n"
            "            encoding='utf-8',\n"
            "            errors='replace',\n"
            "            timeout=8,\n"
            "        )\n"
            "        if int(r.returncode or 0) != 0:\n"
            "            return []\n"
            "        rows = str(r.stdout or '').splitlines()\n"
            "    except Exception:\n"
            "        rows = []\n"
            "    out = []\n"
            "    for line in rows:\n"
            "        text = str(line or '').strip()\n"
            "        if (not text) or ('wechatapp.py' not in text):\n"
            "            continue\n"
            "        parts = text.split(None, 1)\n"
            "        if (not parts) or (not str(parts[0]).isdigit()):\n"
            "            continue\n"
            "        pid = int(parts[0])\n"
            "        cmd = str(parts[1] if len(parts) > 1 else '').lower().replace('\\\\', '/')\n"
            "        if target_script and (target_script in cmd):\n"
            "            out.append(pid)\n"
            "            continue\n"
            "        if ('wechatapp.py' in cmd) and target_base and (target_base in cmd):\n"
            "            out.append(pid)\n"
            "    seen = set()\n"
            "    uniq = []\n"
            "    for pid in out:\n"
            "        if pid in seen:\n"
            "            continue\n"
            "        seen.add(pid)\n"
            "        uniq.append(pid)\n"
            "    return uniq\n"
            "\n"
            "def terminate_pid_force(pid):\n"
            "    p = int(pid or 0)\n"
            "    if p <= 0:\n"
            "        return False\n"
            "    try:\n"
            "        os.kill(p, signal.SIGTERM)\n"
            "    except Exception:\n"
            "        pass\n"
            "    for _ in range(20):\n"
            "        if not pid_alive(p):\n"
            "            return True\n"
            "        time.sleep(0.1)\n"
            "    try:\n"
            "        os.kill(p, signal.SIGKILL)\n"
            "    except Exception:\n"
            "        pass\n"
            "    for _ in range(20):\n"
            "        if not pid_alive(p):\n"
            "            return True\n"
            "        time.sleep(0.05)\n"
            "    return (not pid_alive(p))\n"
            "\n"
            "existing = load_json(session_path)\n"
            "existing_pid = int(existing.get('process_pid') or 0)\n"
            "managed_pids = set([int(existing_pid or 0)])\n"
            "if existing_pid and pid_alive(existing_pid):\n"
            "    print(json.dumps({'ok': True, 'already_running': True, 'pid': existing_pid, 'session_id': sid}, ensure_ascii=False))\n"
            "    raise SystemExit(0)\n"
            "\n"
            "for other in conflicts:\n"
            "    other_sid = f'launcher_remote_channel_{other}'\n"
            "    other_path = os.path.join(sess_dir, other_sid + '.json')\n"
            "    other_data = load_json(other_path)\n"
            "    other_pid = int(other_data.get('process_pid') or 0)\n"
            "    if other_pid and pid_alive(other_pid):\n"
            "        msg = f'{channel_label} 与 {other} 不能同时运行（单实例冲突）。'\n"
            "        print(json.dumps({'ok': False, 'error': msg}, ensure_ascii=False))\n"
            "        raise SystemExit(0)\n"
            "\n"
            "script_path = os.path.join(base, script_rel)\n"
            "if not os.path.isfile(script_path):\n"
            "    print(json.dumps({'ok': False, 'error': f'远端缺少渠道脚本: {script_path}'}, ensure_ascii=False))\n"
            "    raise SystemExit(0)\n"
            "\n"
            "if channel_id == 'wechat':\n"
            "    launcher_home = os.path.join(base, 'temp', 'launcher_home')\n"
            "    token_path = os.path.join(launcher_home, '.wxbot', 'token.json')\n"
            "    token = ''\n"
            "    if os.path.isfile(token_path):\n"
            "        try:\n"
            "            with open(token_path, 'r', encoding='utf-8', errors='replace') as f:\n"
            "                token_obj = json.load(f)\n"
            "            if isinstance(token_obj, dict):\n"
            "                token = str(token_obj.get('bot_token') or '').strip()\n"
            "        except Exception:\n"
            "            token = ''\n"
            "    if not token:\n"
            "        print(json.dumps({'ok': False, 'error': '远端微信未绑定，请先点击“远端扫码”。'}, ensure_ascii=False))\n"
            "        raise SystemExit(0)\n"
            "    if wechat_lock_occupied():\n"
            "        candidates = [p for p in find_wechat_pids() if int(p or 0) > 0 and int(p or 0) not in managed_pids and int(p or 0) != os.getpid()]\n"
            "        killed = 0\n"
            "        failed = []\n"
            "        for p in candidates:\n"
            "            if terminate_pid_force(p):\n"
            "                killed += 1\n"
            "            else:\n"
            "                failed.append(str(p))\n"
            "        for _ in range(30):\n"
            "            if not wechat_lock_occupied():\n"
            "                break\n"
            "            time.sleep(0.1)\n"
            "        if wechat_lock_occupied():\n"
            "            detail = ''\n"
            "            if failed:\n"
            "                detail = '自动结束失败 PID: ' + ', '.join(failed)\n"
            "            elif killed > 0:\n"
            "                detail = f'已结束 {killed} 个外部微信进程，但锁仍占用'\n"
            "            else:\n"
            "                detail = '未发现可结束的外部微信进程'\n"
            "            print(json.dumps({'ok': False, 'error': '远端微信单实例锁被占用，启动失败。', 'detail': detail}, ensure_ascii=False))\n"
            "            raise SystemExit(0)\n"
            "\n"
            "py_bin = str(os.environ.get('GA_PY_BIN') or 'python3').strip() or 'python3'\n"
            "log_path = os.path.join(log_dir, f'{channel_id}.log')\n"
            "proc_env = dict(os.environ)\n"
            "user_site = ''\n"
            "user_base = ''\n"
            "try:\n"
            "    user_site = str(site.getusersitepackages() or '').strip()\n"
            "except Exception:\n"
            "    user_site = ''\n"
            "try:\n"
            "    user_base = str(site.getuserbase() or '').strip()\n"
            "except Exception:\n"
            "    user_base = ''\n"
            "if channel_id == 'wechat':\n"
            "    launcher_home = os.path.join(base, 'temp', 'launcher_home')\n"
            "    os.makedirs(os.path.join(launcher_home, '.wxbot'), exist_ok=True)\n"
            "    proc_env['HOME'] = launcher_home\n"
            "    if user_site:\n"
            "        prev_path = str(proc_env.get('PYTHONPATH') or '').strip()\n"
            "        proc_env['PYTHONPATH'] = user_site if not prev_path else (user_site + os.pathsep + prev_path)\n"
            "    if user_base:\n"
            "        proc_env['PYTHONUSERBASE'] = user_base\n"
            "with open(log_path, 'a', encoding='utf-8') as log:\n"
            "    log.write('\\n==== ' + time.strftime('%Y-%m-%d %H:%M:%S') + f' start {channel_id} ====\\n')\n"
            "    log.flush()\n"
            "    proc = subprocess.Popen(\n"
            "        [py_bin, '-u', script_path],\n"
            "        cwd=base,\n"
            "        stdin=subprocess.DEVNULL,\n"
            "        stdout=log,\n"
            "        stderr=subprocess.STDOUT,\n"
            "        env=proc_env,\n"
            "        start_new_session=True,\n"
            "        close_fds=True,\n"
            "    )\n"
            "\n"
            "now = time.time()\n"
            "created = float(existing.get('created_at', now) or now)\n"
            "session = {\n"
            "    'id': sid,\n"
            "    'title': f'{channel_label} 进程 ' + time.strftime('%m-%d %H:%M', time.localtime(now)),\n"
            "    'created_at': created,\n"
            "    'updated_at': now,\n"
            "    'session_kind': 'channel_process',\n"
            "    'session_source_label': channel_label,\n"
            "    'channel_id': channel_id,\n"
            "    'channel_label': channel_label,\n"
            "    'process_pid': int(proc.pid or 0),\n"
            "    'process_status': '运行中',\n"
            "    'process_started_at': now,\n"
            "    'process_ended_at': 0,\n"
            "    'channel_log_path': log_path,\n"
            "    'bubbles': [],\n"
            "}\n"
            "if bool(existing.get('pinned', False)):\n"
            "    session['pinned'] = True\n"
            "with open(session_path, 'w', encoding='utf-8') as f:\n"
            "    json.dump(session, f, ensure_ascii=False, indent=2)\n"
            "time.sleep(1.0)\n"
            "if not pid_alive(int(proc.pid or 0)):\n"
            "    ended = time.time()\n"
            "    session['updated_at'] = ended\n"
            "    session['process_status'] = '已退出'\n"
            "    session['process_ended_at'] = ended\n"
            "    with open(session_path, 'w', encoding='utf-8') as f:\n"
            "        json.dump(session, f, ensure_ascii=False, indent=2)\n"
            "    tail = read_tail(log_path, limit=16000)\n"
            "    print(json.dumps({'ok': False, 'error': f'{channel_label} 启动后立即退出。', 'pid': int(proc.pid or 0), 'session_id': sid, 'tail': tail}, ensure_ascii=False))\n"
            "    raise SystemExit(0)\n"
            "print(json.dumps({'ok': True, 'already_running': False, 'pid': int(proc.pid or 0), 'session_id': sid, 'log_path': log_path}, ensure_ascii=False))\n"
        )
        ok, payload, err = self._remote_exec_json_script(device, script, timeout=120)
        if not ok:
            return False, str(err or "远端启动失败。").strip() or "远端启动失败。", {}
        if not bool(payload.get("ok", False)):
            msg = str(payload.get("error") or "远端启动失败。").strip() or "远端启动失败。"
            detail = str(payload.get("detail") or "").strip()
            if detail:
                msg = f"{msg} {detail}"
            return False, msg, payload
        return True, "", payload

    def _start_remote_channel_process(self, channel_id, show_errors=True):
        spec = lz.COMM_CHANNEL_INDEX.get(channel_id)
        if not spec:
            return False
        _is_remote, dev, ctx = self._channel_target_context()
        if not bool(ctx.get("is_remote")):
            return False
        if not isinstance(dev, dict) or (not dev):
            if show_errors:
                self._channel_warning("无法启动", "当前远端设备信息不可用，请先检查设备配置。")
            return False
        if not self._qt_channels_save(silent=True, apply_running=False):
            return False
        values = {field["key"]: self._qt_channel_extras.get(field["key"]) for field in spec.get("fields", [])}
        missing = self._channel_missing_required(channel_id, values)
        if missing:
            if show_errors:
                self._channel_warning("配置不完整", f"{spec.get('label', channel_id)} 还缺少这些字段：\n- " + "\n- ".join(missing))
            return False
        did = str(ctx.get("device_id") or "").strip()
        conflict = self._remote_channel_conflict_message(did, channel_id)
        if conflict:
            if show_errors:
                self._channel_warning("无法启动", conflict)
            return False
        self._set_status(f"正在启动远端 {spec.get('label', channel_id)} 渠道…")
        holder = {"ok": False, "msg": "", "payload": {}}

        def worker():
            try:
                ok, msg, payload = self._remote_start_channel_process_blocking(dev, channel_id, spec)
                holder["ok"] = bool(ok)
                holder["msg"] = str(msg or "")
                holder["payload"] = dict(payload or {})
            except Exception as e:
                holder["ok"] = False
                holder["msg"] = f"远端启动异常：{e}"
                holder["payload"] = {}

            def done():
                self._force_remote_channel_sync()
                self._refresh_channels_runtime_status_labels()
                self._reload_channels_editor_state()
                self._last_session_list_signature = None
                self._refresh_sessions()
                if holder["ok"]:
                    if bool(holder["payload"].get("already_running")):
                        self._set_status(f"远端 {spec.get('label', channel_id)} 已在运行。")
                        if show_errors:
                            self._channel_info("已在运行", f"远端 {spec.get('label', channel_id)} 已在运行，无需重复启动。")
                    else:
                        pid = int(holder["payload"].get("pid") or 0)
                        self._set_status(f"已启动远端 {spec.get('label', channel_id)} 渠道（PID {pid if pid > 0 else '-'}）。")
                        if show_errors:
                            self._channel_info("启动成功", f"远端 {spec.get('label', channel_id)} 已启动。")
                    return
                msg = holder["msg"] or f"远端 {spec.get('label', channel_id)} 启动失败。"
                if channel_id == "wechat" and ("未绑定" in msg):
                    if self._open_wechat_qr_dialog(show_errors=show_errors, remote_device=dev):
                        self._start_remote_channel_process(channel_id, show_errors=show_errors)
                        return
                self._set_status(msg)
                if show_errors:
                    detail = str(holder["payload"].get("tail") or "").strip()
                    self._channel_warning("启动失败", msg, detail=detail)

            self._channel_post_ui(done, action_name="远端渠道启动回调")

        threading.Thread(target=worker, name=f"remote-start-{channel_id}", daemon=True).start()
        return True

    def _remote_stop_channel_process_blocking(self, device, channel_id):
        script = (
            "import json, os, signal, time\n"
            f"channel_id = {str(channel_id)!r}\n"
            "base = os.getcwd()\n"
            "sess_dir = os.path.join(base, 'temp', 'launcher_sessions')\n"
            "sid = f'launcher_remote_channel_{channel_id}'\n"
            "session_path = os.path.join(sess_dir, sid + '.json')\n"
            "data = {}\n"
            "if os.path.isfile(session_path):\n"
            "    try:\n"
            "        with open(session_path, 'r', encoding='utf-8', errors='replace') as f:\n"
            "            obj = json.load(f)\n"
            "        if isinstance(obj, dict):\n"
            "            data = obj\n"
            "    except Exception:\n"
            "        data = {}\n"
            "pid = int(data.get('process_pid') or 0)\n"
            "\n"
            "def pid_alive(x):\n"
            "    if not x:\n"
            "        return False\n"
            "    try:\n"
            "        os.kill(int(x), 0)\n"
            "        return True\n"
            "    except Exception:\n"
            "        return False\n"
            "\n"
            "was_running = pid_alive(pid)\n"
            "stopped = False\n"
            "if was_running:\n"
            "    try:\n"
            "        os.kill(pid, signal.SIGTERM)\n"
            "    except Exception:\n"
            "        pass\n"
            "    for _ in range(20):\n"
            "        if not pid_alive(pid):\n"
            "            break\n"
            "        time.sleep(0.15)\n"
            "    if pid_alive(pid):\n"
            "        try:\n"
            "            os.kill(pid, signal.SIGKILL)\n"
            "        except Exception:\n"
            "            pass\n"
            "        for _ in range(20):\n"
            "            if not pid_alive(pid):\n"
            "                break\n"
            "            time.sleep(0.1)\n"
            "    stopped = (not pid_alive(pid))\n"
            "now = time.time()\n"
            "if data:\n"
            "    data['updated_at'] = now\n"
            "    if stopped or (not pid_alive(pid)):\n"
            "        data['process_status'] = '已退出'\n"
            "        data['process_ended_at'] = now\n"
            "    else:\n"
            "        data['process_status'] = '停止失败'\n"
            "    if stopped:\n"
            "        data['process_pid'] = 0\n"
            "    with open(session_path, 'w', encoding='utf-8') as f:\n"
            "        json.dump(data, f, ensure_ascii=False, indent=2)\n"
            "print(json.dumps({'ok': True, 'was_running': bool(was_running), 'stopped': bool(stopped), 'status': str(data.get('process_status') or '')}, ensure_ascii=False))\n"
        )
        ok, payload, err = self._remote_exec_json_script(device, script, timeout=80)
        if not ok:
            return False, str(err or "远端停止失败。").strip() or "远端停止失败。", {}
        if not bool(payload.get("ok", False)):
            return False, str(payload.get("error") or "远端停止失败。").strip() or "远端停止失败。", payload
        return True, "", payload

    def _stop_remote_channel_process(self, channel_id):
        _is_remote, dev, ctx = self._channel_target_context()
        if not bool(ctx.get("is_remote")):
            return False
        if not isinstance(dev, dict) or (not dev):
            self._channel_warning("无法停止", "当前远端设备信息不可用，请先检查设备配置。")
            return False
        spec = lz.COMM_CHANNEL_INDEX.get(channel_id) or {}
        holder = {"ok": False, "msg": "", "payload": {}}
        self._set_status(f"正在停止远端 {spec.get('label', channel_id) or channel_id} 渠道…")

        def worker():
            try:
                ok, msg, payload = self._remote_stop_channel_process_blocking(dev, channel_id)
                holder["ok"] = bool(ok)
                holder["msg"] = str(msg or "")
                holder["payload"] = dict(payload or {})
            except Exception as e:
                holder["ok"] = False
                holder["msg"] = f"远端停止异常：{e}"
                holder["payload"] = {}

            def done():
                self._force_remote_channel_sync()
                self._refresh_channels_runtime_status_labels()
                self._reload_channels_editor_state()
                self._last_session_list_signature = None
                self._refresh_sessions()
                if holder["ok"]:
                    if bool(holder["payload"].get("was_running")):
                        self._channel_info("已停止", f"远端 {spec.get('label', channel_id) or channel_id} 已停止。")
                    else:
                        self._channel_info("未运行", f"远端 {spec.get('label', channel_id) or channel_id} 当前未运行。")
                    self._set_status(f"远端 {spec.get('label', channel_id) or channel_id} 停止命令已完成。")
                    return
                msg = holder["msg"] or f"远端 {spec.get('label', channel_id) or channel_id} 停止失败。"
                self._set_status(msg)
                self._channel_warning("停止失败", msg)

            self._channel_post_ui(done, action_name="远端渠道停止回调")

        threading.Thread(target=worker, name=f"remote-stop-{channel_id}", daemon=True).start()
        return True

    def _remote_tail_channel_log_blocking(self, device, channel_id):
        script = (
            "import json, os\n"
            f"channel_id = {str(channel_id)!r}\n"
            "base = os.getcwd()\n"
            "sess_dir = os.path.join(base, 'temp', 'launcher_sessions')\n"
            "sid = f'launcher_remote_channel_{channel_id}'\n"
            "session_path = os.path.join(sess_dir, sid + '.json')\n"
            "log_path = os.path.join(base, 'temp', 'launcher_channels', f'{channel_id}.log')\n"
            "status = ''\n"
            "if os.path.isfile(session_path):\n"
            "    try:\n"
            "        with open(session_path, 'r', encoding='utf-8', errors='replace') as f:\n"
            "            obj = json.load(f)\n"
            "        if isinstance(obj, dict):\n"
            "            status = str(obj.get('process_status') or '')\n"
            "            maybe = str(obj.get('channel_log_path') or '').strip()\n"
            "            if maybe:\n"
            "                log_path = maybe\n"
            "    except Exception:\n"
            "        pass\n"
            "tail = ''\n"
            "if os.path.isfile(log_path):\n"
            "    try:\n"
            "        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:\n"
            "            tail = f.read()[-16000:]\n"
            "    except Exception:\n"
            "        tail = ''\n"
            "print(json.dumps({'ok': True, 'status': status, 'log_path': log_path, 'tail': tail}, ensure_ascii=False))\n"
        )
        ok, payload, err = self._remote_exec_json_script(device, script, timeout=60)
        if not ok:
            return False, "", "", str(err or "读取远端日志失败。").strip() or "读取远端日志失败。"
        if not bool(payload.get("ok", False)):
            return False, "", "", str(payload.get("error") or "读取远端日志失败。").strip() or "读取远端日志失败。"
        return True, str(payload.get("tail") or ""), str(payload.get("status") or ""), ""

    def _show_remote_channel_log_tail(self, channel_id, title):
        _is_remote, dev, ctx = self._channel_target_context()
        if not bool(ctx.get("is_remote")):
            return False
        if not isinstance(dev, dict) or (not dev):
            self._channel_warning("无法读取日志", "当前远端设备信息不可用，请先检查设备配置。")
            return False
        holder = {"ok": False, "tail": "", "status": "", "err": ""}
        self._set_status(f"正在读取远端 {title} 日志…")

        def worker():
            try:
                ok, tail, status, err = self._remote_tail_channel_log_blocking(dev, channel_id)
                holder["ok"] = bool(ok)
                holder["tail"] = str(tail or "")
                holder["status"] = str(status or "")
                holder["err"] = str(err or "")
            except Exception as e:
                holder["ok"] = False
                holder["tail"] = ""
                holder["status"] = ""
                holder["err"] = f"远端日志读取异常：{e}"

            def done():
                if holder["ok"]:
                    brief = f"状态：{holder['status']}" if holder["status"] else "远端日志尾部"
                    self._channel_info(f"{title} 远端日志尾部", brief, detail=(holder["tail"] or "暂无日志。"))
                    self._set_status(f"已读取远端 {title} 日志。")
                    return
                self._channel_warning("读取失败", holder["err"] or "读取远端日志失败。")
                self._set_status(holder["err"] or "读取远端日志失败。")

            self._channel_post_ui(done, action_name="远端日志回调")

        threading.Thread(target=worker, name=f"remote-log-{channel_id}", daemon=True).start()
        return True

    def _start_channel_process(
        self,
        channel_id,
        show_errors=True,
        *,
        allow_interactive=True,
        skip_wechat_token_probe=False,
        skip_dependency_check=False,
        force_local=False,
    ):
        is_remote_target, _dev, _ctx = self._channel_target_context()
        if is_remote_target and (not force_local):
            return self._start_remote_channel_process(channel_id, show_errors=show_errors)
        spec = lz.COMM_CHANNEL_INDEX.get(channel_id)
        if not spec:
            return False
        if not self._qt_channels_save(silent=True, apply_running=False):
            return False
        if channel_id == "wechat":
            token_info = self._wx_token_info()
            if (not skip_wechat_token_probe) and str(token_info.get("bot_token", "") or "").strip():
                token_ok, token_state, token_detail = self._probe_local_wechat_token(token_info, timeout=6)
                if token_ok is False and token_state == "session_timeout":
                    self._clear_wx_token_info()
                    token_info = {}
                    self._set_status("检测到本地微信 token 已失效，已自动清除并准备重新扫码。")
                elif token_ok is None and token_detail:
                    self._set_status(f"本地微信 token 预检失败，继续按现有绑定尝试启动：{token_detail}")
            if not str(token_info.get("bot_token", "") or "").strip():
                if not allow_interactive:
                    self._set_status("本地微信未绑定，已跳过自动启动。")
                    return False
                if not self._open_wechat_qr_dialog(show_errors=show_errors):
                    return False
            if self._wechat_singleton_locked():
                killed, failed = self._terminate_external_wechat_instances()
                if killed:
                    self._set_status(f"已关闭 {killed} 个外部微信实例，准备由启动器拉起。")
                if failed:
                    self._set_status("检测到外部微信实例，但自动关闭失败：" + ", ".join(failed))
                # 等待端口释放
                for _ in range(30):
                    if not self._wechat_singleton_locked():
                        break
                    time.sleep(0.1)
                if self._wechat_singleton_locked():
                    if show_errors:
                        self._channel_warning("无法启动", "微信单实例锁仍被占用。已尝试自动关闭外部实例，请手动结束后重试。")
                    return False
        if self._channel_proc_alive(channel_id):
            self._reload_channels_editor_state()
            return True
        if not lz.is_valid_agent_dir(self.agent_dir):
            if show_errors:
                self._channel_warning("目录无效", "请先选择有效的 GenericAgent 目录。")
            return False
        conflict = self._channel_conflict_message(channel_id)
        if conflict:
            if show_errors:
                self._channel_warning("无法启动", conflict)
            return False
        extra_packages = self._channel_extra_packages(spec)
        if not skip_dependency_check:
            if not self._check_runtime_dependencies(
                purpose=f"启动{spec.get('label', channel_id)}渠道",
                extra_packages=extra_packages,
                visual=bool(show_errors),
            ):
                if show_errors and not extra_packages:
                    self._channel_critical("缺少 Python", "未找到可用的系统 Python，或依赖检查失败。")
                return False
        py = lz._resolve_config_path(str(self.cfg.get("python_exe") or "").strip()) or lz._find_system_python()
        if not py or not os.path.isfile(py):
            if show_errors:
                self._channel_critical("缺少 Python", "依赖检查完成后仍未找到可用的 Python 可执行文件。")
            return False
        values = {field["key"]: self._qt_channel_extras.get(field["key"]) for field in spec.get("fields", [])}
        missing = self._channel_missing_required(channel_id, values)
        if missing:
            if show_errors:
                self._channel_warning("配置不完整", f"{spec.get('label', channel_id)} 还缺少这些字段：\n- " + "\n- ".join(missing))
            return False
        script_path = os.path.join(self.agent_dir, "frontends", spec.get("script", ""))
        if not os.path.isfile(script_path):
            if show_errors:
                self._channel_critical("脚本不存在", f"未找到渠道脚本：\n{script_path}")
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
                self._channel_critical("启动失败", str(e))
            return False
        session_id = self._create_channel_process_session(channel_id, proc, log_path)
        self._channel_procs[channel_id] = {
            "proc": proc,
            "log_handle": log_handle,
            "log_path": log_path,
            "session_id": session_id,
            "log_start_pos": int(log_handle.tell() or 0),
            "last_snapshot_sig": None,
        }
        self._channel_set_external_running(channel_id, False)
        self._sync_channel_process_session(channel_id, final=False)
        if channel_id == "wechat":
            self._start_wechat_health_watch(show_errors=show_errors)
        QTimer.singleShot(
            1200,
            lambda cid=channel_id, se=show_errors: self._safe_channel_ui_call(
                "渠道启动后检查",
                lambda: self._after_channel_launch_check(cid, show_errors=se),
                show_errors=False,
            ),
        )
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
        lock_hit = ("[WeChat] Another instance running, exiting." in str(tail or ""))
        self._channel_procs.pop(channel_id, None)
        self._channel_set_external_running(channel_id, False, persist=True)
        self._reload_channels_editor_state()
        self._last_session_list_signature = None
        self._refresh_sessions()
        if show_errors:
            if lock_hit and str(channel_id or "").strip().lower() == "wechat":
                self._channel_warning(
                    "渠道启动失败",
                    "微信单实例锁被占用，启动器拉起失败。",
                    detail=(tail or "(空)"),
                )
                return
            self._channel_warning(
                "渠道启动失败",
                f"{channel_id} 已退出，返回码 {proc.returncode}。",
                detail=(tail or "(空)"),
            )

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
        is_remote_target, _dev, _ctx = self._channel_target_context()
        if is_remote_target:
            return self._stop_remote_channel_process(channel_id)
        info = self._channel_procs.get(channel_id)
        if not info:
            self._channel_set_external_running(channel_id, False, persist=True)
            self._reload_channels_editor_state()
            return False
        proc = info.get("proc")
        try:
            if proc is not None:
                lz.terminate_process_tree(proc, terminate_timeout=1.5, kill_timeout=1.5)
        finally:
            exit_code = proc.returncode if proc else None
            self._sync_channel_process_session(channel_id, final=True, exit_code=exit_code)
            self._close_channel_log_handle(channel_id)
            self._channel_procs.pop(channel_id, None)
            self._channel_set_external_running(channel_id, False, persist=True)
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
        is_remote_target, _dev, _ctx = self._channel_target_context()
        if is_remote_target:
            return self._show_remote_channel_log_tail(channel_id, title)
        tail = self._channel_tail_log(channel_id) or "暂无日志。"
        self._channel_info(f"{title} 日志尾部", "本机日志尾部", detail=tail)

    def _start_channel_process_autostart(self, channel_id, done=None):
        if self._channel_proc_alive(channel_id):
            if callable(done):
                try:
                    done(True)
                except Exception:
                    pass
            return
        if not lz.is_valid_agent_dir(self.agent_dir):
            if callable(done):
                try:
                    done(False)
                except Exception:
                    pass
            return
        spec = lz.COMM_CHANNEL_INDEX.get(channel_id)
        if not spec:
            if callable(done):
                try:
                    done(False)
                except Exception:
                    pass
            return

        def worker():
            holder = {
                "dep_result": None,
                "extra_packages": self._channel_extra_packages(spec),
                "skip_reason": "",
                "clear_token": False,
                "token_warning": "",
            }
            if channel_id == "wechat":
                token_info = self._wx_token_info()
                token = str(token_info.get("bot_token", "") or "").strip()
                if not token:
                    holder["skip_reason"] = "本地微信未绑定，已跳过自动启动。"
                else:
                    token_ok, token_state, token_detail = self._probe_local_wechat_token(token_info, timeout=6)
                    if token_ok is False and token_state == "session_timeout":
                        holder["clear_token"] = True
                        holder["skip_reason"] = "检测到本地微信 token 已失效，已清除失效 token，自动启动已跳过。"
                    elif token_ok is None and token_detail:
                        holder["token_warning"] = f"本地微信 token 预检异常，按现有绑定继续自动启动：{token_detail}"
            if not holder["skip_reason"]:
                try:
                    holder["dep_result"] = lz._ensure_runtime_dependencies(
                        self.agent_dir,
                        extra_packages=holder["extra_packages"],
                        progress=None,
                        force_sync=False,
                    )
                except Exception as e:
                    holder["dep_result"] = {"ok": False, "python": "", "error": str(e)}

            def done_ui():
                started = False
                if holder["clear_token"]:
                    self._clear_wx_token_info()
                if holder["token_warning"]:
                    self._set_status(holder["token_warning"])
                skip_reason = str(holder["skip_reason"] or "").strip()
                if skip_reason:
                    self._set_status(skip_reason)
                else:
                    dep_ok, _py, dep_err = self._apply_dependency_check_result(
                        holder["dep_result"] or {},
                        extra_packages=holder["extra_packages"],
                    )
                    if dep_ok:
                        started = bool(
                            self._start_channel_process(
                                channel_id,
                                show_errors=False,
                                allow_interactive=False,
                                skip_wechat_token_probe=True,
                                skip_dependency_check=True,
                                force_local=True,
                            )
                        )
                    else:
                        self._set_status(dep_err or f"{spec.get('label', channel_id)} 自动启动失败：依赖检查未通过。")
                if callable(done):
                    try:
                        done(started)
                    except Exception:
                        pass

            self._channel_post_ui(done_ui, action_name="自动启动渠道回调")

        threading.Thread(target=worker, daemon=True, name=f"autostart-{channel_id}").start()

    def _start_autostart_channels(self):
        if not lz.is_valid_agent_dir(self.agent_dir):
            return
        if bool(getattr(self, "_autostart_channels_running", False)):
            return
        pending = [spec["id"] for spec in lz.COMM_CHANNEL_SPECS if self._channel_is_auto_start(spec["id"]) and not self._channel_proc_alive(spec["id"])]
        if not pending:
            self._autostart_channel_pending_ids = set()
            self._autostart_channel_current = ""
            self._refresh_channels_runtime_status_labels()
            return
        self._autostart_channels_running = True
        self._autostart_channel_pending_ids = set(pending)
        self._autostart_channel_current = ""
        queue = list(pending)
        self._refresh_channels_runtime_status_labels()

        def run_next():
            if not queue:
                self._autostart_channels_running = False
                self._autostart_channel_pending_ids = set()
                self._autostart_channel_current = ""
                self._refresh_channels_runtime_status_labels()
                return
            channel_id = queue.pop(0)
            self._autostart_channel_current = str(channel_id or "").strip()
            self._refresh_channels_runtime_status_labels()

            def after_one(_started=False, cid=channel_id):
                pending_ids = getattr(self, "_autostart_channel_pending_ids", None)
                if not isinstance(pending_ids, set):
                    pending_ids = set()
                pending_ids.discard(str(cid or "").strip())
                self._autostart_channel_pending_ids = pending_ids
                self._autostart_channel_current = ""
                self._refresh_channels_runtime_status_labels()
                run_next()

            self._start_channel_process_autostart(
                channel_id,
                done=after_one,
            )

        self._channel_post_ui(run_next, action_name="启动自动启动队列")
