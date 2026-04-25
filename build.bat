@echo off
setlocal EnableExtensions
pushd "%~dp0"
echo Building GenericAgent Launcher (installer architecture)...
echo.

set VERSION=%1
if "%VERSION%"=="" set VERSION=0.0.0-local

python -c "import sys; print('Python:', sys.executable); print('Version:', sys.version)"
if errorlevel 1 (
    echo [ERROR] Failed to detect Python runtime
    pause
    popd
    exit /b 1
)

python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install requirements
    pause
    popd
    exit /b 1
)

python -m pip install pyinstaller
if errorlevel 1 (
    echo [ERROR] Failed to install PyInstaller
    pause
    popd
    exit /b 1
)

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

python -m PyInstaller --clean --noconfirm GenericAgentLauncher.spec
if errorlevel 1 (
    echo [ERROR] Build app failed
    pause
    popd
    exit /b 1
)

python -m PyInstaller --clean --noconfirm LauncherBootstrap.spec
if errorlevel 1 (
    echo [ERROR] Build bootstrap failed
    pause
    popd
    exit /b 1
)

python -m PyInstaller --clean --noconfirm Updater.spec
if errorlevel 1 (
    echo [ERROR] Build updater failed
    pause
    popd
    exit /b 1
)

python tools/build_release_bundle.py --version %VERSION% --out release
if errorlevel 1 (
    echo [ERROR] Build release bundle failed
    echo [ERROR] Release builds require update signing keys. Use GitHub Actions secrets or set:
    echo [ERROR]   GA_LAUNCHER_UPDATE_PRIVATE_KEY_PEM
    echo [ERROR]   GA_LAUNCHER_UPDATE_PUBLIC_KEY_PEM
    pause
    popd
    exit /b 1
)

set "ISCC_EXE=%~dp0tools\InnoSetup\ISCC.exe"
if not exist "%ISCC_EXE%" set "ISCC_EXE=%~dp0temp\InnoSetup\ISCC.exe"
if not exist "%ISCC_EXE%" set "ISCC_EXE=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC_EXE%" set "ISCC_EXE=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC_EXE%" set "ISCC_EXE=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC_EXE%" set "ISCC_EXE="
if not defined ISCC_EXE for /f "delims=" %%I in ('where iscc 2^>nul') do if not defined ISCC_EXE set "ISCC_EXE=%%~fI"

if defined ISCC_EXE (
    echo [INFO] Using Inno Setup compiler: "%ISCC_EXE%"
    "%ISCC_EXE%" /DMyVersion=%VERSION% installer\GenericAgentLauncher.iss
    if errorlevel 1 (
        echo [ERROR] Build installer failed
        pause
        popd
        exit /b 1
    )
) else (
    echo [WARN] Inno Setup compiler not found. Checked:
    echo        1) INNO_ISCC env path
    echo        2) .\tools\InnoSetup\ISCC.exe
    echo        3) .\temp\InnoSetup\ISCC.exe
    echo        4) %LocalAppData%\Programs\Inno Setup 6\ISCC.exe
    echo        5) Program Files Inno Setup 6
    echo        6) PATH (where iscc)
    echo [WARN] Installer compilation skipped.
)

echo.
echo ===========================================
echo  Build complete!
echo  Release bundle: release\%VERSION%
echo ===========================================
pause
popd
exit /b 0
