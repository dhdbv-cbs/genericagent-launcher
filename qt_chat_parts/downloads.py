from __future__ import annotations

import os
import platform
import shutil
import subprocess
import threading
import time
from datetime import datetime
from urllib.parse import urlparse

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QFileDialog, QMessageBox

from launcher_app import core as lz

from .common import PRIVATE_PYTHON_VERSION


class DownloadMixin:
    def _append_download_log(self, message: str):
        box = getattr(self, "download_log", None)
        text = str(message or "").rstrip()
        if box is None or not text:
            return
        box.append(text)
        box.moveCursor(QTextCursor.End)

    def _refresh_download_state(self):
        if hasattr(self, "download_parent_label"):
            target = os.path.join(self.install_parent, "GenericAgent") if self.install_parent else "未选择安装位置"
            self.download_parent_label.setText(f"安装位置：{self.install_parent}\n目标目录：{target}")
        if hasattr(self, "download_parent_value"):
            self.download_parent_value.setText(self.install_parent or "未选择安装位置")
        if hasattr(self, "download_btn"):
            self.download_btn.setEnabled(not self._download_running)
            self.download_btn.setText("下载中…" if self._download_running and self._download_mode == "clone" else "开始下载")
        if hasattr(self, "download_private_btn"):
            self.download_private_btn.setEnabled(not self._download_running)
            self.download_private_btn.setText(
                "构建中…" if self._download_running and self._download_mode == "private_python" else "下载并配置 3.12 虚拟环境"
            )
        if hasattr(self, "download_private_only_checkbox"):
            self.download_private_only_checkbox.setEnabled(not self._download_running)
        if hasattr(self, "download_source_checkboxes"):
            for cb in (self.download_source_checkboxes or {}).values():
                cb.setEnabled(not self._download_running)
        if hasattr(self, "download_progress"):
            if self._download_running:
                self.download_progress.setRange(0, 0)
            else:
                self.download_progress.setRange(0, 1)
                self.download_progress.setValue(0)

    def _choose_install_parent(self):
        path = QFileDialog.getExistingDirectory(
            self,
            "选择安装位置",
            self.install_parent or os.path.expanduser("~"),
        )
        if path:
            self.install_parent = path
            self.cfg["install_parent"] = path
            lz.save_config(self.cfg)
            self._refresh_download_state()

    def _private_python_source_cfg_key(self):
        return "private_python_download_source_ids"

    def _private_python_source_ui_options(self):
        spec, _ = self._private_python_spec()
        raw_sources = (spec or {}).get("sources") or []
        options = []
        for item in raw_sources:
            source_id = str(item.get("id") or "").strip()
            label = str(item.get("name") or source_id).strip()
            if not source_id:
                continue
            options.append({"id": source_id, "label": label})
        if options:
            return options
        return [
            {"id": "official", "label": "官方源（python.org）"},
            {"id": "huawei", "label": "华为云镜像"},
            {"id": "nju", "label": "南京大学镜像"},
            {"id": "bfsu", "label": "北外镜像"},
            {"id": "tsinghua", "label": "清华镜像"},
            {"id": "ustc", "label": "中科大镜像（部分网络可能触发验证）"},
        ]

    def _private_python_selected_source_ids(self, available_ids):
        ids = [str(item).strip() for item in (available_ids or []) if str(item).strip()]
        if not ids:
            return []
        raw = self.cfg.get(self._private_python_source_cfg_key())
        if isinstance(raw, list):
            selected = [str(item).strip() for item in raw if str(item).strip() in ids]
            if selected:
                return selected
        return list(ids)

    def _sync_private_python_source_checkboxes_from_cfg(self):
        checks = getattr(self, "download_source_checkboxes", None) or {}
        if not checks:
            return
        ids = [sid for sid in checks.keys()]
        selected_ids = self._private_python_selected_source_ids(ids)
        for sid, cb in checks.items():
            cb.blockSignals(True)
            cb.setChecked(sid in selected_ids)
            cb.blockSignals(False)

    def _on_private_python_source_toggled(self, source_id=None):
        checks = getattr(self, "download_source_checkboxes", None) or {}
        if not checks:
            return
        selected = [sid for sid, cb in checks.items() if cb.isChecked()]
        if not selected:
            fallback = source_id if source_id in checks else next(iter(checks.keys()), None)
            if fallback is not None:
                cb = checks[fallback]
                cb.blockSignals(True)
                cb.setChecked(True)
                cb.blockSignals(False)
                selected = [fallback]
            QMessageBox.information(self, "至少选择一个下载源", "请至少勾选一个 Python 下载源。")
        self.cfg[self._private_python_source_cfg_key()] = list(selected)
        lz.save_config(self.cfg)

    def _private_python_selected_sources(self, spec):
        sources = []
        for item in (spec or {}).get("sources") or []:
            source_id = str(item.get("id") or "").strip()
            url = str(item.get("url") or "").strip()
            if not source_id or not url:
                continue
            sources.append({"id": source_id, "name": str(item.get("name") or source_id).strip(), "url": url})
        if not sources:
            urls = [str(item).strip() for item in ((spec or {}).get("urls") or []) if str(item).strip()]
            return [
                {"id": f"url_{idx}", "name": self._download_source_label(url), "url": url}
                for idx, url in enumerate(urls, start=1)
            ]

        order = [item["id"] for item in sources]
        checks = getattr(self, "download_source_checkboxes", None) or {}
        if checks:
            ui_selected = [sid for sid, cb in checks.items() if cb.isChecked()]
            selected_ids = [sid for sid in ui_selected if sid in order]
            if selected_ids:
                self.cfg[self._private_python_source_cfg_key()] = list(selected_ids)
                lz.save_config(self.cfg)
            else:
                selected_ids = self._private_python_selected_source_ids(order)
        else:
            selected_ids = self._private_python_selected_source_ids(order)

        by_id = {item["id"]: item for item in sources}
        picked = [by_id[sid] for sid in selected_ids if sid in by_id]
        return picked if picked else list(sources)

    def _start_download_repo(self, private_python=False, private_only=False):
        parent = str(self.install_parent or "").strip()
        if not parent or not os.path.isdir(parent):
            QMessageBox.warning(self, "位置无效", "请选择有效的安装位置。")
            return
        if hasattr(self, "download_log"):
            self.download_log.clear()
            self._append_download_log(f"[{datetime.now().strftime('%H:%M:%S')}] 准备开始下载")
        target = os.path.join(parent, "GenericAgent")
        if private_python and private_only and not os.path.exists(target):
            QMessageBox.warning(self, "目录不存在", "你勾选了“仅配置虚拟环境”，但目标目录里还没有 GenericAgent。\n\n请先下载原项目，或取消该勾选。")
            self._append_download_log(f"[{datetime.now().strftime('%H:%M:%S')}] 仅配置虚拟环境失败：目标目录不存在 {target}")
            return
        if os.path.exists(target):
            if QMessageBox.question(self, "目录已存在", f"{target}\n\n已存在。是否直接使用它作为 GenericAgent 目录？") != QMessageBox.Yes:
                return
            if lz.is_valid_agent_dir(target):
                if not private_python:
                    self._set_agent_dir(target)
                    self.download_status_label.setText("已使用现有目录。现在可以直接进入聊天。")
                    self._append_download_log(f"[{datetime.now().strftime('%H:%M:%S')}] 目录已存在，已直接接管：{target}")
                    return
                self._append_download_log(f"[{datetime.now().strftime('%H:%M:%S')}] 目录已存在，将继续为它配置私有 3.12 虚拟环境：{target}")
            else:
                QMessageBox.warning(self, "目录无效", "该目录存在但不是有效的 GenericAgent 目录。")
                self._append_download_log(f"[{datetime.now().strftime('%H:%M:%S')}] 目录存在，但不是有效的 GenericAgent 根目录：{target}")
                return
        elif private_python and private_only:
            QMessageBox.warning(self, "目录不存在", "你勾选了“仅配置虚拟环境”，但目标目录里还没有 GenericAgent。\n\n请先下载原项目，或取消该勾选。")
            self._append_download_log(f"[{datetime.now().strftime('%H:%M:%S')}] 仅配置虚拟环境失败：目标目录不存在 {target}")
            return
        self._download_running = True
        self._download_mode = "private_python" if private_python else "clone"
        self._refresh_download_state()
        if private_python and private_only:
            self.download_status_label.setText("正在为现有 GenericAgent 配置私有 3.12 环境…")
        else:
            self.download_status_label.setText("正在准备私有 3.12 环境…" if private_python else "正在检查 Git 并开始下载…")
        self._append_download_log(f"[{datetime.now().strftime('%H:%M:%S')}] 目标目录：{target}")
        self.cfg["install_parent"] = parent
        lz.save_config(self.cfg)
        threading.Thread(target=self._run_clone_repo, args=(target, private_python, private_only), daemon=True).start()

    def _private_python_spec(self):
        machine = (platform.machine() or "").lower()
        if machine in ("amd64", "x86_64", "x64", ""):
            arch = "amd64"
        elif "arm64" in machine or "aarch64" in machine:
            arch = "arm64"
        else:
            return None, f"当前机器架构 {platform.machine() or 'unknown'} 暂未接入私有 Python 3.12 自动安装。"
        filename = f"python-{PRIVATE_PYTHON_VERSION}-{arch}.exe"
        official_url = f"https://www.python.org/ftp/python/{PRIVATE_PYTHON_VERSION}/{filename}"
        custom_url = str(os.environ.get("GA_PYTHON_INSTALLER_URL") or "").strip()
        if custom_url:
            try:
                custom_url = custom_url.format(version=PRIVATE_PYTHON_VERSION, filename=filename)
            except Exception:
                pass
        sources = []
        if custom_url:
            sources.append(
                {
                    "id": "custom",
                    "name": "自定义源（GA_PYTHON_INSTALLER_URL）",
                    "url": custom_url,
                }
            )
        sources.extend(
            [
                {
                    "id": "official",
                    "name": "官方源（python.org）",
                    "url": official_url,
                },
                {
                    "id": "huawei",
                    "name": "华为云镜像",
                    "url": f"https://mirrors.huaweicloud.com/python/{PRIVATE_PYTHON_VERSION}/{filename}",
                },
                {
                    "id": "nju",
                    "name": "南京大学镜像",
                    "url": f"https://mirror.nju.edu.cn/python/{PRIVATE_PYTHON_VERSION}/{filename}",
                },
                {
                    "id": "bfsu",
                    "name": "北外镜像",
                    "url": f"https://mirrors.bfsu.edu.cn/python/{PRIVATE_PYTHON_VERSION}/{filename}",
                },
                {
                    "id": "tsinghua",
                    "name": "清华镜像",
                    "url": f"https://mirrors.tuna.tsinghua.edu.cn/python/{PRIVATE_PYTHON_VERSION}/{filename}",
                },
                {
                    "id": "ustc",
                    "name": "中科大镜像（部分网络可能触发验证）",
                    "url": f"https://mirrors.ustc.edu.cn/python/{PRIVATE_PYTHON_VERSION}/{filename}",
                },
            ]
        )
        dedup_sources = []
        dedup_urls = []
        seen = set()
        for item in sources:
            key = str(item.get("url") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            dedup_sources.append(item)
            dedup_urls.append(key)
        return {
            "version": PRIVATE_PYTHON_VERSION,
            "arch": arch,
            "filename": filename,
            "url": official_url,
            "urls": dedup_urls,
            "sources": dedup_sources,
        }, None

    def _private_runtime_paths(self, target):
        root = os.path.join(target, ".launcher_runtime")
        python_root = os.path.join(root, "python312")
        venv_root = os.path.join(root, "venv312")
        downloads_root = os.path.join(root, "downloads")
        python_exe = os.path.join(python_root, "python.exe" if os.name == "nt" else "bin/python")
        venv_python = os.path.join(venv_root, "Scripts", "python.exe") if os.name == "nt" else os.path.join(venv_root, "bin", "python")
        return {
            "root": root,
            "python_root": python_root,
            "venv_root": venv_root,
            "downloads_root": downloads_root,
            "python_exe": python_exe,
            "venv_python": venv_python,
        }

    def _probe_python_version_prefix(self, py_path):
        try:
            result = subprocess.run(
                [py_path, "-c", "import sys;print(sys.version.split()[0])"],
                capture_output=True,
                text=True,
                timeout=15,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception:
            return ""
        if result.returncode != 0:
            return ""
        return str((result.stdout or "").strip().splitlines()[-1] if (result.stdout or "").strip() else "")

    def _private_python_candidate_paths(self, paths):
        candidates = []
        seen = set()

        def add(path):
            raw = str(path or "").strip()
            if not raw:
                return
            norm = os.path.normcase(os.path.normpath(raw))
            if norm in seen:
                return
            seen.add(norm)
            candidates.append(raw)

        add((paths or {}).get("python_exe"))
        root = str((paths or {}).get("python_root") or "").strip()
        if root:
            add(os.path.join(root, "python.exe"))
            add(os.path.join(root, "bin", "python"))
            add(os.path.join(root, "Scripts", "python.exe"))
            add(os.path.join(root, "python312", "python.exe"))
            add(os.path.join(root, "python", "python.exe"))
            try:
                base_depth = root.count(os.sep)
                for dirpath, dirnames, filenames in os.walk(root):
                    if dirpath.count(os.sep) - base_depth > 4:
                        dirnames[:] = []
                        continue
                    for name in filenames:
                        if name.lower() == "python.exe":
                            add(os.path.join(dirpath, name))
                    if len(candidates) >= 30:
                        break
            except Exception:
                pass

        if os.name == "nt":
            local_appdata = str(os.environ.get("LOCALAPPDATA") or "").strip()
            if local_appdata:
                add(os.path.join(local_appdata, "Programs", "Python", "Python312", "python.exe"))
                add(os.path.join(local_appdata, "Programs", "Python", "Python312-32", "python.exe"))

        return candidates

    def _resolve_private_python_exe(self, paths, *, wait_seconds=0):
        try:
            remain = float(wait_seconds or 0)
        except Exception:
            remain = 0.0
        deadline = time.time() + max(remain, 0.0)
        scanned = []

        while True:
            candidates = self._private_python_candidate_paths(paths)
            scanned = candidates
            for item in candidates:
                if not os.path.isfile(item):
                    continue
                version = self._probe_python_version_prefix(item)
                if version.startswith("3.12"):
                    return item, version, scanned
            if time.time() >= deadline:
                break
            time.sleep(1.0)

        return "", "", scanned

    def _read_tail_text(self, path, max_lines=20):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            return "".join(lines[-max_lines:]).strip()
        except Exception:
            return ""

    def _is_probably_valid_installer(self, path):
        try:
            if not os.path.isfile(path):
                return False
            # 避免复用明显异常的残缺安装包。
            if os.path.getsize(path) < 8 * 1024 * 1024:
                return False
            with open(path, "rb") as f:
                return f.read(2) == b"MZ"
        except Exception:
            return False

    def _download_source_label(self, url):
        try:
            info = urlparse(str(url))
            host = (info.netloc or "").strip()
            if host:
                return host
        except Exception:
            pass
        return str(url)

    def _download_request_headers(self, *, probe=False):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "*/*",
        }
        if probe:
            headers["Range"] = "bytes=0-65535"
        return headers

    def _probe_download_source(self, source):
        src = dict(source or {})
        name = str(src.get("name") or src.get("id") or "").strip() or "下载源"
        url = str(src.get("url") or "").strip()
        if not url:
            return {"ok": False, "name": name, "url": url, "error": "缺少 URL"}

        start_ts = time.time()
        try:
            with lz.requests.get(
                url,
                stream=True,
                timeout=(5, 15),
                headers=self._download_request_headers(probe=True),
            ) as resp:
                resp.raise_for_status()
                ctype = str(resp.headers.get("content-type") or "").lower()
                if "text/html" in ctype:
                    raise RuntimeError("返回 HTML 页面（疑似验证/反爬）")
                first = b""
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        first = chunk
                        break
                if not first:
                    raise RuntimeError("未读取到有效内容")
                if first[:2] != b"MZ":
                    preview = first[:120].decode("utf-8", errors="ignore").lower()
                    if "html" in preview or "verify" in preview or "javascript" in preview:
                        raise RuntimeError("返回验证页而非 exe")
                elapsed = max(time.time() - start_ts, 0.001)
                speed_mb = len(first) / elapsed / (1024 * 1024)
                return {
                    "ok": True,
                    "name": name,
                    "url": url,
                    "latency_ms": int(elapsed * 1000),
                    "probe_speed_mb": speed_mb,
                }
        except Exception as e:
            return {
                "ok": False,
                "name": name,
                "url": url,
                "error": str(e),
            }

    def _rank_download_sources(self, sources, label):
        entries = []
        for item in (sources or []):
            sid = str(item.get("id") or "").strip()
            name = str(item.get("name") or sid).strip() or sid
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            entries.append({"id": sid, "name": name, "url": url})
        if not entries:
            return []
        if len(entries) == 1:
            return entries

        self._event_queue.put({"event": "clone_status", "msg": f"{label}：正在预检下载源可用性…"})
        probe_rows = []
        for item in entries:
            probe = self._probe_download_source(item)
            row = dict(item)
            row.update(probe)
            probe_rows.append(row)
            if probe.get("ok"):
                self._event_queue.put(
                    {
                        "event": "clone_status",
                        "msg": (
                            f"下载源预检通过：{item['name']} "
                            f"({probe.get('latency_ms', 0)} ms, {float(probe.get('probe_speed_mb', 0.0)):.2f} MB/s)"
                        ),
                    }
                )
            else:
                self._event_queue.put(
                    {
                        "event": "clone_status",
                        "msg": f"下载源预检失败：{item['name']}（{probe.get('error') or '未知错误'}）",
                    }
                )

        passed = [row for row in probe_rows if row.get("ok")]
        failed = [row for row in probe_rows if not row.get("ok")]
        if passed:
            passed.sort(key=lambda r: (int(r.get("latency_ms") or 999999), -float(r.get("probe_speed_mb") or 0.0)))
            ordered = passed + failed
            self._event_queue.put(
                {
                    "event": "clone_status",
                    "msg": "下载源尝试顺序（预检后）: " + "、".join([str(r.get("name") or r.get("id") or "") for r in ordered]),
                }
            )
            return ordered
        self._event_queue.put({"event": "clone_status", "msg": "下载源预检均失败，将按勾选顺序继续尝试。"})
        return entries

    def _download_to_file(self, url_or_urls, dest, label):
        if isinstance(url_or_urls, (list, tuple)):
            candidates = [str(item or "").strip() for item in url_or_urls if str(item or "").strip()]
        else:
            single = str(url_or_urls or "").strip()
            candidates = [single] if single else []
        dedup = []
        seen = set()
        for item in candidates:
            if item in seen:
                continue
            seen.add(item)
            dedup.append(item)
        candidates = dedup
        if not candidates:
            raise RuntimeError(f"{label}失败：没有可用的下载地址")

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        temp_dest = dest + ".part"
        errors = []

        for idx, url in enumerate(candidates, start=1):
            source = self._download_source_label(url)
            self._event_queue.put(
                {
                    "event": "clone_status",
                    "msg": f"{label}：尝试下载源 {idx}/{len(candidates)}（{source}）",
                }
            )
            start_ts = time.time()
            last_report_ts = start_ts
            last_report_progress = -1

            try:
                if os.path.isfile(temp_dest):
                    os.remove(temp_dest)
                with lz.requests.get(
                    url,
                    stream=True,
                    timeout=(12, 600),
                    headers=self._download_request_headers(probe=False),
                ) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length") or 0)
                    ctype = str(resp.headers.get("content-type") or "").lower()
                    if "text/html" in ctype:
                        raise RuntimeError("返回 HTML 页面而不是安装包，可能触发了镜像站防爬/验证机制")
                    downloaded = 0
                    first_chunk = True
                    with open(temp_dest, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=1024 * 1024):
                            if not chunk:
                                continue
                            if first_chunk:
                                first_chunk = False
                                if chunk[:2] != b"MZ":
                                    # Windows 安装器应以 MZ 开头；否则大概率拿到了错误页面。
                                    preview = chunk[:120].decode("utf-8", errors="ignore").lower()
                                    if "html" in preview or "verify" in preview or "javascript" in preview:
                                        raise RuntimeError("下载内容疑似验证页面而非 exe，请切换镜像源")
                            f.write(chunk)
                            downloaded += len(chunk)
                            now = time.time()
                            elapsed = max(now - start_ts, 0.001)
                            speed_mb = downloaded / elapsed / (1024 * 1024)
                            if total > 0:
                                progress = int(downloaded * 100 / total)
                                if (
                                    progress >= last_report_progress + 5
                                    or downloaded == total
                                    or (now - last_report_ts) >= 2.5
                                ):
                                    last_report_progress = progress
                                    last_report_ts = now
                                    self._event_queue.put(
                                        {
                                            "event": "clone_status",
                                            "msg": (
                                                f"{label}：{progress}% "
                                                f"({downloaded // (1024 * 1024)} / {max(total // (1024 * 1024), 1)} MB, "
                                                f"{speed_mb:.2f} MB/s)"
                                            ),
                                        }
                                    )
                            elif (now - last_report_ts) >= 2.5:
                                last_report_ts = now
                                self._event_queue.put(
                                    {
                                        "event": "clone_status",
                                        "msg": f"{label}：已下载 {downloaded // (1024 * 1024)} MB（{speed_mb:.2f} MB/s）",
                                    }
                                )
                    if total > 0 and downloaded != total:
                        raise RuntimeError(f"下载不完整：期望 {total} 字节，实际 {downloaded} 字节")

                os.replace(temp_dest, dest)
                elapsed = max(time.time() - start_ts, 0.001)
                avg_speed = os.path.getsize(dest) / elapsed / (1024 * 1024)
                self._event_queue.put(
                    {
                        "event": "clone_status",
                        "msg": f"{label}：下载完成（源 {source}，平均 {avg_speed:.2f} MB/s）",
                    }
                )
                return
            except Exception as e:
                errors.append(f"{source}: {e}")
                try:
                    if os.path.isfile(temp_dest):
                        os.remove(temp_dest)
                except Exception:
                    pass
                if idx < len(candidates):
                    self._event_queue.put(
                        {
                            "event": "clone_status",
                            "msg": f"{label}：下载源 {source} 失败，正在切换下一个源…",
                        }
                    )

        raise RuntimeError(
            f"{label}失败：已尝试 {len(candidates)} 个下载源。"
            + ("\n" + "\n".join(errors[-3:]) if errors else "")
        )

    def _run_checked_command(self, args, *, cwd=None, timeout=1200, log_path=None, label=""):
        result = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode == 0:
            return True, ""
        exit_code_text = f"{result.returncode} (0x{(int(result.returncode) & 0xFFFFFFFF):08X})"
        header = f"{label or '命令'}失败，退出码 {exit_code_text}"
        detail = (result.stderr or result.stdout or "").strip()
        if log_path and os.path.isfile(log_path):
            tail = self._read_tail_text(log_path)
            if tail:
                detail = tail
        if detail:
            lines = [line.strip() for line in detail.splitlines() if line.strip()]
            detail = "\n".join(lines[-12:]) if lines else detail
            detail = f"{header}\n{detail}"
        else:
            detail = header
        if int(result.returncode) == 0x570:
            detail += (
                "\n提示：检测到 Windows 退出码 0x570。"
                "这通常表示安装包损坏，或目标盘目录状态异常。"
                "启动器已自动尝试重新下载安装包；若仍失败，请检查目标盘健康状态，"
                "并删除 GenericAgent/.launcher_runtime/downloads 后重试。"
            )
        return False, detail

    def _ensure_private_python_env(self, target):
        spec, spec_err = self._private_python_spec()
        if spec_err:
            return None, spec_err
        paths = self._private_runtime_paths(target)
        os.makedirs(paths["root"], exist_ok=True)
        os.makedirs(paths["downloads_root"], exist_ok=True)

        python_exe, python_version, _ = self._resolve_private_python_exe(paths, wait_seconds=0)
        if python_exe:
            paths["python_exe"] = python_exe
        if not python_version.startswith("3.12"):
            installer_path = os.path.join(paths["downloads_root"], spec["filename"])
            install_log = os.path.join(paths["downloads_root"], "python-install.log")
            selected_sources = self._private_python_selected_sources(spec)
            selected_names = [str(item.get("name") or item.get("id") or "").strip() for item in selected_sources]
            if selected_names:
                self._event_queue.put({"event": "clone_status", "msg": f"当前下载源选择：{'、'.join(selected_names)}"})
            ranked_sources = self._rank_download_sources(selected_sources, f"下载 Python {spec['version']} 安装包")
            selected_urls = [item["url"] for item in ranked_sources if str(item.get("url") or "").strip()]
            install_ok = False
            install_detail = ""
            for attempt in range(2):
                if not self._is_probably_valid_installer(installer_path):
                    if attempt > 0:
                        self._event_queue.put({"event": "clone_status", "msg": "检测到安装失败，正在重新下载安装包后重试一次…"})
                    self._download_to_file(selected_urls or spec.get("urls") or [spec["url"]], installer_path, f"下载 Python {spec['version']} 安装包")
                else:
                    self._event_queue.put({"event": "clone_status", "msg": f"复用已下载的 Python 安装包：{installer_path}"})

                if attempt > 0 and os.path.isdir(paths["python_root"]):
                    shutil.rmtree(paths["python_root"], ignore_errors=True)

                self._event_queue.put({"event": "clone_status", "msg": "正在安装私有 Python 3.12（静默安装，可能需要 1-3 分钟，不会修改系统 PATH）…"})
                ok, detail = self._run_checked_command(
                    [
                        installer_path,
                        "/quiet",
                        "/log",
                        install_log,
                        "InstallAllUsers=0",
                        f"TargetDir={paths['python_root']}",
                        "PrependPath=0",
                        "Include_launcher=0",
                        "Include_test=0",
                        "Include_pip=1",
                        "Include_venv=1",
                        "Include_tcltk=0",
                        "Include_doc=0",
                        "Include_dev=0",
                        "Include_symbols=0",
                        "Include_debug=0",
                        "AssociateFiles=0",
                        "Shortcuts=0",
                        "SimpleInstall=1",
                    ],
                    timeout=1800,
                    log_path=install_log,
                    label="安装私有 Python 3.12",
                )
                if ok:
                    install_ok = True
                    break
                install_detail = detail
                if attempt == 0:
                    self._event_queue.put({"event": "clone_status", "msg": "私有 Python 安装失败，准备重新下载安装包并重试一次…"})
                    try:
                        if os.path.isfile(installer_path):
                            os.remove(installer_path)
                    except Exception:
                        pass
            if not install_ok:
                return None, install_detail
            python_exe, python_version, scanned = self._resolve_private_python_exe(paths, wait_seconds=45)
            if python_exe:
                paths["python_exe"] = python_exe
            if not python_version.startswith("3.12"):
                detail = "私有 Python 3.12 安装完成后仍未检测到可用的 python.exe。"
                if scanned:
                    detail += "\n\n已扫描路径：\n" + "\n".join(scanned[:12])
                tail = self._read_tail_text(install_log, max_lines=40)
                if tail:
                    detail += "\n\n安装日志尾部：\n" + tail
                return None, detail
            self._event_queue.put({"event": "clone_status", "msg": f"检测到私有 Python：{paths['python_exe']}（{python_version}）"})
        else:
            self._event_queue.put({"event": "clone_status", "msg": f"复用已存在的私有 Python {python_version}：{paths['python_exe']}"})

        self._event_queue.put({"event": "clone_status", "msg": "正在创建私有 3.12 虚拟环境…"})
        ok, detail = self._run_checked_command(
            [paths["python_exe"], "-m", "venv", "--clear", paths["venv_root"]],
            timeout=1200,
            label="创建私有虚拟环境",
        )
        if not ok:
            return None, detail
        if not os.path.isfile(paths["venv_python"]):
            return None, "虚拟环境创建完成后未找到 venv 的 python.exe。"

        self._event_queue.put({"event": "clone_status", "msg": "正在初始化 pip…"})
        ok, detail = self._run_checked_command(
            [paths["venv_python"], "-m", "ensurepip", "--upgrade"],
            timeout=1200,
            label="初始化 pip",
        )
        if not ok:
            return None, detail

        self._event_queue.put({"event": "clone_status", "msg": "正在为私有虚拟环境安装 requests…"})
        ok, detail = self._run_checked_command(
            [paths["venv_python"], "-m", "pip", "install", "requests"],
            timeout=1800,
            label="安装 requests",
        )
        if not ok:
            return None, detail

        ok, detail = lz._probe_python_agent_compat(paths["venv_python"], target)
        if not ok:
            return None, f"私有 3.12 虚拟环境已创建，但载入 GenericAgent 失败：{detail}"
        return paths["venv_python"], None

    def _run_clone_repo(self, target, private_python=False, private_only=False):
        try:
            if private_python and private_only:
                if not lz.is_valid_agent_dir(target):
                    self._event_queue.put({"event": "clone_error", "msg": "仅配置虚拟环境时，目标目录必须已经是有效的 GenericAgent 根目录。"})
                    return
                self._event_queue.put({"event": "clone_status", "msg": "已跳过源码下载，继续为现有目录配置私有 3.12 环境。"})
            elif not lz.is_valid_agent_dir(target):
                try:
                    subprocess.run(
                        ["git", "--version"],
                        check=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                except Exception:
                    self._event_queue.put({"event": "clone_error", "msg": "未检测到 Git。请先安装 Git for Windows：\nhttps://git-scm.com/download/win"})
                    return
                self._event_queue.put({"event": "clone_status", "msg": f"开始下载到：{target}"})
                proc = subprocess.Popen(
                    ["git", "clone", "--progress", lz.REPO_URL, target],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                last_lines = []
                for line in proc.stdout:
                    text = line.rstrip()
                    if text:
                        last_lines.append(text)
                        if len(last_lines) > 3:
                            last_lines = last_lines[-3:]
                        self._event_queue.put({"event": "clone_status", "msg": text})
                proc.wait()
                if proc.returncode != 0 or not lz.is_valid_agent_dir(target):
                    detail = "\n".join(last_lines).strip()
                    self._event_queue.put({"event": "clone_error", "msg": detail or "git clone 失败，请检查网络后重试。"})
                    return
            elif private_python:
                self._event_queue.put({"event": "clone_status", "msg": "检测到现有 GenericAgent 目录，跳过 git clone，继续配置私有 3.12 环境。"})

            python_exe = ""
            if private_python:
                python_exe, py_err = self._ensure_private_python_env(target)
                if not python_exe:
                    self._event_queue.put({"event": "clone_error", "msg": py_err or "私有 3.12 虚拟环境配置失败。"})
                    return
            self._event_queue.put({"event": "clone_done", "target": target, "python_exe": python_exe, "private_python": bool(private_python)})
        except Exception as e:
            self._event_queue.put({"event": "clone_error", "msg": str(e)})

    def _handle_download_event(self, ev) -> bool:
        et = ev.get("event")
        if et == "clone_status":
            msg = str(ev.get("msg") or "").strip()
            self.download_status_label.setText(msg)
            self._append_download_log(msg)
            return True
        if et == "clone_done":
            self._download_running = False
            self._download_mode = ""
            target = str(ev.get("target") or "").strip()
            python_exe = str(ev.get("python_exe") or "").strip()
            private_python = bool(ev.get("private_python"))
            if python_exe:
                self.cfg["python_exe"] = lz._make_config_relative_path(python_exe)
                lz.save_config(self.cfg)
            if target:
                self._set_agent_dir(target)
            self._refresh_download_state()
            if private_python and python_exe:
                self.download_status_label.setText("下载完成，已配置私有 3.12 虚拟环境并设置为当前 GenericAgent 目录。现在可以直接进入聊天。")
                self._append_download_log(f"[{datetime.now().strftime('%H:%M:%S')}] 私有 3.12 虚拟环境已就绪：{python_exe}")
            else:
                self.download_status_label.setText("下载完成，已设置为当前 GenericAgent 目录。现在可以直接进入聊天。")
            self._append_download_log(f"[{datetime.now().strftime('%H:%M:%S')}] 下载完成")
            return True
        if et == "clone_error":
            self._download_running = False
            self._download_mode = ""
            self._refresh_download_state()
            msg = str(ev.get("msg") or "下载失败").strip()
            self.download_status_label.setText(f"下载失败：{msg}")
            self._append_download_log(f"[{datetime.now().strftime('%H:%M:%S')}] 下载失败：{msg}")
            QMessageBox.warning(self, "下载失败", msg)
            return True
        return False
