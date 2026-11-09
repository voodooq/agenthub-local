#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================================"
echo "  AgentHub Local 跨平台桌面启动器 (macOS / Linux)"
echo "========================================================"

if [ -x ".venv/bin/python" ]; then
    PY_BIN=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY_BIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
    PY_BIN="$(command -v python)"
else
    echo "[错误] 未检测到 Python 3，请先安装 Python >= 3.10。"
    exit 1
fi

echo "正在使用解释器: $PY_BIN"
exec "$PY_BIN" "$SCRIPT_DIR/desktop_app.py" "$@"

