# Google OAuth 接入说明

> 适用于当前 `pagegatedev` 仓库。本文档先定义接入方案和实施步骤，等域名备案完成并切到 `https` 后再落代码。

## 目标

在现有钉钉、微信之外，增加一个新的访客登录 provider:

- `Google`

要求：

- 不改现有 `visitor/session` 数据模型
- 复用当前审批流、私密页和待审批页
- 尽量保持单文件后端风格，不引入重型鉴权框架

---

## 当前代码结构

Google 登录应复用现有多 provider 架构，而不是新建一套账号体系。

关键位置：

- `server.py`
  - `/auth/dingtalk` 和 `/auth/dingtalk/callback`
  - `/auth/wechat` 和 `/auth/wechat/callback`
  - `_register_visitor_and_redirect()`
  - `build_access_event()` 里的 provider 名称映射
  - 登录页渲染时的 `has_dingtalk` / `has_wechat`
- `templates/login.html`
  - 访客未登录时的登录入口按钮
- `config.example.yaml`
  - OAuth 配置项

现有 visitor 结构已经满足 Google 登录需要：

```json
{
  "id": "google_1234567890",
  "provider": "google",
  "name": "Alice",
  "avatar": "https://...",
  "approved_pages": [],
  "pending_pages": [],
  "whitelisted_owners": [],
  "blocked": false
}
```

---

## 接入前提

生产接入 Google 登录前，先满足这些前提：

1. 域名已备案并能稳定访问。
2. `server.base_url` 已切到正式 `https` 域名。
3. 能在 Google Cloud Console 创建 OAuth Client。
4. 愿意维护一个 Google OAuth 应用的 `client_id` / `client_secret`。

当前示例配置里的 `server.base_url` 还是 `http://...`。这只能用于本地调试，不适合正式 Google OAuth。

---

## Google Cloud 侧准备

在 Google Cloud Console 中创建 OAuth 凭据时，选择：

- Application type: `Web application`

需要配置的回调地址：

```text
https://your-domain.com/auth/google/callback
```

本地调试时可额外加入：

```text
http://localhost:8888/auth/google/callback
```

如果后续只允许某个 Google Workspace 域的账号登录，可以把 `hd` 作为可选参数加入授权请求，但当前版本建议先做通用 Google 登录。

参考文档：

- https://developers.google.com/identity/protocols/oauth2/web-server
- https://developers.google.com/identity/openid-connect/openid-connect

---

## 配置变更

在 `config.example.yaml` 和实际 `config.yaml` 中新增：

```yaml
google:
  client_id: ""
  client_secret: ""
```

生产环境注意：

- 不要把真实 `client_secret` 提交进仓库。
- `config.yaml` 继续保持本地或服务器私有文件。

---

## 后端实现方案

### 1. 新增 Google 登录入口

新增路由：

- `GET /auth/google`
- `GET /auth/google/callback`

授权地址使用：

```text
https://accounts.google.com/o/oauth2/v2/auth
```

建议参数：

- `client_id`
- `redirect_uri`
- `response_type=code`
- `scope=openid email profile`
- `state=<signed-state>`
- `prompt=consent`

当前项目只需要身份信息，不需要长期代用户访问 Google API，因此不需要为了登录场景额外持久化 `refresh_token`。

### 2. 用 code 换 token

回调收到 `code` 后，请求：

```text
POST https://oauth2.googleapis.com/token
```

请求参数：

- `client_id`
- `client_secret`
- `code`
- `grant_type=authorization_code`
- `redirect_uri`

### 3. 获取用户资料

拿到 access token 后，调用：

```text
GET https://openidconnect.googleapis.com/v1/userinfo
Authorization: Bearer <access_token>
```

推荐读取这些字段：

- `sub`
- `name`
- `email`
- `picture`
- `email_verified`

### 4. 生成 visitor

visitor 生成规则：

- `visitor_id = f"google_{sub}"`
- `provider = "google"`
- `name = name or email or "Google 用户"`
- `avatar = picture or ""`

必须使用 `sub` 作为唯一标识，不要使用 email 作为主键。Google 官方文档明确建议以 `sub` 作为应用内唯一用户 ID。

### 5. 复用现有注册与审批逻辑

回调最后直接复用：

```python
return await _register_visitor_and_redirect(
    request, visitor_id, "google", name, avatar, redirect_slug
)
```

这样：

- `approval` 页面会自动进入待审批
- `private` 页面会复用已有 session 逻辑
- `visitors.json` 无需迁移

---

## 推荐顺手修的安全项

### 1. 统一校验 OAuth state

当前钉钉和微信实现虽然传了 `state`，但只是把 `redirect_slug` 拼进去，没有真正做 CSRF 校验。

Google 接入时，建议顺手抽两个通用 helper：

- `build_oauth_state(redirect_slug: str) -> str`
- `parse_oauth_state(state: str) -> str`

建议做法：

- 把 `redirect_slug` 和随机 nonce 封装成 JSON
- 用现有 `itsdangerous` signer 做签名
- 回调时验签并限制过期时间
- 解析失败直接返回 `400`

如果这一步做了，最好把钉钉和微信也一起切到同一个 helper，避免三套 state 逻辑不一致。

### 2. 生产环境 cookie 建议加 `secure=True`

当前 `set_session_cookie()` 没有设置 `secure=True`。

在正式 `https` 域名启用后，建议改成以下两种方式之一：

- `secure=BASE_URL.startswith("https://")`
- 或增加显式配置项 `server.secure_cookies`

否则 session cookie 仍可能在非 HTTPS 请求中发送。

### 3. 不把 email 当作授权主键

即使后续 UI 想展示 email，也不要把 email 用作：

- `visitor_id`
- whitelist 主键
- 审批记录主键

身份主键应始终基于 `sub`。

---

## 需要改的代码点

### `config.example.yaml`

新增：

```yaml
google:
  client_id: ""
  client_secret: ""
```

### `server.py`

需要改动的区域：

1. provider 名称映射补 `"google": "Google"`
2. 新增 `build_oauth_state()` / `parse_oauth_state()`
3. 新增 `/auth/google`
4. 新增 `/auth/google/callback`
5. 登录页渲染时增加 `has_google` 和 `google_login_url`

### `templates/login.html`

新增一个 Google 登录按钮，和钉钉、微信并列展示。

文案建议：

```text
使用 Google 登录
```

---

## 建议的实现顺序

等域名和 HTTPS 可用后，按这个顺序落地：

1. 在 Google Cloud Console 创建 OAuth Client。
2. 把 `https://your-domain.com/auth/google/callback` 加入回调地址。
3. 在服务器 `config.yaml` 填入 `google.client_id` / `google.client_secret`。
4. 修改 `config.example.yaml`。
5. 在 `server.py` 增加 Google 路由和统一 `state` helper。
6. 在 `templates/login.html` 增加按钮。
7. 手工验证登录、审批、私密页和重复访问。

---

## 手工验证清单

上线前至少验证这些路径：

1. 未登录访问 `approval` 页面，能看到 Google 登录按钮。
2. 点击 Google 登录后，能正确跳转到 Google 授权页。
3. 授权成功后，能回到原始 `slug` 页面。
4. 首次访问 `approval` 页面时，会进入 `pending` 状态并触发通知。
5. 审批通过后，访客可正常访问页面。
6. 访客再次访问同一页面时，不需要重复授权。
7. 访客访问 `private` 页面时，仍遵守原有 private 规则。
8. dashboard 和待审批列表中，provider 显示为 `Google`。
9. 非法或过期 `state` 会被拒绝，而不是静默放行。
10. 切换到 HTTPS 后，session cookie 带 `Secure`。

---

## 预期最小代码骨架

仅作实现提示，不要求完全照抄：

```python
@app.get("/auth/google")
async def google_login(request: Request, redirect: str = Query("")):
    google_config = CONFIG.get("google", {})
    client_id = google_config.get("client_id", "")
    client_secret = google_config.get("client_secret", "")
    if not client_id or not client_secret:
        raise HTTPException(500, "Google OAuth not configured")

    callback_url = f"{BASE_URL}/auth/google/callback"
    state = build_oauth_state(redirect)
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": client_id,
        "redirect_uri": callback_url,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "consent",
    })
    return RedirectResponse(auth_url)


@app.get("/auth/google/callback")
async def google_callback(
    request: Request,
    code: str = Query(""),
    state: str = Query(""),
):
    google_config = CONFIG.get("google", {})
    redirect_slug = parse_oauth_state(state)

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": google_config["client_id"],
                "client_secret": google_config["client_secret"],
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": f"{BASE_URL}/auth/google/callback",
            },
        )
        token_data = token_resp.json()
        access_token = token_data.get("access_token", "")

        user_resp = await client.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        user = user_resp.json()

    visitor_id = f"google_{user['sub']}"
    name = user.get("name") or user.get("email") or "Google 用户"
    avatar = user.get("picture", "")
    return await _register_visitor_and_redirect(
        request, visitor_id, "google", name, avatar, redirect_slug
    )
```

---

## 本文档的边界

本文档只覆盖：

- Google 访客登录接入
- 与当前审批流兼容的最小实现

本文档不覆盖：

- Google Workspace 域限制
- 基于 Google 账号自动白名单
- 多 provider 账号合并
- 前端视觉改版

如果后续要做“同一个人可用钉钉、微信、Google 任一方式登录并合并为同一 visitor”，那会变成账号绑定问题，需要单独设计，不应和这次 OAuth 接入混在一起。
