@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

set "ROOT=%~dp0"
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "SCRIPT=%ROOT%\V2\web_panel.py"
set "PORT=8765"
set "CLOUDFLARED=%ROOT%\tools\cloudflared.exe"
set "TUNNEL_LOG=%ROOT%\V2\public_tunnel.log"
set "URL_FILE=%ROOT%\V2\public_tunnel_url.txt"

echo [V2 公网面板] 正在启动本地面板 + Cloudflare 隧道（无需账号密码）...

if not exist "%PY%" (
  echo [错误] 未找到虚拟环境：%PY%
  pause
  exit /b 1
)
if not exist "%SCRIPT%" (
  echo [错误] 未找到面板脚本：%SCRIPT%
  pause
  exit /b 1
)

if not exist "%CLOUDFLARED%" (
  echo [V2 公网面板] 首次运行，正在下载 cloudflared...
  if not exist "%ROOT%\tools" mkdir "%ROOT%\tools"
  powershell -NoProfile -Command ^
    "Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile '%CLOUDFLARED%' -UseBasicParsing"
  if not exist "%CLOUDFLARED%" (
    echo [错误] cloudflared 下载失败，请检查网络后重试。
    pause
    exit /b 1
  )
)

echo [V2 公网面板] 结束旧面板与隧道进程...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT% " ^| findstr LISTENING') do (
  taskkill /F /PID %%p >nul 2>&1
)
taskkill /F /FI "WINDOWTITLE eq V2 Web Panel*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq V2 Public Tunnel*" >nul 2>&1

ping 127.0.0.1 -n 2 >nul

echo [V2 公网面板] 启动本地面板 http://127.0.0.1:%PORT%/
start "V2 Web Panel" /min "%PY%" "%SCRIPT%"

ping 127.0.0.1 -n 3 >nul

if exist "%TUNNEL_LOG%" del /f /q "%TUNNEL_LOG%"
if exist "%URL_FILE%" del /f /q "%URL_FILE%"

echo [V2 公网面板] 正在创建公网隧道...
start "V2 Public Tunnel" /min cmd /c ""%CLOUDFLARED%" tunnel --url http://127.0.0.1:%PORT% --no-autoupdate 1>> "%TUNNEL_LOG%" 2>>&1"

set "PUBLIC_URL="
for /L %%i in (1,1,45) do (
  ping 127.0.0.1 -n 2 >nul
  for /f "usebackq delims=" %%u in (`powershell -NoProfile -Command ^
    "$log='%TUNNEL_LOG%'; if (Test-Path $log) { $m = Select-String -Path $log -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1; if ($m) { $m.Matches[0].Value } }"`) do (
    set "PUBLIC_URL=%%u"
  )
  if defined PUBLIC_URL goto :url_ready
)

echo [错误] 未能获取公网地址，请查看日志：%TUNNEL_LOG%
pause
exit /b 1

:url_ready
echo %PUBLIC_URL%> "%URL_FILE%"
echo.
echo ============================================================
echo [V2 公网面板] 公网访问地址（已写入 %URL_FILE%）：
echo   %PUBLIC_URL%
echo.
echo 本机访问：http://127.0.0.1:%PORT%/
echo 说明：每次重启本脚本会生成新的公网地址；未设密码，请勿泄露链接。
echo 停止服务请双击 stop_v2_public_panel.bat
echo ============================================================
echo.

start "" "%PUBLIC_URL%"
ping 127.0.0.1 -n 4 >nul
exit /b 0
