@echo off
setlocal
cd /d "%~dp0.."

rem Re-creates any missing tables/indexes without deleting existing data
rem (all CREATE statements use IF NOT EXISTS).

if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Run setup.bat first.
  pause
  exit /b 1
)

.venv\Scripts\python.exe -m app.cli --config config\config.json --init
echo Schema check/creation complete. No existing data was deleted.
pause
