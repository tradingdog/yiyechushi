@echo off
chcp 65001 >nul
setlocal

rem 脚本所在目录的上级 = 仓库根目录
set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "SCRIPT=%ROOT%\V2\web_panel.py"
set "PORT=8765"

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

rem 结束占用 8765 的旧进程（含 Cursor 里启动的实例）
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT% " ^| findstr LISTENING') do (
  taskkill /F /PID %%p >nul 2>&1
)

echo [V2 面板] 正在启动 http://127.0.0.1:%PORT%/ ...
start "V2 Web Panel" /min "%PY%" "%SCRIPT%"

timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:%PORT%/"

echo [V2 面板] 已在独立窗口运行，可关闭本窗口。
echo 修改 web_panel.py 后请重新双击本 bat；浏览器用 Ctrl+F5 强刷。
timeout /t 5 /nobreak >nul
exit /b 0
