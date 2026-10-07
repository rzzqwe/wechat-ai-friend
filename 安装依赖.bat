@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title 安装依赖 - 实时进度
echo 正在加载环境检查脚本，实时进度将在本窗口显示……
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_python.ps1"
set "SETUP_EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%SETUP_EXIT_CODE%"=="0" echo 安装未完成，请查看上面的错误信息。
pause
exit /b %SETUP_EXIT_CODE%
