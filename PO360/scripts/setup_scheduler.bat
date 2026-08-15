@echo off
setlocal
cd /d "%~dp0.."
set TASKNAME=OutlookEmailAnalyzer
set SCRIPT=%~dp0run_once.bat
set /p MINUTES=Enter interval in minutes (example 15): 
if "%MINUTES%"=="" set MINUTES=15
schtasks /Create /TN "%TASKNAME%" /TR "\"%SCRIPT%\"" /SC MINUTE /MO %MINUTES% /F
if errorlevel 1 (
  echo Failed to create scheduled task. Try running this BAT as Administrator.
  pause
  exit /b 1
)
echo Scheduled task '%TASKNAME%' created.
pause
