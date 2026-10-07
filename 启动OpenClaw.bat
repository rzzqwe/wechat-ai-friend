@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title OpenClaw - 实时进度
echo 正在加载启动脚本，实时进度将在本窗口显示……
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_openclaw.ps1"
set "START_EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%START_EXIT_CODE%"=="0" echo OpenClaw 启动失败，请查看上面的错误信息。
pause
exit /b %START_EXIT_CODE%
