@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PROFILE_DIR=%SCRIPT_DIR%tools\chrome_automation_profile"
set "CDP_PORT=9222"
set "CREATOR_URL=https://creator.douyin.com/creator-micro/home"
set "CHECK_ONLY=0"
set "CHROME_PATH="

if /I "%~1"=="--check" set "CHECK_ONLY=1"

if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" set "CHROME_PATH=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
if not defined CHROME_PATH if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" set "CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not defined CHROME_PATH if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" set "CHROME_PATH=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

if not defined CHROME_PATH (
    echo [ERROR] Chrome.exe was not found.
    echo Checked:
    echo   %LOCALAPPDATA%\Google\Chrome\Application\chrome.exe
    echo   C:\Program Files\Google\Chrome\Application\chrome.exe
    echo   C:\Program Files ^(x86^)^\Google\Chrome\Application\chrome.exe
    echo Edit this bat if you need a custom Chrome path.
    pause
    exit /b 1
)

if not exist "%PROFILE_DIR%" mkdir "%PROFILE_DIR%"

if "%CHECK_ONLY%"=="1" (
    echo Chrome path: %CHROME_PATH%
    echo Debug endpoint: http://127.0.0.1:%CDP_PORT%
    echo Automation profile: %PROFILE_DIR%
    echo Creator URL: %CREATOR_URL%
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$uri='http://127.0.0.1:%CDP_PORT%/json/version'; try { $response = Invoke-WebRequest -UseBasicParsing -Uri $uri -TimeoutSec 2; if ($response.StatusCode -eq 200) { Write-Host 'Port 9222 is ready.' } else { Write-Host 'Port 9222 is not ready.' } } catch { Write-Host 'Port 9222 is not ready.' }"
    exit /b 0
)

start "" "%CHROME_PATH%" "--remote-debugging-port=%CDP_PORT%" "--user-data-dir=%PROFILE_DIR%" "--new-window" "--no-first-run" "--no-default-browser-check" "%CREATOR_URL%"
exit /b 0