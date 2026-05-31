@echo off
setlocal

set "ROOT=D:\my_program\yiyechushi"
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "SCRIPT=%ROOT%\V2\web_panel.py"

if not exist "%PY%" exit /b 1
if not exist "%SCRIPT%" exit /b 1

for /f %%i in ('powershell -NoProfile -Command "(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*V2/web_panel.py*' } | Measure-Object).Count"') do set "RUNNING=%%i"
if not "%RUNNING%"=="0" exit /b 0

powershell -NoProfile -Command "Start-Process -FilePath '%PY%' -ArgumentList '\"%SCRIPT%\"' -WindowStyle Hidden"

exit /b 0
