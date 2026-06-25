@echo off
chcp 65001 >nul 2>&1
echo ================================
echo   Goldbug Setup
echo ================================
echo.
echo [1/2] Installing Playwright ...
pip install playwright
if errorlevel 1 (
    echo FAILED - Please install Python first
    pause
    exit /b 1
)
echo.
echo [2/2] Downloading Chromium (~150MB) ...
playwright install chromium
if errorlevel 1 (
    echo Download failed, check network
    pause
    exit /b 1
)
echo.
echo ================================
echo   Done! Run Goldbug.exe to start
echo ================================
pause
