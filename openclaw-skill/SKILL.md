---
name: pagegate-client
description: Onboard and manage PageGate from OpenClaw chat. Use when installing or configuring PageGate access, publishing local HTML files, checking pending visitors, approving or rejecting access requests, or updating page metadata and access mode. Onboarding must be led by the main agent in chat, not by an interactive local terminal wizard.
---

# PageGate Client

Use this skill as the chat-side client for a deployed PageGate server.

## Agent-led onboarding only

When `.env` is missing, incomplete, `.onboarding-pending` exists, or the user asks to configure PageGate:

- Run onboarding as a normal chat conversation.
- Do **not** use PTY-driven local terminal question flows.
- Ask only for missing information, one item or one grouped step at a time.
- Prefer the hosted public server unless the user asks for self-hosted.
- Use the SSE watcher bridge only. Do not configure webhook delivery.

## Chat onboarding flow

Ask and resolve values in this order:

1. **Server choice**
   - Default: hosted server `http://115.190.148.77:8888`
   - If the user wants self-hosted, ask for the base URL.

2. **PageGate auth**
   - First ask whether the user already has an existing `PageGate API token`.
   - If they do, use token mode directly.
   - If they do not, ask them to give this PageGate a name in the format `{Name}'s PageGate`.
   - Give at least two concrete examples when asking, for example `Xuan's PageGate` and `XCompany's PageGate`.
   - Make the format explicit: tell the user this should look like `{Name}'s PageGate`.
   - If OpenClaw already knows the user's name, prefer suggesting a prefilled candidate in the form `<known-name>'s PageGate`, then ask them to confirm or revise it.
   - Do **not** ask ordinary hosted users for email/password by default.
   - Do **not** ask ordinary hosted users for server `admin_token`.

3. **OpenClaw notify route**
   - Required: `OPENCLAW_NOTIFY_CHANNEL`, `OPENCLAW_NOTIFY_TARGET`, `OPENCLAW_NOTIFY_ACCOUNT`
   - The preferred method is a handshake from the exact channel the user wants to receive notifications in.
   - Generate a very short challenge in the format `pagegateNN`, where `NN` is two digits, for example `pagegate42`.
   - Ask the user to send that exact short phrase from the target channel first.
   - Then call the helper with `--notify-handshake pagegate42` so it can resolve the route from the message that actually arrived in OpenClaw.
   - After the helper resolves the route, read it back in one line and ask for confirmation, for example:
     `我识别到通知路由是 openclaw-weixin / o9cq...@im.wechat / 0d37...-im-bot，要用这个吗？`
   - Only fall back to current-session auto-discovery when the handshake path is unavailable or the user explicitly wants to skip it.
   - If any route field is still missing, ask for just that missing field explicitly.

4. **Apply config non-interactively**
   - After collecting values, call:

```bash
bash scripts/pagegate_onboard.sh \
  --url http://115.190.148.77:8888 \
  --auth-mode quick-register|token \
  --pagegate-name "Xuan's PageGate" \
  --api-token 'token-if-using-token-mode' \
  --notify-handshake pagegate42
```

Notes:
- For `auth-mode=quick-register`, provide `--pagegate-name`. The server will create the account and return the API token directly.
- For `auth-mode=token`, omit `--pagegate-name` and provide `--api-token`.
- `--notify-handshake pagegateNN` is the preferred route-binding path.
- `--notify-channel`, `--notify-target`, and `--notify-account` may be omitted only when the helper has already discovered the current OpenClaw session route and you have shown that detected route to the user for confirmation.
- If the user already gave an exact route manually, you may still pass `--notify-channel`, `--notify-target`, and `--notify-account` directly instead of using handshake.
- The helper now starts the watcher by default. You do not need to pass `--start-watcher`.
- Only pass `--no-start-watcher` when the user explicitly wants onboarding without a running watcher.
- Add `--send-test` only after the user agrees to receive a test notification.
- The script writes `.env`, saves the API token and PageGate metadata to disk, clears `.onboarding-pending`, verifies PageGate access, starts the watcher by default, and returns a single JSON result.

When asking for `--pagegate-name`, prefer wording like:

```text
请先给你的 PageGate 起一个名字，格式是 {Name}'s PageGate。
例如：Xuan's PageGate，XCompany's PageGate。
如果你愿意，也可以直接用：<known-name>'s PageGate。
```

5. **Explain the result in chat**
   - Confirm the server URL, whether watcher started, and whether a test message was sent.
   - Explicitly show which notify route was finally used: `channel / target / account`.
   - If the helper says the route came from discovery, say that clearly so the user can spot mismatches immediately.
   - Explicitly show the user their `PageGate API token` once onboarding succeeds, and remind them it has already been saved into `.env`.
   - Explicitly show the generated `username` when the server returns one.
   - Explicitly show both the personal `PageGate URL` (`/<username>`) and the `dashboard URL`, and remind the user that both have already been saved into `.env`.
   - Explain the split clearly: `PageGate URL` is the user's personal gateway homepage. Its `公开页面` tab shows public pages; after a visitor logs in, its `已授权给我` tab can show pages that visitor has been approved for. `dashboard URL` is where the owner manages all pages plus visitor approvals and whitelist state.
   - After onboarding succeeds, proactively propose one real access test:
     publish a small `approval` or `private` page, open it once with the user's own visitor identity, then help them run `bash scripts/pagegate_client.sh visitors`.
   - If the user is doing self-testing, suggest adding that visitor to the current account's owner-level whitelist with `bash scripts/pagegate_client.sh whitelist-add --visitor-id <their-visitor-id>`.
   - If route info or auth is still missing, ask for the next missing item instead of dumping raw script output.

## Runtime environment

After onboarding, these variables should exist in `.env`:

- `PAGEGATE_URL`
- `PAGEGATE_API_TOKEN`
- `PAGEGATE_USERNAME`
- `PAGEGATE_HOME_URL`
- `PAGEGATE_DASHBOARD_URL`
- `OPENCLAW_NOTIFY_CHANNEL`
- `OPENCLAW_NOTIFY_TARGET`
- `OPENCLAW_NOTIFY_ACCOUNT`

If missing, continue onboarding in chat instead of asking the user to manually export variables.

## Start real-time watching

Preferred launcher:

```bash
./scripts/start-watcher.sh
```

The watcher must keep stdout/stderr away from OpenClaw exec channels. Diagnostics belong in the log file only.

After onboarding succeeds:
- By default, start the watcher.
- By default, recommend enabling keepalive cron as well, unless the user clearly wants a one-off local test without background keepalive.
- Keep the decision in chat. The main agent should explain that keepalive cron is recommended for reliable approval notifications.
- Do not dump cron internals onto the user as a manual setup chore. The main agent can decide and run it directly.
- Recommended follow-up command:

```bash
bash scripts/register_watch_cron.sh
```

The keepalive helpers:
- `scripts/check-watcher.sh` checks the health file, pid, and status, then restarts the watcher when needed
- `scripts/register_watch_cron.sh` invokes the silent Python helper and returns the structured result
- `scripts/register_watch_cron.py` creates or updates an OpenClaw cron job whose message tells the OpenClaw main agent to run `check-watcher.sh`

## Publish a local page

When the user wants to publish a local HTML file:

1. Confirm the local file path.
2. Ask only for missing metadata: `slug`, `title`, optional `category`, optional `description`, and `access` (`public`, `approval`, or `private`).
3. Call:

```bash
bash scripts/pagegate_client.sh publish \
  --file /absolute/path/to/page.html \
  --slug my-page \
  --title "My Page" \
  --category "未分类" \
  --access public \
  --description "optional"
```

4. Return the published URL clearly.
   - Always return the single-page canonical URL first.
   - If `access=public` and `PAGEGATE_HOME_URL` exists, also remind the user that this page will appear in the `公开页面` tab of their personal PageGate homepage.
   - If `access=approval` or `access=private`, explicitly tell the user that this page will **not** automatically appear in the public `公开页面` tab; direct them to the `dashboard URL` for the full page list and access management.
   - For `access=approval` or `access=private`, also explain that once a visitor is approved or whitelisted and logs in at the owner's `PageGate URL`, the page can appear in that visitor's `已授权给我` tab.
   - When `dashboard URL` exists, mention it as the place to review all pages, pending approvals, approved visitors, and owner-level whitelist state.

Do not forward raw JSON to the user unless they ask.

## Pending approvals

Use:

```bash
bash scripts/pagegate_client.sh pending
```

Format pending requests as a short list with page, visitor, and visitor ID.

## Owner-level whitelist

This whitelist is scoped to the current PageGate user, not global.

If a visitor is added to your whitelist, they can access all pages owned by your user account without re-requesting approval page by page.

List visitors who have previously requested pages from you:

```bash
bash scripts/pagegate_client.sh visitors
```

```bash
bash scripts/pagegate_client.sh whitelist-add --visitor-id dingtalk_oABC123
```

Remove a visitor from your whitelist:

```bash
bash scripts/pagegate_client.sh whitelist-remove --visitor-id dingtalk_oABC123
```

When presenting visitor results in chat, show:
- visitor name
- visitor ID
- whether they are already whitelisted
- which of your pages they have requested

If `PAGEGATE_DASHBOARD_URL` exists, remind the user they can open it to verify the current whitelist and approval state visually.

For onboarding follow-up or self-testing, recommend this sequence:
- publish one `approval` or `private` page
- open it once with the same person who will be whitelisted
- run `bash scripts/pagegate_client.sh visitors`
- then run `bash scripts/pagegate_client.sh whitelist-add --visitor-id <their-visitor-id>`

## Approve or reject a visitor

When the current message contains a notification like:

```text
有人想查看你的页面
页面：西安之旅 (xian-trip)
访客：妈妈（钉钉登录）
访客ID：dingtalk_oABC123
```

Extract:
- `slug = xian-trip`
- `visitor_id = dingtalk_oABC123`

Approve:

```bash
bash scripts/pagegate_client.sh approve --slug xian-trip --visitor-id dingtalk_oABC123
```

Reject:

```bash
bash scripts/pagegate_client.sh reject --slug xian-trip --visitor-id dingtalk_oABC123
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

After an approval, rejection, whitelist add, whitelist remove, or revoke action succeeds:
- confirm the page slug and visitor ID that were affected
- after an approval or whitelist add, if `PAGEGATE_HOME_URL` exists, mention that the visitor can log in at that PageGate homepage and look under `已授权给我`
- if `PAGEGATE_DASHBOARD_URL` exists, remind the user they can open it to review the updated authorization state
- do not claim the personal `PageGate URL` exposes all owner pages publicly; its `公开页面` tab is public, while `已授权给我` is personalized per logged-in visitor

## Update page metadata or access mode

Use:

```bash
bash scripts/pagegate_client.sh update \
  --slug my-page \
  --title "New Title" \
  --access approval
```

Only send fields the user actually wants changed.

## Delete or revoke

These are destructive. Confirm before acting.

Delete page:

```bash
bash scripts/pagegate_client.sh delete --slug my-page
```

Revoke a visitor:

```bash
bash scripts/pagegate_client.sh revoke --slug my-page --visitor-id some_visitor
```

## Bundled resources

- `scripts/pagegate_onboard.sh` — chat-facing onboarding wrapper
- `scripts/pagegate_onboard.py` — silent onboarding helper that writes its result file
- `scripts/pagegate_client.sh` — chat-facing API wrapper
- `scripts/pagegate_client.py` — silent API helper that writes its result file
- `scripts/start-watcher.sh` — safe watcher launcher
- `scripts/check-watcher.sh` — keepalive health checker
- `scripts/register_watch_cron.sh` — chat-facing keepalive cron wrapper
- `scripts/register_watch_cron.py` — silent keepalive cron helper
- `scripts/pagegate_watch.py` — SSE watcher bridge
