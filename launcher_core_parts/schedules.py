from __future__ import annotations

import json
import os
import re
import socket
from datetime import datetime, timedelta

SCHEDULER_LOCK_HOST = "127.0.0.1"
SCHEDULER_LOCK_PORT = 45762
_TASK_CORE_KEYS = {"schedule", "repeat", "enabled", "prompt", "max_delay_hours"}
SCHEDULE_REPEAT_OPTIONS = [
    ("daily", "每天"),
    ("weekday", "工作日"),
    ("weekly", "每周"),
    ("monthly", "每月"),
    ("once", "仅一次"),
    ("every_30m", "每 30 分钟"),
    ("every_1h", "每 1 小时"),
    ("every_6h", "每 6 小时"),
    ("every_12h", "每 12 小时"),
    ("every_2d", "每 2 天"),
]


def upstream_scheduler_paths(agent_dir):
    root = os.path.abspath(str(agent_dir or "").strip()) if str(agent_dir or "").strip() else ""
    return {
        "tasks_dir": os.path.join(root, "sche_tasks"),
        "done_dir": os.path.join(root, "sche_tasks", "done"),
        "log_path": os.path.join(root, "sche_tasks", "scheduler.log"),
        "scheduler_py": os.path.join(root, "reflect", "scheduler.py"),
        "sop_path": os.path.join(root, "memory", "scheduled_task_sop.md"),
    }


def scheduler_repeat_options():
    return list(SCHEDULE_REPEAT_OPTIONS)


def normalize_scheduled_task_id(task_id):
    text = str(task_id or "").strip()
    if not text:
        return ""
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[\\/:*?\"<>|]+", "", text)
    text = re.sub(r"_+", "_", text)
    text = text.strip(" ._")
    return text


def default_scheduled_task(task_id=""):
    ident = normalize_scheduled_task_id(task_id) or "new_task"
    return {
        "id": ident,
        "schedule": "08:00",
        "repeat": "daily",
        "enabled": False,
        "prompt": "",
        "max_delay_hours": 6,
        "extra_fields": {},
    }


def _scheduled_task_payload(payload):
    data = dict(payload or {})
    extra = dict(data.pop("extra_fields", {}) or {})
    out = {
        "schedule": str(data.get("schedule", "08:00") or "08:00").strip() or "08:00",
        "repeat": str(data.get("repeat", "daily") or "daily").strip() or "daily",
        "enabled": bool(data.get("enabled", False)),
        "prompt": str(data.get("prompt", "") or ""),
        "max_delay_hours": int(data.get("max_delay_hours", 6) or 6),
    }
    out.update(extra)
    return out


def load_scheduled_task(agent_dir, task_id):
    task_name = normalize_scheduled_task_id(task_id)
    if not task_name:
        raise ValueError("任务名不能为空。")
    path = os.path.join(upstream_scheduler_paths(agent_dir)["tasks_dir"], f"{task_name}.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("任务文件不是 JSON 对象。")
    core = default_scheduled_task(task_name)
    for key in _TASK_CORE_KEYS:
        if key in data:
            core[key] = data.get(key)
    core["enabled"] = bool(core.get("enabled", False))
    try:
        core["max_delay_hours"] = int(core.get("max_delay_hours", 6) or 6)
    except Exception:
        core["max_delay_hours"] = 6
    core["extra_fields"] = {k: v for k, v in data.items() if k not in _TASK_CORE_KEYS}
    return core


def save_scheduled_task(agent_dir, task_id, payload, *, original_id=None):
    paths = upstream_scheduler_paths(agent_dir)
    tasks_dir = paths["tasks_dir"]
    os.makedirs(tasks_dir, exist_ok=True)

    task_name = normalize_scheduled_task_id(task_id)
    if not task_name:
        raise ValueError("任务名不能为空。")

    target_path = os.path.join(tasks_dir, f"{task_name}.json")
    original_name = normalize_scheduled_task_id(original_id)
    original_path = os.path.join(tasks_dir, f"{original_name}.json") if original_name else ""
    if original_name and task_name != original_name and os.path.exists(target_path):
        raise FileExistsError(f"任务 {task_name} 已存在。")

    serialized = json.dumps(_scheduled_task_payload(payload), ensure_ascii=False, indent=2) + "\n"
    temp_path = target_path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        f.write(serialized)
    os.replace(temp_path, target_path)

    renamed = bool(original_name and task_name != original_name)
    if renamed and original_path and os.path.isfile(original_path):
        os.remove(original_path)
    return {"task_id": task_name, "path": target_path, "renamed": renamed}


def delete_scheduled_task(agent_dir, task_id):
    task_name = normalize_scheduled_task_id(task_id)
    if not task_name:
        return False
    path = os.path.join(upstream_scheduler_paths(agent_dir)["tasks_dir"], f"{task_name}.json")
    if not os.path.isfile(path):
        return False
    os.remove(path)
    return True


def detect_scheduler_lock(host=SCHEDULER_LOCK_HOST, port=SCHEDULER_LOCK_PORT, timeout=0.15):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(float(timeout))
        return sock.connect_ex((str(host), int(port))) == 0
    except Exception:
        return False
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _schedule_repeat_cooldown(repeat):
    text = str(repeat or "daily").strip().lower() or "daily"
    if text == "once":
        return timedelta(days=365000)
    if text in ("daily", "weekday"):
        return timedelta(hours=20)
    if text == "weekly":
        return timedelta(days=6)
    if text == "monthly":
        return timedelta(days=27)
    if text.startswith("every_"):
        payload = text.split("_", 1)[1]
        try:
            count = int(payload[:-1])
        except Exception:
            return None
        unit = payload[-1:]
        if unit == "h":
            return timedelta(hours=count)
        if unit == "d":
            return timedelta(days=count)
        if unit == "m":
            return timedelta(minutes=count)
    return None


def _schedule_last_run(task_id, done_dir):
    latest_ts = None
    latest_name = ""
    report_count = 0
    if not os.path.isdir(done_dir):
        return {"ts": None, "name": "", "path": "", "count": 0}
    suffix = f"_{task_id}.md"
    for fn in os.listdir(done_dir):
        if not fn.endswith(suffix):
            continue
        report_count += 1
        try:
            ts = datetime.strptime(fn[:15], "%Y-%m-%d_%H%M")
        except Exception:
            continue
        if latest_ts is None or ts > latest_ts:
            latest_ts = ts
            latest_name = fn
    return {
        "ts": latest_ts,
        "name": latest_name,
        "path": os.path.join(done_dir, latest_name) if latest_name else "",
        "count": report_count,
    }


def _schedule_prompt_preview(prompt, limit=140):
    text = " ".join(str(prompt or "").replace("\r", "\n").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def list_scheduled_tasks(agent_dir, now=None):
    paths = upstream_scheduler_paths(agent_dir)
    current = now if isinstance(now, datetime) else datetime.now()
    supported = os.path.isfile(paths["scheduler_py"])
    tasks = []
    errors = []
    if not os.path.isdir(paths["tasks_dir"]):
        return {
            "supported": supported,
            "paths": paths,
            "tasks": tasks,
            "errors": errors,
            "enabled_count": 0,
            "error_count": 0,
        }

    for fn in sorted(os.listdir(paths["tasks_dir"])):
        if not fn.endswith(".json"):
            continue
        task_id = fn[:-5]
        path = os.path.join(paths["tasks_dir"], fn)
        row = {
            "id": task_id,
            "file_name": fn,
            "path": path,
            "enabled": False,
            "repeat": "daily",
            "schedule": "00:00",
            "prompt": "",
            "prompt_preview": "",
            "max_delay_hours": 6,
            "parse_error": "",
            "status": "配置错误",
            "status_code": "error",
            "last_run_at": "",
            "last_run_ts": None,
            "latest_report_name": "",
            "latest_report_path": "",
            "report_count": 0,
            "next_ready_at": "",
            "extra_fields": {},
        }
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("任务文件不是 JSON 对象")
        except Exception as e:
            row["parse_error"] = str(e)
            row["prompt_preview"] = "任务文件解析失败"
            tasks.append(row)
            errors.append(f"{fn}: {e}")
            continue

        row["enabled"] = bool(data.get("enabled", False))
        row["repeat"] = str(data.get("repeat", "daily") or "daily").strip() or "daily"
        row["schedule"] = str(data.get("schedule", "00:00") or "00:00").strip() or "00:00"
        row["prompt"] = str(data.get("prompt", "") or "")
        row["prompt_preview"] = _schedule_prompt_preview(row["prompt"])
        try:
            row["max_delay_hours"] = int(data.get("max_delay_hours", 6) or 6)
        except Exception:
            row["max_delay_hours"] = 6
        row["extra_fields"] = {k: v for k, v in data.items() if k not in _TASK_CORE_KEYS}

        cooldown = _schedule_repeat_cooldown(row["repeat"])
        last_run = _schedule_last_run(task_id, paths["done_dir"])
        row["last_run_ts"] = last_run["ts"]
        row["last_run_at"] = last_run["ts"].strftime("%Y-%m-%d %H:%M") if last_run["ts"] else ""
        row["latest_report_name"] = last_run["name"]
        row["latest_report_path"] = last_run["path"]
        row["report_count"] = int(last_run["count"] or 0)
        if last_run["ts"] and cooldown is not None:
            row["next_ready_at"] = (last_run["ts"] + cooldown).strftime("%Y-%m-%d %H:%M")

        try:
            hour_text, minute_text = row["schedule"].split(":", 1)
            hour = int(hour_text)
            minute = int(minute_text)
            if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                raise ValueError("时间超出范围")
        except Exception as e:
            row["parse_error"] = f"schedule 无效：{e}"
            row["status"] = "配置错误"
            row["status_code"] = "error"
            tasks.append(row)
            errors.append(f"{fn}: schedule 无效")
            continue

        if cooldown is None:
            row["parse_error"] = f"repeat 无效：{row['repeat']}"
            row["status"] = "配置错误"
            row["status_code"] = "error"
            tasks.append(row)
            errors.append(f"{fn}: repeat 无效")
            continue

        if not row["enabled"]:
            row["status"] = "已禁用"
            row["status_code"] = "disabled"
        elif row["last_run_ts"] is None:
            row["status"] = "从未执行"
            row["status_code"] = "never_run"
        elif current < (row["last_run_ts"] + cooldown):
            row["status"] = "冷却中"
            row["status_code"] = "cooldown"
        else:
            row["status"] = "待触发"
            row["status_code"] = "ready"
        tasks.append(row)

    return {
        "supported": supported,
        "paths": paths,
        "tasks": tasks,
        "errors": errors,
        "enabled_count": sum(1 for row in tasks if row.get("enabled")),
        "error_count": sum(1 for row in tasks if row.get("status_code") == "error"),
    }


def tail_scheduler_log(agent_dir, limit=4000):
    path = upstream_scheduler_paths(agent_dir)["log_path"]
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()[-max(1, int(limit)) :].strip()
    except Exception:
        return ""
