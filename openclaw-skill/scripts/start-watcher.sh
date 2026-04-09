#!/bin/bash
# PageGate Watcher 启动脚本（带自动重启）
# 使用方法: ./scripts/start-watcher.sh
#
# 会自动：
#   1. 加载 skill 根目录下的 .env 文件（如果存在）
#   2. 尽早重定向所有输出（stdout + stderr）到日志文件
#   3. 杀掉已有的 watcher 进程
#   4. 启动新的 watcher，崩溃后自动重启（指数退避，最高 60s；配置错误时延后重试）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WATCH_SCRIPT="$SCRIPT_DIR/pagegate_watch.py"
ENV_FILE="$SKILL_DIR/.env"
DEFAULT_LOG_FILE="$HOME/.openclaw/workspace/memory/pagegate-watch.log"

if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

LOG_FILE="${PAGEGATE_WATCH_LOG_FILE:-$DEFAULT_LOG_FILE}"
mkdir -p "$(dirname "$LOG_FILE")"

# 从这里开始，脚本不再向 OpenClaw exec 通道输出任何内容。
exec </dev/null >> "$LOG_FILE" 2>&1

log() {
    printf '[%s] [pagegate-watch] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$1"
}

if [ -n "${PAGEGATE_WATCH_PYTHON:-}" ]; then
    PYTHON_BIN="$PAGEGATE_WATCH_PYTHON"
elif [ -x "$SKILL_DIR/venv/bin/python" ]; then
    PYTHON_BIN="$SKILL_DIR/venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
else
    log "python3 or python is required to start pagegate_watch.py"
    exit 1
fi

# 杀掉已有 watcher
log "stopping existing watcher"
pkill -f "$WATCH_SCRIPT" 2>/dev/null || true
sleep 1

# 自动重启循环
RESTART_DELAY=2
MAX_DELAY=60
while true; do
    log "=== watcher started ==="
    if "$PYTHON_BIN" "$WATCH_SCRIPT"; then
        EXIT_CODE=0
    else
        EXIT_CODE=$?
    fi
    if [ "$EXIT_CODE" -eq 2 ]; then
        RESTART_DELAY=30
    fi
    log "watcher exited (code=$EXIT_CODE), restarting in ${RESTART_DELAY}s"
    sleep "$RESTART_DELAY"
    RESTART_DELAY=$((RESTART_DELAY * 2))
    [ "$RESTART_DELAY" -gt "$MAX_DELAY" ] && RESTART_DELAY="$MAX_DELAY"
done
