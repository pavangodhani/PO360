@echo off
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Run setup.bat first.
  pause
  exit /b 1
)

for /f "delims=" %%i in ('.venv\Scripts\python.exe scripts\read_config.py logging.dir logs') do set LOG_DIR=%%i

echo Watching log directory: %LOG_DIR%
echo Press Ctrl+C to stop.
echo.

powershell -NoProfile -Command "Get-Content -Path (Join-Path '%LOG_DIR%' 'outlook_analyzer.log') -Wait -Tail 50"
