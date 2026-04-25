from __future__ import annotations

import os
import queue
import threading
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QTextEdit, QVBoxLayout

from launcher_app import core as lz


class DependencyRuntimeMixin:
    def _dependency_check_cache_key(self, extra_packages=None):
        agent_dir = os.path.normcase(os.path.abspath(self.agent_dir)) if str(self.agent_dir or "").strip() else ""
        req_path = os.path.join(self.agent_dir, "requirements.txt") if agent_dir else ""
        req_mtime = 0.0
        try:
            if req_path and os.path.isfile(req_path):
                req_mtime = float(os.path.getmtime(req_path) or 0.0)
        except Exception:
            req_mtime = 0.0
        py_cfg = str(self.cfg.get("python_exe") or "").strip()
        py_resolved = os.path.normcase(os.path.normpath(lz._resolve_config_path(py_cfg))) if py_cfg else ""
        installer_mode = str(self.cfg.get("dependency_installer") or "auto").strip().lower()
        if installer_mode not in ("auto", "uv", "pip"):
            installer_mode = "auto"
        extras = tuple(sorted(str(item or "").strip() for item in (extra_packages or []) if str(item or "").strip()))
        return (agent_dir, py_resolved, req_mtime, installer_mode, extras)

    def _refresh_dependency_status(self):
        label = getattr(self, "locate_dependency_label", None)
        if label is None:
            return
        if not lz.is_valid_agent_dir(self.agent_dir):
            label.setText("依赖检查：需要先选择有效的 GenericAgent 目录。")
            return
        report = getattr(self, "_last_dependency_check", None) or {}
        if not report:
            label.setText("依赖检查：尚未执行。进入聊天时会自动检查并补齐。")
            return
        if report.get("key") != self._dependency_check_cache_key(report.get("extra_packages") or []):
            label.setText("依赖状态可能已变化。下次载入时会重新检查。")
            return
        if report.get("ok"):
            py = str(report.get("python") or "").strip()
            dep_report = getattr(self, "_last_dependency_report", None) or {}
            summary = dict(dep_report.get("summary") or {})
            count_text = ""
            if summary:
                count_text = (
                    f"\n检查项：{int(summary.get('checked', 0) or 0)}"
                    f" / 通过 {int(summary.get('ok', 0) or 0)}"
                    f" / 警告 {int(summary.get('warn', 0) or 0)}"
                    f" / 失败 {int(summary.get('error', 0) or 0)}"
                    f" / 自动修复 {int(summary.get('fixed', 0) or 0)}"
                )
            if py:
                label.setText(f"依赖检查：已通过\n解释器：{py}{count_text}")
            else:
                label.setText("依赖检查：已通过" + count_text)
            return
        err = str(report.get("error") or "").strip()
        label.setText("依赖检查失败：\n" + (err or "未知错误"))

    def _remember_dependency_check(self, *, ok, python="", error="", extra_packages=None):
        self._last_dependency_check = {
            "ok": bool(ok),
            "python": str(python or "").strip(),
            "error": str(error or "").strip(),
            "extra_packages": [str(item or "").strip() for item in (extra_packages or []) if str(item or "").strip()],
            "key": self._dependency_check_cache_key(extra_packages=extra_packages),
            "ts": time.time(),
        }
        self._refresh_dependency_status()

    def _remember_dependency_report(self, report):
        self._last_dependency_report = dict(report or {})
        self._refresh_dependency_status()

    def _show_dependency_report(self):
        report = getattr(self, "_last_dependency_report", None) or {}
        text = str(report.get("text") or "").strip()
        if not text:
            return

        dlg = QDialog(self)
        dlg.setModal(True)
        dlg.resize(840, 620)
        dlg.setMinimumSize(700, 480)
        dlg.setWindowTitle("依赖检查报告")

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel("依赖检查报告")
        title.setObjectName("cardTitle")
        layout.addWidget(title)

        summary = dict(report.get("summary") or {})
        summary_label = QLabel(
            f"检查项：{int(summary.get('checked', 0) or 0)}"
            f" / 通过 {int(summary.get('ok', 0) or 0)}"
            f" / 警告 {int(summary.get('warn', 0) or 0)}"
            f" / 失败 {int(summary.get('error', 0) or 0)}"
            f" / 自动修复 {int(summary.get('fixed', 0) or 0)}"
        )
        summary_label.setWordWrap(True)
        summary_label.setObjectName("bodyText")
        layout.addWidget(summary_label)

        box = QTextEdit()
        box.setReadOnly(True)
        box.setPlainText(text)
        layout.addWidget(box, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet(self._action_button_style())
        close_btn.clicked.connect(dlg.accept)
        actions.addWidget(close_btn, 0)
        layout.addLayout(actions)
        dlg.exec()

    def _dependency_check_desc_text(self, extra_packages=None):
        extras = [str(item or "").strip() for item in (extra_packages or []) if str(item or "").strip()]
        text = (
            "将检查系统 Python、基础依赖、GenericAgent requirements.txt，并验证 agentmain 是否可载入。"
            "缺少依赖时会自动补齐。依赖安装器默认 auto（优先 uv，失败回退 pip）。"
        )
        if extras:
            text += "\n\n本次还会同步渠道额外依赖：" + "、".join(extras)
        return text

    def _apply_dependency_check_result(self, result, *, extra_packages=None):
        result = dict(result or {})
        ok = bool(result.get("ok"))
        py = str(result.get("python") or "").strip()
        err = str(result.get("error") or "").strip()
        self._remember_dependency_report(result.get("report") or {})
        if ok and py:
            self.cfg["python_exe"] = lz._make_config_relative_path(py)
            lz.save_config(self.cfg)
            if hasattr(self, "locate_python_edit"):
                self.locate_python_edit.setText(self.cfg["python_exe"])
        self._remember_dependency_check(ok=ok, python=py, error=err, extra_packages=extra_packages)
        return ok, py, err

    def _check_runtime_dependencies(self, *, purpose="载入内核", extra_packages=None, force_sync=False, ignore_cache=False, visual=True):
        extra_packages = [str(item or "").strip() for item in (extra_packages or []) if str(item or "").strip()]
        cache_key = self._dependency_check_cache_key(extra_packages=extra_packages)
        cached = getattr(self, "_last_dependency_check", None) or {}
        if (not ignore_cache) and (not force_sync) and cached.get("ok") and cached.get("key") == cache_key:
            return True
        if not visual:
            try:
                result = lz._ensure_runtime_dependencies(
                    self.agent_dir,
                    extra_packages=extra_packages,
                    progress=None,
                    force_sync=force_sync,
                )
            except Exception as e:
                result = {"ok": False, "python": "", "error": str(e)}
            ok, _py, _err = self._apply_dependency_check_result(result, extra_packages=extra_packages)
            self._refresh_welcome_state()
            return ok

        dlg = QDialog(self)
        dlg.setModal(True)
        dlg.resize(760, 520)
        dlg.setMinimumSize(640, 420)
        dlg.setWindowTitle(f"{purpose}前检查依赖")

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel(f"{purpose}前依赖检查")
        title.setObjectName("cardTitle")
        layout.addWidget(title)

        desc = QLabel(self._dependency_check_desc_text(extra_packages=extra_packages))
        desc.setWordWrap(True)
        desc.setObjectName("cardDesc")
        layout.addWidget(desc)

        status_label = QLabel("准备开始…")
        status_label.setWordWrap(True)
        status_label.setObjectName("bodyText")
        layout.addWidget(status_label)

        progress = QProgressBar()
        progress.setRange(0, 0)
        progress.setTextVisible(False)
        progress.setFixedHeight(6)
        layout.addWidget(progress)

        log_box = QTextEdit()
        log_box.setReadOnly(True)
        log_box.setMinimumHeight(280)
        layout.addWidget(log_box, 1)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addStretch(1)
        close_btn = QPushButton("检查中…")
        close_btn.setEnabled(False)
        close_btn.setStyleSheet(self._action_button_style())
        actions.addWidget(close_btn, 0)
        layout.addLayout(actions)

        event_queue: queue.Queue = queue.Queue()
        result_holder = {"done": False, "ok": False, "python": "", "error": ""}

        def emit_line(text):
            txt = str(text or "").strip()
            if not txt:
                return
            log_box.append(txt)

        def worker():
            try:
                result = lz._ensure_runtime_dependencies(
                    self.agent_dir,
                    extra_packages=extra_packages,
                    progress=lambda ev: event_queue.put({"event": "progress", **dict(ev or {})}),
                    force_sync=force_sync,
                )
            except Exception as e:
                result = {"ok": False, "python": "", "error": str(e)}
            event_queue.put({"event": "done", "result": result})

        threading.Thread(target=worker, daemon=True).start()

        timer = QTimer(dlg)

        def drain():
            while True:
                try:
                    ev = event_queue.get_nowait()
                except queue.Empty:
                    break
                if ev.get("event") == "progress":
                    msg = str(ev.get("msg") or "").strip()
                    if msg:
                        status_label.setText(msg)
                        emit_line(msg)
                elif ev.get("event") == "done":
                    timer.stop()
                    result = ev.get("result") or {}
                    result_holder["done"] = True
                    result_holder["ok"], result_holder["python"], result_holder["error"] = self._apply_dependency_check_result(
                        result,
                        extra_packages=extra_packages,
                    )
                    if result_holder["ok"]:
                        py = result_holder["python"]
                        progress.setRange(0, 1)
                        progress.setValue(1)
                        status_label.setText(f"依赖检查完成，将使用：{py or '自动探测 Python'}")
                        emit_line("依赖检查完成。")
                        close_btn.setText("继续")
                        close_btn.setEnabled(True)
                        QTimer.singleShot(500, dlg.accept)
                    else:
                        progress.setRange(0, 1)
                        progress.setValue(0)
                        status_label.setText(result_holder["error"] or "依赖检查失败。")
                        if result_holder["error"]:
                            emit_line(result_holder["error"])
                        close_btn.setText("关闭")
                        close_btn.setEnabled(True)

        timer.timeout.connect(drain)
        timer.start(40)
        close_btn.clicked.connect(dlg.accept)
        dlg.exec()
        self._refresh_welcome_state()
        return bool(result_holder["ok"])

    def _check_runtime_dependencies_from_locate(self):
        combo = getattr(self, "locate_dependency_installer_combo", None)
        if combo is not None:
            selected = str(combo.currentData() or "").strip().lower()
            if selected not in ("auto", "uv", "pip"):
                selected = "auto"
            self.cfg["dependency_installer"] = selected
            lz.save_config(self.cfg)
        if not lz.is_valid_agent_dir(self.agent_dir):
            self._set_agent_dir(self.locate_path_edit.text().strip() if hasattr(self, "locate_path_edit") else self.agent_dir)
        if not lz.is_valid_agent_dir(self.agent_dir):
            return
        self._check_runtime_dependencies(purpose="载入内核", force_sync=False, ignore_cache=True)
