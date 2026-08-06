@echo off
rem Launcher for: scripts\sync\sync_offline_to_online.py
rem %~dp0 = directory of this batch file (project root), regardless of install location
cd /d "%~dp0"
python scripts\sync\sync_offline_to_online.py
echo.
echo Press any key to close...
pause >nul