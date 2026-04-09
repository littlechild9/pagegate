---
name: pagegate-client
description: Onboard and manage PageGate from OpenClaw chat. On first use, ask whether the user wants the managed hosted server (recommended) or a self-hosted server, then guide registration/login or reuse an existing PageGate API token, configure OpenClaw notify routing, and start the realtime watcher. Also use this skill when publishing local HTML files to PageGate, checking pending visitors, approving or rejecting access requests, or updating page metadata and access mode.
---

# PageGate Client

Use this skill as the chat-side client for a deployed PageGate server.

## Onboarding

首次使用，或者缺少 `.env` / `PAGEGATE_URL` / `PAGEGATE_API_TOKEN` 时，不要先追问底层环境变量。直接开始 onboarding。

优先使用初始化向导：

```bash
python3 scripts/setup.py
```

onboarding 的顺序必须是：

1. 先问用户：`托管服务器（推荐）` 还是 `自部署服务器`
2. 如果用户选择托管服务器：
   - 默认服务器使用 `http://115.190.148.77:8888`
   - 直接引导普通用户 `注册新账号` 或 `登录已有账号`
   - 不要向托管服务器普通用户索取服务器 `admin_token`
   - 只有用户明确说自己已经有 `PageGate API token` 时，才直接复用它
3. 如果用户选择自部署服务器：
   - 继续问：`已经有服务器` 还是 `需要先部署`
   - 如果还没部署，先引导部署，再继续 onboarding
   - 如果服务器支持注册，先引导普通用户注册或登录；不要默认要求服务器 `admin_token`
   - 如果服务器关闭注册，或用户明确说自己已经有 token，再让用户提供现有 `PageGate API token`
4. 获取到 PageGate API token 后，再配置 OpenClaw 通知路由
5. 保存 `.env`
6. 启动 watcher

`PAGEGATE_API_TOKEN` 保存的是 PageGate API token。托管服务器普通用户的注册 / 登录 token，或者自部署服务器上可用的访问 token，都放在这里。服务器配置里的 `admin_token` 只属于自部署服务器管理员，不属于普通用户 onboarding 输入项。

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

## Runtime environment

完成 onboarding 后，才期望这些环境变量存在：

- `PAGEGATE_URL`
- `PAGEGATE_API_TOKEN`

如果缺少其中任意一项，不要直接让用户手工 export；优先重新运行 `python3 scripts/setup.py` 进入 onboarding。

## Files and API behavior

- Register a new account through `/api/auth/register`.
- Login through `/api/auth/login`.
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
# First time: prefer running the setup wizard
python3 scripts/setup.py
# Or copy .env.example and fill in the values manually
# cp .env.example .env

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
- `PAGEGATE_API_TOKEN`
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
