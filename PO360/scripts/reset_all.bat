@echo off
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Run setup.bat first.
  pause
  exit /b 1
)

echo *** THIS WILL PERMANENTLY DELETE ***
echo   - all log files
echo   - all saved raw emails/attachments
echo   - the SQLite database (all PO/email data)
echo.
set /p CONFIRM="Type YES to continue: "
if /i not "%CONFIRM%"=="YES" (
  echo Aborted. Nothing was deleted.
  exit /b 1
)

for /f "delims=" %%i in ('.venv\Scripts\python.exe scripts\read_config.py logging.dir logs') do set LOG_DIR=%%i
for /f "delims=" %%i in ('.venv\Scripts\python.exe scripts\read_config.py processing.raw_email_dir data\raw_emails') do set RAW_DIR=%%i
for /f "delims=" %%i in ('.venv\Scripts\python.exe scripts\read_config.py processing.attachment_dir data\attachments') do set ATTACH_DIR=%%i
for /f "delims=" %%i in ('.venv\Scripts\python.exe scripts\read_config.py database.path data\analyzer.db') do set DB_PATH=%%i

echo Deleting logs in %LOG_DIR% ...
if exist "%LOG_DIR%" del /q "%LOG_DIR%\*" 2>nul

echo Deleting raw emails in %RAW_DIR% ...
if exist "%RAW_DIR%" rd /s /q "%RAW_DIR%" 2>nul

echo Deleting attachments in %ATTACH_DIR% ...
if exist "%ATTACH_DIR%" rd /s /q "%ATTACH_DIR%" 2>nul

echo Deleting database %DB_PATH% ...
if exist "%DB_PATH%" del /q "%DB_PATH%"
if exist "%DB_PATH%-wal" del /q "%DB_PATH%-wal"
if exist "%DB_PATH%-shm" del /q "%DB_PATH%-shm"

echo Recreating empty database schema...
.venv\Scripts\python.exe -m app.cli --config config\config.json --init

echo.
echo Reset complete.
pause
