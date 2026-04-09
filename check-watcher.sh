#!/bin/bash
# 仓库根目录下的便捷入口。
# 实际保活逻辑已经下沉到 openclaw-skill/scripts/check-watcher.sh，
# 这样安装后的 skill 自己就能被 OpenClaw cron 直接调用。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALLED_SKILL_DIR="${HOME}/.openclaw/workspace/skills/pagegate-client"
REPO_SKILL_DIR="${SCRIPT_DIR}/openclaw-skill"

if [ -n "${PAGEGATE_WATCH_SKILL_DIR:-}" ]; then
    CHECK_SCRIPT="${PAGEGATE_WATCH_SKILL_DIR}/scripts/check-watcher.sh"
elif [ -f "${INSTALLED_SKILL_DIR}/scripts/check-watcher.sh" ]; then
    CHECK_SCRIPT="${INSTALLED_SKILL_DIR}/scripts/check-watcher.sh"
else
    CHECK_SCRIPT="${REPO_SKILL_DIR}/scripts/check-watcher.sh"
fi

if [ ! -f "${CHECK_SCRIPT}" ]; then
    printf 'error: check-watcher script not found: %s\n' "${CHECK_SCRIPT}" >&2
    exit 1
fi

exec bash "${CHECK_SCRIPT}"
