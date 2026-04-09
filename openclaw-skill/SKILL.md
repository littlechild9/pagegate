---
name: htmlhub-client
description: Manage a deployed HTML Hub from OpenClaw chat. Use when publishing local HTML files to HTML Hub, listing pages, updating page metadata or access mode, checking pending visitors, approving or rejecting access requests, or bridging real-time HTML Hub approval events into an active OpenClaw session. Requires HTMLHUB_URL, HTMLHUB_ADMIN_TOKEN, and OPENCLAW_SESSION_KEY for the realtime bridge.
---

# HTML Hub Client

Use this skill as the chat-side client for a deployed HTML Hub server.

## Required environment

Expect these environment variables to exist before making API calls:

- `HTMLHUB_URL`
- `HTMLHUB_ADMIN_TOKEN`

If either is missing, stop and tell the user exactly what is missing.

## Files and API behavior

- Publish local HTML by uploading a file with multipart form data to `/api/publish`.
- Manage existing pages through `/api/pages/{slug}` and related visitor endpoints.
- Read pending approvals from `/api/pending`.
- Subscribe to real-time approval events from `/api/events/stream` through the bundled bridge watcher.
- Deliver approval notifications through Gateway RPC `send` to the user's configured channel route.
- Approval notifications may arrive as plain text messages containing page title, slug, visitor name, and visitor ID.

## Core workflows

### 1. Publish a local page

When the user wants to publish a local HTML file:

1. Confirm the local file path.
2. Ask only for missing metadata: `slug`, `title`, optional `category`, optional `description`, and `access` (`public`, `approval`, or `private`).
3. Call the helper script:

```bash
python3 scripts/htmlhub_client.py publish \
  --file /absolute/path/to/page.html \
  --slug my-page \
  --title "My Page" \
  --category "未分类" \
  --access public \
  --description "optional"
```

4. Return the published URL clearly.

If the user already gave enough info, do not ask redundant questions.

### 2. List pages or pending approvals

Use:

```bash
python3 scripts/htmlhub_client.py pending
```

For page management tasks where the server lacks a list API, inspect local `data/index.json` on the server side only if the user explicitly says this OpenClaw instance is running next to the server files. Otherwise explain that remote HTML Hub currently exposes pending requests, but not a general page list endpoint.

### 3. Start real-time watching

**Preferred: use the launcher script** (handles output redirection automatically):

```bash
# First time: copy .env.example and fill in your tokens
cp .env.example .env
# Edit .env with your actual values

# Start the watcher
./scripts/start-watcher.sh
```

The launcher script:
- Loads config from `.env` automatically
- Uses `exec >> logfile 2>&1` to redirect all output — **critical**: stray stdout/stderr from background Python processes can crash the OpenClaw gateway exec listener
- Kills any existing watcher process before starting fresh

**Manual start** (if you prefer):

```bash
# All output MUST be redirected to avoid gateway crashes
python3 scripts/htmlhub_watch.py >> ~/.openclaw/workspace/memory/htmlhub-watch.log 2>&1 &
```

Required environment for the bridge:

- `HTMLHUB_URL`
- `HTMLHUB_ADMIN_TOKEN`
- `OPENCLAW_NOTIFY_CHANNEL`
- `OPENCLAW_NOTIFY_TARGET`
- `OPENCLAW_NOTIFY_ACCOUNT`

Optional environment:

- `OPENCLAW_GATEWAY_URL`
- `OPENCLAW_GATEWAY_TOKEN`
- `HTMLHUB_WATCH_LOG_FILE`

The bridge watcher:

- keeps the outbound SSE connection to HTML Hub,
- deduplicates pending + stream events by event id,
- rate-limits delivery,
- forwards events through Gateway RPC `send` to the configured OpenClaw channel route,
- writes diagnostics to a log file instead of noisy stdout by default.

**Important**: OpenClaw's background exec listener is fragile — any stderr/stdout output from the watcher process can crash the gateway. Always use `>> file 2>&1` or the launcher script.

Use it in a background session or process manager, not as a one-shot foreground chat step.

### 4. Approve or reject a visitor

If the current message is a notification like:

```text
有人想查看你的页面
页面：西安之旅 (xian-trip)
访客：妈妈（钉钉登录）
访客ID：dingtalk_oABC123
```

extract:

- slug: `xian-trip`
- visitor_id: `dingtalk_oABC123`

Then, when the user replies with approval intent, run:

```bash
python3 scripts/htmlhub_client.py approve --slug xian-trip --visitor-id dingtalk_oABC123
```

For rejection intent, run:

```bash
python3 scripts/htmlhub_client.py reject --slug xian-trip --visitor-id dingtalk_oABC123
```

Treat these as approval intents:
- `通过`
- `同意`
- `批准`
- `approve`
- `ok`

Treat these as rejection intents:
- `拒绝`
- `不同意`
- `deny`
- `reject`
- `不行`

After success, reply briefly and clearly.

### 5. Update page metadata or access mode

Use:

```bash
python3 scripts/htmlhub_client.py update \
  --slug my-page \
  --title "New Title" \
  --access approval
```

Only send fields the user actually wants changed.

### 6. Delete or revoke

These are destructive. Confirm before acting.

Delete page:

```bash
python3 scripts/htmlhub_client.py delete --slug my-page
```

Revoke a visitor from a page:

```bash
python3 scripts/htmlhub_client.py revoke --slug my-page --visitor-id some_visitor
```

## Response style

- Keep replies short and operational.
- When publishing, include the URL.
- When showing pending requests, format as a simple list with page, visitor, and visitor ID.
- When blocked by missing env vars or missing file paths, say exactly what is missing.

## Bundled resource

Use `scripts/htmlhub_client.py` for all API calls instead of rewriting ad-hoc curl commands.
