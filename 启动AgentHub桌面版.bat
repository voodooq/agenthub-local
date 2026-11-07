@echo off
title AgentHub Local 桌面启动器
cd /d "%~dp0"

echo ========================================================
echo   AgentHub Local 本地全场景多源算力智能工作台 (桌面版)
echo ========================================================
echo.
echo 正在启动桌面原生窗体应用...
echo.

if exist "dist\AgentHub\AgentHub.exe" (
    start "" "dist\AgentHub\AgentHub.exe"
) else if exist "dist\GeminiHub\GeminiHub.exe" (
    start "" "dist\GeminiHub\GeminiHub.exe"
) else (
    echo [提示] 正在通过 Python 运行时拉起原生桌面窗体...
    start "" ".\.venv\Scripts\pythonw.exe" "desktop_app.py"
)

exit

