@echo off
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Run setup.bat first.
  pause
  exit /b 1
)

.venv\Scripts\python.exe -m app.cli --config config\config.json --check-config
set EXITCODE=%ERRORLEVEL%
if %EXITCODE%==0 (
  echo Config check passed.
) else (
  echo Config check FAILED - see logs\errors.log for details.
)
pause
exit /b %EXITCODE%
