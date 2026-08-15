@echo off
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Run install.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m app.cli --config config\config.json --once
set EXITCODE=%ERRORLEVEL%
echo Application exit code: %EXITCODE%
exit /b %EXITCODE%
