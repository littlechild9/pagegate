# PageGate

> A personal HTML hub with built-in access control for the vibe-coding era.

## Vision

In the age of AI-assisted coding, HTML has become the better document format. With a single prompt, anyone can generate rich, interactive pages -- no design skills required. People are creating HTML pages for personal use every day and sharing them with family, friends, and colleagues.

**PageGate** is built for this personal HTML era. Unlike generic HTML hosting services, PageGate gives you a fully controllable access control system as its core feature. You decide exactly who can see each page -- whether it's open to the world, restricted to approved visitors, or completely private.

Authentication is central to this vision. PageGate currently supports **DingTalk OAuth login**, with **WeChat** login already wired in for deployments that need it. Each login provider lets you gate access to the people in your life, not just anonymous internet traffic.

This is what sets PageGate apart: it's not just hosting -- it's your personal gateway to sharing HTML with the right people.

## Why PageGate

PageGate 不是普通的静态 HTML 托管。

它的重点是：

- 让你发布自己生成的 HTML 页面
- 让每个页面都带上清晰的访问控制
- 让“谁能看、什么时候能看”变成一个实时决策，而不是预先写死的白名单

页面支持三种权限：

- `public`：任何人都能看
- `approval`：访客登录后，需要你实时审批
- `private`：只有页面 owner 能看

### 核心 Feature：实时鉴权

PageGate 最重要的 feature 不是上传页面，而是实时授权。

当访客访问一个 `approval` 页面时，流程是：

```text
访客打开页面
  -> 用钉钉 / 微信登录
  -> PageGate 记录为待审批
  -> OpenClaw watcher 通过 SSE 收到事件
  -> OpenClaw 把消息发到你的微信 / 其它通知通道
  -> 你直接回复 1 / 2，或回复“通过” / “拒绝”
  -> 访客页面立刻刷新，得到结果
```

这件事的价值在于：

- 它是实时的，不需要你提前维护访客名单
- 它是轻量的，审批动作就在消息里完成
- 它是自然的，适合家庭成员、朋友、同事之间的小范围分享

### 最简单的开始方式

如果你只是想用起来，不要先研究部署。

然后直接把下面这段话贴给 OpenClaw：

```text
请帮我配置 PageGate。

要求：
- 优先使用默认公共服务器 http://115.190.148.77:8888
- 只使用 OpenClaw 的 SSE watcher，不要配置 webhook
- 如果缺少公共服务器账号、PAGEGATE_ADMIN_TOKEN 或通知路由，请逐项向我询问
- 先执行这个安装命令（它会从 GitHub 下载 skill 并进入安装流程）：
  curl -fsSL https://raw.githubusercontent.com/littlechild9/pagegate/main/openclaw-skill/install.sh | bash
- 如果安装脚本询问是否运行初始化向导，请选择 Y，并继续完成 setup.py
- 配置完成后启动 watcher
- 最后告诉我如何发布一个本地 HTML 页面
```

这就是推荐入口。

默认公共服务器：

```text
http://115.190.148.77:8888
```

OpenClaw 会继续引导你补齐当前需要的信息，例如：

- 公共服务器账号或登录信息
- `PAGEGATE_ADMIN_TOKEN`
- OpenClaw 的通知路由
- 本地 HTML 文件路径
- 页面的 `slug`、标题和访问模式

配置完成后，你通常只需要继续对 OpenClaw 说：

```text
把 /absolute/path/page.html 发布到 PageGate，slug 用 my-page，标题用 我的页面，access 用 approval。
```

如果你想手工打开 Dashboard：

```text
http://115.190.148.77:8888/dashboard?token=<token>
```

### OpenClaw skill 是怎么接进来的

README 里只保留一种推荐集成方式：`SSE watcher`。

- `openclaw-skill/install.sh`：安装 skill
- `openclaw-skill/scripts/setup.py`：初始化向导，生成 `.env`
- `openclaw-skill/scripts/start-watcher.sh`：启动并守护 watcher
- `openclaw-skill/scripts/pagegate_watch.py`：同步 `/api/pending`，订阅 `/api/events/stream`
- `openclaw-skill/scripts/pagegate_client.py`：发布、审批、更新、删除页面

如果你确实要手工启动 watcher：

```bash
cd ~/.openclaw/workspace/skills/pagegate-client
./scripts/start-watcher.sh
```

## 第二部分：自托管服务器

如果你想完全控制数据、域名、登录配置和 token，再看这一部分。

### 本地启动

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
```

至少修改这些配置：

```yaml
admin_token: "your-random-secret-token"

registration:
  mode: "open"

server:
  host: "0.0.0.0"
  port: 8888
  base_url: "https://your-domain.com"
  session_secret: "another-random-secret"
```

启动：

```bash
python3 server.py
```

### 部署到服务器

推荐直接使用本地包装脚本：

```bash
bash scripts/run_server.sh user@your-server your-domain.com
```

它会：

- 在本地生成或复用部署密钥
- 渲染部署用 `config.yaml`
- 同步代码到远端
- 在远端调用 `deploy.sh`
- 自动完成 Nginx、SSL 和 systemd 配置

如果你已经把代码放到远端，也可以直接运行：

```bash
sudo bash deploy.sh your-domain.com
```

### 配置登录方式

自托管时，`approval` 页面要依赖你自己的登录配置。

#### DingTalk

推荐优先配置钉钉：

```yaml
dingtalk:
  app_key: "dingxxxxxxxxxxxxxxxx"
  app_secret: "xxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

回调地址：

```text
${server.base_url}/auth/dingtalk/callback
```

#### WeChat

如果你要支持微信扫码登录：

```yaml
wechat:
  app_id: "wxxxxxxxxxxxxxxxxxxx"
  app_secret: "xxxxxxxxxxxxxxxxxxxxxxx"
```

回调地址：

```text
${server.base_url}/auth/wechat/callback
```

### 自托管之后，还是一句话交给 OpenClaw

当你的自托管服务器准备好之后，继续把下面这段话贴给 OpenClaw，把域名和 token 换成你自己的：

```text
请帮我配置 PageGate。

要求：
- 使用我自托管的服务器 https://your-domain.com
- 我的 PAGEGATE_ADMIN_TOKEN 是 <your-admin-token>
- 只使用 OpenClaw 的 SSE watcher，不要配置 webhook
- 先执行这个安装命令（它会从 GitHub 下载 skill 并进入安装流程）：
  curl -fsSL https://raw.githubusercontent.com/littlechild9/pagegate/main/openclaw-skill/install.sh | bash
- 如果安装脚本询问是否运行初始化向导，请选择 Y，并继续完成 setup.py
- 配置完成后启动 watcher
- 最后告诉我如何发布一个本地 HTML 页面
```

如果你是通过 `bash scripts/run_server.sh user@your-server your-domain.com` 部署的，脚本结束时会直接显示 dashboard 地址和 super-admin token。

## 关键文件

- `server.py`：FastAPI 后端
- `templates/`：登录页、等待审批页、Dashboard
- `pages/`：发布后的 HTML 页面
- `data/`：页面索引、访客、用户数据
- `openclaw-skill/`：OpenClaw skill 与 watcher

## License

MIT
