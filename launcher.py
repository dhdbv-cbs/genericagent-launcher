"""Compatibility entry for the Qt-based GenericAgent launcher.

The real frontend now lives in `qt_chat_window.py`, while shared non-UI
logic is provided by `launcher_core.py`.
"""

from __future__ import annotations

import sys

import launcher_core as _core
from qt_chat_window import main

for _name in dir(_core):
    if _name.startswith("__"):
        continue
    globals()[_name] = getattr(_core, _name)


def run(agent_dir: str | None = None) -> int:
    return main(agent_dir)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--qt-chat":
        agent_dir = args[1] if len(args) > 1 else None
        raise SystemExit(run(agent_dir))
    agent_dir = args[0] if args else None
    raise SystemExit(run(agent_dir))
