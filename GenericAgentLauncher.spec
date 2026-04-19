# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

datas = [('bridge.py', '.')]
binaries = []
hiddenimports = ['qt_chat_window', 'shiboken6']

datas += collect_data_files('customtkinter')
hiddenimports += collect_submodules('customtkinter')

hiddenimports += [
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'PySide6.QtSvg',
]
binaries += collect_dynamic_libs('PySide6')
binaries += collect_dynamic_libs('shiboken6')
datas += collect_data_files('PySide6', subdir='plugins/platforms')
datas += collect_data_files('PySide6', subdir='plugins/styles')
datas += collect_data_files('PySide6', subdir='plugins/imageformats')


a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='GenericAgentLauncher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
