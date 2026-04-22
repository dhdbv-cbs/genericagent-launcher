from __future__ import annotations

import json
import os
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget

from launcher_app import core as lz
from launcher_app.theme import C, F


class PersonalUsageMixin:
    _LANGFUSE_DEFAULT_HOST = "https://cloud.langfuse.com"
    _GITHUB_API_CANDIDATES = (
        "https://api.github.com{path}",
        "https://mirror.ghproxy.com/https://api.github.com{path}",
        "https://ghproxy.com/https://api.github.com{path}",
    )

    def _short_sha(self, sha):
        text = str(sha or "").strip()
        return text[:8] if len(text) >= 8 else (text or "未知")

    def _git_cmd_text(self, repo_dir: str, args, *, timeout: int = 8) -> str:
        root = os.path.abspath(str(repo_dir or "").strip()) if str(repo_dir or "").strip() else ""
        if not root or not os.path.isdir(root):
            return ""
        try:
            result = lz._run_external_subprocess(
                ["git", "-C", root, *list(args or [])],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(1, int(timeout or 8)),
            )
        except Exception:
            return ""
        if int(getattr(result, "returncode", 1) or 1) != 0:
            return ""
        return str(getattr(result, "stdout", "") or "").strip()

    def _git_is_ancestor(self, repo_dir: str, older_sha: str, newer_sha: str) -> bool:
        root = os.path.abspath(str(repo_dir or "").strip()) if str(repo_dir or "").strip() else ""
        if not root or not os.path.isdir(root):
            return False
        left = str(older_sha or "").strip()
        right = str(newer_sha or "").strip()
        if not left or not right:
            return False
        try:
            result = lz._run_external_subprocess(
                ["git", "-C", root, "merge-base", "--is-ancestor", left, right],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
            )
        except Exception:
            return False
        return int(getattr(result, "returncode", 1) or 1) == 0

    def _repo_slug_from_url(self, repo_url: str) -> str:
        raw = str(repo_url or "").strip()
        if not raw:
            return ""
        direct = re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", raw)
        if direct:
            return raw
        m = re.search(r"github\.com[:/]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", raw, flags=re.IGNORECASE)
        if not m:
            return ""
        owner = str(m.group(1) or "").strip()
        repo = str(m.group(2) or "").strip()
        if repo.endswith(".git"):
            repo = repo[:-4]
        return f"{owner}/{repo}" if owner and repo else ""

    def _build_github_api_urls(self, path: str):
        normalized_path = "/" + str(path or "").lstrip("/")
        full_url = f"https://api.github.com{normalized_path}"
        custom = self.cfg.get("github_update_api_urls")
        custom_items = custom if isinstance(custom, list) else []
        candidates = list(self._GITHUB_API_CANDIDATES) + [str(item or "").strip() for item in custom_items if str(item or "").strip()]
        seen = set()
        urls = []
        for template in candidates:
            text = str(template or "").strip()
            if not text:
                continue
            if "{path}" in text:
                url = text.replace("{path}", normalized_path)
            elif "{full_url}" in text:
                url = text.replace("{full_url}", full_url)
            elif "api.github.com" in text:
                url = text.rstrip("/") + normalized_path
            else:
                url = text.rstrip("/") + "/" + full_url
            if url in seen:
                continue
            seen.add(url)
            urls.append(url)
        return urls

    def _github_json_with_fallback(self, path: str, *, allow_404: bool = False, timeout: int = 8):
        last_errors = []
        for url in self._build_github_api_urls(path):
            req = Request(
                url,
                headers={
                    "Accept": "application/vnd.github+json, application/json",
                    "User-Agent": "GenericAgentLauncher/UpdateChecker",
                },
                method="GET",
            )
            try:
                with urlopen(req, timeout=max(2, int(timeout or 8))) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                payload = json.loads(raw) if raw.strip() else {}
                return payload, url, last_errors
            except HTTPError as e:
                if allow_404 and int(getattr(e, "code", 0) or 0) == 404:
                    return None, url, last_errors
                last_errors.append(f"{url} -> HTTP {int(getattr(e, 'code', 0) or 0)}")
            except URLError as e:
                last_errors.append(f"{url} -> {e.reason}")
            except Exception as e:
                last_errors.append(f"{url} -> {e}")
        return None, "", last_errors

    def _collect_local_repo_context(self, repo_dir: str):
        root = os.path.abspath(str(repo_dir or "").strip()) if str(repo_dir or "").strip() else ""
        if not root or not os.path.isdir(root):
            return {"repo_dir": "", "remote_url": "", "local_sha": ""}
        sha = self._git_cmd_text(root, ["rev-parse", "HEAD"])
        remote = self._git_cmd_text(root, ["remote", "get-url", "origin"])
        if sha:
            return {"repo_dir": root, "remote_url": remote, "local_sha": sha}
        return {"repo_dir": "", "remote_url": "", "local_sha": ""}

    def _discover_launcher_repo_context(self):
        roots = []
        for item in (lz.APP_DIR, os.path.dirname(lz.APP_DIR), os.getcwd()):
            text = os.path.abspath(str(item or "").strip()) if str(item or "").strip() else ""
            if text and text not in roots:
                roots.append(text)
        for root in roots:
            ctx = self._collect_local_repo_context(root)
            if ctx.get("repo_dir"):
                return ctx
        return {"repo_dir": "", "remote_url": "", "local_sha": ""}

    def _check_repo_update(self, *, name: str, repo_url: str, local_repo_dir: str, local_sha: str):
        row = {
            "name": str(name or "").strip(),
            "repo_url": str(repo_url or "").strip(),
            "repo_slug": "",
            "local_sha": str(local_sha or "").strip(),
            "remote_sha": "",
            "default_branch": "",
            "latest_release_tag": "",
            "status": "unknown",
            "message": "",
            "api_source": "",
            "errors": [],
        }
        slug = self._repo_slug_from_url(row["repo_url"])
        row["repo_slug"] = slug
        if not slug:
            row["status"] = "error"
            row["message"] = "仓库地址无法解析为 GitHub owner/repo。"
            return row

        meta, meta_url, meta_errors = self._github_json_with_fallback(f"/repos/{slug}", timeout=8)
        if not isinstance(meta, dict):
            row["status"] = "error"
            row["message"] = "无法访问 GitHub 仓库信息。"
            row["errors"] = list(meta_errors or [])
            return row
        row["api_source"] = meta_url
        default_branch = str(meta.get("default_branch") or "main").strip() or "main"
        row["default_branch"] = default_branch

        commit, commit_url, commit_errors = self._github_json_with_fallback(f"/repos/{slug}/commits/{default_branch}", timeout=8)
        if not isinstance(commit, dict):
            row["status"] = "error"
            row["message"] = "无法读取 GitHub 最新提交。"
            row["errors"] = list(meta_errors or []) + list(commit_errors or [])
            return row
        row["api_source"] = commit_url or row["api_source"]
        row["remote_sha"] = str(commit.get("sha") or "").strip()

        rel, _, rel_errors = self._github_json_with_fallback(f"/repos/{slug}/releases/latest", allow_404=True, timeout=8)
        if isinstance(rel, dict):
            row["latest_release_tag"] = str(rel.get("tag_name") or "").strip()
        row["errors"] = list(meta_errors or []) + list(commit_errors or []) + list(rel_errors or [])

        local = str(row.get("local_sha") or "").strip()
        remote = str(row.get("remote_sha") or "").strip()
        if not remote:
            row["status"] = "error"
            row["message"] = "GitHub 返回中缺少远端提交哈希。"
            return row
        if not local:
            row["status"] = "unknown_local"
            row["message"] = f"远端最新 {self._short_sha(remote)}，但本地版本无法识别。"
            return row
        if local == remote:
            row["status"] = "up_to_date"
            row["message"] = f"已是最新（{self._short_sha(local)}）。"
            return row
        repo_dir = os.path.abspath(str(local_repo_dir or "").strip()) if str(local_repo_dir or "").strip() else ""
        if repo_dir and self._git_is_ancestor(repo_dir, local, remote):
            row["status"] = "behind"
            row["message"] = f"本地 {self._short_sha(local)} 落后于远端 {self._short_sha(remote)}。"
            return row
        if repo_dir and self._git_is_ancestor(repo_dir, remote, local):
            row["status"] = "ahead"
            row["message"] = f"本地 {self._short_sha(local)} 比远端更新（可能是未推送提交）。"
            return row
        row["status"] = "diverged"
        row["message"] = f"本地 {self._short_sha(local)} 与远端 {self._short_sha(remote)} 分叉。"
        return row

    def _perform_update_check(self):
        checked_at = time.time()
        launcher_ctx = self._discover_launcher_repo_context()
        launcher_repo_url = (
            str(self.cfg.get("launcher_repo_url") or "").strip()
            or str(launcher_ctx.get("remote_url") or "").strip()
            or str(getattr(lz, "LAUNCHER_REPO_URL", "") or "").strip()
        )
        launcher = self._check_repo_update(
            name="启动器",
            repo_url=launcher_repo_url,
            local_repo_dir=str(launcher_ctx.get("repo_dir") or ""),
            local_sha=str(launcher_ctx.get("local_sha") or ""),
        )

        if lz.is_valid_agent_dir(self.agent_dir):
            kernel_ctx = self._collect_local_repo_context(self.agent_dir)
            kernel_repo_url = str(kernel_ctx.get("remote_url") or "").strip() or str(lz.REPO_URL or "").strip()
            kernel = self._check_repo_update(
                name="agant 内核（GenericAgent）",
                repo_url=kernel_repo_url,
                local_repo_dir=str(kernel_ctx.get("repo_dir") or ""),
                local_sha=str(kernel_ctx.get("local_sha") or ""),
            )
        else:
            kernel = {
                "name": "agant 内核（GenericAgent）",
                "repo_url": str(lz.REPO_URL or "").strip(),
                "repo_slug": self._repo_slug_from_url(str(lz.REPO_URL or "").strip()),
                "local_sha": "",
                "remote_sha": "",
                "default_branch": "",
                "latest_release_tag": "",
                "status": "skipped",
                "message": "当前没有有效的 GenericAgent 目录，已跳过内核更新检查。",
                "api_source": "",
                "errors": [],
            }

        has_update = any(str(item.get("status") or "") == "behind" for item in (launcher, kernel))
        return {"checked_at": checked_at, "launcher": launcher, "kernel": kernel, "has_update": has_update}

    def _update_row_to_text(self, row):
        item = row if isinstance(row, dict) else {}
        label = str(item.get("name") or "组件")
        status = str(item.get("status") or "").strip().lower()
        msg = str(item.get("message") or "").strip()
        tag = str(item.get("latest_release_tag") or "").strip()
        tail = f"（最新发布 {tag}）" if tag else ""
        if status == "behind":
            return f"- {label}：发现更新。{msg}{tail}"
        if status == "up_to_date":
            return f"- {label}：已是最新。{msg}{tail}"
        if status == "ahead":
            return f"- {label}：本地领先远端。{msg}{tail}"
        if status == "diverged":
            return f"- {label}：本地与远端分叉。{msg}{tail}"
        if status == "skipped":
            return f"- {label}：未检查。{msg}"
        if status == "unknown_local":
            return f"- {label}：远端可达，但无法判断本地版本。{msg}{tail}"
        return f"- {label}：检查失败。{msg}"

    def _update_result_summary(self, result):
        payload = result if isinstance(result, dict) else {}
        checked_at = float(payload.get("checked_at") or 0)
        when = self._usage_time_label(checked_at) if checked_at > 0 else "未知时间"
        lines = [f"GitHub 检查时间：{when}"]
        lines.append(self._update_row_to_text(payload.get("launcher")))
        lines.append(self._update_row_to_text(payload.get("kernel")))
        return "\n".join(lines)

    def _update_history_items(self):
        history = self.cfg.get("github_update_check_history")
        if not isinstance(history, list):
            return []
        out = []
        for item in history:
            if isinstance(item, dict):
                out.append(item)
        return out

    def _append_update_history(self, result):
        payload = result if isinstance(result, dict) else {}

        def _compact(row):
            item = row if isinstance(row, dict) else {}
            return {
                "name": str(item.get("name") or ""),
                "status": str(item.get("status") or ""),
                "local_sha": str(item.get("local_sha") or ""),
                "remote_sha": str(item.get("remote_sha") or ""),
                "message": str(item.get("message") or ""),
                "latest_release_tag": str(item.get("latest_release_tag") or ""),
            }

        history = self._update_history_items()
        history.append(
            {
                "checked_at": float(payload.get("checked_at") or time.time()),
                "launcher": _compact(payload.get("launcher")),
                "kernel": _compact(payload.get("kernel")),
                "has_update": bool(payload.get("has_update", False)),
            }
        )
        try:
            limit = int(self.cfg.get("github_update_history_limit", 120) or 120)
        except Exception:
            limit = 120
        limit = max(20, min(1000, limit))
        if len(history) > limit:
            history = history[-limit:]
        self.cfg["github_update_check_history"] = history
        return history

    def _detect_version_changes(self, result):
        payload = result if isinstance(result, dict) else {}
        changes = []
        for key, seen_key in (
            ("launcher", "github_last_seen_launcher_remote_sha"),
            ("kernel", "github_last_seen_kernel_remote_sha"),
        ):
            row = payload.get(key) if isinstance(payload.get(key), dict) else {}
            new_sha = str(row.get("remote_sha") or "").strip()
            old_sha = str(self.cfg.get(seen_key) or "").strip()
            if new_sha and old_sha and old_sha != new_sha:
                changes.append(
                    {
                        "key": key,
                        "name": str(row.get("name") or key),
                        "old_sha": old_sha,
                        "new_sha": new_sha,
                    }
                )
            if new_sha:
                self.cfg[seen_key] = new_sha
        return changes

    def _format_version_changes_text(self, changes):
        rows = changes if isinstance(changes, list) else []
        if not rows:
            return ""
        lines = ["检测到远端版本变动："]
        for item in rows:
            lines.append(
                f"- {str(item.get('name') or '组件')}："
                f"{self._short_sha(item.get('old_sha'))} -> {self._short_sha(item.get('new_sha'))}"
            )
        return "\n".join(lines)

    def _update_history_brief_text(self, *, limit: int = 3):
        history = self._update_history_items()
        if not history:
            return "历史记录：暂无。"
        top = max(1, min(6, int(limit or 3)))
        tail = history[-top:]
        lines = [f"历史记录：已累计 {len(history)} 次检查（最近 {len(tail)} 次）"]
        for item in reversed(tail):
            ts = self._usage_time_label(item.get("checked_at"))
            launcher = item.get("launcher") if isinstance(item.get("launcher"), dict) else {}
            kernel = item.get("kernel") if isinstance(item.get("kernel"), dict) else {}
            lines.append(
                f"- {ts} | 启动器 {launcher.get('status', 'unknown')} {self._short_sha(launcher.get('remote_sha'))} | "
                f"内核 {kernel.get('status', 'unknown')} {self._short_sha(kernel.get('remote_sha'))}"
            )
        return "\n".join(lines)

    def _refresh_about_update_widgets(self):
        running = bool(getattr(self, "_update_check_running", False))
        status_label = getattr(self, "settings_about_update_status", None)
        if status_label is not None:
            if running:
                status_label.setText("正在检测 GitHub 更新，请稍候…")
            else:
                last = getattr(self, "_last_update_check_result", None)
                if isinstance(last, dict):
                    status_label.setText(
                        self._update_result_summary(last)
                        + "\n\n"
                        + self._update_history_brief_text(limit=3)
                    )
                else:
                    status_label.setText(
                        "尚未检查。支持手动检测；也可勾选开机自动检测。\n\n"
                        + self._update_history_brief_text(limit=3)
                    )
        btn = getattr(self, "settings_about_check_updates_btn", None)
        if btn is not None:
            btn.setEnabled(not running)
            btn.setText("正在检测…" if running else "立即检测 GitHub 更新")

    def _on_toggle_update_auto_check(self, checked):
        self.cfg["auto_check_github_updates"] = bool(checked)
        lz.save_config(self.cfg)
        self._set_status("已更新“启动时自动检查更新”设置。")

    def _finish_update_check(self, result, *, manual: bool):
        self._update_check_running = False
        self._last_update_check_result = result if isinstance(result, dict) else {}
        changes = self._detect_version_changes(result)
        history = self._append_update_history(result)
        self.cfg["last_github_update_check_at"] = float((result or {}).get("checked_at") or time.time())
        lz.save_config(self.cfg)
        self._refresh_about_update_widgets()

        summary = self._update_result_summary(result)
        changes_text = self._format_version_changes_text(changes)
        record_text = f"本次检查已记录，累计 {len(history)} 次。"
        if manual:
            text = f"{summary}\n\n{record_text}"
            if changes_text:
                text = f"{summary}\n\n{changes_text}\n\n{record_text}"
            QMessageBox.information(self, "GitHub 更新检测", text)
            self._set_status("已完成 GitHub 更新检测，并记录本次结果。")
            return

        if changes_text:
            QMessageBox.information(
                self,
                "检测到版本变动",
                f"{summary}\n\n{changes_text}\n\n{record_text}",
            )
        self._set_status("已自动完成 GitHub 更新检测，并记录本次结果。")

    def _start_update_check(self, *, manual: bool):
        if bool(getattr(self, "_update_check_running", False)):
            if manual:
                QMessageBox.information(self, "请稍候", "正在进行更新检测，请等待当前任务完成。")
            return
        self._update_check_running = True
        self._refresh_about_update_widgets()
        self._set_status("正在检查 GitHub 更新…")

        result_holder = {"result": None}

        def worker():
            try:
                result_holder["result"] = self._perform_update_check()
            except Exception as e:
                result_holder["result"] = {
                    "checked_at": time.time(),
                    "launcher": {"name": "启动器", "status": "error", "message": str(e)},
                    "kernel": {"name": "agant 内核（GenericAgent）", "status": "error", "message": "更新检查中断"},
                    "has_update": False,
                }
        thread = threading.Thread(target=worker, name="launcher-update-check", daemon=True)
        thread.start()

        def poll():
            if thread.is_alive():
                QTimer.singleShot(120, poll)
                return
            self._finish_update_check(result_holder.get("result") or {}, manual=bool(manual))

        QTimer.singleShot(120, poll)

    def _schedule_startup_update_check(self):
        if bool(getattr(self, "_startup_update_check_scheduled", False)):
            return
        self._startup_update_check_scheduled = True
        enabled = bool(self.cfg.get("auto_check_github_updates", True))
        if not enabled:
            return
        QTimer.singleShot(1400, lambda: self._start_update_check(manual=False))

    def _reload_personal_preferences(self):
        sound_box = getattr(self, "settings_disable_reply_sound", None)
        message_box = getattr(self, "settings_disable_reply_message", None)
        if sound_box is not None:
            sound_box.setChecked(bool(self.cfg.get("disable_reply_sound", False)))
        if message_box is not None:
            message_box.setChecked(bool(self.cfg.get("disable_reply_message", False)))

    def _save_personal_preferences(self):
        sound_box = getattr(self, "settings_disable_reply_sound", None)
        message_box = getattr(self, "settings_disable_reply_message", None)
        self.cfg["disable_reply_sound"] = bool(sound_box is not None and sound_box.isChecked())
        self.cfg["disable_reply_message"] = bool(message_box is not None and message_box.isChecked())
        lz.save_config(self.cfg)
        QMessageBox.information(self, "已保存", "回复提醒设置已保存。")

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
        self._reload_personal_preferences()
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

    def _usage_num(self, value):
        try:
            return f"{int(value or 0):,}"
        except Exception:
            return "0"

    def _usage_time_label(self, ts):
        try:
            value = float(ts or 0)
        except Exception:
            value = 0.0
        if value <= 0:
            return "暂无"
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")

    def _usage_model_label(self, model_name):
        text = str(model_name or "").strip()
        return text or "(未记录模型)"

    def _usage_source_label(self, source):
        source = str(source or "estimate").strip().lower() or "estimate"
        if source == "provider":
            return "真实 usage"
        return "估算"

    def _usage_cache_label(self, value):
        try:
            normalized = int(value or 0)
        except Exception:
            normalized = 0
        if normalized <= 0:
            return "数据不足"
        return self._usage_num(normalized)

    def _usage_add_line(self, box, text, *, object_name="softTextSmall", selectable=False):
        line = QLabel(text)
        line.setWordWrap(True)
        line.setObjectName(object_name)
        if selectable:
            line.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.addWidget(line)
        return line

    def _usage_detail_card(self, title, desc="", *, lines=None):
        card = self._panel_card()
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(8)
        head = QLabel(title)
        head.setObjectName("cardTitle")
        box.addWidget(head)
        if desc:
            self._usage_add_line(box, desc, object_name="cardDesc")
        for item in lines or []:
            self._usage_add_line(
                box,
                item.get("text", ""),
                object_name=item.get("object_name", "softTextSmall"),
                selectable=bool(item.get("selectable", False)),
            )
        return card

    def _usage_metric_card(self, title, value, detail="", *, accent=False):
        card = self._panel_card()
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(4)
        head = QLabel(title)
        head.setObjectName("mutedText")
        box.addWidget(head)
        body = QLabel(str(value or "0"))
        body.setStyleSheet(
            f"color: {C['accent_text'] if accent else C['text']}; "
            f"font-size: {F['font_title']}px; font-weight: 700; background: transparent;"
        )
        box.addWidget(body)
        if detail:
            self._usage_add_line(box, detail, object_name="softTextSmall")
        box.addStretch(1)
        return card

    def _usage_table_row(self, values, *, stretches=None, header=False, selectable_cols=None):
        frame = QFrame()
        frame.setObjectName("cardInset")
        row = QHBoxLayout(frame)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(10)
        stretches = list(stretches or [1] * len(values))
        selectable_cols = set(selectable_cols or [])
        for idx, text in enumerate(values):
            label = QLabel(str(text or ""))
            label.setWordWrap(True)
            label.setObjectName("mutedText" if header else ("bodyText" if idx == 0 else "softTextSmall"))
            if idx in selectable_cols:
                label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row.addWidget(label, stretches[idx] if idx < len(stretches) else 1)
        return frame

    def _usage_table_card(self, title, desc, headers, rows, *, stretches=None, empty_text="暂无数据", selectable_cols=None):
        card = self._panel_card()
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(8)
        head = QLabel(title)
        head.setObjectName("cardTitle")
        box.addWidget(head)
        if desc:
            self._usage_add_line(box, desc, object_name="cardDesc")
        box.addWidget(self._usage_table_row(headers, stretches=stretches, header=True))
        if rows:
            for row_values in rows:
                box.addWidget(
                    self._usage_table_row(
                        row_values,
                        stretches=stretches,
                        header=False,
                        selectable_cols=selectable_cols,
                    )
                )
        else:
            self._usage_add_line(box, empty_text, object_name="mutedText")
        return card

    def _load_langfuse_status(self):
        status = {
            "configured": False,
            "enabled": False,
            "plugin_exists": False,
            "llmcore_hook": False,
            "host": "",
            "public_key_set": False,
            "secret_key_set": False,
            "summary": "未配置 Langfuse。",
            "notes": [],
            "parse_error": "",
        }
        if not lz.is_valid_agent_dir(self.agent_dir):
            status["summary"] = "没有可用的 GenericAgent 目录，无法检查 Langfuse。"
            return status

        plugin_fp = os.path.join(self.agent_dir, "plugins", "langfuse_tracing.py")
        llmcore_fp = os.path.join(self.agent_dir, "llmcore.py")
        mykey_fp = os.path.join(self.agent_dir, "mykey.py")
        status["plugin_exists"] = os.path.isfile(plugin_fp)

        if os.path.isfile(llmcore_fp):
            try:
                with open(llmcore_fp, "r", encoding="utf-8") as f:
                    llmcore_src = f.read()
                status["llmcore_hook"] = "from plugins import langfuse_tracing" in llmcore_src
            except Exception:
                status["llmcore_hook"] = False

        parsed = lz.parse_mykey_py(mykey_fp)
        if parsed.get("error"):
            status["parse_error"] = str(parsed.get("error"))
        cfg = (parsed.get("extras") or {}).get("langfuse_config")
        if isinstance(cfg, dict):
            status["configured"] = True
            status["host"] = str(cfg.get("host") or self._LANGFUSE_DEFAULT_HOST).strip()
            status["public_key_set"] = bool(str(cfg.get("public_key") or "").strip())
            status["secret_key_set"] = bool(str(cfg.get("secret_key") or "").strip())
            status["config"] = {
                "public_key": str(cfg.get("public_key") or "").strip(),
                "secret_key": str(cfg.get("secret_key") or "").strip(),
                "host": str(cfg.get("host") or self._LANGFUSE_DEFAULT_HOST).strip(),
            }
        else:
            status["config"] = {
                "public_key": "",
                "secret_key": "",
                "host": self._LANGFUSE_DEFAULT_HOST,
            }

        if status["configured"] and status["plugin_exists"] and status["llmcore_hook"]:
            if status["public_key_set"] and status["secret_key_set"]:
                status["enabled"] = True
                status["summary"] = "已接好 Langfuse 追踪链路。GenericAgent 运行时读取 mykey.py 后，会在 llmcore 中按需 import 插件并上报 trace。"
            else:
                status["summary"] = "检测到 langfuse_config，但 key 还没填完整。"
        elif status["configured"]:
            status["summary"] = "mykey.py 里有 Langfuse 配置，但当前上游代码链路不完整，启动后不一定会真正上报。"

        if status["configured"]:
            status["notes"].append(
                f"配置状态：host={'已设置' if status['host'] else '默认云端'}，public_key={'已填' if status['public_key_set'] else '缺失'}，secret_key={'已填' if status['secret_key_set'] else '缺失'}"
            )
            if status["host"]:
                status["notes"].append(f"目标地址：{status['host']}")
        else:
            status["notes"].append("mykey.py 里还没有 langfuse_config；目前界面只能展示本地日志。")
        status["notes"].append(f"插件文件：{'存在' if status['plugin_exists'] else '缺失'}")
        status["notes"].append(f"llmcore 挂钩：{'已发现' if status['llmcore_hook'] else '未发现'}")
        if status["parse_error"]:
            status["notes"].append(f"mykey.py 解析状态：{status['parse_error']}")
        return status

    def _langfuse_input_row(self, label_text, editor, *, secret=False):
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        label = QLabel(label_text)
        label.setMinimumWidth(92)
        row.addWidget(label, 0)
        if secret:
            editor.setEchoMode(QLineEdit.Password)
        self._fluent_input(editor)
        row.addWidget(editor, 1)
        if secret:
            toggle = QPushButton("显示")
            toggle.setCheckable(True)
            toggle.setStyleSheet(self._action_button_style())

            def sync_secret_visible(checked, edit=editor, btn=toggle):
                edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
                btn.setText("隐藏" if checked else "显示")

            toggle.toggled.connect(sync_secret_visible)
            row.addWidget(toggle, 0)
        return host

    def _current_langfuse_form_data(self):
        public_edit = getattr(self, "settings_langfuse_public_key", None)
        secret_edit = getattr(self, "settings_langfuse_secret_key", None)
        host_edit = getattr(self, "settings_langfuse_host", None)
        return {
            "public_key": str(public_edit.text() if public_edit is not None else "").strip(),
            "secret_key": str(secret_edit.text() if secret_edit is not None else "").strip(),
            "host": str(host_edit.text() if host_edit is not None else "").strip() or self._LANGFUSE_DEFAULT_HOST,
        }

    def _fill_langfuse_default_host(self):
        host_edit = getattr(self, "settings_langfuse_host", None)
        if host_edit is not None and not str(host_edit.text() or "").strip():
            host_edit.setText(self._LANGFUSE_DEFAULT_HOST)

    def _write_langfuse_config(self, config, *, restart=False, remove=False):
        if not lz.is_valid_agent_dir(self.agent_dir):
            QMessageBox.warning(self, "目录无效", "请先选择有效的 GenericAgent 目录。")
            return
        py_path, parsed = self._load_mykey_source()
        if not py_path:
            QMessageBox.warning(self, "无法保存", "还没有可用的 mykey.py。")
            return
        extras = dict(parsed.get("extras") or {})
        if remove:
            extras.pop("langfuse_config", None)
        else:
            extras["langfuse_config"] = {
                "public_key": str(config.get("public_key") or "").strip(),
                "secret_key": str(config.get("secret_key") or "").strip(),
                "host": str(config.get("host") or self._LANGFUSE_DEFAULT_HOST).strip() or self._LANGFUSE_DEFAULT_HOST,
            }
        try:
            txt = lz.serialize_mykey_py(
                configs=[{"var": c["var"], "kind": c["kind"], "data": dict(c["data"])} for c in (parsed.get("configs") or [])],
                extras=extras,
                passthrough=list(parsed.get("passthrough") or []),
            )
            with open(py_path, "w", encoding="utf-8") as f:
                f.write(txt)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return

        restarted = self._restart_running_channels(show_errors=False)
        if restart:
            self._restart_bridge()
            msg = "已写入 Langfuse 配置，并已重启聊天内核。"
        elif remove:
            msg = "已清除 Langfuse 配置。聊天内核需重启后才会彻底停用追踪。"
        else:
            msg = "已写入 Langfuse 配置。聊天内核需重启后才会开始上报追踪。"
        if restarted:
            msg += f"\n已自动重启 {restarted} 个由启动器托管的通讯渠道。"
        QMessageBox.information(self, "已保存", msg)
        self._reload_api_editor_state()
        self._reload_channels_editor_state()
        self._reload_usage_panel()

    def _save_langfuse_config(self, restart=False):
        data = self._current_langfuse_form_data()
        if not data["public_key"] or not data["secret_key"]:
            QMessageBox.warning(self, "配置不完整", "请至少填写 Langfuse 的 public_key 和 secret_key。")
            return
        self._write_langfuse_config(data, restart=restart, remove=False)

    def _clear_langfuse_config(self):
        self._write_langfuse_config({}, restart=False, remove=True)

    def _collect_usage_stats(self, lookback_days=7):
        channel_stats = {}
        day_stats = {}
        model_stats = {}
        source_stats = {}
        session_stats = {}
        timeline = []
        warnings = []
        now = time.time()
        today_key = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        lookback_cutoff = now - max(1, int(lookback_days)) * 86400

        def make_total():
            return {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "api_calls": 0,
                "cached_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "sources": set(),
            }

        today_total = make_total()
        recent_total = make_total()
        all_total = make_total()
        activity = {
            "session_count": 0,
            "sessions_with_events": 0,
            "provider_sessions": 0,
            "estimate_only_sessions": 0,
            "event_count": 0,
            "provider_events": 0,
            "estimate_events": 0,
            "api_calls": 0,
            "cached_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "last_event_ts": 0,
            "models": set(),
            "channels": set(),
            "missing_model_events": 0,
        }

        if not lz.is_valid_agent_dir(self.agent_dir):
            for item in (today_total, recent_total, all_total):
                item["mode"] = lz._usage_mode_from_sources(item.get("sources"))
            return {
                "today": today_total,
                "recent": recent_total,
                "all": all_total,
                "channels": [],
                "days": [],
                "models": [],
                "sources": [],
                "sessions": [],
                "timeline": [],
                "warnings": warnings,
                "activity": activity,
            }

        for meta in lz.list_sessions(self.agent_dir):
            try:
                session = lz.load_session(self.agent_dir, meta["id"])
            except Exception:
                session = None
            if not session:
                continue

            activity["session_count"] += 1
            before = json.dumps(session.get("token_usage") or {}, ensure_ascii=False, sort_keys=True)
            self._ensure_session_usage_metadata(session)
            after = json.dumps(session.get("token_usage") or {}, ensure_ascii=False, sort_keys=True)
            if before != after:
                lz.save_session(self.agent_dir, session)

            usage = session.get("token_usage") or {}
            channel_id = str(session.get("channel_id") or usage.get("channel_id") or "launcher").strip().lower()
            channel_label = lz._usage_channel_label(channel_id)
            channel_row = channel_stats.setdefault(
                channel_id,
                {
                    "channel_id": channel_id,
                    "label": channel_label,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "turns": 0,
                    "sessions": set(),
                    "last_active": 0,
                    "api_calls": 0,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "sources": set(),
                },
            )
            channel_row["sessions"].add(session.get("id"))
            channel_row["last_active"] = max(channel_row["last_active"], float(session.get("updated_at", 0) or 0))

            session_row = session_stats.setdefault(
                str(session.get("id") or meta.get("id") or ""),
                {
                    "session_id": str(session.get("id") or meta.get("id") or ""),
                    "title": str(session.get("title") or "(未命名会话)"),
                    "channel_id": channel_id,
                    "channel_label": channel_label,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "turns": 0,
                    "api_calls": 0,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "last_active": float(session.get("updated_at", 0) or 0),
                    "last_model": self._usage_model_label(usage.get("last_model")),
                    "sources": set(),
                    "pinned": bool(session.get("pinned", False)),
                },
            )

            events = list(usage.get("events") or [])
            if not events:
                events = lz._fallback_token_events_from_bubbles(
                    session.get("bubbles") or [],
                    base_ts=session.get("updated_at") or session.get("created_at") or now,
                    channel_id=channel_id,
                    model_name=usage.get("last_model") or "",
                )

            if events:
                activity["sessions_with_events"] += 1

            for ev in events:
                inp = int(ev.get("input_tokens", 0) or 0)
                out = int(ev.get("output_tokens", 0) or 0)
                total = int(ev.get("total_tokens", inp + out) or (inp + out))
                api_calls = int(ev.get("api_calls", 0) or 0)
                cached_tokens = int(ev.get("cached_tokens", 0) or 0)
                cache_creation = int(ev.get("cache_creation_input_tokens", 0) or 0)
                cache_read = int(ev.get("cache_read_input_tokens", 0) or 0)
                try:
                    ts = float(ev.get("ts", session.get("updated_at", now)) or now)
                except Exception:
                    ts = now
                day_key = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                source = str(ev.get("usage_source") or "estimate").strip().lower() or "estimate"
                model_name = self._usage_model_label(ev.get("model") or session_row.get("last_model") or usage.get("last_model"))
                if model_name == "(未记录模型)":
                    activity["missing_model_events"] += 1

                row = day_stats.setdefault(
                    day_key,
                    {
                        "date": day_key,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                        "turns": 0,
                        "channels": {},
                        "api_calls": 0,
                        "sources": set(),
                    },
                )
                row["input_tokens"] += inp
                row["output_tokens"] += out
                row["total_tokens"] += total
                row["turns"] += 1 if inp > 0 else 0
                row["channels"][channel_id] = row["channels"].get(channel_id, 0) + total
                row["api_calls"] += api_calls
                row["sources"].add(source)

                model_row = model_stats.setdefault(
                    model_name,
                    {
                        "model": model_name,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                        "turns": 0,
                        "sessions": set(),
                        "last_active": 0,
                        "api_calls": 0,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                        "sources": set(),
                    },
                )
                source_row = source_stats.setdefault(
                    source,
                    {
                        "source": source,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                        "turns": 0,
                        "events": 0,
                        "api_calls": 0,
                        "sessions": set(),
                    },
                )

                channel_row["input_tokens"] += inp
                channel_row["output_tokens"] += out
                channel_row["total_tokens"] += total
                channel_row["turns"] += 1 if inp > 0 else 0
                channel_row["api_calls"] += api_calls
                channel_row["cache_read_input_tokens"] += cache_read
                channel_row["cache_creation_input_tokens"] += cache_creation
                channel_row["sources"].add(source)

                model_row["input_tokens"] += inp
                model_row["output_tokens"] += out
                model_row["total_tokens"] += total
                model_row["turns"] += 1 if inp > 0 else 0
                model_row["sessions"].add(session_row["session_id"])
                model_row["last_active"] = max(model_row["last_active"], ts)
                model_row["api_calls"] += api_calls
                model_row["cache_read_input_tokens"] += cache_read
                model_row["cache_creation_input_tokens"] += cache_creation
                model_row["sources"].add(source)

                source_row["input_tokens"] += inp
                source_row["output_tokens"] += out
                source_row["total_tokens"] += total
                source_row["turns"] += 1 if inp > 0 else 0
                source_row["events"] += 1
                source_row["api_calls"] += api_calls
                source_row["sessions"].add(session_row["session_id"])

                session_row["input_tokens"] += inp
                session_row["output_tokens"] += out
                session_row["total_tokens"] += total
                session_row["turns"] += 1 if inp > 0 else 0
                session_row["api_calls"] += api_calls
                session_row["cache_read_input_tokens"] += cache_read
                session_row["cache_creation_input_tokens"] += cache_creation
                session_row["last_active"] = max(session_row["last_active"], ts)
                session_row["last_model"] = model_name
                session_row["sources"].add(source)

                activity["event_count"] += 1
                activity["api_calls"] += api_calls
                activity["cached_tokens"] += cached_tokens
                activity["cache_creation_input_tokens"] += cache_creation
                activity["cache_read_input_tokens"] += cache_read
                activity["last_event_ts"] = max(activity["last_event_ts"], ts)
                activity["models"].add(model_name)
                activity["channels"].add(channel_id)
                if source == "provider":
                    activity["provider_events"] += 1
                else:
                    activity["estimate_events"] += 1

                for total_bucket in (all_total, today_total if day_key == today_key else None, recent_total if ts >= lookback_cutoff else None):
                    if total_bucket is None:
                        continue
                    total_bucket["input_tokens"] += inp
                    total_bucket["output_tokens"] += out
                    total_bucket["total_tokens"] += total
                    total_bucket["api_calls"] += api_calls
                    total_bucket["cached_tokens"] += cached_tokens
                    total_bucket["cache_creation_input_tokens"] += cache_creation
                    total_bucket["cache_read_input_tokens"] += cache_read
                    total_bucket["sources"].add(source)

                timeline.append(
                    {
                        "ts": ts,
                        "channel_label": channel_label,
                        "session_title": session_row["title"],
                        "model": model_name,
                        "source": source,
                        "total_tokens": total,
                        "input_tokens": inp,
                        "output_tokens": out,
                        "api_calls": api_calls,
                    }
                )

        sessions = []
        for row in session_stats.values():
            row["mode"] = lz._usage_mode_from_sources(row.get("sources"))
            if row["sources"] == {"provider"}:
                activity["provider_sessions"] += 1
            elif row["sources"] == {"estimate"} and row["total_tokens"] > 0:
                activity["estimate_only_sessions"] += 1
            sessions.append(row)

        channels = sorted(
            [{**row, "sessions": len(row["sessions"]), "mode": lz._usage_mode_from_sources(row.get("sources"))} for row in channel_stats.values()],
            key=lambda x: (x["total_tokens"], x["last_active"]),
            reverse=True,
        )
        days = sorted(day_stats.values(), key=lambda x: x["date"], reverse=True)
        models = sorted(
            [{**row, "sessions": len(row["sessions"]), "mode": lz._usage_mode_from_sources(row.get("sources"))} for row in model_stats.values()],
            key=lambda x: (x["total_tokens"], x["last_active"]),
            reverse=True,
        )
        sources = sorted(
            [{**row, "sessions": len(row["sessions"])} for row in source_stats.values()],
            key=lambda x: (x["total_tokens"], x["events"]),
            reverse=True,
        )
        sessions.sort(key=lambda x: (x["total_tokens"], x["last_active"]), reverse=True)
        timeline.sort(key=lambda x: x["ts"], reverse=True)

        for item in (today_total, recent_total, all_total):
            item["mode"] = lz._usage_mode_from_sources(item.get("sources"))
        for row in days:
            row["mode"] = lz._usage_mode_from_sources(row.get("sources"))

        if activity["event_count"] <= 0:
            warnings.append("当前还没有可分析的 usage 事件，通常说明还没产生完整会话或日志都来自空白新会话。")
        if activity["event_count"] > 0 and activity["provider_events"] <= 0:
            warnings.append("目前所有 token 统计都来自本地估算，说明当前渠道/模型还没有把 provider usage 回传给启动器。")
        if activity["missing_model_events"] > 0:
            warnings.append(f"有 {activity['missing_model_events']} 条日志没有记录模型名，模型分布会出现“未记录模型”。")
        if activity["estimate_only_sessions"] > 0:
            warnings.append(f"当前有 {activity['estimate_only_sessions']} 个会话仍是纯估算统计。")

        return {
            "today": today_total,
            "recent": recent_total,
            "all": all_total,
            "channels": channels,
            "days": days[: max(1, int(lookback_days))],
            "models": models,
            "sources": sources,
            "sessions": sessions[:10],
            "timeline": timeline[:12],
            "warnings": warnings,
            "activity": activity,
        }

    def _reload_usage_panel(self):
        if not hasattr(self, "settings_usage_notice"):
            return
        self._clear_layout(self.settings_usage_list_layout)
        if not lz.is_valid_agent_dir(self.agent_dir):
            self.settings_usage_notice.setText("请先选择有效的 GenericAgent 目录。")
            return

        stats = self._collect_usage_stats(lookback_days=7)
        langfuse = self._load_langfuse_status()
        self.settings_usage_notice.setText(
            "本页优先展示本地 usage 摘要、渠道分布和最近活动。旧会话或不返回 usage 的渠道，仍可能只能显示估算。"
        )

        hero_grid = QGridLayout()
        hero_grid.setSpacing(10)
        hero_cards = [
            self._usage_metric_card(
                "今天",
                self._usage_num(stats["today"]["total_tokens"]),
                f"{lz._usage_mode_label(stats['today'].get('mode'))}  ·  入 {self._usage_num(stats['today']['input_tokens'])} / 出 {self._usage_num(stats['today']['output_tokens'])} / 调用 {self._usage_num(stats['today']['api_calls'])}",
                accent=True,
            ),
            self._usage_metric_card(
                "近 7 天",
                self._usage_num(stats["recent"]["total_tokens"]),
                f"{lz._usage_mode_label(stats['recent'].get('mode'))}  ·  入 {self._usage_num(stats['recent']['input_tokens'])} / 出 {self._usage_num(stats['recent']['output_tokens'])}",
            ),
            self._usage_metric_card(
                "累计",
                self._usage_num(stats["all"]["total_tokens"]),
                f"缓存读取 {self._usage_cache_label(stats['all']['cache_read_input_tokens'])}  ·  API 调用 {self._usage_num(stats['activity']['api_calls'])}",
            ),
            self._usage_metric_card(
                "活跃概览",
                self._usage_num(stats["activity"]["sessions_with_events"]),
                f"有日志会话 / 全部 {self._usage_num(stats['activity']['session_count'])}  ·  最近活动 {self._usage_time_label(stats['activity']['last_event_ts'])}",
            ),
        ]
        for idx, card in enumerate(hero_cards):
            hero_grid.addWidget(card, 0, idx)
        self.settings_usage_list_layout.addLayout(hero_grid)

        stats_grid = QGridLayout()
        stats_grid.setSpacing(10)
        stats_grid.addWidget(
            self._usage_metric_card(
                "模型数",
                self._usage_num(len(stats["activity"]["models"])),
                "最近 7 天内出现过的模型",
            ),
            0,
            0,
        )
        stats_grid.addWidget(
            self._usage_metric_card(
                "渠道数",
                self._usage_num(len(stats["activity"]["channels"])),
                "出现过 usage 的聊天渠道",
            ),
            0,
            1,
        )
        stats_grid.addWidget(
            self._usage_metric_card(
                "真实 usage 事件",
                self._usage_num(stats["activity"]["provider_events"]),
                "provider 返回的原始 usage",
            ),
            0,
            2,
        )
        stats_grid.addWidget(
            self._usage_metric_card(
                "估算事件",
                self._usage_num(stats["activity"]["estimate_events"]),
                "按字符估算得到的 usage",
            ),
            0,
            3,
        )
        self.settings_usage_list_layout.addLayout(stats_grid)

        warnings = stats.get("warnings") or []
        if warnings:
            warning_card = self._panel_card()
            warning_box = QVBoxLayout(warning_card)
            warning_box.setContentsMargins(14, 12, 14, 12)
            warning_box.setSpacing(8)
            title = QLabel("提醒")
            title.setObjectName("cardTitle")
            warning_box.addWidget(title)
            for warn in warnings:
                row = QFrame()
                row.setObjectName("cardInset")
                inner = QHBoxLayout(row)
                inner.setContentsMargins(12, 10, 12, 10)
                inner.setSpacing(8)
                mark = QLabel("•")
                mark.setStyleSheet(f"color: {C['warning']}; font-size: 18px; font-weight: 700; background: transparent;")
                inner.addWidget(mark, 0, Qt.AlignTop)
                text = QLabel(warn)
                text.setWordWrap(True)
                text.setObjectName("softTextSmall")
                inner.addWidget(text, 1)
                warning_box.addWidget(row)
            self.settings_usage_list_layout.addWidget(warning_card)

        source_rows = [
            [
                self._usage_source_label(row["source"]),
                self._usage_num(row["total_tokens"]),
                self._usage_num(row["events"]),
                self._usage_num(row["sessions"]),
            ]
            for row in (stats.get("sources") or [])
        ]
        quality_card = self._usage_table_card(
            "日志来源",
            "优先看这里，能快速分辨当前数据到底有多少是真实 provider usage，多少还是本地估算。",
            ["来源", "总 token", "事件数", "会话数"],
            source_rows,
            stretches=[3, 2, 2, 2],
            empty_text="暂无来源统计。",
        )
        quality_extra = QFrame()
        quality_extra.setObjectName("cardInset")
        quality_row = QHBoxLayout(quality_extra)
        quality_row.setContentsMargins(12, 10, 12, 10)
        quality_row.setSpacing(12)
        for text in (
            f"API 调用 {self._usage_num(stats['activity']['api_calls'])}",
            f"缓存读取 {self._usage_cache_label(stats['activity']['cache_read_input_tokens'])}",
            f"缓存写入 {self._usage_cache_label(stats['activity']['cache_creation_input_tokens'])}",
        ):
            label = QLabel(text)
            label.setObjectName("softTextSmall")
            quality_row.addWidget(label, 1)
        quality_layout = quality_card.layout()
        if quality_layout is not None:
            quality_layout.addWidget(quality_extra)
        self.settings_usage_list_layout.addWidget(quality_card)

        mid_grid = QGridLayout()
        mid_grid.setSpacing(10)
        mid_grid.addWidget(
            self._usage_table_card(
                "按渠道",
                "看问题集中在哪个入口，主聊天区和外部通讯前端能一眼区分。",
                ["渠道", "总 token", "轮次", "会话", "最近活动"],
                [
                    [
                        row["label"],
                        self._usage_num(row["total_tokens"]),
                        self._usage_num(row["turns"]),
                        self._usage_num(row["sessions"]),
                        self._usage_time_label(row["last_active"]),
                    ]
                    for row in (stats.get("channels") or [])[:8]
                ],
                stretches=[3, 2, 2, 2, 3],
                empty_text="暂无可统计的渠道日志。",
            ),
            0,
            0,
        )
        mid_grid.addWidget(
            self._usage_table_card(
                "按模型",
                "模型维度更适合看消耗结构，尤其能看出是不是某一个模型异常偏高。",
                ["模型", "总 token", "会话", "调用", "最近活动"],
                [
                    [
                        row["model"],
                        self._usage_num(row["total_tokens"]),
                        self._usage_num(row["sessions"]),
                        self._usage_num(row["api_calls"]),
                        self._usage_time_label(row["last_active"]),
                    ]
                    for row in (stats.get("models") or [])[:8]
                ],
                stretches=[4, 2, 2, 2, 3],
                empty_text="暂无可统计的模型日志。",
            ),
            0,
            1,
        )
        self.settings_usage_list_layout.addLayout(mid_grid)

        lower_grid = QGridLayout()
        lower_grid.setSpacing(10)
        lower_grid.addWidget(
            self._usage_table_card(
                "最近活动",
                "按时间倒序展示，适合快速复盘最近几次请求发生在哪里、用了什么模型。",
                ["时间", "渠道", "会话", "模型", "token"],
                [
                    [
                        self._usage_time_label(row["ts"]),
                        row["channel_label"],
                        row["session_title"],
                        row["model"],
                        self._usage_num(row["total_tokens"]),
                    ]
                    for row in (stats.get("timeline") or [])[:8]
                ],
                stretches=[2, 2, 4, 4, 2],
                empty_text="最近还没有 usage 事件。",
            ),
            0,
            0,
        )
        lower_grid.addWidget(
            self._usage_table_card(
                "高消耗会话",
                "挑出 token 累积最高的会话，便于快速定位最值得排查的对象。",
                ["会话", "渠道", "总 token", "最近模型", "最近活动"],
                [
                    [
                        row["title"] + ("  · 已收藏" if row.get("pinned") else ""),
                        row["channel_label"],
                        self._usage_num(row["total_tokens"]),
                        row["last_model"],
                        self._usage_time_label(row["last_active"]),
                    ]
                    for row in (stats.get("sessions") or [])[:8]
                ],
                stretches=[4, 2, 2, 3, 3],
                empty_text="暂无可统计的会话。",
            ),
            0,
            1,
        )
        self.settings_usage_list_layout.addLayout(lower_grid)

        day_rows = []
        for row in (stats.get("days") or []):
            top_channel = ""
            top_pairs = sorted(row.get("channels", {}).items(), key=lambda kv: kv[1], reverse=True)[:2]
            if top_pairs:
                top_channel = " / ".join(f"{lz._usage_channel_label(cid)} {self._usage_num(total)}" for cid, total in top_pairs)
            day_rows.append(
                [
                    row["date"],
                    self._usage_num(row["total_tokens"]),
                    self._usage_num(row["turns"]),
                    self._usage_num(row["api_calls"]),
                    top_channel or "无渠道细分",
                ]
            )
        self.settings_usage_list_layout.addWidget(
            self._usage_table_card(
                "最近几天",
                "按天回看整体波动，适合判断今天是不是异常、哪个渠道最近突然抬升。",
                ["日期", "总 token", "轮次", "调用", "主要渠道"],
                day_rows,
                stretches=[2, 2, 2, 2, 5],
                empty_text="最近几天没有可用统计。",
            )
        )

        advanced_card = self._panel_card()
        advanced_box = QVBoxLayout(advanced_card)
        advanced_box.setContentsMargins(14, 12, 14, 12)
        advanced_box.setSpacing(8)
        advanced_toggle = QPushButton()
        advanced_toggle.setCheckable(True)
        advanced_toggle.setChecked(bool(langfuse.get("configured")))
        advanced_toggle.setStyleSheet(self._action_button_style(kind="subtle"))
        advanced_box.addWidget(advanced_toggle)

        advanced_wrap = QWidget()
        advanced_wrap_box = QVBoxLayout(advanced_wrap)
        advanced_wrap_box.setContentsMargins(0, 0, 0, 0)
        advanced_wrap_box.setSpacing(8)
        advanced_box.addWidget(advanced_wrap)

        def sync_advanced(flag=None):
            expanded = bool(advanced_toggle.isChecked() if flag is None else flag)
            advanced_toggle.setText(("▾ " if expanded else "▸ ") + "高级模式 · Langfuse")
            advanced_wrap.setVisible(expanded)

        status_card = QFrame()
        status_card.setObjectName("cardInset")
        status_box = QVBoxLayout(status_card)
        status_box.setContentsMargins(12, 10, 12, 10)
        status_box.setSpacing(6)
        title = QLabel("Langfuse 状态")
        title.setObjectName("bodyText")
        status_box.addWidget(title)
        summary = QLabel(langfuse["summary"])
        summary.setWordWrap(True)
        summary.setObjectName("softTextSmall")
        status_box.addWidget(summary)
        for note in (langfuse.get("notes") or [])[:4]:
            self._usage_add_line(
                status_box,
                note,
                object_name="mutedText",
                selectable=note.startswith("目标地址："),
            )
        advanced_wrap_box.addWidget(status_card)

        form_card = QFrame()
        form_card.setObjectName("cardInset")
        form_box = QVBoxLayout(form_card)
        form_box.setContentsMargins(12, 10, 12, 10)
        form_box.setSpacing(8)
        form_title = QLabel("配置 Langfuse")
        form_title.setObjectName("bodyText")
        form_box.addWidget(form_title)
        form_hint = QLabel("这是开发/观测用途的高级配置，不填也不影响正常聊天。")
        form_hint.setWordWrap(True)
        form_hint.setObjectName("mutedText")
        form_box.addWidget(form_hint)

        self.settings_langfuse_public_key = QLineEdit()
        self.settings_langfuse_public_key.setPlaceholderText("pk-lf-...")
        self.settings_langfuse_public_key.setText(str((langfuse.get("config") or {}).get("public_key") or ""))
        form_box.addWidget(self._langfuse_input_row("Public Key", self.settings_langfuse_public_key))

        self.settings_langfuse_secret_key = QLineEdit()
        self.settings_langfuse_secret_key.setPlaceholderText("sk-lf-...")
        self.settings_langfuse_secret_key.setText(str((langfuse.get("config") or {}).get("secret_key") or ""))
        form_box.addWidget(self._langfuse_input_row("Secret Key", self.settings_langfuse_secret_key, secret=True))

        self.settings_langfuse_host = QLineEdit()
        self.settings_langfuse_host.setPlaceholderText(self._LANGFUSE_DEFAULT_HOST)
        self.settings_langfuse_host.setText(str((langfuse.get("config") or {}).get("host") or self._LANGFUSE_DEFAULT_HOST))
        form_box.addWidget(self._langfuse_input_row("Host", self.settings_langfuse_host))

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        quick_fill_btn = QPushButton("使用官方云端")
        quick_fill_btn.setStyleSheet(self._action_button_style())
        quick_fill_btn.clicked.connect(lambda: self.settings_langfuse_host.setText(self._LANGFUSE_DEFAULT_HOST))
        action_row.addWidget(quick_fill_btn, 0)
        save_btn = QPushButton("保存配置")
        save_btn.setStyleSheet(self._action_button_style())
        save_btn.clicked.connect(lambda: self._save_langfuse_config(restart=False))
        action_row.addWidget(save_btn, 0)
        restart_btn = QPushButton("保存并重启内核")
        restart_btn.setStyleSheet(self._action_button_style(primary=True))
        restart_btn.clicked.connect(lambda: self._save_langfuse_config(restart=True))
        action_row.addWidget(restart_btn, 0)
        clear_btn = QPushButton("清除配置")
        clear_btn.setStyleSheet(self._action_button_style(kind="destructive"))
        clear_btn.clicked.connect(self._clear_langfuse_config)
        clear_btn.setEnabled(bool(langfuse.get("configured")))
        action_row.addWidget(clear_btn, 0)
        action_row.addStretch(1)
        form_box.addLayout(action_row)
        advanced_wrap_box.addWidget(form_card)

        advanced_toggle.toggled.connect(sync_advanced)
        sync_advanced()
        self.settings_usage_list_layout.addWidget(advanced_card)
        self.settings_usage_list_layout.addStretch(1)

    def _reload_about_panel(self):
        if not hasattr(self, "settings_about_list_layout"):
            return
        self._clear_layout(self.settings_about_list_layout)
        if not isinstance(getattr(self, "_last_update_check_result", None), dict):
            history = self._update_history_items()
            if history:
                self._last_update_check_result = history[-1]
        update_card = self._panel_card()
        update_box = QVBoxLayout(update_card)
        update_box.setContentsMargins(14, 12, 14, 12)
        update_box.setSpacing(8)
        update_title = QLabel("更新检测")
        update_title.setObjectName("cardTitle")
        update_box.addWidget(update_title)
        update_desc = QLabel(
            "支持分别检查“启动器仓库”和“agant 内核仓库（GenericAgent）”是否有新提交。"
            "检测会优先直连 GitHub API，失败后自动尝试镜像代理地址（更适合国内网络）。"
        )
        update_desc.setWordWrap(True)
        update_desc.setObjectName("cardDesc")
        update_box.addWidget(update_desc)
        self.settings_about_update_status = QLabel("")
        self.settings_about_update_status.setWordWrap(True)
        self.settings_about_update_status.setObjectName("mutedText")
        self.settings_about_update_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        update_box.addWidget(self.settings_about_update_status)
        self.settings_about_auto_check_updates = QCheckBox("每次启动启动器时自动检测 GitHub 更新")
        self.settings_about_auto_check_updates.setChecked(bool(self.cfg.get("auto_check_github_updates", True)))
        self.settings_about_auto_check_updates.toggled.connect(self._on_toggle_update_auto_check)
        update_box.addWidget(self.settings_about_auto_check_updates)
        update_action_row = QHBoxLayout()
        update_action_row.setSpacing(8)
        self.settings_about_check_updates_btn = QPushButton("立即检测 GitHub 更新")
        self.settings_about_check_updates_btn.setStyleSheet(self._action_button_style(primary=True))
        self.settings_about_check_updates_btn.clicked.connect(lambda: self._start_update_check(manual=True))
        update_action_row.addWidget(self.settings_about_check_updates_btn, 0)
        update_action_row.addStretch(1)
        update_box.addLayout(update_action_row)
        self.settings_about_list_layout.addWidget(update_card)

        launcher_ctx = self._discover_launcher_repo_context()
        launcher_repo_url = (
            str(self.cfg.get("launcher_repo_url") or "").strip()
            or str(launcher_ctx.get("remote_url") or "").strip()
            or str(getattr(lz, "LAUNCHER_REPO_URL", "") or "").strip()
            or "(未配置)"
        )
        rows = [
            ("项目定位", "GenericAgent 的非官方桌面启动器 / 前端壳"),
            ("当前主架构", "Qt 主壳（欢迎页、聊天主区、设置主区）"),
            ("当前状态", "可用，且正持续把 Tk 时代的设置与工具页并到 Qt"),
            ("启动器仓库", launcher_repo_url),
            ("agant 内核仓库", lz.REPO_URL),
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
        self._refresh_about_update_widgets()
