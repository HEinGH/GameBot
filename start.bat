@echo off
cd /d "%~dp0"

:: Check for admin rights; re-launch with elevation if needed
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Start-Process '%~dpnx0' -Verb RunAs -WorkingDirectory '%~dp0'"
    exit /b
)

start "" pythonw.exe gui.pyw
exit
