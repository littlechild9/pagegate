#!/bin/bash
# HTMLHub Watcher 看门狗脚本
# 用法: 放入 crontab，每分钟运行一次
# */1 * * * * /path/to/htmlhub/check-watcher.sh
#
# 如果 watcher 进程不存在，就自动启动

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WATCH_SCRIPT="$SCRIPT_DIR/openclaw-skill/scripts/htmlhub_watch.py"
LOG_FILE="$HOME/.openclaw/workspace/memory/htmlhub-watch-real.log"

# 检查进程是否在跑
if ! pgrep -f "htmlhub_watch.py" > /dev/null 2>&1; then
    echo "[$(date '+%Y-%m-%dT%H:%M:%S')] [watcher-keepalive] no watcher found, starting..." >> "$LOG_FILE"
    
    # 加载 .env
    ENV_FILE="$SCRIPT_DIR/.env"
    if [ -f "$ENV_FILE" ]; then
        set -a
        source "$ENV_FILE"
        set +a
    fi
    
    mkdir -p "$(dirname "$LOG_FILE")"
    "$SCRIPT_DIR/venv/bin/python" "$WATCH_SCRIPT" >> "$LOG_FILE" 2>&1 &
    
    echo "[$(date '+%Y-%m-%dT%H:%M:%S')] [watcher-keepalive] watcher started (PID: $!)" >> "$LOG_FILE"
fi
