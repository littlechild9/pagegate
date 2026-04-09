# PageGate — 架构设计文档 v2

> 一个简单的Python服务器，用于发布、管理和分享AI生成的HTML页面。

---

## 系统概览

```
本地文件夹
├── server.py              # Python服务器（唯一后端）
├── config.yaml            # 配置（管理员密钥、OAuth配置）
├── data/
│   ├── index.json         # 所有页面的元数据索引
│   └── visitors.json      # 访客授权记录
└── pages/
    ├── index.html          # 自动生成的管理首页（公开目录）
    ├── xian-trip/
    │   └── index.html
    ├── q1-report/
    │   └── index.html
    └── ai-weekly/
        ├── index.html
        └── assets/
            └── chart.png
```

---

## 模块一：发布（Publish）

### 发布鉴权

只有管理员（你）可以发布。简单方案：Bearer Token。

```yaml
# config.yaml
admin_token: "一个随机长字符串"  # 发布时携带此token
```

### 发布方式

**方式1：CLI上传**

```bash
curl -X POST http://your-server:8000/api/publish \
  -H "Authorization: Bearer <admin_token>" \
  -F "slug=xian-trip" \
  -F "title=西安之旅" \
  -F "category=travel" \
  -F "access=approval" \
  -F "file=@xian-trip.html"
```

**方式2：本地直接放文件**

```bash
# 把HTML文件放进pages目录，然后触发索引更新
cp xian-trip.html pages/xian-trip/index.html
curl -X POST http://your-server:8000/api/reindex \
  -H "Authorization: Bearer <admin_token>"
```

### 发布流程

```
1. 验证 admin_token
2. 将HTML存入 pages/{slug}/index.html
3. 更新 data/index.json（添加元数据）
4. 重新生成 pages/index.html（管理首页）
5. 返回访问链接
```

---

## 模块二：管理（Manage）

### 设计决策：两层索引

访客的入口是你直接分享的链接，不是目录。访客不需要、也不应该看到完整的页面列表（标题本身就是信息泄露）。因此采用两层索引设计：

**公开层：`/` (index.html)** — 静态生成，仅列出 `access=public` 的页面。纯展示，无敏感信息。

```
┌──────────────────────────────────────────────┐
│  📂 PageGate                                 │
│                                              │
│  📄 杭州周末          2026-03-15              │
│  📄 AI周报 #12       2026-04-05              │
│                                              │
│  （仅显示公开页面）                             │
└──────────────────────────────────────────────┘
```

**私有层：`/dashboard` (需admin_token)** — 管理员专用，显示所有页面及其状态、访客、权限。

```
┌──────────────────────────────────────────────┐
│  📂 PageGate — Dashboard           [发布新页面] │
│                                              │
│  🔍 搜索...                                  │
│                                              │
│  ── 旅行 ──────────────────────────────────  │
│  📄 西安之旅          2026-04-01   🔒 审批制  │
│     └ 已授权：妈妈、爸爸  │  待审批：0          │
│  📄 杭州周末          2026-03-15   🌐 公开    │
│                                              │
│  ── 工作 ──────────────────────────────────  │
│  📄 Q1数据报告        2026-03-30   🔒 审批制  │
│     └ 已授权：合伙人A  │  待审批：1            │
│  📄 AI周报 #12       2026-04-05   🌐 公开    │
│                                              │
│  ── 个人 ──────────────────────────────────  │
│  📄 读书笔记合集       2026-02-20   🔒 私密   │
│                                              │
└──────────────────────────────────────────────┘
```

这样 index.html 保持纯静态生成（每次发布时重建），不涉及任何权限逻辑。所有需要鉴权的页面通过直接链接分享，访客通过链接进入 → 登录 → 申请 → 审批。

### 数据模型

```json
// data/index.json
{
  "pages": [
    {
      "slug": "xian-trip",
      "title": "西安之旅",
      "category": "travel",
      "created_at": "2026-04-01T10:00:00Z",
      "access": "approval",
      "description": "女儿的西安旅行照片集"
    }
  ]
}
```

### 管理操作（全部需要admin_token）

```
POST   /api/publish              # 上传新页面
POST   /api/reindex              # 扫描pages目录，重建索引
PUT    /api/pages/:slug          # 更新元数据（标题/分类/访问模式）
DELETE /api/pages/:slug          # 删除页面
GET    /api/pages/:slug/visitors # 查看访客列表
DELETE /api/pages/:slug/visitors/:id  # 撤销访客权限
```

---

## 模块三：鉴权（Auth）

### 访客登录：微信 + 钉钉

访客通过微信或钉钉OAuth登录，系统获取其身份信息（openid + 昵称 + 头像），无需自建账号体系。

### 鉴权流程

```
访客打开链接
    │
    ▼
access == public? ──是──► 直接返回HTML
    │
    否
    ▼
检查Cookie中的session
    │
    ▼
有有效session? ──是──► 该visitor已被批准? ──是──► 返回HTML
    │                        │
    否                       否
    ▼                        ▼
显示登录页                  显示"等待审批"页
┌─────────────┐            ┌──────────────┐
│ [微信登录]   │            │ ⏳ 已申请      │
│ [钉钉登录]   │            │ 等待作者审批   │
└─────────────┘            └──────────────┘
    │
    ▼
OAuth回调 → 获取用户身份
    │
    ▼
自动提交访问申请
    │
    ▼
OpenClaw推送给作者
    │
    ▼
作者回复"通过" → 更新visitors.json → 访客刷新后可看
```

### OAuth配置

```yaml
# config.yaml
admin_token: "xxx"

wechat:
  app_id: "wx..."
  app_secret: "..."
  # 微信开放平台OAuth2.0
  # 需要企业认证的开放平台账号

dingtalk:
  app_key: "..."
  app_secret: "..."
  # 钉钉扫码登录
  # 企业内部应用即可，门槛较低

server:
  host: "0.0.0.0"
  port: 8000
  base_url: "https://hub.example.com"
  session_secret: "随机字符串"
```

### 访客数据模型

```json
// data/visitors.json
{
  "visitors": [
    {
      "id": "wechat_oABC123",
      "provider": "wechat",
      "name": "妈妈",
      "avatar": "https://...",
      "first_seen": "2026-04-08T10:00:00Z",
      "approved_pages": ["xian-trip"],
      "pending_pages": [],
      "blocked": false
    }
  ]
}
```

### OpenClaw审批集成

```
OpenClaw: "有人想看「西安之旅」
           昵称：妈妈（微信登录）
           要通过吗？"

作者：    "通过"

OpenClaw → POST /api/pages/xian-trip/approve
           { "visitor_id": "wechat_oABC123" }
           Header: Authorization: Bearer <admin_token>
```

---

## Server.py 路由总览

```python
# === 静态服务 ===
GET  /                          # → pages/index.html（公开目录，仅public页面）
GET  /dashboard                 # → 管理后台（需admin_token，显示所有页面+访客状态）
GET  /:slug                     # → 鉴权检查 → pages/{slug}/index.html
GET  /:slug/assets/*            # → 静态资源

# === 管理API（需admin_token）===
POST   /api/publish             # 上传HTML
POST   /api/reindex             # 重建索引
PUT    /api/pages/:slug         # 更新元数据
DELETE /api/pages/:slug         # 删除页面
GET    /api/pages/:slug/visitors    # 访客列表
POST   /api/pages/:slug/approve     # 批准访客（OpenClaw回调）
POST   /api/pages/:slug/reject      # 拒绝访客
DELETE /api/pages/:slug/visitors/:id # 撤销权限

# === OAuth ===
GET  /auth/wechat               # 微信登录跳转
GET  /auth/wechat/callback      # 微信回调
GET  /auth/dingtalk             # 钉钉登录跳转
GET  /auth/dingtalk/callback    # 钉钉回调

# === 访客API ===
GET  /api/check-approval/:slug  # 轮询审批状态
```

---

## 部署

最简单的部署方式：一台VPS + Nginx反向代理。

```
互联网 → Nginx(443/SSL) → Python(8000)
```

```nginx
server {
    listen 443 ssl;
    server_name hub.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
    }
}
```

Python服务器用 FastAPI 或 Flask，单文件即可。

```bash
# 启动
pip install fastapi uvicorn
python server.py

# 或用systemd做守护进程
```

---

## MVP 优先级

### 第一步：能发能看（2小时）

- [ ] FastAPI server.py，静态文件服务
- [ ] `POST /api/publish` 上传HTML + admin_token验证
- [ ] 自动生成 `pages/index.html` 目录页
- [ ] public模式直接访问

### 第二步：能控（半天）

- [ ] 接入钉钉OAuth（门槛比微信低，不需要企业认证）
- [ ] session管理（Cookie + 服务端session存储）
- [ ] approval模式：登录后自动申请 → 存入pending
- [ ] OpenClaw skill：推送审批 + 处理回复

### 第三步：微信（看需求）

- [ ] 接入微信OAuth（需要企业认证）
- [ ] 微信内置浏览器兼容处理

### 第四步：打磨（持续）

- [ ] 访客管理页面（谁看了什么、什么时候看的）
- [ ] 批量审批
- [ ] Token过期策略
- [ ] index.html美化（分类、搜索、缩略图）
