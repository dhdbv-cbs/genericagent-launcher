from __future__ import annotations

import json
import os
import subprocess

from .constants import CONFIG_PATH
from .runtime import _python_creationflags, _resolve_config_path


def _system_python_commands():
    candidates = []
    cfg_py = None
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg_py = json.load(f).get("python_exe")
    except Exception:
        pass
    if cfg_py:
        resolved = _resolve_config_path(cfg_py)
        if resolved:
            candidates.append([resolved])
    if os.name == "nt":
        candidates += [["py", "-3"], ["python"], ["python3"]]
    else:
        candidates += [["python3"], ["python"]]
    return candidates


def _probe_python_command(cmd):
    try:
        r = subprocess.run(
            cmd + ["-c", "import sys;print(sys.executable);print(sys.version.split()[0])"],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=_python_creationflags(),
        )
        if r.returncode != 0:
            return None
        lines = [line.strip() for line in (r.stdout or "").splitlines() if line.strip()]
        if not lines:
            return None
        path = lines[0]
        ver = lines[1] if len(lines) > 1 else ""
        if path and os.path.isfile(path):
            return {"cmd": list(cmd), "path": path, "version": ver}
    except Exception:
        pass
    return None


def _system_python_candidates():
    items = []
    seen = set()
    for cmd in _system_python_commands():
        info = _probe_python_command(cmd)
        if not info:
            continue
        key = os.path.normcase(os.path.normpath(info["path"]))
        if key in seen:
            continue
        seen.add(key)
        items.append(info)
    return items


def _find_system_python():
    items = _system_python_candidates()
    return items[0]["path"] if items else None


def _probe_python_agent_compat(py, agent_dir):
    code = (
        "import os, sys\n"
        "import requests\n"
        "agent_dir = sys.argv[1]\n"
        "os.chdir(agent_dir)\n"
        "sys.path.insert(0, agent_dir)\n"
        "import agentmain\n"
        "print('OK')\n"
    )
    try:
        r = subprocess.run(
            [py, "-c", code, agent_dir],
            capture_output=True,
            text=True,
            timeout=20,
            cwd=agent_dir,
            encoding="utf-8",
            errors="replace",
            creationflags=_python_creationflags(),
        )
    except Exception as e:
        return False, str(e)
    if r.returncode == 0 and "OK" in (r.stdout or ""):
        return True, ""
    detail = (r.stderr or r.stdout or "").strip()
    if detail:
        lines = [line.strip() for line in detail.splitlines() if line.strip()]
        detail = lines[-1] if lines else detail
    else:
        detail = f"退出码 {r.returncode}"
    return False, detail


def _format_python_candidate_label(info):
    if not info:
        return ""
    ver = (info.get("version") or "").strip()
    path = info.get("path") or ""
    return f"{path} (Python {ver})" if ver else path


def _find_compatible_system_python(agent_dir):
    candidates = _system_python_candidates()
    if not candidates:
        return None, "未找到系统 Python。请先安装 Python 并加入 PATH，或在 launcher_config.json 中设置 python_exe。"
    failures = []
    for info in candidates:
        ok, detail = _probe_python_agent_compat(info["path"], agent_dir)
        if ok:
            return info["path"], None
        failures.append((info, detail))
    lines = ["已找到系统 Python，但都无法载入 GenericAgent 内核。"]
    for info, detail in failures[:3]:
        lines.append(f"- {_format_python_candidate_label(info)}: {detail}")
    lines.append("可在 launcher_config.json 中手动指定 python_exe。")
    lines.append("当前不会强制限制版本，但如果高版本解释器兼容性不稳，通常改用 Python 3.11 / 3.12 更稳。")
    return None, "\n".join(lines)
