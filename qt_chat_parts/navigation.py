from __future__ import annotations

import os
import subprocess
import sys
import webbrowser

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QMessageBox

from launcher_app import core as lz

from .common import invalidate_runtime_bound_state


class NavigationMixin:
    def _apply_navigation_widget_state(self, widget, enabled, *, enabled_tooltip="", disabled_tooltip=""):
        if widget is None:
            return
        widget.setEnabled(bool(enabled))
        tooltip = enabled_tooltip if bool(enabled) else disabled_tooltip
        try:
            widget.setToolTip(str(tooltip or ""))
        except Exception:
            pass

    def _schedule_local_channel_autostart(self, delay_ms=260):
        if not lz.is_valid_agent_dir(self.agent_dir):
            return
        if bool(getattr(self, "_local_channel_autostart_scheduled", False)):
            return
        self._local_channel_autostart_scheduled = True

        def run():
            self._local_channel_autostart_scheduled = False
            self._start_autostart_channels()

        QTimer.singleShot(max(0, int(delay_ms or 0)), self, run)

    def _defer_chat_runtime_bootstrap(self):
        if bool(getattr(self, "_chat_runtime_bootstrap_pending", False)):
            return
        self._chat_runtime_bootstrap_pending = True

        def run():
            self._chat_runtime_bootstrap_pending = False
            self._start_autostart_channels()
            starter = getattr(self, "_start_autostart_scheduler", None)
            if callable(starter):
                starter()
            if self.bridge_proc is None or self.bridge_proc.poll() is not None:
                self._safe_start_bridge()

        QTimer.singleShot(160, self, run)

    def _can_skip_dependency_check_on_quick_enter(self):
        return True

    def _quick_enter_chat(self):
        self._enter_chat(skip_dependency_check=self._can_skip_dependency_check_on_quick_enter())

    def _official_frontend_extra_packages(self, group_id, fallback_packages):
        packages = []
        try:
            groups = lz.resolve_upstream_frontend_dependency_groups(self.agent_dir)
        except Exception:
            groups = []
        for group in groups or []:
            if str((group or {}).get("id") or "").strip() != str(group_id or "").strip():
                continue
            for item in list((group or {}).get("items") or []):
                spec = str((item or {}).get("package") or "").strip()
                if spec and spec not in packages:
                    packages.append(spec)
            break
        return packages or [str(spec or "").strip() for spec in (fallback_packages or []) if str(spec or "").strip()]

    def _official_gui_extra_packages(self):
        return self._official_frontend_extra_packages("launch_web_ui", ["streamlit", "pywebview"])

    def _official_frontend_script_path(self, script_relpath):
        parts = [part for part in str(script_relpath or "").replace("\\", "/").split("/") if part]
        return os.path.join(self.agent_dir, *parts) if parts else self.agent_dir

    def _refresh_official_frontend_entry(
        self,
        *,
        button_attr,
        status_attr,
        dependency_attr,
        script_relpath,
        extra_packages,
        enabled_tooltip,
    ):
        valid = lz.is_valid_agent_dir(self.agent_dir)
        script_path = self._official_frontend_script_path(script_relpath)
        script_exists = bool(valid and os.path.isfile(script_path))
        status_widget = getattr(self, status_attr, None)
        if status_widget is not None:
            if not valid:
                status_text = f"入口：{script_relpath}\n状态：等待选择有效目录。"
            elif script_exists:
                status_text = f"入口：{script_relpath}\n状态：入口脚本可用。"
            else:
                status_text = f"入口：{script_relpath}\n状态：当前目录未找到这个脚本。"
            status_widget.setText(status_text)
        dependency_widget = getattr(self, dependency_attr, None)
        if dependency_widget is not None:
            dependency_widget.setText("启动前会自动检查并补齐依赖：" + "、".join(extra_packages) + "。")
        button = getattr(self, button_attr, None)
        if button is not None:
            if not valid:
                disabled_tooltip = "请先选择有效的 GenericAgent 目录。"
            elif not script_exists:
                disabled_tooltip = f"当前目录未找到 {script_relpath}。"
            else:
                disabled_tooltip = ""
            self._apply_navigation_widget_state(
                button,
                valid and script_exists,
                enabled_tooltip=enabled_tooltip,
                disabled_tooltip=disabled_tooltip,
            )

    def _official_desktop_release_page_url(self):
        repo = str(getattr(lz, "REPO_URL", "") or "").strip().rstrip("/")
        if repo.endswith(".git"):
            repo = repo[:-4]
        return (repo + "/releases/latest") if repo else "https://github.com/lsdefine/GenericAgent/releases/latest"

    def _open_official_desktop_release_page(self):
        target = self._official_desktop_release_page_url()
        opened = False
        try:
            opened = bool(QDesktopServices.openUrl(QUrl(target)))
        except Exception:
            opened = False
        if not opened:
            try:
                opened = bool(webbrowser.open(target))
            except Exception:
                opened = False
        if not opened:
            QMessageBox.warning(self, "打开链接失败", f"请手动打开以下地址：\n{target}")
        return opened

    def _official_desktop_release_candidates(self):
        is_macos = bool(getattr(lz, "IS_MACOS", sys.platform == "darwin"))
        platform_name = "windows" if os.name == "nt" else ("macos" if is_macos else "other")
        release_url = self._official_desktop_release_page_url()
        valid_agent_dir = lz.is_valid_agent_dir(self.agent_dir)
        ctx = {
            "platform": platform_name,
            "release_url": release_url,
            "supported": platform_name in ("windows", "macos"),
            "valid_agent_dir": valid_agent_dir,
            "path": "",
            "launch_args": [],
            "launch_cwd": "",
            "status_text": "",
            "detail_text": "",
            "enabled_tooltip": "",
            "disabled_tooltip": "",
        }
        if platform_name == "windows":
            candidates = []
            if valid_agent_dir:
                candidates = [
                    os.path.join(self.agent_dir, "frontends", "GenericAgent.exe"),
                    os.path.join(self.agent_dir, "frontends", "GenericAgent-windows-x64.exe"),
                ]
            found = next((path for path in candidates if os.path.isfile(path)), "")
            ctx["path"] = found
            ctx["launch_args"] = [found] if found else []
            ctx["launch_cwd"] = os.path.dirname(found) if found else (os.path.join(self.agent_dir, "frontends") if valid_agent_dir else "")
            ctx["status_text"] = (
                f"入口：{found}\n状态：已发现 Windows 发布版桌面客户端。"
                if found
                else (
                    "入口：frontends/GenericAgent.exe\n状态：当前目录还没发现 Windows 发布版桌面客户端。"
                    if valid_agent_dir
                    else "入口：frontends/GenericAgent.exe\n状态：请先选择有效目录，再放入发布版 exe。"
                )
            )
            ctx["detail_text"] = (
                "发布页资产：GenericAgent-windows-x64.exe / GenericAgent.exe。\n"
                "官方说明是把 exe 放到当前 GenericAgent 的 frontends/ 目录，通常会重命名为 GenericAgent.exe。"
            )
            ctx["enabled_tooltip"] = "拉起当前目录 frontends/ 下的官方发布版桌面客户端。"
            ctx["disabled_tooltip"] = (
                "请先选择有效的 GenericAgent 目录，并把发布版 exe 放到 frontends/。"
                if not valid_agent_dir
                else "当前目录未找到官方发布版桌面客户端，可先打开 Release 页面下载。"
            )
            return ctx
        if platform_name == "macos":
            candidates = [
                os.path.join("/Applications", "GenericAgent.app"),
                os.path.join(os.path.expanduser("~"), "Applications", "GenericAgent.app"),
            ]
            found = next((path for path in candidates if os.path.isdir(path)), "")
            ctx["path"] = found
            ctx["launch_args"] = ["open", found] if found else []
            ctx["launch_cwd"] = os.path.dirname(found) if found else ""
            ctx["status_text"] = (
                f"入口：{found}\n状态：已发现已安装的 macOS 发布版桌面客户端。"
                if found
                else "入口：/Applications/GenericAgent.app 或 ~/Applications/GenericAgent.app\n状态：当前还没发现已安装的 macOS 发布版桌面客户端。"
            )
            ctx["detail_text"] = (
                "发布页资产：GenericAgent_<version>_aarch64.dmg。\n"
                "下载后把 GenericAgent.app 拖到 /Applications；如果只想当前用户使用，放到 ~/Applications 也可以。"
            )
            ctx["enabled_tooltip"] = "拉起已安装的 GenericAgent.app。"
            ctx["disabled_tooltip"] = "当前未发现 /Applications/GenericAgent.app 或 ~/Applications/GenericAgent.app，可先打开 Release 页面下载安装。"
            return ctx
        ctx["status_text"] = "入口：官方发布版桌面客户端\n状态：当前平台暂未适配这个启动入口。"
        ctx["detail_text"] = "目前发布页明确提供的是 Windows x64 和 macOS Apple Silicon 桌面资产。"
        ctx["enabled_tooltip"] = ""
        ctx["disabled_tooltip"] = "当前发布页桌面版只提供 Windows x64 和 macOS Apple Silicon。"
        return ctx

    def _refresh_official_desktop_release_state(self):
        ctx = self._official_desktop_release_candidates()
        status_widget = getattr(self, "official_desktop_status_label", None)
        if status_widget is not None:
            status_widget.setText(str(ctx.get("status_text") or ""))
        detail_widget = getattr(self, "official_desktop_dependency_label", None)
        if detail_widget is not None:
            detail_widget.setText(
                str(ctx.get("detail_text") or "")
                + ("\nRelease 页面：" + str(ctx.get("release_url") or "") if str(ctx.get("release_url") or "").strip() else "")
            )
        button = getattr(self, "official_desktop_launch_btn", None)
        if button is not None:
            self._apply_navigation_widget_state(
                button,
                bool(ctx.get("path")),
                enabled_tooltip=str(ctx.get("enabled_tooltip") or ""),
                disabled_tooltip=str(ctx.get("disabled_tooltip") or ""),
            )

    def _refresh_official_gui_state(self):
        valid = lz.is_valid_agent_dir(self.agent_dir)
        if hasattr(self, "official_gui_path_label"):
            self.official_gui_path_label.setText(self.agent_dir if valid else "尚未配置有效的 GenericAgent 目录。")
        if hasattr(self, "official_gui_path_hint_label"):
            self.official_gui_path_hint_label.setText(
                "如果你想改目录或指定 Python，可先回到“我已经下载了 GenericAgent”页面调整。"
                if valid
                else "当前还没有有效目录。请先去“我已经下载了 GenericAgent”页面选择项目根目录。"
            )
        py_cfg = str(self.cfg.get("python_exe") or "").strip()
        if py_cfg:
            py_resolved = lz._resolve_configured_python_exe(py_cfg, agent_dir=self.agent_dir)
            py_text = f"当前 Python：{py_cfg}\n解析后：{py_resolved}"
        else:
            py_text = "当前 Python：未指定，启动前会自动探测并记住可用解释器。"
        installer_mode = str(self.cfg.get("dependency_installer") or "auto").strip().lower()
        if installer_mode not in ("auto", "uv", "pip"):
            installer_mode = "auto"
        installer_text = {
            "auto": "自动（优先 uv，失败回退 pip）",
            "uv": "强制 uv",
            "pip": "强制 pip",
        }.get(installer_mode, "自动（优先 uv，失败回退 pip）")
        if hasattr(self, "official_gui_status_label"):
            self.official_gui_status_label.setText(
                (f"当前目录：{self.agent_dir}\n{py_text}\n依赖安装器：{installer_text}")
                if valid
                else (f"当前还没有有效目录，默认 GUI 暂时不能启动；发布版桌面端会单独检测已安装应用。\n{py_text}\n依赖安装器：{installer_text}")
            )
        self._refresh_official_frontend_entry(
            button_attr="official_gui_launch_btn",
            status_attr="official_gui_entry_status_label",
            dependency_attr="official_gui_dependency_label",
            script_relpath="launch.pyw",
            extra_packages=self._official_gui_extra_packages(),
            enabled_tooltip="检查依赖后拉起上游 launch.pyw。",
        )
        self._refresh_official_desktop_release_state()

    def _launch_official_frontend(
        self,
        *,
        purpose,
        script_relpath,
        extra_packages,
        launch_error_title,
        success_status,
        success_notice,
    ):
        launch_script = self._official_frontend_script_path(script_relpath)
        if not lz.is_valid_agent_dir(self.agent_dir):
            QMessageBox.warning(self, "目录无效", "请选择有效的 GenericAgent 根目录。\n\n将返回载入内核页面。")
            self._show_locate()
            return False
        ensured = lz._ensure_mykey_file(self.agent_dir)
        if not ensured.get("ok"):
            QMessageBox.critical(
                self,
                "初始化失败",
                "无法准备 mykey.py。\n\n"
                f"目标：{ensured.get('path', '')}\n"
                f"错误：{ensured.get('error', '未知错误')}",
            )
            return False
        if ensured.get("created"):
            QMessageBox.information(
                self,
                "已初始化配置文件",
                "已自动创建 mykey.py。\n\n接下来如果提示未配置 LLM，请在后续 Qt 设置页补充 API 配置。",
            )
        if not self._check_runtime_dependencies(purpose=purpose, extra_packages=extra_packages):
            return False
        resolver = getattr(self, "_resolve_bridge_python", None)
        if callable(resolver):
            py, py_err = resolver()
        else:
            py, py_err = lz._find_compatible_system_python(self.agent_dir)
        if not py:
            QMessageBox.critical(self, "启动失败", py_err or "未找到可用的 Python 解释器。")
            return False
        rememberer = getattr(self, "_remember_bridge_python", None)
        if callable(rememberer):
            try:
                rememberer(py)
            except Exception:
                pass
        if not os.path.isfile(launch_script):
            QMessageBox.critical(self, "启动失败", f"未找到上游入口：\n{launch_script}")
            return False
        try:
            lz._popen_external_subprocess(
                [py, launch_script],
                cwd=self.agent_dir,
                env=lz._external_subprocess_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "启动失败",
                f"{launch_error_title}\n\n"
                f"入口：{launch_script}\n"
                f"解释器：{py}\n"
                f"错误：{e}",
            )
            return False
        setter = getattr(self, "_set_status", None)
        if callable(setter):
            setter(success_status)
        if hasattr(self, "official_gui_notice_label"):
            self.official_gui_notice_label.setText(success_notice)
        refresher = getattr(self, "_refresh_official_gui_state", None)
        if callable(refresher):
            refresher()
        return True

    def _launch_official_gui(self):
        return self._launch_official_frontend(
            purpose="启动官方 GUI",
            script_relpath="launch.pyw",
            extra_packages=self._official_gui_extra_packages(),
            launch_error_title="无法拉起官方默认 GUI。",
            success_status="已拉起官方 GUI。",
            success_notice="已尝试拉起上游 launch.pyw。它使用的是官方默认界面，不会接管到启动器聊天主区。",
        )

    def _launch_official_desktop_app(self):
        ctx = self._official_desktop_release_candidates()
        target = str(ctx.get("path") or "").strip()
        args = list(ctx.get("launch_args") or [])
        if (not target) or (not args):
            self._open_official_desktop_release_page()
            return False
        try:
            lz._popen_external_subprocess(
                args,
                cwd=str(ctx.get("launch_cwd") or "") or os.path.dirname(target) or None,
                env=lz._external_subprocess_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "启动失败",
                "无法拉起官方桌面版。\n\n"
                f"入口：{target}\n"
                f"命令：{' '.join(args)}\n"
                f"错误：{e}",
            )
            return False
        setter = getattr(self, "_set_status", None)
        if callable(setter):
            setter("已拉起官方桌面版。")
        if hasattr(self, "official_gui_notice_label"):
            self.official_gui_notice_label.setText(
                f"已尝试拉起官方发布版桌面客户端：{target}。它会新开官方窗口，不会接管到启动器聊天主区。"
            )
        refresher = getattr(self, "_refresh_official_gui_state", None)
        if callable(refresher):
            refresher()
        return True

    def _set_agent_dir(self, path: str, *, persist: bool = True):
        raw = str(path or "").strip()
        new_dir = os.path.abspath(raw) if raw else ""
        changed = os.path.normcase(new_dir) != os.path.normcase(self.agent_dir)
        self.agent_dir = new_dir
        if changed:
            self._stop_bridge()
            self._stop_all_managed_channels(refresh=False)
            stopper = getattr(self, "_stop_scheduler_process", None)
            if callable(stopper):
                stopper(refresh=False)
            lan_stopper = getattr(self, "_stop_lan_interface_process", None)
            if callable(lan_stopper):
                lan_stopper(refresh=False)
            if hasattr(self, "_scheduler_last_exit_code"):
                self._scheduler_last_exit_code = None
            if hasattr(self, "_lan_interface_last_exit_code"):
                self._lan_interface_last_exit_code = None
            if hasattr(self, "_local_channel_autostart_scheduled"):
                self._local_channel_autostart_scheduled = False
            if hasattr(self, "_chat_runtime_bootstrap_pending"):
                self._chat_runtime_bootstrap_pending = False
            if hasattr(self, "_lan_interface_autostart_scheduled"):
                self._lan_interface_autostart_scheduled = False
            if hasattr(self, "_lan_interface_autostart_running"):
                self._lan_interface_autostart_running = False
            invalidate_runtime_bound_state(self, bump_runtime=True, bump_settings_target=True, clear_remote_sync_queues=True)
            self._last_dependency_check = None
            self._last_dependency_report = None
            self.current_session = None
            self._selected_session_id = None
            self._pending_state_session = None
            self._ignore_session_select = True
            self.session_list.clear()
            self._ignore_session_select = False
            self._last_session_list_signature = None
            if hasattr(self, "_session_index_warmup_started"):
                self._session_index_warmup_started = False
            if hasattr(self, "_settings_loaded_categories"):
                self._settings_loaded_categories.clear()
        if persist:
            self.cfg["agent_dir"] = self.agent_dir
            lz.save_config(self.cfg)
        self._refresh_welcome_state()
        in_settings = bool(getattr(self, "pages", None) is not None and self.pages.currentWidget() is getattr(self, "_settings_page", None))
        if in_settings:
            self._settings_reload(categories=[getattr(self, "_current_settings_category", "api")], force=True)
        else:
            self._settings_reload(categories=[])
        if lz.is_valid_agent_dir(self.agent_dir):
            warmer = getattr(self, "_schedule_session_index_warmup", None)
            if callable(warmer):
                warmer()
            lz.purge_archived_sessions(self.agent_dir)
            self._enforce_session_archive_limits(refresh=False)
            self._refresh_sessions()
            self._schedule_local_channel_autostart()
            scheduler_starter = getattr(self, "_start_autostart_scheduler", None)
            if callable(scheduler_starter):
                scheduler_starter()
            lan_starter = getattr(self, "_schedule_lan_interface_autostart", None)
            if callable(lan_starter):
                lan_starter()

    def _refresh_welcome_state(self):
        valid = lz.is_valid_agent_dir(self.agent_dir)
        if hasattr(self, "recent_path_label"):
            self.recent_path_label.setText(self.agent_dir if valid else "尚未配置有效的 GenericAgent 目录。")
        if hasattr(self, "recent_card"):
            self.recent_card.setVisible(valid)
        if hasattr(self, "enter_chat_btn"):
            self._apply_navigation_widget_state(
                self.enter_chat_btn,
                valid,
                enabled_tooltip="进入聊天页并开始准备当前内核环境。",
                disabled_tooltip="请先选择有效的 GenericAgent 目录。",
            )
        if hasattr(self, "locate_path_edit"):
            self.locate_path_edit.setText(self.agent_dir or "")
        if hasattr(self, "locate_python_edit"):
            self.locate_python_edit.setText(str(self.cfg.get("python_exe") or "").strip())
        if hasattr(self, "locate_dependency_installer_combo"):
            mode = str(self.cfg.get("dependency_installer") or "auto").strip().lower()
            if mode not in ("auto", "uv", "pip"):
                mode = "auto"
            idx = self.locate_dependency_installer_combo.findData(mode)
            if idx >= 0 and self.locate_dependency_installer_combo.currentIndex() != idx:
                self.locate_dependency_installer_combo.setCurrentIndex(idx)
        if hasattr(self, "locate_status_label"):
            py_cfg = str(self.cfg.get("python_exe") or "").strip()
            if py_cfg:
                py_resolved = lz._resolve_configured_python_exe(py_cfg, agent_dir=self.agent_dir)
                py_text = f"\nPython 可执行文件：{py_cfg}\n解析后：{py_resolved}"
            else:
                py_text = "\nPython 可执行文件：未指定（将自动探测）"
            installer_mode = str(self.cfg.get("dependency_installer") or "auto").strip().lower()
            if installer_mode not in ("auto", "uv", "pip"):
                installer_mode = "auto"
            installer_text = {
                "auto": "自动（优先 uv，失败回退 pip）",
                "uv": "强制 uv",
                "pip": "强制 pip",
            }.get(installer_mode, "自动（优先 uv，失败回退 pip）")
            self.locate_status_label.setText(
                (f"当前目录有效，可以直接载入：\n{self.agent_dir}{py_text}\n依赖安装器：{installer_text}")
                if valid
                else ("当前还没有有效的 GenericAgent 目录。请先浏览并选择正确的项目根目录。" + py_text + f"\n依赖安装器：{installer_text}")
            )
        self._refresh_download_state()
        if hasattr(self, "_refresh_dependency_status"):
            self._refresh_dependency_status()
        if hasattr(self, "settings_status_label"):
            self._settings_reload(categories=[])
        if hasattr(self, "official_gui_launch_btn"):
            refresher = getattr(self, "_refresh_official_gui_state", None)
            if callable(refresher):
                refresher()

    def _choose_agent_dir(self):
        path = QFileDialog.getExistingDirectory(
            self,
            "选择 GenericAgent 目录",
            self.agent_dir or os.path.expanduser("~"),
        )
        if path:
            self._set_agent_dir(path)
            if hasattr(self, "locate_path_edit"):
                self.locate_path_edit.setText(path)

    def _choose_python_executable(self):
        current = ""
        if hasattr(self, "locate_python_edit"):
            current = self.locate_python_edit.text().strip()
        agent_dir = str(getattr(self, "agent_dir", "") or "").strip()
        start_dir = os.path.dirname(lz._resolve_configured_python_exe(current, agent_dir=agent_dir)) if current else os.path.expanduser("~")
        if not os.path.isdir(start_dir):
            start_dir = os.path.expanduser("~")
        if os.name == "nt":
            path, _ = QFileDialog.getOpenFileName(
                self,
                "选择 Python 可执行文件",
                start_dir,
                "Executable (*.exe);;All Files (*)",
            )
        else:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "选择 Python 可执行文件",
                start_dir,
                "All Files (*)",
            )
        if path and hasattr(self, "locate_python_edit"):
            self.locate_python_edit.setText(lz._make_python_exe_config_path(path, agent_dir=agent_dir))

    def _locate_enter_chat(self):
        raw = self.locate_path_edit.text().strip() if hasattr(self, "locate_path_edit") else self.agent_dir
        py_raw = self.locate_python_edit.text().strip() if hasattr(self, "locate_python_edit") else ""
        if py_raw:
            resolved = lz._resolve_configured_python_exe(py_raw, agent_dir=raw)
            if not os.path.isfile(resolved):
                QMessageBox.warning(self, "Python 路径无效", f"未找到可执行文件：\n{resolved}")
                return
            self.cfg["python_exe"] = lz._make_python_exe_config_path(resolved, agent_dir=raw)
        else:
            self.cfg.pop("python_exe", None)
        mode = "auto"
        combo = getattr(self, "locate_dependency_installer_combo", None)
        if combo is not None:
            selected = str(combo.currentData() or "").strip().lower()
            if selected in ("auto", "uv", "pip"):
                mode = selected
        self.cfg["dependency_installer"] = mode
        lz.save_config(self.cfg)
        self._set_agent_dir(raw)
        self._enter_chat()

    def _show_settings(self):
        self.setWindowTitle("GenericAgent 启动器")
        ensure = getattr(self, "_ensure_settings_page_built", None)
        if callable(ensure):
            ensure()
        self.pages.setCurrentWidget(self._settings_page)
        valid = lz.is_valid_agent_dir(self.agent_dir)
        if self._settings_top_back_btn is not None:
            self._settings_top_back_btn.setText("返回聊天" if valid else "返回首页")
            try:
                self._settings_top_back_btn.clicked.disconnect()
            except Exception:
                pass
            self._settings_top_back_btn.clicked.connect(self._show_chat_page if valid else self._show_welcome)
        self._refresh_welcome_state()

        def _reload_after_switch():
            self._settings_reload(categories=[getattr(self, "_current_settings_category", "api")], force=True)

        try:
            QTimer.singleShot(0, self, _reload_after_switch)
        except Exception:
            try:
                QTimer.singleShot(0, _reload_after_switch)
            except Exception:
                _reload_after_switch()

    def _show_chat_page(self):
        self.setWindowTitle("GenericAgent 启动器")
        self.pages.setCurrentWidget(self._chat_page)

    def _save_settings_and_enter_chat(self):
        self._enter_chat()

    def _enter_chat(self, *, skip_dependency_check=False):
        if not lz.is_valid_agent_dir(self.agent_dir):
            QMessageBox.warning(self, "目录无效", "请选择有效的 GenericAgent 根目录。\n\n将返回载入内核页面。")
            self._show_locate()
            return
        ensured = lz._ensure_mykey_file(self.agent_dir)
        if not ensured.get("ok"):
            QMessageBox.critical(
                self,
                "初始化失败",
                "无法准备 mykey.py。\n\n"
                f"目标：{ensured.get('path', '')}\n"
                f"错误：{ensured.get('error', '未知错误')}",
            )
            return
        if ensured.get("created"):
            QMessageBox.information(
                self,
                "已初始化配置文件",
                "已自动创建 mykey.py。\n\n接下来如果提示未配置 LLM，请在后续 Qt 设置页补充 API 配置。",
            )
        if (not skip_dependency_check) and (not self._check_runtime_dependencies(purpose="载入内核")):
            return
        self._show_chat_page()
        self._refresh_sessions()
        if self.current_session:
            self._render_session(self.current_session)
        else:
            self._reset_chat_area("选择一个会话，或新建会话开始聊天。")
        self._defer_chat_runtime_bootstrap()
