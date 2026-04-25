from __future__ import annotations

import os

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox

from launcher_app import core as lz


class NavigationMixin:
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

    def _quick_enter_chat(self):
        self._enter_chat(skip_dependency_check=True)

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
            self.enter_chat_btn.setEnabled(valid)
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
                py_resolved = lz._resolve_config_path(py_cfg)
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
        start_dir = os.path.dirname(lz._resolve_config_path(current)) if current else os.path.expanduser("~")
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
            self.locate_python_edit.setText(lz._make_config_relative_path(path))

    def _locate_enter_chat(self):
        raw = self.locate_path_edit.text().strip() if hasattr(self, "locate_path_edit") else self.agent_dir
        py_raw = self.locate_python_edit.text().strip() if hasattr(self, "locate_python_edit") else ""
        if py_raw:
            resolved = lz._resolve_config_path(py_raw)
            if not os.path.isfile(resolved):
                QMessageBox.warning(self, "Python 路径无效", f"未找到可执行文件：\n{resolved}")
                return
            self.cfg["python_exe"] = lz._make_config_relative_path(resolved)
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
            self._settings_top_back_btn.setText("←  返回聊天" if valid else "←  返回首页")
            try:
                self._settings_top_back_btn.clicked.disconnect()
            except Exception:
                pass
            self._settings_top_back_btn.clicked.connect(self._show_chat_page if valid else self._show_welcome)
        self._refresh_welcome_state()
        self._settings_reload(categories=[getattr(self, "_current_settings_category", "api")], force=True)

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
