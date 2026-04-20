"""Entry point for the Qt-based GenericAgent launcher."""

from __future__ import annotations

import sys

from launcher_app import core as _core
from launcher_app.window import main

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
