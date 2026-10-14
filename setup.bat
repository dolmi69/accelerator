@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if not exist ".venv\Scripts\python.exe" (
  py -3.14 -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
if not exist ".env" copy /y ".env.example" ".env" >nul
".venv\Scripts\python.exe" manage.py migrate --noinput
if errorlevel 1 goto failed
echo Ready. Run start.bat to open the website.
pause
exit /b 0
:failed
echo Setup failed. Install Python 3.14 with the Python launcher, then try again.
pause
exit /b 1
