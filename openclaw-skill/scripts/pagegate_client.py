#!/usr/bin/env python3
import argparse
import json
import mimetypes
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib import error, request


def emit(payload: dict, exit_code: int = 0):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    raise SystemExit(exit_code)


def fail(message: str, exit_code: int = 1, **extra):
    payload = {"ok": False, "error": message}
    payload.update(extra)
    emit(payload, exit_code=exit_code)


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        fail(message, exit_code=2)


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        fail(f"Missing required environment variable: {name}", exit_code=2)
    return value.rstrip("/") if name == "PAGEGATE_URL" else value


def optional_env(name: str) -> str:
    return os.environ.get(name, "").strip()


def parse_json(body: str) -> Any:
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


BASE_URL = env("PAGEGATE_URL")
API_TOKEN = env("PAGEGATE_API_TOKEN")
PAGEGATE_USERNAME = optional_env("PAGEGATE_USERNAME")
PAGEGATE_HOME_URL = optional_env("PAGEGATE_HOME_URL")
PAGEGATE_DASHBOARD_URL = optional_env("PAGEGATE_DASHBOARD_URL")


def api_request(
    path: str,
    method: str = "GET",
    data: Optional[bytes] = None,
    content_type: Optional[str] = None,
) -> Any:
    req = request.Request(
        BASE_URL + path,
        method=method,
        headers={"Authorization": f"Bearer {API_TOKEN}"},
        data=data,
    )
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            parsed = parse_json(body)
            return parsed if parsed is not None else {"ok": True, "raw": body}
    except error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        parsed = parse_json(body)
        detail = parsed.get("detail") if isinstance(parsed, dict) else body
        fail(
            detail or f"HTTP {e.code}",
            status=e.code,
            detail=parsed if isinstance(parsed, dict) else body,
        )
    except error.URLError as e:
        fail(f"Request failed: {e.reason}")


def encode_multipart(fields, file_field: str, file_path: str):
    boundary = f"----pagegate-{uuid.uuid4().hex}"
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


def current_account_metadata() -> dict:
    metadata = {}
    if PAGEGATE_USERNAME:
        metadata["username"] = PAGEGATE_USERNAME
    if PAGEGATE_HOME_URL:
        metadata["pagegateUrl"] = PAGEGATE_HOME_URL
    if PAGEGATE_DASHBOARD_URL:
        metadata["dashboardUrl"] = PAGEGATE_DASHBOARD_URL
    return metadata


def emit_result(result: Any, **extra):
    if isinstance(result, dict):
        payload = dict(result)
    else:
        payload = {"ok": True, "data": result}

    for key, value in current_account_metadata().items():
        if value and key not in payload:
            payload[key] = value
    for key, value in extra.items():
        if value is not None:
            payload[key] = value
    emit(payload)


def cmd_publish(args):
    if not Path(args.file).exists():
        fail(f"File not found: {args.file}", exit_code=2)
    fields = {
        "slug": args.slug,
        "title": args.title,
        "category": args.category,
        "access": args.access,
        "description": args.description,
    }
    boundary, body = encode_multipart(fields, "file", args.file)
    result = api_request("/api/publish", "POST", body, f"multipart/form-data; boundary={boundary}")
    emit_result(
        result,
        pagegateHomeHasAuthorizedTab=True,
        pagegateHomeShowsPublicPagesOnly=True,
        publishedPageAppearsOnPagegateHome=(args.access == "public"),
        publishedPageAppearsOnAuthorizedTabAfterApproval=(args.access != "public"),
        dashboardTracksAllPages=True,
    )


def cmd_pending(_args):
    emit_result(api_request("/api/pending"))


def cmd_visitors(_args):
    emit_result(api_request("/api/visitors"))


def cmd_approve(args):
    payload = json.dumps({"visitor_id": args.visitor_id}, ensure_ascii=False).encode("utf-8")
    emit_result(
        api_request(f"/api/pages/{args.slug}/approve", "POST", payload, "application/json"),
        pagegateHomeHasAuthorizedTab=True,
        dashboardTracksAllPages=True,
    )


def cmd_reject(args):
    payload = json.dumps({"visitor_id": args.visitor_id}, ensure_ascii=False).encode("utf-8")
    emit_result(
        api_request(f"/api/pages/{args.slug}/reject", "POST", payload, "application/json"),
        pagegateHomeHasAuthorizedTab=True,
        dashboardTracksAllPages=True,
    )


def cmd_update(args):
    body = {}
    for key in ("title", "category", "access", "description", "owner"):
        value = getattr(args, key)
        if value is not None:
            body[key] = value
    if not body:
        fail("No update fields provided", exit_code=2)
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    emit_result(api_request(f"/api/pages/{args.slug}", "PUT", payload, "application/json"))


def cmd_delete(args):
    emit_result(api_request(f"/api/pages/{args.slug}", "DELETE"))


def cmd_revoke(args):
    emit_result(api_request(f"/api/pages/{args.slug}/visitors/{args.visitor_id}", "DELETE"))


def cmd_whitelist_add(args):
    emit_result(
        api_request(f"/api/visitors/{args.visitor_id}/whitelist", "POST"),
        pagegateHomeHasAuthorizedTab=True,
        dashboardTracksAllPages=True,
    )


def cmd_whitelist_remove(args):
    emit_result(
        api_request(f"/api/visitors/{args.visitor_id}/whitelist", "DELETE"),
        pagegateHomeHasAuthorizedTab=True,
        dashboardTracksAllPages=True,
    )


parser = JsonArgumentParser(description="PageGate client")
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

p = sub.add_parser("visitors")
p.set_defaults(func=cmd_visitors)

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

p = sub.add_parser("whitelist-add")
p.add_argument("--visitor-id", required=True)
p.set_defaults(func=cmd_whitelist_add)

p = sub.add_parser("whitelist-remove")
p.add_argument("--visitor-id", required=True)
p.set_defaults(func=cmd_whitelist_remove)


if __name__ == "__main__":
    args = parser.parse_args()
    args.func(args)
