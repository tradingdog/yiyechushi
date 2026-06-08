@echo off
chcp 65001 >nul
setlocal

set "PORT=8765"

echo [V2 公网面板] 正在停止本地面板与公网隧道...

for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT% " ^| findstr LISTENING') do (
  echo 结束面板进程 PID=%%p
  taskkill /F /PID %%p >nul 2>&1
)

taskkill /F /FI "WINDOWTITLE eq V2 Web Panel*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq V2 Public Tunnel*" >nul 2>&1

echo [V2 公网面板] 已停止。
ping 127.0.0.1 -n 3 >nul
exit /b 0
