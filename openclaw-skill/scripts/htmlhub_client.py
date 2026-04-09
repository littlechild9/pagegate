#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional
from urllib import request, error
import mimetypes
import uuid


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        print(f"Missing required environment variable: {name}", file=sys.stderr)
        sys.exit(2)
    return value.rstrip("/") if name == "HTMLHUB_URL" else value


BASE_URL = env("HTMLHUB_URL")
ADMIN_TOKEN = env("HTMLHUB_ADMIN_TOKEN")


def api_request(path: str, method: str = "GET", data: Optional[bytes] = None, content_type: Optional[str] = None) -> Any:
    req = request.Request(
        BASE_URL + path,
        method=method,
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        data=data,
    )
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {"ok": True}
    except error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code}: {body}", file=sys.stderr)
        sys.exit(1)
    except error.URLError as e:
        print(f"Request failed: {e}", file=sys.stderr)
        sys.exit(1)


def encode_multipart(fields, file_field: str, file_path: str):
    boundary = f"----htmlhub-{uuid.uuid4().hex}"
    chunks = []

    for key, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
            str(value).encode("utf-8"),
            b"\r\n",
        ])

    filename = Path(file_path).name
    mime = mimetypes.guess_type(filename)[0] or "text/html"
    with open(file_path, "rb") as f:
        file_bytes = f.read()

    chunks.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
        file_bytes,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])

    return boundary, b"".join(chunks)


def cmd_publish(args):
    if not Path(args.file).exists():
        print(f"File not found: {args.file}", file=sys.stderr)
        sys.exit(2)
    fields = {
        "slug": args.slug,
        "title": args.title,
        "category": args.category,
        "access": args.access,
        "description": args.description,
    }
    boundary, body = encode_multipart(fields, "file", args.file)
    result = api_request("/api/publish", "POST", body, f"multipart/form-data; boundary={boundary}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_pending(_args):
    result = api_request("/api/pending")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_approve(args):
    payload = json.dumps({"visitor_id": args.visitor_id}, ensure_ascii=False).encode("utf-8")
    result = api_request(f"/api/pages/{args.slug}/approve", "POST", payload, "application/json")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_reject(args):
    payload = json.dumps({"visitor_id": args.visitor_id}, ensure_ascii=False).encode("utf-8")
    result = api_request(f"/api/pages/{args.slug}/reject", "POST", payload, "application/json")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_update(args):
    body = {}
    for key in ("title", "category", "access", "description", "owner"):
        value = getattr(args, key)
        if value is not None:
            body[key] = value
    if not body:
        print("No update fields provided", file=sys.stderr)
        sys.exit(2)
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    result = api_request(f"/api/pages/{args.slug}", "PUT", payload, "application/json")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_delete(args):
    result = api_request(f"/api/pages/{args.slug}", "DELETE")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_revoke(args):
    result = api_request(f"/api/pages/{args.slug}/visitors/{args.visitor_id}", "DELETE")
    print(json.dumps(result, ensure_ascii=False, indent=2))


parser = argparse.ArgumentParser(description="HTML Hub client")
sub = parser.add_subparsers(dest="command", required=True)

p = sub.add_parser("publish")
p.add_argument("--file", required=True)
p.add_argument("--slug", required=True)
p.add_argument("--title", required=True)
p.add_argument("--category", default="未分类")
p.add_argument("--access", default="public", choices=["public", "approval", "private"])
p.add_argument("--description", default="")
p.set_defaults(func=cmd_publish)

p = sub.add_parser("pending")
p.set_defaults(func=cmd_pending)

p = sub.add_parser("approve")
p.add_argument("--slug", required=True)
p.add_argument("--visitor-id", required=True)
p.set_defaults(func=cmd_approve)

p = sub.add_parser("reject")
p.add_argument("--slug", required=True)
p.add_argument("--visitor-id", required=True)
p.set_defaults(func=cmd_reject)

p = sub.add_parser("update")
p.add_argument("--slug", required=True)
p.add_argument("--title")
p.add_argument("--category")
p.add_argument("--access", choices=["public", "approval", "private"])
p.add_argument("--description")
p.add_argument("--owner")
p.set_defaults(func=cmd_update)

p = sub.add_parser("delete")
p.add_argument("--slug", required=True)
p.set_defaults(func=cmd_delete)

p = sub.add_parser("revoke")
p.add_argument("--slug", required=True)
p.add_argument("--visitor-id", required=True)
p.set_defaults(func=cmd_revoke)


if __name__ == "__main__":
    args = parser.parse_args()
    args.func(args)
