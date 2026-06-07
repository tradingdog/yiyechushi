@echo off
chcp 65001 >nul
setlocal

set "ROOT=%~dp0"
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "SCRIPT=%ROOT%\V2\web_panel.py"
set "PORT=8765"

echo [V2 面板] 正在完全重启（端口 %PORT%）...

for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT% " ^| findstr LISTENING') do (
  echo [V2 面板] 结束占用端口的进程 PID=%%p
  taskkill /F /PID %%p >nul 2>&1
)

taskkill /F /FI "WINDOWTITLE eq V2 Web Panel*" >nul 2>&1

ping 127.0.0.1 -n 2 >nul

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

echo [V2 面板] 启动 http://127.0.0.1:%PORT%/
start "V2 Web Panel" /min "%PY%" "%SCRIPT%"

ping 127.0.0.1 -n 3 >nul
start "" "http://127.0.0.1:%PORT%/"

echo [V2 面板] 重启完成。修改代码后请重新双击本 bat；浏览器用 Ctrl+F5 强刷。
ping 127.0.0.1 -n 4 >nul
exit /b 0
