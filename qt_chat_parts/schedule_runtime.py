from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from launcher_app import core as lz
from launcher_app.theme import C, F


class ScheduleRuntimeMixin:
    def _schedule_time_label(self, ts):
        try:
            value = float(ts)
        except Exception:
            value = time.time()
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))
        except Exception:
            return str(value)

    def _scheduler_cfg_bucket(self):
        bucket = self.cfg.get("scheduler_runtime")
        if not isinstance(bucket, dict):
            bucket = {}
            self.cfg["scheduler_runtime"] = bucket
        return bucket

    def _scheduler_is_auto_start(self):
        return bool(self._scheduler_cfg_bucket().get("auto_start", False))

    def _scheduler_set_auto_start(self, enabled, *, persist=True):
        self._scheduler_cfg_bucket()["auto_start"] = bool(enabled)
        if persist:
            lz.save_config(self.cfg)

    def _scheduler_proc_alive(self):
        proc = getattr(self, "_scheduler_proc", None)
        return bool(proc and proc.poll() is None)

    def _scheduler_external_running(self):
        if not lz.is_valid_agent_dir(self.agent_dir):
            return False
        return bool(lz.detect_scheduler_lock())

    def _scheduler_close_log_handle(self):
        handle = getattr(self, "_scheduler_log_handle", None)
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        self._scheduler_log_handle = None

    def _scheduler_launcher_log_path(self):
        if not lz.is_valid_agent_dir(self.agent_dir):
            return ""
        path = os.path.join(self.agent_dir, "temp", "launcher_scheduler_runtime.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def _scheduler_launcher_log_tail(self, limit=2500):
        path = self._scheduler_launcher_log_path()
        if not path or not os.path.isfile(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()[-max(1, int(limit)) :].strip()
        except Exception:
            return ""

    def _scheduler_cleanup_if_exited(self):
        proc = getattr(self, "_scheduler_proc", None)
        if proc is None or proc.poll() is None:
            return
        self._scheduler_last_exit_code = proc.returncode
        self._scheduler_proc = None
        self._scheduler_close_log_handle()

    def _get_schedule_task_state_rows(self):
        states = getattr(self, "_schedule_task_state_rows_data", None)
        if not isinstance(states, list):
            states = []
            self._schedule_task_state_rows_data = states
        return states

    def _schedule_last_data(self):
        data = getattr(self, "_schedule_last_data_snapshot", None)
        return data if isinstance(data, dict) else {}

    def _schedule_combo_style(self):
        styler = getattr(self, "_api_combo_style", None)
        if callable(styler):
            return styler()
        return (
            f"QComboBox {{ background: {C['field_bg']}; color: {C['text']}; "
            f"border: 1px solid {C['stroke_default']}; border-radius: {F['radius_md']}px; "
            f"padding: 6px 28px 6px 10px; min-height: 20px; }}"
            f"QComboBox:hover {{ border-color: {C['stroke_hover']}; }}"
            f"QComboBox:focus {{ border-color: {C['stroke_focus']}; }}"
            f"QComboBox::drop-down {{ border: none; width: 22px; }}"
            f"QComboBox::down-arrow {{ image: none; width: 0px; height: 0px; "
            f"border-left: 5px solid transparent; border-right: 5px solid transparent; "
            f"border-top: 6px solid {C['muted']}; margin-right: 8px; }}"
            f"QComboBox QAbstractItemView {{ background: {C['layer1']}; color: {C['text']}; "
            f"border: 1px solid {C['stroke_hover']}; border-radius: {F['radius_md']}px; padding: 4px; "
            f"selection-background-color: {C['accent_soft_bg']}; selection-color: {C['text']}; outline: 0; }}"
        )

    def _schedule_spin_style(self):
        return (
            f"QSpinBox {{ background: {C['field_bg']}; color: {C['text']}; "
            f"border: 1px solid {C['stroke_default']}; border-radius: {F['radius_md']}px; padding: 8px 10px; min-width: 92px; }}"
            f"QSpinBox::up-button, QSpinBox::down-button {{ width: 20px; border: none; background: transparent; }}"
        )

    def _schedule_open_path(self, path):
        target = str(path or "").strip()
        if not target or not os.path.exists(target):
            QMessageBox.warning(self, "路径不存在", "目标文件或目录不存在。")
            return
        try:
            if os.name == "nt":
                os.startfile(target)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
        except Exception as e:
            QMessageBox.warning(self, "打开失败", str(e))

    def _schedule_badge(self, status_text, status_code):
        fg, bg = self._schedule_status_style(status_code)
        badge = QLabel(str(status_text or "未知"))
        badge.setStyleSheet(
            f"color: {fg}; background: {bg}; border-radius: 10px; padding: 4px 10px; font-size: 12px; font-weight: 600;"
        )
        return badge

    def _schedule_status_style(self, status_code):
        code = str(status_code or "").strip().lower()
        if code in ("running", "ready"):
            return C["success"], C["success_soft"]
        if code in ("cooldown", "starting"):
            return C["accent"], C["accent_soft_bg"]
        if code == "disabled":
            return C["muted"], C["layer2"]
        if code == "never_run":
            return C["warning"], C["warning_soft"]
        return C["danger_text"], C["error_soft"]

    def _schedule_metric_card(self, title, value, detail="", *, accent=False):
        card = self._panel_card()
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(4)
        head = QLabel(title)
        head.setObjectName("mutedText")
        box.addWidget(head)
        body = QLabel(str(value or "0"))
        body.setStyleSheet(
            f"color: {C['accent_text'] if accent else C['text']}; font-size: {F['font_title']}px; font-weight: 700; background: transparent;"
        )
        box.addWidget(body)
        if detail:
            tail = QLabel(detail)
            tail.setWordWrap(True)
            tail.setObjectName("softTextSmall")
            box.addWidget(tail)
        box.addStretch(1)
        return card

    def _schedule_info_chip_row(self, pairs):
        row_host = QWidget()
        row = QHBoxLayout(row_host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        for title, value in pairs:
            block = QFrame()
            block.setObjectName("cardInset")
            inner = QVBoxLayout(block)
            inner.setContentsMargins(12, 10, 12, 10)
            inner.setSpacing(2)
            head = QLabel(title)
            head.setObjectName("mutedText")
            inner.addWidget(head)
            body = QLabel(str(value or ""))
            body.setWordWrap(True)
            body.setObjectName("bodyText")
            inner.addWidget(body)
            row.addWidget(block, 1)
        return row_host

    def _schedule_make_state(self, row=None):
        payload = dict(row or {})
        default_id = f"task_{time.strftime('%m%d_%H%M%S')}"
        task_id = str(payload.get("id") or default_id).strip()
        extra_fields = dict(payload.get("extra_fields") or {})
        extra_text = json.dumps(extra_fields, ensure_ascii=False, indent=2) if extra_fields else ""
        return {
            "original_id": task_id if row else "",
            "task_id": task_id,
            "enabled": bool(payload.get("enabled", False)),
            "schedule": str(payload.get("schedule", "08:00") or "08:00").strip() or "08:00",
            "repeat": str(payload.get("repeat", "daily") or "daily").strip() or "daily",
            "prompt": str(payload.get("prompt", "") or ""),
            "max_delay_hours": int(payload.get("max_delay_hours", 6) or 6),
            "extra_json": extra_text,
            "advanced_expanded": bool(extra_fields),
            "status": str(payload.get("status") or ("草稿" if not row else "未知")),
            "status_code": str(payload.get("status_code") or ("disabled" if not row else "")),
            "parse_error": str(payload.get("parse_error") or ""),
            "last_run_at": str(payload.get("last_run_at") or ""),
            "next_ready_at": str(payload.get("next_ready_at") or ""),
            "latest_report_name": str(payload.get("latest_report_name") or ""),
            "latest_report_path": str(payload.get("latest_report_path") or ""),
            "report_count": int(payload.get("report_count") or 0),
            "file_name": str(payload.get("file_name") or ""),
            "path": str(payload.get("path") or ""),
            "save_status": "",
            "is_new": not bool(row),
        }

    def _schedule_collect_state_payload(self, state):
        raw_task_id = str(state.get("task_id") or "").strip()
        task_id = lz.normalize_scheduled_task_id(raw_task_id)
        if not task_id:
            raise ValueError("任务名不能为空，且不能只包含空格或非法文件名字符。")
        schedule_text = str(state.get("schedule") or "").strip()
        try:
            hour_text, minute_text = schedule_text.split(":", 1)
            hour = int(hour_text)
            minute = int(minute_text)
        except Exception as e:
            raise ValueError("执行时间需要是 HH:MM 格式。") from e
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError("执行时间超出范围。")

        repeat_text = str(state.get("repeat") or "").strip()
        if not repeat_text:
            raise ValueError("重复规则不能为空。")

        extra_text = str(state.get("extra_json") or "").strip()
        extra_fields = {}
        if extra_text:
            try:
                extra_fields = json.loads(extra_text)
            except Exception as e:
                raise ValueError("额外字段需要填写合法的 JSON 对象。") from e
            if not isinstance(extra_fields, dict):
                raise ValueError("额外字段必须是 JSON 对象。")
        for key in ("schedule", "repeat", "enabled", "prompt", "max_delay_hours"):
            extra_fields.pop(key, None)

        payload = {
            "schedule": f"{hour:02d}:{minute:02d}",
            "repeat": repeat_text,
            "enabled": bool(state.get("enabled")),
            "prompt": str(state.get("prompt") or ""),
            "max_delay_hours": int(state.get("max_delay_hours") or 0),
            "extra_fields": extra_fields,
        }
        return task_id, payload

    def _schedule_summary_status(self, task_data=None):
        self._scheduler_cleanup_if_exited()
        data = task_data if isinstance(task_data, dict) else self._schedule_last_data()
        enabled_count = int(data.get("enabled_count") or 0)
        if self._scheduler_proc_alive():
            return {
                "text": "运行中",
                "code": "running",
                "detail": f"启动器托管中 · PID {int(getattr(self._scheduler_proc, 'pid', 0) or 0)}",
            }
        if self._scheduler_external_running():
            return {"text": "运行中", "code": "running", "detail": "检测到上游 scheduler 已在后台运行。"}
        exit_code = getattr(self, "_scheduler_last_exit_code", None)
        if enabled_count > 0:
            if exit_code is not None:
                return {
                    "text": "已启用",
                    "code": "error",
                    "detail": f"已有 {enabled_count} 个启用任务，但上次启动退出码为 {exit_code}。",
                }
            return {
                "text": "已启用",
                "code": "cooldown",
                "detail": f"已有 {enabled_count} 个启用任务；当前未检测到运行中的调度器。",
            }
        if exit_code is not None:
            return {"text": f"已退出 ({exit_code})", "code": "error", "detail": "上次启动后进程已退出。"}
        return {"text": "未启用", "code": "disabled", "detail": "当前没有启用的定时任务。"}

    def _after_scheduler_launch_check(self, *, show_errors=True):
        self._scheduler_cleanup_if_exited()
        if self._scheduler_proc_alive() or self._scheduler_external_running():
            self._reload_schedule_panel()
            return
        if show_errors:
            tail = self._scheduler_launcher_log_tail() or "(空)"
            QMessageBox.warning(self, "调度器启动失败", f"scheduler 进程启动后已退出。\n\n日志尾部：\n{tail}")
        self._reload_schedule_panel()

    def _start_scheduler_process(self, show_errors=True):
        if self._scheduler_proc_alive() or self._scheduler_external_running():
            self._reload_schedule_panel()
            return True
        if not lz.is_valid_agent_dir(self.agent_dir):
            if show_errors:
                QMessageBox.warning(self, "目录无效", "请先选择有效的 GenericAgent 目录。")
            return False
        paths = lz.upstream_scheduler_paths(self.agent_dir)
        scheduler_py = paths.get("scheduler_py", "")
        agentmain_py = os.path.join(self.agent_dir, "agentmain.py")
        if not os.path.isfile(scheduler_py):
            if show_errors:
                QMessageBox.warning(self, "缺少 scheduler", f"未找到上游调度器脚本：\n{scheduler_py}")
            return False
        if not os.path.isfile(agentmain_py):
            if show_errors:
                QMessageBox.warning(self, "目录无效", f"未找到 agentmain.py：\n{agentmain_py}")
            return False
        if not self._check_runtime_dependencies(purpose="启动定时任务调度器", visual=bool(show_errors)):
            return False

        py = lz._resolve_config_path(str(self.cfg.get("python_exe") or "").strip()) or lz._find_system_python()
        if not py or not os.path.isfile(py):
            if show_errors:
                QMessageBox.critical(self, "缺少 Python", "依赖检查完成后仍未找到可用的 Python 可执行文件。")
            return False

        log_path = self._scheduler_launcher_log_path()
        try:
            log_handle = open(log_path, "a", encoding="utf-8", buffering=1)
            log_handle.write(f"\n==== {self._schedule_time_label(time.time())} start scheduler ====\n")
            proc = lz._popen_external_subprocess(
                [py, agentmain_py, "--reflect", scheduler_py, "--llm_no", str(int(getattr(self, "_current_llm_index", lambda: 0)() or 0))],
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

        self._scheduler_proc = proc
        self._scheduler_log_handle = log_handle
        self._scheduler_last_exit_code = None
        QTimer.singleShot(1200, lambda se=show_errors: self._after_scheduler_launch_check(show_errors=se))
        self._reload_schedule_panel()
        return True

    def _stop_scheduler_process(self, *, refresh=True):
        proc = getattr(self, "_scheduler_proc", None)
        if proc is None:
            if refresh:
                self._reload_schedule_panel()
            return False
        try:
            if proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                        proc.wait(timeout=2)
                    except Exception:
                        pass
        finally:
            self._scheduler_last_exit_code = proc.returncode
            self._scheduler_proc = None
            self._scheduler_close_log_handle()
            if refresh:
                self._reload_schedule_panel()
        return True

    def _start_autostart_scheduler(self):
        if self._scheduler_is_auto_start() and (not self._scheduler_proc_alive()) and (not self._scheduler_external_running()):
            self._start_scheduler_process(show_errors=False)

    def _schedule_sync_runtime_with_enabled_tasks(self, *, show_errors=False):
        if not lz.is_valid_agent_dir(self.agent_dir):
            return
        fresh = lz.list_scheduled_tasks(self.agent_dir)
        enabled_count = int(fresh.get("enabled_count") or 0)
        self._scheduler_set_auto_start(enabled_count > 0)
        if enabled_count > 0:
            if (not self._scheduler_proc_alive()) and (not self._scheduler_external_running()):
                self._start_scheduler_process(show_errors=show_errors)
            else:
                self._reload_schedule_panel()
        else:
            if self._scheduler_proc_alive():
                self._stop_scheduler_process(refresh=False)
            self._reload_schedule_panel()

    def _schedule_add_task_card(self):
        if not hasattr(self, "settings_schedule_list_layout"):
            return
        self._get_schedule_task_state_rows().insert(0, self._schedule_make_state())
        self.settings_schedule_notice.setText("已新增一张未保存的任务卡片。保存后会写入上游 sche_tasks。")
        self._render_schedule_panel(self._schedule_last_data())

    def _schedule_save_task_state(self, state, *, show_message=False):
        try:
            task_id, payload = self._schedule_collect_state_payload(state)
            result = lz.save_scheduled_task(self.agent_dir, task_id, payload, original_id=state.get("original_id") or None)
        except Exception as e:
            state["save_status"] = str(e)
            self._render_schedule_panel(self._schedule_last_data())
            if show_message:
                QMessageBox.warning(self, "保存失败", str(e))
            return False

        state["original_id"] = result["task_id"]
        state["task_id"] = result["task_id"]
        state["save_status"] = f"已保存到 {result['task_id']}.json"
        self.settings_schedule_notice.setText(
            f"已保存任务：{result['task_id']}。AI 或上游新增任务后，也可以点“刷新任务”重新读入。"
        )
        self._schedule_sync_runtime_with_enabled_tasks(show_errors=bool(state.get("enabled")))
        return True

    def _schedule_delete_task_state(self, state):
        task_id = str(state.get("original_id") or state.get("task_id") or "").strip()
        if not task_id:
            states = self._get_schedule_task_state_rows()
            if state in states:
                states.remove(state)
            self.settings_schedule_notice.setText("已移除未保存的任务草稿。")
            self._render_schedule_panel(self._schedule_last_data())
            return
        confirm = QMessageBox.question(self, "删除任务", f"确定删除任务 `{task_id}` 吗？")
        if confirm != QMessageBox.Yes:
            return
        try:
            lz.delete_scheduled_task(self.agent_dir, task_id)
        except Exception as e:
            QMessageBox.warning(self, "删除失败", str(e))
            return
        self.settings_schedule_notice.setText(f"已删除任务：{task_id}")
        self._schedule_sync_runtime_with_enabled_tasks(show_errors=False)

    def _reload_schedule_panel(self):
        if not hasattr(self, "settings_schedule_notice"):
            return
        self._clear_layout(self.settings_schedule_list_layout)
        if not lz.is_valid_agent_dir(self.agent_dir):
            self.settings_schedule_notice.setText("请先选择有效的 GenericAgent 目录。")
            return

        self._scheduler_cleanup_if_exited()
        data = lz.list_scheduled_tasks(self.agent_dir)
        self._schedule_last_data_snapshot = data
        self._schedule_task_state_rows_data = [self._schedule_make_state(row) for row in (data.get("tasks") or [])]
        self.settings_schedule_notice.setText(
            f"已识别 {len(data.get('tasks') or [])} 个上游任务。刷新后会重新读取 AI 或上游写入的 sche_tasks/*.json。"
        )
        self._render_schedule_panel(data)

    def _render_schedule_panel(self, data):
        self._clear_layout(self.settings_schedule_list_layout)
        paths = dict((data or {}).get("paths") or {})
        runtime = self._schedule_summary_status(data)

        runtime_card = self._panel_card()
        runtime_box = QVBoxLayout(runtime_card)
        runtime_box.setContentsMargins(16, 14, 16, 14)
        runtime_box.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("调度状态")
        title.setObjectName("cardTitle")
        head.addWidget(title, 0)
        head.addStretch(1)
        head.addWidget(self._schedule_badge(runtime["text"], runtime["code"]), 0)
        runtime_box.addLayout(head)

        runtime_desc = QLabel(runtime.get("detail") or "")
        runtime_desc.setWordWrap(True)
        runtime_desc.setObjectName("softTextSmall")
        runtime_box.addWidget(runtime_desc)

        runtime_box.addWidget(
            self._schedule_info_chip_row(
                [
                    ("任务目录", paths.get("tasks_dir", "")),
                    ("报告目录", paths.get("done_dir", "")),
                    ("日志文件", paths.get("log_path", "")),
                ]
            )
        )

        controls = QHBoxLayout()
        controls.setSpacing(8)
        new_btn = QPushButton("新建任务")
        new_btn.setStyleSheet(self._action_button_style(primary=True))
        new_btn.clicked.connect(self._schedule_add_task_card)
        controls.addWidget(new_btn, 0)
        refresh_btn = QPushButton("刷新任务")
        refresh_btn.setStyleSheet(self._action_button_style())
        refresh_btn.clicked.connect(self._reload_schedule_panel)
        controls.addWidget(refresh_btn, 0)
        controls.addStretch(1)
        start_btn = QPushButton("启动调度器")
        start_btn.setStyleSheet(self._action_button_style())
        start_btn.clicked.connect(lambda: self._start_scheduler_process(show_errors=True))
        start_btn.setEnabled((not self._scheduler_proc_alive()) and (not self._scheduler_external_running()))
        controls.addWidget(start_btn, 0)
        stop_btn = QPushButton("停止调度器")
        stop_btn.setStyleSheet(self._action_button_style())
        stop_btn.clicked.connect(lambda: self._stop_scheduler_process(refresh=True))
        stop_btn.setEnabled(self._scheduler_proc_alive())
        controls.addWidget(stop_btn, 0)
        runtime_box.addLayout(controls)
        self.settings_schedule_list_layout.addWidget(runtime_card)

        summary_grid = QGridLayout()
        summary_grid.setSpacing(10)
        summary_cards = [
            self._schedule_metric_card("任务数", len((data or {}).get("tasks") or []), "已识别的 sche_tasks/*.json", accent=True),
            self._schedule_metric_card("已启用", int((data or {}).get("enabled_count") or 0), "enabled=true 的任务"),
            self._schedule_metric_card("待触发", sum(1 for row in ((data or {}).get("tasks") or []) if row.get("status_code") == "ready"), "当前满足触发窗口"),
            self._schedule_metric_card("配置错误", int((data or {}).get("error_count") or 0), "repeat / schedule / JSON 异常"),
        ]
        for idx, card in enumerate(summary_cards):
            summary_grid.addWidget(card, 0, idx)
        self.settings_schedule_list_layout.addLayout(summary_grid)

        states = self._get_schedule_task_state_rows()
        if not states:
            empty = self._panel_card()
            box = QVBoxLayout(empty)
            box.setContentsMargins(16, 14, 16, 14)
            box.setSpacing(6)
            title = QLabel("暂无任务")
            title.setObjectName("cardTitle")
            box.addWidget(title)
            desc = QLabel("上游 `sche_tasks` 目录里还没有任务。点击上面的“新建任务”即可直接创建。")
            desc.setWordWrap(True)
            desc.setObjectName("cardDesc")
            box.addWidget(desc)
            self.settings_schedule_list_layout.addWidget(empty)
        else:
            self._render_schedule_task_cards(states, paths)

        self._render_schedule_log_cards()
        self.settings_schedule_list_layout.addStretch(1)

    def _render_schedule_task_cards(self, states, paths):
        repeat_map = {value: label for value, label in lz.scheduler_repeat_options()}
        for idx, state in enumerate(states):
            card = self._panel_card()
            body = QVBoxLayout(card)
            body.setContentsMargins(16, 14, 16, 14)
            body.setSpacing(10)

            head = QHBoxLayout()
            title = QLabel(state.get("original_id") or f"新任务 {idx + 1}")
            title.setObjectName("cardTitle")
            head.addWidget(title, 0)
            meta = QLabel(f"{state.get('repeat', 'daily')} · {state.get('schedule', '08:00')}")
            meta.setObjectName("mutedText")
            head.addWidget(meta, 0)
            head.addStretch(1)
            head.addWidget(self._schedule_badge(state.get("status") or "草稿", state.get("status_code") or "disabled"), 0)
            body.addLayout(head)

            row1 = QHBoxLayout()
            row1.setSpacing(10)
            row1.addWidget(QLabel("任务名"), 0)
            task_edit = QLineEdit()
            task_edit.setPlaceholderText("例如 morning_report")
            task_edit.setText(str(state.get("task_id") or ""))
            row1.addWidget(task_edit, 2)
            enable_box = QCheckBox("启用")
            enable_box.setChecked(bool(state.get("enabled")))
            row1.addWidget(enable_box, 0)
            body.addLayout(row1)

            row2 = QHBoxLayout()
            row2.setSpacing(10)
            row2.addWidget(QLabel("执行时间"), 0)
            schedule_edit = QLineEdit()
            schedule_edit.setPlaceholderText("HH:MM")
            schedule_edit.setText(str(state.get("schedule") or "08:00"))
            row2.addWidget(schedule_edit, 1)
            row2.addWidget(QLabel("重复"), 0)
            repeat_box = QComboBox()
            repeat_box.setEditable(True)
            repeat_box.setStyleSheet(self._schedule_combo_style())
            repeat_box.addItem("", "")
            for value, label in lz.scheduler_repeat_options():
                repeat_box.addItem(f"{label} ({value})", value)
            current_repeat = str(state.get("repeat") or "daily")
            repeat_idx = repeat_box.findData(current_repeat)
            if repeat_idx >= 0:
                repeat_box.setCurrentIndex(repeat_idx)
            else:
                repeat_box.setCurrentText(current_repeat)
            row2.addWidget(repeat_box, 2)
            body.addLayout(row2)

            prompt_label = QLabel("任务内容")
            prompt_label.setObjectName("mutedText")
            body.addWidget(prompt_label)
            prompt_edit = QTextEdit()
            prompt_edit.setMinimumHeight(120)
            prompt_edit.setPlaceholderText("到点后要执行的 prompt。")
            prompt_edit.setPlainText(str(state.get("prompt") or ""))
            body.addWidget(prompt_edit)

            body.addWidget(
                self._schedule_info_chip_row(
                    [
                        ("上次执行", state.get("last_run_at") or "从未执行"),
                        ("下次可触发", state.get("next_ready_at") or "数据不足"),
                        ("报告数", str(int(state.get("report_count") or 0))),
                        ("最新报告", state.get("latest_report_name") or "暂无"),
                    ]
                )
            )

            status_label = QLabel("")
            status_label.setWordWrap(True)
            status_label.setObjectName("mutedText")
            body.addWidget(status_label)

            advanced_toggle = QPushButton()
            advanced_toggle.setCheckable(True)
            advanced_toggle.setChecked(bool(state.get("advanced_expanded", False)))
            advanced_toggle.setStyleSheet(self._action_button_style(kind="subtle"))
            body.addWidget(advanced_toggle)

            advanced_wrap = QWidget()
            advanced_layout = QVBoxLayout(advanced_wrap)
            advanced_layout.setContentsMargins(0, 4, 0, 0)
            advanced_layout.setSpacing(8)
            body.addWidget(advanced_wrap)

            adv_row = QHBoxLayout()
            adv_row.setSpacing(10)
            adv_row.addWidget(QLabel("max_delay_hours"), 0)
            delay_spin = QSpinBox()
            delay_spin.setRange(0, 168)
            delay_spin.setValue(int(state.get("max_delay_hours") or 0))
            delay_spin.setStyleSheet(self._schedule_spin_style())
            adv_row.addWidget(delay_spin, 0)
            adv_row.addStretch(1)
            advanced_layout.addLayout(adv_row)

            extra_title = QLabel("额外字段（JSON 对象）")
            extra_title.setObjectName("mutedText")
            advanced_layout.addWidget(extra_title)
            extra_edit = QTextEdit()
            extra_edit.setMinimumHeight(96)
            extra_edit.setPlaceholderText('例如 { "max_tokens": 2048 }')
            extra_edit.setPlainText(str(state.get("extra_json") or ""))
            advanced_layout.addWidget(extra_edit)

            path_card = QFrame()
            path_card.setObjectName("cardInset")
            path_box = QVBoxLayout(path_card)
            path_box.setContentsMargins(12, 10, 12, 10)
            path_box.setSpacing(4)
            path_hint = QLabel(
                f"任务文件：{state.get('path') or '保存后生成'}\n最新报告：{state.get('latest_report_path') or '暂无'}"
            )
            path_hint.setWordWrap(True)
            path_hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
            path_hint.setObjectName("softTextSmall")
            path_box.addWidget(path_hint)
            advanced_layout.addWidget(path_card)

            footer = QHBoxLayout()
            footer.setSpacing(8)
            save_btn = QPushButton("保存任务")
            save_btn.setStyleSheet(self._action_button_style(primary=True))
            footer.addWidget(save_btn, 0)
            delete_btn = QPushButton("删除任务")
            delete_btn.setStyleSheet(self._action_button_style(kind="destructive"))
            footer.addWidget(delete_btn, 0)
            report_btn = QPushButton("打开最新报告")
            report_btn.setStyleSheet(self._action_button_style())
            report_btn.setEnabled(bool(state.get("latest_report_path")))
            footer.addWidget(report_btn, 0)
            folder_btn = QPushButton("打开任务目录")
            folder_btn.setStyleSheet(self._action_button_style())
            footer.addWidget(folder_btn, 0)
            footer.addStretch(1)
            body.addLayout(footer)

            def sync_status_label(s=state, label=status_label):
                text = str(s.get("save_status") or s.get("parse_error") or "").strip()
                if not text:
                    repeat_value = str(s.get("repeat") or "daily")
                    repeat_display = repeat_map.get(repeat_value, repeat_value)
                    text = (
                        f"启用后会自动联动调度器；当前重复规则：{repeat_display}，"
                        f"时间：{s.get('schedule') or '08:00'}。"
                    )
                label.setText(text)

            def sync_advanced_fold(checked=None, s=state, btn=advanced_toggle, panel=advanced_wrap):
                flag = bool(btn.isChecked() if checked is None else checked)
                s["advanced_expanded"] = flag
                btn.setText(("▾ " if flag else "▸ ") + "高级参数")
                panel.setVisible(flag)

            task_edit.textChanged.connect(lambda text, s=state: s.__setitem__("task_id", text))
            schedule_edit.textChanged.connect(lambda text, s=state: s.__setitem__("schedule", text.strip()))

            def on_repeat_changed(_=0, s=state, box=repeat_box, label=status_label):
                text = str(box.currentData() or box.currentText() or "").strip()
                s["repeat"] = text
                sync_status_label(s, label)

            repeat_box.currentIndexChanged.connect(on_repeat_changed)
            repeat_box.editTextChanged.connect(lambda text, s=state, label=status_label: (s.__setitem__("repeat", text.strip()), sync_status_label(s, label)))
            prompt_edit.textChanged.connect(lambda s=state, edit=prompt_edit: s.__setitem__("prompt", edit.toPlainText()))
            delay_spin.valueChanged.connect(lambda value, s=state: s.__setitem__("max_delay_hours", int(value)))
            extra_edit.textChanged.connect(lambda s=state, edit=extra_edit: s.__setitem__("extra_json", edit.toPlainText()))
            advanced_toggle.toggled.connect(sync_advanced_fold)

            def on_enabled_toggled(checked, s=state):
                s["enabled"] = bool(checked)
                self._schedule_save_task_state(s, show_message=False)

            enable_box.toggled.connect(on_enabled_toggled)
            save_btn.clicked.connect(lambda _=False, s=state: self._schedule_save_task_state(s, show_message=True))
            delete_btn.clicked.connect(lambda _=False, s=state: self._schedule_delete_task_state(s))
            report_btn.clicked.connect(lambda _=False, s=state: self._schedule_open_path(s.get("latest_report_path")))
            folder_btn.clicked.connect(lambda _=False, p=paths.get("tasks_dir", ""): self._schedule_open_path(p))

            sync_status_label()
            sync_advanced_fold()
            self.settings_schedule_list_layout.addWidget(card)

    def _render_schedule_log_cards(self):
        log_grid = QGridLayout()
        log_grid.setSpacing(10)

        upstream_log_card = self._panel_card()
        upstream_log_box = QVBoxLayout(upstream_log_card)
        upstream_log_box.setContentsMargins(16, 14, 16, 14)
        upstream_log_box.setSpacing(8)
        upstream_title = QLabel("调度日志")
        upstream_title.setObjectName("cardTitle")
        upstream_log_box.addWidget(upstream_title)
        upstream_tail = lz.tail_scheduler_log(self.agent_dir, limit=2200)
        upstream_text = QLabel(upstream_tail or "暂无 scheduler.log。")
        upstream_text.setWordWrap(True)
        upstream_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        upstream_text.setObjectName("tokenTree")
        upstream_log_box.addWidget(upstream_text)
        log_grid.addWidget(upstream_log_card, 0, 0)

        runtime_log_card = self._panel_card()
        runtime_log_box = QVBoxLayout(runtime_log_card)
        runtime_log_box.setContentsMargins(16, 14, 16, 14)
        runtime_log_box.setSpacing(8)
        runtime_log_title = QLabel("启动日志")
        runtime_log_title.setObjectName("cardTitle")
        runtime_log_box.addWidget(runtime_log_title)
        runtime_tail = self._scheduler_launcher_log_tail() or "暂无启动日志。"
        runtime_log_text = QLabel(runtime_tail)
        runtime_log_text.setWordWrap(True)
        runtime_log_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        runtime_log_text.setObjectName("tokenTree")
        runtime_log_box.addWidget(runtime_log_text)
        log_grid.addWidget(runtime_log_card, 0, 1)

        self.settings_schedule_list_layout.addLayout(log_grid)
