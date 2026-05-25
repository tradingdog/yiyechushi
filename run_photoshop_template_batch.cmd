@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

set "ROOT_DIR=%~dp0"
pushd "%ROOT_DIR%" >nul

set "TOOL_PATH=%ROOT_DIR%tools\apply_photoshop_template_batch.py"
set "OUTPUT_ROOT=%ROOT_DIR%output"
set "PYTHON_EXE=%ROOT_DIR%.venv\Scripts\python.exe"

if not exist "%TOOL_PATH%" (
    echo 找不到工具脚本：%TOOL_PATH%
    goto :fail
)

if not exist "%OUTPUT_ROOT%" (
    echo 找不到 output 目录：%OUTPUT_ROOT%
    goto :fail
)

if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=python"
)

set "TARGET_INPUT="
set "EXTRA_ARGS="

if not "%~1"=="" (
    set "FIRST_ARG=%~1"
    if /i not "!FIRST_ARG:~0,1!"=="-" if /i not "!FIRST_ARG:~0,1!"=="/" (
        set "TARGET_INPUT=%~1"
        shift
    )
)

:collect_args
if "%~1"=="" goto args_done
set "EXTRA_ARGS=!EXTRA_ARGS! "%~1""
shift
goto collect_args

:args_done
if defined TARGET_INPUT goto resolve_target

echo.
echo 可处理的 output 子目录：
set "LATEST_DIR="
for /f "delims=" %%D in ('dir /b /ad /o-d "%OUTPUT_ROOT%" 2^>nul') do (
    if not defined LATEST_DIR set "LATEST_DIR=%%D"
    echo   %%D
)

echo.
if defined LATEST_DIR (
    echo 直接回车将处理最新目录：!LATEST_DIR!
) else (
    echo 当前 output 下没有找到任何子目录。
)
set /p "TARGET_INPUT=请输入 output 子目录名，或直接粘贴完整目录路径："
if not defined TARGET_INPUT set "TARGET_INPUT=!LATEST_DIR!"

if not defined TARGET_INPUT (
    echo 没有可处理的目录。
    goto :fail
)

:resolve_target
if exist "%TARGET_INPUT%" (
    set "TARGET_DIR=%TARGET_INPUT%"
) else (
    set "TARGET_DIR=%OUTPUT_ROOT%\%TARGET_INPUT%"
)

if not exist "%TARGET_DIR%" (
    echo 指定目录不存在：%TARGET_DIR%
    goto :fail
)

echo.
echo 即将处理目录：%TARGET_DIR%
echo 使用解释器：%PYTHON_EXE%
echo.
"%PYTHON_EXE%" "%TOOL_PATH%" "%TARGET_DIR%" %EXTRA_ARGS%
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if "%EXIT_CODE%"=="0" (
    echo 处理完成。
) else (
    echo 处理失败，退出码：%EXIT_CODE%
)
goto :end

:fail
set "EXIT_CODE=1"

:end
echo.
pause
popd >nul
exit /b %EXIT_CODE%