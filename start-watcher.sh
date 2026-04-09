#!/bin/bash
# PageGate Watcher 启动脚本
# 用法: ./start-watcher.sh
# 启动后立即返回，不阻塞

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python"
WATCH_SCRIPT="$SCRIPT_DIR/openclaw-skill/scripts/pagegate_watch.py"
LOG_FILE="$HOME/.openclaw/workspace/memory/pagegate-watch.log"

# 加载 .env
ENV_FILE="$SCRIPT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

# 杀掉旧进程
pkill -f "pagegate_watch.py" 2>/dev/null || true
sleep 1

# 启动，日志追加，所有输出进文件
mkdir -p "$(dirname "$LOG_FILE")"
nohup "$VENV_PYTHON" "$WATCH_SCRIPT" >> "$LOG_FILE" 2>&1 &

echo "Watcher started (PID: $!, log: $LOG_FILE)"
