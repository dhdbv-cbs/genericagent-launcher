"""Entry point for the Qt-based GenericAgent launcher."""

from __future__ import annotations

import faulthandler
import os
import sys
import threading
import time
import traceback

from launcher_app import core as _core
from launcher_app.window import main

for _name in dir(_core):
    if _name.startswith("__"):
        continue
    globals()[_name] = getattr(_core, _name)


def run(agent_dir: str | None = None) -> int:
    crash_dir = os.path.join(str(getattr(_core, "DATA_ROOT", os.path.expanduser("~")) or os.path.expanduser("~")), "state")
    os.makedirs(crash_dir, exist_ok=True)
    crash_log_path = os.path.join(crash_dir, "launcher_crash.log")

    def _append_crash_log(tag: str, body: str):
        try:
            with open(crash_log_path, "a", encoding="utf-8") as f:
                f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {tag}\n{body}\n")
        except Exception:
            pass

    fault_log = None
    try:
        fault_log = open(crash_log_path, "a", encoding="utf-8")
        faulthandler.enable(fault_log, all_threads=True)
    except Exception:
        fault_log = None

    prev_excepthook = sys.excepthook

    def _sys_hook(exc_type, exc_value, exc_tb):
        _append_crash_log("sys.excepthook", "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
        try:
            prev_excepthook(exc_type, exc_value, exc_tb)
        except Exception:
            pass

    sys.excepthook = _sys_hook

    prev_thread_hook = getattr(threading, "excepthook", None)

    def _thread_hook(args):
        msg = "".join(
            traceback.format_exception(
                getattr(args, "exc_type", Exception),
                getattr(args, "exc_value", Exception("thread error")),
                getattr(args, "exc_traceback", None),
            )
        )
        _append_crash_log(f"threading.excepthook:{getattr(getattr(args, 'thread', None), 'name', 'unknown')}", msg)
        if callable(prev_thread_hook):
            try:
                prev_thread_hook(args)
            except Exception:
                pass

    if callable(prev_thread_hook):
        threading.excepthook = _thread_hook

    startup_acked = False
    try:
        startup_acked = bool(_core.acknowledge_pending_update_startup())
    except Exception:
        startup_acked = False
    if startup_acked:
        try:
            _core.start_pending_update_alive_probe()
        except Exception:
            pass
    try:
        return main(agent_dir)
    except Exception:
        _append_crash_log("run.main.exception", traceback.format_exc())
        raise
    finally:
        try:
            if callable(prev_thread_hook):
                threading.excepthook = prev_thread_hook
        except Exception:
            pass
        sys.excepthook = prev_excepthook
        if fault_log is not None:
            try:
                fault_log.flush()
                fault_log.close()
            except Exception:
                pass


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--qt-chat":
        agent_dir = args[1] if len(args) > 1 else None
        raise SystemExit(run(agent_dir))
    agent_dir = args[0] if args else None
    raise SystemExit(run(agent_dir))
