@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment missing. Run setup.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" start_local.py %*
if errorlevel 1 pause
