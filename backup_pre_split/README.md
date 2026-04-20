This folder stores backup copies of the pre-split single-file implementation.

Source commit:
- `bde042a` (`Release v0.1.3`)

Backed up files:
- `launcher_core.py`
- `qt_chat_window.py`

Purpose:
- Keep the original monolithic versions for reference and rollback comparison.
- The active implementation remains the split version in the repo root plus
  `launcher_core_parts/` and `qt_chat_parts/`.
