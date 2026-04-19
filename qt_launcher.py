from __future__ import annotations

import sys

from qt_chat_window import main


if __name__ == "__main__":
    agent_dir = sys.argv[1] if len(sys.argv) > 1 else None
    raise SystemExit(main(agent_dir))
