#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import time


def env(name: str, required: bool = True) -> str:
    value = os.environ.get(name, "").strip()
    if required and not value:
        print(f"Missing required env: {name}", file=sys.stderr)
        sys.exit(2)
    return value


def make_idempotency_key(seed: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._:-]+", "-", seed)
    return ("htmlhub-test-" + safe)[:120]


parser = argparse.ArgumentParser(description="Reproduce watcher-style gateway chat.send call")
parser.add_argument("message", nargs="?", default="gateway rpc subprocess test")
parser.add_argument("--count", type=int, default=1)
parser.add_argument("--delay-ms", type=int, default=1000)
parser.add_argument("--session-key")
parser.add_argument("--gateway-url")
parser.add_argument("--gateway-token")
args = parser.parse_args()

session_key = args.session_key or env("OPENCLAW_SESSION_KEY")
gateway_url = args.gateway_url or env("OPENCLAW_GATEWAY_URL", required=False)
gateway_token = args.gateway_token or env("OPENCLAW_GATEWAY_TOKEN", required=False)

for idx in range(args.count):
    text = args.message if args.count == 1 else f"{args.message} #{idx+1}"
    params = {
        "sessionKey": session_key,
        "message": text,
        "idempotencyKey": make_idempotency_key(text + f"-{idx+1}"),
    }
    cmd = [
        "openclaw",
        "gateway",
        "call",
        "chat.send",
        "--json",
        "--params",
        json.dumps(params, ensure_ascii=False),
    ]
    if gateway_url:
        cmd.extend(["--url", gateway_url])
    if gateway_token:
        cmd.extend(["--token", gateway_token])

    print("RUN", idx + 1, ":", " ".join(cmd), flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    print("EXIT:", result.returncode, flush=True)
    if result.stdout:
        print("STDOUT:")
        print(result.stdout.strip(), flush=True)
    if result.stderr:
        print("STDERR:")
        print(result.stderr.strip(), flush=True)
    if idx + 1 < args.count:
        time.sleep(args.delay_ms / 1000.0)
