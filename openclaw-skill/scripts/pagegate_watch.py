#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from urllib import request
from urllib.parse import quote

# Redirect stderr to stdout so nohup >>file 2>&1 captures everything
# (OpenClaw background exec is sensitive to stray stderr output)
sys.stderr = sys.stdout


def env(name: str, required: bool = True) -> str:
    value = os.environ.get(name, "").strip()
    if required and not value:
        print(f"Missing required environment variable: {name}", file=sys.stderr)
        sys.exit(2)
    return value.rstrip("/") if name in ("PAGEGATE_URL", "OPENCLAW_GATEWAY_URL") else value


base_url = env("PAGEGATE_URL")
api_token = env("PAGEGATE_API_TOKEN")
notify_channel = env("OPENCLAW_NOTIFY_CHANNEL")
notify_target = env("OPENCLAW_NOTIFY_TARGET")
notify_account = env("OPENCLAW_NOTIFY_ACCOUNT")
gateway_url = env("OPENCLAW_GATEWAY_URL", required=False)
gateway_token = env("OPENCLAW_GATEWAY_TOKEN", required=False)
state_file = os.environ.get(
    "PAGEGATE_WATCH_STATE_FILE",
    os.path.expanduser("~/.openclaw/workspace/memory/pagegate-watch-state.json"),
)
send_delay_ms = int(os.environ.get("PAGEGATE_WATCH_SEND_DELAY_MS", "1200"))
reconnect_delay_ms = int(os.environ.get("PAGEGATE_WATCH_RECONNECT_MS", "2000"))
sync_pending_on_start = os.environ.get("PAGEGATE_WATCH_SYNC_PENDING", "1") == "1"
verbose = os.environ.get("PAGEGATE_WATCH_VERBOSE", "0") == "1"
log_file = os.environ.get(
    "PAGEGATE_WATCH_LOG_FILE",
    os.path.expanduser("~/.openclaw/workspace/memory/pagegate-watch.log"),
)


def log(msg: str):
    # Write to file only — no stdout/stderr to avoid OpenClaw exec listener issues
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state():
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        state = {}
    state.setdefault("last_event_id", "")
    state.setdefault("sent_ids", [])
    return state


def save_state(state):
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def remember_sent(state, event_id):
    sent_ids = state.setdefault("sent_ids", [])
    if event_id in sent_ids:
        return False
    sent_ids.append(event_id)
    if len(sent_ids) > 2000:
        del sent_ids[:-2000]
    save_state(state)
    return True


def build_pending_event(item):
    provider_name = item.get("provider", "")
    return {
        "id": f"{item['slug']}:{item['visitor_id']}",
        "type": "access_requested",
        "page": {"title": item.get("page_title", item["slug"]), "slug": item["slug"]},
        "visitor": {
            "name": item.get("visitor_name", ""),
            "id": item["visitor_id"],
            "provider_name": provider_name,
        },
        "requested_at": item.get("requested_at", ""),
    }


def build_message(event):
    page = event.get("page", {})
    visitor = event.get("visitor", {})
    return (
        f"[pagegate-event]\n"
        f"有人想查看你的页面\n"
        f"页面：{page.get('title', '')} ({page.get('slug', '')})\n"
        f"访客：{visitor.get('name', '')}（{visitor.get('provider_name', visitor.get('provider', ''))}登录）\n"
        f"访客ID：{visitor.get('id', '')}\n"
        f"事件ID：{event.get('id', '')}\n\n"
        f"回复 1 通过 / 2 拒绝（直接回复数字更快）"
    )


def make_idempotency_key(event_id):
    safe = re.sub(r"[^a-zA-Z0-9._:-]+", "-", event_id or "pagegate")
    return ("pagegate-send-" + safe)[:120]


def send_notification(event):
    params = {
        "channel": notify_channel,
        "to": notify_target,
        "accountId": notify_account,
        "message": build_message(event),
        "idempotencyKey": make_idempotency_key(event.get("id", "")),
    }
    cmd = [
        "openclaw",
        "gateway",
        "call",
        "send",
        "--json",
        "--params",
        json.dumps(params, ensure_ascii=False),
    ]
    if gateway_url:
        cmd.extend(["--url", gateway_url])
    if gateway_token:
        cmd.extend(["--token", gateway_token])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"send exited {result.returncode}")
    return result.stdout.strip()


def deliver_event(state, event):
    event_id = event.get("id", "")
    if not event_id:
        return
    if not remember_sent(state, event_id):
        log(f"[pagegate-watch] skip duplicate {event_id}")
        return
    out = send_notification(event)
    log(f"[pagegate-watch] delivered {event_id}")
    if out:
        log(f"[pagegate-watch] send result {out}")
    time.sleep(send_delay_ms / 1000.0)


def fetch_pending():
    req = request.Request(
        base_url + "/api/pending",
        headers={"Authorization": f"Bearer {api_token}"},
    )
    with request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def sync_pending(state):
    try:
        data = fetch_pending()
        if data.get("count", 0) == 0:
            log("[pagegate-watch] no pending requests")
            return
        for item in data.get("pending", []):
            deliver_event(state, build_pending_event(item))
    except Exception as e:
        log(f"[pagegate-watch] sync_pending failed: {e}")


def stream_events():
    state = load_state()
    first_loop = True
    while True:
        if first_loop and sync_pending_on_start:
            sync_pending(state)
        first_loop = False
        try:
            url = base_url + "/api/events/stream"
            if state.get("last_event_id"):
                url += "?last_event_id=" + quote(state["last_event_id"], safe="")
            req = request.Request(
                url,
                headers={"Authorization": f"Bearer {api_token}", "Accept": "text/event-stream"},
            )
            with request.urlopen(req, timeout=60) as resp:
                event_type = None
                data_lines = []
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                    if not line.strip():
                        if event_type and data_lines:
                            payload = json.loads("\n".join(data_lines))
                            state["last_event_id"] = payload.get("id", state.get("last_event_id", ""))
                            save_state(state)
                            deliver_event(state, payload)
                        event_type = None
                        data_lines = []
                        continue
                    if line.startswith(":"):
                        continue
                    if line.startswith("event: "):
                        event_type = line[7:]
                    elif line.startswith("data: "):
                        data_lines.append(line[6:])
                    elif line.startswith("id: "):
                        state["last_event_id"] = line[4:]
                        save_state(state)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log(f"[pagegate-watch] reconnect after error: {e}")
        time.sleep(reconnect_delay_ms / 1000.0)


if __name__ == "__main__":
    try:
        stream_events()
    except Exception as e:
        log(f"[pagegate-watch] FATAL: {e}")
        import sys
        sys.exit(1)
