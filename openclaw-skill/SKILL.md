---
name: pagegate-client
description: Manage a deployed PageGate from OpenClaw chat. Use when publishing local HTML files to PageGate, listing pages, updating page metadata or access mode, checking pending visitors, approving or rejecting access requests, or bridging real-time PageGate approval events into an active OpenClaw session. Requires PAGEGATE_URL, PAGEGATE_ADMIN_TOKEN, and OPENCLAW_SESSION_KEY for the realtime bridge.
---

# PageGate Client

Use this skill as the chat-side client for a deployed PageGate server.

## 初始化设置 (First-time Setup)

首次使用前，运行初始化向导完成配置：

```bash
python3 scripts/setup.py
```

向导会引导你完成以下步骤：

1. **选择服务器** — 使用默认公共服务器（xuanzhang.net:8888）或连接自建服务器
2. **配置 Admin Token** — 输入服务器管理令牌
3. **验证连通性** — 自动测试服务器是否可达
4. **配置微信通道** — 设置 OpenClaw 通知通道（channel、target、account）
5. **保存配置** — 自动生成 `.env` 文件
6. **发送测试消息** — 验证微信通知链路是否正常

### 自建服务器

如果你想搭建自己的 PageGate 服务器：

```bash
git clone https://github.com/littlechild9/pagegate
cd pagegate
pip install -r requirements.txt
cp config.example.yaml config.yaml
# 编辑 config.yaml 设置 admin_token、session_secret、base_url
python3 server.py
```

部署到 Linux 服务器可使用一键部署脚本：`sudo bash deploy.sh your-domain.com`

### 重新配置

任何时候都可以重新运行 `python3 scripts/setup.py` 来更新配置。

## Required environment

Expect these environment variables to exist before making API calls:

- `PAGEGATE_URL`
- `PAGEGATE_ADMIN_TOKEN`

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
python3 scripts/pagegate_client.py publish \
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
python3 scripts/pagegate_client.py pending
```

For page management tasks where the server lacks a list API, inspect local `data/index.json` on the server side only if the user explicitly says this OpenClaw instance is running next to the server files. Otherwise explain that remote PageGate currently exposes pending requests, but not a general page list endpoint.

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
python3 scripts/pagegate_watch.py >> ~/.openclaw/workspace/memory/pagegate-watch.log 2>&1 &
```

Required environment for the bridge:

- `PAGEGATE_URL`
- `PAGEGATE_ADMIN_TOKEN`
- `OPENCLAW_NOTIFY_CHANNEL`
- `OPENCLAW_NOTIFY_TARGET`
- `OPENCLAW_NOTIFY_ACCOUNT`

Optional environment:

- `OPENCLAW_GATEWAY_URL`
- `OPENCLAW_GATEWAY_TOKEN`
- `PAGEGATE_WATCH_LOG_FILE`

The bridge watcher:

- keeps the outbound SSE connection to PageGate,
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
python3 scripts/pagegate_client.py approve --slug xian-trip --visitor-id dingtalk_oABC123
```

For rejection intent, run:

```bash
python3 scripts/pagegate_client.py reject --slug xian-trip --visitor-id dingtalk_oABC123
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
python3 scripts/pagegate_client.py update \
  --slug my-page \
  --title "New Title" \
  --access approval
```

Only send fields the user actually wants changed.

### 6. Delete or revoke

These are destructive. Confirm before acting.

Delete page:

```bash
python3 scripts/pagegate_client.py delete --slug my-page
```

Revoke a visitor from a page:

```bash
python3 scripts/pagegate_client.py revoke --slug my-page --visitor-id some_visitor
```

## Response style

- Keep replies short and operational.
- When publishing, include the URL.
- When showing pending requests, format as a simple list with page, visitor, and visitor ID.
- When blocked by missing env vars or missing file paths, say exactly what is missing.

## Bundled resource

Use `scripts/pagegate_client.py` for all API calls instead of rewriting ad-hoc curl commands.
