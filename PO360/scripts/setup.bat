@echo off
setlocal
cd /d "%~dp0.."

echo === Outlook Email Analyzer setup ===

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found on PATH. Install Python 3.11+ and re-run this script.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv .venv
  if errorlevel 1 (
    echo Failed to create virtual environment.
    pause
    exit /b 1
  )
)

echo Installing dependencies...
.venv\Scripts\python.exe -m pip install --upgrade pip >nul
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
  echo Dependency installation failed.
  pause
  exit /b 1
)

if not exist "config\config.json" (
  echo Creating config\config.json from config.example.json - EDIT IT before running.
  copy /y "config\config.example.json" "config\config.json" >nul
)

echo Creating SQLite database and schema...
.venv\Scripts\python.exe -m app.cli --config config\config.json --init
if errorlevel 1 (
  echo Database initialization failed.
  pause
  exit /b 1
)

echo.
echo Setup complete. Edit config\config.json with real mailbox/API credentials,
echo then run scripts\check_config.bat to validate them, and scripts\run_once.bat to process email.
pause
