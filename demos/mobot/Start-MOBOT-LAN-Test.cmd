@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" exit /b 1
".venv\Scripts\python.exe" -B scripts\prepare_transport_test.py
if errorlevel 1 exit /b 1
if "%~1"=="" (
  ".venv\Scripts\python.exe" -B -m scripts.prepare_lan_test
) else (
  ".venv\Scripts\python.exe" -B -m scripts.prepare_lan_test --server-url "%~1"
)
if errorlevel 1 (
  pause
  exit /b 1
)
set MOBOT_V1_MODE=TRANSPORT_TEST
set MOBOT_DB=%~dp0instance\transport-test\mobot.sqlite3
set MOBOT_HOST=0.0.0.0
set MOBOT_PORT=8000
call Start-MOBOT.cmd
