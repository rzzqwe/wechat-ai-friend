@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title 微信 AI 朋友 - 实时进度
echo 正在加载启动脚本，实时进度将在本窗口显示……
set "PYTHONUTF8=1"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_workbench.ps1"
set "START_EXIT_CODE=%ERRORLEVEL%"
if not "%START_EXIT_CODE%"=="0" (
  echo.
  echo 程序启动失败，请查看上面的错误信息。
  pause
)
exit /b %START_EXIT_CODE%
