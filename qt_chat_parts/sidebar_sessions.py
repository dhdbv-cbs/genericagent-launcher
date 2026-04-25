from __future__ import annotations

import hashlib
import json
import os
import shlex
import threading
import time
import uuid

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from launcher_app import core as lz
from launcher_app.theme import C, F

from .common import _session_copy, normalize_remote_agent_dir, normalize_ssh_error_text


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

    def _normalize_remote_device(self, raw):
        item = raw if isinstance(raw, dict) else {}
        host = str(item.get("host") or "").strip()
        username = str(item.get("username") or "").strip()
        if not host or not username:
            return None
        try:
            port = int(item.get("port") or 22)
        except Exception:
            port = 22
        port = max(1, min(65535, port))
        digest = hashlib.sha1(f"{username}@{host}:{port}".encode("utf-8", errors="ignore")).hexdigest()[:10]
        did = str(item.get("id") or f"remote_{digest}").strip() or f"remote_{digest}"
        name = str(item.get("name") or host).strip() or host
        key_path = str(item.get("ssh_key_path") or "").strip()
        password = str(item.get("password") or "").strip()
        agent_dir = normalize_remote_agent_dir(item.get("agent_dir"), username=username)
        python_cmd = str(item.get("python_cmd") or "python3").strip() or "python3"
        auto_ssh = self._normalize_remote_auto_ssh_value(item.get("auto_ssh", True), default=True)
        return {
            "id": did,
            "name": name,
            "host": host,
            "username": username,
            "port": port,
            "ssh_key_path": key_path,
            "password": password,
            "agent_dir": agent_dir,
            "python_cmd": python_cmd,
            "auto_ssh": auto_ssh,
        }

    def _fallback_remote_device_from_vps(self):
        cfg = dict(self.cfg.get("vps_connection") or {})
        host = str(cfg.get("host") or "").strip()
        username = str(cfg.get("username") or "").strip()
        if not host or not username:
            return None
        deploy = dict(self.cfg.get("vps_deploy") or {})
        raw = {
            "name": "默认服务器",
            "host": host,
            "username": username,
            "port": int(cfg.get("port") or 22),
            "ssh_key_path": str(cfg.get("ssh_key_path") or "").strip(),
            "password": str(cfg.get("password") or "").strip(),
            "agent_dir": normalize_remote_agent_dir(deploy.get("remote_dir"), username=username),
            "python_cmd": "python3",
            "auto_ssh": True,
        }
        return self._normalize_remote_device(raw)

    def _normalize_remote_auto_ssh_value(self, value, *, default=True):
        if isinstance(value, bool):
            return value
        if value is None:
            return bool(default)
        text = str(value).strip().lower()
        if not text:
            return bool(default)
        if text in ("0", "false", "no", "off", "disable", "disabled", "关", "关闭", "否"):
            return False
        if text in ("1", "true", "yes", "on", "enable", "enabled", "开", "开启", "是"):
            return True
        return bool(value)

    def _remote_devices(self):
        raw_rows = self.cfg.get("remote_devices")
        rows = []
        if isinstance(raw_rows, list):
            for raw in raw_rows:
                norm = self._normalize_remote_device(raw)
                if norm:
                    rows.append(norm)
        if not rows:
            fallback = self._fallback_remote_device_from_vps()
            if fallback:
                rows.append(fallback)
        seen = set()
        out = []
        for row in rows:
            did = str(row.get("id") or "").strip()
            if not did or did in seen:
                continue
            seen.add(did)
            out.append(row)
        return out

    def _save_remote_devices(self, rows):
        payload = []
        for raw in rows or []:
            norm = self._normalize_remote_device(raw)
            if norm:
                payload.append(norm)
        self.cfg["remote_devices"] = payload
        lz.save_config(self.cfg)

    def _remote_device_by_id(self, device_id: str):
        did = str(device_id or "").strip()
        if not did:
            return None
        for row in self._remote_devices():
            if str(row.get("id") or "").strip() == did:
                return row
        return None

    def _remote_device_auto_ssh_enabled(self, device_or_id=""):
        if isinstance(device_or_id, dict):
            return self._normalize_remote_auto_ssh_value(device_or_id.get("auto_ssh", True), default=True)
        did = str(device_or_id or "").strip()
        if not did:
            return True
        dev = self._remote_device_by_id(did)
        if not isinstance(dev, dict):
            return True
        return self._normalize_remote_auto_ssh_value(dev.get("auto_ssh", True), default=True)

    def _auto_ssh_remote_devices(self, device_id: str = ""):
        target_id = str(device_id or "").strip()
        rows = []
        for dev in self._remote_devices():
            did = str(dev.get("id") or "").strip()
            if target_id and did != target_id:
                continue
            if not self._remote_device_auto_ssh_enabled(dev):
                continue
            rows.append(dev)
        return rows

    def _set_remote_device_auto_ssh(self, device_id: str, enabled: bool):
        did = str(device_id or "").strip()
        if not did:
            return
        rows = self._remote_devices()
        changed = False
        for row in rows:
            if str(row.get("id") or "").strip() != did:
                continue
            old = self._remote_device_auto_ssh_enabled(row)
            row["auto_ssh"] = bool(enabled)
            changed = old != bool(enabled)
            break
        if not changed:
            return
        self._save_remote_devices(rows)
        self._last_session_list_signature = None
        if bool(enabled):
            self._next_remote_launcher_sync_at = 0.0
            self._next_remote_channel_sync_at = 0.0
        else:
            error_map = getattr(self, "_remote_channel_device_sync_errors", None)
            if isinstance(error_map, dict):
                meta = dict(error_map.get(did) or {})
                meta["auto_ssh_disabled"] = True
                meta["fail_count"] = 0
                meta["last_error"] = ""
                error_map[did] = meta
        self._refresh_sessions()
        if bool(enabled):
            self._sync_remote_device_launcher_sessions(force=True, device_id=did)
            self._sync_remote_device_channel_process_sessions()
            probe = getattr(self, "_request_server_connection_probe", None)
            if callable(probe):
                try:
                    probe(force=True)
                except Exception:
                    pass

    def _session_device_scope_id(self, session):
        data = session if isinstance(session, dict) else {}
        scope = str(data.get("device_scope") or "local").strip().lower()
        if scope not in ("local", "remote"):
            scope = "local"
        if scope == "remote":
            did = str(data.get("device_id") or "").strip()
            if did:
                return scope, did
        return "local", "local"

    def _session_matches_device(self, session, scope: str, device_id: str):
        s_scope, s_id = self._session_device_scope_id(session)
        if str(scope or "local").strip().lower() != s_scope:
            return False
        if s_scope == "remote":
            return str(device_id or "").strip() == s_id
        return True

    def _current_device_context(self):
        if isinstance(self.current_session, dict) and str(self.current_session.get("id") or "").strip():
            return self._session_device_scope_id(self.current_session)
        scope = str(getattr(self, "_sidebar_device_scope", "local") or "local").strip().lower()
        device_id = str(getattr(self, "_sidebar_device_id", "local") or "local").strip()
        if scope not in ("local", "remote"):
            scope = "local"
        if scope == "remote" and not device_id:
            device_id = "local"
            scope = "local"
        return scope, (device_id or "local")

    def _sidebar_switch_to_roots(self):
        self._sidebar_view_mode = "roots"
        self._last_session_list_signature = None
        self._refresh_sessions()

    def _sidebar_open_remote_devices(self):
        self._sidebar_view_mode = "remote_devices"
        self._last_session_list_signature = None
        self._refresh_sessions()

    def _sidebar_open_device(self, scope: str, device_id: str):
        target_scope = str(scope or "local").strip().lower()
        if target_scope not in ("local", "remote"):
            target_scope = "local"
        target_id = str(device_id or "local").strip() or "local"
        self._sidebar_device_scope = target_scope
        self._sidebar_device_id = target_id if target_scope == "remote" else "local"
        self._sidebar_view_mode = "channels"
        self._last_session_list_signature = None
        if target_scope == "remote" and self._remote_device_auto_ssh_enabled(target_id):
            self._sync_remote_device_launcher_sessions(force=True, device_id=target_id)
        self._refresh_sessions()

    def _remote_device_ssh_payload(self, device):
        item = device if isinstance(device, dict) else {}
        key_rel = str(item.get("ssh_key_path") or "").strip()
        key_abs = lz._resolve_config_path(key_rel) if key_rel else ""
        if key_rel and (not key_abs or not os.path.isfile(key_abs)):
            return None
        payload = {
            "host": str(item.get("host") or "").strip(),
            "username": str(item.get("username") or "").strip(),
            "port": int(item.get("port") or 22),
            "password": str(item.get("password") or "").strip(),
            "key_abs": key_abs,
        }
        if not payload["host"] or not payload["username"]:
            return None
        if (not payload["password"]) and (not payload["key_abs"]):
            return None
        return payload

    def _normalize_remote_session_id(self, value, fallback=""):
        raw = str(value or "").strip() or str(fallback or "").strip()
        if not raw:
            raw = uuid.uuid4().hex[:12]
        safe = "".join(ch for ch in raw if (ch.isalnum() or ch in ("_", "-")))
        if not safe:
            safe = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]
        return safe[:96]

    def _remote_cache_session_id(self, device_id: str, remote_sid: str):
        did = self._normalize_remote_session_id(device_id or "remote", fallback="remote")
        rid = self._normalize_remote_session_id(remote_sid, fallback=uuid.uuid4().hex[:12])
        return f"rchat_{did}_{rid}"

    def _remote_source_session_id(self, session):
        data = session if isinstance(session, dict) else {}
        rid = str(data.get("remote_session_id") or "").strip()
        if rid:
            return self._normalize_remote_session_id(rid, fallback=rid)
        sid = str(data.get("id") or "").strip()
        scope, did = self._session_device_scope_id(data)
        if scope == "remote" and sid.startswith(f"rchat_{did}_"):
            return self._normalize_remote_session_id(sid[len(f"rchat_{did}_") :], fallback=sid)
        return self._normalize_remote_session_id(sid, fallback=uuid.uuid4().hex[:12])

    def _remote_launcher_sessions_dir(self, device):
        dev = device if isinstance(device, dict) else {}
        remote_dir = normalize_remote_agent_dir(dev.get("agent_dir"), username=dev.get("username"))
        return f"{remote_dir.rstrip('/')}/temp/launcher_sessions"

    def _open_remote_device_client(self, device, *, timeout=10):
        if not self._remote_device_auto_ssh_enabled(device):
            return None, "该远程设备已关闭自动 SSH，请先在“其他设备”中打开开关。", False
        payload = self._remote_device_ssh_payload(device)
        if not payload:
            return None, "远程设备 SSH 配置无效。", True
        client, err_msg, detail, missing = self._open_vps_ssh_client(payload, timeout=timeout)
        if client is None:
            msg = err_msg or "SSH 连接失败。"
            if detail:
                msg = f"{msg}\n{detail}"
            return None, msg, bool(missing)
        return client, "", False

    def _ensure_remote_launcher_sessions_dir(self, client, device):
        remote_sessions_dir = self._remote_launcher_sessions_dir(device)
        rc, _out, err = self._vps_exec_remote(client, f"mkdir -p {shlex.quote(remote_sessions_dir)}", timeout=20)
        if rc != 0:
            return False, str(err or "创建远端会话目录失败。").strip() or "创建远端会话目录失败。"
        return True, ""

    def _fetch_remote_launcher_session_metas(self, device, *, include_all_channels=False, include_usage=False):
        client, err_msg, _missing = self._open_remote_device_client(device, timeout=8)
        if client is None:
            return False, [], err_msg
        rows = []
        try:
            ok, detail = self._ensure_remote_launcher_sessions_dir(client, device)
            if not ok:
                return False, [], detail
            sftp = client.open_sftp()
            try:
                remote_dir = self._remote_launcher_sessions_dir(device)
                try:
                    names = list(sftp.listdir(remote_dir))
                except Exception:
                    names = []
                for name in names:
                    fn = str(name or "").strip()
                    if not fn.endswith(".json") or fn.startswith("."):
                        continue
                    remote_fp = f"{remote_dir}/{fn}"
                    try:
                        with sftp.open(remote_fp, "rb") as fp:
                            raw = fp.read()
                    except Exception:
                        continue
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw.decode("utf-8", errors="replace"))
                    except Exception:
                        continue
                    if not isinstance(data, dict):
                        continue
                    if str(data.get("session_kind") or "").strip().lower() == "channel_process":
                        continue
                    channel_id = lz._normalize_usage_channel_id(data.get("channel_id"), "launcher")
                    if (not include_all_channels) and channel_id != "launcher":
                        continue
                    remote_sid = self._normalize_remote_session_id(data.get("id") or fn[:-5], fallback=fn[:-5])
                    bubbles = list(data.get("bubbles") or [])
                    preview = ""
                    if bubbles:
                        preview = str((bubbles[-1] or {}).get("text") or "").strip()
                    row = {
                        "remote_session_id": remote_sid,
                        "title": str(data.get("title") or "").strip() or "(未命名)",
                        "updated_at": float(data.get("updated_at", 0) or 0),
                        "created_at": float(data.get("created_at", 0) or 0),
                        "pinned": bool(data.get("pinned", False)),
                        "channel_id": channel_id,
                        "channel_label": str(data.get("channel_label") or lz._usage_channel_label(channel_id)).strip()
                        or lz._usage_channel_label(channel_id),
                        "preview_text": preview,
                    }
                    if include_usage:
                        row["payload"] = data
                    rows.append(row)
            finally:
                try:
                    sftp.close()
                except Exception:
                    pass
        finally:
            try:
                client.close()
            except Exception:
                pass
        rows.sort(key=lambda row: float(row.get("updated_at", 0) or 0), reverse=True)
        return True, rows, ""

    def _fetch_remote_session_payload(self, device, remote_session_id: str):
        remote_sid = self._normalize_remote_session_id(remote_session_id, fallback=remote_session_id)
        if not remote_sid:
            return None, "远端会话 ID 无效。"
        client, err_msg, _missing = self._open_remote_device_client(device, timeout=10)
        if client is None:
            return None, err_msg
        try:
            ok, detail = self._ensure_remote_launcher_sessions_dir(client, device)
            if not ok:
                return None, detail
            sftp = client.open_sftp()
            try:
                remote_dir = self._remote_launcher_sessions_dir(device)
                remote_fp = f"{remote_dir}/{remote_sid}.json"
                try:
                    with sftp.open(remote_fp, "rb") as fp:
                        raw = fp.read()
                except Exception as e:
                    return None, f"读取远端会话失败：{e}"
                if not raw:
                    return None, "远端会话为空。"
                try:
                    payload = json.loads(raw.decode("utf-8", errors="replace"))
                except Exception as e:
                    return None, f"解析远端会话失败：{e}"
                if not isinstance(payload, dict):
                    return None, "远端会话格式无效。"
                payload["id"] = remote_sid
                return payload, ""
            finally:
                try:
                    sftp.close()
                except Exception:
                    pass
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _remote_session_cache_payload(self, device, remote_row, existing):
        dev = device if isinstance(device, dict) else {}
        row = remote_row if isinstance(remote_row, dict) else {}
        old = existing if isinstance(existing, dict) else {}
        did = str(dev.get("id") or "").strip()
        dname = str(dev.get("name") or dev.get("host") or "远程设备").strip() or "远程设备"
        remote_sid = self._normalize_remote_session_id(row.get("remote_session_id"), fallback=row.get("id"))
        cache_sid = str(old.get("id") or "").strip() or self._remote_cache_session_id(did, remote_sid)
        source_payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        channel_id = lz._normalize_usage_channel_id(
            source_payload.get("channel_id") or row.get("channel_id") or old.get("channel_id"),
            "launcher",
        )
        channel_label = str(
            source_payload.get("channel_label") or row.get("channel_label") or old.get("channel_label") or lz._usage_channel_label(channel_id)
        ).strip() or lz._usage_channel_label(channel_id)
        preview = str(row.get("preview_text") or "").strip()
        bubbles = list(source_payload.get("bubbles") or old.get("bubbles") or [])
        if not bubbles and preview:
            bubbles = [{"role": "assistant", "text": preview}]
        created_at = float(source_payload.get("created_at", row.get("created_at", old.get("created_at", time.time()))) or time.time())
        updated_at = float(source_payload.get("updated_at", row.get("updated_at", old.get("updated_at", created_at))) or created_at)
        payload = dict(old)
        if source_payload:
            payload.update(source_payload)
            payload.pop("path", None)
        payload.update(
            {
                "id": cache_sid,
                "remote_session_id": remote_sid,
                "title": str(source_payload.get("title") or row.get("title") or old.get("title") or "(未命名)").strip() or "(未命名)",
                "created_at": created_at,
                "updated_at": updated_at,
                "pinned": bool(source_payload.get("pinned", row.get("pinned", old.get("pinned", False)))),
                "session_source_label": dname,
                "channel_id": channel_id,
                "channel_label": channel_label,
                "device_scope": "remote",
                "device_id": did,
                "device_name": dname,
                "bubbles": bubbles,
                "remote_updated_at": updated_at,
            }
        )
        payload.setdefault("backend_history", [])
        payload.setdefault("agent_history", [])
        payload.setdefault("llm_idx", 0)
        payload.setdefault("snapshot", {"version": 1, "kind": "turn_complete"})
        lz._normalize_token_usage_inplace(payload)
        return payload

    def _sync_remote_device_launcher_sessions_blocking(self, *, force=False, device_id="", include_all_channels=False, include_usage=False):
        if not lz.is_valid_agent_dir(self.agent_dir):
            return False
        if not force:
            mode = str(getattr(self, "_sidebar_view_mode", "roots") or "roots").strip().lower()
            scope, _did = self._current_device_context()
            current_scope, _current_did = self._session_device_scope_id(self.current_session or {})
            if mode not in ("remote_devices", "channels", "sessions") and scope != "remote" and current_scope != "remote":
                return False
        now = time.time()
        next_at = float(getattr(self, "_next_remote_launcher_sync_at", 0) or 0)
        if (not force) and now < next_at:
            return False
        self._next_remote_launcher_sync_at = now + 8.0
        target_id = str(device_id or "").strip()
        devices = self._auto_ssh_remote_devices(target_id)
        active_ids_by_device = {}
        synced_device_ids = set()
        changed = False
        for dev in devices:
            did = str(dev.get("id") or "").strip()
            if not did:
                continue
            ok, rows, _err = self._fetch_remote_launcher_session_metas(
                dev,
                include_all_channels=include_all_channels,
                include_usage=include_usage,
            )
            if not ok:
                continue
            synced_device_ids.add(did)
            active_ids = active_ids_by_device.setdefault(did, set())
            for row in rows:
                remote_sid = self._normalize_remote_session_id(row.get("remote_session_id"), fallback=row.get("id"))
                cache_sid = self._remote_cache_session_id(did, remote_sid)
                active_ids.add(cache_sid)
                old = lz.load_session(self.agent_dir, cache_sid) or {}
                payload = self._remote_session_cache_payload(dev, row, old)
                same_payload = (
                    str(old.get("title") or "") == str(payload.get("title") or "")
                    and float(old.get("updated_at", 0) or 0) == float(payload.get("updated_at", 0) or 0)
                    and bool(old.get("pinned", False)) == bool(payload.get("pinned", False))
                    and str(old.get("remote_session_id") or "") == str(payload.get("remote_session_id") or "")
                    and str(old.get("device_id") or "") == str(payload.get("device_id") or "")
                    and lz._normalize_usage_channel_id(old.get("channel_id"), "launcher") == lz._normalize_usage_channel_id(payload.get("channel_id"), "launcher")
                )
                if same_payload and include_usage:
                    try:
                        same_payload = json.dumps(old.get("token_usage") or {}, ensure_ascii=False, sort_keys=True) == json.dumps(
                            payload.get("token_usage") or {},
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    except Exception:
                        same_payload = False
                if not same_payload:
                    lz.save_session(self.agent_dir, payload, touch=False)
                    changed = True
        for meta in lz.list_sessions(self.agent_dir):
            scope = str(meta.get("device_scope") or "").strip().lower()
            if scope != "remote":
                continue
            if (not include_all_channels) and lz._normalize_usage_channel_id(meta.get("channel_id"), "launcher") != "launcher":
                continue
            if str(meta.get("session_kind") or "").strip().lower() == "channel_process":
                continue
            did = str(meta.get("device_id") or "").strip()
            sid = str(meta.get("id") or "").strip()
            remote_sid = str(meta.get("remote_session_id") or "").strip()
            if not did or did not in synced_device_ids:
                continue
            if not sid.startswith(f"rchat_{did}_"):
                continue
            if not remote_sid:
                continue
            if sid in (active_ids_by_device.get(did) or set()):
                continue
            lz.delete_session(self.agent_dir, sid)
            changed = True
        return changed

    def _sidebar_post_ui(self, fn):
        callback = fn if callable(fn) else (lambda: None)
        poster = getattr(self, "_api_on_ui_thread", None)
        if callable(poster):
            try:
                poster(callback)
                return
            except Exception:
                pass
        app = QApplication.instance()
        try:
            if app is not None:
                QTimer.singleShot(0, app, callback)
            else:
                QTimer.singleShot(0, callback)
        except Exception:
            pass

    def _should_refresh_remote_sync_ui(self):
        mode = str(getattr(self, "_sidebar_view_mode", "roots") or "roots").strip().lower()
        if mode == "remote_devices":
            return True
        scope, _did = self._current_device_context()
        if scope == "remote" and mode in ("channels", "sessions"):
            return True
        current_scope, _current_did = self._session_device_scope_id(self.current_session or {})
        return current_scope == "remote"

    def _sync_remote_device_launcher_sessions(self, *, force=False, device_id="", trigger_refresh=True):
        if not lz.is_valid_agent_dir(self.agent_dir):
            return
        req_device_id = str(device_id or "").strip()
        if not self._auto_ssh_remote_devices(req_device_id):
            return
        now = time.time()
        next_at = float(getattr(self, "_next_remote_launcher_sync_at", 0) or 0)
        if (not force) and now < next_at:
            return
        running = bool(getattr(self, "_remote_launcher_sync_running", False))
        if running:
            if force:
                self._remote_launcher_sync_pending_force = True
            did = str(device_id or "").strip()
            if did:
                self._remote_launcher_sync_pending_device_id = did
            if trigger_refresh:
                self._remote_launcher_sync_pending_refresh = True
            return
        self._remote_launcher_sync_running = True
        req_force = bool(force)
        req_refresh = bool(trigger_refresh)

        def worker():
            changed = False
            try:
                changed = bool(self._sync_remote_device_launcher_sessions_blocking(force=req_force, device_id=req_device_id))
            except Exception:
                changed = False

            def done():
                self._remote_launcher_sync_running = False
                if changed and req_refresh and self._should_refresh_remote_sync_ui():
                    self._last_session_list_signature = None
                    self._refresh_sessions()
                pending_force = bool(getattr(self, "_remote_launcher_sync_pending_force", False))
                pending_device_id = str(getattr(self, "_remote_launcher_sync_pending_device_id", "") or "").strip()
                pending_refresh = bool(getattr(self, "_remote_launcher_sync_pending_refresh", False))
                self._remote_launcher_sync_pending_force = False
                self._remote_launcher_sync_pending_device_id = ""
                self._remote_launcher_sync_pending_refresh = False
                if pending_force or pending_device_id or pending_refresh:
                    self._sync_remote_device_launcher_sessions(
                        force=pending_force,
                        device_id=pending_device_id,
                        trigger_refresh=pending_refresh,
                    )

            self._sidebar_post_ui(done)

        threading.Thread(target=worker, name="remote-launcher-sync", daemon=True).start()

    def _save_remote_session_source(self, session):
        data = session if isinstance(session, dict) else {}
        scope, did = self._session_device_scope_id(data)
        if scope != "remote":
            return True, ""
        if self._is_channel_process_session(data):
            return True, ""
        dev = self._remote_device_by_id(did)
        if not isinstance(dev, dict):
            return False, "远程设备配置不存在。"
        remote_sid = self._remote_source_session_id(data)
        cache_sid = str(data.get("id") or "").strip() or self._remote_cache_session_id(did, remote_sid)
        payload = dict(data)
        payload["id"] = remote_sid
        payload["remote_session_id"] = remote_sid
        payload["device_scope"] = "remote"
        payload["device_id"] = did
        payload["device_name"] = str(payload.get("device_name") or dev.get("name") or "远程设备").strip() or "远程设备"
        payload["channel_id"] = lz._normalize_usage_channel_id(payload.get("channel_id"), "launcher")
        payload["channel_label"] = str(payload.get("channel_label") or lz._usage_channel_label(payload["channel_id"])).strip() or lz._usage_channel_label(payload["channel_id"])
        payload.pop("path", None)
        payload["updated_at"] = float(payload.get("updated_at", time.time()) or time.time())
        payload.setdefault("created_at", payload["updated_at"])
        client, err_msg, _missing = self._open_remote_device_client(dev, timeout=12)
        if client is None:
            return False, err_msg
        try:
            ok, detail = self._ensure_remote_launcher_sessions_dir(client, dev)
            if not ok:
                return False, detail
            sftp = client.open_sftp()
            try:
                remote_dir = self._remote_launcher_sessions_dir(dev)
                remote_fp = f"{remote_dir}/{remote_sid}.json"
                text = json.dumps(payload, ensure_ascii=False, indent=2)
                with sftp.open(remote_fp, "wb") as fp:
                    fp.write(text.encode("utf-8"))
            finally:
                try:
                    sftp.close()
                except Exception:
                    pass
        except Exception as e:
            return False, f"写入远端会话失败：{e}"
        finally:
            try:
                client.close()
            except Exception:
                pass
        local_payload = dict(payload)
        local_payload["id"] = cache_sid
        local_payload["remote_session_id"] = remote_sid
        local_payload["remote_updated_at"] = float(payload.get("updated_at", 0) or 0)
        lz.save_session(self.agent_dir, local_payload, touch=False)
        return True, ""

    def _save_remote_session_source_async(self, session, *, on_error_status=True):
        data = _session_copy(session if isinstance(session, dict) else {})
        scope, _did = self._session_device_scope_id(data)
        if scope != "remote":
            return

        def worker():
            ok, err = self._save_remote_session_source(data)

            def done():
                if not ok and on_error_status:
                    self._set_status(f"远端会话同步失败：{err}")

            self._sidebar_post_ui(done)

        threading.Thread(target=worker, name="remote-session-save", daemon=True).start()

    def _delete_remote_session_source(self, session):
        data = session if isinstance(session, dict) else {}
        scope, did = self._session_device_scope_id(data)
        if scope != "remote":
            return True, ""
        if self._is_channel_process_session(data):
            return True, ""
        dev = self._remote_device_by_id(did)
        if not isinstance(dev, dict):
            return False, "远程设备配置不存在。"
        remote_sid = self._remote_source_session_id(data)
        if not remote_sid:
            return False, "远端会话 ID 无效。"
        client, err_msg, _missing = self._open_remote_device_client(dev, timeout=10)
        if client is None:
            return False, err_msg
        try:
            ok, detail = self._ensure_remote_launcher_sessions_dir(client, dev)
            if not ok:
                return False, detail
            remote_dir = self._remote_launcher_sessions_dir(dev)
            cmd = f"rm -f {shlex.quote(remote_dir.rstrip('/') + '/' + remote_sid + '.json')} >/dev/null 2>&1 || true"
            rc, _out, err = self._vps_exec_remote(client, cmd, timeout=20)
            if rc != 0:
                return False, str(err or "删除远端会话失败。").strip() or "删除远端会话失败。"
            return True, ""
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _refresh_remote_session_cache(self, session):
        data = session if isinstance(session, dict) else {}
        scope, did = self._session_device_scope_id(data)
        if scope != "remote":
            return data, ""
        if self._is_channel_process_session(data):
            return data, ""
        dev = self._remote_device_by_id(did)
        if not isinstance(dev, dict):
            return data, "远程设备配置不存在。"
        remote_sid = self._remote_source_session_id(data)
        payload, err = self._fetch_remote_session_payload(dev, remote_sid)
        if not isinstance(payload, dict):
            return data, err or "读取远端会话失败。"
        local_payload = dict(payload)
        local_payload["id"] = str(data.get("id") or "").strip() or self._remote_cache_session_id(did, remote_sid)
        local_payload["remote_session_id"] = remote_sid
        local_payload["device_scope"] = "remote"
        local_payload["device_id"] = did
        local_payload["device_name"] = str((dev or {}).get("name") or payload.get("device_name") or "远程设备").strip() or "远程设备"
        local_payload["session_source_label"] = local_payload["device_name"]
        channel_id = lz._normalize_usage_channel_id(payload.get("channel_id") or data.get("channel_id"), "launcher")
        local_payload["channel_id"] = channel_id
        local_payload["channel_label"] = str(payload.get("channel_label") or data.get("channel_label") or lz._usage_channel_label(channel_id)).strip() or lz._usage_channel_label(channel_id)
        local_payload["remote_updated_at"] = float(payload.get("updated_at", 0) or 0)
        lz._normalize_token_usage_inplace(local_payload)
        lz.save_session(self.agent_dir, local_payload, touch=False)
        return local_payload, ""

    def _refresh_remote_session_cache_async(self, session):
        data = _session_copy(session if isinstance(session, dict) else {})
        sid = str(data.get("id") or "").strip()
        if not sid:
            return
        scope, _did = self._session_device_scope_id(data)
        if scope != "remote":
            return
        inflight = getattr(self, "_remote_session_refresh_inflight", None)
        if not isinstance(inflight, set):
            inflight = set()
            self._remote_session_refresh_inflight = inflight
        if sid in inflight:
            return
        inflight.add(sid)

        def worker():
            fresh, err = self._refresh_remote_session_cache(data)

            def done():
                inflight.discard(sid)
                if isinstance(fresh, dict):
                    self._last_session_list_signature = None
                    current_sid = str((self.current_session or {}).get("id") or "").strip()
                    if current_sid == sid:
                        self.current_session = fresh
                        self._render_session(self.current_session)
                        self._refresh_composer_enabled()
                        self._set_status("已同步远程会话。")
                    self._refresh_sessions()
                    return
                if err:
                    current_sid = str((self.current_session or {}).get("id") or "").strip()
                    if current_sid == sid:
                        self._set_status(f"远端同步失败，已使用本地缓存：{err}")

            self._sidebar_post_ui(done)

        threading.Thread(target=worker, name="remote-session-refresh", daemon=True).start()

    def _fetch_remote_channel_snapshots(self, device):
        if not self._remote_device_auto_ssh_enabled(device):
            return False, [], "该远程设备已关闭自动 SSH。"
        payload = self._remote_device_ssh_payload(device)
        if not payload:
            return False, [], "远程设备 SSH 配置无效。"
        dev = device if isinstance(device, dict) else {}
        remote_dir = normalize_remote_agent_dir(dev.get("agent_dir"), username=dev.get("username"))
        python_cmd = str((device or {}).get("python_cmd") or "python3").strip() or "python3"
        client, _err_msg, _detail, _missing = self._open_vps_ssh_client(payload, timeout=8)
        if client is None:
            detail = str(_detail or _err_msg or "SSH 连接失败。").strip() or "SSH 连接失败。"
            return False, [], detail
        try:
            q_dir = shlex.quote(remote_dir)
            q_py = shlex.quote(python_cmd)
            cmd = (
                "set -e; "
                f"cd {q_dir}; "
                f"PY_BIN={q_py}; "
                "if ! command -v \"$PY_BIN\" >/dev/null 2>&1; then "
                "if command -v python3 >/dev/null 2>&1; then PY_BIN=python3; "
                "elif command -v python >/dev/null 2>&1; then PY_BIN=python; "
                "else echo '{}'; exit 0; fi; "
                "fi; "
                "\"$PY_BIN\" - <<'GA_SNAPSHOT_PY'\n"
                "import glob, json, os, time\n"
                "base = os.path.join(os.getcwd(), 'temp', 'launcher_sessions')\n"
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
                "def read_tail(path, limit=12000):\n"
                "    if not path or (not os.path.isfile(path)):\n"
                "        return ''\n"
                "    try:\n"
                "        with open(path, 'r', encoding='utf-8', errors='replace') as f:\n"
                "            return f.read()[-limit:]\n"
                "    except Exception:\n"
                "        return ''\n"
                "\n"
                "def fmt_ts(value, fallback='未知'):\n"
                "    ts = float(value or 0)\n"
                "    if ts <= 0:\n"
                "        return fallback\n"
                "    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))\n"
                "\n"
                "def build_bubble(label, status, pid, started_at, ended_at, log_path, tail_text):\n"
                "    parts = [\n"
                "        f'**{label} 渠道进程快照**',\n"
                "        '',\n"
                "        f'- 状态：{status or \"未知\"}',\n"
                "        f'- PID：{pid or \"未知\"}',\n"
                "        f'- 启动时间：{fmt_ts(started_at)}',\n"
                "        f'- 结束时间：{fmt_ts(ended_at, \"仍在运行\") if float(ended_at or 0) > 0 else \"仍在运行\"}',\n"
                "        f'- 日志文件：`{log_path}`' if log_path else '- 日志文件：暂无',\n"
                "        '',\n"
                "        '```log',\n"
                "        tail_text or '(暂无日志输出)',\n"
                "        '```',\n"
                "    ]\n"
                "    return '\\n'.join(parts)\n"
                "\n"
                "rows = []\n"
                "for fp in glob.glob(os.path.join(base, '*.json')):\n"
                "    try:\n"
                "        with open(fp, 'r', encoding='utf-8', errors='replace') as f:\n"
                "            data = json.load(f)\n"
                "    except Exception:\n"
                "        continue\n"
                "    if str(data.get('session_kind') or '').strip().lower() != 'channel_process':\n"
                "        continue\n"
                "    cid = str(data.get('channel_id') or 'launcher').strip().lower() or 'launcher'\n"
                "    clabel = str(data.get('channel_label') or cid or 'channel')\n"
                "    pid = int(data.get('process_pid') or 0)\n"
                "    started = float(data.get('process_started_at', data.get('created_at', 0)) or 0)\n"
                "    ended = float(data.get('process_ended_at', 0) or 0)\n"
                "    status = str(data.get('process_status') or '').strip()\n"
                "    alive = pid_alive(pid)\n"
                "    changed = False\n"
                "    if alive:\n"
                "        if status != '运行中':\n"
                "            status = '运行中'\n"
                "            changed = True\n"
                "        if ended > 0:\n"
                "            ended = 0\n"
                "            changed = True\n"
                "    else:\n"
                "        if pid > 0 and ended <= 0:\n"
                "            ended = time.time()\n"
                "            changed = True\n"
                "        if (not status) or ('运行中' in status):\n"
                "            status = '已退出'\n"
                "            changed = True\n"
                "    log_path = str(data.get('channel_log_path') or '').strip()\n"
                "    if not log_path:\n"
                "        log_path = os.path.join(os.getcwd(), 'temp', 'launcher_channels', f'{cid}.log')\n"
                "    tail_text = read_tail(log_path, limit=12000)\n"
                "    bubble_text = build_bubble(clabel, status, pid, started, ended, log_path, tail_text)\n"
                "    updated_at = float(data.get('updated_at', 0) or 0)\n"
                "    if changed:\n"
                "        now = time.time()\n"
                "        data['process_status'] = status\n"
                "        data['process_ended_at'] = ended\n"
                "        data['updated_at'] = now\n"
                "        updated_at = now\n"
                "        try:\n"
                "            with open(fp, 'w', encoding='utf-8') as f:\n"
                "                json.dump(data, f, ensure_ascii=False, indent=2)\n"
                "        except Exception:\n"
                "            pass\n"
                "    rows.append({\n"
                "        'channel_id': cid,\n"
                "        'channel_label': clabel,\n"
                "        'title': str(data.get('title') or f'{clabel} 进程'),\n"
                "        'updated_at': updated_at,\n"
                "        'process_status': status,\n"
                "        'process_pid': pid,\n"
                "        'process_started_at': started,\n"
                "        'process_ended_at': ended,\n"
                "        'bubble_text': bubble_text,\n"
                "    })\n"
                "print(json.dumps({'rows': rows}, ensure_ascii=False))\n"
                "GA_SNAPSHOT_PY"
            )
            rc, out, err = self._vps_exec_remote(client, cmd, timeout=180)
            if rc != 0:
                detail = str(err or out or f"远端命令失败 (exit {rc})").strip() or f"远端命令失败 (exit {rc})"
                return False, [], detail
            raw = str(out or err or "").strip()
            if not raw:
                return False, [], "远端未返回渠道状态数据。"
            payload_obj = None
            for line in reversed(raw.splitlines()):
                text = str(line or "").strip()
                if not text.startswith("{"):
                    continue
                try:
                    parsed = json.loads(text)
                except Exception:
                    parsed = None
                if isinstance(parsed, dict):
                    payload_obj = parsed
                    break
            if not isinstance(payload_obj, dict):
                return False, [], "远端返回格式异常。"
            rows = payload_obj.get("rows")
            if not isinstance(rows, list):
                return False, [], "远端缺少渠道快照列表。"
            return True, [dict(item) for item in rows if isinstance(item, dict)], ""
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _sync_remote_device_channel_process_sessions_blocking(self):
        if not lz.is_valid_agent_dir(self.agent_dir):
            return False
        now = time.time()
        next_at = float(getattr(self, "_next_remote_channel_sync_at", 0) or 0)
        if now < next_at:
            return False
        self._next_remote_channel_sync_at = now + 15.0
        checked_map = getattr(self, "_remote_channel_checked_at", None)
        if not isinstance(checked_map, dict):
            checked_map = {}
            self._remote_channel_checked_at = checked_map
        device_checked_map = getattr(self, "_remote_channel_device_checked_at", None)
        if not isinstance(device_checked_map, dict):
            device_checked_map = {}
            self._remote_channel_device_checked_at = device_checked_map
        error_map = getattr(self, "_remote_channel_device_sync_errors", None)
        if not isinstance(error_map, dict):
            error_map = {}
            self._remote_channel_device_sync_errors = error_map
        devices = self._auto_ssh_remote_devices()
        active_ids_by_device = {}
        synced_device_ids = set()
        changed = False
        for dev in devices:
            did = str(dev.get("id") or "").strip()
            if not did:
                continue
            dname = str(dev.get("name") or dev.get("host") or "远程设备").strip() or "远程设备"
            sync_meta = dict(error_map.get(did) or {})
            sync_meta["last_attempt_at"] = now
            ok, rows, err = self._fetch_remote_channel_snapshots(dev)
            if not ok:
                # 避免 SSH 暂时不可达时误删本地镜像会话
                sync_meta["fail_count"] = int(sync_meta.get("fail_count") or 0) + 1
                raw_err = str(err or "远端状态读取失败。").strip() or "远端状态读取失败。"
                sync_meta["last_error"] = normalize_ssh_error_text(raw_err, context="SSH 连接")
                sync_meta["last_error_at"] = now
                error_map[did] = sync_meta
                continue
            synced_device_ids.add(did)
            device_checked_map[did] = now
            sync_meta["fail_count"] = 0
            sync_meta["last_error"] = ""
            sync_meta["last_error_at"] = 0.0
            sync_meta["last_success_at"] = now
            error_map[did] = sync_meta
            active_ids = active_ids_by_device.setdefault(did, set())
            for row in rows:
                cid = lz._normalize_usage_channel_id(row.get("channel_id"), "launcher")
                checked_map[(did, cid)] = now
                sid = f"rdev_{did}_{cid}_proc"
                active_ids.add(sid)
                data = lz.load_session(self.agent_dir, sid) or {}
                title = str(row.get("title") or "").strip() or f"{dname} · {lz._usage_channel_label(cid)} 进程"
                bubble_text = str(row.get("bubble_text") or "").strip()
                if not bubble_text:
                    bubble_text = f"**{dname} / {lz._usage_channel_label(cid)} 渠道进程快照**\n\n(远端暂无日志)"
                payload = {
                    "id": sid,
                    "title": title,
                    "created_at": float(data.get("created_at", row.get("process_started_at", now)) or now),
                    "updated_at": float(row.get("updated_at", now) or now),
                    "session_kind": "channel_process",
                    "session_source_label": dname,
                    "channel_id": cid,
                    "channel_label": str(row.get("channel_label") or lz._usage_channel_label(cid)).strip() or lz._usage_channel_label(cid),
                    "device_scope": "remote",
                    "device_id": did,
                    "device_name": dname,
                    "process_pid": int(row.get("process_pid") or 0),
                    "process_status": str(row.get("process_status") or "").strip() or "运行中",
                    "process_started_at": float(row.get("process_started_at", 0) or 0),
                    "process_ended_at": float(row.get("process_ended_at", 0) or 0),
                    "bubbles": [{"role": "assistant", "text": bubble_text}],
                    "backend_history": [],
                    "agent_history": [],
                    "llm_idx": 0,
                    "token_usage": {"events": []},
                    "snapshot": {
                        "version": 1,
                        "kind": "channel_process",
                        "captured_at": now,
                        "turns": 0,
                        "llm_idx": 0,
                        "process_pid": int(row.get("process_pid") or 0),
                        "has_backend_history": False,
                        "has_agent_history": False,
                    },
                }
                if bool(data.get("pinned", False)):
                    payload["pinned"] = True
                lz._normalize_token_usage_inplace(payload)
                same_payload = (
                    str(data.get("title") or "") == payload["title"]
                    and float(data.get("updated_at", 0) or 0) == payload["updated_at"]
                    and int(data.get("process_pid", 0) or 0) == payload["process_pid"]
                    and str(data.get("process_status") or "") == payload["process_status"]
                    and float(data.get("process_started_at", 0) or 0) == payload["process_started_at"]
                    and float(data.get("process_ended_at", 0) or 0) == payload["process_ended_at"]
                    and str(data.get("device_scope") or "") == payload["device_scope"]
                    and str(data.get("device_id") or "") == payload["device_id"]
                    and str(data.get("channel_id") or "") == payload["channel_id"]
                    and str(data.get("session_kind") or "") == payload["session_kind"]
                    and str(((list(data.get("bubbles") or [{}])[-1] or {}).get("text") or "")) == bubble_text
                )
                if not same_payload:
                    lz.save_session(self.agent_dir, payload, touch=False)
                    changed = True
        prefix = "rdev_"
        for meta in lz.list_sessions(self.agent_dir):
            sid = str(meta.get("id") or "").strip()
            if not sid.startswith(prefix):
                continue
            if str(meta.get("session_kind") or "").strip().lower() != "channel_process":
                continue
            if str(meta.get("device_scope") or "").strip().lower() != "remote":
                continue
            did = str(meta.get("device_id") or "").strip()
            if not did or did not in synced_device_ids:
                continue
            active_ids = active_ids_by_device.get(did) or set()
            if sid in active_ids:
                continue
            lz.delete_session(self.agent_dir, sid)
            changed = True
        return changed

    def _sync_remote_device_channel_process_sessions(self):
        if not lz.is_valid_agent_dir(self.agent_dir):
            return
        if not self._auto_ssh_remote_devices():
            return
        now = time.time()
        next_at = float(getattr(self, "_next_remote_channel_sync_at", 0) or 0)
        if now < next_at:
            return
        if bool(getattr(self, "_remote_channel_sync_running", False)):
            return
        self._remote_channel_sync_running = True

        def worker():
            changed = False
            try:
                changed = bool(self._sync_remote_device_channel_process_sessions_blocking())
            except Exception:
                changed = False

            def done():
                self._remote_channel_sync_running = False
                if changed and self._should_refresh_remote_sync_ui():
                    self._last_session_list_signature = None
                    self._refresh_sessions()
                if changed:
                    refresher = getattr(self, "_refresh_channels_runtime_status_labels", None)
                    if callable(refresher):
                        try:
                            refresher()
                        except Exception:
                            pass

            self._sidebar_post_ui(done)

        threading.Thread(target=worker, name="remote-channel-sync", daemon=True).start()

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
        scope, did = self._current_device_context()
        self._sidebar_open_device(scope, did)

    def _sidebar_open_channel(self, channel_id: str):
        self._sidebar_view_mode = "sessions"
        self._sidebar_channel_id = lz._normalize_usage_channel_id(channel_id, "launcher")
        self._last_session_list_signature = None
        self._refresh_sessions()

    def _sidebar_root_rows(self):
        return [
            {"kind": "root_local", "title": "本机", "subtitle": "当前设备会话与渠道"},
            {"kind": "root_remote", "title": "其他设备", "subtitle": "通过 SSH 连接的远程 agant"},
        ]

    def _sidebar_device_rows(self):
        rows = []
        for item in self._remote_devices():
            rows.append(
                {
                    "kind": "device",
                    "device_scope": "remote",
                    "device_id": str(item.get("id") or "").strip(),
                    "device_name": str(item.get("name") or "").strip() or str(item.get("host") or "远程设备"),
                    "host": str(item.get("host") or "").strip(),
                    "username": str(item.get("username") or "").strip(),
                    "port": int(item.get("port") or 22),
                    "auto_ssh": self._remote_device_auto_ssh_enabled(item),
                }
            )
        rows.sort(key=lambda row: str(row.get("device_name") or "").lower())
        return rows

    def _sidebar_channel_rows(self):
        scope, device_id = self._current_device_context()
        counts = {}
        if lz.is_valid_agent_dir(self.agent_dir):
            for meta in lz.list_sessions(self.agent_dir):
                if not self._session_matches_device(meta, scope, device_id):
                    continue
                cid = lz._normalize_usage_channel_id(meta.get("channel_id"), "launcher")
                counts[cid] = counts.get(cid, 0) + 1
        rows = []
        for cid in self._archive_known_channel_ids():
            active_count = int(counts.get(cid, 0) or 0)
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
        scope, device_id = self._current_device_context()
        cid = lz._normalize_usage_channel_id(channel_id, "launcher")
        rows = []
        for meta in lz.list_sessions(self.agent_dir):
            if not self._session_matches_device(meta, scope, device_id):
                continue
            meta_cid = lz._normalize_usage_channel_id(meta.get("channel_id"), "launcher")
            if meta_cid != cid:
                continue
            rows.append(
                {
                    "kind": "session",
                    "id": meta.get("id"),
                    "title": meta.get("title") or "(未命名)",
                    "updated_at": float(meta.get("updated_at", 0) or 0),
                    "pinned": bool(meta.get("pinned", False)),
                    "channel_id": cid,
                    "channel_label": str(meta.get("channel_label") or lz._usage_channel_label(cid)),
                    "path": meta.get("path"),
                    "device_scope": scope,
                    "device_id": device_id,
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
            self._sidebar_device_scope,
            self._sidebar_device_id,
            self._sidebar_channel_id,
            self._selected_session_id or ((self.current_session or {}).get("id")),
            keyword,
            tuple(
                tuple(
                    row.get(key)
                    for key in (
                        "kind",
                        "id",
                        "channel_id",
                        "title",
                        "updated_at",
                        "pinned",
                        "active_count",
                        "device_id",
                        "device_name",
                        "host",
                        "auto_ssh",
                    )
                )
                for row in items
            ),
        )

    def _sidebar_item_text(self, row):
        if row.get("kind") == "root_local":
            return "本机\n当前设备会话" if not self.sidebar_collapsed else "本"
        if row.get("kind") == "root_remote":
            return "其他设备\nSSH 远程 agant" if not self.sidebar_collapsed else "设"
        if row.get("kind") == "device":
            name = str(row.get("device_name") or "远程设备").strip() or "远程设备"
            host = str(row.get("host") or "").strip()
            if self.sidebar_collapsed:
                return (name[:1] or "设").upper()
            subtitle = f"{row.get('username')}@{host}:{int(row.get('port') or 22)}" if host else "远程设备"
            return f"{name}\n{subtitle}"
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

    def _sidebar_uses_device_row_widget(self, row, mode):
        return (
            str(mode or "").strip().lower() == "remote_devices"
            and str((row or {}).get("kind") or "").strip().lower() == "device"
            and not self.sidebar_collapsed
        )

    def _make_sidebar_device_row_widget(self, row):
        did = str(row.get("device_id") or "").strip()
        selected = (
            str(getattr(self, "_sidebar_device_scope", "local") or "local").strip().lower() == "remote"
            and str(getattr(self, "_sidebar_device_id", "") or "").strip() == did
        )
        box = QFrame(self.session_list)
        box.setObjectName("sidebarDeviceRow")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        open_btn = QPushButton(self._sidebar_item_text(row), box)
        open_btn.setObjectName("sidebarDeviceOpenButton")
        open_btn.setCursor(Qt.PointingHandCursor)
        open_btn.setStyleSheet(self._sidebar_button_style(selected=selected))
        open_btn.clicked.connect(lambda _=False, device_id=did: self._sidebar_open_device("remote", device_id))
        layout.addWidget(open_btn, 1)

        toggle = QCheckBox("自动SSH", box)
        toggle.setObjectName("sidebarAutoSshSwitch")
        toggle.setCursor(Qt.PointingHandCursor)
        toggle.setFixedWidth(92)
        toggle.setChecked(self._remote_device_auto_ssh_enabled(row))
        toggle.setToolTip("打开后会自动同步这台设备；关闭后后台刷新、状态探测不会 SSH 到这台设备。")
        toggle.setStyleSheet(
            f"QCheckBox {{ color: {C['text_soft']}; font-size: 12px; spacing: 6px; padding: 0 4px; }}"
            f"QCheckBox:hover {{ color: {C['text']}; }}"
            f"QCheckBox::indicator {{ width: 30px; height: 16px; border-radius: 8px; "
            f"background: {C['layer3']}; border: 1px solid {C['stroke_default']}; }}"
            f"QCheckBox::indicator:checked {{ background: {C['accent']}; border-color: {C['accent']}; }}"
            f"QCheckBox::indicator:unchecked {{ background: {C['layer3']}; border-color: {C['stroke_default']}; }}"
        )
        toggle.toggled.connect(lambda checked, device_id=did: self._set_remote_device_auto_ssh(device_id, checked))
        layout.addWidget(toggle, 0, Qt.AlignVCenter)
        return box

    def _refresh_sessions(self):
        if bool(getattr(self, "_closing_in_progress", False)):
            return
        if not lz.is_valid_agent_dir(self.agent_dir):
            self._ignore_session_select = True
            if hasattr(self, "session_list") and self.session_list is not None:
                self.session_list.clear()
            self._ignore_session_select = False
            self._last_session_list_signature = None
            return
        keyword = str(getattr(self, "_session_filter_keyword", "") or "").strip().lower()
        mode = str(getattr(self, "_sidebar_view_mode", "roots") or "roots").strip().lower()
        scope, did = self._current_device_context()
        if scope == "remote" and mode in ("channels", "sessions") and self._remote_device_auto_ssh_enabled(did):
            self._sync_remote_device_launcher_sessions(device_id=did)
        if mode == "sessions":
            rows = [row for row in self._sidebar_session_rows(self._sidebar_channel_id) if self._sidebar_row_matches_keyword(row, keyword)]
        elif mode == "channels":
            rows = [row for row in self._sidebar_channel_rows() if self._sidebar_row_matches_keyword(row, keyword)]
        elif mode == "remote_devices":
            rows = [row for row in self._sidebar_device_rows() if self._sidebar_row_matches_keyword(row, keyword)]
        else:
            rows = [row for row in self._sidebar_root_rows() if self._sidebar_row_matches_keyword(row, keyword)]
        signature = self._session_list_signature(rows, keyword)
        if signature == getattr(self, "_last_session_list_signature", None):
            return
        wanted = self._selected_session_id or ((self.current_session or {}).get("id"))
        self._ignore_session_select = True
        updates_disabled = False
        try:
            try:
                self.session_list.setUpdatesEnabled(False)
                updates_disabled = True
            except Exception:
                updates_disabled = False
            self.session_list.clear()
            if getattr(self, "sidebar_group_label", None) is not None:
                if mode == "sessions":
                    self.sidebar_group_label.setText(lz._usage_channel_label(self._sidebar_channel_id))
                elif mode == "channels":
                    scope, did = self._current_device_context()
                    if scope == "remote":
                        dev = self._remote_device_by_id(did)
                        self.sidebar_group_label.setText(str((dev or {}).get("name") or "远程设备").strip() or "远程设备")
                    else:
                        self.sidebar_group_label.setText("本机")
                elif mode == "remote_devices":
                    self.sidebar_group_label.setText("其他设备")
                else:
                    self.sidebar_group_label.setText("设备")
            if mode in ("sessions", "channels", "remote_devices"):
                back_text = "← 返回渠道"
                if mode == "channels":
                    back_text = "← 返回设备"
                elif mode == "remote_devices":
                    back_text = "← 返回上层"
                back_item = QListWidgetItem(back_text if not self.sidebar_collapsed else "←")
                back_item.setData(Qt.UserRole, {"kind": "back"})
                self.session_list.addItem(back_item)
            for row in rows:
                text = self._sidebar_item_text(row)
                uses_row_widget = self._sidebar_uses_device_row_widget(row, mode)
                item = QListWidgetItem("" if uses_row_widget else text)
                item.setData(Qt.UserRole, row)
                tip = text
                if row.get("kind") == "channel":
                    tip = f"{row.get('channel_label')}\n会话 {row.get('active_count', 0)}"
                elif row.get("kind") == "device":
                    auto_text = "开" if self._remote_device_auto_ssh_enabled(row) else "关"
                    tip = f"{row.get('device_name')}\n{row.get('username')}@{row.get('host')}:{row.get('port')}\n自动SSH：{auto_text}"
                elif row.get("kind") in ("root_local", "root_remote"):
                    tip = f"{row.get('title')}\n{row.get('subtitle')}"
                item.setToolTip(tip)
                if self.sidebar_collapsed:
                    item.setTextAlignment(Qt.AlignCenter)
                self.session_list.addItem(item)
                if uses_row_widget:
                    widget = self._make_sidebar_device_row_widget(row)
                    item.setSizeHint(widget.sizeHint())
                    self.session_list.setItemWidget(item, widget)
                if row.get("kind") == "session" and wanted and row.get("id") == wanted:
                    self.session_list.setCurrentItem(item)
            back_count = 1 if mode in ("sessions", "channels", "remote_devices") else 0
            if self.session_list.count() == back_count:
                empty_text = "当前分类还没有会话"
                if mode == "remote_devices":
                    empty_text = "还没有可用的远程设备"
                elif mode == "roots":
                    empty_text = "暂无设备入口"
                empty = QListWidgetItem(empty_text)
                empty.setFlags(Qt.NoItemFlags)
                self.session_list.addItem(empty)
        finally:
            self._ignore_session_select = False
            if updates_disabled:
                try:
                    self.session_list.setUpdatesEnabled(True)
                except Exception:
                    pass
        self._last_session_list_signature = signature

    def _on_session_item_changed(self, current, previous):
        if self._ignore_session_select or current is None:
            return
        data = current.data(Qt.UserRole)
        if not isinstance(data, dict):
            return
        kind = data.get("kind")
        mode = str(getattr(self, "_sidebar_view_mode", "roots") or "roots").strip().lower()
        if kind == "root_local":
            self._sidebar_open_device("local", "local")
            return
        if kind == "root_remote":
            self._sidebar_open_remote_devices()
            return
        if kind == "device":
            if mode == "remote_devices" and self.session_list.itemWidget(current) is not None:
                return
            self._sidebar_open_device(data.get("device_scope") or "remote", data.get("device_id") or "")
            return
        if kind == "channel":
            self._sidebar_open_channel(data.get("channel_id") or "launcher")
            return
        if kind == "back":
            mode = str(getattr(self, "_sidebar_view_mode", "roots") or "roots").strip().lower()
            if mode == "sessions":
                self._sidebar_switch_to_channels()
            elif mode == "channels":
                scope, _did = self._current_device_context()
                if scope == "remote":
                    self._sidebar_open_remote_devices()
                else:
                    self._sidebar_switch_to_roots()
            elif mode == "remote_devices":
                self._sidebar_switch_to_roots()
            else:
                self._sidebar_switch_to_roots()
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
        mode = str(getattr(self, "_sidebar_view_mode", "") or "").strip().lower()
        item = self.session_list.itemAt(pos)
        data = item.data(Qt.UserRole) if item is not None else None
        if not isinstance(data, dict):
            return
        kind = str(data.get("kind") or "").strip().lower()
        if mode == "remote_devices" and kind == "device":
            menu = QMenu(self)
            rename_action = menu.addAction("重命名设备")
            auto_enabled = self._remote_device_auto_ssh_enabled(data)
            toggle_action = menu.addAction("关闭自动 SSH" if auto_enabled else "开启自动 SSH")
            chosen = menu.exec(self.session_list.viewport().mapToGlobal(pos))
            if chosen is rename_action:
                old_name = str(data.get("device_name") or "").strip()
                text, ok = QInputDialog.getText(self, "重命名设备", "设备名称", text=old_name)
                if not ok:
                    return
                new_name = str(text or "").strip()
                if not new_name or new_name == old_name:
                    return
                did = str(data.get("device_id") or "").strip()
                rows = self._remote_devices()
                changed = False
                for row in rows:
                    if str(row.get("id") or "").strip() == did:
                        row["name"] = new_name
                        changed = True
                        break
                if changed:
                    self._save_remote_devices(rows)
                    self._last_session_list_signature = None
                    self._refresh_sessions()
            elif chosen is toggle_action:
                self._set_remote_device_auto_ssh(data.get("device_id") or "", not auto_enabled)
            return
        if mode != "sessions" or kind != "session":
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
        scope, _did = self._session_device_scope_id(data)
        if scope == "remote":
            return self._save_remote_session_source(data)
        return True, ""

    def _set_sidebar_sessions_pinned(self, rows, pinned: bool):
        failed = []
        for row in rows:
            data = self._load_sidebar_session_row(row)
            if not data:
                continue
            data["pinned"] = bool(pinned)
            ok, err = self._save_sidebar_session_row(row, data, touch=True)
            if not ok:
                failed.append(str(err or "同步远端失败。"))
        self._last_session_list_signature = None
        self._refresh_sessions()
        if failed:
            QMessageBox.warning(self, "远端同步失败", "\n".join(dict.fromkeys(failed)))

    def _clear_current_context_after_session_removed(self, status_text: str, *, restart_bridge=True):
        self._pending_state_session = None
        self.current_session = None
        self._selected_session_id = None
        self._set_status(status_text)
        self._reset_chat_area("选择一个会话，或新建会话开始聊天。")
        if restart_bridge:
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
        deleted_current_scope = "local"
        failed = []
        for row in rows:
            sid = str(row.get("id") or "")
            if not sid:
                continue
            data = lz.load_session(self.agent_dir, sid) or {}
            scope, _did = self._session_device_scope_id(data)
            if scope == "remote":
                ok, err = self._delete_remote_session_source(data)
                if not ok:
                    failed.append(str(err or f"删除远端会话失败：{sid}"))
                    continue
            lz.delete_session(self.agent_dir, sid)
            if sid == current_sid:
                deleted_current = True
                deleted_current_scope = scope
        if deleted_current:
            self._clear_current_context_after_session_removed("当前会话已删除。", restart_bridge=(deleted_current_scope != "remote"))
        self._last_session_list_signature = None
        self._refresh_sessions()
        if failed:
            QMessageBox.warning(self, "远端删除失败", "\n".join(dict.fromkeys(failed)))

    def _load_session_by_id(self, sid: str):
        if self._busy:
            QMessageBox.information(self, "忙碌中", "当前还在生成，请先等待结束或手动中断。")
            return
        data = lz.load_session(self.agent_dir, sid)
        if not data:
            self._refresh_sessions()
            QMessageBox.warning(self, "会话不存在", "该会话已经失效，请重新选择。")
            return
        scope, did = self._session_device_scope_id(data)
        data["device_scope"] = scope
        data["device_id"] = did
        if scope == "remote":
            dev = self._remote_device_by_id(did)
            data["device_name"] = str((dev or {}).get("name") or data.get("device_name") or "远程设备").strip() or "远程设备"
        else:
            data["device_name"] = "本机"
        self._selected_session_id = sid
        self.current_session = data
        self._render_session(self.current_session)
        self._refresh_composer_enabled()
        if self._is_channel_process_session(self.current_session):
            self._set_status("已载入渠道进程快照。该会话仅用于回顾，不能在这里继续发送消息。")
            return
        if self._session_device_scope_id(self.current_session)[0] == "remote":
            self._set_status("已载入远程会话缓存，正在后台同步…")
            self._refresh_remote_session_cache_async(self.current_session)
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

    def _new_session_device_entries(self):
        entries = [
            {
                "label": "1. 本机（启动器）",
                "scope": "local",
                "device_id": "local",
                "device_name": "本机",
                "enabled": True,
            }
        ]
        for index, dev in enumerate(self._remote_devices(), start=2):
            did = str(dev.get("id") or "").strip()
            if not did:
                continue
            name = str(dev.get("name") or dev.get("host") or "远程设备").strip() or "远程设备"
            host = str(dev.get("host") or "").strip()
            username = str(dev.get("username") or "").strip()
            port = int(dev.get("port") or 22)
            enabled = self._remote_device_auto_ssh_enabled(dev)
            endpoint = f"{username}@{host}:{port}" if host else "远程设备"
            state = "自动SSH开" if enabled else "自动SSH关"
            entries.append(
                {
                    "label": f"{index}. {name}（服务器 agant，{endpoint}，{state}）",
                    "scope": "remote",
                    "device_id": did,
                    "device_name": name,
                    "enabled": enabled,
                }
            )
        return entries

    def _new_session_default_device_index(self, entries):
        scope, did = self._current_device_context()
        for idx, item in enumerate(entries or []):
            if str(item.get("scope") or "").strip().lower() != str(scope or "local").strip().lower():
                continue
            if str(item.get("scope") or "").strip().lower() == "remote":
                if str(item.get("device_id") or "").strip() == str(did or "").strip():
                    return idx
                continue
            return idx
        return 0

    def _choose_new_session_device(self):
        entries = self._new_session_device_entries()
        if not entries:
            return None
        labels = [str(item.get("label") or "").strip() for item in entries]
        default_index = self._new_session_default_device_index(entries)
        chosen, ok = QInputDialog.getItem(
            self,
            "新建对话",
            "选择这次对话运行在哪台设备的启动器分类中：",
            labels,
            default_index,
            False,
        )
        if not ok:
            return None
        try:
            idx = labels.index(str(chosen or ""))
        except ValueError:
            idx = default_index
        return dict(entries[max(0, min(idx, len(entries) - 1))])

    def _normalize_new_session_device_target(self, scope="", device_id=""):
        target_scope = str(scope or "local").strip().lower()
        if target_scope not in ("local", "remote"):
            target_scope = "local"
        target_id = str(device_id or "local").strip() or "local"
        if target_scope != "remote":
            return {"scope": "local", "device_id": "local", "device_name": "本机", "enabled": True}
        dev = self._remote_device_by_id(target_id)
        if not isinstance(dev, dict):
            return None
        return {
            "scope": "remote",
            "device_id": target_id,
            "device_name": str(dev.get("name") or dev.get("host") or "远程设备").strip() or "远程设备",
            "enabled": self._remote_device_auto_ssh_enabled(dev),
        }

    def _new_session(self, checked=False, *, scope="", device_id="", prompt_device=True):
        if self._busy:
            QMessageBox.information(self, "忙碌中", "当前还在生成，请先等待结束或手动中断。")
            return
        if prompt_device and not str(scope or "").strip() and not str(device_id or "").strip():
            target = self._choose_new_session_device()
        else:
            target = self._normalize_new_session_device_target(scope=scope, device_id=device_id)
        if not isinstance(target, dict):
            return
        scope = str(target.get("scope") or "local").strip().lower()
        did = str(target.get("device_id") or "local").strip() or "local"
        if scope == "remote":
            dev = self._remote_device_by_id(did)
            if not isinstance(dev, dict):
                QMessageBox.warning(self, "无法新建", "当前远程设备配置不存在，请先在“其他设备”里确认设备配置。")
                return
            if not self._remote_device_auto_ssh_enabled(dev):
                QMessageBox.information(self, "无法新建远程对话", "该远程设备已关闭自动 SSH，请先在“其他设备”中打开开关。")
                return
        if not self._can_create_session_for_channel("launcher", show_message=True, device_scope=scope, device_id=did):
            return
        self._pending_state_session = None
        self.current_session = None
        self._selected_session_id = None
        self._sidebar_view_mode = "sessions"
        self._sidebar_device_scope = scope
        self._sidebar_device_id = did if scope == "remote" else "local"
        self._sidebar_channel_id = "launcher"
        if scope == "remote":
            self._set_status(f"正在创建 {target.get('device_name') or '远程设备'} 的启动器对话…")
        else:
            self._set_status("正在创建新会话进程…")
        self._reset_chat_area(
            "远程设备已选定，发送第一条消息后会在该服务器 agant 的启动器分类中创建会话。"
            if scope == "remote" else
            "新进程已准备，发送第一条消息后会创建会话。"
        )
        if scope == "local":
            self._restart_bridge()
        self._refresh_composer_enabled()
        self._last_session_list_signature = None
        self._refresh_sessions()

    def _ensure_session(self, first_user_text: str):
        if self.current_session is not None:
            return
        scope, did = self._current_device_context()
        device_name = "本机"
        local_session_id = uuid.uuid4().hex[:12]
        remote_session_id = ""
        if scope == "remote":
            dev = self._remote_device_by_id(did)
            device_name = str((dev or {}).get("name") or (dev or {}).get("host") or "远程设备").strip() or "远程设备"
            remote_session_id = self._normalize_remote_session_id(uuid.uuid4().hex[:12], fallback=uuid.uuid4().hex[:12])
            local_session_id = self._remote_cache_session_id(did, remote_session_id)
        title = (first_user_text or "新会话").strip().replace("\n", " ")
        if len(title) > 30:
            title = title[:30] + "…"
        self.current_session = {
            "id": local_session_id,
            "remote_session_id": remote_session_id,
            "title": title or "新会话",
            "created_at": time.time(),
            "updated_at": time.time(),
            "bubbles": [],
            "process_pid": getattr(self.bridge_proc, "pid", None),
            "session_source_label": ("启动器" if scope == "local" else device_name),
            "channel_id": "launcher",
            "channel_label": lz._usage_channel_label("launcher"),
            "device_scope": scope,
            "device_id": (did if scope == "remote" else "local"),
            "device_name": device_name,
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
        scope, did = self._current_device_context()
        if scope == "remote" and self._remote_device_auto_ssh_enabled(did):
            self._sync_remote_device_launcher_sessions(force=True, device_id=did)
        self._refresh_sessions()
