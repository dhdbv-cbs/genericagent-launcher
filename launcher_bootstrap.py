from __future__ import annotations

import os
import sys

from launcher_core_parts.constants import MAIN_EXE_NAME
from launcher_core_parts.runtime import (
    _popen_external_subprocess,
    load_version_state,
    resolved_versions_dir,
    set_current_version,
)


def _pick_target_executable() -> str:
    state = load_version_state()
    current = str((state or {}).get("current_version") or "").strip()
    if current:
        candidate = os.path.join(resolved_versions_dir(), current, MAIN_EXE_NAME)
        if os.path.isfile(candidate):
            return candidate
    versions_dir = resolved_versions_dir()
    if os.path.isdir(versions_dir):
        candidates = []
        for name in os.listdir(versions_dir):
            fp = os.path.join(versions_dir, name, MAIN_EXE_NAME)
            if os.path.isfile(fp):
                candidates.append((name, fp))
        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            selected_version, selected_fp = candidates[0]
            try:
                set_current_version(selected_version, previous_version="", pending_update={})
            except Exception:
                pass
            return selected_fp
    fallback = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), MAIN_EXE_NAME)
    if os.path.isfile(fallback):
        return fallback
    return ""


def _show_bootstrap_error(text: str) -> None:
    message = str(text or "").strip() or "启动失败。"
    try:
        if os.name == "nt":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "LauncherBootstrap", 0x10)
            return
    except Exception:
        pass
    sys.stderr.write(message + "\n")


def run() -> int:
    if not getattr(sys, "frozen", False):
        from launcher import run as main_run

        agent_dir = sys.argv[1] if len(sys.argv) > 1 else None
        return int(main_run(agent_dir))
    target = _pick_target_executable()
    if not target:
        _show_bootstrap_error("未找到可启动的 GenericAgentLauncher.exe。请重新安装启动器。")
        return 1
    args = [target, *sys.argv[1:]]
    try:
        _popen_external_subprocess(args, cwd=os.path.dirname(target))
        return 0
    except Exception as e:
        _show_bootstrap_error(f"启动失败：{e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(run())
