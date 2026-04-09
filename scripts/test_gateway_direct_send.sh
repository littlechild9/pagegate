#!/usr/bin/env bash
set -euo pipefail

CHANNEL="${OPENCLAW_NOTIFY_CHANNEL:-openclaw-weixin}"
TARGET="${OPENCLAW_NOTIFY_TARGET:?Set OPENCLAW_NOTIFY_TARGET first}"
ACCOUNT="${OPENCLAW_NOTIFY_ACCOUNT:?Set OPENCLAW_NOTIFY_ACCOUNT first}"
MESSAGE="${1:-gateway rpc direct send test from script}"
IDEMPOTENCY_KEY="${OPENCLAW_IDEMPOTENCY_KEY:-pagegate-gw-send-$(date +%s)}"

PARAMS=$(python3 - <<'PY' "$CHANNEL" "$TARGET" "$ACCOUNT" "$MESSAGE" "$IDEMPOTENCY_KEY"
import json, sys
channel, target, account, message, idem = sys.argv[1:6]
print(json.dumps({
    "channel": channel,
    "to": target,
    "accountId": account,
    "message": message,
    "idempotencyKey": idem,
}, ensure_ascii=False))
PY
)

CMD=(openclaw gateway call send --json --params "$PARAMS")

if [[ -n "${OPENCLAW_GATEWAY_URL:-}" ]]; then
  CMD+=(--url "$OPENCLAW_GATEWAY_URL")
fi

if [[ -n "${OPENCLAW_GATEWAY_TOKEN:-}" ]]; then
  CMD+=(--token "$OPENCLAW_GATEWAY_TOKEN")
fi

printf 'Running: %q ' "${CMD[@]}"
printf '\n'
"${CMD[@]}"
