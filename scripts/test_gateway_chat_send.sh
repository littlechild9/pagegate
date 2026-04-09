#!/usr/bin/env bash
set -euo pipefail

: "${OPENCLAW_SESSION_KEY:?Set OPENCLAW_SESSION_KEY first}"

MESSAGE="${1:-gateway rpc test via chat.send}"
IDEMPOTENCY_KEY="${OPENCLAW_IDEMPOTENCY_KEY:-htmlhub-chat-send-$(date +%s)}"

PARAMS=$(python3 - <<'PY' "$OPENCLAW_SESSION_KEY" "$MESSAGE" "$IDEMPOTENCY_KEY"
import json, sys
session_key, message, idem = sys.argv[1:4]
print(json.dumps({
    "sessionKey": session_key,
    "message": message,
    "idempotencyKey": idem,
}, ensure_ascii=False))
PY
)

CMD=(openclaw gateway call chat.send --json --params "$PARAMS")

if [[ -n "${OPENCLAW_GATEWAY_URL:-}" ]]; then
  CMD+=(--url "$OPENCLAW_GATEWAY_URL")
fi

if [[ -n "${OPENCLAW_GATEWAY_TOKEN:-}" ]]; then
  CMD+=(--token "$OPENCLAW_GATEWAY_TOKEN")
fi

printf 'Running: %q ' "${CMD[@]}"
printf '\n'
"${CMD[@]}"
