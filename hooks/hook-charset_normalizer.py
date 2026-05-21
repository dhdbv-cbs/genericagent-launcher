from __future__ import annotations

from PyInstaller.utils.hooks import collect_all

# launcher.py imports charset_normalizer during packaged smoke validation,
# so collect the whole package instead of relying on implicit analysis.
datas, binaries, hiddenimports = collect_all("charset_normalizer")
