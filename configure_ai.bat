@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
".venv\Scripts\python.exe" configure_ai.py
pause
