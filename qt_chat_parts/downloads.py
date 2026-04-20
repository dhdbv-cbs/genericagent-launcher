from __future__ import annotations

import os
import platform
import subprocess
import threading
from datetime import datetime

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
        return {
            "version": PRIVATE_PYTHON_VERSION,
            "arch": arch,
            "filename": filename,
            "url": f"https://www.python.org/ftp/python/{PRIVATE_PYTHON_VERSION}/{filename}",
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

    def _read_tail_text(self, path, max_lines=20):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            return "".join(lines[-max_lines:]).strip()
        except Exception:
            return ""

    def _download_to_file(self, url, dest, label):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        self._event_queue.put({"event": "clone_status", "msg": f"{label}：开始下载"})
        with lz.requests.get(url, stream=True, timeout=(20, 600)) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            downloaded = 0
            last_report = 0
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        progress = int(downloaded * 100 / total)
                        if progress >= last_report + 10 or downloaded == total:
                            last_report = progress
                            self._event_queue.put(
                                {
                                    "event": "clone_status",
                                    "msg": f"{label}：{progress}% ({downloaded // (1024 * 1024)} / {max(total // (1024 * 1024), 1)} MB)",
                                }
                            )
                    elif downloaded - last_report >= 8 * 1024 * 1024:
                        last_report = downloaded
                        self._event_queue.put(
                            {
                                "event": "clone_status",
                                "msg": f"{label}：已下载 {downloaded // (1024 * 1024)} MB",
                            }
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
        detail = (result.stderr or result.stdout or "").strip()
        if log_path and os.path.isfile(log_path):
            tail = self._read_tail_text(log_path)
            if tail:
                detail = tail
        if detail:
            lines = [line.strip() for line in detail.splitlines() if line.strip()]
            detail = "\n".join(lines[-12:]) if lines else detail
        else:
            detail = f"{label or '命令'}失败，退出码 {result.returncode}"
        return False, detail

    def _ensure_private_python_env(self, target):
        spec, spec_err = self._private_python_spec()
        if spec_err:
            return None, spec_err
        paths = self._private_runtime_paths(target)
        os.makedirs(paths["root"], exist_ok=True)
        os.makedirs(paths["downloads_root"], exist_ok=True)

        python_version = self._probe_python_version_prefix(paths["python_exe"])
        if not python_version.startswith("3.12"):
            installer_path = os.path.join(paths["downloads_root"], spec["filename"])
            if not os.path.isfile(installer_path):
                self._download_to_file(spec["url"], installer_path, f"下载官方 Python {spec['version']} 安装包")
            else:
                self._event_queue.put({"event": "clone_status", "msg": f"复用已下载的 Python 安装包：{installer_path}"})
            install_log = os.path.join(paths["downloads_root"], "python-install.log")
            self._event_queue.put({"event": "clone_status", "msg": "正在安装私有 Python 3.12（不会修改系统 PATH）…"})
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
            if not ok:
                return None, detail
            python_version = self._probe_python_version_prefix(paths["python_exe"])
            if not python_version.startswith("3.12"):
                return None, "私有 Python 3.12 安装完成后仍未检测到可用的 python.exe。"
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
