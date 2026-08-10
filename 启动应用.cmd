@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Project Python environment was not found.
  echo Run: python -m venv .venv
  echo Then install: .venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 2
)

".venv\Scripts\python.exe" run.py
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" pause
exit /b %APP_EXIT%
