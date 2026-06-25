@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
echo Goldbug starting...
echo.
python gui.pyw
echo.
echo GUI closed.
pause
