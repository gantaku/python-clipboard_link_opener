@echo off
echo ========================================
echo Clipboard Link Opener - Build Script
echo ========================================
echo.

echo [1/3] Installing dependencies...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)
echo.

echo [2/3] Cleaning old build files...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
echo.

echo [3/3] Building exe file...
pyinstaller ^
    --onefile ^
    --windowed ^
    --name clipboard_link_opener ^
    --distpath build ^
    --hidden-import pystray._win32 ^
    --noconfirm ^
    run.py
if %errorlevel% neq 0 (
    echo ERROR: Build failed
    pause
    exit /b 1
)
echo.

if exist build\clipboard_link_opener.exe (
    echo SUCCESS: build\clipboard_link_opener.exe
) else (
    echo ERROR: exe file was not generated
    pause
    exit /b 1
)

if exist clipboard_link_opener.spec del /q clipboard_link_opener.spec
