@echo off
setlocal
cd /d "%~dp0.."
if not exist "logs\outlook_analyzer.log" (
  echo Log file does not exist yet. Start the application first.
  pause
  exit /b 0
)
:loop
cls
echo ================================================================
echo Outlook Email Analyzer - Live Log Monitor
echo Press CTRL+C to stop
echo ================================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Content -Path 'logs\outlook_analyzer.log' -Wait -Tail 80"
goto loop
