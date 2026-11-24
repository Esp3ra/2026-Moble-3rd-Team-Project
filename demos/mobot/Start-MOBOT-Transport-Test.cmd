@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Install the Python environment first. See README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -B scripts\prepare_transport_test.py
if errorlevel 1 (
  pause
  exit /b 1
)
set MOBOT_V1_MODE=TRANSPORT_TEST
set MOBOT_DB=%~dp0instance\transport-test\mobot.sqlite3
call Start-MOBOT.cmd
