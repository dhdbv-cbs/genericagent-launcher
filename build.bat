@echo off
echo Building GenericAgent Launcher...
echo.

python -m pip install -r requirements.txt
python -m pip install pyinstaller
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies
    pause
    exit /b 1
)

python -m PyInstaller --noconfirm GenericAgentLauncher.spec
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
