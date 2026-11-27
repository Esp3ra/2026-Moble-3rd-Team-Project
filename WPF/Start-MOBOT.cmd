@echo off
setlocal
cd /d "%~dp0..\server\mobot" || exit /b 1

if not defined MOBOT_PYTHON set "MOBOT_PYTHON=%LOCALAPPDATA%\MOBOT\.venv\Scripts\python.exe"
if not exist "%MOBOT_PYTHON%" (
  echo Create the MOBOT virtual environment and install requirements.txt. See README.md.
  exit /b 1
)

"%MOBOT_PYTHON%" -B app.py
exit /b %errorlevel%
