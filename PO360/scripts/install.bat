@echo off
setlocal
cd /d "%~dp0.."
where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher 'py' was not found.
  echo Install Python 3.12 from python.org and enable 'Add Python to PATH'.
  pause
  exit /b 1
)
py -3.12 -m venv .venv
if errorlevel 1 (
  echo Failed to create virtual environment.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
if not exist "config\config.json" copy /Y "config\config.example.json" "config\config.json" >nul
if not exist "data\attachments" mkdir "data\attachments"
if not exist "logs" mkdir "logs"
if not exist "output" mkdir "output"
echo.
echo Installation complete.
echo Edit config\config.json before running.
pause
