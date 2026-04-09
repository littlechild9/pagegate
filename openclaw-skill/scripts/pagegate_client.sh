#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RESULT_FILE="${PAGEGATE_CLIENT_RESULT_FILE:-$HOME/.openclaw/workspace/memory/pagegate-client-result.json}"

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

PYTHON_BIN="$(choose_python)" || {
    printf '%s\n' '{"ok":false,"error":"python3 or python is required"}'
    exit 1
}

mkdir -p "$(dirname "$RESULT_FILE")"
rm -f "$RESULT_FILE"

set +e
PAGEGATE_CLIENT_RESULT_FILE="$RESULT_FILE" \
  "$PYTHON_BIN" "$SCRIPT_DIR/pagegate_client.py" "$@" >/dev/null 2>&1
EXIT_CODE=$?
set -e

if [ -f "$RESULT_FILE" ]; then
    cat "$RESULT_FILE"
else
    printf '%s\n' '{"ok":false,"error":"pagegate_client.py did not write a result file"}'
fi

exit "$EXIT_CODE"
