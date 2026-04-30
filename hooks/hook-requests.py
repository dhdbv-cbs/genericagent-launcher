from __future__ import annotations

from PyInstaller.utils.hooks import collect_all

# launcher_app.core imports requests via importlib at runtime, so make the
# requests package explicit for PyInstaller's analysis step.
datas, binaries, hiddenimports = collect_all("requests")
