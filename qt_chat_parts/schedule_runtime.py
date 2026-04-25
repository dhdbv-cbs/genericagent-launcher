from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
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

    def _schedule_target_context(self):
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

    def _schedule_target_device(self):
        target = self._schedule_target_context()
        if not bool(target.get("is_remote")):
            return None
        getter = getattr(self, "_settings_remote_device_by_id", None)
        if not callable(getter):
            return None
        try:
            device = getter(target.get("device_id"))
        except Exception:
            device = None
        return dict(device) if isinstance(device, dict) else None

    def _schedule_remote_cache_dir(self, device, *parts):
        item = device if isinstance(device, dict) else {}
        raw_id = str(item.get("id") or "remote").strip() or "remote"
        safe_id = "".join(ch for ch in raw_id if (ch.isalnum() or ch in ("_", "-", "."))).strip(" .")
        if not safe_id:
            safe_id = "remote"
        path = lz.launcher_data_path("remote_schedule_cache", safe_id, *[str(p or "").strip() for p in parts if str(p or "").strip()])
        os.makedirs(path, exist_ok=True)
        return path

    def _schedule_remote_local_name(self, remote_path: str, fallback: str = ""):
        raw = os.path.basename(str(remote_path or "").replace("\\", "/").rstrip("/"))
        name = str(raw or fallback or "remote_file").strip()
        safe = "".join(ch for ch in name if ch not in '\\/:*?"<>|')
        safe = safe.strip() or "remote_file"
        return safe[:180]

    def _schedule_download_remote_file(self, device, remote_path: str, *, local_subdir="files", preferred_name=""):
        remote_fp = str(remote_path or "").strip()
        if not remote_fp:
            raise RuntimeError("远端文件路径为空。")
        client, err = self._settings_target_open_remote_client(device, timeout=12)
        if client is None:
            raise RuntimeError(str(err or "SSH 连接失败。").strip() or "SSH 连接失败。")
        try:
            local_dir = self._schedule_remote_cache_dir(device, local_subdir)
            local_name = self._schedule_remote_local_name(remote_fp, fallback=preferred_name)
            target_path = os.path.join(local_dir, local_name)
            temp_path = target_path + ".tmp"
            sftp = client.open_sftp()
            try:
                with sftp.open(remote_fp, "rb") as src, open(temp_path, "wb") as dst:
                    while True:
                        chunk = src.read(1024 * 128)
                        if not chunk:
                            break
                        dst.write(chunk)
            finally:
                try:
                    sftp.close()
                except Exception:
                    pass
            os.replace(temp_path, target_path)
            return target_path
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _schedule_sync_remote_tasks_dir(self, device, remote_dir: str):
        target_dir = str(remote_dir or "").strip()
        if not target_dir:
            raise RuntimeError("远端任务目录为空。")
        client, err = self._settings_target_open_remote_client(device, timeout=12)
        if client is None:
            raise RuntimeError(str(err or "SSH 连接失败。").strip() or "SSH 连接失败。")
        try:
            local_dir = self._schedule_remote_cache_dir(device, "sche_tasks")
            for name in os.listdir(local_dir):
                if name.lower().endswith(".json"):
                    try:
                        os.remove(os.path.join(local_dir, name))
                    except Exception:
                        pass
            sftp = client.open_sftp()
            count = 0
            try:
                try:
                    names = list(sftp.listdir(target_dir))
                except Exception as e:
                    raise RuntimeError(f"读取远端任务目录失败：{e}") from e
                for name in names:
                    fn = str(name or "").strip()
                    if (not fn) or fn.startswith(".") or (not fn.lower().endswith(".json")):
                        continue
                    remote_fp = str(target_dir).rstrip("/") + "/" + fn
                    local_fp = os.path.join(local_dir, self._schedule_remote_local_name(fn, fallback="task.json"))
                    temp_path = local_fp + ".tmp"
                    with sftp.open(remote_fp, "rb") as src, open(temp_path, "wb") as dst:
                        while True:
                            chunk = src.read(1024 * 128)
                            if not chunk:
                                break
                            dst.write(chunk)
                    os.replace(temp_path, local_fp)
                    count += 1
            finally:
                try:
                    sftp.close()
                except Exception:
                    pass
            readme_path = os.path.join(local_dir, "_README.txt")
            try:
                with open(readme_path, "w", encoding="utf-8") as f:
                    f.write(
                        "这是启动器从远端设备同步下来的 sche_tasks 快照目录。\n"
                        "这里的文件只用于查看与备份；实际保存请仍然在启动器设置页里操作。\n"
                    )
            except Exception:
                pass
            return local_dir, count
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _schedule_run_remote_job(self, *, title: str, notice_text: str, worker, on_success=None):
        token = int(time.time() * 1000)
        self._schedule_remote_job_token = token
        notice = getattr(self, "settings_schedule_notice", None)
        if notice is not None and notice_text:
            notice.setText(str(notice_text))

        def runner():
            result = None
            error = ""
            try:
                result = worker()
            except Exception as e:
                error = str(e)

            def apply():
                if int(getattr(self, "_schedule_remote_job_token", 0) or 0) != token:
                    return
                if error:
                    if notice is not None:
                        notice.setText(f"{title}失败：{error}")
                    QMessageBox.warning(self, title, error)
                    return
                if callable(on_success):
                    on_success(result)

            poster = getattr(self, "_api_on_ui_thread", None)
            if callable(poster):
                try:
                    poster(apply)
                    return
                except Exception:
                    pass
            QTimer.singleShot(0, apply)

        threading.Thread(target=runner, name=f"schedule-remote-job-{token}", daemon=True).start()

    def _schedule_open_report(self, state):
        data = self._schedule_last_data()
        is_remote = bool(data.get("is_remote"))
        report_path = str((state or {}).get("latest_report_path") or "").strip()
        report_name = str((state or {}).get("latest_report_name") or "").strip()
        if not report_path:
            QMessageBox.information(self, "暂无报告", "当前任务还没有可打开的最新报告。")
            return
        if not is_remote:
            self._schedule_open_path(report_path)
            return
        device = self._schedule_target_device()
        if not isinstance(device, dict):
            QMessageBox.warning(self, "设备无效", "当前设置目标对应的远程设备不存在。")
            return

        def worker():
            return self._schedule_download_remote_file(
                device,
                report_path,
                local_subdir="reports",
                preferred_name=report_name or os.path.basename(report_path),
            )

        def done(local_path):
            local_fp = str(local_path or "").strip()
            if hasattr(self, "settings_schedule_notice"):
                self.settings_schedule_notice.setText(f"已下载远端报告到本地缓存：{local_fp}")
            self._schedule_open_path(local_fp)

        self._schedule_run_remote_job(
            title="下载远端报告",
            notice_text="正在从远端设备下载最新报告…",
            worker=worker,
            on_success=done,
        )

    def _schedule_open_tasks_dir(self, tasks_dir: str):
        data = self._schedule_last_data()
        is_remote = bool(data.get("is_remote"))
        target_dir = str(tasks_dir or "").strip()
        if not target_dir:
            QMessageBox.warning(self, "目录无效", "当前任务目录为空。")
            return
        if not is_remote:
            self._schedule_open_path(target_dir)
            return
        device = self._schedule_target_device()
        if not isinstance(device, dict):
            QMessageBox.warning(self, "设备无效", "当前设置目标对应的远程设备不存在。")
            return

        def worker():
            return self._schedule_sync_remote_tasks_dir(device, target_dir)

        def done(result):
            local_dir, count = result if isinstance(result, tuple) and len(result) == 2 else ("", 0)
            if hasattr(self, "settings_schedule_notice"):
                self.settings_schedule_notice.setText(
                    f"已同步远端任务目录到本地缓存，共 {int(count or 0)} 个任务文件。"
                )
            self._schedule_open_path(local_dir)

        self._schedule_run_remote_job(
            title="同步远端任务目录",
            notice_text="正在同步远端 sche_tasks 目录到本地缓存…",
            worker=worker,
            on_success=done,
        )

    def _schedule_open_log_file(self, *, title: str, path: str, local_subdir: str, preferred_name: str):
        log_path = str(path or "").strip()
        if not log_path:
            QMessageBox.information(self, title, "当前没有可用日志文件。")
            return
        data = self._schedule_last_data()
        is_remote = bool(data.get("is_remote"))
        if not is_remote:
            self._schedule_open_path(log_path)
            return
        device = self._schedule_target_device()
        if not isinstance(device, dict):
            QMessageBox.warning(self, "设备无效", "当前设置目标对应的远程设备不存在。")
            return

        def worker():
            return self._schedule_download_remote_file(
                device,
                log_path,
                local_subdir=local_subdir,
                preferred_name=preferred_name,
            )

        def done(local_path):
            local_fp = str(local_path or "").strip()
            if hasattr(self, "settings_schedule_notice"):
                self.settings_schedule_notice.setText(f"已下载远端日志到本地缓存：{local_fp}")
            self._schedule_open_path(local_fp)

        self._schedule_run_remote_job(
            title=title,
            notice_text=f"正在下载{title}…",
            worker=worker,
            on_success=done,
        )

    def _schedule_remote_exec_json(self, device, script_text: str, *, timeout=120):
        executor = getattr(self, "_remote_exec_json_script", None)
        if not callable(executor):
            return False, {}, "当前构建缺少远端命令执行能力。"
        dev = device if isinstance(device, dict) else {}
        try:
            return executor(dev, str(script_text or ""), timeout=timeout)
        except Exception as e:
            return False, {}, str(e)

    def _remote_schedule_status_script(self):
        return (
            "import json, os, socket, signal, time\n"
            "from datetime import datetime, timedelta\n"
            "base = os.getcwd()\n"
            "paths = {\n"
            "    'tasks_dir': os.path.join(base, 'sche_tasks'),\n"
            "    'done_dir': os.path.join(base, 'sche_tasks', 'done'),\n"
            "    'log_path': os.path.join(base, 'sche_tasks', 'scheduler.log'),\n"
            "    'scheduler_py': os.path.join(base, 'reflect', 'scheduler.py'),\n"
            "    'sop_path': os.path.join(base, 'memory', 'scheduled_task_sop.md'),\n"
            "    'launcher_log_path': os.path.join(base, 'temp', 'launcher_scheduler_runtime.log'),\n"
            "    'pid_path': os.path.join(base, 'temp', 'launcher_scheduler_runtime.pid'),\n"
            "}\n"
            "def read_text(path, limit):\n"
            "    if not os.path.isfile(path):\n"
            "        return ''\n"
            "    try:\n"
            "        with open(path, 'r', encoding='utf-8', errors='replace') as f:\n"
            "            return f.read()[-limit:].strip()\n"
            "    except Exception:\n"
            "        return ''\n"
            "def repeat_cooldown(repeat):\n"
            "    text = str(repeat or 'daily').strip().lower() or 'daily'\n"
            "    if text == 'once':\n"
            "        return timedelta(days=365000)\n"
            "    if text in ('daily', 'weekday'):\n"
            "        return timedelta(hours=20)\n"
            "    if text == 'weekly':\n"
            "        return timedelta(days=6)\n"
            "    if text == 'monthly':\n"
            "        return timedelta(days=27)\n"
            "    if text.startswith('every_'):\n"
            "        payload = text.split('_', 1)[1]\n"
            "        try:\n"
            "            count = int(payload[:-1])\n"
            "        except Exception:\n"
            "            return None\n"
            "        unit = payload[-1:]\n"
            "        if unit == 'h':\n"
            "            return timedelta(hours=count)\n"
            "        if unit == 'd':\n"
            "            return timedelta(days=count)\n"
            "        if unit == 'm':\n"
            "            return timedelta(minutes=count)\n"
            "    return None\n"
            "def last_run(task_id):\n"
            "    done_dir = paths['done_dir']\n"
            "    latest_ts = None\n"
            "    latest_name = ''\n"
            "    report_count = 0\n"
            "    if not os.path.isdir(done_dir):\n"
            "        return {'ts': None, 'name': '', 'path': '', 'count': 0}\n"
            "    suffix = f'_{task_id}.md'\n"
            "    for fn in os.listdir(done_dir):\n"
            "        if not fn.endswith(suffix):\n"
            "            continue\n"
            "        report_count += 1\n"
            "        try:\n"
            "            ts = datetime.strptime(fn[:15], '%Y-%m-%d_%H%M')\n"
            "        except Exception:\n"
            "            continue\n"
            "        if latest_ts is None or ts > latest_ts:\n"
            "            latest_ts = ts\n"
            "            latest_name = fn\n"
            "    return {'ts': latest_ts, 'name': latest_name, 'path': os.path.join(done_dir, latest_name) if latest_name else '', 'count': report_count}\n"
            "def pid_alive(pid):\n"
            "    try:\n"
            "        os.kill(int(pid), 0)\n"
            "        return True\n"
            "    except Exception:\n"
            "        return False\n"
            "def read_pid():\n"
            "    path = paths['pid_path']\n"
            "    if not os.path.isfile(path):\n"
            "        return 0\n"
            "    try:\n"
            "        with open(path, 'r', encoding='utf-8') as f:\n"
            "            text = f.read().strip()\n"
            "        return int(text or 0)\n"
            "    except Exception:\n"
            "        return 0\n"
            "pid = read_pid()\n"
            "lock_active = False\n"
            "try:\n"
            "    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "    s.settimeout(0.15)\n"
            "    lock_active = s.connect_ex(('127.0.0.1', 45762)) == 0\n"
            "    s.close()\n"
            "except Exception:\n"
            "    lock_active = False\n"
            "runtime_status = '未运行'\n"
            "runtime_code = 'disabled'\n"
            "runtime_detail = '未检测到 scheduler 进程。'\n"
            "if pid > 0 and pid_alive(pid):\n"
            "    runtime_status = '启动器托管中'\n"
            "    runtime_code = 'running'\n"
            "    runtime_detail = f'启动器托管中 · PID {pid}'\n"
            "elif lock_active:\n"
            "    runtime_status = '运行中'\n"
            "    runtime_code = 'running'\n"
            "    runtime_detail = '检测到上游 scheduler 已在后台运行。'\n"
            "elif pid > 0:\n"
            "    runtime_status = '已退出'\n"
            "    runtime_code = 'error'\n"
            "    runtime_detail = f'pidfile 指向 {pid}，但进程已不存在。'\n"
            "tasks = []\n"
            "errors = []\n"
            "if os.path.isdir(paths['tasks_dir']):\n"
            "    for fn in sorted(os.listdir(paths['tasks_dir'])):\n"
            "        if not fn.endswith('.json'):\n"
            "            continue\n"
            "        task_id = fn[:-5]\n"
            "        path = os.path.join(paths['tasks_dir'], fn)\n"
            "        row = {'id': task_id, 'file_name': fn, 'path': path, 'enabled': False, 'repeat': 'daily', 'schedule': '00:00', 'prompt': '', 'prompt_preview': '', 'max_delay_hours': 6, 'parse_error': '', 'status': '配置错误', 'status_code': 'error', 'last_run_at': '', 'last_run_ts': None, 'latest_report_name': '', 'latest_report_path': '', 'report_count': 0, 'next_ready_at': '', 'extra_fields': {}}\n"
            "        try:\n"
            "            with open(path, 'r', encoding='utf-8') as f:\n"
            "                data = json.load(f)\n"
            "            if not isinstance(data, dict):\n"
            "                raise ValueError('任务文件不是 JSON 对象')\n"
            "        except Exception as e:\n"
            "            row['parse_error'] = str(e)\n"
            "            row['prompt_preview'] = '任务文件解析失败'\n"
            "            tasks.append(row)\n"
            "            errors.append(f'{fn}: {e}')\n"
            "            continue\n"
            "        row['enabled'] = bool(data.get('enabled', False))\n"
            "        row['repeat'] = str(data.get('repeat', 'daily') or 'daily').strip() or 'daily'\n"
            "        row['schedule'] = str(data.get('schedule', '00:00') or '00:00').strip() or '00:00'\n"
            "        row['prompt'] = str(data.get('prompt', '') or '')\n"
            "        row['prompt_preview'] = ' '.join(row['prompt'].replace('\\r', '\\n').split())\n"
            "        if len(row['prompt_preview']) > 140:\n"
            "            row['prompt_preview'] = row['prompt_preview'][:139].rstrip() + '…'\n"
            "        try:\n"
            "            row['max_delay_hours'] = int(data.get('max_delay_hours', 6) or 6)\n"
            "        except Exception:\n"
            "            row['max_delay_hours'] = 6\n"
            "        row['extra_fields'] = {k: v for k, v in data.items() if k not in {'schedule', 'repeat', 'enabled', 'prompt', 'max_delay_hours'}}\n"
            "        cooldown = repeat_cooldown(row['repeat'])\n"
            "        lr = last_run(task_id)\n"
            "        row['last_run_ts'] = lr['ts']\n"
            "        row['last_run_at'] = lr['ts'].strftime('%Y-%m-%d %H:%M') if lr['ts'] else ''\n"
            "        row['latest_report_name'] = lr['name']\n"
            "        row['latest_report_path'] = lr['path']\n"
            "        row['report_count'] = int(lr['count'] or 0)\n"
            "        if lr['ts'] and cooldown is not None:\n"
            "            row['next_ready_at'] = (lr['ts'] + cooldown).strftime('%Y-%m-%d %H:%M')\n"
            "        try:\n"
            "            hour_text, minute_text = row['schedule'].split(':', 1)\n"
            "            hour = int(hour_text)\n"
            "            minute = int(minute_text)\n"
            "            if hour < 0 or hour > 23 or minute < 0 or minute > 59:\n"
            "                raise ValueError('时间超出范围')\n"
            "        except Exception as e:\n"
            "            row['parse_error'] = f'schedule 无效：{e}'\n"
            "            row['status'] = '配置错误'\n"
            "            row['status_code'] = 'error'\n"
            "            tasks.append(row)\n"
            "            errors.append(f'{fn}: schedule 无效')\n"
            "            continue\n"
            "        if cooldown is None:\n"
            "            row['parse_error'] = f'repeat 无效：{row[\"repeat\"]}'\n"
            "            row['status'] = '配置错误'\n"
            "            row['status_code'] = 'error'\n"
            "            tasks.append(row)\n"
            "            errors.append(f'{fn}: repeat 无效')\n"
            "            continue\n"
            "        if not row['enabled']:\n"
            "            row['status'] = '已禁用'\n"
            "            row['status_code'] = 'disabled'\n"
            "        elif row['last_run_ts'] is None:\n"
            "            row['status'] = '从未执行'\n"
            "            row['status_code'] = 'never_run'\n"
            "        elif datetime.now() < (row['last_run_ts'] + cooldown):\n"
            "            row['status'] = '冷却中'\n"
            "            row['status_code'] = 'cooldown'\n"
            "        else:\n"
            "            row['status'] = '待触发'\n"
            "            row['status_code'] = 'ready'\n"
            "        tasks.append(row)\n"
            "upstream_tail = read_text(paths['log_path'], 2200)\n"
            "runtime_tail = read_text(paths['launcher_log_path'], 2500)\n"
            "print(json.dumps({'ok': True, 'supported': os.path.isfile(paths['scheduler_py']), 'paths': paths, 'tasks': tasks, 'errors': errors, 'enabled_count': sum(1 for row in tasks if row.get('enabled')), 'error_count': sum(1 for row in tasks if row.get('status_code') == 'error'), 'runtime_status': runtime_status, 'runtime_code': runtime_code, 'runtime_detail': runtime_detail, 'scheduler_pid': pid, 'scheduler_lock_active': lock_active, 'upstream_log_tail': upstream_tail, 'launcher_log_tail': runtime_tail}, ensure_ascii=False))\n"
        )

    def _remote_schedule_snapshot(self, device):
        script = self._remote_schedule_status_script()
        ok, payload, err = self._schedule_remote_exec_json(device, script, timeout=120)
        if not ok:
            return False, {}, err
        if not isinstance(payload, dict):
            return False, {}, "远端返回格式异常。"
        if not bool(payload.get("ok", False)):
            return False, payload, str(payload.get("error") or "远端读取失败。")
        return True, payload, ""

    def _schedule_remote_script(self, request, body: str):
        request_json = json.dumps(request if isinstance(request, dict) else {}, ensure_ascii=False)
        return (
            "import json\n"
            f"request = json.loads({json.dumps(request_json, ensure_ascii=False)})\n"
            f"{body}"
        )

    def _schedule_remote_save_task(self, device, task_id: str, payload: dict, *, original_id: str = ""):
        script = self._schedule_remote_script(
            {
                "task_id": str(task_id or "").strip(),
                "original_id": str(original_id or "").strip(),
                "payload": dict(payload or {}),
            },
            (
                "import json, os, re\n"
                "def normalize_task_id(value):\n"
                "    text = str(value or '').strip()\n"
                "    if not text:\n"
                "        return ''\n"
                "    text = re.sub(r'\\s+', '_', text)\n"
                "    text = re.sub(r'[\\\\/:*?\"<>|]+', '', text)\n"
                "    text = re.sub(r'_+', '_', text)\n"
                "    return text.strip(' ._')\n"
                "task_id = normalize_task_id(request.get('task_id'))\n"
                "original_id = normalize_task_id(request.get('original_id'))\n"
                "if not task_id:\n"
                "    print(json.dumps({'ok': False, 'error': '任务名不能为空。'}, ensure_ascii=False))\n"
                "    raise SystemExit(0)\n"
                "payload = dict(request.get('payload') or {})\n"
                "extra_fields = dict(payload.pop('extra_fields', {}) or {})\n"
                "data = {\n"
                "    'schedule': str(payload.get('schedule', '08:00') or '08:00').strip() or '08:00',\n"
                "    'repeat': str(payload.get('repeat', 'daily') or 'daily').strip() or 'daily',\n"
                "    'enabled': bool(payload.get('enabled', False)),\n"
                "    'prompt': str(payload.get('prompt', '') or ''),\n"
                "    'max_delay_hours': int(payload.get('max_delay_hours', 6) or 6),\n"
                "}\n"
                "for key in ('schedule', 'repeat', 'enabled', 'prompt', 'max_delay_hours'):\n"
                "    extra_fields.pop(key, None)\n"
                "data.update(extra_fields)\n"
                "tasks_dir = os.path.join(os.getcwd(), 'sche_tasks')\n"
                "os.makedirs(tasks_dir, exist_ok=True)\n"
                "target_path = os.path.join(tasks_dir, f'{task_id}.json')\n"
                "original_path = os.path.join(tasks_dir, f'{original_id}.json') if original_id else ''\n"
                "if original_id and original_id != task_id and os.path.exists(target_path):\n"
                "    print(json.dumps({'ok': False, 'error': f'任务 {task_id} 已存在。'}, ensure_ascii=False))\n"
                "    raise SystemExit(0)\n"
                "tmp_path = target_path + '.tmp'\n"
                "with open(tmp_path, 'w', encoding='utf-8') as f:\n"
                "    f.write(json.dumps(data, ensure_ascii=False, indent=2) + '\\n')\n"
                "os.replace(tmp_path, target_path)\n"
                "renamed = bool(original_id and original_id != task_id)\n"
                "if renamed and original_path and os.path.isfile(original_path):\n"
                "    os.remove(original_path)\n"
                "print(json.dumps({'ok': True, 'task_id': task_id, 'path': target_path, 'renamed': renamed}, ensure_ascii=False))\n"
            ),
        )
        ok, payload_data, err = self._schedule_remote_exec_json(device, script, timeout=120)
        if not ok:
            raise RuntimeError(str(err or "远端保存任务失败。").strip() or "远端保存任务失败。")
        return payload_data if isinstance(payload_data, dict) else {}

    def _schedule_remote_delete_task(self, device, task_id: str):
        script = self._schedule_remote_script(
            {"task_id": str(task_id or "").strip()},
            (
                "import json, os, re\n"
                "def normalize_task_id(value):\n"
                "    text = str(value or '').strip()\n"
                "    if not text:\n"
                "        return ''\n"
                "    text = re.sub(r'\\s+', '_', text)\n"
                "    text = re.sub(r'[\\\\/:*?\"<>|]+', '', text)\n"
                "    text = re.sub(r'_+', '_', text)\n"
                "    return text.strip(' ._')\n"
                "task_id = normalize_task_id(request.get('task_id'))\n"
                "if not task_id:\n"
                "    print(json.dumps({'ok': True, 'deleted': False}, ensure_ascii=False))\n"
                "    raise SystemExit(0)\n"
                "path = os.path.join(os.getcwd(), 'sche_tasks', f'{task_id}.json')\n"
                "deleted = False\n"
                "if os.path.isfile(path):\n"
                "    os.remove(path)\n"
                "    deleted = True\n"
                "print(json.dumps({'ok': True, 'deleted': deleted, 'path': path}, ensure_ascii=False))\n"
            ),
        )
        if not ok:
            raise RuntimeError(str(err or "远端删除任务失败。").strip() or "远端删除任务失败。")
        return payload_data if isinstance(payload_data, dict) else {}

    def _schedule_remote_start(self, device, *, llm_index=0):
        script = self._schedule_remote_script(
            {"llm_index": int(llm_index or 0)},
            (
                "import json, os, socket, subprocess, sys, time\n"
                "base = os.getcwd()\n"
                "temp_dir = os.path.join(base, 'temp')\n"
                "os.makedirs(temp_dir, exist_ok=True)\n"
                "log_path = os.path.join(temp_dir, 'launcher_scheduler_runtime.log')\n"
                "pid_path = os.path.join(temp_dir, 'launcher_scheduler_runtime.pid')\n"
                "scheduler_py = os.path.join(base, 'reflect', 'scheduler.py')\n"
                "agentmain_py = os.path.join(base, 'agentmain.py')\n"
                "def read_tail(path, limit=2200):\n"
                "    if not os.path.isfile(path):\n"
                "        return ''\n"
                "    try:\n"
                "        with open(path, 'r', encoding='utf-8', errors='replace') as f:\n"
                "            return f.read()[-limit:].strip()\n"
                "    except Exception:\n"
                "        return ''\n"
                "def pid_alive(pid):\n"
                "    try:\n"
                "        os.kill(int(pid), 0)\n"
                "        return True\n"
                "    except Exception:\n"
                "        return False\n"
                "def read_pid():\n"
                "    if not os.path.isfile(pid_path):\n"
                "        return 0\n"
                "    try:\n"
                "        with open(pid_path, 'r', encoding='utf-8') as f:\n"
                "            return int((f.read() or '').strip() or 0)\n"
                "    except Exception:\n"
                "        return 0\n"
                "lock_active = False\n"
                "try:\n"
                "    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
                "    sock.settimeout(0.15)\n"
                "    lock_active = sock.connect_ex(('127.0.0.1', 45762)) == 0\n"
                "    sock.close()\n"
                "except Exception:\n"
                "    lock_active = False\n"
                "current_pid = read_pid()\n"
                "if current_pid > 0 and pid_alive(current_pid):\n"
                "    print(json.dumps({'ok': True, 'started': False, 'runtime_status': '启动器托管中', 'runtime_code': 'running', 'runtime_detail': f'启动器托管中 · PID {current_pid}', 'pid': current_pid}, ensure_ascii=False))\n"
                "    raise SystemExit(0)\n"
                "if current_pid > 0 and (not pid_alive(current_pid)):\n"
                "    try:\n"
                "        os.remove(pid_path)\n"
                "    except Exception:\n"
                "        pass\n"
                "if lock_active:\n"
                "    print(json.dumps({'ok': True, 'started': False, 'runtime_status': '运行中', 'runtime_code': 'running', 'runtime_detail': '检测到上游 scheduler 已在后台运行。', 'pid': 0, 'external': True}, ensure_ascii=False))\n"
                "    raise SystemExit(0)\n"
                "if not os.path.isfile(scheduler_py):\n"
                "    print(json.dumps({'ok': False, 'error': f'未找到上游调度器脚本：{scheduler_py}'}, ensure_ascii=False))\n"
                "    raise SystemExit(0)\n"
                "if not os.path.isfile(agentmain_py):\n"
                "    print(json.dumps({'ok': False, 'error': f'未找到 agentmain.py：{agentmain_py}'}, ensure_ascii=False))\n"
                "    raise SystemExit(0)\n"
                "py_bin = str(os.environ.get('GA_PY_BIN') or sys.executable or 'python3').strip() or 'python3'\n"
                "log_handle = open(log_path, 'a', encoding='utf-8', buffering=1)\n"
                "log_handle.write(f'\\n==== {time.strftime(\"%Y-%m-%d %H:%M:%S\")} start scheduler ====\\n')\n"
                "proc = subprocess.Popen([py_bin, agentmain_py, '--reflect', scheduler_py, '--llm_no', str(int(request.get('llm_index', 0) or 0))], cwd=base, stdin=subprocess.DEVNULL, stdout=log_handle, stderr=subprocess.STDOUT, start_new_session=True)\n"
                "log_handle.close()\n"
                "with open(pid_path, 'w', encoding='utf-8') as f:\n"
                "    f.write(str(int(proc.pid or 0)))\n"
                "time.sleep(1.2)\n"
                "if proc.poll() is not None:\n"
                "    try:\n"
                "        os.remove(pid_path)\n"
                "    except Exception:\n"
                "        pass\n"
                "    print(json.dumps({'ok': False, 'error': f'scheduler 进程启动后已退出，exit={proc.returncode}', 'tail': read_tail(log_path), 'exit_code': proc.returncode}, ensure_ascii=False))\n"
                "    raise SystemExit(0)\n"
                "print(json.dumps({'ok': True, 'started': True, 'runtime_status': '启动器托管中', 'runtime_code': 'running', 'runtime_detail': f'启动器托管中 · PID {int(proc.pid or 0)}', 'pid': int(proc.pid or 0)}, ensure_ascii=False))\n"
            ),
        )
        ok, payload_data, err = self._schedule_remote_exec_json(device, script, timeout=120)
        if not ok:
            detail = str(err or "远端启动调度器失败。").strip() or "远端启动调度器失败。"
            payload = payload_data if isinstance(payload_data, dict) else {}
            tail = str(payload.get("tail") or "").strip()
            if tail:
                detail = f"{detail}\n\n日志尾部：\n{tail}"
            raise RuntimeError(detail)
        return payload_data if isinstance(payload_data, dict) else {}

    def _schedule_remote_stop(self, device):
        script = self._schedule_remote_script(
            {},
            (
                "import json, os, signal, socket, time\n"
                "pid_path = os.path.join(os.getcwd(), 'temp', 'launcher_scheduler_runtime.pid')\n"
                "def pid_alive(pid):\n"
                "    try:\n"
                "        os.kill(int(pid), 0)\n"
                "        return True\n"
                "    except Exception:\n"
                "        return False\n"
                "def read_pid():\n"
                "    if not os.path.isfile(pid_path):\n"
                "        return 0\n"
                "    try:\n"
                "        with open(pid_path, 'r', encoding='utf-8') as f:\n"
                "            return int((f.read() or '').strip() or 0)\n"
                "    except Exception:\n"
                "        return 0\n"
                "pid = read_pid()\n"
                "lock_active = False\n"
                "try:\n"
                "    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
                "    sock.settimeout(0.15)\n"
                "    lock_active = sock.connect_ex(('127.0.0.1', 45762)) == 0\n"
                "    sock.close()\n"
                "except Exception:\n"
                "    lock_active = False\n"
                "if pid <= 0:\n"
                "    if lock_active:\n"
                "        print(json.dumps({'ok': False, 'error': '检测到上游 scheduler 正在运行，但不属于启动器托管，已保留该进程。'}, ensure_ascii=False))\n"
                "    else:\n"
                "        print(json.dumps({'ok': True, 'stopped': False, 'detail': '当前没有启动器托管的调度器进程。'}, ensure_ascii=False))\n"
                "    raise SystemExit(0)\n"
                "if not pid_alive(pid):\n"
                "    try:\n"
                "        os.remove(pid_path)\n"
                "    except Exception:\n"
                "        pass\n"
                "    print(json.dumps({'ok': True, 'stopped': False, 'detail': f'pidfile 指向 {pid}，但进程已不存在，已清理残留 pidfile。'}, ensure_ascii=False))\n"
                "    raise SystemExit(0)\n"
                "try:\n"
                "    os.kill(pid, signal.SIGTERM)\n"
                "except Exception as e:\n"
                "    print(json.dumps({'ok': False, 'error': f'发送 SIGTERM 失败：{e}'}, ensure_ascii=False))\n"
                "    raise SystemExit(0)\n"
                "for _ in range(30):\n"
                "    if not pid_alive(pid):\n"
                "        break\n"
                "    time.sleep(0.1)\n"
                "if pid_alive(pid):\n"
                "    try:\n"
                "        os.kill(pid, signal.SIGKILL)\n"
                "    except Exception as e:\n"
                "        print(json.dumps({'ok': False, 'error': f'发送 SIGKILL 失败：{e}'}, ensure_ascii=False))\n"
                "        raise SystemExit(0)\n"
                "for _ in range(20):\n"
                "    if not pid_alive(pid):\n"
                "        break\n"
                "    time.sleep(0.1)\n"
                "if pid_alive(pid):\n"
                "    print(json.dumps({'ok': False, 'error': f'调度器进程 {pid} 仍未退出。'}, ensure_ascii=False))\n"
                "    raise SystemExit(0)\n"
                "try:\n"
                "    os.remove(pid_path)\n"
                "except Exception:\n"
                "    pass\n"
                "print(json.dumps({'ok': True, 'stopped': True, 'detail': f'已停止启动器托管的调度器进程 {pid}。'}, ensure_ascii=False))\n"
            ),
        )
        ok, payload_data, err = self._schedule_remote_exec_json(device, script, timeout=120)
        if not ok:
            raise RuntimeError(str(err or "远端停止调度器失败。").strip() or "远端停止调度器失败。")
        return payload_data if isinstance(payload_data, dict) else {}

    def _schedule_apply_snapshot(self, data, *, notice_text=""):
        payload = dict(data or {})
        self._schedule_last_data_snapshot = payload
        self._schedule_task_state_rows_data = [self._schedule_make_state(row) for row in (payload.get("tasks") or [])]
        if hasattr(self, "settings_schedule_notice") and notice_text:
            self.settings_schedule_notice.setText(str(notice_text))
        self._render_schedule_panel(payload)

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
        if bool(data.get("is_remote")):
            text = str(data.get("runtime_status") or "").strip() or "未运行"
            code = str(data.get("runtime_code") or "").strip() or "disabled"
            detail = str(data.get("runtime_detail") or "").strip() or "未检测到远端调度器状态。"
            return {"text": text, "code": code, "detail": detail}
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
        target = self._schedule_target_context()
        if target["is_remote"]:
            device = self._schedule_target_device()
            if not isinstance(device, dict):
                if show_errors:
                    QMessageBox.warning(self, "设备无效", "当前设置目标对应的远程设备不存在，请先检查 SSH 设备列表。")
                return False
            try:
                self._schedule_remote_start(
                    device,
                    llm_index=int(getattr(self, "_current_llm_index", lambda: 0)() or 0),
                )
            except Exception as e:
                if show_errors:
                    QMessageBox.warning(self, "启动失败", str(e))
                return False
            self._reload_schedule_panel()
            return True
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
        target = self._schedule_target_context()
        if target["is_remote"]:
            device = self._schedule_target_device()
            if not isinstance(device, dict):
                if refresh:
                    self._reload_schedule_panel()
                return False
            try:
                self._schedule_remote_stop(device)
            except Exception as e:
                QMessageBox.warning(self, "停止失败", str(e))
                if refresh:
                    self._reload_schedule_panel()
                return False
            if refresh:
                self._reload_schedule_panel()
            return True
        proc = getattr(self, "_scheduler_proc", None)
        if proc is None:
            if refresh:
                self._reload_schedule_panel()
            return False
        try:
            lz.terminate_process_tree(proc, terminate_timeout=1.5, kill_timeout=1.5)
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
        target = self._schedule_target_context()
        if target["is_remote"]:
            device = self._schedule_target_device()
            if not isinstance(device, dict):
                return
            ok, data, err = self._remote_schedule_snapshot(device)
            if not ok:
                if show_errors:
                    QMessageBox.warning(self, "同步失败", str(err or "远端读取调度器状态失败。"))
                self._reload_schedule_panel()
                return
            enabled_count = int(data.get("enabled_count") or 0)
            runtime_code = str(data.get("runtime_code") or "").strip().lower()
            scheduler_pid = int(data.get("scheduler_pid") or 0)
            if enabled_count > 0:
                if runtime_code != "running":
                    try:
                        self._schedule_remote_start(
                            device,
                            llm_index=int(getattr(self, "_current_llm_index", lambda: 0)() or 0),
                        )
                    except Exception as e:
                        if show_errors:
                            QMessageBox.warning(self, "启动失败", str(e))
                self._reload_schedule_panel()
                return
            if scheduler_pid > 0:
                try:
                    self._schedule_remote_stop(device)
                except Exception:
                    pass
            self._reload_schedule_panel()
            return
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
            target = self._schedule_target_context()
            if target["is_remote"]:
                device = self._schedule_target_device()
                if not isinstance(device, dict):
                    raise RuntimeError("当前设置目标对应的远程设备不存在。")
                result = self._schedule_remote_save_task(
                    device,
                    task_id,
                    payload,
                    original_id=state.get("original_id") or None,
                )
            else:
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
            target = self._schedule_target_context()
            if target["is_remote"]:
                device = self._schedule_target_device()
                if not isinstance(device, dict):
                    raise RuntimeError("当前设置目标对应的远程设备不存在。")
                self._schedule_remote_delete_task(device, task_id)
            else:
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
        target = self._schedule_target_context()
        if target["is_remote"]:
            device = self._schedule_target_device()
            if not isinstance(device, dict):
                self.settings_schedule_notice.setText("当前设置目标对应的远程设备不存在，请先检查 SSH 设备配置。")
                return
            token_getter = getattr(self, "_settings_target_generation", None)
            target_generation = token_getter() if callable(token_getter) else 0
            token = int(time.time() * 1000)
            self._settings_schedule_remote_reload_token = token
            self.settings_schedule_notice.setText(f"正在读取 {target['label']} 的定时任务与调度状态…")

            def worker():
                ok, data, err = self._remote_schedule_snapshot(device)

                def apply():
                    current_generation = token_getter() if callable(token_getter) else target_generation
                    if int(current_generation or 0) != int(target_generation or 0):
                        return
                    if int(getattr(self, "_settings_schedule_remote_reload_token", 0) or 0) != token:
                        return
                    payload = dict(data or {}) if isinstance(data, dict) else {}
                    payload["is_remote"] = True
                    payload["scope"] = "remote"
                    payload["device_id"] = str(target.get("device_id") or "").strip()
                    payload["label"] = str(target.get("label") or "远程设备")
                    if ok:
                        self._schedule_apply_snapshot(
                            payload,
                            notice_text=(
                                f"已从 {target['label']} 读取 {len(payload.get('tasks') or [])} 个定时任务。"
                                "保存、删除和启停操作都会直接写入远端 agant 目录。"
                            ),
                        )
                    else:
                        self._schedule_apply_snapshot(
                            {
                                "is_remote": True,
                                "scope": "remote",
                                "device_id": str(target.get("device_id") or "").strip(),
                                "label": str(target.get("label") or "远程设备"),
                                "paths": {},
                                "tasks": [],
                                "errors": [str(err or "远端读取失败。")],
                                "enabled_count": 0,
                                "error_count": 1,
                                "runtime_status": "读取失败",
                                "runtime_code": "error",
                                "runtime_detail": str(err or "远端读取失败。"),
                                "upstream_log_tail": "",
                                "launcher_log_tail": "",
                            },
                            notice_text=f"读取 {target['label']} 失败：{err or '远端读取失败。'}",
                        )

                poster = getattr(self, "_api_on_ui_thread", None)
                if callable(poster):
                    try:
                        poster(apply)
                        return
                    except Exception:
                        pass
                QTimer.singleShot(0, apply)

            threading.Thread(target=worker, name=f"schedule-remote-reload-{target['device_id']}", daemon=True).start()
            return
        if not lz.is_valid_agent_dir(self.agent_dir):
            self.settings_schedule_notice.setText("请先选择有效的 GenericAgent 目录。")
            return

        self._scheduler_cleanup_if_exited()
        data = lz.list_scheduled_tasks(self.agent_dir)
        self._schedule_apply_snapshot(
            data,
            notice_text=(
                f"已识别 {len(data.get('tasks') or [])} 个上游任务。刷新后会重新读取 AI 或上游写入的 sche_tasks/*.json。"
            ),
        )

    def _render_schedule_panel(self, data):
        self._clear_layout(self.settings_schedule_list_layout)
        paths = dict((data or {}).get("paths") or {})
        runtime = self._schedule_summary_status(data)
        is_remote = bool((data or {}).get("is_remote"))
        scheduler_pid = int((data or {}).get("scheduler_pid") or 0)
        runtime_code = str(runtime.get("code") or "").strip().lower()

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
        if is_remote:
            start_btn.setEnabled(runtime_code != "running")
        else:
            start_btn.setEnabled((not self._scheduler_proc_alive()) and (not self._scheduler_external_running()))
        controls.addWidget(start_btn, 0)
        stop_btn = QPushButton("停止调度器")
        stop_btn.setStyleSheet(self._action_button_style())
        stop_btn.clicked.connect(lambda: self._stop_scheduler_process(refresh=True))
        if is_remote:
            stop_btn.setEnabled(scheduler_pid > 0)
        else:
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
        is_remote = bool(self._schedule_last_data().get("is_remote"))
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
            if is_remote:
                report_btn.setText("下载并打开报告")
            footer.addWidget(report_btn, 0)
            folder_btn = QPushButton("打开任务目录")
            folder_btn.setStyleSheet(self._action_button_style())
            if is_remote:
                folder_btn.setText("同步并打开目录")
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
                if is_remote:
                    text = (text + " " if text else "") + "当前为远端设备任务，打开报告和目录时会自动同步到本地缓存后再打开。"
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
            report_btn.clicked.connect(lambda _=False, s=state: self._schedule_open_report(s))
            folder_btn.clicked.connect(lambda _=False, p=paths.get("tasks_dir", ""): self._schedule_open_tasks_dir(p))

            sync_status_label()
            sync_advanced_fold()
            self.settings_schedule_list_layout.addWidget(card)

    def _render_schedule_log_cards(self):
        data = self._schedule_last_data()
        is_remote = bool(data.get("is_remote"))
        paths = dict(data.get("paths") or {})
        log_grid = QGridLayout()
        log_grid.setSpacing(10)

        upstream_log_card = self._panel_card()
        upstream_log_box = QVBoxLayout(upstream_log_card)
        upstream_log_box.setContentsMargins(16, 14, 16, 14)
        upstream_log_box.setSpacing(8)
        upstream_title = QLabel("调度日志")
        upstream_title.setObjectName("cardTitle")
        upstream_log_box.addWidget(upstream_title)
        if is_remote:
            upstream_tail = str(data.get("upstream_log_tail") or "").strip()
        else:
            upstream_tail = lz.tail_scheduler_log(self.agent_dir, limit=2200)
        upstream_text = QLabel(upstream_tail or "暂无 scheduler.log。")
        upstream_text.setWordWrap(True)
        upstream_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        upstream_text.setObjectName("tokenTree")
        upstream_log_box.addWidget(upstream_text)
        upstream_actions = QHBoxLayout()
        upstream_actions.setSpacing(8)
        open_upstream_btn = QPushButton("下载并打开完整日志" if is_remote else "打开完整日志")
        open_upstream_btn.setStyleSheet(self._action_button_style())
        open_upstream_btn.setEnabled(bool(paths.get("log_path")))
        open_upstream_btn.clicked.connect(
            lambda _=False, p=paths.get("log_path", ""): self._schedule_open_log_file(
                title="调度日志",
                path=p,
                local_subdir="logs",
                preferred_name="scheduler.log",
            )
        )
        upstream_actions.addWidget(open_upstream_btn, 0)
        upstream_actions.addStretch(1)
        upstream_log_box.addLayout(upstream_actions)
        log_grid.addWidget(upstream_log_card, 0, 0)

        runtime_log_card = self._panel_card()
        runtime_log_box = QVBoxLayout(runtime_log_card)
        runtime_log_box.setContentsMargins(16, 14, 16, 14)
        runtime_log_box.setSpacing(8)
        runtime_log_title = QLabel("启动日志")
        runtime_log_title.setObjectName("cardTitle")
        runtime_log_box.addWidget(runtime_log_title)
        runtime_tail = (str(data.get("launcher_log_tail") or "").strip() if is_remote else self._scheduler_launcher_log_tail()) or "暂无启动日志。"
        runtime_log_text = QLabel(runtime_tail)
        runtime_log_text.setWordWrap(True)
        runtime_log_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        runtime_log_text.setObjectName("tokenTree")
        runtime_log_box.addWidget(runtime_log_text)
        runtime_actions = QHBoxLayout()
        runtime_actions.setSpacing(8)
        runtime_path = str(paths.get("launcher_log_path") or "").strip() or (
            str(data.get("paths", {}).get("launcher_log_path") or "").strip() if is_remote else self._scheduler_launcher_log_path()
        )
        open_runtime_btn = QPushButton("下载并打开完整日志" if is_remote else "打开完整日志")
        open_runtime_btn.setStyleSheet(self._action_button_style())
        open_runtime_btn.setEnabled(bool(runtime_path))
        open_runtime_btn.clicked.connect(
            lambda _=False, p=runtime_path: self._schedule_open_log_file(
                title="启动日志",
                path=p,
                local_subdir="logs",
                preferred_name="launcher_scheduler_runtime.log",
            )
        )
        runtime_actions.addWidget(open_runtime_btn, 0)
        runtime_actions.addStretch(1)
        runtime_log_box.addLayout(runtime_actions)
        log_grid.addWidget(runtime_log_card, 0, 1)

        self.settings_schedule_list_layout.addLayout(log_grid)
