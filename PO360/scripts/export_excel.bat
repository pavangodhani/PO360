@echo off
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Run install.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m app.cli --config config\config.json --export
set EXITCODE=%ERRORLEVEL%
if %EXITCODE%==0 (
  echo Export complete. See output\ (output.export_dir in config.json) for the .xlsx file.
) else (
  echo Export FAILED - see logs\errors.log for details.
)
pause
exit /b %EXITCODE%
