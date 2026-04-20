from __future__ import annotations

import os

from PySide6.QtWidgets import QFileDialog, QMessageBox

from launcher_app import core as lz


class NavigationMixin:
    def _set_agent_dir(self, path: str, *, persist: bool = True):
        raw = str(path or "").strip()
        new_dir = os.path.abspath(raw) if raw else ""
        changed = os.path.normcase(new_dir) != os.path.normcase(self.agent_dir)
        self.agent_dir = new_dir
        if changed:
            self._stop_bridge()
            self._stop_all_managed_channels(refresh=False)
            self.current_session = None
            self._selected_session_id = None
            self._pending_state_session = None
            self._ignore_session_select = True
            self.session_list.clear()
            self._ignore_session_select = False
            self._last_session_list_signature = None
        if persist:
            self.cfg["agent_dir"] = self.agent_dir
            lz.save_config(self.cfg)
        self._refresh_welcome_state()
        self._settings_reload()
        if lz.is_valid_agent_dir(self.agent_dir):
            lz.purge_archived_sessions(self.agent_dir)
            self._enforce_session_archive_limits(refresh=False)
            self._refresh_sessions()

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
        if hasattr(self, "locate_status_label"):
            py_cfg = str(self.cfg.get("python_exe") or "").strip()
            if py_cfg:
                py_resolved = lz._resolve_config_path(py_cfg)
                py_text = f"\nPython 可执行文件：{py_cfg}\n解析后：{py_resolved}"
            else:
                py_text = "\nPython 可执行文件：未指定（将自动探测）"
            self.locate_status_label.setText(
                (f"当前目录有效，可以直接载入：\n{self.agent_dir}{py_text}")
                if valid
                else ("当前还没有有效的 GenericAgent 目录。请先浏览并选择正确的项目根目录。" + py_text)
            )
        self._refresh_download_state()
        if hasattr(self, "settings_status_label"):
            self._settings_reload()

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
        lz.save_config(self.cfg)
        self._set_agent_dir(raw)
        self._enter_chat()

    def _show_settings(self):
        self.setWindowTitle("GenericAgent 启动器")
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
        self._settings_reload()

    def _show_chat_page(self):
        self.setWindowTitle("GenericAgent 启动器")
        self.pages.setCurrentWidget(self._chat_page)

    def _save_settings_and_enter_chat(self):
        self._enter_chat()

    def _enter_chat(self):
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
        self._show_chat_page()
        self._refresh_sessions()
        if self.current_session:
            self._render_session(self.current_session)
        else:
            self._reset_chat_area("选择一个会话，或新建会话开始聊天。")
        self._start_autostart_channels()
        if self.bridge_proc is None or self.bridge_proc.poll() is not None:
            self._safe_start_bridge()
