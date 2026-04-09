#!/bin/bash
# HTMLHub Watcher 启动脚本（带自动重启）
# 使用方法: ./start-watcher.sh
#
# 会自动：
#   1. 加载同目录下的 .env 文件（如果存在）
#   2. 重定向所有输出（stdout + stderr）到日志文件
#   3. 杀掉已有的 watcher 进程
#   4. 启动新的 watcher，崩溃后自动重启（指数退避 1s→2s→4s→5s）

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python"
WATCH_SCRIPT="$SCRIPT_DIR/openclaw-skill/scripts/htmlhub_watch.py"
LOG_FILE="${HTMLHUB_WATCH_LOG_FILE:-$HOME/.openclaw/workspace/memory/htmlhub-watch-real.log}"

# 加载 .env（如果存在）
ENV_FILE="$SCRIPT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
    echo "Loading env from $ENV_FILE..."
    set -a  # 自动 export
    source "$ENV_FILE"
    set +a
fi

# 杀掉已有 watcher
echo "Stopping existing watcher..."
pkill -f "htmlhub_watch.py" 2>/dev/null || true
sleep 1

# 创建日志目录
mkdir -p "$(dirname "$LOG_FILE")"

# 打开日志文件作为 stdout/stderr（追加模式）
exec >> "$LOG_FILE" 2>&1

# 自动重启循环
RESTART_DELAY=1
MAX_DELAY=5
while true; do
    echo "[$(date '+%Y-%m-%dT%H:%M:%S')] [htmlhub-watch] === watcher started ==="
    # 不用 exec，直接运行 Python；Python 崩溃/退出后循环继续
    "$VENV_PYTHON" "$WATCH_SCRIPT"
    EXIT_CODE=$?
    echo "[$(date '+%Y-%m-%dT%H:%M:%S')] [htmlhub-watch] watcher exited (code=$EXIT_CODE), restarting in ${RESTART_DELAY}s..."
    sleep "$RESTART_DELAY"
    RESTART_DELAY=$((RESTART_DELAY * 2))
    [ "$RESTART_DELAY" -gt "$MAX_DELAY" ] && RESTART_DELAY="$MAX_DELAY"
done
