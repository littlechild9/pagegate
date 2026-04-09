#!/usr/bin/env bash
set -euo pipefail

# PageGate local deployment wrapper.
# - Generates or reuses a local super-admin token.
# - Writes local deployment credentials under .deploy-secrets/.
# - Renders a deployment config.yaml for the target domain.
# - Syncs the repository to the remote host and runs remote deploy.sh.
#
# Usage:
#   bash scripts/run_server.sh user@your-server your-domain.com [/opt/pagegate]
#   bash scripts/run_server.sh --dry-run user@your-server your-domain.com

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS_DIR="$ROOT_DIR/.deploy-secrets"
DRY_RUN=0

if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
  shift
fi

REMOTE="${1:-}"
DOMAIN="${2:-}"
REMOTE_APP_DIR="${3:-/opt/pagegate}"

if [[ -z "$REMOTE" || -z "$DOMAIN" ]]; then
  echo "用法: bash scripts/run_server.sh [--dry-run] user@your-server your-domain.com [/opt/pagegate]" >&2
  exit 2
fi

if [[ "$REMOTE" == *"@"* ]]; then
  REMOTE_USER="${REMOTE%@*}"
else
  REMOTE_USER="${USER}"
fi

mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"

SECRETS_FILE="$SECRETS_DIR/${DOMAIN}.env"
STAGED_CONFIG="$SECRETS_DIR/${DOMAIN}.config.yaml"
CONFIG_SOURCE="$ROOT_DIR/config.yaml"

if [[ ! -f "$CONFIG_SOURCE" ]]; then
  CONFIG_SOURCE="$ROOT_DIR/config.example.yaml"
fi

if [[ -f "$SECRETS_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$SECRETS_FILE"
fi

generate_secret() {
  python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
}

SUPER_ADMIN_TOKEN="${SUPER_ADMIN_TOKEN:-$(generate_secret)}"
SESSION_SECRET="${SESSION_SECRET:-$(generate_secret)}"
GENERATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
BASE_URL="https://${DOMAIN}"

write_secrets_file() {
  {
    printf 'DOMAIN=%q\n' "$DOMAIN"
    printf 'REMOTE=%q\n' "$REMOTE"
    printf 'REMOTE_APP_DIR=%q\n' "$REMOTE_APP_DIR"
    printf 'BASE_URL=%q\n' "$BASE_URL"
    printf 'SUPER_ADMIN_TOKEN=%q\n' "$SUPER_ADMIN_TOKEN"
    printf 'SESSION_SECRET=%q\n' "$SESSION_SECRET"
    printf 'GENERATED_AT=%q\n' "$GENERATED_AT"
    printf 'DASHBOARD_URL=%q\n' "${BASE_URL}/dashboard?token=${SUPER_ADMIN_TOKEN}"
  } > "$SECRETS_FILE"
  chmod 600 "$SECRETS_FILE"
}

render_config() {
  python3 - "$CONFIG_SOURCE" "$STAGED_CONFIG" "$SUPER_ADMIN_TOKEN" "$SESSION_SECRET" "$BASE_URL" <<'PY'
from pathlib import Path
import re
import sys

source_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
admin_token = sys.argv[3]
session_secret = sys.argv[4]
base_url = sys.argv[5]

text = source_path.read_text(encoding="utf-8")

def replace_top_level(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"(?m)^{re.escape(key)}:\s*.*$")
    replacement = f'{key}: "{value}"'
    if pattern.search(text):
        return pattern.sub(replacement, text, count=1)
    return replacement + "\n" + text

def replace_server_key(text: str, key: str, value: str) -> str:
    server_match = re.search(r"(?ms)^server:\s*\n(?P<body>(?:^[ \t].*\n?)*)", text)
    replacement = f'  {key}: "{value}"'
    if not server_match:
        block = f'server:\n{replacement}\n'
        return text.rstrip() + "\n\n" + block

    body = server_match.group("body")
    key_pattern = re.compile(rf"(?m)^[ \t]+{re.escape(key)}:\s*.*$")
    if key_pattern.search(body):
        new_body = key_pattern.sub(replacement, body, count=1)
    else:
        new_body = body + replacement + "\n"

    return text[:server_match.start("body")] + new_body + text[server_match.end("body"):]

text = replace_top_level(text, "admin_token", admin_token)
text = replace_server_key(text, "base_url", base_url)
text = replace_server_key(text, "session_secret", session_secret)
output_path.write_text(text, encoding="utf-8")
PY
  chmod 600 "$STAGED_CONFIG"
}

remote_run() {
  if (( DRY_RUN )); then
    printf '[dry-run] %s\n' "$*"
    return
  fi
  "$@"
}

write_secrets_file
render_config

echo "============================================"
echo "  PageGate 部署准备"
echo "============================================"
echo "  远端:        $REMOTE"
echo "  域名:        $DOMAIN"
echo "  目标目录:    $REMOTE_APP_DIR"
echo "  本地凭据:    $SECRETS_FILE"
echo "  部署配置:    $STAGED_CONFIG"
echo ""
echo "  Super Admin Token:"
echo "    $SUPER_ADMIN_TOKEN"
echo ""
echo "  Dashboard:"
echo "    ${BASE_URL}/dashboard?token=${SUPER_ADMIN_TOKEN}"
echo "============================================"

remote_run ssh "$REMOTE" "sudo mkdir -p '$REMOTE_APP_DIR' && sudo chown -R '$REMOTE_USER':'$REMOTE_USER' '$REMOTE_APP_DIR'"

remote_run rsync -az \
  --delete \
  --exclude '.git/' \
  --exclude 'venv/' \
  --exclude '__pycache__/' \
  --exclude '.deploy-secrets/' \
  --exclude 'data/' \
  --exclude 'pages/' \
  --exclude '.pid' \
  --exclude 'config.yaml' \
  "$ROOT_DIR/" "$REMOTE:$REMOTE_APP_DIR/"

remote_run scp "$STAGED_CONFIG" "$REMOTE:$REMOTE_APP_DIR/config.yaml"

remote_run ssh "$REMOTE" "cd '$REMOTE_APP_DIR' && sudo bash deploy.sh '$DOMAIN'"

echo ""
if (( DRY_RUN )); then
  echo "dry-run 完成。未执行远端同步或部署。"
else
  echo "部署完成。"
fi
echo "Super admin token 已写入本地: $SECRETS_FILE"
