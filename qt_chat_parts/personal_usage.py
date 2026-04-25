from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import threading
import time
import webbrowser
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget

from launcher_app import core as lz
from launcher_app.theme import C, F


class PersonalUsageMixin:
    _LANGFUSE_DEFAULT_HOST = "https://cloud.langfuse.com"
    _LAN_INTERFACE_DEFAULT_PORT = 8501
    _LAN_INTERFACE_EXTRA_PACKAGES = ("streamlit>=1.28",)
    _LAN_INTERFACE_FRONTENDS = (
        ("frontends/stapp.py", "默认 Streamlit（stapp.py）"),
        ("frontends/stapp2.py", "备用 Streamlit（stapp2.py）"),
    )
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
        rc = getattr(result, "returncode", 1)
        if int(1 if rc is None else rc) != 0:
            return ""
        return str(getattr(result, "stdout", "") or "").strip()

    def _git_run(self, repo_dir: str, args, *, timeout: int = 20):
        root = os.path.abspath(str(repo_dir or "").strip()) if str(repo_dir or "").strip() else ""
        if not root or not os.path.isdir(root):
            return {"ok": False, "returncode": 2, "stdout": "", "stderr": "repo_dir_invalid"}
        cmd = ["git", "-C", root, *list(args or [])]
        try:
            result = lz._run_external_subprocess(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(2, int(timeout or 20)),
            )
        except Exception as e:
            return {"ok": False, "returncode": 1, "stdout": "", "stderr": str(e)}
        rc = getattr(result, "returncode", 1)
        code = int(1 if rc is None else rc)
        return {
            "ok": code == 0,
            "returncode": code,
            "stdout": str(getattr(result, "stdout", "") or ""),
            "stderr": str(getattr(result, "stderr", "") or ""),
        }

    def _format_git_result_detail(self, payload) -> str:
        item = payload if isinstance(payload, dict) else {}
        out = str(item.get("stdout") or "").strip()
        err = str(item.get("stderr") or "").strip()
        if out and err:
            return out + "\n" + err
        if err:
            return err
        if out:
            return out
        return f"exit={item.get('returncode', 1)}"

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
        rc = getattr(result, "returncode", 1)
        return int(1 if rc is None else rc) == 0

    def _git_has_commit(self, repo_dir: str, commit_sha: str) -> bool:
        root = os.path.abspath(str(repo_dir or "").strip()) if str(repo_dir or "").strip() else ""
        sha = str(commit_sha or "").strip()
        if not root or not os.path.isdir(root) or not sha:
            return False
        try:
            result = lz._run_external_subprocess(
                ["git", "-C", root, "cat-file", "-e", f"{sha}^{{commit}}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
            )
        except Exception:
            return False
        rc = getattr(result, "returncode", 1)
        return int(1 if rc is None else rc) == 0

    def _git_try_fetch_origin(self, repo_dir: str, branch: str = "main") -> str:
        root = os.path.abspath(str(repo_dir or "").strip()) if str(repo_dir or "").strip() else ""
        if not root or not os.path.isdir(root):
            return "repo_dir_invalid"
        result = self._git_fetch_origin_result(root, branch)
        if bool(result.get("ok")):
            return ""
        return self._format_git_result_detail(result)

    def _git_fetch_origin_result(self, repo_dir: str, branch: str = "main"):
        root = os.path.abspath(str(repo_dir or "").strip()) if str(repo_dir or "").strip() else ""
        target_branch = str(branch or "").strip() or "main"
        return self._git_run(root, ["fetch", "--prune", "--no-tags", "origin", target_branch], timeout=28)

    def _git_pull_ff_only(self, repo_dir: str, branch: str = "main"):
        root = os.path.abspath(str(repo_dir or "").strip()) if str(repo_dir or "").strip() else ""
        target_branch = str(branch or "").strip() or "main"
        return self._git_run(root, ["pull", "--ff-only", "origin", target_branch], timeout=40)

    def _git_ls_remote_default_head(self, repo_url: str):
        url = str(repo_url or "").strip()
        if not url:
            return {"default_branch": "", "head_sha": "", "error": "repo_url_empty"}
        probe = self._git_run(os.getcwd(), ["ls-remote", "--symref", url, "HEAD"], timeout=20)
        if not bool(probe.get("ok")):
            return {"default_branch": "", "head_sha": "", "error": self._format_git_result_detail(probe)}
        default_branch = ""
        head_sha = ""
        raw = str(probe.get("stdout") or "")
        for line in raw.splitlines():
            text = str(line or "").strip()
            if not text:
                continue
            if text.startswith("ref:") and text.endswith("HEAD"):
                parts = text.split()
                if len(parts) >= 3:
                    ref = str(parts[1] or "").strip()
                    if ref.startswith("refs/heads/"):
                        default_branch = ref[len("refs/heads/") :]
            elif text.endswith("HEAD"):
                parts = text.split()
                if parts:
                    sha = str(parts[0] or "").strip()
                    if re.fullmatch(r"[0-9a-fA-F]{40}", sha):
                        head_sha = sha.lower()
        return {"default_branch": default_branch, "head_sha": head_sha, "error": ""}

    def _git_ls_remote_branch_head(self, repo_url: str, branch: str):
        url = str(repo_url or "").strip()
        target_branch = str(branch or "").strip() or "main"
        if not url:
            return {"sha": "", "error": "repo_url_empty"}
        probe = self._git_run(os.getcwd(), ["ls-remote", url, f"refs/heads/{target_branch}"], timeout=20)
        if not bool(probe.get("ok")):
            return {"sha": "", "error": self._format_git_result_detail(probe)}
        sha = ""
        raw = str(probe.get("stdout") or "")
        for line in raw.splitlines():
            text = str(line or "").strip()
            if not text:
                continue
            parts = text.split()
            if parts:
                item = str(parts[0] or "").strip()
                if re.fullmatch(r"[0-9a-fA-F]{40}", item):
                    sha = item.lower()
                    break
        if not sha:
            return {"sha": "", "error": f"未找到 refs/heads/{target_branch} 的远端提交"}
        return {"sha": sha, "error": ""}

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
            return {"repo_dir": "", "remote_url": "", "local_sha": "", "local_version": ""}
        sha = self._git_cmd_text(root, ["rev-parse", "HEAD"])
        remote = self._git_cmd_text(root, ["remote", "get-url", "origin"])
        if not sha:
            sha = self._read_git_head_sha(root)
        if not remote:
            remote = self._read_git_origin_url(root)
        local_version = self._detect_local_version_hint(root)
        return {
            "repo_dir": root,
            "remote_url": str(remote or "").strip(),
            "local_sha": str(sha or "").strip(),
            "local_version": str(local_version or "").strip(),
        }

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
        return {"repo_dir": "", "remote_url": "", "local_sha": "", "local_version": ""}

    def _git_dir_for_repo(self, repo_dir: str) -> str:
        root = os.path.abspath(str(repo_dir or "").strip()) if str(repo_dir or "").strip() else ""
        if not root:
            return ""
        direct = os.path.join(root, ".git")
        if os.path.isdir(direct):
            return direct
        if os.path.isfile(direct):
            try:
                with open(direct, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read().strip()
                m = re.match(r"^gitdir:\s*(.+)$", text, flags=re.IGNORECASE)
                if m:
                    val = str(m.group(1) or "").strip()
                    if val:
                        if not os.path.isabs(val):
                            val = os.path.normpath(os.path.join(root, val))
                        if os.path.isdir(val):
                            return val
            except Exception:
                return ""
        return ""

    def _read_git_head_sha(self, repo_dir: str) -> str:
        git_dir = self._git_dir_for_repo(repo_dir)
        if not git_dir:
            return ""
        head_file = os.path.join(git_dir, "HEAD")
        try:
            with open(head_file, "r", encoding="utf-8", errors="replace") as f:
                head = f.read().strip()
        except Exception:
            return ""
        if not head:
            return ""
        if re.fullmatch(r"[0-9a-fA-F]{40}", head):
            return head.lower()
        m = re.match(r"^ref:\s*(.+)$", head)
        if not m:
            return ""
        ref_name = str(m.group(1) or "").strip()
        if not ref_name:
            return ""
        ref_file = os.path.join(git_dir, *ref_name.split("/"))
        try:
            with open(ref_file, "r", encoding="utf-8", errors="replace") as f:
                val = f.read().strip()
            if re.fullmatch(r"[0-9a-fA-F]{40}", val):
                return val.lower()
        except Exception:
            pass
        packed_refs = os.path.join(git_dir, "packed-refs")
        try:
            with open(packed_refs, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    text = str(line or "").strip()
                    if not text or text.startswith("#") or text.startswith("^"):
                        continue
                    parts = text.split(" ", 1)
                    if len(parts) != 2:
                        continue
                    sha, ref = parts
                    if ref == ref_name and re.fullmatch(r"[0-9a-fA-F]{40}", sha):
                        return sha.lower()
        except Exception:
            pass
        return ""

    def _read_git_origin_url(self, repo_dir: str) -> str:
        git_dir = self._git_dir_for_repo(repo_dir)
        if not git_dir:
            return ""
        cfg_file = os.path.join(git_dir, "config")
        if not os.path.isfile(cfg_file):
            return ""
        section = ""
        try:
            with open(cfg_file, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    text = str(line or "").strip()
                    if not text or text.startswith(("#", ";")):
                        continue
                    if text.startswith("[") and text.endswith("]"):
                        section = text.lower()
                        continue
                    if section == '[remote "origin"]':
                        m = re.match(r"^url\s*=\s*(.+)$", text, flags=re.IGNORECASE)
                        if m:
                            return str(m.group(1) or "").strip()
        except Exception:
            return ""
        return ""

    def _read_json_version_field(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if isinstance(payload, dict):
                for key in ("version", "tag", "release", "app_version"):
                    val = str(payload.get(key) or "").strip()
                    if val:
                        return val
        except Exception:
            return ""
        return ""

    def _detect_local_version_hint(self, repo_dir: str) -> str:
        root = os.path.abspath(str(repo_dir or "").strip()) if str(repo_dir or "").strip() else ""
        if not root:
            return ""
        candidates_json = (
            os.path.join(root, "version.json"),
            os.path.join(root, ".version.json"),
        )
        for fp in candidates_json:
            if os.path.isfile(fp):
                val = self._read_json_version_field(fp)
                if val:
                    return val
        candidates_text = (
            os.path.join(root, "VERSION"),
            os.path.join(root, "version.txt"),
            os.path.join(root, ".version"),
        )
        for fp in candidates_text:
            if not os.path.isfile(fp):
                continue
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read().strip()
                if text:
                    return text
            except Exception:
                continue
        return ""

    def _version_tuple(self, version_text: str):
        text = str(version_text or "").strip().lower().lstrip("v")
        if not text:
            return tuple()
        out = []
        for chunk in re.split(r"[.+-]", text):
            if chunk.isdigit():
                out.append(int(chunk))
            elif chunk:
                out.append(chunk)
        return tuple(out)

    def _compare_versions(self, left: str, right: str) -> int:
        lv = self._version_tuple(left)
        rv = self._version_tuple(right)
        if not lv or not rv:
            return 0
        if lv < rv:
            return -1
        if lv > rv:
            return 1
        return 0

    def _build_launcher_external_update_info(self, release: dict, *, local_version: str = ""):
        if not isinstance(release, dict):
            return None
        release_tag = str(release.get("tag_name") or "").strip()
        target_version = release_tag.lstrip("v")
        if not target_version:
            return None
        local_ver = str(local_version or "").strip().lstrip("v")
        cmp = self._compare_versions(local_ver, target_version)
        if cmp >= 0:
            return None
        release_url = str(release.get("html_url") or "").strip()
        assets = release.get("assets")
        best_url = ""
        best_name = ""
        best_score = -1
        if isinstance(assets, list):
            for item in assets:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                url = str(item.get("browser_download_url") or "").strip()
                if not name or not url:
                    continue
                lowered = name.lower()
                score = 0
                if re.search(r"genericagentlauncher[-_]?setup[^/\\]*\.exe$", lowered):
                    score = 100
                elif lowered == "genericagentlauncher.exe":
                    score = 90
                elif lowered.endswith(".msi"):
                    score = 80
                elif lowered.endswith(".exe"):
                    score = 70
                elif lowered.endswith(".zip"):
                    score = 50
                if score > best_score:
                    best_score = score
                    best_name = name
                    best_url = url
        external_url = best_url or release_url
        if not external_url:
            return None
        return {
            "target_version": target_version,
            "current_version": local_ver,
            "channel": "stable",
            "release_tag": release_tag,
            "release_url": release_url,
            "is_update_available": True,
            "install_mode": "external",
            "external_url": external_url,
            "external_asset_name": best_name,
            "fallback_reason": "manifest_or_signing_unavailable",
        }

    def _check_repo_update(self, *, name: str, repo_url: str, local_repo_dir: str, local_sha: str, local_version: str = ""):
        row = {
            "name": str(name or "").strip(),
            "repo_url": str(repo_url or "").strip(),
            "repo_slug": "",
            "local_sha": str(local_sha or "").strip(),
            "local_version": str(local_version or "").strip(),
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

        default_branch = "main"
        meta_errors = []
        commit_errors = []
        meta, meta_url, meta_errors = self._github_json_with_fallback(f"/repos/{slug}", timeout=8)
        if isinstance(meta, dict):
            row["api_source"] = meta_url
            default_branch = str(meta.get("default_branch") or "main").strip() or "main"
            row["default_branch"] = default_branch
        else:
            ls_head = self._git_ls_remote_default_head(row["repo_url"])
            head_sha = str(ls_head.get("head_sha") or "").strip()
            if not head_sha:
                row["status"] = "error"
                row["message"] = "无法访问 GitHub 仓库信息，且 git ls-remote 兜底失败。"
                merged = list(meta_errors or [])
                ls_err = str(ls_head.get("error") or "").strip()
                if ls_err:
                    merged.append(f"git ls-remote -> {ls_err}")
                row["errors"] = merged
                return row
            row["api_source"] = "git ls-remote --symref"
            default_branch = str(ls_head.get("default_branch") or "main").strip() or "main"
            row["default_branch"] = default_branch
            row["remote_sha"] = head_sha

        if not str(row.get("remote_sha") or "").strip():
            commit, commit_url, commit_errors = self._github_json_with_fallback(f"/repos/{slug}/commits/{default_branch}", timeout=8)
            if isinstance(commit, dict):
                row["api_source"] = commit_url or row["api_source"]
                row["remote_sha"] = str(commit.get("sha") or "").strip()
            else:
                ls_branch = self._git_ls_remote_branch_head(row["repo_url"], default_branch)
                ls_sha = str(ls_branch.get("sha") or "").strip()
                if ls_sha:
                    row["api_source"] = f"git ls-remote refs/heads/{default_branch}"
                    row["remote_sha"] = ls_sha
                    ls_err = str(ls_branch.get("error") or "").strip()
                    if ls_err:
                        commit_errors = list(commit_errors or []) + [f"git ls-remote -> {ls_err}"]
                else:
                    row["status"] = "error"
                    row["message"] = "无法读取 GitHub 最新提交。"
                    merged = list(meta_errors or []) + list(commit_errors or [])
                    ls_err = str(ls_branch.get("error") or "").strip()
                    if ls_err:
                        merged.append(f"git ls-remote -> {ls_err}")
                    row["errors"] = merged
                    return row

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
            local_ver = str(row.get("local_version") or "").strip().lstrip("v")
            remote_tag = str(row.get("latest_release_tag") or "").strip().lstrip("v")
            if local_ver and remote_tag:
                lv = self._version_tuple(local_ver)
                rv = self._version_tuple(remote_tag)
                if lv and rv:
                    if lv == rv:
                        row["status"] = "up_to_date"
                        row["message"] = f"本地版本 {local_ver} 与远端发布 {remote_tag} 一致。"
                        return row
                    if lv < rv:
                        row["status"] = "behind"
                        row["message"] = f"本地版本 {local_ver} 落后于远端发布 {remote_tag}。"
                        return row
                    row["status"] = "ahead"
                    row["message"] = f"本地版本 {local_ver} 高于远端发布 {remote_tag}。"
                    return row
            row["status"] = "unknown_local"
            if local_ver:
                row["message"] = f"本地版本 {local_ver}，但无法读取本地提交哈希；远端最新提交 {self._short_sha(remote)}。"
            else:
                row["message"] = f"远端最新 {self._short_sha(remote)}，但本地版本无法识别。"
            return row
        if local == remote:
            row["status"] = "up_to_date"
            row["message"] = f"已是最新（{self._short_sha(local)}）。"
            return row
        repo_dir = os.path.abspath(str(local_repo_dir or "").strip()) if str(local_repo_dir or "").strip() else ""
        if repo_dir:
            local_present = self._git_has_commit(repo_dir, local)
            remote_present = self._git_has_commit(repo_dir, remote)
            fetch_error = ""
            auto_fetch_enabled = bool(self.cfg.get("kernel_update_auto_fetch_enabled", True))
            if (not remote_present) and default_branch and auto_fetch_enabled:
                fetch_error = self._git_try_fetch_origin(repo_dir, default_branch)
                remote_present = self._git_has_commit(repo_dir, remote)
            if (not local_present) or (not remote_present):
                row["status"] = "need_sync"
                detail = f"远端提交 {self._short_sha(remote)} 暂不可在本地仓库校验。"
                if fetch_error:
                    detail += f" fetch 结果：{fetch_error}"
                elif not auto_fetch_enabled:
                    detail += " 当前已关闭“自动 fetch 远端引用”。"
                row["message"] = (
                    f"{detail} 请先在内核目录执行 git fetch / git pull，再重新检查更新。"
                )
                return row
        if repo_dir and self._git_is_ancestor(repo_dir, local, remote):
            row["status"] = "behind"
            row["message"] = f"本地 {self._short_sha(local)} 落后于远端 {self._short_sha(remote)}。"
            return row
        if repo_dir and self._git_is_ancestor(repo_dir, remote, local):
            row["status"] = "ahead"
            row["message"] = f"本地 {self._short_sha(local)} 比远端更新（可能是未推送提交）。"
            return row
        if repo_dir:
            base = self._git_cmd_text(repo_dir, ["merge-base", local, remote], timeout=8)
            if not base:
                row["status"] = "need_sync"
                row["message"] = (
                    f"本地 {self._short_sha(local)} 与远端 {self._short_sha(remote)} 暂无法建立可比对的共同基线，"
                    "请先同步远端后再检查。"
                )
                return row
        row["status"] = "diverged"
        row["message"] = f"本地 {self._short_sha(local)} 与远端 {self._short_sha(remote)} 分叉。"
        return row

    def _perform_update_check(self):
        checked_at = time.time()
        launcher_repo_url = str(self.cfg.get("launcher_repo_url") or "").strip() or str(getattr(lz, "LAUNCHER_REPO_URL", "") or "").strip()
        api_candidates = self.cfg.get("github_update_api_urls")
        if not isinstance(api_candidates, list):
            api_candidates = []
        public_key_pem = (
            str(self.cfg.get("update_signing_public_key_pem") or "").strip()
            or str(os.environ.get("GA_LAUNCHER_UPDATE_PUBLIC_KEY_PEM") or "").strip()
            or str(os.environ.get("GA_LAUNCHER_UPDATE_PUBKEY") or "").strip()
            or str(getattr(lz, "UPDATE_SIGNING_PUBLIC_KEY_PEM", "") or "").strip()
        )

        launcher = {
            "name": "启动器",
            "repo_url": launcher_repo_url,
            "repo_slug": self._repo_slug_from_url(launcher_repo_url),
            "local_sha": "",
            "local_version": str(lz.current_launcher_version()),
            "remote_sha": "",
            "default_branch": "",
            "latest_release_tag": "",
            "status": "unknown",
            "message": "",
            "api_source": "",
            "errors": [],
            "update_info": None,
        }
        try:
            info = lz.query_launcher_update(
                repo_url=launcher_repo_url,
                current_version=lz.current_launcher_version(),
                public_key_pem=public_key_pem,
                api_candidates=api_candidates,
            )
            launcher["latest_release_tag"] = str(info.get("release_tag") or "").strip()
            launcher["remote_sha"] = launcher["latest_release_tag"]
            launcher["local_version"] = str(info.get("current_version") or launcher.get("local_version") or "").strip()
            launcher["api_source"] = str(info.get("api_source") or "").strip()
            launcher["update_info"] = info
            if bool(info.get("is_update_available", False)):
                launcher["status"] = "behind"
                launcher["message"] = (
                    f"当前 {info.get('current_version')}，可升级到 {info.get('target_version')}。"
                )
            else:
                launcher["status"] = "up_to_date"
                launcher["message"] = f"当前版本 {info.get('current_version')} 已是最新。"
        except Exception as e:
            slug = str(launcher.get("repo_slug") or "").strip()
            rel = None
            rel_url = ""
            rel_errors = []
            if slug:
                rel, rel_url, rel_errors = self._github_json_with_fallback(
                    f"/repos/{slug}/releases/latest",
                    allow_404=True,
                    timeout=8,
                )
            tag = ""
            if isinstance(rel, dict):
                tag = str(rel.get("tag_name") or "").strip()
                launcher["latest_release_tag"] = tag
                launcher["remote_sha"] = tag
                launcher["api_source"] = rel_url or launcher.get("api_source", "")
            local_ver = str(launcher.get("local_version") or "").strip()
            cmp = self._compare_versions(local_ver, tag)
            detail = str(e or "").strip()
            detail_lower = detail.lower()
            is_release_metadata_issue = (
                "manifest" in detail_lower
                or "signature" in detail_lower
                or "签名" in detail
                or "公钥" in detail
            )
            if tag and local_ver and is_release_metadata_issue:
                fallback_info = self._build_launcher_external_update_info(rel, local_version=local_ver)
                if cmp < 0:
                    launcher["status"] = "behind"
                    if isinstance(fallback_info, dict):
                        launcher["update_info"] = fallback_info
                        launcher["message"] = (
                            f"当前版本 {local_ver}，GitHub 最新发布 {tag}。"
                            "该发布不满足应用内静默升级条件，已自动切换为“下载更新安装包”模式。"
                        )
                    else:
                        launcher["message"] = (
                            f"当前版本 {local_ver}，GitHub 最新发布 {tag}。"
                            "但该发布缺少可下载安装资产，暂不能触发更新。"
                        )
                elif cmp > 0:
                    launcher["status"] = "ahead"
                    launcher["message"] = f"当前版本 {local_ver} 高于 GitHub 最新发布 {tag}。"
                else:
                    launcher["status"] = "up_to_date"
                    launcher["message"] = f"当前版本 {local_ver} 与 GitHub 最新发布 {tag} 一致。"
                launcher["errors"] = list(rel_errors or [])
            else:
                launcher["status"] = "error"
                launcher["message"] = f"当前版本 {local_ver or '未知'}，更新检查失败：{e}"

        if lz.is_valid_agent_dir(self.agent_dir):
            kernel_ctx = self._collect_local_repo_context(self.agent_dir)
            kernel_repo_url = str(kernel_ctx.get("remote_url") or "").strip() or str(lz.REPO_URL or "").strip()
            kernel = self._check_repo_update(
                name="agant 内核（GenericAgent）",
                repo_url=kernel_repo_url,
                local_repo_dir=str(kernel_ctx.get("repo_dir") or ""),
                local_sha=str(kernel_ctx.get("local_sha") or ""),
                local_version=str(kernel_ctx.get("local_version") or ""),
            )
        else:
            kernel = {
                "name": "agant 内核（GenericAgent）",
                "repo_url": str(lz.REPO_URL or "").strip(),
                "repo_slug": self._repo_slug_from_url(str(lz.REPO_URL or "").strip()),
                "local_sha": "",
                "local_version": "",
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
        source_raw = str(item.get("api_source") or "").strip()
        source_brief = ""
        if source_raw:
            lower = source_raw.lower()
            if "git ls-remote" in lower:
                source_brief = "git ls-remote"
            elif "api.github.com" in lower:
                source_brief = "GitHub API"
            elif "ghproxy" in lower:
                source_brief = "GitHub API（代理）"
            else:
                source_brief = source_raw if len(source_raw) <= 42 else (source_raw[:39] + "...")
        source_tail = f"（来源：{source_brief}）" if source_brief else ""
        if status == "behind":
            return f"- {label}：发现更新。{msg}{tail}{source_tail}"
        if status == "up_to_date":
            return f"- {label}：已是最新。{msg}{tail}{source_tail}"
        if status == "ahead":
            return f"- {label}：本地领先远端。{msg}{tail}{source_tail}"
        if status == "diverged":
            return f"- {label}：本地与远端分叉。{msg}{tail}{source_tail}"
        if status == "need_sync":
            return f"- {label}：[需同步远端] {msg}{tail}{source_tail}"
        if status == "skipped":
            return f"- {label}：未检查。{msg}"
        if status == "unknown_local":
            return f"- {label}：远端可达，但无法判断本地版本。{msg}{tail}{source_tail}"
        return f"- {label}：检查失败。{msg}{source_tail}"

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
                "local_version": str(item.get("local_version") or ""),
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

    def _format_update_diagnostics_text(self):
        lines = []
        latest_job = {}
        try:
            latest_job = lz.latest_update_job() or {}
        except Exception as e:
            lines.append(f"最近更新任务：读取失败（{e}）")
            latest_job = {}

        if isinstance(latest_job, dict) and latest_job:
            job_id = str(latest_job.get("job_id") or "").strip() or "unknown"
            status = str(latest_job.get("status") or "").strip() or "unknown"
            phase = str(latest_job.get("phase") or "").strip() or "unknown"
            version = str(latest_job.get("target_version") or "").strip() or "unknown"
            error_code = str(latest_job.get("error_code") or "").strip()
            error_detail = str(latest_job.get("error_detail") or "").strip()
            lines.append(f"最近更新任务：{job_id}")
            lines.append(f"- 状态：{status}（阶段：{phase}）")
            lines.append(f"- 目标版本：{version}")
            if error_code:
                lines.append(f"- 错误码：{error_code}")
            if error_detail:
                short = error_detail if len(error_detail) <= 180 else (error_detail[:177] + "...")
                lines.append(f"- 错误详情：{short}")
            auth = latest_job.get("authenticode") if isinstance(latest_job.get("authenticode"), dict) else {}
            if auth:
                lines.append(
                    f"- 主程序签名：{auth.get('status', 'Unknown')}"
                    + ("（已验证）" if bool(auth.get("is_valid")) else "（未通过）")
                )
            completed_at = float(latest_job.get("completed_at") or 0.0)
            if completed_at > 0:
                lines.append(f"- 完成时间：{self._usage_time_label(completed_at)}")
        else:
            lines.append("最近更新任务：暂无记录。")

        try:
            tail = str(lz.read_updater_log_tail(max_lines=18, max_chars=2600) or "").strip()
        except Exception as e:
            tail = f"读取 updater.log 失败：{e}"
        if tail:
            lines.append("")
            lines.append("updater.log（最近 18 行）：")
            lines.append(tail)
        else:
            lines.append("")
            lines.append("updater.log：暂无内容。")
        return "\n".join(lines)

    def _refresh_about_update_diagnostics_widgets(self):
        label = getattr(self, "settings_about_update_diag_status", None)
        if label is None:
            return
        label.setText(self._format_update_diagnostics_text())

    def _refresh_about_update_diagnostics_manual(self):
        self._refresh_about_update_diagnostics_widgets()
        self._set_status("已刷新更新诊断信息。")

    def _refresh_about_update_widgets(self):
        running = bool(getattr(self, "_update_check_running", False))
        kernel_sync_running = bool(getattr(self, "_kernel_repo_sync_running", False))
        status_label = getattr(self, "settings_about_update_status", None)
        if status_label is not None:
            if running:
                status_label.setText("正在检测 GitHub 更新，请稍候…")
            else:
                last = getattr(self, "_last_update_check_result", None)
                if isinstance(last, dict):
                    notice = str(getattr(self, "_about_update_notice_text", "") or "").strip()
                    detail = self._update_history_brief_text(limit=3)
                    if notice:
                        detail = notice + "\n\n" + detail
                    status_label.setText(
                        self._update_result_summary(last)
                        + "\n\n"
                        + detail
                    )
                else:
                    status_label.setText(
                        "尚未检查。支持手动检测；也可勾选开机自动检测。\n\n"
                        + self._update_history_brief_text(limit=3)
                    )
        btn = getattr(self, "settings_about_check_updates_btn", None)
        if btn is not None:
            btn.setEnabled((not running) and (not kernel_sync_running))
            btn.setText("正在检测…" if running else "立即检测 GitHub 更新")
        install_btn = getattr(self, "settings_about_install_update_btn", None)
        if install_btn is not None:
            launcher_row = {}
            result = getattr(self, "_last_update_check_result", None)
            if isinstance(result, dict) and isinstance(result.get("launcher"), dict):
                launcher_row = result.get("launcher") or {}
            info = launcher_row.get("update_info") if isinstance(launcher_row.get("update_info"), dict) else {}
            install_mode = str(info.get("install_mode") or "").strip().lower()
            updater_exists = os.path.isfile(str(getattr(lz, "updater_executable_path", lambda: "")() or "").strip())
            external_target = str(info.get("external_url") or info.get("release_url") or "").strip()
            can_install = (
                (not running)
                and str((launcher_row or {}).get("status") or "").strip().lower() == "behind"
                and isinstance((launcher_row or {}).get("update_info"), dict)
                and (
                    (install_mode == "external" and bool(external_target))
                    or (install_mode != "external" and updater_exists)
                )
            )
            install_btn.setEnabled(can_install and (not kernel_sync_running))
            install_btn.setText("下载更新安装包" if install_mode == "external" else "安装更新并重启")
        fetch_btn = getattr(self, "settings_about_sync_kernel_fetch_btn", None)
        if fetch_btn is not None:
            fetch_btn.setEnabled((not running) and (not kernel_sync_running) and lz.is_valid_agent_dir(self.agent_dir))
            fetch_btn.setText("同步中…" if kernel_sync_running else "同步内核远端（fetch）")
        pull_btn = getattr(self, "settings_about_sync_kernel_pull_btn", None)
        if pull_btn is not None:
            pull_btn.setEnabled((not running) and (not kernel_sync_running) and lz.is_valid_agent_dir(self.agent_dir))
            pull_btn.setText("同步中…" if kernel_sync_running else "拉取并快进（pull --ff-only）")
        self._refresh_about_update_diagnostics_widgets()

    def _on_toggle_update_auto_check(self, checked):
        self.cfg["auto_check_github_updates"] = bool(checked)
        lz.save_config(self.cfg)
        self._set_status("已更新“启动时自动检查更新”设置。")

    def _on_toggle_kernel_update_auto_fetch(self, checked):
        self.cfg["kernel_update_auto_fetch_enabled"] = bool(checked)
        lz.save_config(self.cfg)
        self._set_status("已更新“检测内核更新时自动 fetch”设置。")

    def _kernel_repo_sync_target(self):
        if not lz.is_valid_agent_dir(self.agent_dir):
            return {"repo_dir": "", "repo_url": "", "branch": "main"}
        ctx = self._collect_local_repo_context(self.agent_dir)
        repo_dir = os.path.abspath(str(ctx.get("repo_dir") or "").strip()) if str(ctx.get("repo_dir") or "").strip() else ""
        result = getattr(self, "_last_update_check_result", None)
        kernel_row = (result or {}).get("kernel") if isinstance(result, dict) else {}
        branch = str((kernel_row or {}).get("default_branch") or "").strip() or "main"
        repo_url = str(ctx.get("remote_url") or "").strip() or str((kernel_row or {}).get("repo_url") or "").strip() or str(lz.REPO_URL or "").strip()
        return {"repo_dir": repo_dir, "repo_url": repo_url, "branch": branch}

    def _start_kernel_repo_sync(self, mode: str):
        action = str(mode or "").strip().lower()
        if action not in ("fetch", "pull"):
            QMessageBox.warning(self, "参数错误", f"不支持的同步动作：{mode}")
            return
        if bool(getattr(self, "_update_check_running", False)):
            QMessageBox.information(self, "请稍候", "当前正在进行更新检测，请稍后再执行仓库同步。")
            return
        if bool(getattr(self, "_kernel_repo_sync_running", False)):
            QMessageBox.information(self, "请稍候", "内核仓库同步正在执行，请等待完成。")
            return
        target = self._kernel_repo_sync_target()
        repo_dir = str(target.get("repo_dir") or "").strip()
        branch = str(target.get("branch") or "").strip() or "main"
        if not repo_dir or not os.path.isdir(repo_dir):
            QMessageBox.warning(self, "无法同步", "当前没有可用的内核 Git 仓库目录。")
            return
        if action == "pull":
            if (
                QMessageBox.question(
                    self,
                    "拉取并快进",
                    f"将执行 git pull --ff-only origin {branch}。\n\n"
                    "如果本地有未提交变更或无法快进，操作会失败并保留现状。是否继续？",
                )
                != QMessageBox.Yes
            ):
                return
        holder = {"ok": False, "detail": "", "action": action, "branch": branch}
        self._kernel_repo_sync_running = True
        self._refresh_about_update_widgets()
        self._set_status(f"正在同步内核仓库（{action} {branch}）…")

        def worker():
            if action == "fetch":
                fetch_result = self._git_fetch_origin_result(repo_dir, branch)
                holder["ok"] = bool(fetch_result.get("ok"))
                holder["detail"] = self._format_git_result_detail(fetch_result)
                return
            pull_result = self._git_pull_ff_only(repo_dir, branch)
            holder["ok"] = bool(pull_result.get("ok"))
            holder["detail"] = self._format_git_result_detail(pull_result)

        thread = threading.Thread(target=worker, name=f"kernel-git-{action}", daemon=True)
        thread.start()

        def poll():
            if thread.is_alive():
                QTimer.singleShot(120, poll)
                return
            self._kernel_repo_sync_running = False
            self._refresh_about_update_widgets()
            if holder.get("ok"):
                self._set_status(f"内核仓库同步成功（{action} {branch}）。")
                detail = str(holder.get("detail") or "").strip()
                content = (
                    f"动作：{action} origin {branch}\n\n"
                    + (detail or "命令已执行完成。")
                )
                QMessageBox.information(self, "内核仓库同步完成", content)
                if bool(getattr(self, "_update_check_running", False)):
                    return
                self._start_update_check(manual=False)
                return
            QMessageBox.warning(self, "内核仓库同步失败", str(holder.get("detail") or "未知错误"))
            self._set_status("内核仓库同步失败。")

        QTimer.singleShot(120, poll)

    def _sync_kernel_repo_fetch(self):
        self._start_kernel_repo_sync("fetch")

    def _sync_kernel_repo_pull(self):
        self._start_kernel_repo_sync("pull")

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
            self._about_update_notice_text = ""
            text = f"{summary}\n\n{record_text}"
            if changes_text:
                text = f"{summary}\n\n{changes_text}\n\n{record_text}"
            QMessageBox.information(self, "GitHub 更新检测", text)
            self._set_status("已完成 GitHub 更新检测，并记录本次结果。")
            return

        if changes_text:
            self._about_update_notice_text = (
                "最近一次自动检查发现版本变动，请在本页查看详情。\n\n"
                + changes_text
            )
            self._refresh_about_update_widgets()
            self._set_status("启动时自动更新检测发现版本变动，请到“关于”页查看详情。")
            return
        self._about_update_notice_text = ""
        self._refresh_about_update_widgets()
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

    def _start_launcher_update_install(self):
        if bool(getattr(self, "_update_check_running", False)):
            QMessageBox.information(self, "请稍候", "当前正在进行更新检测，请稍后再安装更新。")
            return
        result = getattr(self, "_last_update_check_result", None)
        launcher_row = (result or {}).get("launcher") if isinstance(result, dict) else None
        if not isinstance(launcher_row, dict):
            QMessageBox.information(self, "暂无更新信息", "请先执行一次“立即检测 GitHub 更新”。")
            return
        info = launcher_row.get("update_info")
        if not isinstance(info, dict):
            QMessageBox.information(self, "暂无可安装更新", "当前还没有可安装的启动器更新。")
            return
        if str(launcher_row.get("status") or "").strip().lower() != "behind":
            QMessageBox.information(self, "已是最新", "当前启动器已是最新版本。")
            return
        target_version = str(info.get("target_version") or "").strip()
        install_mode = str(info.get("install_mode") or "").strip().lower()
        if install_mode == "external":
            external_url = str(info.get("external_url") or info.get("release_url") or "").strip()
            if not external_url:
                QMessageBox.warning(self, "无法下载更新", "当前发布未提供可用下载链接。")
                return
            asset_name = str(info.get("external_asset_name") or "").strip()
            asset_hint = f"（{asset_name}）" if asset_name else ""
            if (
                QMessageBox.question(
                    self,
                    "下载更新安装包",
                    f"将打开版本 {target_version} 的下载链接{asset_hint}。\n\n"
                    "下载并安装后，原有设置与使用数据会保留，是否继续？",
                )
                != QMessageBox.Yes
            ):
                return
            opened = False
            try:
                opened = bool(QDesktopServices.openUrl(QUrl(external_url)))
            except Exception:
                opened = False
            if not opened:
                try:
                    opened = bool(webbrowser.open(external_url))
                except Exception:
                    opened = False
            if not opened:
                QMessageBox.warning(self, "打开链接失败", f"请手动打开以下地址下载安装：\n{external_url}")
                return
            self._set_status(f"已打开更新下载链接（目标版本 {target_version}）。")
            return
        if (
            QMessageBox.question(
                self,
                "安装启动器更新",
                f"将安装版本 {target_version} 并重启启动器。\n\n更新过程中会保留你的设置与使用数据，是否继续？",
            )
            != QMessageBox.Yes
        ):
            return
        try:
            created = lz.create_update_job(info)
            job_path = str(created.get("job_path") or "").strip()
            if not job_path:
                raise RuntimeError("创建更新任务失败：job_path 为空")
            lz.launch_update_job(job_path)
            self._set_status(f"已启动更新任务，目标版本 {target_version}。即将重启启动器…")
            self._force_exit_requested = True
            QTimer.singleShot(220, self.close)
        except Exception as e:
            QMessageBox.warning(self, "启动更新失败", str(e))

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

    def _lan_bool(self, value, *, default=False):
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

    def _lan_interface_cfg(self):
        raw = self.cfg.get("lan_interface")
        data = raw if isinstance(raw, dict) else {}
        try:
            port = int(data.get("port", self._LAN_INTERFACE_DEFAULT_PORT) or self._LAN_INTERFACE_DEFAULT_PORT)
        except Exception:
            port = self._LAN_INTERFACE_DEFAULT_PORT
        port = min(65535, max(1024, int(port or self._LAN_INTERFACE_DEFAULT_PORT)))
        frontend = str(data.get("frontend") or self._LAN_INTERFACE_FRONTENDS[0][0]).strip().replace("\\", "/").lstrip("/")
        allowed = {item[0] for item in self._LAN_INTERFACE_FRONTENDS}
        if frontend not in allowed:
            parts = [part for part in frontend.split("/") if part]
            if not (len(parts) >= 2 and parts[0] == "frontends" and parts[-1].endswith(".py") and ".." not in parts):
                frontend = self._LAN_INTERFACE_FRONTENDS[0][0]
        enabled = self._lan_bool(data.get("enabled"), default=False)
        auto_start = self._lan_bool(data.get("auto_start"), default=False)
        bind_all = self._lan_bool(data.get("bind_all"), default=True)
        if not enabled:
            auto_start = False
        return {
            "enabled": bool(enabled),
            "auto_start": bool(auto_start),
            "bind_all": bool(bind_all),
            "port": port,
            "frontend": frontend,
        }

    def _collect_lan_interface_settings_from_widgets(self):
        cfg = self._lan_interface_cfg()
        enabled_box = getattr(self, "settings_lan_enabled", None)
        bind_box = getattr(self, "settings_lan_bind_all", None)
        auto_box = getattr(self, "settings_lan_autostart", None)
        port_spin = getattr(self, "settings_lan_port_spin", None)
        frontend_combo = getattr(self, "settings_lan_frontend_combo", None)
        if enabled_box is not None:
            cfg["enabled"] = bool(enabled_box.isChecked())
        if bind_box is not None:
            cfg["bind_all"] = bool(bind_box.isChecked())
        if auto_box is not None:
            cfg["auto_start"] = bool(cfg["enabled"] and auto_box.isChecked())
        if port_spin is not None:
            try:
                cfg["port"] = min(65535, max(1024, int(port_spin.value() or self._LAN_INTERFACE_DEFAULT_PORT)))
            except Exception:
                cfg["port"] = self._LAN_INTERFACE_DEFAULT_PORT
        if frontend_combo is not None:
            try:
                frontend = str(frontend_combo.currentData() or "").strip()
            except Exception:
                frontend = ""
            if frontend:
                cfg["frontend"] = frontend.replace("\\", "/").lstrip("/")
        return cfg

    def _lan_interface_bind_host(self, cfg=None):
        item = cfg if isinstance(cfg, dict) else self._lan_interface_cfg()
        return "0.0.0.0" if bool(item.get("bind_all")) else "127.0.0.1"

    def _lan_interface_frontend_path(self, frontend=""):
        root = os.path.abspath(str(self.agent_dir or "").strip()) if str(self.agent_dir or "").strip() else ""
        if not root:
            return ""
        rel = str(frontend or self._LAN_INTERFACE_FRONTENDS[0][0]).strip().replace("\\", "/").lstrip("/")
        candidate = os.path.abspath(os.path.join(root, *[part for part in rel.split("/") if part]))
        try:
            if os.path.commonpath([root, candidate]) != root:
                return ""
        except Exception:
            return ""
        return candidate

    def _lan_interface_command(self, py_path="", cfg=None, script_path=""):
        item = cfg if isinstance(cfg, dict) else self._lan_interface_cfg()
        py = str(py_path or "").strip()
        script = str(script_path or self._lan_interface_frontend_path(item.get("frontend"))).strip()
        return [
            py,
            "-m",
            "streamlit",
            "run",
            script,
            "--server.port",
            str(int(item.get("port") or self._LAN_INTERFACE_DEFAULT_PORT)),
            "--server.address",
            self._lan_interface_bind_host(item),
            "--server.headless",
            "true",
        ]

    def _lan_interface_log_path(self):
        if not lz.is_valid_agent_dir(self.agent_dir):
            return ""
        path = os.path.join(self.agent_dir, "temp", "launcher_lan_interface.log")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except Exception:
            pass
        return path

    def _lan_interface_log_tail(self, limit=3500):
        path = self._lan_interface_log_path()
        if not path or not os.path.isfile(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()[-max(1, int(limit or 3500)) :].strip()
        except Exception:
            return ""

    def _lan_interface_close_log_handle(self):
        handle = getattr(self, "_lan_interface_log_handle", None)
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        self._lan_interface_log_handle = None

    def _lan_interface_proc_alive(self):
        proc = getattr(self, "_lan_interface_proc", None)
        if proc is None:
            return False
        try:
            code = proc.poll()
        except Exception:
            code = None
        if code is None:
            return True
        self._lan_interface_last_exit_code = code
        self._lan_interface_proc = None
        self._lan_interface_close_log_handle()
        return False

    def _lan_interface_port_in_use(self, port):
        try:
            target_port = int(port or 0)
        except Exception:
            target_port = 0
        if target_port <= 0:
            return False
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(0.25)
            return sock.connect_ex(("127.0.0.1", target_port)) == 0
        except Exception:
            return False
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def _lan_interface_health_ok(self, port=None):
        cfg = self._lan_interface_cfg()
        try:
            target_port = int(port or cfg.get("port") or self._LAN_INTERFACE_DEFAULT_PORT)
        except Exception:
            target_port = self._LAN_INTERFACE_DEFAULT_PORT
        url = f"http://127.0.0.1:{target_port}/_stcore/health"
        try:
            req = Request(url, headers={"User-Agent": "GenericAgentLauncher/lan-health"})
            with urlopen(req, timeout=0.45) as resp:
                status = int(getattr(resp, "status", 200) or 200)
                return 200 <= status < 400
        except HTTPError as e:
            try:
                return 200 <= int(getattr(e, "code", 0) or 0) < 400
            except Exception:
                return False
        except (URLError, OSError, TimeoutError, ValueError):
            return False
        except Exception:
            return False

    def _lan_interface_local_ips(self):
        seen = set()
        ips = []

        def add_ip(value):
            text = str(value or "").strip()
            if not text or ":" in text:
                return
            if text.startswith("127.") or text == "0.0.0.0" or text.startswith("169.254."):
                return
            parts = text.split(".")
            if len(parts) != 4:
                return
            try:
                if any(int(part) < 0 or int(part) > 255 for part in parts):
                    return
            except Exception:
                return
            if text in seen:
                return
            seen.add(text)
            ips.append(text)

        try:
            hostname = socket.gethostname()
            for item in socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM):
                add_ip((item[4] or [""])[0])
        except Exception:
            pass
        for probe_host in ("8.8.8.8", "1.1.1.1"):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.settimeout(0.25)
                sock.connect((probe_host, 80))
                add_ip(sock.getsockname()[0])
            except Exception:
                pass
            finally:
                try:
                    sock.close()
                except Exception:
                    pass
        return ips

    def _lan_interface_urls(self, cfg=None):
        item = cfg if isinstance(cfg, dict) else self._lan_interface_cfg()
        port = int(item.get("port") or self._LAN_INTERFACE_DEFAULT_PORT)
        local = f"http://127.0.0.1:{port}"
        lan = []
        if bool(item.get("bind_all")):
            lan = [f"http://{ip}:{port}" for ip in self._lan_interface_local_ips()]
        return {"local": local, "lan": lan}

    def _lan_interface_status_lines(self):
        cfg = self._lan_interface_cfg()
        if not lz.is_valid_agent_dir(self.agent_dir):
            return ["请先选择有效的 GenericAgent 目录。"]
        running = self._lan_interface_proc_alive()
        external = False if running else self._lan_interface_health_ok(cfg.get("port"))
        proc = getattr(self, "_lan_interface_proc", None)
        if running and proc is not None:
            lines = [f"状态：运行中（PID {getattr(proc, 'pid', '-')}）。"]
        elif external:
            lines = ["状态：端口已有 Streamlit 响应，可能是启动器外部启动的进程。"]
        elif cfg["enabled"]:
            code = getattr(self, "_lan_interface_last_exit_code", None)
            suffix = f"；上次退出码 {code}" if code is not None else ""
            lines = [f"状态：已启用，当前未运行{suffix}。"]
        else:
            lines = ["状态：未启用。"]
        urls = self._lan_interface_urls(cfg)
        lines.append(f"本机地址：{urls['local']}")
        if cfg["bind_all"]:
            lan_urls = urls.get("lan") or []
            lines.append("局域网地址：" + ("、".join(lan_urls) if lan_urls else "未探测到可用 IPv4 地址，请用本机局域网 IP + 端口访问。"))
        else:
            lines.append("当前绑定 127.0.0.1，仅本机可访问；如需局域网访问请勾选绑定 0.0.0.0。")
        frontend_label = cfg.get("frontend") or self._LAN_INTERFACE_FRONTENDS[0][0]
        lines.append(f"上游前端：{frontend_label}")
        log_path = self._lan_interface_log_path()
        if log_path:
            lines.append(f"日志：{log_path}")
        return lines

    def _reload_lan_interface_panel(self):
        status = getattr(self, "settings_lan_status", None)
        if status is None:
            return
        cfg = self._lan_interface_cfg()
        enabled_box = getattr(self, "settings_lan_enabled", None)
        bind_box = getattr(self, "settings_lan_bind_all", None)
        auto_box = getattr(self, "settings_lan_autostart", None)
        port_spin = getattr(self, "settings_lan_port_spin", None)
        frontend_combo = getattr(self, "settings_lan_frontend_combo", None)
        widgets = [
            (enabled_box, cfg["enabled"]),
            (bind_box, cfg["bind_all"]),
            (auto_box, cfg["auto_start"]),
        ]
        for widget, value in widgets:
            if widget is None:
                continue
            try:
                widget.blockSignals(True)
                widget.setChecked(bool(value))
            finally:
                try:
                    widget.blockSignals(False)
                except Exception:
                    pass
        if port_spin is not None:
            try:
                port_spin.blockSignals(True)
                port_spin.setValue(int(cfg["port"]))
            finally:
                try:
                    port_spin.blockSignals(False)
                except Exception:
                    pass
        if frontend_combo is not None:
            try:
                frontend_combo.blockSignals(True)
                selected = str(cfg["frontend"] or "")
                found = False
                for idx in range(frontend_combo.count()):
                    if str(frontend_combo.itemData(idx) or "") == selected:
                        frontend_combo.setCurrentIndex(idx)
                        found = True
                        break
                if not found and frontend_combo.count() > 0:
                    frontend_combo.setCurrentIndex(0)
            finally:
                try:
                    frontend_combo.blockSignals(False)
                except Exception:
                    pass
        valid = lz.is_valid_agent_dir(self.agent_dir)
        running = self._lan_interface_proc_alive()
        self._refresh_lan_interface_controls_for_enabled(cfg["enabled"])
        if port_spin is not None:
            port_spin.setEnabled(valid)
        if frontend_combo is not None:
            frontend_combo.setEnabled(valid)
        save_btn = getattr(self, "settings_lan_save_btn", None)
        start_btn = getattr(self, "settings_lan_start_btn", None)
        stop_btn = getattr(self, "settings_lan_stop_btn", None)
        open_btn = getattr(self, "settings_lan_open_btn", None)
        log_btn = getattr(self, "settings_lan_log_btn", None)
        if save_btn is not None:
            save_btn.setEnabled(valid)
        if start_btn is not None:
            start_btn.setEnabled(valid and (not running))
        if stop_btn is not None:
            stop_btn.setEnabled(running)
        if open_btn is not None:
            open_btn.setEnabled(valid)
        if log_btn is not None:
            log_btn.setEnabled(bool(self._lan_interface_log_path()))
        status.setText("\n".join(self._lan_interface_status_lines()))

    def _refresh_lan_interface_controls_for_enabled(self, checked=None):
        valid = lz.is_valid_agent_dir(self.agent_dir)
        if checked is None:
            enabled_box = getattr(self, "settings_lan_enabled", None)
            enabled = bool(enabled_box is not None and enabled_box.isChecked())
        else:
            enabled = bool(checked)
        for name in ("settings_lan_bind_all", "settings_lan_autostart"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(valid and enabled)

    def _persist_lan_interface_cfg(self, cfg):
        item = cfg if isinstance(cfg, dict) else self._lan_interface_cfg()
        self.cfg["lan_interface"] = {
            "enabled": bool(item.get("enabled")),
            "auto_start": bool(item.get("auto_start")),
            "bind_all": bool(item.get("bind_all", True)),
            "port": int(item.get("port") or self._LAN_INTERFACE_DEFAULT_PORT),
            "frontend": str(item.get("frontend") or self._LAN_INTERFACE_FRONTENDS[0][0]).strip(),
        }
        lz.save_config(self.cfg)

    def _save_lan_interface_settings(self):
        old = self._lan_interface_cfg()
        new = self._collect_lan_interface_settings_from_widgets()
        changed_runtime = (
            int(old.get("port") or 0) != int(new.get("port") or 0)
            or bool(old.get("bind_all")) != bool(new.get("bind_all"))
            or str(old.get("frontend") or "") != str(new.get("frontend") or "")
        )
        was_running = self._lan_interface_proc_alive()
        self._persist_lan_interface_cfg(new)
        if not new["enabled"]:
            if was_running:
                self._stop_lan_interface_process(refresh=False)
        elif was_running and changed_runtime:
            self._stop_lan_interface_process(refresh=False)
            self._start_lan_interface_process(show_errors=True, skip_dependency_check=False, refresh=False)
        self._reload_lan_interface_panel()
        QMessageBox.information(self, "已保存", "局域网 Web 接口设置已保存。")

    def _start_lan_interface_from_settings(self):
        cfg = self._collect_lan_interface_settings_from_widgets()
        if not cfg["enabled"]:
            cfg["enabled"] = True
            enabled_box = getattr(self, "settings_lan_enabled", None)
            if enabled_box is not None:
                enabled_box.setChecked(True)
        if not cfg.get("bind_all", True):
            # 允许用户用本机模式验证，但保持提示明确。
            cfg["bind_all"] = False
        self._persist_lan_interface_cfg(cfg)
        return self._start_lan_interface_process(show_errors=True, skip_dependency_check=False, refresh=True)

    def _start_lan_interface_process(self, show_errors=True, *, skip_dependency_check=False, refresh=True):
        cfg = self._lan_interface_cfg()
        if not cfg["enabled"]:
            if show_errors:
                QMessageBox.warning(self, "未启用", "请先启用局域网 Web 接口。")
            return False
        if self._lan_interface_proc_alive():
            if refresh:
                self._reload_lan_interface_panel()
            return True
        if not lz.is_valid_agent_dir(self.agent_dir):
            if show_errors:
                QMessageBox.warning(self, "目录无效", "请先选择有效的 GenericAgent 目录。")
            return False
        script_path = self._lan_interface_frontend_path(cfg.get("frontend"))
        if not script_path or not os.path.isfile(script_path):
            if show_errors:
                QMessageBox.warning(self, "前端不存在", f"未找到上游 Streamlit 前端：\n{script_path or cfg.get('frontend')}")
            return False
        if not skip_dependency_check:
            checker = getattr(self, "_check_runtime_dependencies", None)
            if callable(checker):
                if not checker(
                    purpose="启动局域网 Web 接口",
                    extra_packages=list(self._LAN_INTERFACE_EXTRA_PACKAGES),
                    visual=bool(show_errors),
                ):
                    return False
        py = lz._resolve_config_path(str(self.cfg.get("python_exe") or "").strip()) or lz._find_system_python()
        if not py or not os.path.isfile(py):
            if show_errors:
                QMessageBox.critical(self, "缺少 Python", "依赖检查完成后仍未找到可用的 Python 可执行文件。")
            return False
        port = int(cfg.get("port") or self._LAN_INTERFACE_DEFAULT_PORT)
        if self._lan_interface_port_in_use(port):
            if self._lan_interface_health_ok(port):
                if refresh:
                    self._reload_lan_interface_panel()
                return True
            if show_errors:
                QMessageBox.warning(self, "端口被占用", f"端口 {port} 已被其他进程占用，请更换端口后重试。")
            if refresh:
                self._reload_lan_interface_panel()
            return False
        log_path = self._lan_interface_log_path()
        cmd = self._lan_interface_command(py, cfg, script_path)
        log_handle = None
        try:
            log_handle = open(log_path, "a", encoding="utf-8", buffering=1)
            urls = self._lan_interface_urls(cfg)
            log_handle.write(f"\n==== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} start lan interface ====\n")
            log_handle.write("command: " + " ".join(cmd) + "\n")
            log_handle.write("local_url: " + urls["local"] + "\n")
            if urls.get("lan"):
                log_handle.write("lan_urls: " + ", ".join(urls["lan"]) + "\n")
            proc = lz._popen_external_subprocess(
                cmd,
                cwd=self.agent_dir,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                env=lz._external_subprocess_env(),
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
        self._lan_interface_proc = proc
        self._lan_interface_log_handle = log_handle
        self._lan_interface_last_exit_code = None
        self._set_status(f"局域网 Web 接口已启动：{self._lan_interface_urls(cfg)['local']}")
        if QApplication.instance() is not None:
            QTimer.singleShot(1800, lambda se=show_errors: self._after_lan_interface_launch_check(show_errors=se))
        if refresh:
            self._reload_lan_interface_panel()
        return True

    def _after_lan_interface_launch_check(self, *, show_errors=True):
        proc = getattr(self, "_lan_interface_proc", None)
        if proc is None:
            self._reload_lan_interface_panel()
            return
        try:
            code = proc.poll()
        except Exception:
            code = None
        if code is not None:
            self._lan_interface_last_exit_code = code
            self._lan_interface_proc = None
            self._lan_interface_close_log_handle()
            if show_errors:
                tail = self._lan_interface_log_tail() or "(空)"
                QMessageBox.warning(self, "局域网接口启动失败", f"Streamlit 进程启动后已退出。\n\n日志尾部：\n{tail}")
        self._reload_lan_interface_panel()

    def _stop_lan_interface_process(self, *, refresh=True):
        proc = getattr(self, "_lan_interface_proc", None)
        if proc is not None:
            try:
                lz.terminate_process_tree(proc, terminate_timeout=1.5, kill_timeout=1.5)
                self._lan_interface_last_exit_code = proc.returncode
            except Exception:
                pass
        self._lan_interface_proc = None
        self._lan_interface_close_log_handle()
        if refresh:
            self._reload_lan_interface_panel()
        return True

    def _open_lan_interface_local_url(self):
        url = self._lan_interface_urls().get("local") or f"http://127.0.0.1:{self._LAN_INTERFACE_DEFAULT_PORT}"
        opened = False
        try:
            opened = bool(QDesktopServices.openUrl(QUrl(url)))
        except Exception:
            opened = False
        if not opened:
            try:
                opened = bool(webbrowser.open(url))
            except Exception:
                opened = False
        if not opened:
            QMessageBox.warning(self, "打开失败", f"请手动打开：\n{url}")

    def _open_lan_interface_log(self):
        path = self._lan_interface_log_path()
        if not path:
            QMessageBox.warning(self, "日志不可用", "请先选择有效的 GenericAgent 目录。")
            return
        if not os.path.isfile(path):
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "a", encoding="utf-8"):
                    pass
            except Exception:
                pass
        opened = False
        try:
            opened = bool(QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
        except Exception:
            opened = False
        if not opened:
            QMessageBox.warning(self, "打开失败", f"请手动打开日志：\n{path}")

    def _start_lan_interface_autostart(self):
        if bool(getattr(self, "_lan_interface_autostart_running", False)):
            return
        cfg = self._lan_interface_cfg()
        if not cfg["enabled"] or not cfg["auto_start"] or self._lan_interface_proc_alive():
            return
        if not lz.is_valid_agent_dir(self.agent_dir):
            return
        self._lan_interface_autostart_running = True

        def worker():
            try:
                result = lz._ensure_runtime_dependencies(
                    self.agent_dir,
                    extra_packages=list(self._LAN_INTERFACE_EXTRA_PACKAGES),
                    progress=None,
                    force_sync=False,
                )
            except Exception as e:
                result = {"ok": False, "python": "", "error": str(e)}

            def done_ui():
                self._lan_interface_autostart_running = False
                applier = getattr(self, "_apply_dependency_check_result", None)
                if callable(applier):
                    dep_ok, _py, dep_err = applier(result, extra_packages=list(self._LAN_INTERFACE_EXTRA_PACKAGES))
                else:
                    dep_ok = bool((result or {}).get("ok"))
                    dep_err = str((result or {}).get("error") or "")
                if dep_ok:
                    self._start_lan_interface_process(show_errors=False, skip_dependency_check=True, refresh=True)
                else:
                    setter = getattr(self, "_set_status", None)
                    if callable(setter):
                        setter(dep_err or "局域网 Web 接口自动启动失败：依赖检查未通过。")
                    self._reload_lan_interface_panel()

            poster = getattr(self, "_api_on_ui_thread", None)
            if callable(poster):
                poster(done_ui)
            else:
                QTimer.singleShot(0, done_ui)

        threading.Thread(target=worker, daemon=True, name="lan-interface-autostart").start()

    def _schedule_lan_interface_autostart(self, delay_ms=900):
        cfg = self._lan_interface_cfg()
        if not cfg["enabled"] or not cfg["auto_start"]:
            return
        if bool(getattr(self, "_lan_interface_autostart_scheduled", False)):
            return
        self._lan_interface_autostart_scheduled = True

        def run():
            self._lan_interface_autostart_scheduled = False
            self._start_lan_interface_autostart()

        try:
            QTimer.singleShot(max(0, int(delay_ms or 0)), self, run)
        except TypeError:
            QTimer.singleShot(max(0, int(delay_ms or 0)), run)

    def _settings_data_target_context(self):
        getter = getattr(self, "_settings_target_context", None)
        ctx = getter() if callable(getter) else {"scope": "local", "device_id": "local", "is_remote": False, "label": "本机"}
        if not isinstance(ctx, dict):
            ctx = {"scope": "local", "device_id": "local", "is_remote": False, "label": "本机"}
        scope = "remote" if bool(ctx.get("is_remote")) else "local"
        device_id = str(ctx.get("device_id") or "").strip() if scope == "remote" else "local"
        if scope == "remote" and not device_id:
            scope = "local"
            device_id = "local"
        label = str(ctx.get("label") or ("远程设备" if scope == "remote" else "本机")).strip() or ("远程设备" if scope == "remote" else "本机")
        return {"scope": scope, "device_id": device_id, "label": label, "is_remote": scope == "remote"}

    def _settings_remote_sync_key(self, target, *, kind: str):
        item = target if isinstance(target, dict) else {}
        scope = str(item.get("scope") or "local").strip().lower()
        device_id = str(item.get("device_id") or "local").strip()
        token_getter = getattr(self, "_settings_target_generation", None)
        token = token_getter() if callable(token_getter) else 0
        return f"{str(kind or 'settings').strip()}:{scope}:{device_id}:{int(token or 0)}"

    def _settings_session_matches_target(self, session, scope: str, device_id: str):
        matcher = getattr(self, "_session_matches_device", None)
        if callable(matcher):
            try:
                return bool(matcher(session, scope, device_id))
            except Exception:
                pass
        data = session if isinstance(session, dict) else {}
        row_scope = str(data.get("device_scope") or "local").strip().lower()
        if row_scope not in ("local", "remote"):
            row_scope = "local"
        row_device_id = str(data.get("device_id") or "").strip() if row_scope == "remote" else "local"
        if str(scope or "local").strip().lower() != row_scope:
            return False
        if row_scope == "remote":
            return row_device_id == str(device_id or "").strip()
        return True

    def _archive_limit_target_key(self, device_scope="local", device_id="local"):
        scope = str(device_scope or "local").strip().lower()
        if scope not in ("local", "remote"):
            scope = "local"
        did = str(device_id or "").strip() if scope == "remote" else "local"
        if scope == "remote" and not did:
            scope = "local"
            did = "local"
        return f"{scope}:{did or 'local'}"

    def _archive_limit_root(self):
        bucket = self.cfg.get("session_archive_limits")
        if not isinstance(bucket, dict):
            bucket = {}
        legacy_like = bool(bucket) and all(not isinstance(value, dict) for value in bucket.values())
        if legacy_like:
            bucket = {"local:local": dict(bucket)}
        self.cfg["session_archive_limits"] = bucket
        return bucket

    def _archive_limit_bucket(self, device_scope="local", device_id="local"):
        root = self._archive_limit_root()
        key = self._archive_limit_target_key(device_scope=device_scope, device_id=device_id)
        bucket = root.get(key)
        if not isinstance(bucket, dict):
            bucket = {}
            root[key] = bucket
        return bucket

    def _archive_known_channel_ids(self, device_scope="local", device_id="local"):
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
                if not self._settings_session_matches_target(meta, device_scope, device_id):
                    continue
                cid = lz._normalize_usage_channel_id(meta.get("channel_id"), "launcher")
                if cid not in seen:
                    seen.add(cid)
                    ordered.append(cid)
        return ordered

    def _archive_channel_label(self, channel_id):
        return lz._usage_channel_label(channel_id)

    def _archive_limit_for_channel(self, channel_id, device_scope="local", device_id="local"):
        bucket = self._archive_limit_bucket(device_scope=device_scope, device_id=device_id)
        raw = bucket.get(channel_id, 10)
        try:
            value = int(raw)
        except Exception:
            value = 10
        return max(0, value)

    def _collect_archive_stats(self, device_scope="local", device_id="local"):
        active = {}
        if not lz.is_valid_agent_dir(self.agent_dir):
            return {"active": active}
        for meta in lz.list_sessions(self.agent_dir):
            if not self._settings_session_matches_target(meta, device_scope, device_id):
                continue
            cid = lz._normalize_usage_channel_id(meta.get("channel_id"), "launcher")
            active[cid] = active.get(cid, 0) + 1
        return {"active": active}

    def _trigger_settings_remote_session_sync(self, *, device_id="", on_done=None, include_all_channels=False, include_usage=False):
        did = str(device_id or "").strip()
        syncer = getattr(self, "_sync_remote_device_launcher_sessions", None)
        channel_syncer = getattr(self, "_sync_remote_device_channel_process_sessions", None)
        if callable(syncer) and not (include_all_channels or include_usage):
            try:
                syncer(force=True, device_id=did, trigger_refresh=False)
            except Exception:
                pass
        if callable(channel_syncer):
            try:
                channel_syncer()
            except Exception:
                pass
        blocking_sync = getattr(self, "_sync_remote_device_launcher_sessions_blocking", None)
        blocking_channel_sync = getattr(self, "_sync_remote_device_channel_process_sessions_blocking", None)
        if (not callable(blocking_sync)) and (not callable(blocking_channel_sync)):
            return

        def worker():
            try:
                if callable(blocking_sync):
                    blocking_sync(
                        force=True,
                        device_id=did,
                        include_all_channels=include_all_channels,
                        include_usage=include_usage,
                    )
            except Exception:
                pass
            try:
                if callable(blocking_channel_sync):
                    blocking_channel_sync()
            except Exception:
                pass
            if callable(on_done):
                try:
                    poster = getattr(self, "_api_on_ui_thread", None)
                    if callable(poster):
                        poster(on_done)
                    else:
                        QTimer.singleShot(0, on_done)
                except Exception:
                    pass

        threading.Thread(target=worker, name=f"settings-remote-sync-{did or 'all'}", daemon=True).start()

    def _reload_personal_panel(self):
        if not hasattr(self, "settings_personal_notice"):
            return
        self._reload_personal_preferences()
        self._reload_lan_interface_panel()
        self._clear_layout(self.settings_personal_list_layout)
        self._archive_limit_inputs = {}
        target = self._settings_data_target_context()
        scope_hint = getattr(self, "settings_personal_scope_hint", None)
        if scope_hint is not None:
            scope_hint.setText(
                f"当前正在调整 {target['label']} 的会话上限。下方“回复提醒”卡片不会跟随设备切换，始终只作用于当前这台启动器。"
            )
        if target["is_remote"] and (not getattr(self, "_settings_personal_remote_sync_running", False)):
            sync_key = self._settings_remote_sync_key(target, kind="personal")
            if str(getattr(self, "_settings_personal_remote_synced_key", "") or "") != sync_key:
                self._settings_personal_remote_sync_running = True
                self._settings_personal_remote_sync_key = sync_key
                token_getter = getattr(self, "_settings_target_generation", None)
                target_token = token_getter() if callable(token_getter) else 0
                self.settings_personal_notice.setText(f"正在同步 {target['label']} 的会话缓存，当前页面会在同步完成后自动刷新。")

                def done():
                    current_token = token_getter() if callable(token_getter) else target_token
                    if int(current_token or 0) != int(target_token or 0):
                        if str(getattr(self, "_settings_personal_remote_sync_key", "") or "") == sync_key:
                            self._settings_personal_remote_sync_running = False
                            self._settings_personal_remote_sync_key = ""
                        return
                    self._settings_personal_remote_sync_running = False
                    self._settings_personal_remote_sync_key = ""
                    self._settings_personal_remote_synced_key = sync_key
                    self._reload_personal_panel()

                self._trigger_settings_remote_session_sync(
                    device_id=target["device_id"],
                    on_done=done,
                    include_all_channels=True,
                )
                return
        elif target["is_remote"] and bool(getattr(self, "_settings_personal_remote_sync_running", False)):
            self.settings_personal_notice.setText(f"正在同步 {target['label']} 的会话缓存，当前页面会在同步完成后自动刷新。")
            return
        if not lz.is_valid_agent_dir(self.agent_dir):
            self.settings_personal_notice.setText("请先选择有效的 GenericAgent 目录。")
            return
        self.settings_personal_notice.setText(
            f"当前正在配置 {target['label']} 的会话上限。启动器会按渠道与设备分别统计并执行自动清理；超出上限时只删除该设备下最旧未收藏会话。"
        )
        stats = self._collect_archive_stats(target["scope"], target["device_id"])
        for cid in self._archive_known_channel_ids(target["scope"], target["device_id"]):
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
            spin.setValue(self._archive_limit_for_channel(cid, target["scope"], target["device_id"]))
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
        target = self._settings_data_target_context()
        bucket = self._archive_limit_bucket(target["scope"], target["device_id"])
        for cid, spin in self._archive_limit_inputs.items():
            bucket[cid] = int(spin.value() or 0)
        self.cfg["session_archive_limits"] = self._archive_limit_root()
        lz.save_config(self.cfg)
        removed = self._enforce_session_archive_limits(
            device_scope=target["scope"],
            device_id=target["device_id"],
            exclude_session_ids={((self.current_session or {}).get("id"))},
        )
        self._reload_personal_panel()
        self._reload_usage_panel()
        self._refresh_sessions()
        if removed:
            QMessageBox.information(self, "已保存", f"{target['label']} 的会话上限已保存，并已自动删除 {removed} 个旧会话。")
        else:
            QMessageBox.information(self, "已保存", f"{target['label']} 的会话上限已保存。当前没有触发新的自动清理。")

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

    def _usage_export_dir(self):
        path = lz.launcher_data_path("usage_exports")
        os.makedirs(path, exist_ok=True)
        return path

    def _usage_target_cache_dir(self):
        if not lz.is_valid_agent_dir(self.agent_dir):
            return ""
        return lz.sessions_dir(self.agent_dir)

    def _usage_target_safe_name(self, target):
        item = target if isinstance(target, dict) else {}
        label = str(item.get("label") or item.get("device_id") or "usage").strip() or "usage"
        safe = re.sub(r'[\\/:*?"<>|]+', "_", label)
        safe = re.sub(r"\s+", "_", safe).strip(" ._")
        return safe or "usage"

    def _usage_build_export_payload(self, stats, target, langfuse):
        data = dict(stats or {})
        item = target if isinstance(target, dict) else {}
        lang = langfuse if isinstance(langfuse, dict) else {}
        return {
            "target": {
                "label": str(item.get("label") or "本机"),
                "scope": str(item.get("scope") or "local"),
                "device_id": str(item.get("device_id") or "local"),
                "is_remote": bool(item.get("is_remote")),
            },
            "generated_at": time.time(),
            "summary": {
                "today": dict(data.get("today") or {}),
                "recent": dict(data.get("recent") or {}),
                "all": dict(data.get("all") or {}),
                "activity": dict(data.get("activity") or {}),
                "warnings": list(data.get("warnings") or []),
            },
            "channels": list(data.get("channels") or []),
            "models": list(data.get("models") or []),
            "sources": list(data.get("sources") or []),
            "timeline": list(data.get("timeline") or []),
            "sessions": list(data.get("sessions") or []),
            "days": list(data.get("days") or []),
            "langfuse": {
                "configured": bool(lang.get("configured")),
                "summary": str(lang.get("summary") or ""),
                "notes": list(lang.get("notes") or []),
            },
        }

    def _usage_write_export_files(self, stats, target, langfuse):
        payload = self._usage_build_export_payload(stats, target, langfuse)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = self._usage_target_safe_name(target)
        out_dir = self._usage_export_dir()
        base_name = f"usage_{safe_name}_{stamp}"
        json_path = os.path.join(out_dir, base_name + ".json")
        txt_path = os.path.join(out_dir, base_name + ".txt")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        lines = []
        target_info = payload["target"]
        summary = payload["summary"]
        activity = summary.get("activity") or {}
        lines.append(f"使用日志导出 - {target_info.get('label')}")
        lines.append(f"生成时间：{self._usage_time_label(payload.get('generated_at'))}")
        lines.append(f"目标类型：{'远程设备' if target_info.get('is_remote') else '本机'}")
        lines.append("")
        lines.append("概览")
        lines.append(
            f"- 今天：总 {self._usage_num((summary.get('today') or {}).get('total_tokens'))}，"
            f"输入 {self._usage_num((summary.get('today') or {}).get('input_tokens'))}，"
            f"输出 {self._usage_num((summary.get('today') or {}).get('output_tokens'))}"
        )
        lines.append(
            f"- 近 7 天：总 {self._usage_num((summary.get('recent') or {}).get('total_tokens'))}，"
            f"调用 {self._usage_num((summary.get('recent') or {}).get('api_calls'))}"
        )
        lines.append(
            f"- 累计：总 {self._usage_num((summary.get('all') or {}).get('total_tokens'))}，"
            f"调用 {self._usage_num(activity.get('api_calls'))}"
        )
        lines.append(
            f"- 活跃会话：{self._usage_num(activity.get('sessions_with_events'))} / {self._usage_num(activity.get('session_count'))}"
        )
        lines.append("")
        warnings = list(summary.get("warnings") or [])
        lines.append("提醒")
        if warnings:
            for item in warnings:
                lines.append(f"- {item}")
        else:
            lines.append("- 无")
        lines.append("")
        lines.append("按渠道")
        for row in (payload.get("channels") or [])[:10]:
            lines.append(
                f"- {row.get('label')}: 总 {self._usage_num(row.get('total_tokens'))} / 轮次 {self._usage_num(row.get('turns'))} / 会话 {self._usage_num(row.get('sessions'))}"
            )
        if not (payload.get("channels") or []):
            lines.append("- 无")
        lines.append("")
        lines.append("按模型")
        for row in (payload.get("models") or [])[:10]:
            lines.append(
                f"- {row.get('model')}: 总 {self._usage_num(row.get('total_tokens'))} / 会话 {self._usage_num(row.get('sessions'))} / 调用 {self._usage_num(row.get('api_calls'))}"
            )
        if not (payload.get("models") or []):
            lines.append("- 无")
        lines.append("")
        lines.append("最近活动")
        for row in (payload.get("timeline") or [])[:12]:
            lines.append(
                f"- {self._usage_time_label(row.get('ts'))} | {row.get('channel_label')} | {row.get('session_title')} | {row.get('model')} | {self._usage_num(row.get('total_tokens'))}"
            )
        if not (payload.get("timeline") or []):
            lines.append("- 无")
        lines.append("")
        lines.append("高消耗会话")
        for row in (payload.get("sessions") or [])[:12]:
            lines.append(
                f"- {row.get('title')} | {row.get('channel_label')} | 总 {self._usage_num(row.get('total_tokens'))} | 最近 {row.get('last_model')} | {self._usage_time_label(row.get('last_active'))}"
            )
        if not (payload.get("sessions") or []):
            lines.append("- 无")
        lines.append("")
        lang = payload.get("langfuse") or {}
        lines.append("Langfuse")
        lines.append(f"- 已配置：{'是' if lang.get('configured') else '否'}")
        lines.append(f"- 状态：{lang.get('summary') or '无'}")
        for item in (lang.get("notes") or [])[:6]:
            lines.append(f"- {item}")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines).rstrip() + "\n")
        return {"txt_path": txt_path, "json_path": json_path, "dir": out_dir}

    def _usage_export_current_report(self, stats, target, langfuse):
        try:
            files = self._usage_write_export_files(stats, target, langfuse)
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        txt_path = str(files.get("txt_path") or "").strip()
        json_path = str(files.get("json_path") or "").strip()
        self.settings_usage_notice.setText(f"已导出当前设备的使用摘要：{txt_path}")
        if txt_path:
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(txt_path))
            if not opened:
                QMessageBox.information(self, "导出完成", f"摘要文件：\n{txt_path}\n\n明细 JSON：\n{json_path}")

    def _usage_open_cache_dir(self):
        target_dir = self._usage_target_cache_dir()
        if not target_dir:
            QMessageBox.warning(self, "目录无效", "当前还没有可打开的会话缓存目录。")
            return
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(target_dir))
        if not opened:
            QMessageBox.warning(self, "打开失败", f"无法打开目录：\n{target_dir}")

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

    def _collect_usage_stats(self, lookback_days=7, *, device_scope="local", device_id="local"):
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
            if not self._settings_session_matches_target(meta, device_scope, device_id):
                continue
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
        target = self._settings_data_target_context()
        if target["is_remote"] and (not getattr(self, "_settings_usage_remote_sync_running", False)):
            sync_key = self._settings_remote_sync_key(target, kind="usage")
            if str(getattr(self, "_settings_usage_remote_synced_key", "") or "") != sync_key:
                self._settings_usage_remote_sync_running = True
                self._settings_usage_remote_sync_key = sync_key
                token_getter = getattr(self, "_settings_target_generation", None)
                target_token = token_getter() if callable(token_getter) else 0
                self.settings_usage_notice.setText(f"正在同步 {target['label']} 的远端使用日志、会话与渠道快照，完成后会自动刷新。")

                def done():
                    current_token = token_getter() if callable(token_getter) else target_token
                    if int(current_token or 0) != int(target_token or 0):
                        if str(getattr(self, "_settings_usage_remote_sync_key", "") or "") == sync_key:
                            self._settings_usage_remote_sync_running = False
                            self._settings_usage_remote_sync_key = ""
                        return
                    self._settings_usage_remote_sync_running = False
                    self._settings_usage_remote_sync_key = ""
                    self._settings_usage_remote_synced_key = sync_key
                    self._reload_usage_panel()

                self._trigger_settings_remote_session_sync(
                    device_id=target["device_id"],
                    on_done=done,
                    include_all_channels=True,
                    include_usage=True,
                )
                return
        elif target["is_remote"] and bool(getattr(self, "_settings_usage_remote_sync_running", False)):
            self.settings_usage_notice.setText(f"正在同步 {target['label']} 的远端使用日志、会话与渠道快照，完成后会自动刷新。")
            return
        if not lz.is_valid_agent_dir(self.agent_dir):
            self.settings_usage_notice.setText("请先选择有效的 GenericAgent 目录。")
            return

        stats = self._collect_usage_stats(lookback_days=7, device_scope=target["scope"], device_id=target["device_id"])
        langfuse = self._load_langfuse_status()
        self.settings_usage_notice.setText(
            f"当前展示目标：{target['label']}。本页优先展示该设备的 usage 摘要、渠道分布和最近活动；旧会话或不返回 usage 的渠道，仍可能只能显示估算。"
        )

        actions_card = self._panel_card()
        actions_box = QVBoxLayout(actions_card)
        actions_box.setContentsMargins(14, 12, 14, 12)
        actions_box.setSpacing(8)
        actions_title = QLabel("常用操作")
        actions_title.setObjectName("cardTitle")
        actions_box.addWidget(actions_title)
        actions_desc = QLabel("可以把当前设备的使用摘要导出到本地文件，也可以直接打开当前会话缓存目录。")
        actions_desc.setWordWrap(True)
        actions_desc.setObjectName("cardDesc")
        actions_box.addWidget(actions_desc)
        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)
        export_btn = QPushButton("导出当前摘要")
        export_btn.setStyleSheet(self._action_button_style(primary=True))
        export_btn.clicked.connect(lambda: self._usage_export_current_report(stats, target, langfuse))
        actions_row.addWidget(export_btn, 0)
        cache_btn = QPushButton("打开会话缓存")
        cache_btn.setStyleSheet(self._action_button_style())
        cache_btn.clicked.connect(self._usage_open_cache_dir)
        actions_row.addWidget(cache_btn, 0)
        actions_row.addStretch(1)
        actions_box.addLayout(actions_row)
        self.settings_usage_list_layout.addWidget(actions_card)

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
        self.settings_about_auto_fetch_kernel = QCheckBox("检测内核更新时自动同步远端引用（git fetch）")
        self.settings_about_auto_fetch_kernel.setChecked(bool(self.cfg.get("kernel_update_auto_fetch_enabled", True)))
        self.settings_about_auto_fetch_kernel.toggled.connect(self._on_toggle_kernel_update_auto_fetch)
        update_box.addWidget(self.settings_about_auto_fetch_kernel)
        update_action_row = QHBoxLayout()
        update_action_row.setSpacing(8)
        self.settings_about_check_updates_btn = QPushButton("立即检测 GitHub 更新")
        self.settings_about_check_updates_btn.setStyleSheet(self._action_button_style(primary=True))
        self.settings_about_check_updates_btn.clicked.connect(lambda: self._start_update_check(manual=True))
        update_action_row.addWidget(self.settings_about_check_updates_btn, 0)
        self.settings_about_install_update_btn = QPushButton("安装更新并重启")
        self.settings_about_install_update_btn.setStyleSheet(self._action_button_style())
        self.settings_about_install_update_btn.clicked.connect(self._start_launcher_update_install)
        update_action_row.addWidget(self.settings_about_install_update_btn, 0)
        update_action_row.addStretch(1)
        update_box.addLayout(update_action_row)
        self.settings_about_list_layout.addWidget(update_card)

        diag_card = self._panel_card()
        diag_box = QVBoxLayout(diag_card)
        diag_box.setContentsMargins(14, 12, 14, 12)
        diag_box.setSpacing(8)
        diag_title = QLabel("更新诊断")
        diag_title.setObjectName("cardTitle")
        diag_box.addWidget(diag_title)
        diag_desc = QLabel("用于排查更新异常，包含最近任务状态、错误码、updater.log 尾部日志，以及内核仓库同步操作。")
        diag_desc.setWordWrap(True)
        diag_desc.setObjectName("cardDesc")
        diag_box.addWidget(diag_desc)
        self.settings_about_update_diag_status = QLabel("")
        self.settings_about_update_diag_status.setWordWrap(True)
        self.settings_about_update_diag_status.setObjectName("mutedText")
        self.settings_about_update_diag_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        diag_box.addWidget(self.settings_about_update_diag_status)
        diag_actions = QHBoxLayout()
        diag_actions.setSpacing(8)
        refresh_diag_btn = QPushButton("刷新诊断信息")
        refresh_diag_btn.setStyleSheet(self._action_button_style())
        refresh_diag_btn.clicked.connect(self._refresh_about_update_diagnostics_manual)
        diag_actions.addWidget(refresh_diag_btn, 0)
        self.settings_about_sync_kernel_fetch_btn = QPushButton("同步内核远端（fetch）")
        self.settings_about_sync_kernel_fetch_btn.setStyleSheet(self._action_button_style())
        self.settings_about_sync_kernel_fetch_btn.clicked.connect(self._sync_kernel_repo_fetch)
        diag_actions.addWidget(self.settings_about_sync_kernel_fetch_btn, 0)
        self.settings_about_sync_kernel_pull_btn = QPushButton("拉取并快进（pull --ff-only）")
        self.settings_about_sync_kernel_pull_btn.setStyleSheet(self._action_button_style())
        self.settings_about_sync_kernel_pull_btn.clicked.connect(self._sync_kernel_repo_pull)
        diag_actions.addWidget(self.settings_about_sync_kernel_pull_btn, 0)
        diag_actions.addStretch(1)
        diag_box.addLayout(diag_actions)
        self.settings_about_list_layout.addWidget(diag_card)

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
            ("启动器版本", str(lz.current_launcher_version())),
            ("启动器仓库", launcher_repo_url),
            ("agant 内核仓库", lz.REPO_URL),
            ("当前配置文件", lz.CONFIG_PATH),
            ("用户数据目录", lz.DATA_ROOT),
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
