from __future__ import annotations

import importlib.util

from PyInstaller.utils.hooks import check_requirement, collect_data_files

# Prior to v1.2.0, a `version.txt` file is used to set __version__. Later versions use `importlib.metadata`.
if check_requirement("importlib_resources < 1.2.0"):
    datas = collect_data_files("importlib_resources", includes=["version.txt"])

hiddenimports = []
if check_requirement("importlib_resources >= 1.3.1") and importlib.util.find_spec("importlib_resources.trees") is not None:
    hiddenimports.append("importlib_resources.trees")
