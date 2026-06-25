@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
echo === Step 1: Python path ===
where python
echo.
echo === Step 2: Test tkinter ===
echo If you see a small blank window, tkinter works.
echo.
python -c "import tkinter; r=tkinter.Tk(); r.title('Test Window'); r.geometry('300x200'); tkinter.Label(r,text='tkinter OK! Close this window').pack(); r.mainloop()"
echo.
echo === Step 3: Window closed ===
pause
