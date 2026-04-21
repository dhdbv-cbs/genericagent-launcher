@echo off
setlocal
echo Building GenericAgent Launcher...
echo.

python -c "import sys; print('Python:', sys.executable); print('Version:', sys.version)"
if errorlevel 1 (
    echo [ERROR] Failed to detect Python runtime
    pause
    exit /b 1
)

python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install requirements
    pause
    exit /b 1
)

python -m pip install pyinstaller
if errorlevel 1 (
    echo [ERROR] Failed to install PyInstaller
    pause
    exit /b 1
)

if exist build (
    rmdir /s /q build
)
if exist dist (
    rmdir /s /q dist
)

python -m PyInstaller --clean --noconfirm GenericAgentLauncher.spec
if errorlevel 1 (
    echo [ERROR] Build failed
    pause
    exit /b 1
)

echo.
echo ================================
echo  Build complete!
echo  Output: dist\GenericAgentLauncher.exe
echo ================================
pause
