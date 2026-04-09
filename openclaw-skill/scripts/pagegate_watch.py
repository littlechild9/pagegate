#!/usr/bin/env python3
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from urllib import request
from urllib.parse import quote

log_file = os.environ.get(
    "PAGEGATE_WATCH_LOG_FILE",
    os.path.expanduser("~/.openclaw/workspace/memory/pagegate-watch.log"),
)

_DEVNULL = open(os.devnull, "w", encoding="utf-8")
sys.stdout = _DEVNULL
sys.stderr = _DEVNULL


def ensure_parent_dir(path: str):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def env(name: str, required: bool = True) -> str:
    value = os.environ.get(name, "").strip()
    if required and not value:
        log(f"[pagegate-watch] missing required environment variable: {name}")
        sys.exit(2)
    return value.rstrip("/") if name in ("PAGEGATE_URL", "OPENCLAW_GATEWAY_URL") else value


def log(msg: str):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    ensure_parent_dir(log_file)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")


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
health_file = os.environ.get(
    "PAGEGATE_WATCH_HEALTH_FILE",
    os.path.expanduser("~/.openclaw/workspace/memory/pagegate-watch-health.json"),
)
send_delay_ms = int(os.environ.get("PAGEGATE_WATCH_SEND_DELAY_MS", "1200"))
reconnect_base_ms = max(1000, int(os.environ.get("PAGEGATE_WATCH_RECONNECT_MS", "3000")))
reconnect_max_ms = max(reconnect_base_ms, int(os.environ.get("PAGEGATE_WATCH_RECONNECT_MAX_MS", "30000")))
reconnect_reset_after_ms = max(reconnect_base_ms, int(os.environ.get("PAGEGATE_WATCH_RECONNECT_RESET_MS", "60000")))
stream_read_timeout_sec = max(30, int(os.environ.get("PAGEGATE_WATCH_STREAM_TIMEOUT_SEC", "90")))
pending_sync_interval_ms = int(os.environ.get("PAGEGATE_WATCH_PENDING_SYNC_MS", "60000"))
sync_pending_on_start = os.environ.get("PAGEGATE_WATCH_SYNC_PENDING", "1") == "1"
health_heartbeat_sec = max(5, int(os.environ.get("PAGEGATE_WATCH_HEALTH_HEARTBEAT_SEC", "10")))
verbose = os.environ.get("PAGEGATE_WATCH_VERBOSE", "0") == "1"

state_lock = threading.RLock()
health_stop_event = threading.Event()
started_at_iso = datetime.now().isoformat(timespec='seconds')
health = {
    "pid": os.getpid(),
    "started_at": started_at_iso,
    "status": "starting",
    "last_connect_at": None,
    "last_event_at": None,
    "last_pending_sync_at": None,
    "last_send_ok_at": None,
    "last_error": "",
    "consecutive_failures": 0,
    "last_event_id": "",
    "last_heartbeat_at": None,
    "heartbeat_interval_sec": health_heartbeat_sec,
}


def update_health(**fields):
    with state_lock:
        health.update(fields)
        health["updated_at"] = datetime.now().isoformat(timespec='seconds')
        ensure_parent_dir(health_file)
        with open(health_file, "w", encoding="utf-8") as f:
            json.dump(health, f, ensure_ascii=False, indent=2)


def health_heartbeat_loop():
    while not health_stop_event.wait(health_heartbeat_sec):
        update_health(last_heartbeat_at=datetime.now().isoformat(timespec='seconds'))


def load_state():
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        state = {}
    state.setdefault("last_event_id", "")
    state.setdefault("sent_ids", [])
    update_health(last_event_id=state.get("last_event_id", ""))
    return state


def save_state(state):
    ensure_parent_dir(state_file)
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    update_health(last_event_id=state.get("last_event_id", ""))


def remember_sent(state, event_id):
    with state_lock:
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
    update_health(last_send_ok_at=datetime.now().isoformat(timespec='seconds'))
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


def sync_pending(state, reason: str = ""):
    try:
        data = fetch_pending()
        update_health(last_pending_sync_at=datetime.now().isoformat(timespec='seconds'))
        if data.get("count", 0) == 0:
            if reason:
                log(f"[pagegate-watch] no pending requests ({reason})")
            else:
                log("[pagegate-watch] no pending requests")
            return
        if reason:
            log(f"[pagegate-watch] syncing {data.get('count', 0)} pending request(s) ({reason})")
        for item in data.get("pending", []):
            deliver_event(state, build_pending_event(item))
    except Exception as e:
        suffix = f" ({reason})" if reason else ""
        update_health(last_error=f"sync_pending failed{suffix}: {e}")
        log(f"[pagegate-watch] sync_pending failed{suffix}: {e}")


def maybe_sync_pending(state, last_sync_at: float, reason: str, force: bool = False) -> float:
    now = time.monotonic()
    if not force and pending_sync_interval_ms > 0 and now - last_sync_at < (pending_sync_interval_ms / 1000.0):
        return last_sync_at
    sync_pending(state, reason=reason)
    return now


def compute_reconnect_delay_ms(consecutive_failures: int, server_retry_ms: int) -> int:
    base_ms = max(reconnect_base_ms, server_retry_ms)
    exp_ceiling_ms = min(reconnect_max_ms, base_ms * (2 ** max(0, consecutive_failures - 1)))
    jitter_floor_ms = max(1000, exp_ceiling_ms // 2)
    return random.randint(jitter_floor_ms, exp_ceiling_ms)


def pending_sync_loop(state):
    if pending_sync_interval_ms <= 0:
        return
    jitter_ms = min(5000, max(0, pending_sync_interval_ms // 10))
    while True:
        sleep_ms = pending_sync_interval_ms
        if jitter_ms > 0:
            sleep_ms += random.randint(-jitter_ms, jitter_ms)
        time.sleep(max(5, sleep_ms / 1000.0))
        try:
            sync_pending(state, reason="periodic")
        except Exception as e:
            update_health(last_error=f"periodic sync failed: {e}")
            log(f"[pagegate-watch] periodic sync failed: {e}")


def stream_events():
    state = load_state()
    if pending_sync_interval_ms > 0:
        threading.Thread(target=pending_sync_loop, args=(state,), daemon=True).start()
    first_loop = True
    last_pending_sync_at = 0.0
    consecutive_failures = 0
    server_retry_ms = reconnect_base_ms
    while True:
        if sync_pending_on_start and (first_loop or pending_sync_interval_ms > 0):
            reason = "startup" if first_loop else "before-reconnect"
            last_pending_sync_at = maybe_sync_pending(state, last_pending_sync_at, reason=reason, force=first_loop)
        first_loop = False
        connected_at = time.monotonic()
        update_health(
            status="connecting",
            last_connect_at=datetime.now().isoformat(timespec='seconds'),
            consecutive_failures=consecutive_failures,
        )
        try:
            url = base_url + "/api/events/stream"
            if state.get("last_event_id"):
                url += "?last_event_id=" + quote(state["last_event_id"], safe="")
            headers = {
                "Authorization": f"Bearer {api_token}",
                "Accept": "text/event-stream",
                "Cache-Control": "no-cache",
            }
            if state.get("last_event_id"):
                headers["Last-Event-ID"] = state["last_event_id"]
            req = request.Request(url, headers=headers)
            with request.urlopen(req, timeout=stream_read_timeout_sec) as resp:
                consecutive_failures = 0
                update_health(status="connected", consecutive_failures=0, last_error="")
                event_type = None
                data_lines = []
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                    if not line.strip():
                        if event_type and data_lines:
                            payload = json.loads("\n".join(data_lines))
                            with state_lock:
                                state["last_event_id"] = payload.get("id", state.get("last_event_id", ""))
                                save_state(state)
                            update_health(last_event_at=datetime.now().isoformat(timespec='seconds'))
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
                    elif line.startswith("retry: "):
                        try:
                            server_retry_ms = max(1000, min(reconnect_max_ms, int(line[7:])))
                        except ValueError:
                            pass
                    elif line.startswith("id: "):
                        with state_lock:
                            state["last_event_id"] = line[4:]
                            save_state(state)
        except KeyboardInterrupt:
            update_health(status="stopped")
            break
        except Exception as e:
            connection_lifetime_ms = int((time.monotonic() - connected_at) * 1000)
            if connection_lifetime_ms >= reconnect_reset_after_ms:
                consecutive_failures = 0
            consecutive_failures += 1
            update_health(status="reconnecting", consecutive_failures=consecutive_failures, last_error=str(e))
            log(f"[pagegate-watch] reconnect after error: {e}")
            if sync_pending_on_start:
                last_pending_sync_at = maybe_sync_pending(state, last_pending_sync_at, reason="after-error")
        else:
            connection_lifetime_ms = int((time.monotonic() - connected_at) * 1000)
            if connection_lifetime_ms >= reconnect_reset_after_ms:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                update_health(status="reconnecting", consecutive_failures=consecutive_failures)
                log(f"[pagegate-watch] stream ended after {connection_lifetime_ms}ms; reconnecting")

        delay_ms = compute_reconnect_delay_ms(consecutive_failures, server_retry_ms)
        if verbose:
            log(f"[pagegate-watch] reconnect sleep {delay_ms}ms (failures={consecutive_failures}, server_retry={server_retry_ms})")
        time.sleep(delay_ms / 1000.0)


if __name__ == "__main__":
    try:
        threading.Thread(target=health_heartbeat_loop, daemon=True).start()
        update_health(status="starting", last_heartbeat_at=datetime.now().isoformat(timespec='seconds'))
        stream_events()
    except Exception as e:
        update_health(status="fatal", last_error=str(e))
        log(f"[pagegate-watch] FATAL: {e}")
        sys.exit(1)
    finally:
        health_stop_event.set()
