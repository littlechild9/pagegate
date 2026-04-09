# PageGate

> A personal HTML hub with built-in access control for the vibe-coding era.

## Vision

In the age of AI-assisted coding, HTML has become the better document format. With a single prompt, anyone can generate rich, interactive pages -- no design skills required. People are creating HTML pages for personal use every day and sharing them with family, friends, and colleagues.

**PageGate** is built for this personal HTML era. Unlike generic HTML hosting services, PageGate gives you a fully controllable access control system as its core feature. You decide exactly who can see each page -- whether it's open to the world, restricted to approved visitors, or completely private.

Authentication is central to this vision. PageGate currently supports **DingTalk OAuth login**, with **WeChat, Feishu, Google, Apple, and GitHub** login coming soon. Each login provider lets you gate access to the people in your life, not just anonymous internet traffic.

This is what sets PageGate apart: it's not just hosting -- it's your personal gateway to sharing HTML with the right people.

## Public Server

We run a public instance so you can get started without self-hosting:

**http://xuanzhang.net:8888**

- Registration is open -- sign up to start publishing and managing your pages
- Great for trying things out or if you don't have your own server

If you want full control over your data and configuration, follow the self-hosting guide below.

---

## 快速开始（自托管）

### 1. 安装依赖

```bash
# Python 3.9+
pip install -r requirements.txt
```

### 2. 修改配置

```bash
cp config.example.yaml config.yaml
```

编辑你自己的 `config.yaml`，**必须修改**以下两项：

```yaml
admin_token: "your-random-secret-token-here"

server:
  session_secret: "another-random-secret-here"
  base_url: "https://your-domain.com"   # 你的实际域名
```

生成随机 token 的方法：

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 3. 启动

```bash
python3 server.py
```

服务运行在 `http://0.0.0.0:8888`。

---

## 部署到服务器

### 方式一：一键部署脚本（推荐）

推荐直接在本地运行包装脚本。它会自动生成并保存在本地的 super-admin token，渲染部署用 `config.yaml`，上传代码，然后在服务器上完成 Nginx + Let's Encrypt SSL + systemd 配置：

```bash
# 在本地仓库里执行
bash scripts/run_server.sh user@your-server your-domain.com
```

本地脚本会自动：
- 生成或复用 super-admin token，并写入 `.deploy-secrets/your-domain.com.env`
- 生成部署配置 `.deploy-secrets/your-domain.com.config.yaml`
- 将代码同步到远端 `/opt/pagegate`
- 通过 SSH 调用远端 `sudo bash deploy.sh your-domain.com`

远端脚本会自动：
- 安装 Nginx、Certbot、Python 依赖
- 配置 Nginx 反向代理
- 申请 Let's Encrypt 免费 SSL 证书（自动续期）
- 创建 systemd 守护进程

部署完成后，终端会直接展示 dashboard 地址和 super-admin token。

> **前提**：域名的 DNS A 记录已指向服务器 IP。

### 方式二：手动部署

```bash
# 1. 上传项目到服务器
scp -r pagegate/ user@your-server:/opt/pagegate

# 2. 在服务器上安装依赖
ssh user@your-server
cd /opt/pagegate
pip3 install -r requirements.txt

# 3. 创建 systemd 服务
sudo tee /etc/systemd/system/pagegate.service > /dev/null <<'EOF'
[Unit]
Description=PageGate
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/pagegate
ExecStart=/usr/bin/python3 /opt/pagegate/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 4. 启动
sudo systemctl daemon-reload
sudo systemctl enable pagegate
sudo systemctl start pagegate

# 查看日志
sudo journalctl -u pagegate -f
```

### 方式二：Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8888
CMD ["python3", "server.py"]
```

```bash
docker build -t pagegate .
docker run -d \
  -p 8888:8888 \
  -v ./data:/app/data \
  -v ./pages:/app/pages \
  -v ./config.yaml:/app/config.yaml \
  --name pagegate \
  pagegate
```

### Nginx 反向代理（HTTPS）

```nginx
server {
    listen 443 ssl http2;
    server_name hub.example.com;

    ssl_certificate     /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    client_max_body_size 50m;  # HTML 文件上传大小限制

    location / {
        proxy_pass http://127.0.0.1:8888;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name hub.example.com;
    return 301 https://$host$request_uri;
}
```

---

## 使用方法

### 发布页面

**方式一：CLI**

```bash
curl -X POST https://hub.example.com/api/publish \
  -H "Authorization: Bearer <your-admin-token>" \
  -F "slug=xian-trip" \
  -F "title=西安之旅" \
  -F "category=旅行" \
  -F "access=public" \
  -F "description=女儿的西安旅行照片集" \
  -F "file=@xian-trip.html"
```

**方式二：Dashboard 网页上传**

访问 `https://hub.example.com/dashboard?token=<your-admin-token>`，点击「发布新页面」。

### 访问模式

| 模式 | 说明 |
|------|------|
| `public` | 任何人都能看，会出现在首页目录 |
| `approval` | 需要登录 + 管理员审批后才能看 |
| `private` | 只有管理员能看（需携带 token） |

### 管理页面

```bash
# 更新元数据
curl -X PUT https://hub.example.com/api/pages/xian-trip \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"title": "新标题", "access": "approval"}'

# 删除页面
curl -X DELETE https://hub.example.com/api/pages/xian-trip \
  -H "Authorization: Bearer <token>"

# 查看访客
curl https://hub.example.com/api/pages/xian-trip/visitors \
  -H "Authorization: Bearer <token>"

# 审批访客
curl -X POST https://hub.example.com/api/pages/xian-trip/approve \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"visitor_id": "dingtalk_xxx"}'
```

---

## 配置钉钉 OAuth 登录

钉钉扫码登录门槛较低，个人/企业均可使用，推荐优先配置。

### 第一步：创建钉钉应用

1. 打开 [钉钉开放平台](https://open-dev.dingtalk.com/)，用你的钉钉账号登录
2. 如果你还没有组织，需要先创建一个（个人也可以创建）
3. 点击左侧菜单「应用开发」
4. 选择「企业内部开发」→ 点击「创建应用」
5. 填写应用名称（如 `PageGate`）和描述，点击确认

### 第二步：获取 Client ID 和 Client Secret

1. 创建完成后，进入应用详情页
2. 在「凭证与基础信息」页面，可以看到：
   - **Client ID**（也叫 AppKey）— 形如 `dingxxxxxxxxxxxxxxxx`
   - **Client Secret**（也叫 AppSecret）— 点击查看后复制
3. **记录下来**，后面要填到 `config.yaml` 中

### 第三步：开启「登录与分享」功能

这一步是关键，很多人漏掉：

1. 在应用详情页，点击左侧菜单「登录与分享」
2. 点击「配置回调域名」（或「添加回调域名」）
3. 填写你的回调地址：

**如果有域名 + HTTPS：**
```
https://hub.example.com/auth/dingtalk/callback
```

**如果用 IP + 端口测试：**
```
http://你的IP:8888/auth/dingtalk/callback
```

> 注意：钉钉支持 HTTP 回调地址用于开发测试。生产环境建议用 HTTPS。

### 第四步：添加权限

1. 在应用详情页，点击左侧菜单「权限管理」
2. 搜索并申请以下权限：
   - **`Contact.User.Read`** — 个人手机号信息和个人邮箱信息（获取昵称、头像）
   - **`openid`** — 获取用户的 openid（通常默认已有）
3. 点击「申请权限」→ 确认

### 第五步：写入配置文件

编辑 `config.yaml`，填入第二步获取的凭证：

```yaml
dingtalk:
  app_key: "dingxxxxxxxxxxxxxxxx"       # 你的 Client ID
  app_secret: "xxxxxxxxxxxxxxxxxxxxxxxxxxx"  # 你的 Client Secret
```

同时确保 `base_url` 与回调地址中的域名一致：

```yaml
server:
  base_url: "https://hub.example.com"   # 或 "http://你的IP:8888"
```

### 第六步：重启并验证

```bash
bash start.sh
```

1. 访问一个 `access: approval` 的页面（如 `http://localhost:8888/secret-page`）
2. 应该看到「钉钉登录」按钮
3. 点击后跳转到钉钉扫码/授权页面
4. 用钉钉扫码后，自动回调 → 提交访问申请 → 显示「等待审批」页

### 排错

| 问题 | 原因 | 解决 |
|------|------|------|
| 点击登录后 404 | `base_url` 配置错误 | 确保 `config.yaml` 里的 `base_url` 和实际访问地址一致 |
| 回调后报 `Failed to get DingTalk token` | Client ID/Secret 填错 | 检查 `config.yaml` 中的 `app_key` 和 `app_secret` |
| 回调后报 `redirect_uri 不合法` | 回调域名不匹配 | 钉钉后台填的回调地址要和 `base_url + /auth/dingtalk/callback` 完全一致 |
| 登录页没有「钉钉登录」按钮 | `app_key` 为空 | 确认 `config.yaml` 里的 `dingtalk.app_key` 已填写 |

---

## 配置微信 OAuth 登录

微信网页扫码登录需要**微信开放平台**账号（需企业资质认证，审核费 300 元）。如果你的用户主要用微信，建议配置。

### 第一步：注册微信开放平台

1. 打开 [微信开放平台](https://open.weixin.qq.com/)
2. 注册账号并完成开发者资质认证（需要企业营业执照）
3. 认证审核通过后才能创建网站应用

### 第二步：创建网站应用

1. 进入「管理中心」→「网站应用」→「创建网站应用」
2. 填写：
   - 应用名称：PageGate
   - 应用官网：`https://hub.example.com`
3. 提交审核（通常 1-7 个工作日）

### 第三步：配置授权回调域

1. 应用审核通过后，进入应用详情
2. 找到「接口信息」→「网站应用」→「授权回调域」
3. 填写你的域名（不含协议和路径）：

```
hub.example.com
```

### 第四步：获取凭证

在应用详情页记录下：

- **AppID**
- **AppSecret**

### 第五步：写入配置

编辑 `config.yaml`：

```yaml
wechat:
  app_id: "wxxxxxxxxxxxxxxxxxxx"        # 你的 AppID
  app_secret: "xxxxxxxxxxxxxxxxxxxxxxx"  # 你的 AppSecret
```

### 验证

重启服务后，访问 `access: approval` 的页面，应该能看到「微信登录」按钮。点击后显示微信二维码，用户扫码授权后自动回调。

### 注意事项

- 微信 OAuth 回调域名必须与 `config.yaml` 中 `server.base_url` 的域名一致
- 微信开放平台的网站应用仅支持扫码登录，**不支持**微信内置浏览器的网页授权（那属于公众号 OAuth）
- 如果需要支持微信内打开链接直接授权，需要额外配置微信公众号（服务号），这是另一套接口

---

## 审批流程说明

当访客访问 `approval` 模式的页面时：

```
访客打开链接 → 未登录 → 显示登录页（钉钉/微信）
    ↓
扫码登录 → 自动提交访问申请 → 显示「等待审批」页
    ↓                            ↓
    ↓                   OpenClaw 推送通知到你的微信/钉钉
    ↓                            ↓
    ↓                   你回复「通过」→ OpenClaw 调用 approve API
    ↓
访客页面自动刷新 → 看到内容
```

管理员有三种方式审批：

1. **OpenClaw 对话审批**（推荐）— 收到推送后直接回复"通过"或"拒绝"
2. **Dashboard 网页审批** — 在管理后台点击按钮
3. **API 审批** — 通过 curl 或其他工具调用

```bash
# API 通过
curl -X POST https://hub.example.com/api/pages/xian-trip/approve \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"visitor_id": "dingtalk_xxx"}'

# API 拒绝
curl -X POST https://hub.example.com/api/pages/xian-trip/reject \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"visitor_id": "dingtalk_xxx"}'

# 查看所有待审批
curl https://hub.example.com/api/pending \
  -H "Authorization: Bearer <token>"
```

---

## 配置 OpenClaw 审批通知

配置 OpenClaw 后，访客申请页面访问时会通过你绑定的 channel（微信/钉钉/Slack 等）推送通知，你可以直接对话回复"通过"或"拒绝"来完成审批。

有两种通知模式：

| 模式 | 适用场景 | 原理 |
|------|---------|------|
| **Webhook 推送** | OpenClaw 有公网地址 | PageGate 主动 POST 到 OpenClaw |
| **SSE Watcher 拉取** | OpenClaw 在内网/本地 | Watcher 长连接 PageGate 的 SSE 端点 |

两种可以同时启用，也可以只用其中一种。

### 第一步：安装 Skill + 脚本

将 `openclaw-skill/` 目录复制到 OpenClaw 的 skills 目录：

```bash
cp -r openclaw-skill ~/.openclaw/workspace/skills/pagegate-client
```

### 第二步：配置环境变量

OpenClaw 需要知道你自己的 PageGate 地址和管理员 token：

```bash
export PAGEGATE_URL="https://hub.example.com"
export PAGEGATE_ADMIN_TOKEN="your-admin-token"
```

这些值不要写死进代码里，放在你自己的 shell 配置、launch 脚本或 OpenClaw 运行配置里。

### 第三步（可选A）：配置 Webhook 推送

如果 OpenClaw 有公网可达的 webhook 地址，编辑 `config.yaml`：

```yaml
openclaw:
  webhook_url: "https://your-openclaw-instance/hooks/pagegate"
  webhook_token: "your-openclaw-webhook-token"
```

### 第三步（可选B）：启动 SSE Watcher

如果 OpenClaw 在内网/本地，无法接收 webhook，改用 bridge watcher 主动拉取事件并注入到你指定的活跃 OpenClaw 会话。

先配置环境变量：

```bash
export PAGEGATE_URL="https://hub.example.com"
export PAGEGATE_ADMIN_TOKEN="your-admin-token"
export OPENCLAW_SESSION_KEY="你的目标 sessionKey"
# 可选：当 gateway 不走默认本机配置时再显式指定
# export OPENCLAW_GATEWAY_URL="ws://127.0.0.1:18789"
# export OPENCLAW_GATEWAY_TOKEN="your-gateway-token"
```

然后启动 bridge watcher：

```bash
# 在后台运行 bridge watcher（保持长连接，实时接收审批通知）
nohup python3 ~/.openclaw/workspace/skills/pagegate-client/scripts/pagegate_watch.py &

# 或用 systemd / screen / tmux 管理
```

Bridge watcher 会：
- 长连接 PageGate 的 `GET /api/events/stream`（SSE）
- 启动或重连时补拉 `/api/pending`
- 对事件去重、限速
- 通过 OpenClaw Gateway RPC `send` 直接通知到你配置的 channel
- 默认写日志到 `~/.openclaw/workspace/memory/pagegate-watch.log`，避免后台 stdout 干扰 OpenClaw

你可以用下面脚本测试 `chat.send` 是否正常：

```bash
export OPENCLAW_SESSION_KEY="你的目标 sessionKey"
./scripts/test_gateway_chat_send.sh "hello from gateway rpc"
```

### 第四步：验证

1. 发布一个 `access: approval` 的页面
2. 用另一个设备打开页面链接，通过钉钉/微信登录
3. 你应该在 OpenClaw 绑定的 channel 收到通知：

```
有人想查看你的页面
页面：西安之旅 (xian-trip)
访客：妈妈（钉钉登录）
访客ID：dingtalk_oABC123

回复"通过"或"拒绝"即可。
```

4. 回复"通过"，OpenClaw 调用 approve API
5. 访客页面自动刷新，看到内容

### CLI 客户端

Skill 自带一个零依赖的命令行客户端 `scripts/pagegate_client.py`，覆盖所有管理操作：

```bash
# 发布页面
python3 scripts/pagegate_client.py publish --file page.html --slug my-page --title "我的页面" --access public

# 查看待审批
python3 scripts/pagegate_client.py pending

# 通过访客
python3 scripts/pagegate_client.py approve --slug xian-trip --visitor-id dingtalk_oABC123

# 拒绝访客
python3 scripts/pagegate_client.py reject --slug xian-trip --visitor-id dingtalk_oABC123

# 更新页面元数据
python3 scripts/pagegate_client.py update --slug my-page --title "新标题" --access approval

# 删除页面
python3 scripts/pagegate_client.py delete --slug my-page

# 撤销访客权限
python3 scripts/pagegate_client.py revoke --slug my-page --visitor-id dingtalk_oABC123
```

---

## 配置原则

- 代码和 README 保持通用，不写死个人账号、sessionKey、聊天目标或 token。
- 所有个人化信息都放进你自己的 `config.yaml`、环境变量或部署配置里。
- 如果你要分享这个项目，优先分享 `config.example.yaml`，不要分享你自己的 `config.yaml`。

## 目录结构

```
pagegate/
├── server.py              # 后端服务（FastAPI）
├── config.yaml            # 配置文件
├── requirements.txt       # Python 依赖
├── deploy.sh              # 一键部署脚本（Nginx + SSL + systemd）
├── .gitignore
├── openclaw-skill/        # OpenClaw 审批技能
│   ├── SKILL.md           # 技能定义（对话式审批）
│   └── scripts/
│       ├── pagegate_client.py  # CLI 客户端（发布/审批/管理）
│       └── pagegate_watch.py   # SSE Watcher（实时拉取审批事件）
├── templates/             # Jinja2 模板
│   ├── index.html         # 公开目录页
│   ├── dashboard.html     # 管理后台
│   ├── login.html         # 登录页
│   └── pending.html       # 等待审批页
├── data/                  # 数据存储（JSON 文件）
│   ├── index.json         # 页面元数据索引
│   └── visitors.json      # 访客记录
└── pages/                 # HTML 页面文件
    ├── index.html         # 自动生成的公开目录
    └── {slug}/
        └── index.html     # 各页面内容
```

## License

MIT
