#!/bin/bash
# PageGate Watcher 启动脚本（带自动重启）
# 使用方法: ./scripts/start-watcher.sh
#
# 会自动：
#   1. 加载 skill 根目录下的 .env 文件（如果存在）
#   2. 重定向所有输出（stdout + stderr）到日志文件
#   3. 杀掉已有的 watcher 进程
#   4. 启动新的 watcher，崩溃后自动重启（指数退避 1s→2s→4s→5s）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WATCH_SCRIPT="$SCRIPT_DIR/pagegate_watch.py"
ENV_FILE="$SKILL_DIR/.env"

# 加载 .env（如果存在）
if [ -f "$ENV_FILE" ]; then
    echo "Loading env from $ENV_FILE..."
    set -a
    source "$ENV_FILE"
    set +a
fi

if [ -n "${PAGEGATE_WATCH_PYTHON:-}" ]; then
    PYTHON_BIN="$PAGEGATE_WATCH_PYTHON"
elif [ -x "$SKILL_DIR/venv/bin/python" ]; then
    PYTHON_BIN="$SKILL_DIR/venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
else
    echo "python3 or python is required to start pagegate_watch.py" >&2
    exit 1
fi

LOG_FILE="${PAGEGATE_WATCH_LOG_FILE:-$HOME/.openclaw/workspace/memory/pagegate-watch.log}"

# 杀掉已有 watcher
echo "Stopping existing watcher..."
pkill -f "$WATCH_SCRIPT" 2>/dev/null || true
sleep 1

# 创建日志目录
mkdir -p "$(dirname "$LOG_FILE")"

# 打开日志文件作为 stdout/stderr（追加模式）
exec >> "$LOG_FILE" 2>&1

# 自动重启循环
RESTART_DELAY=1
MAX_DELAY=5
while true; do
    echo "[$(date '+%Y-%m-%dT%H:%M:%S')] [pagegate-watch] === watcher started ==="
    if "$PYTHON_BIN" "$WATCH_SCRIPT"; then
        EXIT_CODE=0
    else
        EXIT_CODE=$?
    fi
    echo "[$(date '+%Y-%m-%dT%H:%M:%S')] [pagegate-watch] watcher exited (code=$EXIT_CODE), restarting in ${RESTART_DELAY}s..."
    sleep "$RESTART_DELAY"
    RESTART_DELAY=$((RESTART_DELAY * 2))
    [ "$RESTART_DELAY" -gt "$MAX_DELAY" ] && RESTART_DELAY="$MAX_DELAY"
done
