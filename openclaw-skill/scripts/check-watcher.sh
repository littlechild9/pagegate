#!/bin/bash
# PageGate Watcher 看门狗脚本
# 用法：
#   bash scripts/check-watcher.sh
# 适合被 OpenClaw cron 或系统 cron 每分钟调用一次。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WATCH_SCRIPT="${SCRIPT_DIR}/pagegate_watch.py"
START_WATCHER="${SCRIPT_DIR}/start-watcher.sh"
ENV_FILE="${SKILL_DIR}/.env"
DEFAULT_LOG_FILE="${HOME}/.openclaw/workspace/memory/pagegate-watch.log"
DEFAULT_HEALTH_FILE="${HOME}/.openclaw/workspace/memory/pagegate-watch-health.json"
MAX_AGE_SEC="${PAGEGATE_WATCH_HEALTH_MAX_AGE_SEC:-45}"

if [ -f "${ENV_FILE}" ]; then
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
fi

LOG_FILE="${PAGEGATE_WATCH_LOG_FILE:-${DEFAULT_LOG_FILE}}"
HEALTH_FILE="${PAGEGATE_WATCH_HEALTH_FILE:-${DEFAULT_HEALTH_FILE}}"

mkdir -p "$(dirname "${LOG_FILE}")"

log() {
    printf '[%s] [watcher-keepalive] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$1" >> "${LOG_FILE}"
}

status_and_exit() {
    printf '%s\n' "$1"
    exit "${2:-0}"
}

choose_python() {
    if [ -n "${PAGEGATE_WATCH_PYTHON:-}" ] && [ -x "${PAGEGATE_WATCH_PYTHON}" ]; then
        printf '%s\n' "${PAGEGATE_WATCH_PYTHON}"
        return
    fi
    if [ -x "${SKILL_DIR}/venv/bin/python" ]; then
        printf '%s\n' "${SKILL_DIR}/venv/bin/python"
        return
    fi
    if command -v python3 >/dev/null 2>&1; then
        command -v python3
        return
    fi
    if command -v python >/dev/null 2>&1; then
        command -v python
        return
    fi
    return 1
}

count_matching() {
    local pattern="$1"
    local count
    count="$(pgrep -f "${pattern}" 2>/dev/null | sed '/^$/d' | wc -l | tr -d ' ')"
    printf '%s\n' "${count:-0}"
}

kill_matching() {
    local pattern="$1"
    local label="$2"
    local pids

    pids="$(pgrep -f "${pattern}" 2>/dev/null | tr '\n' ' ' | xargs || true)"
    if [ -z "${pids}" ]; then
        return
    fi

    log "stopping ${label}: ${pids}"
    kill ${pids} 2>/dev/null || true
    sleep 1

    pids="$(pgrep -f "${pattern}" 2>/dev/null | tr '\n' ' ' | xargs || true)"
    if [ -n "${pids}" ]; then
        log "force stopping ${label}: ${pids}"
        kill -9 ${pids} 2>/dev/null || true
    fi
}

restart_watcher() {
    local reason="$1"
    log "restart required: ${reason}"
    kill_matching "${START_WATCHER}" "launcher"
    kill_matching "${WATCH_SCRIPT}" "watcher"
    nohup bash "${START_WATCHER}" >/dev/null 2>&1 &
    log "launcher started (pid: $!)"
    status_and_exit "restarted: ${reason}"
}

PYTHON_BIN="$(choose_python)" || {
    log "cannot find python interpreter"
    status_and_exit "error: cannot find python interpreter" 1
}

watcher_count="$(count_matching "${WATCH_SCRIPT}")"
launcher_count="$(count_matching "${START_WATCHER}")"

if [ "${watcher_count}" -gt 1 ]; then
    restart_watcher "multiple watcher processes (${watcher_count})"
fi

if [ "${launcher_count}" -gt 1 ]; then
    restart_watcher "multiple launcher processes (${launcher_count})"
fi

health_reason="$("${PYTHON_BIN}" - "${HEALTH_FILE}" "${MAX_AGE_SEC}" <<'PY'
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

health_path = Path(sys.argv[1]).expanduser()
max_age_sec = int(sys.argv[2])


def fail(reason: str) -> None:
    print(reason)
    raise SystemExit(1)


if not health_path.exists():
    fail("health file missing")

try:
    health = json.loads(health_path.read_text(encoding="utf-8"))
except Exception as exc:
    fail(f"health file invalid: {exc}")

pid = health.get("pid")
if not isinstance(pid, int) or pid <= 0:
    fail("health file missing valid pid")

updated_at = health.get("updated_at")
if not updated_at:
    fail("health file missing updated_at")

try:
    updated_dt = datetime.fromisoformat(updated_at)
except Exception:
    fail(f"invalid updated_at: {updated_at}")

age_sec = (datetime.now() - updated_dt).total_seconds()
if age_sec > max_age_sec:
    fail(f"health stale ({int(age_sec)}s > {max_age_sec}s)")

status = str(health.get("status", "")).strip().lower()
if status in {"fatal", "stopped"}:
    fail(f"status={status}")

try:
    os.kill(pid, 0)
except OSError:
    fail(f"pid {pid} not running")

try:
    command = subprocess.check_output(
        ["ps", "-p", str(pid), "-o", "command="],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()
except Exception as exc:
    fail(f"cannot inspect pid {pid}: {exc}")

if "pagegate_watch.py" not in command:
    fail(f"pid {pid} is not pagegate_watch.py")
PY
)" || {
    if [ ! -f "${HEALTH_FILE}" ] && [ "${watcher_count}" -eq 1 ]; then
        log "legacy watcher detected without health file; leaving current process running"
        status_and_exit "healthy"
    fi
    restart_watcher "${health_reason}"
}

status_and_exit "healthy"
