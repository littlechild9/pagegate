#!/usr/bin/env python3
"""
PageGate OpenClaw Skill 初始化向导

交互式引导用户完成：
1. 选择接入方式（托管服务器 / 自部署服务器）
2. 获取 PageGate API token（注册 / 登录 / 使用已有 token）
3. 验证服务器连通性
4. 识别 OpenClaw 通知通道配置
5. 写入 .env 配置文件
6. 启动 watcher 并发送测试消息
7. 验证端到端通路
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import getpass
from pathlib import Path
from urllib import error, request

# ── 路径 ──────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent          # openclaw-skill/scripts/
SKILL_DIR = SCRIPT_DIR.parent                         # openclaw-skill/
ENV_FILE = SKILL_DIR / ".env"
ENV_EXAMPLE = SKILL_DIR / ".env.example"
WATCH_SCRIPT = SCRIPT_DIR / "pagegate_watch.py"
START_WATCHER = SCRIPT_DIR / "start-watcher.sh"

# ── 默认值 ─────────────────────────────────────────────────────────
DEFAULT_SERVER_URL = "http://115.190.148.77:8888"
DEFAULT_CHANNEL = "openclaw-weixin"
DEFAULT_LOG_FILE = "~/.openclaw/workspace/memory/pagegate-watch.log"
DEFAULT_STATE_FILE = "/tmp/pagegate-watch-state.json"
GITHUB_REPO = "https://github.com/littlechild9/pagegate"

# ── 颜色 ──────────────────────────────────────────────────────────
USE_COLOR = sys.stdout.isatty()


def c(code, text):
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def green(t):   return c("32", t)
def yellow(t):  return c("33", t)
def red(t):     return c("31", t)
def cyan(t):    return c("36", t)
def bold(t):    return c("1", t)
def dim(t):     return c("2", t)


def banner(title):
    width = 50
    print()
    print(cyan("─" * width))
    print(cyan(f"  {title}"))
    print(cyan("─" * width))


def step(n, total, desc):
    print(f"\n{bold(f'[{n}/{total}]')} {desc}")


def ok(msg):
    print(f"  {green('✓')} {msg}")


def warn(msg):
    print(f"  {yellow('⚠')} {msg}")


def fail(msg):
    print(f"  {red('✗')} {msg}")


def info(msg):
    print(f"  {dim('→')} {msg}")


def ask(prompt, default=""):
    suffix = f" [{default}]" if default else ""
    try:
        ans = input(f"  {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    return ans or default


def ask_secret(prompt):
    try:
        if sys.stdin.isatty():
            ans = getpass.getpass(f"  {prompt}: ").strip()
        else:
            ans = input(f"  {prompt}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    return ans


def ask_yn(prompt, default=True):
    hint = "Y/n" if default else "y/N"
    ans = ask(f"{prompt} ({hint})", "y" if default else "n")
    return ans.lower() in ("y", "yes", "是")


def ask_choice(prompt, options):
    for i, (label, _) in enumerate(options, 1):
        print(f"    {bold(str(i))}. {label}")
    while True:
        ans = ask(prompt)
        try:
            idx = int(ans) - 1
            if 0 <= idx < len(options):
                return options[idx][1]
        except ValueError:
            pass
        fail(f"请输入 1-{len(options)}")


# ── Step 1: 选择服务器 ────────────────────────────────────────────
def choose_server():
    step(1, 6, "选择 PageGate 接入方式")
    print()
    mode = ask_choice("请选择", [
        (f"使用托管服务器（推荐，{DEFAULT_SERVER_URL}）", "hosted"),
        ("使用自部署服务器", "self_hosted"),
    ])

    if mode == "hosted":
        ok(f"使用托管服务器: {DEFAULT_SERVER_URL}")
        return mode, DEFAULT_SERVER_URL

    print()
    choice = ask_choice("请选择", [
        ("连接到已经部署好的服务器", "custom"),
        ("我还没有服务器，先看部署步骤", "guide"),
    ])

    if choice == "guide":
        print()
        print(bold("  自部署服务器步骤："))
        print(f"""
    1. 克隆代码仓库:
       {cyan(f'git clone {GITHUB_REPO}')}

    2. 安装依赖:
       {cyan('cd pagegate && pip install -r requirements.txt')}

    3. 创建配置文件:
       {cyan('cp config.example.yaml config.yaml')}
       编辑 config.yaml，设置 admin_token（服务器管理员使用）和 base_url

    4. 启动服务:
       {cyan('python3 server.py')}
       服务默认运行在 http://0.0.0.0:8888

    5. （可选）使用部署脚本一键部署到 Linux 服务器:
       {cyan('sudo bash deploy.sh your-domain.com')}
""")
        if not ask_yn("服务器已经搭建好了吗？"):
            print()
            info("请先搭建好服务器，然后重新运行此脚本。")
            sys.exit(0)

    url = ask("请输入服务器地址（如 http://your-server:8888）").rstrip("/")
    if not url.startswith("http"):
        url = "http://" + url
    ok(f"服务器地址: {url}")
    return mode, url


# ── Step 2: 获取 PageGate API token ───────────────────────────────
def post_json(base_url, path, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        base_url + path,
        method="POST",
        headers={"Content-Type": "application/json"},
        data=data,
    )
    with request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else {"ok": True}


def register_account(url):
    while True:
        print()
        email = ask("请输入注册邮箱")
        password = ask_secret("请输入密码")
        password_confirm = ask_secret("请再次输入密码")

        if not email:
            fail("邮箱不能为空")
            continue
        if len(password) < 6:
            fail("密码至少需要 6 个字符")
            continue
        if password != password_confirm:
            fail("两次输入的密码不一致")
            continue

        try:
            result = post_json(url, "/api/auth/register", {
                "email": email,
                "password": password,
            })
            ok(f"注册成功: {result.get('email', email)}")
            return result["token"]
        except error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            fail(f"注册失败 (HTTP {e.code})")
            if body:
                info(body[:300])
            if e.code == 409 and ask_yn("该邮箱可能已经注册。是否改为登录？"):
                return login_account(url, default_email=email)
            if e.code == 403 and ask_yn("服务器可能关闭了注册。是否改为登录？"):
                return login_account(url, default_email=email)
        except error.URLError as e:
            fail(f"注册请求失败: {e.reason}")
        except Exception as e:
            fail(f"注册失败: {e}")

        if not ask_yn("是否重试注册？"):
            sys.exit(1)


def login_account(url, default_email=""):
    while True:
        print()
        email = ask("请输入登录邮箱", default_email)
        password = ask_secret("请输入密码")

        if not email:
            fail("邮箱不能为空")
            continue
        if not password:
            fail("密码不能为空")
            continue

        try:
            result = post_json(url, "/api/auth/login", {
                "email": email,
                "password": password,
            })
            ok(f"登录成功: {result.get('email', email)}")
            return result["token"]
        except error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            fail(f"登录失败 (HTTP {e.code})")
            if body:
                info(body[:300])
        except error.URLError as e:
            fail(f"登录请求失败: {e.reason}")
        except Exception as e:
            fail(f"登录失败: {e}")

        if not ask_yn("是否重试登录？"):
            sys.exit(1)


def configure_token(url, server_mode):
    step(2, 6, "获取 PageGate API token")
    print()
    info("这里会保存 PageGate API token。")
    if server_mode == "hosted":
        info("使用托管服务器时，普通用户直接注册或登录即可。")
        info("不需要服务器管理员提供 admin_token。")
        print()
        choice = ask_choice("请选择", [
            ("注册新账号（首次使用推荐）", "register"),
            ("登录已有账号", "login"),
        ])
    else:
        info("如果你的自部署服务器开启了 registration.open，你也可以直接注册普通账号。")
        info("如果服务器关闭了注册，再使用现有 PageGate API token 连接。")
        print()
        choice = ask_choice("请选择", [
            ("注册新账号（服务器支持注册时推荐）", "register"),
            ("登录已有账号", "login"),
            ("我已经有 PageGate API token", "token"),
        ])

    if choice == "register":
        return register_account(url)
    if choice == "login":
        return login_account(url)

    token = ask("请输入已有的 PageGate API token（不要填写 config.yaml 里的 admin_token）")
    if not token:
        fail("PageGate API token 不能为空")
        sys.exit(1)
    ok("已记录现有 API token")
    return token


# ── Step 3: 验证连通性 ────────────────────────────────────────────
def verify_connection(url, token):
    step(3, 6, "验证服务器连通性")
    try:
        req = request.Request(
            url + "/api/pending",
            headers={"Authorization": f"Bearer {token}"},
        )
        with request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        ok(f"连接成功！当前有 {data.get('count', 0)} 个待审批请求")
        return True
    except error.HTTPError as e:
        if e.code == 401 or e.code == 403:
            fail(f"认证失败 (HTTP {e.code})，请检查 PageGate API token 是否正确")
        else:
            fail(f"服务器返回错误: HTTP {e.code}")
            body = e.read().decode("utf-8", errors="replace")
            if body:
                info(body[:200])
        return False
    except error.URLError as e:
        fail(f"无法连接到服务器: {e.reason}")
        info("请检查服务器地址是否正确，以及服务是否正在运行")
        return False
    except Exception as e:
        fail(f"连接失败: {e}")
        return False


# ── Step 4: OpenClaw 通知通道配置 ─────────────────────────────────
def configure_openclaw_channel():
    step(4, 6, "配置 OpenClaw 通知通道")
    print()

    # 检测 openclaw CLI
    openclaw_ok = shutil.which("openclaw") is not None
    if not openclaw_ok:
        warn("未检测到 openclaw 命令行工具")
        info("将手动配置通道参数")
    else:
        ok("检测到 openclaw CLI")
        # 尝试自动获取通道信息
        print()
        info("尝试从 OpenClaw 获取通道信息...")
        channels = _discover_openclaw_channels()
        if channels:
            ok(f"发现 {len(channels)} 个通道")
            for ch in channels:
                info(f"  {ch}")

    print()
    print(bold("  通道配置说明："))
    print(f"""
    通知通道用于将 PageGate 的审批请求转发到你的消息通道。
    你需要提供三个参数：

    • {bold('OPENCLAW_NOTIFY_CHANNEL')} — 通道名称
      通常是 {cyan('openclaw-weixin')}（微信通道）

    • {bold('OPENCLAW_NOTIFY_TARGET')} — 接收通知的目标 ID
      这是你的微信联系人/群组在 OpenClaw 中的标识

    • {bold('OPENCLAW_NOTIFY_ACCOUNT')} — 你的 OpenClaw 账号 ID
      在 OpenClaw 设置或 ~/.openclaw/ 目录下可以找到
""")

    channel = ask("通道名称 (OPENCLAW_NOTIFY_CHANNEL)", DEFAULT_CHANNEL)
    target = ask("目标 ID (OPENCLAW_NOTIFY_TARGET)")
    account = ask("账号 ID (OPENCLAW_NOTIFY_ACCOUNT)")

    if not target:
        fail("目标 ID 不能为空")
        sys.exit(1)
    if not account:
        fail("账号 ID 不能为空")
        sys.exit(1)

    ok(f"通道: {channel}")
    ok(f"目标: {target}")
    ok(f"账号: {account}")

    return channel, target, account


def _discover_openclaw_channels():
    """尝试通过 openclaw CLI 或配置文件发现可用通道"""
    channels = []

    # 尝试读取 OpenClaw 配置
    config_path = Path.home() / ".openclaw" / "openclaw.json"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            # 提取通道相关信息
            if "channels" in config:
                for ch in config["channels"]:
                    name = ch.get("name", ch.get("type", "unknown"))
                    channels.append(name)
            if "gateway" in config:
                gw = config["gateway"]
                if isinstance(gw, dict):
                    info(f"Gateway URL: {gw.get('url', 'default')}")
        except Exception:
            pass

    # 尝试读取 device-auth 获取账号信息
    auth_path = Path.home() / ".openclaw" / "identity" / "device-auth.json"
    if auth_path.exists():
        try:
            with open(auth_path, "r", encoding="utf-8") as f:
                auth = json.load(f)
            acct = auth.get("accountId", auth.get("account_id", ""))
            if acct:
                info(f"检测到账号 ID: {acct}")
        except Exception:
            pass

    return channels


# ── Step 5: 写入 .env 文件 ────────────────────────────────────────
def write_env_file(url, token, channel, target, account):
    step(5, 6, "保存配置到 .env 文件")

    log_file = ask("日志文件路径 (PAGEGATE_WATCH_LOG_FILE)", DEFAULT_LOG_FILE)
    state_file = ask("状态文件路径 (PAGEGATE_WATCH_STATE_FILE)", DEFAULT_STATE_FILE)

    content = f"""# PageGate Watcher 环境变量
# 由 setup.py 自动生成于 {time.strftime('%Y-%m-%d %H:%M:%S')}

# PageGate 服务器
PAGEGATE_URL={url}
# PageGate API token
PAGEGATE_API_TOKEN={token}

# OpenClaw 通知通道
OPENCLAW_NOTIFY_CHANNEL={channel}
OPENCLAW_NOTIFY_TARGET={target}
OPENCLAW_NOTIFY_ACCOUNT={account}

# 日志和状态
PAGEGATE_WATCH_LOG_FILE={log_file}
PAGEGATE_WATCH_STATE_FILE={state_file}
"""

    if ENV_FILE.exists():
        if not ask_yn(f".env 文件已存在，是否覆盖？"):
            backup = ENV_FILE.with_suffix(".env.bak")
            ENV_FILE.rename(backup)
            ok(f"已备份到 {backup}")

    ENV_FILE.write_text(content, encoding="utf-8")
    ok(f"配置已保存到 {ENV_FILE}")
    return log_file


# ── Step 6: 测试通知发送 ──────────────────────────────────────────
def test_notification(channel, target, account):
    step(6, 6, "发送测试通知")

    if not shutil.which("openclaw"):
        warn("未检测到 openclaw CLI，跳过测试通知")
        print()
        info("你可以稍后手动测试：")
        info(f"  cd {SKILL_DIR}")
        info(f"  source .env && bash scripts/start-watcher.sh")
        return False

    print()
    info("即将通过 OpenClaw 发送一条测试消息到你的通知通道...")
    if not ask_yn("确认发送？"):
        info("跳过测试通知")
        return False

    test_message = (
        "[pagegate-setup] 🎉 测试消息\n"
        "PageGate 通知通道配置成功！\n"
        "你将在这里收到页面访问审批请求。"
    )
    idempotency_key = f"pagegate-setup-test-{int(time.time())}"

    params = json.dumps({
        "channel": channel,
        "to": target,
        "accountId": account,
        "message": test_message,
        "idempotencyKey": idempotency_key,
    }, ensure_ascii=False)

    cmd = ["openclaw", "gateway", "call", "send", "--json", "--params", params]

    # 检查可选的 gateway 配置
    gw_url = os.environ.get("OPENCLAW_GATEWAY_URL", "")
    gw_token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")
    if gw_url:
        cmd.extend(["--url", gw_url])
    if gw_token:
        cmd.extend(["--token", gw_token])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            ok("测试消息发送成功！")
            info("请检查你的通知通道，确认是否收到了测试消息。")
            return True
        else:
            fail("发送失败")
            err = result.stderr.strip() or result.stdout.strip()
            if err:
                info(err[:300])
            print()
            info("可能的原因：")
            info("  1. OpenClaw Gateway 未启动或不可达")
            info("  2. 通道名称或目标 ID 配置错误")
            info("  3. 账号 ID 不正确")
            return False
    except subprocess.TimeoutExpired:
        fail("发送超时（15秒）")
        info("请检查 OpenClaw Gateway 是否正在运行")
        return False
    except Exception as e:
        fail(f"发送失败: {e}")
        return False


# ── 完成提示 ──────────────────────────────────────────────────────
def print_summary(url, test_ok, log_file):
    banner("初始化完成！")
    print()

    if test_ok:
        print(f"  {green('✓')} 服务器连接正常")
        print(f"  {green('✓')} 测试通知发送成功")
        print(f"  {green('✓')} .env 配置已保存")
    else:
        print(f"  {green('✓')} 服务器连接正常")
        print(f"  {yellow('⚠')} 测试通知未发送或失败（可稍后重试）")
        print(f"  {green('✓')} .env 配置已保存")

    print(f"""
{bold('接下来你可以：')}

  {cyan('1.')} 启动实时审批通知监听:
     cd {SKILL_DIR}
     ./scripts/start-watcher.sh

  {cyan('2.')} 在 OpenClaw 中使用 pagegate-client skill:
     发送 "查看待审批" 查看待审批列表
     发送 "发布页面" 上传 HTML 页面

  {cyan('3.')} 查看日志:
     tail -f {log_file}

  {cyan('4.')} 重新运行初始化:
     python3 {Path(__file__).resolve()}

{bold('服务器地址:')} {url}
{bold('配置文件:')}   {ENV_FILE}
""")


# ── Main ──────────────────────────────────────────────────────────
def main():
    banner("PageGate OpenClaw Skill 初始化向导")
    print()
    print("  本向导将帮助你完成 PageGate 的首次接入配置，")
    print("  包括选择服务器、获取 PageGate API token、配置通知路由以及启动 watcher。")

    # Step 1: 选择服务器
    server_mode, url = choose_server()

    # Step 2: PageGate API token
    token = configure_token(url, server_mode)

    # Step 3: 验证连通性
    if not verify_connection(url, token):
        print()
        if ask_yn("连接失败，是否继续配置？（可稍后修改 .env）"):
            warn("跳过连通性验证，继续配置...")
        else:
            sys.exit(1)

    # Step 4: OpenClaw 通道
    channel, target, account = configure_openclaw_channel()

    # Step 5: 写入 .env
    log_file = write_env_file(url, token, channel, target, account)

    # Step 6: 测试通知
    test_ok = test_notification(channel, target, account)

    # 完成
    print_summary(url, test_ok, log_file)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{yellow('已取消')}")
        sys.exit(130)
