@echo off
title AgentHub Local 实时日志与控制台调试器
cd /d "%~dp0"

echo =======================================================================
echo   AgentHub Local [调试模式] - 实时控制台日志与网络监控已开启
echo =======================================================================
echo.
echo [提示 1] 本窗口将实时滚屏输出后端的全部请求、模型推理流与异常栈；
echo [提示 2] 在桌面应用窗口内，您可随时按 [F12] 呼出原生开发者工具 (DevTools)；
echo [提示 3] 日志同时实时保存在 backend/data/logs/agenthub.log 文本文件中；
echo.
echo 正在拉起桌面服务...
echo.

set "AGENT_HUB_DEBUG=1"
if exist "dist\AgentHub\AgentHub.exe" (
    "dist\AgentHub\AgentHub.exe" --debug
) else (
    .\.venv\Scripts\python.exe desktop_app.py --debug
)

pause

