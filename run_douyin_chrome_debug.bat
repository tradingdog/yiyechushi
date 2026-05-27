@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
set "LOG_DIR=%SCRIPT_DIR%logs"
set "PROFILE_DIR=%SCRIPT_DIR%tools\chrome_automation_profile"
set "CDP_PORT=9222"
set "CREATOR_URL=https://creator.douyin.com/creator-micro/home"
set "CHECK_ONLY=0"
set "CHROME_PATH="
set "LOG_STAMP="
set "LOG_FILE="

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
for /f "delims=" %%I in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "LOG_STAMP=%%I"
if not defined LOG_STAMP set "LOG_STAMP=manual"
set "LOG_FILE=%LOG_DIR%\%LOG_STAMP%_run_douyin_chrome_debug.log"

if /I "%~1"=="--check" set "CHECK_ONLY=1"

call :log "Log file: %LOG_FILE%"

if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" set "CHROME_PATH=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
if not defined CHROME_PATH if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" set "CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not defined CHROME_PATH if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" set "CHROME_PATH=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

if not defined CHROME_PATH (
    call :log "[ERROR] Chrome.exe was not found."
    call :log "Checked: %LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
    call :log "Checked: C:\Program Files\Google\Chrome\Application\chrome.exe"
    call :log "Checked: C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    call :log "Edit this bat if you need a custom Chrome path."
    pause
    exit /b 1
)

if not exist "%PROFILE_DIR%" mkdir "%PROFILE_DIR%"

if "%CHECK_ONLY%"=="1" (
    call :log "Chrome path: %CHROME_PATH%"
    call :log "Debug endpoint: http://127.0.0.1:%CDP_PORT%"
    call :log "Automation profile: %PROFILE_DIR%"
    call :log "Creator URL: %CREATOR_URL%"
    for /f "delims=" %%I in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$uri='http://127.0.0.1:%CDP_PORT%/json/version'; try { $response = Invoke-WebRequest -UseBasicParsing -Uri $uri -TimeoutSec 2; if ($response.StatusCode -eq 200) { Write-Host 'Port 9222 is ready.' } else { Write-Host 'Port 9222 is not ready.' } } catch { Write-Host 'Port 9222 is not ready.' }"') do call :log "%%I"
    exit /b 0
)

call :log "Chrome path: %CHROME_PATH%"
call :log "Debug endpoint: http://127.0.0.1:%CDP_PORT%"
call :log "Automation profile: %PROFILE_DIR%"
call :log "Creator URL: %CREATOR_URL%"
call :log "Launching Chrome with remote debugging..."
start "" "%CHROME_PATH%" "--remote-debugging-port=%CDP_PORT%" "--user-data-dir=%PROFILE_DIR%" "--new-window" "--no-first-run" "--no-default-browser-check" "%CREATOR_URL%"
call :log "Chrome launch command sent."
exit /b 0

:log
echo %~1
>>"%LOG_FILE%" echo %~1
goto :eof