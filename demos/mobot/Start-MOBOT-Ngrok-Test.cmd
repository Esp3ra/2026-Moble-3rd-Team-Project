@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" exit /b 1
".venv\Scripts\python.exe" -B scripts\prepare_transport_test.py
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -B scripts\prepare_ngrok_test.py
if errorlevel 1 exit /b 1
set MOBOT_V1_MODE=TRANSPORT_TEST
set MOBOT_DB=%~dp0instance\transport-test\mobot.sqlite3
call Start-MOBOT.cmd
