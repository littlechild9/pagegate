#!/usr/bin/env python3
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib import error, request

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
ENV_FILE = SKILL_DIR / '.env'
ONBOARDING_MARKER = SKILL_DIR / '.onboarding-pending'
START_WATCHER = SCRIPT_DIR / 'start-watcher.sh'
WATCH_SCRIPT = SCRIPT_DIR / 'pagegate_watch.py'
DEFAULT_LOG_FILE = '~/.openclaw/workspace/memory/pagegate-watch.log'
DEFAULT_STATE_FILE = '~/.openclaw/workspace/memory/pagegate-watch-state.json'
DEFAULT_HEALTH_FILE = '~/.openclaw/workspace/memory/pagegate-watch-health.json'
DEFAULT_PENDING_SYNC_MS = '60000'
DEFAULT_SERVER_URL = 'http://115.190.148.77:8888'
DEFAULT_CHANNEL = 'openclaw-weixin'


def emit(payload, exit_code=0):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + '\n')
    raise SystemExit(exit_code)


def fail(message, exit_code=1, **extra):
    payload = {'ok': False, 'error': message}
    payload.update(extra)
    emit(payload, exit_code=exit_code)


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        fail(message, exit_code=2)


def parse_json(body):
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def post_json(base_url, path, payload):
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = request.Request(
        base_url.rstrip('/') + path,
        method='POST',
        headers={'Content-Type': 'application/json'},
        data=data,
    )
    with request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode('utf-8')
        parsed = parse_json(body)
        return parsed if parsed is not None else {'ok': True, 'raw': body}


def request_json(base_url, path, token):
    req = request.Request(
        base_url.rstrip('/') + path,
        headers={'Authorization': f'Bearer {token}'},
    )
    with request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode('utf-8')
        parsed = parse_json(body)
        return parsed if parsed is not None else {'ok': True, 'raw': body}


def discover_openclaw_config():
    result = {'channels': [], 'account': '', 'gateway_url': ''}

    config_path = Path.home() / '.openclaw' / 'openclaw.json'
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding='utf-8'))
            channels_cfg = config.get('channels', {})
            if isinstance(channels_cfg, dict):
                result['channels'] = sorted(channels_cfg.keys())
            elif isinstance(channels_cfg, list):
                for ch in channels_cfg:
                    if isinstance(ch, dict):
                        name = ch.get('name') or ch.get('type') or ch.get('id')
                        if name:
                            result['channels'].append(name)
            gateway_cfg = config.get('gateway', {})
            if isinstance(gateway_cfg, dict) and gateway_cfg.get('port'):
                result['gateway_url'] = f"http://127.0.0.1:{gateway_cfg['port']}"
        except Exception:
            pass

    auth_path = Path.home() / '.openclaw' / 'identity' / 'device-auth.json'
    if auth_path.exists():
        try:
            auth = json.loads(auth_path.read_text(encoding='utf-8'))
            result['account'] = auth.get('accountId') or auth.get('account_id') or ''
        except Exception:
            pass

    return result


def shell_env_value(value):
    return shlex.quote(str(value))


def backup_existing_env():
    if not ENV_FILE.exists():
        return None
    backup = ENV_FILE.parent / f".env.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(ENV_FILE, backup)
    return str(backup)


def write_env(
    url,
    token,
    channel,
    target,
    account,
    log_file,
    state_file,
    health_file,
    pending_sync_ms,
    *,
    pagegate_name='',
    pagegate_username='',
    pagegate_home_url='',
    pagegate_dashboard_url='',
):
    backup = backup_existing_env()
    content = f"""# PageGate Watcher 环境变量
# 由 pagegate_onboard.py 自动生成于 {time.strftime('%Y-%m-%d %H:%M:%S')}

PAGEGATE_URL={shell_env_value(url)}
PAGEGATE_API_TOKEN={shell_env_value(token)}
PAGEGATE_NAME={shell_env_value(pagegate_name)}
PAGEGATE_USERNAME={shell_env_value(pagegate_username)}
PAGEGATE_HOME_URL={shell_env_value(pagegate_home_url)}
PAGEGATE_DASHBOARD_URL={shell_env_value(pagegate_dashboard_url)}
OPENCLAW_NOTIFY_CHANNEL={shell_env_value(channel)}
OPENCLAW_NOTIFY_TARGET={shell_env_value(target)}
OPENCLAW_NOTIFY_ACCOUNT={shell_env_value(account)}
PAGEGATE_WATCH_LOG_FILE={shell_env_value(log_file)}
PAGEGATE_WATCH_STATE_FILE={shell_env_value(state_file)}
PAGEGATE_WATCH_HEALTH_FILE={shell_env_value(health_file)}
PAGEGATE_WATCH_PENDING_SYNC_MS={shell_env_value(pending_sync_ms)}
"""
    ENV_FILE.write_text(content, encoding='utf-8')
    return backup


def verify_connection(url, token):
    try:
        data = request_json(url, '/api/pending', token)
        return True, {'pendingCount': data.get('count', 0), 'detail': data}
    except error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        parsed = parse_json(body)
        return False, {'status': e.code, 'detail': parsed if parsed is not None else body}
    except error.URLError as e:
        return False, {'reason': str(e.reason)}
    except Exception as e:
        return False, {'reason': str(e)}


def fetch_account_profile(url, token):
    try:
        data = request_json(url, '/api/me', token)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def resolve_auth(args, url):
    auth_mode = args.auth_mode
    try:
        if auth_mode == 'token':
            return {
                'token': args.api_token,
                'authMode': 'token',
                'pagegateName': args.pagegate_name or '',
                'username': args.username or '',
                'pagegateUrl': '',
                'dashboardUrl': '',
            }
        if auth_mode == 'quick-register':
            payload = {
                'pagegate_name': args.pagegate_name,
            }
            if args.username:
                payload['username'] = args.username
            result = post_json(url, '/api/auth/register', payload)
        elif auth_mode == 'register':
            payload = {
                'email': args.email,
                'password': args.password,
            }
            if args.username:
                payload['username'] = args.username
            if args.pagegate_name:
                payload['pagegate_name'] = args.pagegate_name
            result = post_json(url, '/api/auth/register', payload)
        elif auth_mode == 'login':
            result = post_json(url, '/api/auth/login', {
                'email': args.email,
                'password': args.password,
            })
        else:
            fail(f'Unsupported auth mode: {auth_mode}', exit_code=2)
        token = (result or {}).get('token', '')
        if not token:
            fail('PageGate auth succeeded but no token was returned', result=result)
        return {
            'token': token,
            'authMode': auth_mode,
            'pagegateName': (result or {}).get('pagegate_name', args.pagegate_name or ''),
            'username': (result or {}).get('username', args.username or ''),
            'pagegateUrl': (result or {}).get('pagegate_url', ''),
            'dashboardUrl': (result or {}).get('dashboard_url', ''),
            'result': result,
        }
    except error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        parsed = parse_json(body)
        fail(
            f'PageGate auth failed (HTTP {e.code})',
            status=e.code,
            detail=parsed if parsed is not None else body,
            authMode=auth_mode,
        )
    except error.URLError as e:
        fail(f'PageGate auth request failed: {e.reason}', authMode=auth_mode)
    except Exception as e:
        fail(f'PageGate auth failed: {e}', authMode=auth_mode)


def start_watcher():
    try:
        subprocess.Popen(
            ['bash', str(START_WATCHER)],
            cwd=str(SKILL_DIR),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(1.2)
        result = subprocess.run(
            ['pgrep', '-f', str(WATCH_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False


def send_test_message(channel, target, account, gateway_url=''):
    if not shutil.which('openclaw'):
        return False, 'openclaw CLI not found'

    params = json.dumps({
        'channel': channel,
        'to': target,
        'accountId': account,
        'message': '[pagegate-setup] 🎉 测试消息\nPageGate 通知通道配置成功！\n你将在这里收到页面访问审批请求。',
        'idempotencyKey': f'pagegate-setup-test-{int(time.time())}',
    }, ensure_ascii=False)
    cmd = ['openclaw', 'gateway', 'call', 'send', '--json', '--params', params]
    if gateway_url:
        cmd.extend(['--url', gateway_url])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip() or result.stdout.strip() or f'exit {result.returncode}'
    except Exception as e:
        return False, str(e)


parser = JsonArgumentParser(description='Non-interactive PageGate onboarding helper')
parser.add_argument('--url', default=DEFAULT_SERVER_URL)
parser.add_argument('--auth-mode', required=True, choices=['quick-register', 'register', 'login', 'token'])
parser.add_argument('--pagegate-name')
parser.add_argument('--username')
parser.add_argument('--email')
parser.add_argument('--password')
parser.add_argument('--api-token')
parser.add_argument('--notify-channel', default=DEFAULT_CHANNEL)
parser.add_argument('--notify-target', required=True)
parser.add_argument('--notify-account', required=True)
parser.add_argument('--log-file', default=DEFAULT_LOG_FILE)
parser.add_argument('--state-file', default=DEFAULT_STATE_FILE)
parser.add_argument('--health-file', default=DEFAULT_HEALTH_FILE)
parser.add_argument('--pending-sync-ms', default=DEFAULT_PENDING_SYNC_MS)
parser.add_argument('--start-watcher', action='store_true')
parser.add_argument('--send-test', action='store_true')
args = parser.parse_args()

if args.auth_mode == 'quick-register' and not args.pagegate_name:
    fail('--pagegate-name is required for auth-mode=quick-register', exit_code=2)
if args.auth_mode in ('register', 'login'):
    if not args.email:
        fail('--email is required for register/login', exit_code=2)
    if not args.password:
        fail('--password is required for register/login', exit_code=2)
if args.auth_mode == 'token' and not args.api_token:
    fail('--api-token is required for auth-mode=token', exit_code=2)

url = args.url.rstrip('/')
auth = resolve_auth(args, url)
token = auth['token']
resolved_auth_mode = auth['authMode']
verified, verify_detail = verify_connection(url, token)
if not verified:
    fail('Failed to verify PageGate connection', url=url, authMode=resolved_auth_mode, verify=verify_detail)

account = fetch_account_profile(url, token)
resolved_username = account.get('username') or auth.get('username', '') or args.username or ''
resolved_pagegate_name = account.get('pagegate_name') or auth.get('pagegateName', '') or args.pagegate_name or ''
resolved_pagegate_url = account.get('pagegate_url') or auth.get('pagegateUrl', '')
if not resolved_pagegate_url and resolved_username:
    resolved_pagegate_url = f"{url}/{resolved_username}"
resolved_dashboard_url = account.get('dashboard_url') or auth.get('dashboardUrl', '') or f'{url}/dashboard?token={token}'

backup = write_env(
    url,
    token,
    args.notify_channel,
    args.notify_target,
    args.notify_account,
    args.log_file,
    args.state_file,
    args.health_file,
    args.pending_sync_ms,
    pagegate_name=resolved_pagegate_name,
    pagegate_username=resolved_username,
    pagegate_home_url=resolved_pagegate_url,
    pagegate_dashboard_url=resolved_dashboard_url,
)
if ONBOARDING_MARKER.exists():
    ONBOARDING_MARKER.unlink()

watcher_started = False
if args.start_watcher:
    watcher_started = start_watcher()

config = discover_openclaw_config()
test_sent = False
test_detail = ''
if args.send_test:
    test_sent, test_detail = send_test_message(
        args.notify_channel,
        args.notify_target,
        args.notify_account,
        config.get('gateway_url', ''),
    )

emit({
    'ok': True,
    'url': url,
    'authMode': resolved_auth_mode,
    'apiToken': token,
    'envFile': str(ENV_FILE),
    'envBackupFile': backup,
    'onboardingMarkerCleared': not ONBOARDING_MARKER.exists(),
    'notifyChannel': args.notify_channel,
    'notifyTarget': args.notify_target,
    'notifyAccount': args.notify_account,
    'verify': verify_detail,
    'watcherStarted': watcher_started,
    'testSent': test_sent,
    'testDetail': test_detail,
    'discovered': config,
    'pagegateName': resolved_pagegate_name,
    'username': resolved_username,
    'pagegateUrl': resolved_pagegate_url,
    'dashboardUrl': resolved_dashboard_url,
    'nextSteps': [
        '发布一个 access=approval 或 access=private 的测试页面。',
        '用你自己的访客身份打开页面链接并完成一次登录访问。',
        '访问完成后运行 python3 scripts/pagegate_client.py visitors，确认自己的 visitor_id。',
        '确认 visitor_id 后运行 python3 scripts/pagegate_client.py whitelist-add --visitor-id <your-visitor-id>，把自己加入当前账号的用户级白名单。',
        '以后想查看全部页面和审批状态，请直接打开 dashboardUrl；个人首页 pagegateUrl 的公开页面 tab 只展示 public 页面，已获准访问的内容会出现在登录后的已授权给我 tab。',
    ],
    'selfWhitelistCommandTemplate': 'python3 scripts/pagegate_client.py whitelist-add --visitor-id <your-visitor-id>',
})
